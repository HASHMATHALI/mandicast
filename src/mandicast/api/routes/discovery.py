"""Discovery: commodities, states, districts, markets (docs/07 §2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api import schemas
from mandicast.api.deps import decode_cursor, encode_cursor, get_db, market_or_404
from mandicast.api.errors import bad_param

router = APIRouter(tags=["discovery"])
Id = Annotated[int, Query(ge=1, le=2_147_483_647)]
Search = Annotated[str | None, Query(min_length=1, max_length=100, description="Case-insensitive substring")]
Limit = Annotated[int, Query(ge=1, le=500)]


def like(q: str) -> str:
    """ILIKE pattern for a literal substring (wildcards in the input are escaped)."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/commodities", response_model=list[schemas.Commodity])
def commodities(db: Annotated[Session, Depends(get_db)], q: Search = None,
                has_forecasts: Annotated[bool | None, Query()] = None):
    where, params = "", {}
    if q:
        where, params = "WHERE c.name ILIKE :q ESCAPE '\\'", {"q": like(q)}
    rows = db.execute(text(
        "SELECT c.id, c.name, c.price_unit, count(ps.id) AS series, "
        "count(ps.id) FILTER (WHERE ps.eligible_for_forecast) AS eligible_series, min(ps.first_date) AS first_date, "
        "max(ps.last_date) AS last_date, EXISTS (SELECT 1 FROM forecast_runs fr JOIN model_versions mv "
        "ON mv.id = fr.model_version_id WHERE mv.commodity_id = c.id) AS has_forecasts "
        f"FROM commodities c LEFT JOIN price_series ps ON ps.commodity_id = c.id {where} "
        "GROUP BY c.id ORDER BY c.name"), params).all()
    out = [dict(r._mapping) for r in rows]
    return [r for r in out if has_forecasts is None or r["has_forecasts"] == has_forecasts]


@router.get("/states", response_model=list[schemas.StateItem])
def states(db: Annotated[Session, Depends(get_db)], commodity_id: Id | None = None):
    join = "JOIN price_series ps ON ps.market_id = m.id AND ps.commodity_id = :c" if commodity_id else ""
    rows = db.execute(text(
        "SELECT s.id, coalesce(s.display_name, s.name) AS name, count(DISTINCT m.id) AS markets FROM states s "
        f"JOIN districts d ON d.state_id = s.id JOIN markets m ON m.district_id = d.id {join} "
        "GROUP BY s.id ORDER BY 2"), {"c": commodity_id}).all()
    return [dict(r._mapping) for r in rows]


@router.get("/districts", response_model=list[schemas.DistrictItem])
def districts(db: Annotated[Session, Depends(get_db)], state_id: Id, commodity_id: Id | None = None):
    join = "JOIN price_series ps ON ps.market_id = m.id AND ps.commodity_id = :c" if commodity_id else ""
    rows = db.execute(text(
        "SELECT d.id, coalesce(d.display_name, d.name) AS name, d.state_id, count(DISTINCT m.id) AS markets "
        f"FROM districts d JOIN markets m ON m.district_id = d.id {join} "
        "WHERE d.state_id = :s GROUP BY d.id ORDER BY 2"), {"s": state_id, "c": commodity_id}).all()
    return [dict(r._mapping) for r in rows]


@router.get("/markets", response_model=schemas.Page[schemas.MarketItem])
def markets(db: Annotated[Session, Depends(get_db)], state_id: Id | None = None, district_id: Id | None = None,
            commodity_id: Id | None = None, q: Search = None,
            has_forecast: Annotated[bool | None, Query(description="Only mandis with (true) or without (false) a "
                                                       "forecast for commodity_id in its latest publication")] = None,
            limit: Limit = 50, cursor: Annotated[str | None, Query(max_length=1000)] = None):
    """Mandis, by name. Filter by state, district, a commodity they report, a name substring, and whether the
    latest forecast publication for the commodity includes them."""
    conds, params = [], {"lim": limit + 1}
    if has_forecast is not None:
        if not commodity_id:
            raise bad_param("has_forecast needs commodity_id.")
        conds.append(("" if has_forecast else "NOT ") + (
            "EXISTS (SELECT 1 FROM forecasts f JOIN price_series ps ON ps.id = f.series_id WHERE ps.market_id = m.id "
            "AND ps.commodity_id = :c AND f.run_id = (SELECT fr.id FROM forecast_runs fr JOIN model_versions mv ON "
            "mv.id = fr.model_version_id WHERE mv.commodity_id = :c ORDER BY fr.origin DESC, fr.id DESC LIMIT 1))"))
    if state_id:
        conds.append("s.id = :s")
        params["s"] = state_id
    if district_id:
        conds.append("d.id = :d")
        params["d"] = district_id
    if commodity_id:
        conds.append("EXISTS (SELECT 1 FROM price_series ps WHERE ps.market_id = m.id AND ps.commodity_id = :c)")
        params["c"] = commodity_id
    if q:
        conds.append("coalesce(m.display_name, m.name) ILIKE :q ESCAPE '\\'")
        params["q"] = like(q)
    after = decode_cursor(cursor, (str, int))
    if after:
        conds.append("(coalesce(m.display_name, m.name), m.id) > (:an, :ai)")
        params.update(an=after[0], ai=after[1])
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = db.execute(text(
        "SELECT m.id, coalesce(m.display_name, m.name) AS name, d.id AS district_id, "
        "coalesce(d.display_name, d.name) AS district, s.id AS state_id, coalesce(s.display_name, s.name) AS state "
        f"FROM markets m JOIN districts d ON d.id = m.district_id JOIN states s ON s.id = d.state_id {where} "
        "ORDER BY 2, 1 LIMIT :lim"), params).all()
    items = [{"id": r.id, "name": r.name, "district": {"id": r.district_id, "name": r.district},
              "state": {"id": r.state_id, "name": r.state}} for r in rows[:limit]]
    nxt = encode_cursor([rows[limit - 1].name, rows[limit - 1].id]) if len(rows) > limit else None
    return {"items": items, "next_cursor": nxt}


@router.get("/markets/{market_id}", response_model=schemas.MarketDetail,
            responses={404: {"model": schemas.ErrorResponse}})
def market(db: Annotated[Session, Depends(get_db)], market_id: Annotated[int, Path(ge=1, le=2_147_483_647)]):
    m = market_or_404(db, market_id)
    aliases = db.execute(text(
        "SELECT a.raw_name, a.rule, a.last_seen FROM markets mk JOIN districts d ON d.id = mk.district_id "
        "JOIN states s ON s.id = d.state_id JOIN name_aliases a ON a.entity = 'market' "
        "AND a.parent_key = s.name || '|' || d.name AND a.canonical_name = mk.name "
        "WHERE mk.id = :m ORDER BY a.last_seen DESC NULLS LAST, a.raw_name"), {"m": market_id}).all()
    comms = db.execute(text(
        "SELECT c.id AS commodity_id, c.name AS commodity, ps.id AS series_id, ps.first_date, ps.last_date, "
        "ps.n_days AS report_days, ps.eligible_for_forecast, ps.eligibility_reason FROM price_series ps "
        "JOIN commodities c ON c.id = ps.commodity_id WHERE ps.market_id = :m ORDER BY c.name"), {"m": market_id}).all()
    return {"id": m["id"], "name": m["name"], "district": {"id": m["district_id"], "name": m["district"]},
            "state": {"id": m["state_id"], "name": m["state"]},
            "aliases": [{"name_as_reported": a.raw_name, "rule": a.rule, "last_seen": a.last_seen} for a in aliases],
            "commodities": [dict(c._mapping) for c in comms]}
