"""Train, store and register serving models (methodology C6, C8; serving rules C9).

The served method is the one frozen in `experiments/forecast/frozen_choices.json` and evaluated once on the
hold-out year: a global LightGBM per commodity, retrained at the start of every calendar quarter on reports dated
before that day. A model version is that procedure trained at one quarter start; it may serve origins in that
quarter only (`valid_from` … `valid_to`).

Artifacts are LightGBM text files under `models/` (git-ignored) with their SHA-256 stored in `model_versions`; the
hash is checked every time a model is loaded. Training refuses to run if the evaluated forecasting code changed
after freezing, because the served model would then differ from the evaluated one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.forecast import gbm
from mandicast.forecast.data import eligibility_by_origin, load_primary, mondays, series_info
from mandicast.forecast.freeze import MLFLOW_URI, code_sha256
from mandicast.forecast.run import FROZEN, OUT

log = logging.getLogger(__name__)
SERVING_DATA_DIR = PROJECT_ROOT / "data" / "processed" / "serving"
KIND = "lightgbm_global"
HORIZON_DAYS = 14
HOLDOUT_REPORT = OUT / "holdout" / "_reports" / "final.json"


class CodeChangedError(RuntimeError):
    pass


class ArtifactIntegrityError(RuntimeError):
    pass


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def quarter_end(d: date) -> date:
    s = quarter_start(d)
    nxt = date(s.year + (s.month == 10), (s.month + 3 - 1) % 12 + 1, 1)
    return nxt - timedelta(days=1)


def quarter_label(d: date) -> str:
    return f"{d.year}q{(d.month - 1) // 3 + 1}"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def serving_code_sha256() -> str:
    h = hashlib.sha256()
    for f in sorted((PROJECT_ROOT / "src" / "mandicast" / "serving").glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def load_serving_primary(engine: Engine, commodity: str, refresh: bool = True) -> pl.DataFrame:
    """Primary daily series extracted fresh from the database into a serving cache (the Phase 3 caches under
    data/processed/ stay untouched, so the evaluation reports remain reproducible from them)."""
    return load_primary(engine, commodity, refresh=refresh, cache_dir=SERVING_DATA_DIR)


def holidays_for(until: date) -> pl.DataFrame:
    return gbm.holiday_table(range(2014, max(2028, until.year + 2)))


def frozen_choice(commodity: str) -> dict:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    return frozen["commodities"][commodity] | {"frozen_at": frozen["frozen_at"],
                                               "code_sha256_at_freeze": frozen["code_sha256"]}


POST_FREEZE = OUT / "post_freeze_changes.json"


def _sha_of(files: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for name, data in files:
        h.update(name.encode())
        h.update(data)
    return h.hexdigest()


def check_code_unchanged() -> str:
    """The evaluated forecasting code must be unchanged since freezing. A documented edit that does not change
    behaviour is accepted only if undoing it reproduces the frozen SHA-256 exactly (checked here, every time)."""
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))["code_sha256"]
    now = code_sha256()
    if now == frozen:
        return now
    allowed = json.loads(POST_FREEZE.read_text(encoding="utf-8"))["allowed"] if POST_FREEZE.exists() else []
    for entry in allowed:
        if entry["code_sha256"] != now:
            continue
        src = PROJECT_ROOT / "src" / "mandicast" / "forecast"
        files = [(f.name, f.read_bytes()) for f in sorted(src.glob("*.py"))]
        undone = [(n, d.replace(entry["after"].encode(), entry["before"].encode(), 1) if n == entry["file"] else d)
                  for n, d in files]
        if _sha_of(undone) == frozen:
            log.info("forecasting code differs from the frozen version only by the documented %s edit in %s",
                     entry["kind"], entry["file"])
            return now
    raise CodeChangedError("the forecasting code (src/mandicast/forecast) changed after freezing "
                           f"({frozen[:12]} -> {now[:12]}); the served model would differ from the evaluated one. "
                           "Re-evaluate before serving.")


def training_examples(primary: pl.DataFrame, states: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """Exactly the Phase 3 construction (forecast/run.py run_gbm), limited to origins before the cutoff, which are
    the only ones `gbm.train` can use (targets must fall before the cutoff)."""
    pairs = eligibility_by_origin(primary, mondays(gbm.TRAIN_START, cutoff - timedelta(days=1)))
    feats = gbm.origin_features(primary, pairs, states)
    float_cols = [c for c, t in feats.schema.items() if t == pl.Float64 and c != "last_price"]
    feats = feats.with_columns([pl.col(c).cast(pl.Float32) for c in float_cols])
    return gbm.expand_targets(feats, primary, holidays_for(cutoff), with_y=True).filter(~pl.col("y_outlier"))


def evaluation_summary(commodity: str) -> dict:
    """What the API reports as this model's measured accuracy: the single hold-out evaluation of the procedure
    (generated files only; no numbers typed by hand)."""
    final = json.loads(HOLDOUT_REPORT.read_text(encoding="utf-8"))
    c = final["commodities"][commodity]
    s = c["served"]
    keep = ("h", "n", "mae", "wape", "mase_vs_last_value", "dir_acc_moves_ge_2pct", "share_moves_ge_2pct",
            "coverage80", "coverage90", "rel_width80", "rel_width90")
    pick = lambda rows: [{k: r.get(k) for k in keep if k in r} for r in rows]  # noqa: E731
    return {
        "procedure": "global LightGBM per commodity, retrained each calendar quarter (methodology C6, C8)",
        "period": "hold-out: 52 Monday origins, 2025-12-01 to 2026-11-23, scored once after freezing",
        "evaluated_at": final["evaluated_at"], "frozen_at": final["frozen_at"],
        "code_changed_after_freeze": final["code_changed_after_freeze"],
        "interval_method": s["interval_method"], "interval_calibration_in_evaluation": s["calibration_folds"],
        "served": pick(s["headline_h7_h14"]), "last_value": pick(s["last_value_h7_h14"]),
        "by_h": pick(s["by_h"]), "by_quarter_h7": [{k: r.get(k) for k in ("fold", "n", "wape", "coverage80")}
                                                   for r in s["by_quarter_h7"]],
        "all_models_same_points": [{k: r.get(k) for k in ("model", "h", "n", "wape", "mase_vs_last_value",
                                                           "dir_acc_moves_ge_2pct")} for r in c["all_models_headline"]],
        "report": "docs/10_model_evaluation_report.md",
    }


DRIFT_FEATURES = ("vol_28d", "chg_7d", "last_vs_mean_28d", "last_vs_state_median", "age_days")
REGISTRY_NAME = "mandicast-lightgbm-{commodity}"
MLFLOW_EXPERIMENT = "mandicast-models"


def state_codes_sha256(states: pl.DataFrame) -> str:
    """Fingerprint of the state list behind the categorical state codes (C10a)."""
    names = sorted(states.select(pl.col("state").fill_null("?")).unique()["state"].to_list())
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def drift_reference(gate_examples: pl.DataFrame) -> dict:
    """Reference histograms of the drift features over the (series, origin) pairs of the early-stopping set (C10e):
    bin edges at the reference deciles (duplicates merged) and the reference share in each bin."""
    pairs = gate_examples.unique(["series_id", "origin"])
    out: dict = {"pairs": pairs.height}
    if not pairs.height:
        return out
    for f in DRIFT_FEATURES:
        vals = pairs[f].cast(pl.Float64).drop_nulls().to_numpy()
        if not len(vals):
            continue
        edges = sorted({float(x) for x in np.quantile(vals, np.linspace(0.1, 0.9, 9))})
        counts = np.bincount(np.searchsorted(edges, vals, side="right"), minlength=len(edges) + 1)
        out[f] = {"edges": edges, "shares": [float(c) / len(vals) for c in counts]}
    out["eligible_per_origin_mean"] = float(pairs.group_by("origin").len()["len"].mean())
    return out


@dataclass
class Trained:
    commodity: str
    cutoff: date
    path: Path
    sha256: str
    info: dict
    examples: pl.DataFrame | None = field(default=None, repr=False)
    gate_examples: pl.DataFrame | None = field(default=None, repr=False)


def train(engine: Engine, commodity: str, cutoff: date, models_dir: Path | None = None,
          primary: pl.DataFrame | None = None, states: pl.DataFrame | None = None) -> Trained:
    """Train the frozen method for `cutoff` (a quarter start) on the current database and save the artifact.
    The returned object keeps the training examples and the early-stopping (gate) set for validation and the gate."""
    from mandicast.serving import gate
    if cutoff != quarter_start(cutoff):
        raise ValueError(f"cutoff must be the first day of a calendar quarter, got {cutoff}")
    forecast_sha = check_code_unchanged()
    choice = frozen_choice(commodity)
    if {choice["served"]["days_1_7"], choice["served"]["days_8_14"]} != {"gbm"}:
        raise NotImplementedError(f"serving is implemented for the frozen LightGBM only; {commodity} froze "
                                  f"{choice['served']}")
    t0 = time.perf_counter()
    primary = load_serving_primary(engine, commodity) if primary is None else primary
    states = series_info(engine, commodity).select("series_id", "state") if states is None else states
    examples = training_examples(primary, states, cutoff)
    e = gate.gate_set(examples, cutoff)
    model, info = gbm.train(examples, cutoff)
    models_dir = models_dir or get_settings().models_dir
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"lightgbm_{commodity.lower()}_{cutoff.isoformat()}.txt"
    model.save_model(str(path), num_iteration=model.best_iteration)
    sha = file_sha256(path)
    info = {**info, "examples": examples.height, "series_in_training": int(examples["series_id"].n_unique()),
            "data_max_date": str(primary["date"].max()), "primary_rows": primary.height,
            "seconds": round(time.perf_counter() - t0, 1), "forecast_code_sha256": forecast_sha,
            "serving_code_sha256": serving_code_sha256(), "lightgbm": __import__("lightgbm").__version__,
            "trained_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "state_codes_sha256": state_codes_sha256(states), "gate_examples": e.height,
            "drift_reference": drift_reference(e)}
    path.with_suffix(".json").write_text(json.dumps({"sha256": sha, **info}, indent=2) + "\n", encoding="utf-8")
    log.info("trained %s cutoff %s: %s", commodity, cutoff, {k: v for k, v in info.items() if k != "drift_reference"})
    return Trained(commodity, cutoff, path, sha, info, examples, e)


def _mlflow():
    import mlflow
    mlflow.set_tracking_uri(MLFLOW_URI)
    if mlflow.get_experiment_by_name(MLFLOW_EXPERIMENT) is None:
        # absolute artifact location, so artifacts never depend on the working directory
        mlflow.create_experiment(MLFLOW_EXPERIMENT,
                                 artifact_location=(PROJECT_ROOT / "experiments" / "mlruns").as_uri())
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    return mlflow


def log_mlflow(t: Trained, version: str) -> tuple[str | None, str | None]:
    """Track the training run and register the model in the MLflow registry (a mirror: the database is the source
    of truth). Returns (run id, registry version); failures are logged and do not block serving."""
    if not get_settings().mlflow_enabled:
        return None, None
    try:
        import lightgbm as lgb
        mlflow = _mlflow()
        import mlflow.lightgbm as mlflow_lgb
        with mlflow.start_run(run_name=version) as run:
            mlflow.set_tags({"commodity": t.commodity, "version": version, "artifact_sha256": t.sha256,
                             "forecast_code_sha256": t.info["forecast_code_sha256"]})
            mlflow.log_params({**{f"lgb_{k}": v for k, v in gbm.PARAMS.items()}, "cutoff": str(t.cutoff),
                               "max_trees": gbm.MAX_TREES, "early_stop": gbm.EARLY_STOP})
            mlflow.log_metrics({k: float(t.info[k]) for k in ("best_iteration", "train_rows", "valid_rows",
                                                              "examples", "seconds")})
            info = mlflow_lgb.log_model(lgb.Booster(model_file=str(t.path)), name="model",
                                        registered_model_name=REGISTRY_NAME.format(commodity=t.commodity.lower()))
            reg = getattr(info, "registered_model_version", None)
            return run.info.run_id, None if reg is None else str(reg)
    except Exception as exc:  # noqa: BLE001 - tracking must not block serving; the failure is logged
        log.warning("MLflow logging failed for %s: %s: %s", version, type(exc).__name__, exc)
        return None, None


def mlflow_set_production(commodity: str, registry_version: str | None) -> None:
    """Point the registry alias `production` at the given version (or remove it)."""
    if not get_settings().mlflow_enabled:
        return
    try:
        _mlflow()
        from mlflow import MlflowClient
        client, name = MlflowClient(), REGISTRY_NAME.format(commodity=commodity.lower())
        if registry_version is None:
            try:
                client.delete_registered_model_alias(name, "production")
            except Exception:  # noqa: BLE001, S110 - the alias may not exist
                pass
        else:
            client.set_registered_model_alias(name, "production", registry_version)
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow alias update failed for %s: %s", commodity, type(exc).__name__)


def sync_mlflow_alias(session: Session, commodity: str) -> str | None:
    """Point the MLflow alias `production` at the database's production model of the crop (or remove it)."""
    reg = session.execute(text(
        "SELECT mv.mlflow_registry_version FROM model_versions mv JOIN commodities c ON c.id = mv.commodity_id "
        "WHERE c.name = :c AND mv.status = 'production'"), {"c": commodity}).scalar_one_or_none()
    mlflow_set_production(commodity, reg)
    return reg


def record_event(session: Session, commodity_id: int, model_version_id: int | None, event: str, reason: str | None,
                 details: dict | None = None) -> None:
    session.execute(text(
        "INSERT INTO model_events (commodity_id, model_version_id, event, reason, details) "
        "VALUES (:c, :m, :e, :r, CAST(:d AS jsonb))"),
        {"c": commodity_id, "m": model_version_id, "e": event, "r": reason,
         "d": json.dumps(details or {}, default=str)})


def commodity_id_of(session: Session, commodity: str) -> int:
    return session.execute(text("SELECT id FROM commodities WHERE name = :c"), {"c": commodity}).scalar_one()


def version_name(t: Trained) -> str:
    return f"lightgbm-{t.commodity.lower()}-{quarter_label(t.cutoff)}-{t.sha256[:8]}"


def register_candidate(session: Session, t: Trained) -> dict:
    """Insert the model version as `candidate`. An identical artifact already registered is returned as it is
    (its training record gains any fields added since, such as the drift reference)."""
    commodity_id = commodity_id_of(session, t.commodity)
    version = version_name(t)
    existing = session.execute(text(
        "SELECT id, status, mlflow_registry_version FROM model_versions WHERE version = :v"),
        {"v": version}).one_or_none()
    if existing is not None:
        extra = {k: t.info[k] for k in ("state_codes_sha256", "gate_examples", "drift_reference") if k in t.info}
        session.execute(text("UPDATE model_versions SET training = training || CAST(:x AS jsonb) WHERE id = :id"),
                        {"x": json.dumps(extra), "id": existing.id})
        if existing.mlflow_registry_version is None:
            _, reg = log_mlflow(t, version)
            session.execute(text("UPDATE model_versions SET mlflow_registry_version = :r WHERE id = :id"),
                            {"r": reg, "id": existing.id})
        return {"version": version, "id": existing.id, "status": existing.status, "new": False}
    run_id, reg = log_mlflow(t, version)
    path = t.path.relative_to(PROJECT_ROOT).as_posix() if t.path.is_relative_to(PROJECT_ROOT) else str(t.path)
    mv_id = session.execute(text(
        "INSERT INTO model_versions (version, kind, commodity_id, horizon_days, training_cutoff, valid_from, "
        "valid_to, artifact_path, artifact_sha256, code_sha256, params, training, evaluation, interval_method, "
        "mlflow_registry_version) VALUES (:v, :k, :c, :h, :cut, :vf, :vt, :p, :sha, :code, CAST(:params AS jsonb), "
        "CAST(:training AS jsonb), CAST(:ev AS jsonb), :im, :reg) RETURNING id"),
        {"v": version, "k": KIND, "c": commodity_id, "h": HORIZON_DAYS, "cut": t.cutoff,
         "vf": quarter_start(t.cutoff), "vt": quarter_end(t.cutoff), "p": path, "sha": t.sha256,
         "code": t.info["forecast_code_sha256"],
         "params": json.dumps({**gbm.PARAMS, "max_trees": gbm.MAX_TREES, "early_stop": gbm.EARLY_STOP,
                               "features": gbm.FEATURES}),
         "training": json.dumps({**t.info, "mlflow_run_id": run_id}),
         "ev": json.dumps(evaluation_summary(t.commodity)),
         "im": frozen_choice(t.commodity)["intervals"]["method"], "reg": reg}).scalar_one()
    record_event(session, commodity_id, mv_id, "registered", None, {"cutoff": t.cutoff, "sha256": t.sha256})
    return {"version": version, "id": mv_id, "status": "candidate", "new": True}


def promote(session: Session, mv_id: int, reason: str, gate_result: dict | None = None) -> dict:
    """Make `mv_id` the production model of its commodity; the previous one is archived and remembered for
    rollback (`replaced_version_id`)."""
    row = session.execute(text(
        "SELECT mv.commodity_id, c.name, mv.mlflow_registry_version FROM model_versions mv "
        "JOIN commodities c ON c.id = mv.commodity_id WHERE mv.id = :id"), {"id": mv_id}).one()
    old = session.execute(text(
        "SELECT id FROM model_versions WHERE commodity_id = :c AND status = 'production' AND id <> :id"),
        {"c": row.commodity_id, "id": mv_id}).scalar_one_or_none()
    if old is not None:
        session.execute(text("UPDATE model_versions SET status = 'archived', status_reason = :r WHERE id = :o"),
                        {"o": old, "r": f"replaced by model {mv_id}"})
        record_event(session, row.commodity_id, old, "archived", f"replaced by model {mv_id}")
    session.execute(text(
        "UPDATE model_versions SET status = 'production', status_reason = :r, promoted_at = now(), "
        "replaced_version_id = COALESCE(CAST(:o AS integer), replaced_version_id), "
        "gate = COALESCE(CAST(:g AS jsonb), gate) WHERE id = :id"),
        {"o": old, "id": mv_id, "r": reason, "g": None if gate_result is None else json.dumps(gate_result)})
    record_event(session, row.commodity_id, mv_id, "promoted", reason,
                 {"replaced": old, **({"gate": gate_result["metrics"]} if gate_result else {})})
    mlflow_set_production(row.name, row.mlflow_registry_version)
    return {"promoted": mv_id, "replaced": old}


def reject(session: Session, mv_id: int, reason: str, gate_result: dict | None = None) -> None:
    row = session.execute(text("SELECT commodity_id, status FROM model_versions WHERE id = :id"), {"id": mv_id}).one()
    if row.status == "production":
        raise RuntimeError("refusing to reject the production model directly; use rollback")
    session.execute(text(
        "UPDATE model_versions SET status = 'rejected', status_reason = :r, gate = COALESCE(CAST(:g AS jsonb), gate) "
        "WHERE id = :id"), {"id": mv_id, "r": reason, "g": None if gate_result is None else json.dumps(gate_result)})
    record_event(session, row.commodity_id, mv_id, "rejected", reason,
                 {"gate": gate_result["metrics"]} if gate_result else {})


def rollback(session: Session, commodity: str, reason: str) -> dict:
    """C10h: archive the production model and restore the version it replaced. Without one, the crop has no
    production model and is served by last value until a valid model is promoted."""
    commodity_id = commodity_id_of(session, commodity)
    cur = session.execute(text(
        "SELECT id, version, replaced_version_id FROM model_versions "
        "WHERE commodity_id = :c AND status = 'production'"),
        {"c": commodity_id}).one_or_none()
    if cur is None:
        raise RuntimeError(f"no production model for {commodity} to roll back")
    session.execute(text("UPDATE model_versions SET status = 'archived', status_reason = :r WHERE id = :id"),
                    {"id": cur.id, "r": f"rolled back: {reason}"})
    record_event(session, commodity_id, cur.id, "rolled_back", reason)
    restored = None
    if cur.replaced_version_id is not None:
        prev = session.execute(text("SELECT id, version, mlflow_registry_version FROM model_versions WHERE id = :id"),
                               {"id": cur.replaced_version_id}).one()
        session.execute(text("UPDATE model_versions SET status = 'production', status_reason = :r, "
                             "promoted_at = now() WHERE id = :id"),
                        {"id": prev.id, "r": f"restored by rollback of {cur.version}: {reason}"})
        record_event(session, commodity_id, prev.id, "promoted", f"rollback of {cur.version}: {reason}")
        mlflow_set_production(commodity, prev.mlflow_registry_version)
        restored = prev.version
    else:
        record_event(session, commodity_id, None, "fallback",
                     f"rollback of {cur.version} left no model; last value is served")
        mlflow_set_production(commodity, None)
    return {"rolled_back": cur.version, "restored": restored}


def register(session: Session, t: Trained, promote_now: bool = True) -> dict:
    """Phase 4 behaviour (register and promote without the gate), kept for manual use; the CLI uses
    `serving.lifecycle.retrain`, which always applies validation and the gate."""
    out = register_candidate(session, t)
    if promote_now and out["status"] != "production":
        promote(session, out["id"], "promoted without gate (manual)")
        out["status"] = "production"
    return out


def load_model(path: Path, expected_sha256: str):
    """Load a LightGBM artifact after checking its SHA-256 against the registry."""
    import lightgbm as lgb
    full = path if path.is_absolute() else PROJECT_ROOT / path
    if not full.exists():
        raise ArtifactIntegrityError(f"model artifact missing: {path}")
    actual = file_sha256(full)
    if actual != expected_sha256:
        raise ArtifactIntegrityError(f"model artifact {path} has SHA-256 {actual[:12]}, registry says "
                                     f"{expected_sha256[:12]}")
    return lgb.Booster(model_file=str(full))
