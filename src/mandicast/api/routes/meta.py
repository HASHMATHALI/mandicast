"""Freshness and health (docs/07 §5)."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api import schemas
from mandicast.api.deps import get_db
from mandicast.api.source import SOURCE
from mandicast.config import PROJECT_ROOT

router = APIRouter(tags=["meta"])


@router.get("/freshness", response_model=schemas.FreshnessResponse)
def freshness(db: Annotated[Session, Depends(get_db)]):
    """When data was last ingested, how recent it is per commodity, how many mandis stopped reporting, and the
    latest forecast origin."""
    ing = db.execute(text(
        "SELECT id, kind, finished_at, rows_fetched, rows_inserted FROM ingestion_jobs WHERE status = 'succeeded' "
        "AND kind IN ('daily', 'backfill', 'load_raw') ORDER BY finished_at DESC LIMIT 1")).one_or_none()
    pub = db.execute(text(
        "SELECT id, finished_at FROM ingestion_jobs WHERE status = 'succeeded' AND kind = 'publish_forecasts' "
        "ORDER BY finished_at DESC LIMIT 1")).one_or_none()
    rows = db.execute(text(
        "WITH l AS (SELECT commodity_id, max(last_date) AS latest FROM price_series GROUP BY commodity_id) "
        "SELECT c.id AS commodity_id, c.name AS commodity, l.latest AS latest_arrival_date, "
        "max(ps.eligibility_origin) AS latest_complete_date, count(ps.id) AS series, "
        "count(ps.id) FILTER (WHERE ps.last_date > l.latest - 14) AS reported_last_14_days, "
        "count(ps.id) FILTER (WHERE ps.last_date <= l.latest - 15 AND ps.last_date > l.latest - 90) AS "
        "stopped_reporting, count(ps.id) FILTER (WHERE ps.eligible_for_forecast) AS eligible_series, "
        "(SELECT max(fr.origin) FROM forecast_runs fr JOIN model_versions mv ON mv.id = fr.model_version_id "
        " WHERE mv.commodity_id = c.id) AS latest_forecast_origin "
        "FROM commodities c LEFT JOIN l ON l.commodity_id = c.id LEFT JOIN price_series ps ON ps.commodity_id = c.id "
        "GROUP BY c.id, l.latest ORDER BY c.name")).all()
    return {"last_successful_ingestion": dict(ing._mapping) if ing else None,
            "last_forecast_publication": dict(pub._mapping) if pub else None,
            "commodities": [dict(r._mapping) for r in rows], "source": SOURCE}


@router.get("/monitoring", response_model=schemas.MonitoringResponse)
def monitoring(db: Annotated[Session, Depends(get_db)], commodity_id: Annotated[int | None, Query(
        ge=1, le=2_147_483_647)] = None):
    """Operations view (methodology C10): live accuracy of published forecasts, the latest publication's fallback
    decisions and drift, the production model and its gate, recent lifecycle events, and the missing-update checks
    from the last monitor run."""
    job = db.execute(text(
        "SELECT finished_at, quality_report FROM ingestion_jobs WHERE kind = 'monitor' AND status = 'succeeded' "
        "ORDER BY id DESC LIMIT 1")).one_or_none()
    checks = (job.quality_report or {}).get("checks", []) if job else []
    rows = db.execute(text("SELECT id, name FROM commodities WHERE (CAST(:c AS integer) IS NULL OR id = :c) "
                           "ORDER BY name"), {"c": commodity_id}).all()
    out = []
    for c in rows:
        live = db.execute(text(
            "SELECT as_of, day_range, origins, points, wape_model, wape_last_value, wape_served, coverage80, "
            "coverage90, direction_moves_ge_2pct FROM monitoring_snapshots WHERE commodity_id = :c AND as_of = "
            "(SELECT max(as_of) FROM monitoring_snapshots WHERE commodity_id = :c) ORDER BY day_range"),
            {"c": c.id}).all()
        run = db.execute(text(
            "SELECT fr.origin, fr.created_at, mv.version, fr.decisions, fr.drift, fr.series_forecast "
            "FROM forecast_runs fr LEFT JOIN model_versions mv ON mv.id = fr.model_version_id "
            "WHERE fr.commodity_id = :c ORDER BY fr.origin DESC LIMIT 1"), {"c": c.id}).one_or_none()
        prod = db.execute(text(
            "SELECT version, training_cutoff, valid_from, valid_to, promoted_at, status_reason, gate->'passed' AS "
            "gate_passed, gate->'failed' AS gate_failed, gate->'metrics' AS gate_metrics FROM model_versions "
            "WHERE commodity_id = :c AND status = 'production'"), {"c": c.id}).one_or_none()
        events = db.execute(text(
            "SELECT e.created_at, e.event, mv.version, e.reason FROM model_events e LEFT JOIN model_versions mv "
            "ON mv.id = e.model_version_id WHERE e.commodity_id = :c ORDER BY e.id DESC LIMIT 5"), {"c": c.id}).all()
        out.append({"commodity_id": c.id, "commodity": c.name, "live": [dict(r._mapping) for r in live],
                    "latest_publication": dict(run._mapping) if run else None,
                    "production_model": dict(prod._mapping) if prod else None,
                    "recent_events": [dict(e._mapping) for e in events],
                    "warnings": [x for x in checks if x.get("commodity") == c.name and not x.get("ok")]})
    return {"checked_at": job.finished_at if job else None,
            "global_warnings": [x for x in checks if x.get("commodity") is None and not x.get("ok")],
            "commodities": out, "rules": "docs/06_forecasting_methodology.md §9b (C10)"}


@router.get("/health", response_model=schemas.HealthResponse, responses={503: {"model": schemas.HealthResponse}})
async def health(request: Request):
    """Liveness and dependencies: database, Redis, production model artifacts. 503 if the database is down;
    `degraded` if Redis is down (the API still serves, without shared cache and rate limits)."""
    from anyio import to_thread
    state = request.app.state
    checks: dict = {}

    def db_check() -> dict:
        t = time.perf_counter()
        with state.session_factory() as s:
            s.execute(text("SELECT 1"))
            models = s.execute(text(
                "SELECT c.name, mv.version, mv.artifact_path FROM model_versions mv JOIN commodities c "
                "ON c.id = mv.commodity_id WHERE mv.status = 'production' ORDER BY c.name")).all()
        missing = [m.version for m in models if not (PROJECT_ROOT / m.artifact_path).exists()]
        return {"database": {"ok": True, "ms": round(1000 * (time.perf_counter() - t), 1)},
                "models": {"ok": not missing, "production": len(models), "missing_artifacts": missing}}

    try:
        checks.update(await to_thread.run_sync(db_check))
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    if state.redis is None:
        checks["redis"] = {"ok": False, "error": "not configured"}
    else:
        t = time.perf_counter()
        try:
            await state.redis.ping()
            checks["redis"] = {"ok": True, "ms": round(1000 * (time.perf_counter() - t), 1)}
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            checks["redis"] = {"ok": False, "error": type(exc).__name__}
    if not checks["database"]["ok"]:
        return JSONResponse({"status": "down", "checks": checks}, status_code=503)
    ok = checks["redis"]["ok"] and checks.get("models", {}).get("ok", False)
    return {"status": "ok" if ok else "degraded", "checks": checks}
