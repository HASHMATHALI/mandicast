"""Phase 5 verification on the real database and MLflow store (read-only).

    python -I scripts/verify_phase5.py

1. Each production model: artifact SHA-256 matches; a gate result is stored and passed; it is registered in the
   MLflow model registry and the alias `production` points at that registry version.
2. Each crop without a production model: a recorded reason (rejection, rollback or fallback event).
3. Every model version's status agrees with its latest lifecycle event.
Writes reports/phase5/verify_phase5.json.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandicast.db.session import get_engine  # noqa: E402
from mandicast.forecast.freeze import MLFLOW_URI  # noqa: E402
from mandicast.serving.registry import REGISTRY_NAME, file_sha256  # noqa: E402

EVENT_STATUS = {"promoted": "production", "rejected": "rejected", "archived": "archived", "rolled_back": "archived",
                "registered": "candidate"}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import mlflow
    from mlflow import MlflowClient
    mlflow.set_tracking_uri(MLFLOW_URI)
    client = MlflowClient()
    out = {"checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "commodities": {}}
    ok = True
    with get_engine().connect() as c:
        for cid, name in c.execute(text("SELECT id, name FROM commodities ORDER BY name")).all():
            rec: dict = {}
            prod = c.execute(text(
                "SELECT version, artifact_path, artifact_sha256, gate, mlflow_registry_version FROM model_versions "
                "WHERE commodity_id = :c AND status = 'production'"), {"c": cid}).one_or_none()
            if prod:
                alias = None
                try:
                    alias = client.get_model_version_by_alias(REGISTRY_NAME.format(commodity=name.lower()),
                                                              "production").version
                except Exception as exc:  # noqa: BLE001
                    alias = f"error: {type(exc).__name__}"
                artifact_ok = file_sha256(ROOT / prod.artifact_path) == prod.artifact_sha256
                rec["production"] = {
                    "version": prod.version, "artifact_ok": artifact_ok,
                    "gate_passed": bool(prod.gate and prod.gate.get("passed")),
                    "mlflow_registry_version": prod.mlflow_registry_version, "mlflow_alias_production": alias,
                    "mlflow_alias_matches": alias is not None and str(alias) == str(prod.mlflow_registry_version)}
                ok &= all(rec["production"][k] for k in ("artifact_ok", "gate_passed", "mlflow_alias_matches"))
            else:
                last = c.execute(text(
                    "SELECT event, reason FROM model_events WHERE commodity_id = :c ORDER BY id DESC LIMIT 1"),
                    {"c": cid}).one_or_none()
                rec["production"] = None
                rec["no_production_reason"] = None if last is None else f"{last.event}: {last.reason}"
                ok &= last is not None
            mismatches = []
            for v in c.execute(text("SELECT id, version, status FROM model_versions WHERE commodity_id = :c"),
                               {"c": cid}).all():
                ev = c.execute(text("SELECT event FROM model_events WHERE model_version_id = :m ORDER BY id DESC "
                                    "LIMIT 1"), {"m": v.id}).scalar_one_or_none()
                expected = EVENT_STATUS.get(ev)
                if ev is not None and expected != v.status:
                    mismatches.append({"version": v.version, "status": v.status, "latest_event": ev})
            rec["status_event_mismatches"] = mismatches
            ok &= not mismatches
            out["commodities"][name] = rec
            print(name, json.dumps(rec, default=str))
    out["all_ok"] = bool(ok)
    d = ROOT / "reports" / "phase5"
    d.mkdir(parents=True, exist_ok=True)
    (d / "verify_phase5.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print("all ok:", out["all_ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
