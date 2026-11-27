"""Primary daily series from the cleaned layer, cached as Parquet, and eligibility evaluated at each origin.

Eligibility at origin t uses only reports dated ≤ t (methodology §2 rules, clarification C1), so the evaluated
set of series is never chosen by future activity.
"""

from __future__ import annotations

import io
import json
import logging
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from psycopg import sql
from sqlalchemy import Engine, text

from mandicast.clean.build import QUALITY_FLAGS_FOR_ELIGIBILITY, RULES
from mandicast.config import PROJECT_ROOT

log = logging.getLogger(__name__)
CACHE_DIR = PROJECT_ROOT / "data" / "processed"


def _copy_out(engine: Engine, query: sql.Composable) -> bytes:
    raw = engine.raw_connection()
    try:
        buf = io.BytesIO()
        with raw.driver_connection.cursor() as cur, cur.copy(
                sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT csv, HEADER true)").format(query)) as cp:
            for chunk in cp:
                buf.write(chunk)
        return buf.getvalue()
    finally:
        raw.close()


def load_primary(engine: Engine, commodity: str, refresh: bool = False, cache_dir: Path = CACHE_DIR) -> pl.DataFrame:
    """One row per (series, day) with a valid report: series_id, date, price, quality_flag, input_ok.

    `quality_flag`: the primary row carries one of the eligibility quality flags.
    `input_ok`: false for `local_outlier` values (not used as model inputs, clarification C3)."""
    path = cache_dir / f"primary_{commodity.lower()}.parquet"
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    q = sql.SQL(
        "SELECT po.series_id, po.date, po.modal_price, po.flags::text AS flags FROM price_observations po "
        "JOIN price_series ps ON ps.id = po.series_id JOIN commodities c ON c.id = ps.commodity_id "
        "WHERE c.name = {} AND po.is_primary ORDER BY po.series_id, po.date").format(sql.Literal(commodity))
    df = pl.read_csv(_copy_out(engine, q), schema_overrides={"series_id": pl.Int32, "date": pl.Date,
                                                             "modal_price": pl.Float64, "flags": pl.String})
    qf = "|".join(QUALITY_FLAGS_FOR_ELIGIBILITY)
    df = df.select("series_id", "date", pl.col("modal_price").alias("price"),
                   pl.col("flags").str.contains(qf).alias("quality_flag"),
                   (~pl.col("flags").str.contains("local_outlier")).alias("input_ok"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    with engine.connect() as c:
        db_count = c.execute(text(
            "SELECT count(*) FROM price_observations po JOIN price_series ps ON ps.id = po.series_id "
            "JOIN commodities k ON k.id = ps.commodity_id WHERE k.name = :c AND po.is_primary"),
            {"c": commodity}).scalar_one()
    meta = {"commodity": commodity, "rows": df.height, "db_primary_rows": db_count,
            "extracted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if db_count != df.height:
        raise RuntimeError(f"extract mismatch for {commodity}: {df.height} rows vs {db_count} in the database")
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return df


def series_info(engine: Engine, commodity: str) -> pl.DataFrame:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT ps.id, s.display_name, d.display_name, m.display_name FROM price_series ps "
            "JOIN commodities k ON k.id = ps.commodity_id JOIN markets m ON m.id = ps.market_id "
            "JOIN districts d ON d.id = m.district_id JOIN states s ON s.id = d.state_id WHERE k.name = :c"),
            {"c": commodity}).all()
    return pl.DataFrame([tuple(r) for r in rows], schema={"series_id": pl.Int32, "state": pl.String,
                                                          "district": pl.String, "market": pl.String}, orient="row")


def mondays(start: date, end: date) -> list[date]:
    d = start + timedelta(days=(7 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def eligibility_by_origin(primary: pl.DataFrame, origins: list[date]) -> pl.DataFrame:
    """Rows (series_id, origin) for every series that passes all §2 rules at that origin, using data ≤ origin."""
    p = primary.sort(["series_id", "date"]).with_columns(
        pl.int_range(1, pl.len() + 1).over("series_id").alias("cum_n"),
        pl.col("quality_flag").cast(pl.Int32).cum_sum().over("series_id").alias("cum_q"))
    o = pl.DataFrame({"origin": origins}).with_columns(pl.col("origin").cast(pl.Date))
    pairs = p.select("series_id").unique().join(o, how="cross")

    def asof(at: pl.Expr, name: str) -> pl.DataFrame:
        left = pairs.with_columns(at.alias("_at")).sort("_at")
        right = p.select("series_id", pl.col("date").alias("_at"), "cum_n", "cum_q").sort("_at")
        return (left.join_asof(right, on="_at", by="series_id", strategy="backward", check_sortedness=False)
                .select("series_id", "origin", pl.col("cum_n").fill_null(0).alias(f"n_{name}"),
                        pl.col("cum_q").fill_null(0).alias(f"q_{name}"), pl.col("_at")))

    now = asof(pl.col("origin"), "now")
    last = (pairs.with_columns(pl.col("origin").alias("_at")).sort("_at")
            .join_asof(p.select("series_id", pl.col("date").alias("_at"), pl.col("date").alias("last")).sort("_at"),
                       on="_at", by="series_id", strategy="backward", check_sortedness=False)
            .select("series_id", "origin", "last"))
    b730 = asof(pl.col("origin") - timedelta(days=730), "b730")
    b365 = asof(pl.col("origin") - timedelta(days=365), "b365")
    e = (now.drop("_at").join(last, on=["series_id", "origin"]).join(b730.drop("_at"), on=["series_id", "origin"])
         .join(b365.drop("_at"), on=["series_id", "origin"])
         .with_columns((pl.col("n_now") - pl.col("n_b730")).alias("d730"),
                       (pl.col("n_now") - pl.col("n_b365")).alias("d365"),
                       (pl.col("q_now") - pl.col("q_b365")).alias("q365")))
    e = e.filter((pl.col("last") >= pl.col("origin") - timedelta(days=RULES["max_days_since_last"]))
                 & (pl.col("d730") >= RULES["min_report_days_in_last_730"])
                 & (pl.col("q365") <= RULES["max_flagged_share_last_365"] * pl.col("d365")))
    # Median gap between reports over the previous 365 days (exact, per pair, numpy on sorted dates).
    dates = {key[0]: g["date"].to_numpy() for key, g in p.select("series_id", "date").group_by(
        "series_id", maintain_order=True)}
    keep = []
    for sid, origin in e.select("series_id", "origin").iter_rows():
        d = dates[sid]
        o_np = np.datetime64(origin)
        hi = bisect_right(d, o_np)
        lo = bisect_right(d, o_np - np.timedelta64(365, "D"))
        if hi - lo < 2 and lo == 0:
            keep.append(False)
            continue
        window = d[max(lo - 1, 0):hi]  # include the report before the window so the first gap is defined
        gaps = np.diff(window).astype("timedelta64[D]").astype(int)
        keep.append(bool(len(gaps)) and float(np.median(gaps)) <= RULES["max_median_gap_last_365"])
    return e.select("series_id", "origin").filter(pl.Series(keep, dtype=pl.Boolean))
