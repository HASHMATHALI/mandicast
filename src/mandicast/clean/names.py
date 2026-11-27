"""Canonical names for states, districts and mandis, with the rule that maps each source spelling.

Canonical keys: whitespace collapsed and case-folded; Keralam → Kerala and Uttrakhand → Uttarakhand; for mandis
the " APMC" suffix that AGMARKNET appended to almost all market names on 1–4 Nov 2025 is removed ("X APMC" and
"X" in the same district are one mandi: identical overlap prices in 96–100% of rows, Phase 1); plus reviewed
mappings from config/manual_aliases.csv. There is no automatic fuzzy matching: names that match no rule stay
separate entities.

Rule label of a spelling (depends only on the spelling, so any rebuild window gives the same labels):
`manual` > `state_rename` > `apmc_suffix` > `whitespace` (extra, leading or trailing spaces) > `exact`.
The display name of a canonical entity is its most recently seen source spelling.
"""

from __future__ import annotations

import csv
from pathlib import Path

import polars as pl

STATE_RENAMES = {"keralam": "kerala", "uttrakhand": "uttarakhand"}
APMC_SUFFIX = r"\s+apmc$"
MANUAL_COLUMNS = ["entity", "state", "district", "raw_name", "canonical_name", "note"]


def norm(e: str | pl.Expr) -> pl.Expr:
    e = pl.col(e) if isinstance(e, str) else e
    return e.str.strip_chars().str.replace_all(r"\s+", " ").str.to_lowercase()


def norm_py(s: str) -> str:
    return " ".join(s.split()).lower()


def load_manual(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("entity") and not r["entity"].startswith("#")]
    for r in rows:
        if r["entity"] not in ("state", "district", "market") or not r.get("raw_name") or not r.get("canonical_name"):
            raise ValueError(f"invalid manual alias row: {r}")
    return rows


def canonicalise(df: pl.DataFrame, manual: list[dict]) -> pl.DataFrame:
    """Add state_key, district_key, market_key (and <level>_manual flags). Needs state_raw, district_raw, market_raw."""
    df = df.with_columns(norm("state_raw").alias("_s"), norm("district_raw").alias("_d"),
                         norm("market_raw").alias("_m"))
    df = df.with_columns(pl.col("_s").replace(STATE_RENAMES).alias("state_key"), pl.col("_d").alias("district_key"),
                         pl.col("_m").str.replace(APMC_SUFFIX, "").alias("market_key"),
                         pl.lit(False).alias("state_manual"), pl.lit(False).alias("district_manual"),
                         pl.lit(False).alias("market_manual"))
    for level, raw_norm, parents in (("state", "_s", []), ("district", "_d", ["state_key"]),
                                     ("market", "_m", ["state_key", "district_key"])):
        rows = [r for r in manual if r["entity"] == level]
        if not rows:
            continue
        m = pl.DataFrame({
            "state_key": [norm_py(r.get("state", "") or "") for r in rows],
            "district_key": [norm_py(r.get("district", "") or "") for r in rows],
            raw_norm: [norm_py(r["raw_name"]) for r in rows],
            "_canon": [norm_py(r["canonical_name"]) for r in rows],
        }).select([*parents, raw_norm, "_canon"]).unique(subset=[*parents, raw_norm])
        df = (df.join(m, on=[*parents, raw_norm], how="left")
              .with_columns(pl.coalesce("_canon", f"{level}_key").alias(f"{level}_key"),
                            pl.col("_canon").is_not_null().alias(f"{level}_manual"))
              .drop("_canon"))
    return df.drop(["_s", "_d", "_m"])


def alias_table(df: pl.DataFrame) -> pl.DataFrame:
    """Every (level, parent, raw spelling) with its canonical key, rule and last date seen.

    The rule depends only on the spelling itself, so rebuilding from any date window gives the same labels:
    manual > state_rename > apmc_suffix > whitespace (extra/leading/trailing spaces) > exact. Spellings that differ
    only in letter case map to the same key with rule `exact`."""
    parts = []
    for level, parent in (("state", pl.lit("")), ("district", pl.col("state_key")),
                          ("market", pl.col("state_key") + "|" + pl.col("district_key"))):
        parts.append(df.select(pl.lit(level).alias("entity"), parent.alias("parent_key"),
                               pl.col(f"{level}_raw").alias("raw_name"), pl.col(f"{level}_key").alias("canonical_key"),
                               pl.col(f"{level}_manual").alias("manual"), "date")
                     .group_by(["entity", "parent_key", "raw_name", "canonical_key"])
                     .agg(pl.col("date").max().alias("last_seen"), pl.col("manual").any()))
    a = pl.concat(parts, how="vertical")
    collapsed = pl.col("raw_name").str.strip_chars().str.replace_all(r"\s+", " ")
    return a.with_columns(
        pl.when(pl.col("manual")).then(pl.lit("manual"))
        .when((pl.col("entity") == "state") & norm("raw_name").is_in(list(STATE_RENAMES))).then(pl.lit("state_rename"))
        .when((pl.col("entity") == "market") & norm("raw_name").str.contains(APMC_SUFFIX)).then(pl.lit("apmc_suffix"))
        .when(pl.col("raw_name") != collapsed).then(pl.lit("whitespace"))
        .otherwise(pl.lit("exact")).alias("rule")).drop("manual")


def display_names(aliases: pl.DataFrame) -> pl.DataFrame:
    """One display name per (entity, parent_key, canonical_key): the most recently seen source spelling, with the
    date it was last seen (used so that a rebuild over a short window never replaces a newer display name)."""
    return (aliases.with_columns(pl.col("raw_name").str.strip_chars().str.replace_all(r"\s+", " ").alias("clean"))
            .sort(["last_seen", "clean"], descending=[True, False], nulls_last=True)
            .unique(subset=["entity", "parent_key", "canonical_key"], keep="first", maintain_order=True)
            .select(["entity", "parent_key", "canonical_key", pl.col("clean").alias("display_name"),
                     pl.col("last_seen").alias("display_seen")]))
