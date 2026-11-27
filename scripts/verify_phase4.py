"""Phase 4 verification on the real database (read-only).

    python scripts/verify_phase4.py

1. Each production model's artifact matches its registered SHA-256.
2. Each production model reproduces the hold-out evaluation's fit for the same quarter (training rows, validation
   rows, best iteration from experiments/forecast/holdout/gbm/<crop>.json).
3. Forecasts published at an origin inside the hold-out period match the hold-out predictions for that origin
   (same series; relative differences of the point forecasts).
Writes reports/phase4/verify_phase4.json and prints a summary.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandicast.db.session import get_engine  # noqa: E402
from mandicast.forecast.run import OUT  # noqa: E402
from mandicast.serving.registry import file_sha256, quarter_label  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    eng = get_engine()
    out = {"checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "models": [], "forecasts": []}
    ok = True
    with eng.connect() as c:
        models = c.execute(text(
            "SELECT c.name, mv.id, mv.version, mv.artifact_path, mv.artifact_sha256, mv.training_cutoff, mv.training "
            "FROM model_versions mv JOIN commodities c ON c.id = mv.commodity_id WHERE mv.status = 'production' "
            "ORDER BY c.name")).all()
        for m in models:
            sha_ok = file_sha256(ROOT / m.artifact_path) == m.artifact_sha256
            fits = json.loads((OUT / "holdout" / "gbm" / f"{m.name.lower()}.json").read_text(encoding="utf-8"))["fits"]
            label = quarter_label(m.training_cutoff).upper()
            ref = next((f for f in fits if f["fold"] == label), None)
            same = ref is not None and all(ref[k] == m.training[k] for k in ("train_rows", "valid_rows",
                                                                              "best_iteration"))
            out["models"].append({"commodity": m.name, "version": m.version, "artifact_sha256_ok": sha_ok,
                                  "holdout_fold": label, "reproduces_holdout_fit": same,
                                  "serving": {k: m.training[k] for k in ("train_rows", "valid_rows", "best_iteration")},
                                  "holdout": None if ref is None else {k: ref[k] for k in ("train_rows", "valid_rows",
                                                                                           "best_iteration")}})
            ok &= sha_ok and same
            runs = c.execute(text("SELECT id, origin FROM forecast_runs WHERE model_version_id = :m ORDER BY origin"),
                             {"m": m.id}).all()
            ho_all = pl.read_parquet(OUT / "holdout" / "gbm" / f"{m.name.lower()}.parquet")
            for run in runs:
                ho = ho_all.filter(pl.col("origin") == run.origin).select(
                    pl.col("series_id").cast(pl.Int32), pl.col("h").cast(pl.Int16), "yhat")
                if ho.is_empty():
                    out["forecasts"].append({"commodity": m.name, "origin": str(run.origin),
                                             "note": "origin outside the hold-out period; nothing to compare"})
                    continue
                pub = pl.DataFrame([tuple(r) for r in c.execute(text(
                    "SELECT series_id, horizon_days, point::float FROM forecasts WHERE run_id = :r"),
                    {"r": run.id}).all()], schema={"series_id": pl.Int32, "h": pl.Int16, "point": pl.Float64},
                    orient="row")
                j = pub.join(ho, on=["series_id", "h"], how="inner")
                rel = ((j["point"] - j["yhat"]).abs() / j["yhat"])
                rec = {"commodity": m.name, "origin": str(run.origin),
                       "published_series": pub["series_id"].n_unique(), "holdout_series": ho["series_id"].n_unique(),
                       "same_series": set(pub["series_id"].unique()) == set(ho["series_id"].unique()),
                       "points_compared": j.height, "rel_diff_median": float(rel.median()),
                       "rel_diff_p99": float(rel.quantile(0.99)), "rel_diff_max": float(rel.max()),
                       "share_within_0_01pct": float((rel < 1e-4).mean())}
                out["forecasts"].append(rec)
                ok &= rec["same_series"]
    out["all_ok"] = bool(ok)
    d = ROOT / "reports" / "phase4"
    d.mkdir(parents=True, exist_ok=True)
    (d / "verify_phase4.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for m in out["models"]:
        print(f"{m['commodity']:9s} {m['version']:36s} sha ok={m['artifact_sha256_ok']} "
              f"reproduces {m['holdout_fold']} fit={m['reproduces_holdout_fit']} {m['serving']}")
    for f in out["forecasts"]:
        if "note" in f:
            print(f"{f['commodity']:9s} {f['origin']} {f['note']}")
        else:
            print(f"{f['commodity']:9s} {f['origin']} series {f['published_series']}/{f['holdout_series']} "
                  f"same={f['same_series']} within 0.01%: {f['share_within_0_01pct']:.4f} "
                  f"max rel diff {f['rel_diff_max']:.2e}")
    print("all ok:", out["all_ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
