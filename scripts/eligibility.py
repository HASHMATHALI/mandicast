"""Apply the forecasting sufficiency rules (docs/06 §2) at one origin date and count eligible series.

    .venv/Scripts/python -I scripts/eligibility.py onion tomato ...

Series = (commodity, market). Variety and grade are attributes, because variety labels were renamed in
2024–26 (e.g. Onion "Red" → "Bellary") and only ~4% of market-days carry more than one variety (Phase 1 profile).
One value per market-day: rows with invalid prices are dropped; if several rows remain, the market's most
frequent (variety, grade) over the previous 365 days is used, otherwise the median modal price.

Reads data/interim/<commodity>.parquet (written by profile_sample.py). Writes reports/profile/eligibility.json,
including rows per arrival date for the last 21 days (publication lag).
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
OUT = ROOT / "reports" / "profile" / "eligibility.json"
ORIGIN = date(2026, 11, 25)
MARKET = ["State", "District", "Market"]
RULES = {"max_days_since_last": 14, "min_report_days_in_last_730": 250, "max_median_gap_last_365": 3,
         "max_flagged_share_last_365": 0.05}


def row_flags(df: pl.DataFrame) -> pl.DataFrame:
    both = (pl.col("min_price") > 0) & (pl.col("max_price") > 0)
    invalid = (pl.col("modal_price").is_null() | (pl.col("modal_price") <= 0)
               | (both & ((pl.col("min_price") > pl.col("max_price")) | (pl.col("modal_price") < pl.col("min_price"))
                          | (pl.col("modal_price") > pl.col("max_price")))))
    conflict = pl.col("modal_price").n_unique().over([*MARKET, "Variety", "Grade", "date"]) > 1
    return df.with_columns(invalid.fill_null(True).alias("invalid"), conflict.alias("conflict"))


STATE_ALIASES = {"keralam": "kerala", "uttrakhand": "uttarakhand"}


def canonical_names(df: pl.DataFrame) -> pl.DataFrame:
    """Phase 1 approximation of the Phase 2 alias table.

    Whitespace and case are normalised, two state spellings are merged, and the " APMC" suffix that AGMARKNET
    appended to almost every market name around 1-4 Nov 2025 is removed, so "Sehore" and "Sehore APMC" are one
    mandi. Verified in the profile: 83-89% of active "APMC" names have such a predecessor in the same district,
    and on the ~2 overlap days their modal prices are identical in 96-100% of rows.
    """
    def norm(col: str) -> pl.Expr:
        return pl.col(col).str.strip_chars().str.replace_all(r"\s+", " ").str.to_lowercase()
    return df.with_columns(
        norm("State").replace(STATE_ALIASES).alias("State"),
        norm("District").alias("District"),
        norm("Market").str.replace(r" apmc$", "").alias("Market"),
    )


def market_daily(df: pl.DataFrame) -> pl.DataFrame:
    df = row_flags(canonical_names(df).filter(pl.col("date").is_not_null() & (pl.col("date") <= ORIGIN)))
    flagged_days = df.group_by([*MARKET, "date"]).agg((pl.col("invalid") | pl.col("conflict")).any().alias("row_flag"))
    ok = df.filter(~pl.col("invalid"))
    dominant = (ok.filter(pl.col("date") > ORIGIN - timedelta(days=365))
                .group_by([*MARKET, "Variety", "Grade"]).len()
                .sort("len", descending=True).unique(subset=MARKET, keep="first")
                .select([*MARKET, pl.col("Variety").alias("dom_variety"), pl.col("Grade").alias("dom_grade")]))
    ok = ok.join(dominant, on=MARKET, how="left").with_columns(
        ((pl.col("Variety") == pl.col("dom_variety")) & (pl.col("Grade") == pl.col("dom_grade"))).fill_null(False)
        .alias("is_dom"))
    daily = ok.group_by([*MARKET, "date"]).agg(
        pl.when(pl.col("is_dom").any()).then(pl.col("modal_price").filter(pl.col("is_dom")).median())
        .otherwise(pl.col("modal_price").median()).alias("price"))
    daily = (daily.join(flagged_days, on=[*MARKET, "date"], how="left").sort([*MARKET, "date"])
             .with_columns(
                 (pl.col("price") / pl.col("price").rolling_median(window_size=15, center=True, min_samples=5)
                  .over(MARKET)).alias("ratio"),
                 (pl.col("date") - pl.col("date").shift(1).over(MARKET)).dt.total_days().alias("gap")))
    return daily.with_columns(
        (pl.col("row_flag").fill_null(False) | (pl.col("ratio") > 3) | (pl.col("ratio") < 1 / 3)).fill_null(False)
        .alias("flagged"))


def main() -> None:
    names = sys.argv[1:] or sorted(p.stem for p in INTERIM.glob("*.parquet"))
    out = {"origin": str(ORIGIN), "series_unit": "commodity x market", "rules": RULES, "commodities": {}}
    for c in names:
        df = pl.read_parquet(INTERIM / f"{c}.parquet")
        daily = market_daily(df)
        last365 = pl.col("date") > ORIGIN - timedelta(days=365)
        s = daily.group_by(MARKET).agg(
            pl.col("date").max().alias("last"),
            (pl.col("date") > ORIGIN - timedelta(days=730)).sum().alias("days_730"),
            pl.col("gap").filter(last365).median().alias("median_gap_365"),
            pl.col("flagged").filter(last365).mean().alias("flagged_share_365"))
        r1 = pl.col("last") >= ORIGIN - timedelta(days=RULES["max_days_since_last"])
        r2 = pl.col("days_730") >= RULES["min_report_days_in_last_730"]
        r3 = (pl.col("median_gap_365") <= RULES["max_median_gap_last_365"]).fill_null(False)
        r4 = (pl.col("flagged_share_365") <= RULES["max_flagged_share_last_365"]).fill_null(False)
        s = s.with_columns(r1.alias("r1"), r2.alias("r2"), r3.alias("r3"), r4.alias("r4"))
        elig = s.filter(pl.col("r1") & pl.col("r2") & pl.col("r3") & pl.col("r4"))
        recent = df.filter(pl.col("date") > ORIGIN - timedelta(days=21)).group_by("date").len().sort("date")
        hist = daily.join(elig.select(MARKET), on=MARKET, how="semi")
        years = (hist.group_by(MARKET).agg(pl.col("date").min().alias("first"))
                 .select(((pl.lit(ORIGIN) - pl.col("first")).dt.total_days() / 365.25).alias("y"))["y"])
        out["commodities"][c] = {
            "markets_total": s.height,
            "pass_recently_active": int(s["r1"].sum()),
            "pass_active_and_history": int((s["r1"] & s["r2"]).sum()),
            "pass_active_history_gap": int((s["r1"] & s["r2"] & s["r3"]).sum()),
            "eligible_markets": elig.height,
            "eligible_states": elig["State"].n_unique(),
            "eligible_market_days_all_history": hist.height,
            "eligible_flagged_share_median": float(elig["flagged_share_365"].median()) if elig.height else None,
            "eligible_history_years_p10_p50_p90": [round(float(years.quantile(q)), 1) for q in (0.1, 0.5, 0.9)]
            if elig.height else None,
            "eligible_with_5y_history": int((years >= 5).sum()),
            "rows_per_arrival_date_last_21d": {str(d): n for d, n in recent.iter_rows()},
        }
        print(c, {k: v for k, v in out["commodities"][c].items() if k != "rows_per_arrival_date_last_21d"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
