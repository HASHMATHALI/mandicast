"""Simulation of the fallback rule (methodology C10c) on the stored walk-forward predictions.

    python -I scripts/backtest_fallback.py

At every development and hold-out origin O, per crop and day range, the rule looks at the model and last-value
predictions of origins in [O − 56 days, O) whose targets were reported before O (outlier targets excluded), and
serves last value at O if there are ≥ 500 such points and the model's WAPE is higher. The served forecasts are then
scored on O's own targets, next to the model alone and last value alone (outlier targets excluded, as in the
headline metrics). The rule was recorded before this simulation was run (docs/06 §9b); the hold-out results were
already known in aggregate, so the hold-out rows are a consistency check, not an unbiased estimate of the benefit.
Writes reports/phase5/fallback_backtest.json.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandicast.config import get_settings  # noqa: E402
from mandicast.db.session import get_engine  # noqa: E402
from mandicast.forecast.run import OUT  # noqa: E402
from mandicast.forecast.walkforward import dev_origins, holdout_origins  # noqa: E402
from mandicast.serving import registry  # noqa: E402
from mandicast.serving.publish import DAY_RANGES, FALLBACK_DAYS, FALLBACK_MIN_POINTS  # noqa: E402


def load(c: str, primary: pl.DataFrame) -> pl.DataFrame:
    keys = ["series_id", "origin", "h"]
    parts = []
    for period in ("dev", "holdout"):
        m = pl.read_parquet(OUT / period / "gbm" / f"{c.lower()}.parquet").select(*keys, pl.col("yhat").alias("m"))
        lv = pl.read_parquet(OUT / period / "last_value" / f"{c.lower()}.parquet").select(*keys,
                                                                                          pl.col("yhat").alias("lv"))
        parts.append(m.join(lv, on=keys).with_columns(pl.lit(period).alias("period")))
    p = pl.concat(parts).with_columns(pl.col("series_id").cast(pl.Int32), pl.col("origin").cast(pl.Date),
                                      pl.col("h").cast(pl.Int16))
    p = p.with_columns((pl.col("origin") + pl.duration(days=pl.col("h"))).alias("target_date"))
    y = primary.filter(pl.col("input_ok")).select("series_id", pl.col("date").alias("target_date"),
                                                  pl.col("price").alias("y"))
    return p.join(y, on=["series_id", "target_date"], how="inner").with_columns(
        pl.when(pl.col("h") <= 7).then(pl.lit("1-7")).otherwise(pl.lit("8-14")).alias("days"))


def wape(df: pl.DataFrame, col: str) -> float:
    return float((df["y"] - df[col]).abs().sum() / df["y"].sum())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    eng = get_engine()
    origins = {"dev": dev_origins(), "holdout": holdout_origins()}
    out = {"run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "rule": "methodology C10c",
           "window_days": FALLBACK_DAYS, "min_points": FALLBACK_MIN_POINTS, "commodities": {}}
    for c in get_settings().commodities:
        pts = load(c, registry.load_serving_primary(eng, c, refresh=False))
        decisions = []
        for period, os_ in origins.items():
            for o in os_:
                hist = pts.filter((pl.col("origin") >= o - timedelta(days=FALLBACK_DAYS)) & (pl.col("origin") < o)
                                  & (pl.col("target_date") < o))
                for rng, (a, b) in DAY_RANGES.items():
                    d = hist.filter(pl.col("h").is_between(a, b))
                    use_lv = d.height >= FALLBACK_MIN_POINTS and wape(d, "m") > wape(d, "lv")
                    decisions.append({"origin": o, "days": rng, "period": period, "use_lv": use_lv, "n": d.height})
        dec = pl.DataFrame(decisions).with_columns(pl.col("origin").cast(pl.Date))
        scored = pts.join(dec, on=["origin", "days", "period"], how="inner").with_columns(
            pl.when(pl.col("use_lv")).then(pl.col("lv")).otherwise(pl.col("m")).alias("served"))
        rows = []
        for (period, rng), g in scored.group_by(["period", "days"], maintain_order=True):
            d = dec.filter((pl.col("period") == period) & (pl.col("days") == rng))
            rows.append({"period": period, "days": rng, "points": g.height,
                         "wape_model": round(wape(g, "m"), 5), "wape_last_value": round(wape(g, "lv"), 5),
                         "wape_served": round(wape(g, "served"), 5),
                         "origins_on_fallback": int(d["use_lv"].sum()), "origins": d.height,
                         "fallback_origins": [str(x) for x in d.filter(pl.col("use_lv"))["origin"].to_list()]})
        out["commodities"][c] = sorted(rows, key=lambda r: (r["period"] != "dev", r["days"]))
        for r in out["commodities"][c]:
            print(c, {k: v for k, v in r.items() if k != "fallback_origins"}, flush=True)
    d = ROOT / "reports" / "phase5"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fallback_backtest.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("written", d / "fallback_backtest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
