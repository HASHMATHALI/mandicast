"""Profile the downloaded AGMARKNET pages: integrity, validity, duplicates, naming, coverage and outliers.

    .venv/Scripts/python -I scripts/profile_sample.py onion tomato ...

Reads data/raw/agmarknet_variety_daily/<commodity>/ (pages + manifest), verifies every page against its SHA-256,
and writes reports/profile/<commodity>.json. Raw values are kept as strings and parsed explicitly, so nothing
is silently coerced. Also writes data/interim/<commodity>.parquet (all raw string columns + parsed columns) so
later steps do not have to re-read the pages.

"Series" below = one (State, District, Market, Variety, Grade) price series of a commodity.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "agmarknet_variety_daily"
INTERIM = ROOT / "data" / "interim"
REPORTS = ROOT / "reports" / "profile"
TODAY = date(2026, 11, 26)  # date of the download; future dates are counted against this
SERIES = ["State", "District", "Market", "Variety", "Grade"]
KEY = ["Arrival_Date", *SERIES]
PRICES = ["Min_Price", "Max_Price", "Modal_Price"]


def load(commodity_dir: Path) -> tuple[pl.DataFrame, dict]:
    manifest = {}
    for line in (commodity_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        manifest[rec["offset"]] = rec  # last entry per offset wins (re-fetched short pages)
    frames, bad_hash = [], []
    for off in sorted(manifest):
        rec = manifest[off]
        raw = gzip.decompress((commodity_dir / rec["file"]).read_bytes())
        if hashlib.sha256(raw).hexdigest() != rec["sha256_raw"]:
            bad_hash.append(rec["file"])
        frames.append(pl.read_csv(io.BytesIO(raw), infer_schema=False).with_columns(pl.lit(off).alias("_page")))
    df = pl.concat(frames, how="vertical")
    page_rows = df.height

    # Page-boundary dates re-fetched in full with the exact date filter replace the page rows for those dates
    # (see repair_boundaries in fetch_agmarknet_history.py).
    repair: dict = {"boundary_dates_replaced": 0}
    bman = commodity_dir / "by_date_manifest.jsonl"
    if bman.exists():
        by_date = {}
        for line in bman.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            by_date[rec["date"]] = rec
        date_frames = []
        for _date, rec in sorted(by_date.items()):
            raw = gzip.decompress((commodity_dir / rec["file"]).read_bytes())
            if hashlib.sha256(raw).hexdigest() != rec["sha256_raw"]:
                bad_hash.append(rec["file"])
            date_frames.append(pl.read_csv(io.BytesIO(raw), infer_schema=False).with_columns(pl.lit(-1).alias("_page")))
        in_pages = df.filter(pl.col("Arrival_Date").is_in(list(by_date)))
        refetched = pl.concat(date_frames, how="vertical")
        raw_cols = [c for c in df.columns if c != "_page"]
        repair = {"boundary_dates_replaced": len(by_date), "page_rows_for_those_dates": in_pages.height,
                  "refetched_rows_for_those_dates": refetched.height,
                  "page_rows_not_in_refetch": in_pages.select(raw_cols).join(
                      refetched.select(raw_cols), on=raw_cols, how="anti", nulls_equal=True).height,
                  "refetched_rows_not_in_pages": refetched.select(raw_cols).join(
                      in_pages.select(raw_cols), on=raw_cols, how="anti", nulls_equal=True).height}
        df = pl.concat([df.filter(~pl.col("Arrival_Date").is_in(list(by_date))), refetched], how="vertical")

    totals = [r["total_at_request"] for r in manifest.values()]
    meta = {"pages": len(manifest), "page_rows": page_rows, "boundary_repair": repair,
            "pages_failing_sha256": bad_hash, "rows_read": df.height,
            "total_reported_min": min(totals), "total_reported_max": max(totals),
            "first_fetch": min(r["fetched_at"] for r in manifest.values()),
            "last_fetch": max(r["fetched_at"] for r in manifest.values())}
    return df, meta


def _norm(col: str) -> pl.Expr:
    """Lower-case, drop everything except letters/digits: exposes names that differ only in spacing/punctuation."""
    return pl.col(col).str.to_lowercase().str.replace_all(r"[^a-z0-9]", "")


def variants(df: pl.DataFrame, col: str, within: list[str] | None = None, limit: int = 15) -> dict:
    keys = [*(within or []), "_n"]
    g = (df.select([*(within or []), col]).unique().with_columns(_norm(col).alias("_n"))
         .group_by(keys).agg(pl.col(col).sort().alias("spellings"), pl.len().alias("k")).filter(pl.col("k") > 1))
    return {"groups_with_multiple_spellings": g.height,
            "examples": g.sort("k", descending=True).head(limit).select([*(within or []), "spellings"]).to_dicts()}


def quantiles(s: pl.Series, qs=(0.001, 0.01, 0.05, 0.5, 0.95, 0.99, 0.999)) -> dict:
    s = s.drop_nulls()
    return {**{f"p{q * 100:g}": float(s.quantile(q)) for q in qs}, "min": float(s.min()), "max": float(s.max())}


def profile(commodity: str) -> dict:
    df, meta = load(RAW / commodity)
    out: dict = {"commodity_dir": commodity, "integrity": meta}

    df = df.with_columns(
        pl.col("Arrival_Date").str.strptime(pl.Date, "%d/%m/%Y", strict=False).alias("date"),
        *[pl.col(c).str.strip_chars().cast(pl.Float64, strict=False).alias(c.lower()) for c in PRICES],
    )
    n = df.height
    out["dates"] = {
        "unparseable": int(df["date"].null_count()),
        "min": str(df["date"].min()), "max": str(df["date"].max()),
        "after_download_date": int((df["date"] > TODAY).sum()),
        "rows_per_year": {str(k): v for k, v in df.group_by(pl.col("date").dt.year().alias("y")).len()
                          .sort("y").iter_rows()},
        "rows_by_weekday_1mon_7sun": {str(k): v for k, v in df.group_by(pl.col("date").dt.weekday().alias("d"))
                                      .len().sort("d").iter_rows()},
    }
    prices = {}
    for c in PRICES:
        lc = c.lower()
        raw_nonempty = df[c].is_not_null() & (df[c].str.strip_chars() != "")
        prices[c] = {"empty": int((~raw_nonempty).sum()),
                     "non_numeric": int((raw_nonempty & df[lc].is_null()).sum()),
                     "zero": int((df[lc] == 0).sum()), "negative": int((df[lc] < 0).sum())}
    # A zero min/max with a positive modal means "only the modal price was reported" (found in Phase 1),
    # so range checks apply only where both bounds are positive.
    both_bounds = (df["min_price"] > 0) & (df["max_price"] > 0)
    prices["violations"] = {
        "min_and_max_zero_modal_positive": int(((df["min_price"] == 0) & (df["max_price"] == 0)
                                                & (df["modal_price"] > 0)).sum()),
        "only_one_bound_zero": int(((df["min_price"] == 0) ^ (df["max_price"] == 0)).sum()),
        "min_gt_max": int((both_bounds & (df["min_price"] > df["max_price"])).sum()),
        "modal_lt_min": int((both_bounds & (df["modal_price"] < df["min_price"])).sum()),
        "modal_gt_max": int((both_bounds & (df["modal_price"] > df["max_price"])).sum()),
    }
    prices["modal_quantiles"] = quantiles(df["modal_price"])
    out["prices"] = prices

    raw_cols = [c for c in df.columns if c[0].isupper()]
    exact_dup = n - df.select(raw_cols).unique().height
    key_groups = df.group_by(KEY).agg(pl.len().alias("k"), pl.col("modal_price").n_unique().alias("modal_values"))
    out["duplicates"] = {
        "exact_duplicate_rows": exact_dup,
        "exact_duplicates_across_page_boundary": int(
            df.select([*raw_cols, "_page"]).group_by(raw_cols).agg(pl.col("_page").n_unique().alias("p"))
            .filter(pl.col("p") > 1).height),
        "keys_with_multiple_rows": int((key_groups["k"] > 1).sum()),
        "keys_with_conflicting_modal": int((key_groups["modal_values"] > 1).sum()),
        "rows_in_multi_row_keys": int(key_groups.filter(pl.col("k") > 1)["k"].sum() or 0),
    }

    out["names"] = {
        "commodity_name_code_pairs": df.group_by(["Commodity", "Commodity_Code"]).len().sort("len", descending=True)
        .to_dicts(),
        "states": {k: v for k, v in df.group_by("State").len().sort("State").iter_rows()},
        "n_districts": df.select(["State", "District"]).unique().height,
        "n_markets": df.select(["State", "District", "Market"]).unique().height,
        "varieties_top": df.group_by("Variety").len().sort("len", descending=True).head(15).to_dicts(),
        "grades": df.group_by("Grade").len().sort("len", descending=True).to_dicts(),
        "state_spelling_variants": variants(df, "State"),
        "district_spelling_variants": variants(df, "District", ["State"]),
        "market_spelling_variants": variants(df, "Market", ["State", "District"]),
    }

    # One value per series and day (first row; conflicts counted above), then coverage and gaps.
    daily = (df.filter(pl.col("date").is_not_null() & pl.col("modal_price").is_not_null())
             .unique(subset=[*SERIES, "date"], keep="first").sort([*SERIES, "date"]))
    daily = daily.with_columns(
        (pl.col("date") - pl.col("date").shift(1).over(SERIES)).dt.total_days().alias("gap"),
        (pl.col("modal_price") / pl.col("modal_price").rolling_median(window_size=15, center=True, min_samples=5)
         .over(SERIES)).alias("ratio_to_local_median"),
    )
    s = daily.group_by(SERIES).agg(
        pl.len().alias("days"), pl.col("date").min().alias("first"), pl.col("date").max().alias("last"),
        pl.col("gap").max().alias("max_gap"), pl.col("gap").median().alias("median_gap"),
        (pl.col("date") >= TODAY - timedelta(days=730)).sum().alias("days_last_2y"),
    ).with_columns(((pl.col("last") - pl.col("first")).dt.total_days() + 1).alias("span"))
    active = s.filter(pl.col("last") >= TODAY - timedelta(days=30))
    # Candidate for forecasting: reported in the last 30 days and on >= 50% of days in the last two years.
    candidates = active.filter(pl.col("days_last_2y") >= 365)
    out["series"] = {
        "series": s.height, "series_active_last_30d": active.height,
        "series_with_365plus_days_in_last_2y_and_active": candidates.height,
        "rows_in_those_series_last_2y": int(candidates["days_last_2y"].sum() or 0),
        "days_per_series_quantiles": quantiles(s["days"].cast(pl.Float64), (0.1, 0.25, 0.5, 0.75, 0.9)),
        "median_gap_days_quantiles_active": quantiles(active["median_gap"].cast(pl.Float64), (0.1, 0.5, 0.9))
        if active.height else None,
        "top_active_series": candidates.sort("days_last_2y", descending=True).head(10)
        .select([*SERIES, "days_last_2y", "first", "max_gap"]).with_columns(pl.col("first").cast(pl.String))
        .to_dicts(),
    }
    r = daily["ratio_to_local_median"].drop_nulls()
    out["outliers"] = {
        "points_with_local_median": r.len(),
        "ratio_gt_3_or_lt_one_third": int(((r > 3) | (r < 1 / 3)).sum()),
        "ratio_gt_10_or_lt_one_tenth": int(((r > 10) | (r < 0.1)).sum()),
        "abs_log_ratio_p99": float(r.log().abs().quantile(0.99)) if r.len() else math.nan,
    }

    INTERIM.mkdir(parents=True, exist_ok=True)
    df.drop("_page").write_parquet(INTERIM / f"{commodity}.parquet")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{commodity}.json").write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def main() -> None:
    names = sys.argv[1:] or sorted(p.name for p in RAW.iterdir() if p.is_dir())
    for c in names:
        if not re.fullmatch(r"[a-z0-9_]+", c):
            raise SystemExit(f"bad commodity dir name {c!r}")
        o = profile(c)
        i, d, p = o["integrity"], o["duplicates"], o["prices"]
        print(f"{c}: rows {i['rows_read']:,} (reported {i['total_reported_max']:,}), "
              f"sha fails {len(i['pages_failing_sha256'])}, "
              f"dates {o['dates']['min']}..{o['dates']['max']} unparseable {o['dates']['unparseable']}, "
              f"exact dups {d['exact_duplicate_rows']:,}, conflicting keys {d['keys_with_conflicting_modal']:,}, "
              f"violations {p['violations']}, series {o['series']['series']:,}, "
              f"forecast candidates {o['series']['series_with_365plus_days_in_last_2y_and_active']:,}")


if __name__ == "__main__":
    main()
