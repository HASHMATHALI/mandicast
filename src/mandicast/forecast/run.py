"""Run forecasting models over the walk-forward origins and store their predictions.

    python -m mandicast.forecast.run baselines --period dev
    python -m mandicast.forecast.run stats --model ets --period dev [--commodity Soyabean] [--max-series 24]

Predictions: experiments/forecast/<period>/<model>/<commodity>.parquet with columns series_id, origin, h, yhat
(+ fallback for baselines). Scoring and selection read these files (forecast/evaluate.py). The hold-out period
is refused unless --confirm-holdout is given, so it cannot be scored by accident before choices are frozen.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.db.session import get_engine
from mandicast.forecast import baselines, gbm, stats_models
from mandicast.forecast.data import CACHE_DIR, load_primary, series_info
from mandicast.forecast.walkforward import dev_origins, eval_frame, holdout_origins

log = logging.getLogger("mandicast.forecast")
OUT = PROJECT_ROOT / "experiments" / "forecast"
FROZEN = OUT / "frozen_choices.json"


def origins_for(period: str):
    return dev_origins() if period == "dev" else holdout_origins()


SAMPLE_SEED = 20261126


def sample_series(commodity: str, n: int, seed: int = SAMPLE_SEED) -> list[int]:
    """Fixed random sample of series evaluated at ≥ 1 development origin (methodology D3); same for both periods."""
    e = pl.read_parquet(CACHE_DIR / f"eligibility_{commodity.lower()}.parquet")
    dev_ids = sorted(e.filter(pl.col("origin").is_in(pl.Series(dev_origins()).implode()))["series_id"].unique())
    rng = np.random.default_rng(seed)
    return sorted(int(x) for x in rng.choice(dev_ids, size=min(n, len(dev_ids)), replace=False))


def pairs_for(commodity: str, period: str, max_series: int | None, sample: int | None = None) -> pl.DataFrame:
    e = pl.read_parquet(CACHE_DIR / f"eligibility_{commodity.lower()}.parquet")
    e = e.filter(pl.col("origin").is_in(pl.Series(origins_for(period)).implode()))
    if sample:
        e = e.filter(pl.col("series_id").is_in(pl.Series(sample_series(commodity, sample), dtype=pl.Int32).implode()))
    if max_series:
        keep = e["series_id"].unique().sort().head(max_series)
        e = e.filter(pl.col("series_id").is_in(keep.implode()))
    return e


def _save(df: pl.DataFrame, period: str, model: str, commodity: str, meta: dict) -> Path:
    d = OUT / period / model
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{commodity.lower()}.parquet"
    df.write_parquet(path)
    meta = {**meta, "rows": df.height, "written_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_baselines(commodity: str, period: str, max_series: int | None) -> None:
    p = load_primary(get_engine(), commodity)
    f = eval_frame(p, pairs_for(commodity, period, max_series), origins_for(period))
    for name, res in (("last_value", baselines.last_value(f)), ("seasonal_naive", baselines.seasonal_naive(f, p)),
                      ("mean_7d", baselines.moving_mean(f, p, 7)), ("mean_28d", baselines.moving_mean(f, p, 28))):
        out = res.select("series_id", "origin", "h", "yhat", "fallback")
        _save(out, period, name, commodity, {"commodity": commodity, "period": period, "model": name,
                                             "fallback_share": float(out["fallback"].mean())})
        log.info("%s %s %s: %d predictions", period, name, commodity, out.height)


def run_stats(model: str, commodity: str, period: str, max_series: int | None, workers: int | None,
              sample: int | None = None) -> None:
    p = load_primary(get_engine(), commodity)
    pairs = pairs_for(commodity, period, max_series, sample)
    t = time.perf_counter()
    fc, fails = stats_models.forecast(model, p, pairs, workers)
    meta = {"commodity": commodity, "period": period, "model": model, "pairs": pairs.height,
            "pairs_forecast": fc.select("series_id", "origin").unique().height, **fails,
            "seconds": round(time.perf_counter() - t, 1), "window_days": stats_models.WINDOW_DAYS,
            "season": stats_models.SEASON, "sample_per_commodity": sample,
            "sample_seed": SAMPLE_SEED if sample else None,
            "statsforecast": __import__("statsforecast").__version__}
    _save(fc, period, model, commodity, meta)
    log.info("%s %s %s: %s", period, model, commodity, meta)


def run_gbm(commodity: str, period: str, max_series: int | None) -> None:
    """Quarterly-trained global LightGBM (methodology C6). Training labels flagged as data-entry outliers are left
    out of training (they are corrupt labels); evaluation treats them as described in C5."""
    eng = get_engine()
    t0 = time.perf_counter()
    p = load_primary(eng, commodity)
    states = series_info(eng, commodity).select("series_id", "state")
    hol = gbm.holiday_table(range(2014, 2028))
    pairs_all = gbm.training_eligibility(p, commodity)
    feats = gbm.origin_features(p, pairs_all, states)
    float_cols = [c for c, t in feats.schema.items() if t == pl.Float64 and c != "last_price"]
    feats = feats.with_columns([pl.col(c).cast(pl.Float32) for c in float_cols])
    examples = gbm.expand_targets(feats, p, hol, with_y=True).filter(~pl.col("y_outlier"))
    eval_pairs = pairs_for(commodity, period, max_series)
    eval_feats = feats.join(eval_pairs, on=["series_id", "origin"], how="semi")
    rows = gbm.expand_targets(eval_feats, p, hol, with_y=False).with_columns(gbm.fold_of())
    preds, fits = [], []
    for fold, cutoff in gbm.fold_cutoffs(sorted(eval_pairs["origin"].unique().to_list())).items():
        t = time.perf_counter()
        model, info = gbm.train(examples, cutoff)
        part = rows.filter(pl.col("fold") == fold)
        preds.append(gbm.predict(model, part))
        fits.append({**info, "fold": fold, "predicted_rows": part.height, "seconds": round(time.perf_counter() - t, 1)})
        log.info("gbm %s %s: %s", commodity, fold, fits[-1])
    out = pl.concat(preds)
    meta = {"commodity": commodity, "period": period, "model": "gbm", "params": gbm.PARAMS,
            "features": gbm.FEATURES, "fits": fits, "training_pairs": pairs_all.height, "examples": examples.height,
            "lightgbm": __import__("lightgbm").__version__, "holidays": __import__("holidays").__version__,
            "seconds": round(time.perf_counter() - t0, 1)}
    _save(out, period, "gbm", commodity, meta)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["baselines", "stats", "gbm"])
    ap.add_argument("--model", choices=["ets", "arima"])
    ap.add_argument("--period", choices=["dev", "holdout"], required=True)
    ap.add_argument("--commodity", action="append")
    ap.add_argument("--max-series", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--sample-per-commodity", type=int, help="fixed random sample of series (methodology D3)")
    ap.add_argument("--confirm-holdout", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if sys.platform == "win32":  # per-process request so long runs are not stopped by idle sleep
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    if a.period == "holdout" and not a.confirm_holdout:
        raise SystemExit("refusing to score the hold-out without --confirm-holdout (methodology §3)")
    if a.period == "holdout" and not FROZEN.exists():
        raise SystemExit(f"refusing to run the hold-out before choices are frozen in {FROZEN} (methodology C8)")
    for c in a.commodity or get_settings().commodities:
        if a.what == "baselines":
            run_baselines(c, a.period, a.max_series)
        elif a.what == "gbm":
            run_gbm(c, a.period, a.max_series)
        else:
            run_stats(a.model, c, a.period, a.max_series, a.workers, a.sample_per_commodity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
