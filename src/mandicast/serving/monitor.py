"""Live monitoring of published forecasts and missing-update checks (methodology C10c, C10f).

Live metrics use only forecasts as they were published (never recomputed) and reported prices that have arrived
since: per crop and day range, over the last 8 publications, the WAPE of the model's own value (`model_point`), of
the last price, and of what was served, the 80% / 90% range coverage of what was served, and the direction of
moves of at least 2% for the model. Results are stored per day in `monitoring_snapshots`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from mandicast.clean.build import latest_complete_date
from mandicast.db.session import session_scope
from mandicast.jobs import SERVING_LOCK_ID, run_job
from mandicast.serving import publish

log = logging.getLogger(__name__)
LIVE_PUBLICATIONS = 8
MAX_INGESTION_GAP_H = 26  # C10f
MAX_DATA_AGE_DAYS = 4     # C10f

LIVE_SQL = text(
    "WITH runs AS (SELECT id, origin FROM forecast_runs WHERE commodity_id = :c ORDER BY origin DESC LIMIT :k), "
    "pts AS (SELECT f.horizon_days AS h, f.point, f.model_point, f.last_price, f.lower80, f.upper80, f.lower90, "
    "  f.upper90, f.served_by, po.modal_price AS y, r.origin FROM forecasts f JOIN runs r ON r.id = f.run_id "
    "  JOIN price_observations po ON po.series_id = f.series_id AND po.date = f.target_date AND po.is_primary "
    "  WHERE NOT ('local_outlier' = ANY(po.flags)) AND f.target_date <= :as_of) "
    "SELECT CASE WHEN h <= 7 THEN '1-7' ELSE '8-14' END AS day_range, count(*) AS points, "
    "count(DISTINCT origin) AS origins, "
    "sum(abs(y - model_point)) FILTER (WHERE model_point IS NOT NULL) "
    "  / NULLIF(sum(y) FILTER (WHERE model_point IS NOT NULL), 0) AS wape_model, "
    "sum(abs(y - last_price)) / sum(y) AS wape_last_value, sum(abs(y - point)) / sum(y) AS wape_served, "
    "avg((y BETWEEN lower80 AND upper80)::int) AS coverage80, avg((y BETWEEN lower90 AND upper90)::int) AS coverage90, "
    "avg((sign(model_point - last_price) = sign(y - last_price))::int) FILTER (WHERE model_point IS NOT NULL "
    "  AND abs(y - last_price) >= 0.02 * last_price) AS direction_moves_ge_2pct, "
    "count(*) FILTER (WHERE served_by = 'last_value') AS served_by_last_value "
    "FROM pts GROUP BY 1 ORDER BY 1")


def live_metrics(session: Session, commodity_id: int, as_of: date) -> list[dict]:
    rows = session.execute(LIVE_SQL, {"c": commodity_id, "k": LIVE_PUBLICATIONS, "as_of": as_of}).all()
    return [{k: (float(v) if hasattr(v, "is_finite") else v) for k, v in r._mapping.items()} for r in rows]


def store(session: Session, commodity_id: int, as_of: date, metrics: list[dict]) -> None:
    for m in metrics:
        session.execute(text(
            "INSERT INTO monitoring_snapshots (as_of, commodity_id, day_range, origins, points, wape_model, "
            "wape_last_value, wape_served, coverage80, coverage90, direction_moves_ge_2pct, details) VALUES "
            "(:a, :c, :dr, :o, :n, :wm, :wl, :ws, :c8, :c9, :d, CAST(:det AS jsonb)) "
            "ON CONFLICT ON CONSTRAINT uq_monitoring_day DO UPDATE SET origins = EXCLUDED.origins, "
            "points = EXCLUDED.points, wape_model = EXCLUDED.wape_model, wape_last_value = EXCLUDED.wape_last_value, "
            "wape_served = EXCLUDED.wape_served, coverage80 = EXCLUDED.coverage80, coverage90 = EXCLUDED.coverage90, "
            "direction_moves_ge_2pct = EXCLUDED.direction_moves_ge_2pct, details = EXCLUDED.details, "
            "computed_at = now()"),
            {"a": as_of, "c": commodity_id, "dr": m["day_range"], "o": m["origins"], "n": m["points"],
             "wm": m["wape_model"], "wl": m["wape_last_value"], "ws": m["wape_served"], "c8": m["coverage80"],
             "c9": m["coverage90"], "d": m["direction_moves_ge_2pct"],
             "det": json.dumps({"served_by_last_value": m["served_by_last_value"],
                                "publications_considered": LIVE_PUBLICATIONS})})


def operational_checks(session: Session, commodities: list[str], today: date, now: datetime | None = None) -> list:
    """C10f warnings: ingestion gap, stale data, unpublished complete Monday, no valid production model."""
    now = now or datetime.now(UTC)
    out = []
    last = session.execute(text(
        "SELECT max(finished_at) FROM ingestion_jobs WHERE status = 'succeeded' "
        "AND kind IN ('daily', 'backfill', 'load_raw')")).scalar_one()
    gap_h = None if last is None else (now - last).total_seconds() / 3600
    out.append({"check": "ingestion_recent", "commodity": None,
                "ok": gap_h is not None and gap_h <= MAX_INGESTION_GAP_H,
                "detail": f"last successful ingestion {last} ({gap_h:.1f} h ago)" if gap_h is not None
                else "no successful ingestion"})
    for c in commodities:
        complete = latest_complete_date(session, c, today)
        age = None if complete is None else (today - complete).days
        out.append({"check": "data_current", "commodity": c, "ok": age is not None and age <= MAX_DATA_AGE_DAYS,
                    "detail": f"latest complete arrival date {complete} ({age} days old)"})
        if complete is None:
            continue
        monday = publish.monday_on_or_before(complete)
        latest_pub = session.execute(text(
            "SELECT max(fr.origin) FROM forecast_runs fr JOIN commodities k ON k.id = fr.commodity_id "
            "WHERE k.name = :c"), {"c": c}).scalar_one()
        out.append({"check": "latest_monday_published", "commodity": c, "ok": latest_pub is not None
                    and latest_pub >= monday, "detail": f"latest complete Monday {monday}, latest publication "
                                                        f"{latest_pub}"})
        try:
            mv = publish.production_model(session, c, monday)
            out.append({"check": "valid_production_model", "commodity": c, "ok": True, "detail": mv["version"]})
        except publish.ModelUnavailableError as exc:
            out.append({"check": "valid_production_model", "commodity": c, "ok": False, "detail": str(exc)})
    return out


def monitor(engine: Engine, commodities: list[str], today: date | None = None) -> int:
    """Compute and store live metrics for every crop, and record the missing-update checks in the job report."""
    today = today or date.today()
    with run_job(engine, "monitor", {"commodities": commodities, "today": today}, lock_id=SERVING_LOCK_ID) as job:
        with session_scope(engine) as s:
            for c in commodities:
                cid = s.execute(text("SELECT id FROM commodities WHERE name = :c"), {"c": c}).scalar_one()
                metrics = live_metrics(s, cid, today)
                store(s, cid, today, metrics)
                job.report[c] = {"live": metrics}
            checks = operational_checks(s, commodities, today)
        job.report["checks"] = checks
        job.report["warnings"] = [x for x in checks if not x["ok"]]
        for w in job.report["warnings"]:
            log.warning("monitor: %s %s: %s", w["check"], w["commodity"] or "", w["detail"])
    return job.id


__all__ = ["monitor", "live_metrics", "operational_checks"]
