"""Baselines (methodology §4): last value, seasonal naive, 7- and 28-day means. Inputs: reports dated ≤ origin
that are usable as inputs (`input_ok`). Where a baseline has no value, the last value is used and the share of
such fallbacks is reported, so every model is scored on exactly the same points."""

from __future__ import annotations

from datetime import timedelta

import polars as pl


def last_value(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("last_price").alias("yhat"), pl.lit(False).alias("fallback"))


def seasonal_naive(frame: pl.DataFrame, primary: pl.DataFrame) -> pl.DataFrame:
    """Report nearest (±3 days) to target_date − 365 days. That date is always ≤ origin (h ≤ 14)."""
    inputs = (primary.filter(pl.col("input_ok"))
              .select("series_id", pl.col("date").alias("_at"), pl.col("price").alias("_sn")).sort("_at"))
    f = (frame.with_columns((pl.col("target_date") - timedelta(days=365)).alias("_at")).sort("_at")
         .join_asof(inputs, on="_at", by="series_id", strategy="nearest", tolerance="3d", check_sortedness=False))
    return (f.with_columns(pl.col("_sn").is_null().alias("fallback"),
                           pl.coalesce("_sn", "last_price").alias("yhat"))
            .drop("_at", "_sn").sort(["series_id", "origin", "h"]))


def moving_mean(frame: pl.DataFrame, primary: pl.DataFrame, days: int) -> pl.DataFrame:
    """Mean of input reports dated in (origin − days, origin], from cumulative sums (exact, no large joins)."""
    c = (primary.filter(pl.col("input_ok")).sort(["series_id", "date"])
         .with_columns(pl.col("price").cum_sum().over("series_id").alias("_cs"),
                       pl.int_range(1, pl.len() + 1).over("series_id").alias("_cn"))
         .select("series_id", pl.col("date").alias("_at"), "_cs", "_cn").sort("_at"))
    pairs = frame.select("series_id", "origin").unique()

    def at(offset: int, suffix: str) -> pl.DataFrame:
        return (pairs.with_columns((pl.col("origin") - timedelta(days=offset)).alias("_at")).sort("_at")
                .join_asof(c, on="_at", by="series_id", strategy="backward", check_sortedness=False)
                .select("series_id", "origin", pl.col("_cs").fill_null(0.0).alias(f"cs{suffix}"),
                        pl.col("_cn").fill_null(0).alias(f"cn{suffix}")))

    m = at(0, "_now").join(at(days, "_old"), on=["series_id", "origin"]).with_columns(
        pl.when(pl.col("cn_now") > pl.col("cn_old"))
        .then((pl.col("cs_now") - pl.col("cs_old")) / (pl.col("cn_now") - pl.col("cn_old")))
        .otherwise(None).alias("_mm")).select("series_id", "origin", "_mm")
    f = frame.join(m, on=["series_id", "origin"], how="left")
    return (f.with_columns(pl.col("_mm").is_null().alias("fallback"), pl.coalesce("_mm", "last_price").alias("yhat"))
            .drop("_mm"))
