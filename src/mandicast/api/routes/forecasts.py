"""Forecasts, eligibility and model information (docs/07 §4).

Every forecast carries the accuracy its method measured on the hold-out year (WAPE against the last-price
baseline, direction of larger moves, interval coverage), warnings where that evaluation was weak, and a disclaimer.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api import schemas
from mandicast.api.deps import commodity_or_404, get_db, series_or_404, series_ref, to_float
from mandicast.api.errors import ApiError, bad_param
from mandicast.api.source import DISCLAIMER, EXCLUDED_BY_DEFAULT, SOURCE, UNIT
from mandicast.clean.build import QUALITY_FLAGS_FOR_ELIGIBILITY, RULES

router = APIRouter(tags=["forecasts"])
Id = Annotated[int, Query(ge=1, le=2_147_483_647)]
STALE_DAYS = 14
LOW_TREE_COUNT = 10


def latest_run(db: Session, commodity_id: int) -> dict | None:
    """The latest publication for a commodity, with its model (None when last value was served for every day)."""
    row = db.execute(text(
        "SELECT fr.id AS run_id, fr.origin, fr.data_complete_through, fr.created_at, fr.decisions, fr.drift, "
        "mv.id AS model_id, mv.version, mv.kind, mv.training_cutoff, mv.valid_from, mv.valid_to, "
        "(mv.training->>'best_iteration')::int AS best_iteration, "
        "COALESCE(mv.evaluation, (SELECT m2.evaluation FROM model_versions m2 WHERE m2.commodity_id = fr.commodity_id "
        " ORDER BY m2.id DESC LIMIT 1)) AS evaluation "
        "FROM forecast_runs fr LEFT JOIN model_versions mv ON mv.id = fr.model_version_id "
        "WHERE fr.commodity_id = :c ORDER BY fr.origin DESC, fr.id DESC LIMIT 1"), {"c": commodity_id}).one_or_none()
    return dict(row._mapping) if row else None


def _row_for(rows: list[dict], h: int) -> dict:
    return next(r for r in rows if r["h"] == h)


def accuracy(evaluation: dict | None, h: int, method: str) -> dict | None:
    """Hold-out accuracy of the method that served day `h`: the model, or last value (its WAPE is the baseline)."""
    if not evaluation:
        return None
    s, b = _row_for(evaluation["served"], h), _row_for(evaluation["last_value"], h)
    common = {"evaluated_on": evaluation["period"], "horizon_days": h, "method": method, "points": s["n"],
              "baseline_wape": round(b["wape"], 4), "report": evaluation["report"]}
    if method == "last_value":
        return {**common, "wape": round(b["wape"], 4), "beats_baseline": None, "mase_vs_last_value": 1.0,
                "direction_accuracy_moves_ge_2pct": None, "interval_coverage": None}
    return {**common, "wape": round(s["wape"], 4), "beats_baseline": s["wape"] < b["wape"],
            "mase_vs_last_value": round(s["mase_vs_last_value"], 3),
            "direction_accuracy_moves_ge_2pct": round(s["dir_acc_moves_ge_2pct"], 4),
            "interval_coverage": {"0.8": round(s["coverage80"], 4), "0.9": round(s["coverage90"], 4)}}


def day_range(h: int) -> str:
    return "1-7" if h <= 7 else "8-14"


def warnings_for(acc: dict | None, run: dict, last_date: date, today: date, served: dict[str, str]) -> list[str]:
    w = []
    for rng, method in served.items():
        dec = (run["decisions"] or {}).get(rng, {})
        if method == "last_value":
            w.append(f"Days {rng.replace('-', '–')} show the last reported price, not a model forecast: "
                     f"{dec.get('reason') or 'no valid model'}.")
        elif dec.get("model_lost_recently"):
            w.append(f"Over the 8 weeks before this forecast, for days {rng.replace('-', '–')}, the model was less "
                     f"accurate than repeating the last price (WAPE {dec['wape_model']:.1%} vs "
                     f"{dec['wape_last_value']:.1%} on {dec['points']:,} reported prices).")
    if acc and acc["method"] == "model":
        if not acc["beats_baseline"]:
            w.append(f"On the hold-out year this method was not more accurate than simply repeating the last reported "
                     f"price at {acc['horizon_days']} days (WAPE {acc['wape']:.1%} vs {acc['baseline_wape']:.1%}).")
        cov80 = acc["interval_coverage"]["0.8"]
        if cov80 < 0.75:
            w.append(f"On the hold-out year only {cov80:.0%} of actual prices fell inside the 80% range, so the "
                     f"ranges are too narrow for this crop.")
        if run["best_iteration"] is not None and run["best_iteration"] < LOW_TREE_COUNT:
            w.append(f"This quarter's model stopped training after {run['best_iteration']} rounds, so its forecasts "
                     f"are close to the last reported price.")
    drift = run.get("drift") or {}
    if drift.get("status") == "warning":
        moved = [f for f, v in drift.get("features", {}).items() if v.get("level") == "warning"]
        w.append(f"Recent market conditions differ markedly from the model's reference period ({', '.join(moved)}); "
                 "treat the forecast with extra caution.")
    if (today - run["origin"]).days > STALE_DAYS:
        w.append(f"This forecast was issued on {run['origin']} and is more than {STALE_DAYS} days old.")
    if (run["origin"] - last_date).days > 7:
        w.append(f"The latest report used is from {last_date}, {(run['origin'] - last_date).days} days before the "
                 f"forecast was issued.")
    return w


def live_accuracy(db: Session, commodity_id: int, rng: str) -> dict | None:
    row = db.execute(text(
        "SELECT as_of, origins, points, wape_model, wape_last_value, wape_served, coverage80, coverage90 "
        "FROM monitoring_snapshots WHERE commodity_id = :c AND day_range = :r ORDER BY as_of DESC LIMIT 1"),
        {"c": commodity_id, "r": rng}).one_or_none()
    if row is None:
        return None
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row._mapping.items()}
    out["day_range"] = rng
    out["note"] = ("live accuracy of published forecasts whose prices have been reported"
                   + ("; too few points or weeks so far (needs 500 points from 4 publications), not yet reliable"
                      if row.points < 500 or row.origins < 4 else ""))
    return out


def eligibility_stats(db: Session, series_id: int, origin: date | None) -> dict:
    """The four sufficiency statistics of methodology §2 at `origin` (same definitions as the pipeline). The API
    uses the pipeline's latest evaluation origin, or the series' last report date if it was never evaluated."""
    if origin is None:
        return {}
    r = db.execute(text(
        "WITH p AS (SELECT po.date, po.flags, po.date - lag(po.date) OVER (ORDER BY po.date) AS gap "
        "  FROM price_observations po WHERE po.series_id = :s AND po.is_primary AND po.date <= :o "
        "  AND po.date > CAST(:o AS date) - 800) "
        "SELECT max(date) AS last_report, count(*) FILTER (WHERE date > CAST(:o AS date) - 730) AS d730, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) FILTER (WHERE date > CAST(:o AS date) - 365) AS mg, "
        "avg((flags && CAST(:qf AS text[]))::int) FILTER (WHERE date > CAST(:o AS date) - 365) AS fs FROM p"),
        {"s": series_id, "o": origin, "qf": list(QUALITY_FLAGS_FOR_ELIGIBILITY)}).one()
    return {"as_of": origin, "last_report": r.last_report,
            "days_since_last_report": (origin - r.last_report).days if r.last_report else None,
            "report_days_last_730": r.d730, "median_gap_days_last_365": None if r.mg is None else float(r.mg),
            "flagged_share_last_365": None if r.fs is None else round(float(r.fs), 4)}


@router.get("/forecasts", response_model=schemas.ForecastResponse,
            responses={404: {"model": schemas.ErrorResponse}, 422: {"model": schemas.ErrorResponse},
                       503: {"model": schemas.ErrorResponse}})
def forecast(db: Annotated[Session, Depends(get_db)], commodity_id: Id, market_id: Id,
             horizon: Annotated[int, Query(description="7 or 14 days", json_schema_extra={"enum": [7, 14]})] = 7):
    """The latest published forecast for a series: one point per day for `horizon` days after the origin, with
    80% and 90% ranges, which method served each day (model, or last value under the fallback rule), the served
    method's measured hold-out accuracy, live accuracy so far, and warnings."""
    if horizon not in (7, 14):
        raise bad_param("horizon must be 7 or 14.", horizon=horizon)
    series = series_or_404(db, commodity_id, market_id)
    run = latest_run(db, commodity_id)
    if run is None:
        raise ApiError(503, "model_unavailable", f"No forecasts have been published for "
                       f"{series['commodity']['name']} yet.", {"commodity_id": commodity_id})
    pts = db.execute(text(
        "SELECT target_date, horizon_days, point, lower80, upper80, lower90, upper90, last_date, last_price, "
        "model_point, served_by FROM forecasts WHERE run_id = :r AND series_id = :s AND horizon_days <= :h "
        "ORDER BY horizon_days"), {"r": run["run_id"], "s": series["id"], "h": horizon}).all()
    if not pts:
        eligible_now = bool(series["eligible_for_forecast"])
        if eligible_now:
            reason = (f"not eligible at the latest forecast origin ({run['origin']}); it is eligible now and will be "
                      "included in the next weekly forecast")
        else:
            reason = series["eligibility_reason"] or "eligibility has not been evaluated for this series yet"
        raise ApiError(422, "insufficient_data", f"No forecast for {series['commodity']['name']} at "
                       f"{series['market']['name']}: {reason}.",
                       {"reason": reason, "eligible_now": eligible_now, "latest_forecast_origin": str(run["origin"]),
                        "rules": RULES, "eligibility_endpoint": f"/v1/forecasts/eligibility?commodity_id="
                        f"{commodity_id}&market_id={market_id}"})
    actual = dict(db.execute(text(
        "SELECT po.date, po.modal_price FROM price_observations po WHERE po.series_id = :s AND po.is_primary "
        "AND po.date > :o AND po.date <= CAST(:o AS date) + :h AND NOT (po.flags && CAST(:ex AS text[]))"),
        {"s": series["id"], "o": run["origin"], "h": horizon, "ex": EXCLUDED_BY_DEFAULT}).all())
    served = {}
    for p in pts:
        served.setdefault(day_range(p.horizon_days), p.served_by)
    head = next(p for p in pts if p.horizon_days == max(x.horizon_days for x in pts))
    acc = accuracy(run["evaluation"], horizon, head.served_by)
    last_date = pts[0].last_date
    model = None if run["model_id"] is None else {k: run[k] for k in ("version", "kind", "training_cutoff",
                                                                      "valid_from", "valid_to")}
    decisions = run["decisions"] or {}
    return {
        "series": series_ref(series), "unit": UNIT, "horizon_days": horizon, "issued_on": run["origin"],
        "data_complete_through": run["data_complete_through"], "published_at": run["created_at"],
        "last_observed": {"date": last_date, "modal_price": to_float(pts[0].last_price)},
        "model": model,
        "served_by": served,
        "fallback": {k: v for k, v in decisions.items() if k in served} or None,
        "points": [{"date": p.target_date, "horizon_days": p.horizon_days, "modal_price": to_float(p.point),
                    "served_by": p.served_by,
                    "model_modal_price": to_float(p.model_point) if p.served_by == "last_value" else None,
                    "intervals": [{"level": 0.8, "lower": to_float(p.lower80), "upper": to_float(p.upper80)},
                                  {"level": 0.9, "lower": to_float(p.lower90), "upper": to_float(p.upper90)}],
                    "actual_modal_price": to_float(actual.get(p.target_date))} for p in pts],
        "accuracy": acc, "live_accuracy": live_accuracy(db, commodity_id, day_range(horizon)),
        "drift": run["drift"] or None,
        "warnings": warnings_for(acc, run, last_date, date.today(), served),
        "disclaimer": DISCLAIMER, "source": SOURCE}


@router.get("/forecasts/eligibility", response_model=schemas.EligibilityResponse,
            responses={404: {"model": schemas.ErrorResponse}})
def eligibility(db: Annotated[Session, Depends(get_db)], commodity_id: Id, market_id: Id):
    """Whether a series passes the sufficiency rules (methodology §2), the statistics behind the decision, and
    whether it is in the latest published forecast."""
    series = series_or_404(db, commodity_id, market_id)
    run = latest_run(db, commodity_id)
    in_run = bool(run) and db.execute(text(
        "SELECT EXISTS (SELECT 1 FROM forecasts WHERE run_id = :r AND series_id = :s)"),
        {"r": run["run_id"], "s": series["id"]}).scalar_one()
    return {"series": series_ref(series), "eligible": bool(series["eligible_for_forecast"]),
            "reason": series["eligibility_reason"], "evaluated_at_origin": series["eligibility_origin"],
            "rules": RULES,
            "stats": eligibility_stats(db, series["id"], series["eligibility_origin"] or series["last_date"]),
            "latest_forecast_origin": run["origin"] if run else None, "in_latest_forecast": in_run}


MODEL_SQL = (
    "SELECT mv.version, mv.kind, mv.commodity_id, c.name AS commodity, mv.status, mv.horizon_days, mv.training_cutoff, "
    "mv.valid_from, mv.valid_to, mv.promoted_at, mv.interval_method, (mv.training->>'best_iteration')::int AS "
    "best_iteration, (mv.training->>'examples')::int AS training_examples, mv.artifact_sha256, mv.code_sha256, "
    "(SELECT max(fr.origin) FROM forecast_runs fr WHERE fr.model_version_id = mv.id) AS latest_forecast_origin, "
    "mv.status_reason, (mv.gate->>'passed')::boolean AS gate_passed, mv.mlflow_registry_version "
    "FROM model_versions mv JOIN commodities c ON c.id = mv.commodity_id ")


@router.get("/models/current", response_model=list[schemas.ModelSummary])
def models_current(db: Annotated[Session, Depends(get_db)], commodity_id: Id | None = None):
    """The production model of each commodity (or of one)."""
    if commodity_id:
        commodity_or_404(db, commodity_id)
    rows = db.execute(text(MODEL_SQL + "WHERE mv.status = 'production' AND (CAST(:c AS integer) IS NULL OR "
                                       "mv.commodity_id = :c) ORDER BY c.name"), {"c": commodity_id}).all()
    return [dict(r._mapping) for r in rows]


@router.get("/models/{version}/evaluation", responses={404: {"model": schemas.ErrorResponse}})
def model_evaluation(db: Annotated[Session, Depends(get_db)],
                     version: Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")]) -> dict:
    """The walk-forward evaluation of this model's method: hold-out metrics against the last-price baseline and the
    other models on the same points, by horizon and by quarter (generated from the evaluation files)."""
    row = db.execute(text(MODEL_SQL + "WHERE mv.version = :v"), {"v": version}).one_or_none()
    if row is None:
        raise ApiError(404, "model_not_found", f"No model version {version}.", {"version": version})
    ev = db.execute(text("SELECT evaluation, training, gate FROM model_versions WHERE version = :v"),
                    {"v": version}).one()
    return {"model": dict(row._mapping), "evaluation": ev.evaluation, "gate": ev.gate,
            "training": {k: ev.training.get(k) for k in ("cutoff", "train_rows", "valid_rows", "best_iteration",
                                                          "examples", "series_in_training", "data_max_date",
                                                          "trained_at", "lightgbm")}}


@router.get("/models/history", response_model=schemas.ModelHistory, responses={404: {"model": schemas.ErrorResponse}})
def models_history(db: Annotated[Session, Depends(get_db)], commodity_id: Id):
    """Every model version of a commodity (production, candidates, rejected, archived) with its gate outcome, and the
    lifecycle events: registrations, promotions, rejections, rollbacks, fallbacks (newest first)."""
    commodity = commodity_or_404(db, commodity_id)
    versions = db.execute(text(MODEL_SQL + "WHERE mv.commodity_id = :c ORDER BY mv.id DESC"), {"c": commodity_id}).all()
    events = db.execute(text(
        "SELECT e.created_at, e.event, mv.version, e.reason FROM model_events e LEFT JOIN model_versions mv "
        "ON mv.id = e.model_version_id WHERE e.commodity_id = :c ORDER BY e.id DESC LIMIT 100"),
        {"c": commodity_id}).all()
    return {"commodity": {"id": commodity["id"], "name": commodity["name"]},
            "versions": [dict(v._mapping) for v in versions], "events": [dict(e._mapping) for e in events]}


__all__ = ["router"]
