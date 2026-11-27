"""Global LightGBM per commodity (methodology §4, settings fixed in §9 C6).

One model per commodity and training cutoff (calendar quarter start), horizon as a feature. Every feature of a
(series, origin) example uses reports dated ≤ origin; target-date features are calendar facts. Training examples
are eligible (series, Monday) pairs from 2015 whose targets all fall before the cutoff − 105 days; the 15–105 days
before the cutoff provide the early-stopping set. Nothing after the cutoff is used.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import polars as pl

from mandicast.forecast.data import CACHE_DIR, eligibility_by_origin, mondays
from mandicast.forecast.walkforward import HORIZON, fold_of

log = logging.getLogger(__name__)
TRAIN_START = date(2015, 1, 1)
VALID_DAYS = 105  # examples with origin in (cutoff − 105, cutoff − 15] are the early-stopping set
WINDOWS = (7, 28, 91, 365)
CHANGE_LAGS = (7, 14, 28)
PARAMS = {"objective": "l1", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 200,
          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 20261126,
          "num_threads": 12}  # explicit: run alone (oversubscribed OpenMP threads busy-wait and crawl)
MAX_TREES, EARLY_STOP = 1000, 50


def training_eligibility(primary: pl.DataFrame, commodity: str, refresh: bool = False) -> pl.DataFrame:
    path = CACHE_DIR / f"eligibility_train_{commodity.lower()}.parquet"
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    e = eligibility_by_origin(primary, mondays(TRAIN_START, date(2026, 11, 25)))
    e.write_parquet(path)
    return e


def _cum(inputs: pl.DataFrame) -> pl.DataFrame:
    return (inputs.sort(["series_id", "date"])
            .with_columns(pl.col("price").log().alias("lp"))
            .with_columns((pl.col("lp") - pl.col("lp").shift(1).over("series_id")).fill_null(0.0).alias("dlp"))
            .with_columns(pl.col("lp").cum_sum().over("series_id").alias("c_lp"),
                          (pl.col("dlp") ** 2).cum_sum().over("series_id").alias("c_d2"),
                          pl.col("dlp").cum_sum().over("series_id").alias("c_d"),
                          pl.int_range(1, pl.len() + 1).over("series_id").alias("c_n"))
            .select("series_id", pl.col("date").alias("_at"), "lp", "c_lp", "c_d2", "c_d", "c_n").sort("_at"))


def _asof(pairs: pl.DataFrame, cum: pl.DataFrame, days_back: int, suffix: str) -> pl.DataFrame:
    return (pairs.with_columns((pl.col("origin") - timedelta(days=days_back)).alias("_at")).sort("_at")
            .join_asof(cum, on="_at", by="series_id", strategy="backward", check_sortedness=False)
            .select("series_id", "origin", *[pl.col(c).alias(f"{c}{suffix}") for c in ("lp", "c_lp", "c_d2", "c_d",
                                                                                         "c_n")]))


def origin_features(primary: pl.DataFrame, pairs: pl.DataFrame, states: pl.DataFrame) -> pl.DataFrame:
    """Features of each (series, origin) from input reports dated ≤ origin."""
    inputs = primary.filter(pl.col("input_ok")).select("series_id", "date", "price")
    cum = _cum(inputs)
    last = (pairs.with_columns(pl.col("origin").alias("_at")).sort("_at")
            .join_asof(inputs.select("series_id", pl.col("date").alias("_at"), pl.col("date").alias("last_date"),
                                     pl.col("price").alias("last_price")).sort("_at"),
                       on="_at", by="series_id", strategy="backward", check_sortedness=False).drop("_at"))
    f = last.join(_asof(pairs, cum, 0, "_0"), on=["series_id", "origin"])
    f = f.with_columns(pl.col("last_price").log().alias("log_last"),
                       (pl.col("origin") - pl.col("last_date")).dt.total_days().alias("age_days"))
    for w in WINDOWS:
        f = f.join(_asof(pairs, cum, w, f"_{w}"), on=["series_id", "origin"], how="left")
        n = (pl.col("c_n_0") - pl.col(f"c_n_{w}").fill_null(0))
        mean_lp = (pl.col("c_lp_0") - pl.col(f"c_lp_{w}").fill_null(0)) / n
        mean_d = (pl.col("c_d_0") - pl.col(f"c_d_{w}").fill_null(0)) / n
        var_d = (pl.col("c_d2_0") - pl.col(f"c_d2_{w}").fill_null(0)) / n - mean_d ** 2
        f = f.with_columns(n.alias(f"n_{w}d"),
                           pl.when(n > 0).then(pl.col("log_last") - mean_lp).otherwise(None)
                           .alias(f"last_vs_mean_{w}d"),
                           pl.when(n > 2).then(var_d.clip(lower_bound=0).sqrt()).otherwise(None).alias(f"vol_{w}d"))
    for lag in CHANGE_LAGS:
        # lp_<lag>: log price of the last input report dated ≤ origin − lag days (already there for window lags)
        if f"lp_{lag}" not in f.columns:
            f = f.join(_asof(pairs, cum, lag, f"_{lag}").select("series_id", "origin", f"lp_{lag}"),
                       on=["series_id", "origin"], how="left")
        f = f.with_columns((pl.col("log_last") - pl.col(f"lp_{lag}")).alias(f"chg_{lag}d"))
    # Stable integer code per state: position in the sorted list of all states of the commodity, so the same state
    # has the same code in every training, validation and prediction frame. (A per-frame categorical cast gave
    # order-dependent codes; found and fixed during development review, 2026-11-26.)
    codes = (states.select(pl.col("state").fill_null("?")).unique().sort("state")
             .with_row_index("state_code").with_columns(pl.col("state_code").cast(pl.Int32)))
    f = f.join(states.with_columns(pl.col("state").fill_null("?")), on="series_id", how="left").join(
        codes, on="state", how="left")
    f = f.with_columns(
        (pl.col("log_last") - pl.col("log_last").median().over(["origin", "state"])).alias("last_vs_state_median"),
        pl.col("last_vs_mean_28d").median().over("origin").alias("commodity_momentum_28d"),
        pl.col("chg_7d").median().over("origin").alias("commodity_chg_7d"))
    keep = ["series_id", "origin", "state", "state_code", "last_price", "log_last", "age_days",
            *[f"n_{w}d" for w in WINDOWS], *[f"last_vs_mean_{w}d" for w in WINDOWS], *[f"vol_{w}d" for w in WINDOWS],
            *[f"chg_{lag}d" for lag in CHANGE_LAGS], "last_vs_state_median", "commodity_momentum_28d",
            "commodity_chg_7d"]
    return f.select(keep)


def holiday_table(years: range) -> pl.DataFrame:
    import holidays
    days = sorted(holidays.country_holidays("IN", years=list(years)).keys())
    return pl.DataFrame({"hday": days}).with_columns(pl.col("hday").cast(pl.Date))


def expand_targets(feats: pl.DataFrame, primary: pl.DataFrame, hol: pl.DataFrame, with_y: bool) -> pl.DataFrame:
    """One row per (series, origin, h); with_y keeps only days with a report and adds the target."""
    g = (feats.with_columns(pl.int_ranges(1, HORIZON + 1).alias("h")).explode("h")
         .with_columns(pl.col("h").cast(pl.Int16),
                       (pl.col("origin") + pl.duration(days=pl.col("h"))).alias("target_date")))
    if with_y:
        t = primary.select("series_id", pl.col("date").alias("target_date"), pl.col("price").alias("y"),
                           (~pl.col("input_ok")).alias("y_outlier"))
        g = g.join(t, on=["series_id", "target_date"], how="inner")
    inputs = (primary.filter(pl.col("input_ok"))
              .select("series_id", pl.col("date").alias("_at"), pl.col("price").log().alias("_ya")).sort("_at"))
    g = (g.with_columns((pl.col("target_date") - timedelta(days=365)).alias("_at")).sort("_at")
         .join_asof(inputs, on="_at", by="series_id", strategy="nearest", tolerance="3d", check_sortedness=False)
         .with_columns((pl.col("_ya") - pl.col("log_last")).alias("year_ago_vs_last")).drop("_at", "_ya"))
    near = (g.select("target_date").unique().sort("target_date")
            .join_asof(hol.sort("hday"), left_on="target_date", right_on="hday", strategy="nearest",
                       check_sortedness=False)
            .with_columns((pl.col("target_date") - pl.col("hday")).dt.total_days().clip(-30, 30)
                          .alias("days_to_holiday")).select("target_date", "days_to_holiday"))
    doy = pl.col("target_date").dt.ordinal_day() * (2 * np.pi / 365.25)
    wk = pl.col("target_date").dt.week() * (2 * np.pi / 52.18)
    return (g.join(near, on="target_date", how="left")
            .with_columns(pl.col("target_date").dt.weekday().alias("dow"),
                          pl.col("target_date").dt.month().alias("month"),
                          doy.sin().alias("doy_sin"), doy.cos().alias("doy_cos"), wk.sin().alias("week_sin"),
                          wk.cos().alias("week_cos"),
                          (pl.col("days_to_holiday").abs() <= 3).alias("holiday_near")))


FEATURES = ["h", "log_last", "age_days", *[f"n_{w}d" for w in WINDOWS], *[f"last_vs_mean_{w}d" for w in WINDOWS],
            *[f"vol_{w}d" for w in WINDOWS], *[f"chg_{lag}d" for lag in CHANGE_LAGS], "last_vs_state_median",
            "commodity_momentum_28d", "commodity_chg_7d", "year_ago_vs_last", "days_to_holiday", "holiday_near", "dow",
            "month", "doy_sin", "doy_cos", "week_sin", "week_cos", "state_cat", "series_cat"]
CATEGORICAL = ["state_cat", "series_cat"]


def _matrix(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.col("state_code").alias("state_cat"), pl.col("series_id").alias("series_cat"),
                           pl.col("holiday_near").cast(pl.Int8)).select(FEATURES)


def train(examples: pl.DataFrame, cutoff: date):
    """Fit on examples whose targets are dated before cutoff − 105 days; early stopping on the later set."""
    import lightgbm as lgb
    tr = examples.filter(pl.col("target_date") < cutoff - timedelta(days=VALID_DAYS))
    va = examples.filter((pl.col("origin") > cutoff - timedelta(days=VALID_DAYS))
                         & (pl.col("target_date") < cutoff))
    y_tr = (tr["y"].log() - tr["log_last"]).to_numpy()
    y_va = (va["y"].log() - va["log_last"]).to_numpy()
    dtr = lgb.Dataset(_matrix(tr).to_pandas(), y_tr, categorical_feature=CATEGORICAL, free_raw_data=True)
    dva = lgb.Dataset(_matrix(va).to_pandas(), y_va, categorical_feature=CATEGORICAL, reference=dtr)
    model = lgb.train(PARAMS, dtr, num_boost_round=MAX_TREES, valid_sets=[dva],
                      callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
    return model, {"cutoff": str(cutoff), "train_rows": tr.height, "valid_rows": va.height,
                   "best_iteration": model.best_iteration}


def predict(model, rows: pl.DataFrame) -> pl.DataFrame:
    pred = model.predict(_matrix(rows).to_pandas(), num_iteration=model.best_iteration)
    return rows.select("series_id", "origin", "h").with_columns(
        (rows["last_price"] * np.exp(pred)).alias("yhat"))


def fold_cutoffs(origins: list[date]) -> dict[str, date]:
    """Training cutoff of each fold (calendar quarter of the origin) = first day of that quarter."""
    out: dict[str, date] = {}
    for o in origins:
        q = date(o.year, 3 * ((o.month - 1) // 3) + 1, 1)
        out.setdefault(f"{q.year}Q{(q.month - 1) // 3 + 1}", q)
    return out


__all__ = ["training_eligibility", "origin_features", "holiday_table", "expand_targets", "train", "predict",
           "fold_cutoffs", "fold_of", "PARAMS", "FEATURES"]
