"""Split-conformal prediction intervals (methodology §6, settings fixed in §9 C7).

Residual r = log(y) − log(ŷ) on targets without the `local_outlier` flag. Intervals for a fold use only residuals
from earlier folds (development: the 4 preceding quarters, at least 2; hold-out: the last 4 development quarters).
Optionally conditional on the volatility tercile of the series at the origin (terciles cut on the calibration set).
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import polars as pl

LEVELS = (80, 90)
CAL_FOLDS, MIN_CAL_FOLDS = 4, 2
VOL_DAYS = 91


def origin_volatility(primary: pl.DataFrame, pairs: pl.DataFrame) -> pl.DataFrame:
    """Std of daily log changes of input reports dated in (origin − 91 days, origin], per (series, origin)."""
    inputs = (primary.filter(pl.col("input_ok")).sort(["series_id", "date"])
              .with_columns(pl.col("price").log().diff().over("series_id").fill_null(0.0).alias("d"))
              .with_columns(pl.col("d").cum_sum().over("series_id").alias("c1"),
                            (pl.col("d") ** 2).cum_sum().over("series_id").alias("c2"),
                            pl.int_range(1, pl.len() + 1).over("series_id").alias("cn"))
              .select("series_id", pl.col("date").alias("_at"), "c1", "c2", "cn").sort("_at"))

    def at(days: int, sfx: str) -> pl.DataFrame:
        return (pairs.select("series_id", "origin").unique()
                .with_columns((pl.col("origin") - timedelta(days=days)).alias("_at")).sort("_at")
                .join_asof(inputs, on="_at", by="series_id", strategy="backward", check_sortedness=False)
                .select("series_id", "origin", *[pl.col(c).fill_null(0).alias(c + sfx) for c in ("c1", "c2", "cn")]))

    v = at(0, "_0").join(at(VOL_DAYS, "_w"), on=["series_id", "origin"])
    n = pl.col("cn_0") - pl.col("cn_w")
    mean = (pl.col("c1_0") - pl.col("c1_w")) / n
    var = (pl.col("c2_0") - pl.col("c2_w")) / n - mean ** 2
    return v.select("series_id", "origin", pl.when(n > 2).then(var.clip(lower_bound=0).sqrt()).alias("vol"))


def _quantiles(r: np.ndarray, level: int) -> tuple[float, float]:
    a = (100 - level) / 200
    return float(np.quantile(r, a)), float(np.quantile(r, 1 - a))


def apply(preds: pl.DataFrame, calib: pl.DataFrame, conditional: bool) -> pl.DataFrame:
    """Add lo/hi columns to `preds` (series_id, origin, h, yhat[, vol]) from calibration residuals
    `calib` (h, r[, vol]). Quantiles per h (and per volatility tercile if `conditional`)."""
    out = preds
    if conditional:
        cuts = np.nanquantile(calib["vol"].drop_nulls().to_numpy(), [1 / 3, 2 / 3])
        tercile = (pl.when(pl.col("vol").is_null()).then(pl.lit(1))
                   .when(pl.col("vol") <= cuts[0]).then(pl.lit(0))
                   .when(pl.col("vol") <= cuts[1]).then(pl.lit(1)).otherwise(pl.lit(2)).alias("vt"))
        calib, out = calib.with_columns(tercile), out.with_columns(tercile)
        keys = ["h", "vt"]
    else:
        keys = ["h"]
    rows = []
    for k, g in calib.group_by(keys, maintain_order=True):
        r = g["r"].to_numpy()
        rec = dict(zip(keys, k, strict=True))
        for level in LEVELS:
            rec[f"q_lo{level}"], rec[f"q_hi{level}"] = _quantiles(r, level)
        rows.append(rec)
    q = pl.DataFrame(rows).with_columns([pl.col(k).cast(out.schema[k]) for k in keys])
    out = out.join(q, on=keys, how="left")
    for level in LEVELS:
        out = out.with_columns((pl.col("yhat") * pl.col(f"q_lo{level}").exp()).alias(f"lo{level}"),
                               (pl.col("yhat") * pl.col(f"q_hi{level}").exp()).alias(f"hi{level}"))
    return out.drop([c for c in out.columns if c.startswith("q_") or c == "vt"])


def rolling_dev_intervals(scored: pl.DataFrame, conditional: bool) -> pl.DataFrame:
    """Development folds: each fold calibrated on the residuals of up to 4 preceding folds (at least 2).
    `scored`: series_id, origin, h, fold, y, y_outlier, yhat (+ vol)."""
    folds = sorted(scored["fold"].unique().to_list())
    res = scored.filter(~pl.col("y_outlier")).with_columns((pl.col("y").log() - pl.col("yhat").log()).alias("r"))
    parts = []
    for i, f in enumerate(folds):
        prev = folds[max(0, i - CAL_FOLDS):i]
        if len(prev) < MIN_CAL_FOLDS:
            continue
        cal = res.filter(pl.col("fold").is_in(prev))
        parts.append(apply(scored.filter(pl.col("fold") == f), cal, conditional))
    return pl.concat(parts) if parts else scored.head(0)
