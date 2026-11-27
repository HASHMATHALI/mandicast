"""Model lifecycle: retrain → validate (C10a) → register → gate (C10b) → promote or reject; rollback (C10h); and the
scheduled check that a valid production model exists for the current quarter (C10g).
"""

from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import Engine, text

from mandicast.clean.build import latest_complete_date
from mandicast.db.session import session_scope
from mandicast.forecast.data import series_info
from mandicast.jobs import SERVING_LOCK_ID, run_job
from mandicast.serving import gate, publish, registry

log = logging.getLogger(__name__)


def champion_for(session, commodity: str, candidate_id: int | None) -> dict | None:
    """The model the candidate must not be worse than: the current production model, or, when the candidate is
    itself the production model (re-evaluation), the version it replaced."""
    row = session.execute(text(
        "SELECT mv.id, mv.version, mv.artifact_path, mv.artifact_sha256, mv.training, mv.replaced_version_id, "
        "mv.valid_to FROM model_versions mv JOIN commodities c ON c.id = mv.commodity_id "
        "WHERE c.name = :c AND mv.status = 'production'"), {"c": commodity}).one_or_none()
    if row is not None and row.id == candidate_id:
        if row.replaced_version_id is None:
            return None
        row = session.execute(text(
            "SELECT id, version, artifact_path, artifact_sha256, training, replaced_version_id, valid_to "
            "FROM model_versions WHERE id = :id"), {"id": row.replaced_version_id}).one()
    return None if row is None else dict(row._mapping)


def run_gate(t: registry.Trained, champion: dict | None) -> dict:
    """Score candidate, champion and last value on the candidate's early-stopping set and apply C10b."""
    from pathlib import Path
    e = t.gate_examples
    cand_model = registry.load_model(t.path, t.sha256)
    cand = gate.predict(cand_model, e)
    champ_yhat, comparable, note = None, True, None
    if champion is not None:
        champ_model = registry.load_model(Path(champion["artifact_path"]), champion["artifact_sha256"])
        champ_yhat = gate.predict(champ_model, e)
        ref = champion["training"].get("state_codes_sha256")
        if ref is None:
            note = "production model's state list was not recorded (trained before Phase 5); compared as is"
        elif ref != t.info["state_codes_sha256"]:
            comparable, note = False, "state list differs from the production model's"
    can_serve = champion is not None and champion["valid_to"] >= registry.quarter_start(t.cutoff)
    out = gate.evaluate(e, cand, champ_yhat, comparable, champion_can_serve=can_serve)
    out["champion"] = None if champion is None else champion["version"]
    if note:
        out["champion_note"] = note
    return out


def retrain(engine: Engine, commodity: str, cutoff: date, today: date | None = None, promote_if_passed: bool = True,
            trained: registry.Trained | None = None) -> dict:
    """Train (unless `trained` is given), validate, register, gate and promote or reject. Returns a summary."""
    today = today or date.today()
    if trained is None:
        primary = registry.load_serving_primary(engine, commodity)
        states = series_info(engine, commodity).select("series_id", "state")
        trained = registry.train(engine, commodity, cutoff, primary=primary, states=states)
    t = trained
    with session_scope(engine) as s:
        champion = champion_for(s, commodity, None)
        validation = gate.validate_data(t.examples, t.gate_examples, cutoff,
                                        latest_complete_date(s, commodity, today),
                                        champion["training"] if champion else None)
        if not validation["passed"]:
            cid = registry.commodity_id_of(s, commodity)
            failed = [c["name"] for c in validation["checks"] if not c["ok"]]
            registry.record_event(s, cid, None, "rejected", f"validation before training failed: {failed}",
                                  {"cutoff": cutoff, "validation": validation})
            return {"commodity": commodity, "status": "validation_failed", "validation": validation}
    with session_scope(engine) as s:
        reg = registry.register_candidate(s, t)
    with session_scope(engine) as s:
        champion = champion_for(s, commodity, reg["id"])
        result = run_gate(t, champion)
        result["validation"] = validation
        if result["passed"]:
            if promote_if_passed and reg["status"] != "production":
                registry.promote(s, reg["id"], "passed the promotion gate (C10b)", result)
                status = "promoted"
            else:
                s.execute(text("UPDATE model_versions SET gate = CAST(:g AS jsonb) WHERE id = :id"),
                          {"g": json.dumps(result, default=str), "id": reg["id"]})
                status = "passed" if reg["status"] != "production" else "production_confirmed"
                if status == "production_confirmed":
                    registry.record_event(s, registry.commodity_id_of(s, commodity), reg["id"], "promoted",
                                          "re-evaluated under the promotion gate (C10b): passed; remains production",
                                          {"gate": result["metrics"]})
                    registry.sync_mlflow_alias(s, commodity)
        else:
            if reg["status"] == "production":
                # A production model that fails re-evaluation is rolled back (C10h), never silently kept.
                reason = f"failed the promotion gate on re-evaluation: {result['failed']}"
                registry.rollback(s, commodity, reason)
                registry.reject(s, reg["id"], reason, result)
                status = "rolled_back_and_rejected"
            else:
                registry.reject(s, reg["id"], f"failed the promotion gate: {result['failed']}", result)
                status = "rejected"
    log.info("retrain %s %s: %s (%s)", commodity, cutoff, status, result["failed"])
    return {"commodity": commodity, "version": reg["version"], "status": status, "gate": result,
            "best_iteration": t.info["best_iteration"]}


def ensure_models(engine: Engine, commodities: list[str], today: date | None = None) -> int:
    """C10g: for each crop, if no production model is valid for the latest complete origin and no candidate for
    that quarter has been tried, retrain. Returns the job id."""
    today = today or date.today()
    with run_job(engine, "ensure_models", {"commodities": commodities, "today": today},
                 lock_id=SERVING_LOCK_ID) as job:
        for c in commodities:
            with session_scope(engine) as s:
                origin, _ = publish.choose_origin(s, c, today)
                cutoff = registry.quarter_start(origin)
                try:
                    publish.production_model(s, c, origin)
                    job.report[c] = {"status": "valid_model", "origin": origin}
                    continue
                except publish.ModelUnavailableError:
                    pass
                tried = s.execute(text(
                    "SELECT mv.version, mv.status FROM model_versions mv JOIN commodities k ON k.id = mv.commodity_id "
                    "WHERE k.name = :c AND mv.training_cutoff = :cut ORDER BY mv.id DESC LIMIT 1"),
                    {"c": c, "cut": cutoff}).one_or_none()
            if tried is not None and tried.status in ("rejected", "archived"):
                job.report[c] = {"status": "not_retried", "origin": origin,
                                 "reason": f"{tried.version} for this quarter is {tried.status}; last value is served "
                                           "until `mandicast retrain` is run"}
                continue
            job.report[c] = retrain(engine, c, cutoff, today)
            job.report[c].pop("gate", None)
    return job.id
