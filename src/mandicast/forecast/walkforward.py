"""Walk-forward design (methodology §3, clarification C2) and the evaluation frame.

- Origins are Mondays. Inputs: reports dated ≤ origin. Targets: reports dated origin+1 … origin+14 (h = 1 … 14).
- Development origins: 2023-01-02 … 2025-11-10 (150 Mondays; all their targets fall on or before 2025-11-30).
- Hold-out origins: 2025-12-01 … 2026-11-23. Scored once, after every choice is frozen.
- Folds are calendar quarters of the origin date.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from mandicast.forecast.data import mondays

HORIZON = 14
DEV = (date(2023, 1, 1), date(2025, 11, 30))
HOLDOUT = (date(2025, 12, 1), date(2026, 11, 25))
HEADLINE_H = (7, 14)


def dev_origins() -> list[date]:
    return [d for d in mondays(*DEV) if d + timedelta(days=HORIZON) <= DEV[1]]


def holdout_origins() -> list[date]:
    return [d for d in mondays(HOLDOUT[0], HOLDOUT[1] - timedelta(days=1))]


def fold_of(col: str = "origin") -> pl.Expr:
    return (pl.col(col).dt.year().cast(pl.String) + "Q" + pl.col(col).dt.quarter().cast(pl.String)).alias("fold")


def eval_frame(primary: pl.DataFrame, eligible: pl.DataFrame, origins: list[date]) -> pl.DataFrame:
    """One row per (series, origin, h) where a report exists on origin+h, with the last usable input value.

    Columns: series_id, origin, h, target_date, y, y_outlier, last_price, last_date, fold.
    `y_outlier`: the target value carries the trailing-only `local_outlier` flag (excluded from headline
    metrics, clarification C5)."""
    pairs = eligible.filter(pl.col("origin").is_in(pl.Series(origins).implode()))
    inputs = primary.filter(pl.col("input_ok")).select("series_id", pl.col("date").alias("_at"),
                                                       pl.col("date").alias("last_date"),
                                                       pl.col("price").alias("last_price")).sort("_at")
    last = (pairs.with_columns(pl.col("origin").alias("_at")).sort("_at")
            .join_asof(inputs, on="_at", by="series_id", strategy="backward", check_sortedness=False)
            .drop("_at"))
    grid = (last.with_columns(pl.int_ranges(1, HORIZON + 1).alias("h")).explode("h")
            .with_columns((pl.col("origin") + pl.duration(days=pl.col("h"))).alias("target_date")))
    targets = primary.select("series_id", pl.col("date").alias("target_date"), pl.col("price").alias("y"),
                             (~pl.col("input_ok")).alias("y_outlier"))
    out = grid.join(targets, on=["series_id", "target_date"], how="inner")
    return (out.filter(pl.col("last_price").is_not_null())
            .with_columns(pl.col("h").cast(pl.Int16), fold_of())
            .select("series_id", "origin", "h", "target_date", "y", "y_outlier", "last_price", "last_date", "fold")
            .sort(["series_id", "origin", "h"]))
