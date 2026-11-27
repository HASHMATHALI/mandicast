"""Prices: daily history, latest per mandi, comparison, non-predictive trend statistics (docs/07 §3).

Days without a report are absent, never filled. Values with an unreliability flag (source.EXCLUDED_BY_DEFAULT) are
left out unless `include_flagged=true`; then they are returned with their flags.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from statistics import fmean, pstdev
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api import schemas
from mandicast.api.deps import (
    commodity_or_404,
    data_as_of,
    date_range,
    decode_cursor,
    encode_cursor,
    get_db,
    series_or_404,
    series_ref,
    to_float,
)
from mandicast.api.errors import ApiError, bad_param
from mandicast.api.source import EXCLUDED_BY_DEFAULT, SOURCE, UNIT

router = APIRouter(tags=["prices"])
Id = Annotated[int, Query(ge=1, le=2_147_483_647)]
OptDate = Annotated[date | None, Query()]
Label = Annotated[str | None, Query(min_length=1, max_length=100)]
NOT_EXCLUDED = "NOT (po.flags && CAST(:ex AS text[]))"


def _item(r) -> dict:
    return {"date": r.date, "min_price": to_float(r.min_price), "max_price": to_float(r.max_price),
            "modal_price": to_float(r.modal_price), "variety": r.variety, "grade": r.grade, "flags": list(r.flags),
            "source_observation_id": r.source_observation_id}


@router.get("/prices", response_model=schemas.PricesResponse, response_model_by_alias=True,
            responses={404: {"model": schemas.ErrorResponse}})
def prices(db: Annotated[Session, Depends(get_db)], commodity_id: Id, market_id: Id,
           date_from: Annotated[date | None, Query(alias="from")] = None,
           date_to: Annotated[date | None, Query(alias="to")] = None,
           variety: Label = None, grade: Label = None, include_flagged: bool = False,
           limit: Annotated[int, Query(ge=1, le=500)] = 50,
           cursor: Annotated[str | None, Query(max_length=1000)] = None):
    """Daily history of one (commodity, mandi) series. Without `variety`/`grade`: the primary value of each day (one
    row per day). With them: every reported row of that variety/grade."""
    series = series_or_404(db, commodity_id, market_id)
    frm, to = date_range(date_from, date_to, series["last_date"] or date.today(), 365)
    conds = ["po.series_id = :s", "po.date BETWEEN :f AND :t"]
    params = {"s": series["id"], "f": frm, "t": to, "lim": limit + 1, "ex": EXCLUDED_BY_DEFAULT}
    if variety is None and grade is None:
        conds.append("po.is_primary")
    if variety is not None:
        conds.append("lower(po.variety) = lower(:v)")
        params["v"] = variety.strip()
    if grade is not None:
        conds.append("lower(po.grade) = lower(:g)")
        params["g"] = grade.strip()
    if not include_flagged:
        conds.append(NOT_EXCLUDED)
    after = decode_cursor(cursor, (date, int))
    if after:
        conds.append("(po.date, po.id) > (:ad, :ai)")
        params.update(ad=after[0], ai=after[1])
    rows = db.execute(text(
        "SELECT po.id, po.date, po.min_price, po.max_price, po.modal_price, po.variety, po.grade, po.flags, "
        f"po.source_observation_id FROM price_observations po WHERE {' AND '.join(conds)} "
        "ORDER BY po.date, po.id LIMIT :lim"), params).all()
    nxt = encode_cursor([rows[limit - 1].date, rows[limit - 1].id]) if len(rows) > limit else None
    return {"series": series_ref(series), "unit": UNIT, "from": frm, "to": to,
            "rows": "primary" if variety is None and grade is None else "variety_grade",
            "include_flagged": include_flagged, "items": [_item(r) for r in rows[:limit]], "next_cursor": nxt,
            "data_as_of": data_as_of(db, commodity_id), "source": SOURCE}


@router.get("/prices/latest", response_model=schemas.LatestResponse, responses={404: {"model": schemas.ErrorResponse}})
def latest(db: Annotated[Session, Depends(get_db)], commodity_id: Id, state_id: Id | None = None,
           district_id: Id | None = None, max_age_days: Annotated[int, Query(ge=1, le=3650)] = 30,
           include_flagged: bool = False, limit: Annotated[int, Query(ge=1, le=500)] = 50,
           cursor: Annotated[str | None, Query(max_length=1000)] = None):
    """Latest reported price of each mandi for a commodity, if it reported within `max_age_days` of the latest
    arrival date of that commodity."""
    commodity = commodity_or_404(db, commodity_id)
    latest_date = db.execute(text("SELECT max(last_date) FROM price_series WHERE commodity_id = :c"),
                             {"c": commodity_id}).scalar_one()
    conds = ["ps.commodity_id = :c", "ps.last_date >= CAST(:ld AS date) - :age"]
    params = {"c": commodity_id, "ld": latest_date, "age": max_age_days, "lim": limit + 1, "ex": EXCLUDED_BY_DEFAULT}
    if state_id:
        conds.append("s.id = :st")
        params["st"] = state_id
    if district_id:
        conds.append("d.id = :di")
        params["di"] = district_id
    after = decode_cursor(cursor, (str, int))
    if after:
        conds.append("(coalesce(m.display_name, m.name), ps.id) > (:an, :ai)")
        params.update(an=after[0], ai=after[1])
    flag_cond = "" if include_flagged else f"AND {NOT_EXCLUDED}"
    rows = db.execute(text(
        "SELECT ps.id AS series_id, m.id AS market_id, coalesce(m.display_name, m.name) AS market, "
        "d.id AS district_id, coalesce(d.display_name, d.name) AS district, s.id AS state_id, "
        "coalesce(s.display_name, s.name) AS state, "
        "po.date, po.min_price, po.max_price, po.modal_price, po.variety, po.grade, po.flags "
        "FROM price_series ps JOIN markets m ON m.id = ps.market_id JOIN districts d ON d.id = m.district_id "
        "JOIN states s ON s.id = d.state_id CROSS JOIN LATERAL (SELECT po.date, po.min_price, po.max_price, "
        "po.modal_price, po.variety, po.grade, po.flags FROM price_observations po WHERE po.series_id = ps.id "
        f"AND po.is_primary AND po.date <= ps.last_date {flag_cond} ORDER BY po.date DESC LIMIT 1) po "
        f"WHERE {' AND '.join(conds)} ORDER BY 3, 1 LIMIT :lim"), params).all()
    items = [{"series_id": r.series_id,
              "market": {"id": r.market_id, "name": r.market, "district": {"id": r.district_id, "name": r.district},
                         "state": {"id": r.state_id, "name": r.state}},
              "date": r.date, "days_behind_latest": (latest_date - r.date).days, "min_price": to_float(r.min_price),
              "max_price": to_float(r.max_price), "modal_price": to_float(r.modal_price), "variety": r.variety,
              "grade": r.grade, "flags": list(r.flags)} for r in rows[:limit]]
    nxt = encode_cursor([rows[limit - 1].market, rows[limit - 1].series_id]) if len(rows) > limit else None
    return {"commodity": {"id": commodity["id"], "name": commodity["name"]}, "unit": UNIT,
            "latest_arrival_date": latest_date, "max_age_days": max_age_days, "items": items, "next_cursor": nxt,
            "data_as_of": data_as_of(db, commodity_id), "source": SOURCE}


MARKET_IDS = re.compile(r"^\d{1,10}(,\d{1,10}){1,9}$")


@router.get("/compare", response_model=schemas.CompareResponse, response_model_by_alias=True,
            responses={404: {"model": schemas.ErrorResponse}})
def compare(db: Annotated[Session, Depends(get_db)], commodity_id: Id,
            market_ids: Annotated[str, Query(max_length=120, description="2–10 comma-separated market ids")],
            date_from: Annotated[date | None, Query(alias="from")] = None,
            date_to: Annotated[date | None, Query(alias="to")] = None):
    """Modal prices of one commodity at 2–10 mandis on the dates when all of them reported (unflagged primary
    values). Default range: the 90 days up to the latest report among them."""
    if not MARKET_IDS.match(market_ids):
        raise bad_param("market_ids must be 2–10 comma-separated integers.")
    ids = list(dict.fromkeys(int(x) for x in market_ids.split(",")))
    if len(ids) < 2:
        raise bad_param("market_ids must contain at least 2 different markets.")
    series = [series_or_404(db, commodity_id, m) for m in ids]
    last = max((s["last_date"] for s in series if s["last_date"]), default=date.today())
    frm, to = date_range(date_from, date_to, last, 90)
    rows = db.execute(text(
        "SELECT po.series_id, po.date, po.modal_price FROM price_observations po WHERE po.series_id = ANY(:ids) "
        f"AND po.is_primary AND po.date BETWEEN :f AND :t AND {NOT_EXCLUDED} ORDER BY po.date"),
        {"ids": [s["id"] for s in series], "f": frm, "t": to, "ex": EXCLUDED_BY_DEFAULT}).all()
    by_date: dict[date, dict[int, float]] = {}
    counts = {s["id"]: 0 for s in series}
    for r in rows:
        by_date.setdefault(r.date, {})[r.series_id] = to_float(r.modal_price)
        counts[r.series_id] += 1
    order = [s["id"] for s in series]
    items = [{"date": d, "modal_prices": [v[i] for i in order]} for d, v in sorted(by_date.items())
             if len(v) == len(order)]
    return {"commodity": {"id": series[0]["commodity"]["id"], "name": series[0]["commodity"]["name"]}, "unit": UNIT,
            "from": frm, "to": to,
            "markets": [{"market_id": s["market"]["id"], "market": s["market"]["name"], "state": s["market"]["state"],
                         "series_id": s["id"], "days_reported_in_range": counts[s["id"]]} for s in series],
            "common_dates": len(items), "items": items, "data_as_of": data_as_of(db, commodity_id), "source": SOURCE}


TREND_DEFINITIONS = {
    "latest": "the most recent unflagged primary report",
    "vs_previous_7_days": "latest price vs the average of reports dated in the 7 days before it",
    "vs_previous_30_days": "latest price vs the average of reports dated in the 30 days before it",
    "range_52_weeks": "lowest and highest report in the 364 days up to the latest report, and where the latest lies",
    "volatility_91_days_pct": "standard deviation of log changes between consecutive reports in the 91 days up to the "
                              "latest report (needs at least 3 changes)",
}


@router.get("/trends", response_model=schemas.TrendsResponse, responses={404: {"model": schemas.ErrorResponse}})
def trends(db: Annotated[Session, Depends(get_db)], commodity_id: Id, market_id: Id):
    """Descriptive statistics of recent prices. They describe the past and are not forecasts."""
    series = series_or_404(db, commodity_id, market_id)
    rows = db.execute(text(
        "SELECT po.id, po.date, po.min_price, po.max_price, po.modal_price, po.variety, po.grade, po.flags, "
        "po.source_observation_id FROM price_observations po WHERE po.series_id = :s AND po.is_primary "
        f"AND po.date > CAST(:last AS date) - 400 AND {NOT_EXCLUDED} ORDER BY po.date"),
        {"s": series["id"], "last": series["last_date"] or date.today(), "ex": EXCLUDED_BY_DEFAULT}).all()
    out = {"series": series_ref(series), "unit": UNIT, "latest": None, "definitions": TREND_DEFINITIONS,
           "note": "Descriptive statistics of past reports, not a forecast.",
           "data_as_of": data_as_of(db, commodity_id), "source": SOURCE}
    empty_w = {"reports": 0, "average": None, "change_pct": None}
    if not rows:
        return {**out, "vs_previous_7_days": empty_w, "vs_previous_30_days": empty_w,
                "range_52_weeks": {"reports": 0, "low": None, "high": None, "position_pct": None},
                "volatility_91_days_pct": None}
    last = rows[-1]
    lp = float(last.modal_price)

    def window(days: int) -> dict:
        vals = [float(r.modal_price) for r in rows[:-1] if r.date >= last.date - timedelta(days=days)]
        if not vals:
            return empty_w
        avg = fmean(vals)
        return {"reports": len(vals), "average": round(avg, 2), "change_pct": round(100 * (lp / avg - 1), 2)}

    year = [float(r.modal_price) for r in rows if r.date > last.date - timedelta(days=364)]
    lo, hi = min(year), max(year)
    recent = [r for r in rows if r.date > last.date - timedelta(days=91)]
    changes = [math.log(float(b.modal_price) / float(a.modal_price)) for a, b in zip(recent, recent[1:], strict=False)]
    vol = round(100 * pstdev(changes), 2) if len(changes) >= 3 else None
    return {**out, "latest": _item(last), "vs_previous_7_days": window(7), "vs_previous_30_days": window(30),
            "range_52_weeks": {"reports": len(year), "low": round(lo, 2), "high": round(hi, 2),
                               "position_pct": round(100 * (lp - lo) / (hi - lo), 1) if hi > lo else None},
            "volatility_91_days_pct": vol}


__all__ = ["router", "ApiError"]
