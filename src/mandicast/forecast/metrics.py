"""Forecast metrics (methodology §5). All functions take a frame with y, yhat, last_price and optional interval
columns lo80, hi80, lo90, hi90, and aggregate over `by` keys."""

from __future__ import annotations

import polars as pl

DIRECTION_DEADBAND = 0.02  # moves smaller than 2% of the last price count as "flat" in the second accuracy figure


def summarise(df: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    e = (pl.col("yhat") - pl.col("y"))
    ae = e.abs()
    actual_move = (pl.col("y") - pl.col("last_price")) / pl.col("last_price")
    pred_move = (pl.col("yhat") - pl.col("last_price")) / pl.col("last_price")
    big = actual_move.abs() >= DIRECTION_DEADBAND
    same_sign = (actual_move.sign() == pred_move.sign())
    aggs = [
        pl.len().alias("n"),
        ae.mean().alias("mae"),
        (e.pow(2).mean().sqrt()).alias("rmse"),
        (ae.sum() / pl.col("y").abs().sum()).alias("wape"),
        (2 * ae / (pl.col("y").abs() + pl.col("yhat").abs())).mean().alias("smape"),
        (ae / pl.col("y").abs()).mean().alias("mape"),
        ae.mean().truediv((pl.col("last_price") - pl.col("y")).abs().mean()).alias("mase_vs_last_value"),
        same_sign.mean().alias("dir_acc_all"),
        same_sign.filter(big).mean().alias("dir_acc_moves_ge_2pct"),
        big.mean().alias("share_moves_ge_2pct"),
    ]
    for level in (80, 90):
        lo, hi = f"lo{level}", f"hi{level}"
        if lo in df.columns:
            aggs += [((pl.col("y") >= pl.col(lo)) & (pl.col("y") <= pl.col(hi))).mean().alias(f"coverage{level}"),
                     ((pl.col(hi) - pl.col(lo)) / pl.col("last_price")).mean().alias(f"rel_width{level}")]
    return df.group_by(by, maintain_order=True).agg(aggs).sort(by)
