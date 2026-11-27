"""The single hold-out evaluation (methodology §3, C7, C8).

    python -m mandicast.forecast.final

Requires experiments/forecast/frozen_choices.json. Scores every model that has hold-out predictions on the same
points, then the served forecast (frozen model per day range) with intervals calibrated on the last 4
development quarters of that served forecast. Records whether the forecasting code changed after freezing.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

import mlflow
import polars as pl

from mandicast.db.session import get_engine
from mandicast.forecast import evaluate, intervals
from mandicast.forecast.data import load_primary
from mandicast.forecast.freeze import MLFLOW_URI, code_sha256, served_path
from mandicast.forecast.metrics import summarise
from mandicast.forecast.run import FROZEN, OUT

log = logging.getLogger(__name__)


def served_holdout(c: str, choice: dict) -> dict:
    eng = get_engine()
    p = load_primary(eng, c)
    m7, m14 = choice["served"]["days_1_7"], choice["served"]["days_8_14"]
    served_models = sorted({m7, m14, "last_value"})
    ho_long, ho_info = evaluate.scored_frame(c, "holdout", served_models)
    dev_long, _ = evaluate.scored_frame(c, "dev", served_models)
    ho = served_path(ho_long, m7, m14)
    dev = served_path(dev_long, m7, m14)
    last4 = sorted(dev["fold"].unique().to_list())[-intervals.CAL_FOLDS:]
    conditional = choice["intervals"]["method"] == "conditional"
    cal = dev.filter(pl.col("fold").is_in(last4) & ~pl.col("y_outlier")).with_columns(
        (pl.col("y").log() - pl.col("yhat").log()).alias("r"))
    if conditional:
        cal = cal.join(intervals.origin_volatility(p, cal.select("series_id", "origin").unique()),
                       on=["series_id", "origin"], how="left")
        ho = ho.join(intervals.origin_volatility(p, ho.select("series_id", "origin").unique()),
                     on=["series_id", "origin"], how="left")
    ho_iv = intervals.apply(ho, cal, conditional)
    lv = ho_long.filter(pl.col("model") == "last_value")
    clean = ho_iv.filter(~pl.col("y_outlier"))
    bucket = pl.when(pl.col("h") <= 7).then(pl.lit("1-7")).otherwise(pl.lit("8-14")).alias("days")
    return {
        "served_models": {"days_1_7": m7, "days_8_14": m14}, "interval_method": choice["intervals"]["method"],
        "calibration_folds": last4, "points": ho_info,
        "headline_h7_h14": summarise(clean.filter(pl.col("h").is_in([7, 14])), ["h"]).to_dicts(),
        "headline_including_outlier_targets": summarise(ho_iv.filter(pl.col("h").is_in([7, 14])), ["h"]).to_dicts(),
        "last_value_h7_h14": summarise(lv.filter(~pl.col("y_outlier") & pl.col("h").is_in([7, 14])), ["h"]).to_dicts(),
        "by_day_range": summarise(clean.with_columns(bucket), ["days"]).to_dicts(),
        "by_h": summarise(clean, ["h"]).to_dicts(),
        "by_quarter_h7": summarise(clean.filter(pl.col("h") == 7), ["fold"]).to_dicts(),
        "by_state_h7": summarise(clean.filter(pl.col("h") == 7), ["state"]).to_dicts(),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    sys.stdout.reconfigure(encoding="utf-8")
    if not FROZEN.exists():
        raise SystemExit(f"no frozen choices at {FROZEN}; run `python -m mandicast.forecast.freeze` first")
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    now_sha = code_sha256()
    out = {"evaluated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "frozen_at": frozen["frozen_at"],
           "code_sha256_at_freeze": frozen["code_sha256"], "code_sha256_now": now_sha,
           "code_changed_after_freeze": now_sha != frozen["code_sha256"], "commodities": {}}
    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("mandicast-forecast")
    for c, choice in frozen["commodities"].items():
        models = evaluate.available_models("holdout", c)
        all_models = evaluate.report(c, "holdout", models)
        served = served_holdout(c, choice)
        out["commodities"][c] = {"all_models_headline": all_models["headline"], "served": served}
        with mlflow.start_run(run_name=f"holdout-{c}-served"):
            mlflow.set_tags({"period": "holdout", "commodity": c, "model": "served", "code_sha256": now_sha,
                             **{f"served_{k}": v for k, v in served["served_models"].items()}})
            for row in served["headline_h7_h14"]:
                mlflow.log_metrics({f"h{row['h']}_{k}": float(v) for k, v in row.items()
                                    if isinstance(v, float) and v == v})
        print(c, served["served_models"], [(r["h"], round(r["wape"], 4), round(r.get("coverage80", float("nan")), 3))
                                           for r in served["headline_h7_h14"]])
    path = OUT / "holdout" / "_reports" / "final.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print("written:", path, "| code changed after freeze:", out["code_changed_after_freeze"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
