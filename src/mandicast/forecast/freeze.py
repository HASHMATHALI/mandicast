"""Freeze model and interval choices from development data only (methodology §4, C7, C8).

    python -m mandicast.forecast.freeze

For each commodity: the model chosen at h = 7 serves days 1–7 and the model chosen at h = 14 serves days 8–14.
Interval method (unconditional or volatility-tercile conditional) decided by the C7 rule on development folds.
Writes experiments/forecast/frozen_choices.json with a SHA-256 of the forecasting code; hold-out runs require it.
Also logs every development report to MLflow (experiments/mlflow.db).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import UTC, datetime

import mlflow
import polars as pl

from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.db.session import get_engine
from mandicast.forecast import evaluate, intervals
from mandicast.forecast.data import load_primary
from mandicast.forecast.metrics import summarise
from mandicast.forecast.run import FROZEN, OUT

log = logging.getLogger(__name__)
TERCILE_TOLERANCE = 0.05
MLFLOW_URI = f"sqlite:///{(PROJECT_ROOT / 'experiments' / 'mlflow.db').as_posix()}"


def code_sha256() -> str:
    h = hashlib.sha256()
    for f in sorted((PROJECT_ROOT / "src" / "mandicast" / "forecast").glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def served_path(long: pl.DataFrame, model_h7: str, model_h14: str) -> pl.DataFrame:
    return long.filter(((pl.col("h") <= 7) & (pl.col("model") == model_h7))
                       | ((pl.col("h") >= 8) & (pl.col("model") == model_h14)))


def decide_intervals(served: pl.DataFrame, primary: pl.DataFrame) -> dict:
    vol = intervals.origin_volatility(primary, served.select("series_id", "origin").unique())
    s = served.join(vol, on=["series_id", "origin"], how="left")
    uncond = intervals.rolling_dev_intervals(s, conditional=False)
    cuts = s["vol"].drop_nulls().quantile(1 / 3), s["vol"].drop_nulls().quantile(2 / 3)
    tert = (pl.when(pl.col("vol") <= cuts[0]).then(pl.lit("low")).when(pl.col("vol") <= cuts[1])
            .then(pl.lit("mid")).otherwise(pl.lit("high")).alias("vol_tercile"))
    by_t = summarise(uncond.filter(~pl.col("y_outlier")).with_columns(tert), ["vol_tercile"])
    worst = float((by_t["coverage80"] - 0.80).abs().max())
    method = "conditional" if worst > TERCILE_TOLERANCE else "unconditional"
    chosen = uncond if method == "unconditional" else intervals.rolling_dev_intervals(s, conditional=True)
    cov = summarise(chosen.filter(~pl.col("y_outlier")), ["h"]).select(
        "h", "coverage80", "coverage90", "rel_width80", "rel_width90").to_dicts()
    return {"method": method, "unconditional_coverage80_by_vol_tercile": by_t.select("vol_tercile", "n", "coverage80")
            .to_dicts(), "worst_tercile_deviation": worst, "dev_coverage_by_h": cov}


def log_report_mlflow(rep: dict) -> None:
    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("mandicast-forecast")
    for row in rep["headline"]:
        with mlflow.start_run(run_name=f"{rep['period']}-{rep['commodity']}-{row['model']}-h{row['h']}"):
            mlflow.set_tags({"period": rep["period"], "commodity": rep["commodity"], "model": row["model"],
                             "code_sha256": code_sha256()})
            mlflow.log_params({"h": row["h"], "points": row["n"]})
            mlflow.log_metrics({k: float(v) for k, v in row.items() if isinstance(v, float) and v == v})


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    sys.stdout.reconfigure(encoding="utf-8")
    eng = get_engine()
    frozen = {"frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_sha256": code_sha256(),
              "rules": "docs/06_forecasting_methodology.md §4, §6, §9 (C5–C8)", "commodities": {}}
    for c in get_settings().commodities:
        models = evaluate.available_models("dev", c)
        rep = evaluate.report(c, "dev", models)
        log_report_mlflow(rep)
        sel = {r["h"]: r["selected"] for r in rep["selection"]}
        long, _ = evaluate.scored_frame(c, "dev", models)
        served = served_path(long, sel[7], sel[14])
        iv = decide_intervals(served, load_primary(eng, c))
        frozen["commodities"][c] = {"models_compared": models, "points": rep["points"],
                                    "served": {"days_1_7": sel[7], "days_8_14": sel[14]},
                                    "selection_table": rep["selection"], "intervals": iv}
        print(c, "->", frozen["commodities"][c]["served"], "| intervals:", iv["method"])
    FROZEN.write_text(json.dumps(frozen, indent=2, default=str) + "\n", encoding="utf-8")
    print("frozen:", FROZEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "served_path", "decide_intervals", "code_sha256", "OUT"]
