"""Forecasting core: no look-ahead, baselines, metrics, guards. The price series below are artificial
(built to exercise one rule each); real-data behaviour is measured by the walk-forward runs, not here."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from mandicast.forecast import baselines, gbm
from mandicast.forecast.data import eligibility_by_origin, mondays
from mandicast.forecast.metrics import summarise
from mandicast.forecast.stats_models import regular_log_view
from mandicast.forecast.walkforward import dev_origins, eval_frame, holdout_origins


def artificial_series(series_id: int, start: date, days: int, every: int = 1, base: float = 1000.0,
                      outlier_on: set[int] | None = None) -> pl.DataFrame:
    """Artificial daily-ish price series with a gentle trend (not real data)."""
    rows = []
    for i in range(0, days, every):
        d = start + timedelta(days=i)
        rows.append((series_id, d, base * (1 + 0.001 * i), False, not (outlier_on and i in outlier_on)))
    return pl.DataFrame(rows, schema={"series_id": pl.Int32, "date": pl.Date, "price": pl.Float64,
                                      "quality_flag": pl.Boolean, "input_ok": pl.Boolean}, orient="row")


@pytest.fixture
def primary() -> pl.DataFrame:
    a = artificial_series(1, date(2022, 1, 1), 900)             # daily reports
    b = artificial_series(2, date(2022, 1, 1), 900, every=2)     # every other day
    c = artificial_series(3, date(2024, 6, 1), 300)              # starts late
    return pl.concat([a, b, c])


def test_periods_do_not_overlap_and_dev_targets_stay_in_dev():
    dev, ho = dev_origins(), holdout_origins()
    assert all(d.weekday() == 0 for d in dev + ho)
    assert max(dev) + timedelta(days=14) <= date(2025, 11, 30) < min(ho)


def test_eligibility_uses_only_data_up_to_the_origin(primary):
    origins = mondays(date(2023, 6, 1), date(2024, 9, 30))
    full = eligibility_by_origin(primary, origins)
    for o in origins[::10]:
        truncated = eligibility_by_origin(primary.filter(pl.col("date") <= o), [o])
        assert set(truncated["series_id"]) == set(full.filter(pl.col("origin") == o)["series_id"])
    assert 3 not in set(full.filter(pl.col("origin") < date(2024, 6, 1))["series_id"])  # no data yet


def test_eval_frame_targets_and_last_value(primary):
    o = date(2023, 6, 5)
    e = pl.DataFrame({"series_id": [1, 2], "origin": [o, o]}, schema={"series_id": pl.Int32, "origin": pl.Date})
    f = eval_frame(primary, e, [o])
    assert f["h"].min() == 1 and f["h"].max() == 14
    assert (f["target_date"] > f["origin"]).all() and (f["last_date"] <= f["origin"]).all()
    assert f.filter(pl.col("series_id") == 1).height == 14
    assert f.filter(pl.col("series_id") == 2).height == 7  # reports every other day


def test_outlier_value_is_not_an_input_but_is_a_flagged_target():
    # day 155 = 2023-06-05 (the origin itself, an input under C2), day 160 = 2023-06-10 (a target)
    p = artificial_series(1, date(2023, 1, 1), 400, outlier_on={155, 160})
    o = date(2023, 6, 5)
    e = pl.DataFrame({"series_id": [1], "origin": [o]}, schema={"series_id": pl.Int32, "origin": pl.Date})
    f = eval_frame(p, e, [o])
    assert f["last_date"][0] == date(2023, 6, 4)  # the origin-day report is flagged, so the day before is used
    assert f.filter(pl.col("target_date") == date(2023, 6, 10))["y_outlier"][0] is True


def test_moving_mean_matches_a_direct_computation(primary):
    o = date(2023, 6, 5)
    e = pl.DataFrame({"series_id": [2], "origin": [o]}, schema={"series_id": pl.Int32, "origin": pl.Date})
    f = eval_frame(primary, e, [o])
    got = baselines.moving_mean(f, primary, 28)["yhat"][0]
    window = primary.filter((pl.col("series_id") == 2) & (pl.col("date") <= o)
                            & (pl.col("date") > o - timedelta(days=28)))["price"]
    assert got == pytest.approx(window.mean())


def test_seasonal_naive_uses_the_year_ago_report_or_falls_back(primary):
    o = date(2023, 6, 5)
    e = pl.DataFrame({"series_id": [1, 3], "origin": [date(2023, 6, 5), date(2025, 1, 6)]},
                     schema={"series_id": pl.Int32, "origin": pl.Date})
    f = eval_frame(primary, e, [o, date(2025, 1, 6)])
    r = baselines.seasonal_naive(f, primary)
    one = r.filter((pl.col("series_id") == 1) & (pl.col("h") == 7))
    expected = primary.filter((pl.col("series_id") == 1) & (pl.col("date") == date(2023, 6, 12) - timedelta(days=365)))
    assert one["yhat"][0] == pytest.approx(expected["price"][0]) and one["fallback"][0] is False
    assert r.filter(pl.col("series_id") == 3)["fallback"].all()  # no history a year back


def test_metrics_on_known_values():
    df = pl.DataFrame({"g": ["a"] * 4, "y": [100.0, 110.0, 90.0, 100.0], "yhat": [105.0, 105.0, 95.0, 100.0],
                       "last_price": [100.0, 100.0, 100.0, 100.0]})
    m = summarise(df, ["g"]).to_dicts()[0]
    assert m["mae"] == pytest.approx(3.75)
    assert m["wape"] == pytest.approx(15 / 400)
    assert m["mase_vs_last_value"] == pytest.approx(3.75 / 5.0)
    # moves ≥ 2%: rows 2 (+10%, predicted +5%) and 3 (−10%, predicted −5%) → both right
    assert m["dir_acc_moves_ge_2pct"] == pytest.approx(1.0)


def test_regular_view_carries_last_value_and_never_uses_later_reports():
    d = np.array(["2024-01-01", "2024-01-03", "2024-01-10"], dtype="datetime64[D]")
    p = np.array([100.0, 110.0, 999.0])
    v = regular_log_view(d, p, date(2024, 1, 5), window_days=30)
    assert np.allclose(np.exp(v), [100, 100, 110, 110, 110])


def test_gbm_features_do_not_change_when_future_reports_are_removed(primary):
    states = pl.DataFrame({"series_id": [1, 2, 3], "state": ["x", "x", "y"]},
                          schema={"series_id": pl.Int32, "state": pl.String})
    o = date(2023, 9, 4)
    pairs = pl.DataFrame({"series_id": [1, 2], "origin": [o, o]}, schema={"series_id": pl.Int32, "origin": pl.Date})
    full = gbm.origin_features(primary, pairs, states).sort("series_id")
    cut = gbm.origin_features(primary.filter(pl.col("date") <= o), pairs, states).sort("series_id")
    assert full.equals(cut)
    hol = gbm.holiday_table(range(2022, 2025))
    rows_full = gbm.expand_targets(full, primary, hol, with_y=False).drop("target_date")
    rows_cut = gbm.expand_targets(cut, primary.filter(pl.col("date") <= o), hol, with_y=False).drop("target_date")
    assert rows_full.sort(["series_id", "h"]).equals(rows_cut.sort(["series_id", "h"]))


def test_holdout_cannot_be_run_by_accident():
    from mandicast.forecast.run import main
    with pytest.raises(SystemExit, match="confirm-holdout"):
        main(["baselines", "--period", "holdout", "--commodity", "Soyabean"])


def test_holdout_needs_frozen_choices(tmp_path, monkeypatch):
    import mandicast.forecast.run as run
    monkeypatch.setattr(run, "FROZEN", tmp_path / "frozen_choices.json")
    with pytest.raises(SystemExit, match="frozen"):
        run.main(["baselines", "--period", "holdout", "--confirm-holdout", "--commodity", "Soyabean"])


def test_conformal_interval_uses_only_earlier_folds_and_covers_as_calibrated():
    from mandicast.forecast import intervals
    rng = np.random.default_rng(0)
    folds = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
    rows = []
    for fi, f in enumerate(folds):
        scale = 0.02 if fi < 3 else 0.5  # the last fold is much noisier: earlier folds must not see it
        for _ in range(2000):
            y = 100 * float(np.exp(rng.normal(0, scale)))
            rows.append((1, date(2023, 1, 2) + timedelta(days=91 * fi), 1, f, y, False, 100.0))
    s = pl.DataFrame(rows, schema={"series_id": pl.Int32, "origin": pl.Date, "h": pl.Int16, "fold": pl.String,
                                   "y": pl.Float64, "y_outlier": pl.Boolean, "yhat": pl.Float64}, orient="row")
    out = intervals.rolling_dev_intervals(s, conditional=False)
    assert set(out["fold"]) == {"2023Q3", "2023Q4"}  # the first two folds have too little history
    q3 = out.filter(pl.col("fold") == "2023Q3")
    cov = ((q3["y"] >= q3["lo80"]) & (q3["y"] <= q3["hi80"])).mean()
    assert 0.75 <= cov <= 0.85
    q4 = out.filter(pl.col("fold") == "2023Q4")
    assert ((q4["y"] >= q4["lo80"]) & (q4["y"] <= q4["hi80"])).mean() < 0.3  # calibrated on calm folds only


def test_conditional_intervals_are_wider_for_volatile_series():
    from mandicast.forecast import intervals
    rng = np.random.default_rng(1)
    cal_rows, pred_rows = [], []
    for v in (0.01, 0.05, 0.2):
        for _ in range(3000):
            cal_rows.append((1, float(rng.normal(0, v)), v))
        pred_rows.append((int(v * 100), date(2024, 1, 1), 1, 100.0, v))
    cal = pl.DataFrame(cal_rows, schema={"h": pl.Int16, "r": pl.Float64, "vol": pl.Float64}, orient="row")
    preds = pl.DataFrame(pred_rows, schema={"series_id": pl.Int32, "origin": pl.Date, "h": pl.Int16,
                                            "yhat": pl.Float64, "vol": pl.Float64}, orient="row")
    out = intervals.apply(preds, cal, conditional=True).sort("vol")
    widths = (out["hi80"] - out["lo80"]).to_list()
    assert widths[0] < widths[1] < widths[2]


def test_state_codes_are_stable_across_frames(primary):
    states = pl.DataFrame({"series_id": [1, 2, 3], "state": ["Punjab", "Gujarat", "Kerala"]},
                          schema={"series_id": pl.Int32, "state": pl.String})
    o1, o2 = date(2024, 9, 2), date(2024, 9, 9)
    a = gbm.origin_features(primary, pl.DataFrame({"series_id": [1, 3], "origin": [o1, o1]},
                                                  schema={"series_id": pl.Int32, "origin": pl.Date}), states)
    b = gbm.origin_features(primary, pl.DataFrame({"series_id": [3, 2, 1], "origin": [o2, o2, o2]},
                                                  schema={"series_id": pl.Int32, "origin": pl.Date}), states)
    code = lambda df: dict(zip(df["state"], df["state_code"], strict=True))  # noqa: E731
    assert code(a)["Punjab"] == code(b)["Punjab"] and code(a)["Kerala"] == code(b)["Kerala"]
    assert code(b) == {"Gujarat": 0, "Kerala": 1, "Punjab": 2}  # sorted order
