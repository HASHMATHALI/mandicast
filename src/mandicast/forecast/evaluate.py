"""Score stored predictions and choose a model per commodity and horizon (methodology §4–§5, C5).

- Every model is scored on the same points: the targets for which all compared models have a prediction
  (the share of targets dropped because a model lacked a prediction is reported).
- Headline metrics exclude targets flagged `local_outlier` (C5); the same metrics including them are reported.
- Selection, per commodity and headline horizon (7, 14), on development folds (calendar quarters): lowest mean
  fold WAPE, provided the model beats last value in at least 60% of folds; otherwise last value is served.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import polars as pl

from mandicast.config import get_settings
from mandicast.db.session import get_engine
from mandicast.forecast.data import CACHE_DIR, load_primary, series_info
from mandicast.forecast.metrics import summarise
from mandicast.forecast.run import OUT, origins_for
from mandicast.forecast.walkforward import HEADLINE_H, eval_frame

log = logging.getLogger(__name__)
MIN_FOLD_WIN_SHARE = 0.6


SAMPLED_MODELS = ("arima",)  # evaluated on a fixed sample of series only (methodology D3)


def available_models(period: str, commodity: str, include_sampled: bool = False) -> list[str]:
    root = OUT / period
    if not root.exists():
        return []
    models = sorted(d.name for d in root.iterdir() if (d / f"{commodity.lower()}.parquet").exists())
    return models if include_sampled else [m for m in models if m not in SAMPLED_MODELS]


def sample_comparison(commodity: str, period: str) -> dict | None:
    """All models on the points of the sampled series (where every model, including ARIMA, has a prediction)."""
    models = available_models(period, commodity, include_sampled=True)
    if not any(m in models for m in SAMPLED_MODELS):
        return None
    long, info = scored_frame(commodity, period, models)
    d = long.filter(~pl.col("y_outlier") & pl.col("h").is_in(list(HEADLINE_H)))
    return {"points": info, "headline": summarise(d, ["model", "h"]).to_dicts(),
            "mean_fold_wape": summarise(d, ["model", "h", "fold"]).group_by(["model", "h"])
            .agg(pl.col("wape").mean().alias("mean_fold_wape")).sort(["h", "mean_fold_wape"]).to_dicts()}


def scored_frame(commodity: str, period: str, models: list[str]) -> tuple[pl.DataFrame, dict]:
    """Long frame (model, series_id, origin, h, fold, y, y_outlier, yhat, last_price) on common points."""
    eng = get_engine()
    p = load_primary(eng, commodity)
    e = pl.read_parquet(CACHE_DIR / f"eligibility_{commodity.lower()}.parquet")
    f = eval_frame(p, e, origins_for(period))
    preds = {m: pl.read_parquet(OUT / period / m / f"{commodity.lower()}.parquet").select(
        "series_id", "origin", "h", pl.col("yhat").alias(f"yhat__{m}")) for m in models}
    j = f
    for m in models:
        j = j.join(preds[m], on=["series_id", "origin", "h"], how="left")
    complete = pl.all_horizontal([pl.col(f"yhat__{m}").is_not_null() for m in models])
    info = {"targets": f.height, "common_points": int(j.select(complete.sum()).item()),
            "missing_by_model": {m: int(j[f"yhat__{m}"].is_null().sum()) for m in models},
            "outlier_targets": int(f["y_outlier"].sum())}
    j = j.filter(complete)
    states = series_info(eng, commodity).select("series_id", "state")
    j = j.join(states, on="series_id", how="left")
    long = pl.concat([j.select("series_id", "origin", "h", "fold", "state", "y", "y_outlier", "last_price",
                               pl.col(f"yhat__{m}").alias("yhat"), pl.lit(m).alias("model")) for m in models])
    return long, info


def select(long: pl.DataFrame) -> pl.DataFrame:
    """Per headline horizon: fold WAPE per model, win share vs last value, and the selected model."""
    d = long.filter(pl.col("h").is_in(list(HEADLINE_H)) & ~pl.col("y_outlier"))
    folds = summarise(d, ["h", "model", "fold"]).select("h", "model", "fold", "wape")
    lv = folds.filter(pl.col("model") == "last_value").select("h", "fold", pl.col("wape").alias("wape_lv"))
    s = (folds.join(lv, on=["h", "fold"]).group_by(["h", "model"])
         .agg(pl.col("wape").mean().alias("mean_fold_wape"), (pl.col("wape") < pl.col("wape_lv")).mean()
              .alias("share_folds_beating_last_value"), pl.len().alias("folds")))
    s = s.with_columns(((pl.col("model") == "last_value")
                        | (pl.col("share_folds_beating_last_value") >= MIN_FOLD_WIN_SHARE)).alias("qualifies"))
    best = (s.filter(pl.col("qualifies")).sort(["h", "mean_fold_wape"]).group_by("h", maintain_order=True).first()
            .select("h", pl.col("model").alias("selected")))
    return s.join(best, on="h").with_columns((pl.col("model") == pl.col("selected")).alias("is_selected")).sort(
        ["h", "mean_fold_wape"])


def report(commodity: str, period: str, models: list[str] | None = None) -> dict:
    models = models or available_models(period, commodity)
    long, info = scored_frame(commodity, period, models)
    headline = summarise(long.filter(~pl.col("y_outlier") & pl.col("h").is_in(list(HEADLINE_H))), ["model", "h"])
    with_outliers = summarise(long.filter(pl.col("h").is_in(list(HEADLINE_H))), ["model", "h"])
    buckets = summarise(long.filter(~pl.col("y_outlier")).with_columns(
        pl.when(pl.col("h") <= 7).then(pl.lit("1-7")).otherwise(pl.lit("8-14")).alias("days")), ["model", "days"])
    by_state = summarise(long.filter(~pl.col("y_outlier") & (pl.col("h") == 7)), ["model", "state"])
    out = {"commodity": commodity, "period": period, "models": models, "points": info,
           "headline": headline.to_dicts(), "including_outlier_targets": with_outliers.to_dicts(),
           "buckets": buckets.to_dicts(), "by_state_h7": by_state.to_dicts()}
    if period == "dev":
        out["selection"] = select(long).to_dicts()
    out["sampled_model_comparison"] = sample_comparison(commodity, period)
    d = OUT / period / "_reports"
    d.mkdir(parents=True, exist_ok=True)
    Path(d / f"{commodity.lower()}.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.stdout.reconfigure(encoding="utf-8")
    period = sys.argv[1] if len(sys.argv) > 1 else "dev"
    for c in get_settings().commodities:
        r = report(c, period)
        print(c, r["points"])
