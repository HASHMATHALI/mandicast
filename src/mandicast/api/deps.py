"""Request dependencies: read-only database session, strict query parameters, cursors, date ranges, lookups."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator
from datetime import date, timedelta

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api.errors import ApiError, bad_param

MAX_RANGE_DAYS = 5 * 365 + 1  # "range > 5 years" is rejected (docs/07 §1)


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _query_names(dependant) -> set[str]:
    names = {p.alias for p in dependant.query_params}
    for d in dependant.dependencies:
        names |= _query_names(d)
    return names


async def strict_query(request: Request) -> None:
    """Reject unknown query parameters (typos would otherwise be silently ignored) and repeated ones (parameter
    pollution: which value wins would be ambiguous)."""
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return
    allowed = _query_names(dependant)
    keys = [k for k, _ in request.query_params.multi_items()]
    unknown = sorted({k for k in keys if k not in allowed})
    if unknown:
        raise bad_param("Unknown query parameter(s).", unknown=unknown[:10], allowed=sorted(allowed))
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        raise bad_param("Query parameter(s) given more than once.", repeated=repeated[:10])


def encode_cursor(values: list) -> str:
    raw = json.dumps(values, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None, kinds: tuple[type, ...]) -> list | None:
    """Decode an opaque cursor made by `encode_cursor`; `kinds` are the expected types (date, int, str)."""
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) != len(kinds):
            raise ValueError
        out = []
        for v, k in zip(values, kinds, strict=True):
            if k is date:
                out.append(date.fromisoformat(v))
            elif k is int:
                if not isinstance(v, int) or isinstance(v, bool):
                    raise ValueError
                out.append(v)
            else:
                if not isinstance(v, str) or len(v) > 500:
                    raise ValueError
                out.append(v)
        return out
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise bad_param("Invalid cursor; use the next_cursor value from the previous page.") from None


def date_range(frm: date | None, to: date | None, default_to: date, default_days: int) -> tuple[date, date]:
    to = to or default_to
    frm = frm or to - timedelta(days=default_days - 1)
    if frm > to:
        raise bad_param("'from' must not be after 'to'.", **{"from": str(frm), "to": str(to)})
    if (to - frm).days + 1 > MAX_RANGE_DAYS:
        raise ApiError(400, "date_range_too_long", "The date range is longer than 5 years.",
                       {"from": str(frm), "to": str(to), "max_days": MAX_RANGE_DAYS})
    return frm, to


def commodity_or_404(s: Session, commodity_id: int) -> dict:
    row = s.execute(text("SELECT id, name, price_unit FROM commodities WHERE id = :c"),
                    {"c": commodity_id}).one_or_none()
    if row is None:
        raise ApiError(404, "commodity_not_found", f"No commodity with id {commodity_id}.",
                       {"commodity_id": commodity_id})
    return dict(row._mapping)


def market_or_404(s: Session, market_id: int) -> dict:
    row = s.execute(text(
        "SELECT m.id, coalesce(m.display_name, m.name) AS name, d.id AS district_id, "
        "coalesce(d.display_name, d.name) AS district, s.id AS state_id, coalesce(s.display_name, s.name) AS state "
        "FROM markets m JOIN districts d ON d.id = m.district_id JOIN states s ON s.id = d.state_id WHERE m.id = :m"),
        {"m": market_id}).one_or_none()
    if row is None:
        raise ApiError(404, "market_not_found", f"No market with id {market_id}.", {"market_id": market_id})
    return dict(row._mapping)


def series_or_404(s: Session, commodity_id: int, market_id: int) -> dict:
    commodity = commodity_or_404(s, commodity_id)
    market = market_or_404(s, market_id)
    row = s.execute(text(
        "SELECT id, first_date, last_date, n_days, dominant_variety, dominant_grade, eligible_for_forecast, "
        "eligibility_reason, eligibility_origin FROM price_series WHERE commodity_id = :c AND market_id = :m"),
        {"c": commodity_id, "m": market_id}).one_or_none()
    if row is None:
        raise ApiError(404, "series_not_found",
                       f"No price series for {commodity['name']} at {market['name']}.",
                       {"commodity_id": commodity_id, "market_id": market_id})
    return {"commodity": commodity, "market": market, **dict(row._mapping)}


def series_ref(series: dict) -> dict:
    m = series["market"]
    return {"series_id": series["id"], "commodity_id": series["commodity"]["id"],
            "commodity": series["commodity"]["name"], "market_id": m["id"], "market": m["name"],
            "district_id": m["district_id"], "district": m["district"], "state_id": m["state_id"], "state": m["state"],
            "dominant_variety": series["dominant_variety"], "dominant_grade": series["dominant_grade"]}


def data_as_of(s: Session, commodity_id: int | None = None) -> dict:
    latest = s.execute(text(
        "SELECT max(last_date) FROM price_series WHERE (CAST(:c AS integer) IS NULL OR commodity_id = :c)"),
        {"c": commodity_id}).scalar_one()
    ingested = s.execute(text(
        "SELECT max(finished_at) FROM ingestion_jobs WHERE status = 'succeeded' "
        "AND kind IN ('daily', 'backfill', 'load_raw')")).scalar_one()
    return {"latest_arrival_date": latest, "last_successful_ingestion_at": ingested}


def to_float(v) -> float | None:
    return None if v is None else round(float(v), 2)
