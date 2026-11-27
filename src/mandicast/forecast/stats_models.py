"""Per-series statistical models (AutoETS, AutoARIMA from statsforecast) under deviation D1.

For each series and calendar quarter in which it is evaluated: parameters are estimated once on the regularised
log-price view of the 730 days before the quarter start; at each weekly origin inside the quarter the fitted model
is applied (`forward`, parameters fixed) to the view of the 730 days up to the origin, and 14 daily steps are
forecast. Step h corresponds to origin + h.

Regularised view (model input only, methodology §1): daily calendar, last input report carried forward up to the
origin, log scale. Values flagged `local_outlier` are not inputs (C3). Nothing here is stored as data.
"""

from __future__ import annotations

import logging
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

import numpy as np
import polars as pl

log = logging.getLogger(__name__)
WINDOW_DAYS = 730
MIN_POINTS = 60
SEASON = 7


def _make_model(name: str):
    from statsforecast.models import AutoARIMA, AutoETS
    if name == "ets":
        return AutoETS(season_length=SEASON)
    if name == "arima":
        return AutoARIMA(season_length=SEASON)
    raise ValueError(name)


def regular_log_view(dates: np.ndarray, prices: np.ndarray, end: date, window_days: int = WINDOW_DAYS) -> np.ndarray:
    """Daily log prices for (end − window, end], last report carried forward; empty if no report in the window."""
    start = np.datetime64(end - timedelta(days=window_days))
    end64 = np.datetime64(end)
    m = (dates > start) & (dates <= end64)
    if not m.any():
        return np.empty(0)
    d, p = dates[m], prices[m]
    days = np.arange(d[0], end64 + np.timedelta64(1, "D"), dtype="datetime64[D]")
    idx = np.searchsorted(d, days, side="right") - 1
    return np.log(p[idx])


def _series_task(args: tuple) -> tuple[list[tuple], int, int]:
    """Worker: one series, all its quarters. Returns ((series_id, origin, h, yhat) tuples, failed fits,
    failed forwards). Failures are counted and reported; nothing is filled in for them."""
    name, series_id, dates, prices, quarters = args
    warnings.filterwarnings("ignore")
    out: list[tuple] = []
    fit_fail = fwd_fail = 0
    for q_start, origins in quarters:
        y_fit = regular_log_view(dates, prices, q_start - timedelta(days=1))
        if len(y_fit) < MIN_POINTS:
            continue
        model = _make_model(name)
        try:
            model.fit(y_fit)
        except Exception:  # noqa: BLE001 - counted below; the quarter gets no forecast from this model
            fit_fail += 1
            continue
        for origin in origins:
            y = regular_log_view(dates, prices, origin)
            if len(y) < MIN_POINTS:
                continue
            try:
                fc = model.forward(y=y, h=14)["mean"]
            except Exception:  # noqa: BLE001 - counted; this origin gets no forecast from this model
                fwd_fail += 1
                continue
            out.extend((series_id, origin, h, float(np.exp(fc[h - 1]))) for h in range(1, 15))
    return out, fit_fail, fwd_fail


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def forecast(name: str, primary: pl.DataFrame, pairs: pl.DataFrame,
             workers: int | None = None) -> tuple[pl.DataFrame, dict]:
    """Forecasts for every (series_id, origin) in `pairs` where a fit succeeded (series_id, origin, h, yhat),
    and counts of failed fits / forwards."""
    inputs = primary.filter(pl.col("input_ok")).sort(["series_id", "date"])
    by_series = {k[0]: (g["date"].to_numpy().astype("datetime64[D]"), g["price"].to_numpy())
                 for k, g in inputs.group_by("series_id", maintain_order=True)}
    tasks = []
    for k, g in pairs.with_columns(pl.col("origin").map_elements(quarter_start, return_dtype=pl.Date).alias("q"))\
            .group_by("series_id", maintain_order=True):
        sid = k[0]
        if sid not in by_series:
            continue
        quarters = [(qk[0], sorted(gg["origin"].to_list())) for qk, gg in g.group_by("q", maintain_order=True)]
        tasks.append((name, sid, *by_series[sid], sorted(quarters)))
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    rows: list[tuple] = []
    fails = {"series": len(tasks), "failed_fits": 0, "failed_forwards": 0}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (res, ff, fw) in enumerate(ex.map(_series_task, tasks, chunksize=4), start=1):
            rows.extend(res)
            fails["failed_fits"] += ff
            fails["failed_forwards"] += fw
            if i % 100 == 0:
                log.info("%s: %d/%d series done", name, i, len(tasks))
    df = pl.DataFrame(rows, schema={"series_id": pl.Int32, "origin": pl.Date, "h": pl.Int16, "yhat": pl.Float64},
                      orient="row")
    return df, fails
