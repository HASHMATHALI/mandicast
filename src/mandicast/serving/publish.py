"""Publish weekly forecasts (methodology C2, C7, C8; serving rules C9; operations rules C10).

- **Origin**: the latest Monday whose data is complete (`latest_complete_date` ≥ that Monday), as in the
  evaluation (C2): inputs are reports dated ≤ the origin; targets are the 14 days after it.
- **Series**: those eligible at the origin by the §2 rules, using data up to the origin only (C1).
- **Point forecasts**: the production model, if one is valid for the origin's quarter, and always the last value.
- **Intervals** (C9c, C10d): split conformal on log residuals of each forecast (model or last value), volatility-
  conditional when frozen so, calibrated on residuals of origins in the 364 days before the origin whose target
  dates are before the origin, from published forecasts and the stored walk-forward predictions; the window grows by
  whole weeks (up to 728 days) until every (horizon, tercile) group has 100 residuals.
- **Served forecast** (C10c as amended): the model whenever a valid production model exists; last value for every
  day if none exists. The 8-week comparison of model and last value (≥ 500 matured points) is computed at every
  publication and stored as information (automatic switching was dropped after the development simulation showed
  it hurt more often than it helped; docs/06 §9b). The model's own value is always stored (`model_point`).
- **Drift** (C10e): PSI of the origin's features against the model's reference; reported, not acted on.
- **Idempotent**: one run per (commodity, origin); re-running does nothing; published values are never overwritten.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from mandicast.clean.build import latest_complete_date
from mandicast.config import get_settings
from mandicast.db.session import session_scope
from mandicast.forecast import gbm, intervals
from mandicast.forecast.data import eligibility_by_origin, series_info
from mandicast.forecast.run import OUT
from mandicast.jobs import SERVING_LOCK_ID, run_job
from mandicast.serving import registry

log = logging.getLogger(__name__)
CALIBRATION_DAYS = 364
MAX_CALIBRATION_DAYS = 728
MIN_RESIDUALS_PER_GROUP = 100
FALLBACK_DAYS = 56          # C10c: 8 weeks of origins
FALLBACK_MIN_POINTS = 500   # C10c
AUTO_FALLBACK = False       # C10c amendment (2026-11-27 07:52): the comparison is informational only
PSI_NOTICE, PSI_WARNING = 0.10, 0.25  # C10e (conventional thresholds)
DAY_RANGES = {"1-7": (1, 7), "8-14": (8, 14)}


class ModelUnavailableError(RuntimeError):
    pass


def monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def choose_origin(session: Session, commodity: str, today: date) -> tuple[date, date]:
    """(origin, data complete through): the latest Monday on or before the latest complete arrival date."""
    complete = latest_complete_date(session, commodity, today)
    if complete is None:
        raise ModelUnavailableError(f"no recent source data for {commodity}")
    return monday_on_or_before(complete), complete


def production_model(session: Session, commodity: str, origin: date) -> dict:
    row = session.execute(text(
        "SELECT mv.id, mv.version, mv.artifact_path, mv.artifact_sha256, mv.valid_from, mv.valid_to, "
        "mv.interval_method, mv.training FROM model_versions mv JOIN commodities c ON c.id = mv.commodity_id "
        "WHERE c.name = :c AND mv.status = 'production'"), {"c": commodity}).one_or_none()
    if row is None:
        raise ModelUnavailableError(f"no production model for {commodity}; run `mandicast retrain`")
    if not row.valid_from <= origin <= row.valid_to:
        raise ModelUnavailableError(
            f"production model {row.version} serves {row.valid_from}…{row.valid_to}, not origin {origin}; retrain for "
            f"the quarter starting {registry.quarter_start(origin)} (`mandicast retrain`)")
    return dict(row._mapping)


# --- residual pool: what each forecast would have said, for points whose outcome is known --------------------

POOL_SCHEMA = {"series_id": pl.Int32, "origin": pl.Date, "h": pl.Int16, "yhat_model": pl.Float64,
               "yhat_lv": pl.Float64}


def residual_pool(session: Session, commodity: str, origin: date) -> pl.DataFrame:
    """(series_id, origin, h, yhat_model, yhat_lv) with targets before `origin`: published forecasts first (their
    model value and last price), then the walk-forward predictions of the served model and of last value."""
    lo = origin - timedelta(days=MAX_CALIBRATION_DAYS)
    rows = session.execute(text(
        "SELECT f.series_id, f.issued_on, f.horizon_days, f.model_point, f.last_price FROM forecasts f "
        "JOIN forecast_runs fr ON fr.id = f.run_id JOIN commodities c ON c.id = fr.commodity_id "
        "WHERE c.name = :c AND f.issued_on >= :lo AND f.target_date < :o"),
        {"c": commodity, "lo": lo, "o": origin}).all()
    parts = [pl.DataFrame([(r[0], r[1], r[2], None if r[3] is None else float(r[3]), float(r[4])) for r in rows],
                          schema=POOL_SCHEMA, orient="row")]
    choice = registry.frozen_choice(commodity)
    keys = ["series_id", "origin", "h"]
    for period in ("holdout", "dev"):
        lv_path = OUT / period / "last_value" / f"{commodity.lower()}.parquet"
        if not lv_path.exists():
            continue
        lv = pl.read_parquet(lv_path).select(pl.col("series_id").cast(pl.Int32), pl.col("origin").cast(pl.Date),
                                             pl.col("h").cast(pl.Int16), pl.col("yhat").cast(pl.Float64)
                                             .alias("yhat_lv"))
        for model, hs in ((choice["served"]["days_1_7"], (1, 7)), (choice["served"]["days_8_14"], (8, 14))):
            path = OUT / period / model / f"{commodity.lower()}.parquet"
            if path.exists():
                m = pl.read_parquet(path).select(
                    pl.col("series_id").cast(pl.Int32), pl.col("origin").cast(pl.Date), pl.col("h").cast(pl.Int16),
                    pl.col("yhat").cast(pl.Float64).alias("yhat_model")).filter(pl.col("h").is_between(*hs))
                parts.append(m.join(lv, on=keys, how="inner").select(list(POOL_SCHEMA)))
    pool = pl.concat(parts).unique(keys, keep="first", maintain_order=True)
    return pool.filter((pl.col("origin") + pl.duration(days=pl.col("h"))) < origin)


def with_actuals(primary: pl.DataFrame, pool: pl.DataFrame, origin: date, days: int) -> pl.DataFrame:
    """Pool points with origins in [origin − days, origin) and targets before the origin, with the reported price
    `y`; targets flagged `local_outlier` are excluded (as in the evaluation)."""
    p = pool.filter((pl.col("origin") >= origin - timedelta(days=days)) & (pl.col("origin") < origin)).with_columns(
        (pl.col("origin") + pl.duration(days=pl.col("h"))).alias("target_date")).filter(pl.col("target_date") < origin)
    y = primary.select("series_id", pl.col("date").alias("target_date"), pl.col("price").alias("y"), "input_ok")
    return p.join(y, on=["series_id", "target_date"], how="inner").filter(pl.col("input_ok"))


def calibration_set(primary: pl.DataFrame, preds: pl.DataFrame, origin: date, conditional: bool,
                    window_days: int = CALIBRATION_DAYS) -> pl.DataFrame:
    """Residuals r = log(y) − log(ŷ) of `preds` (series_id, origin, h, yhat) for origins in [origin − window,
    origin) and target dates before the origin (C9c)."""
    lo = origin - timedelta(days=window_days)
    p = preds.filter((pl.col("origin") >= lo) & (pl.col("origin") < origin)).with_columns(
        (pl.col("origin") + pl.duration(days=pl.col("h"))).alias("target_date")).filter(pl.col("target_date") < origin)
    y = primary.select("series_id", pl.col("date").alias("target_date"), pl.col("price").alias("y"), "input_ok")
    cal = (p.join(y, on=["series_id", "target_date"], how="inner").filter(pl.col("input_ok"))
           .with_columns((pl.col("y").log() - pl.col("yhat").log()).alias("r")))
    if conditional:
        cal = cal.join(intervals.origin_volatility(primary, cal.select("series_id", "origin").unique()),
                       on=["series_id", "origin"], how="left")
    return cal


def group_sizes(cal: pl.DataFrame, conditional: bool) -> pl.DataFrame:
    if conditional:
        cal = cal.with_columns(volatility_terciles(cal["vol"], cal["vol"]).alias("vt"))
    return cal.group_by(["h", "vt"] if conditional else ["h"]).len()


def choose_window(full: pl.DataFrame, origin: date, conditional: bool) -> tuple[int, pl.DataFrame, pl.DataFrame]:
    """Smallest window (364 days, then whole extra weeks up to 728) in which every group has enough residuals."""
    expected = 14 * (3 if conditional else 1)
    groups = pl.DataFrame({"len": [0]})
    for w in range(CALIBRATION_DAYS, MAX_CALIBRATION_DAYS + 1, 7):
        cal = full.filter(pl.col("origin") >= origin - timedelta(days=w))
        groups = group_sizes(cal, conditional) if cal.height else pl.DataFrame({"len": [0]})
        if groups.height == expected and int(groups["len"].min()) >= MIN_RESIDUALS_PER_GROUP:
            return w, cal, groups
    raise ModelUnavailableError(f"fewer than {MIN_RESIDUALS_PER_GROUP} calibration residuals in some group even with "
                                f"{MAX_CALIBRATION_DAYS} days ({groups.height} groups, smallest {groups['len'].min()})")


def volatility_terciles(cal_vol: pl.Series, vol: pl.Series) -> pl.Series:
    """Same tercile rule as intervals.apply (null volatility → middle tercile), returned for storage."""
    cuts = np.nanquantile(cal_vol.drop_nulls().to_numpy(), [1 / 3, 2 / 3])
    v = vol.cast(pl.Float64).fill_null(np.nan).to_numpy()
    out = np.where(np.isnan(v), 1, np.where(v <= cuts[0], 0, np.where(v <= cuts[1], 1, 2)))
    return pl.Series("volatility_tercile", out, dtype=pl.Int16)


def with_intervals(primary: pl.DataFrame, preds: pl.DataFrame, pool_preds: pl.DataFrame, origin: date,
                   conditional: bool) -> tuple[pl.DataFrame, dict]:
    """Conformal 80% / 90% ranges for `preds` (series_id, origin, h, yhat[, vol]) from the residuals of
    `pool_preds` (series_id, origin, h, yhat)."""
    full = calibration_set(primary, pool_preds, origin, conditional, MAX_CALIBRATION_DAYS)
    window, cal, groups = choose_window(full, origin, conditional)
    out = intervals.apply(preds, cal.select("h", "r", *(["vol"] if conditional else [])), conditional)
    out = out.with_columns(volatility_terciles(cal["vol"], out["vol"]) if conditional
                           else pl.lit(None, dtype=pl.Int16).alias("volatility_tercile"))
    return out, {"window_days": window,
                 "window_origins": [str(origin - timedelta(days=window)), str(origin - timedelta(days=1))],
                 "rule": "target dates before the origin (C9c)", "residuals": cal.height,
                 "smallest_group": int(groups["len"].min()), "groups": groups.height,
                 "origins_in_window": int(cal["origin"].n_unique())}


def fallback_decisions(primary: pl.DataFrame, pool: pl.DataFrame, origin: date) -> dict:
    """C10c: per day range, compare the model with last value over the previous 8 weeks of matured points (≥ 500).
    With AUTO_FALLBACK off (the amended rule) the result is informational and the model is served."""
    pts = with_actuals(primary, pool.filter(pl.col("yhat_model").is_not_null()), origin, FALLBACK_DAYS)
    out = {}
    for name, (a, b) in DAY_RANGES.items():
        d = pts.filter(pl.col("h").is_between(a, b))
        n = d.height
        wm = float((d["y"] - d["yhat_model"]).abs().sum() / d["y"].sum()) if n else None
        wl = float((d["y"] - d["yhat_lv"]).abs().sum() / d["y"].sum()) if n else None
        lost = n >= FALLBACK_MIN_POINTS and wm > wl
        if lost:
            reason = ("over the previous 8 weeks the model's error was higher than repeating the last price"
                      + ("" if AUTO_FALLBACK else " (shown for information; automatic switching is not applied)"))
        elif n < FALLBACK_MIN_POINTS:
            reason = "too few matured points to compare"
        else:
            reason = "the model was not worse than last value over the previous 8 weeks"
        out[name] = {"served_by": "last_value" if (lost and AUTO_FALLBACK) else "model", "model_lost_recently": lost,
                     "points": n, "wape_model": wm, "wape_last_value": wl,
                     "origins": int(d["origin"].n_unique()) if n else 0,
                     "window_origins": [str(origin - timedelta(days=FALLBACK_DAYS)), str(origin - timedelta(days=1))],
                     "reason": reason}
    return out


def psi(reference: dict, values: np.ndarray) -> float | None:
    """Population stability index of `values` against a reference histogram {"edges", "shares"} (C10e)."""
    vals = values[np.isfinite(values)]
    if not len(vals):
        return None
    counts = np.bincount(np.searchsorted(reference["edges"], vals, side="right"),
                         minlength=len(reference["edges"]) + 1)
    actual = np.maximum(counts / len(vals), 1e-4)
    expected = np.maximum(np.asarray(reference["shares"]), 1e-4)
    return float(((actual - expected) * np.log(actual / expected)).sum())


def drift(reference: dict | None, feats: pl.DataFrame) -> dict:
    if not reference:
        return {"status": "no reference for this model"}
    out: dict = {"features": {}}
    for f in registry.DRIFT_FEATURES:
        if f in reference:
            v = psi(reference[f], feats[f].cast(pl.Float64).fill_null(np.nan).to_numpy())
            level = None if v is None else ("warning" if v > PSI_WARNING else "notice" if v > PSI_NOTICE else "stable")
            out["features"][f] = {"psi": None if v is None else round(v, 4), "level": level}
    ref_n = reference.get("eligible_per_origin_mean")
    out["eligible_series"] = {"now": feats.height, "reference_mean": ref_n,
                              "ratio": round(feats.height / ref_n, 3) if ref_n else None}
    levels = [x["level"] for x in out["features"].values()]
    out["status"] = "warning" if "warning" in levels else "notice" if "notice" in levels else "stable"
    return out


@dataclass
class Prepared:
    commodity: str
    origin: date
    complete: date
    model: dict | None
    eligible: int
    rows: pl.DataFrame
    calibration: dict
    decisions: dict
    drift: dict


def prepare(engine: Engine, session: Session, commodity: str, origin: date, complete: date) -> Prepared:
    try:
        mv = production_model(session, commodity, origin)
        model = registry.load_model(Path(mv["artifact_path"]), mv["artifact_sha256"])
        no_model = None
    except ModelUnavailableError as exc:
        mv, model, no_model = None, None, str(exc)
    primary = registry.load_serving_primary(engine, commodity)
    primary = primary.filter(pl.col("date") <= complete)  # nothing after the data used to choose the origin
    states = series_info(engine, commodity).select("series_id", "state")
    pairs = eligibility_by_origin(primary, [origin])
    feats = gbm.origin_features(primary, pairs, states)
    float_cols = [c for c, t in feats.schema.items() if t == pl.Float64 and c != "last_price"]
    feats = feats.with_columns([pl.col(c).cast(pl.Float32) for c in float_cols])
    rows = gbm.expand_targets(feats, primary, registry.holidays_for(origin), with_y=False)
    base = rows.select("series_id", "origin", "h").join(
        feats.select("series_id", "origin", "last_price", (pl.col("origin") - pl.duration(days=pl.col("age_days")))
                     .cast(pl.Date).alias("last_date")), on=["series_id", "origin"])
    method = mv["interval_method"] if mv else registry.frozen_choice(commodity)["intervals"]["method"]
    conditional = method == "conditional"
    if conditional:
        base = base.join(intervals.origin_volatility(primary, pairs), on=["series_id", "origin"], how="left")
    pool = residual_pool(session, commodity, origin)
    keep = ["series_id", "origin", "h"]
    lv_out, lv_cal = with_intervals(primary, base.with_columns(pl.col("last_price").alias("yhat")),
                                    pool.select(*keep, pl.col("yhat_lv").alias("yhat")), origin, conditional)
    calibration = {"method": method, "last_value": lv_cal}
    if model is not None:
        m = gbm.predict(model, rows).select(*keep, "yhat")
        m_out, m_cal = with_intervals(primary, base.join(m, on=keep),
                                      pool.filter(pl.col("yhat_model").is_not_null())
                                      .select(*keep, pl.col("yhat_model").alias("yhat")), origin, conditional)
        calibration["model"] = m_cal
        decisions = fallback_decisions(primary, pool, origin)
        drift_info = drift((mv.get("training") or {}).get("drift_reference"), feats)
    else:
        m_out = None
        decisions = {name: {"served_by": "last_value", "reason": no_model} for name in DAY_RANGES}
        drift_info = {"status": "no model"}
    cols = ["yhat", "lo80", "hi80", "lo90", "hi90", "volatility_tercile"]
    lv_sel = lv_out.select(*keep, "last_date", "last_price", *[pl.col(c).alias(f"lv_{c}") for c in cols])
    if m_out is not None:
        out = lv_sel.join(m_out.select(*keep, *[pl.col(c).alias(f"m_{c}") for c in cols]), on=keep, how="left")
    else:
        out = lv_sel.with_columns([pl.lit(None, dtype=pl.Float64).alias(f"m_{c}") for c in cols[:-1]]
                                  + [pl.lit(None, dtype=pl.Int16).alias("m_volatility_tercile")])
    use_lv = pl.lit(False)
    for name, (a, b) in DAY_RANGES.items():
        if decisions[name]["served_by"] == "last_value":
            use_lv = use_lv | pl.col("h").is_between(a, b)
    out = out.with_columns(pl.when(use_lv).then(pl.lit("last_value")).otherwise(pl.lit("model")).alias("served_by"))
    out = out.with_columns([pl.when(pl.col("served_by") == "last_value").then(pl.col(f"lv_{c}"))
                            .otherwise(pl.col(f"m_{c}")).alias(c) for c in cols]
                           + [pl.col("m_yhat").alias("model_yhat")])
    return Prepared(commodity, origin, complete, mv, pairs.height, out, calibration, decisions, drift_info)


def write(session: Session, prep: Prepared, job_id: int | None) -> dict:
    """Insert the run and its forecasts unless this commodity and origin were already published."""
    commodity_id = registry.commodity_id_of(session, prep.commodity)
    existing = session.execute(text(
        "SELECT id, series_forecast FROM forecast_runs WHERE commodity_id = :c AND origin = :o"),
        {"c": commodity_id, "o": prep.origin}).one_or_none()
    if existing is not None:
        return {"status": "already_published", "run_id": existing.id, "series_forecast": existing.series_forecast}
    r = prep.rows
    mv_id = prep.model["id"] if prep.model else None
    run_id = session.execute(text(
        "INSERT INTO forecast_runs (job_id, commodity_id, model_version_id, origin, data_complete_through, "
        "series_eligible, series_forecast, calibration, decisions, drift) VALUES (:j, :cid, :m, :o, :c, :e, :n, "
        "CAST(:cal AS jsonb), CAST(:dec AS jsonb), CAST(:dr AS jsonb)) RETURNING id"),
        {"j": job_id, "cid": commodity_id, "m": mv_id, "o": prep.origin, "c": prep.complete, "e": prep.eligible,
         "n": int(r["series_id"].n_unique()), "cal": json.dumps(prep.calibration),
         "dec": json.dumps(prep.decisions), "dr": json.dumps(prep.drift)}).scalar_one()
    rnd = lambda c: pl.col(c).round(2)  # noqa: E731
    recs = r.select(pl.col("series_id"), pl.col("h").cast(pl.Int32).alias("h"), pl.col("last_date"),
                    rnd("last_price").alias("last_price"), rnd("yhat").alias("point"), rnd("lo80").alias("lower80"),
                    rnd("hi80").alias("upper80"), rnd("lo90").alias("lower90"), rnd("hi90").alias("upper90"),
                    pl.col("volatility_tercile"), rnd("model_yhat").alias("model_point"), "served_by").to_dicts()
    for rec in recs:
        rec.update(run=run_id, m=mv_id, o=prep.origin, t=prep.origin + timedelta(days=rec["h"]))
    res = session.execute(text(
        "INSERT INTO forecasts (run_id, series_id, model_version_id, issued_on, target_date, horizon_days, last_date, "
        "last_price, point, lower80, upper80, lower90, upper90, volatility_tercile, model_point, served_by) VALUES "
        "(:run, :series_id, :m, :o, :t, :h, :last_date, :last_price, :point, :lower80, :upper80, :lower90, :upper90, "
        ":volatility_tercile, :model_point, :served_by) "
        "ON CONFLICT ON CONSTRAINT uq_forecast_run_series_target DO NOTHING"), recs)
    return {"status": "published", "run_id": run_id, "series_forecast": int(r["series_id"].n_unique()),
            "series_eligible": prep.eligible, "points": len(recs), "rows_inserted": res.rowcount,
            "model_version": prep.model["version"] if prep.model else None,
            "served_by": {k: v["served_by"] for k, v in prep.decisions.items()},
            "drift": prep.drift.get("status"), "calibration": prep.calibration, "decisions": prep.decisions}


def publish(engine: Engine, commodities: list[str] | None = None, origin: date | None = None,
            today: date | None = None) -> int:
    """Publish forecasts for every commodity (each in its own transaction). Returns the job id."""
    settings = get_settings()
    commodities = commodities or settings.commodities
    today = today or date.today()
    with run_job(engine, "publish_forecasts", {"commodities": commodities, "origin": origin, "today": today},
                 lock_id=SERVING_LOCK_ID) as job:
        failures = []
        for c in commodities:
            try:
                with session_scope(engine) as s:
                    o, complete = choose_origin(s, c, today)
                    if origin is not None:
                        if origin.weekday() != 0 or origin > o:
                            raise ValueError(f"origin {origin} must be a Monday with complete data (latest {o})")
                        o = origin
                    prep = prepare(engine, s, c, o, complete)
                    job.report[c] = write(s, prep, job.id)
                    job.counts["rows_inserted"] += job.report[c].get("rows_inserted", 0)
                log.info("forecasts %s at %s: %s", c, o, {k: v for k, v in job.report[c].items()
                                                          if k not in ("calibration", "decisions")})
            except ModelUnavailableError as exc:
                job.report[c] = {"status": "failed", "error": str(exc)}
                failures.append(c)
                log.error("forecasts %s: %s", c, exc)
        if failures:
            raise ModelUnavailableError(f"no forecasts published for {', '.join(failures)} (see job report)")
    return job.id


def preview(engine: Engine, commodity: str, origin: date | None = None, today: date | None = None) -> dict:
    """Compute what a publication would contain (served method per day range, drift, calibration) without writing."""
    today = today or date.today()
    with session_scope(engine) as s:
        o, complete = choose_origin(s, commodity, today)
        prep = prepare(engine, s, commodity, origin or o, complete)
    r = prep.rows
    return {"commodity": commodity, "origin": str(prep.origin), "model": prep.model["version"] if prep.model else None,
            "series": int(r["series_id"].n_unique()), "decisions": prep.decisions, "drift": prep.drift,
            "calibration": prep.calibration,
            "served_by_points": dict(r.group_by("served_by").len().iter_rows())}
