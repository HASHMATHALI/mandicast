"""Build the cleaned layer (`price_observations`, `price_series`, dimensions, aliases) from active source rows.

Rules (docs/05_data_dictionary.md §3, docs/06_forecasting_methodology.md §1–2):
- Every active source row with a parseable date becomes one cleaned row. Values are parsed explicitly; nothing is
  filled in. Problems become flags. A zero min or max price next to a positive modal means "not reported": the
  bound is stored as NULL with flag `bounds_not_reported`.
- Series = (commodity, canonical mandi). Variety and grade are attributes.
- One primary row per series and day, chosen without looking ahead: among valid rows, the (variety, grade)
  reported most often for this mandi in the previous 365 days; ties → the row with the lower-median modal
  price, then the lowest source id.
- `local_outlier` compares the primary modal price with the median of the previous 15 primary prices of the
  series (trailing only, so the flag can be used in walk-forward training without leakage).
- Rebuilds are deterministic and replace the rows of a commodity from a start date on, in one transaction.
"""

from __future__ import annotations

import io
import logging
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.clean import names

log = logging.getLogger(__name__)

EXCLUDING_FLAGS = ("non_numeric_price", "zero_or_negative_price", "min_gt_max", "modal_outside_range",
                   "future_date", "duplicate_after_normalisation")
QUALITY_FLAGS_FOR_ELIGIBILITY = ("local_outlier", "day_has_invalid_rows", "day_has_conflicting_reports")
CONTEXT_DAYS = 400  # history read before `since`: 365-day dominant variety + 15-observation outlier window
RULES = {"max_days_since_last": 14, "min_report_days_in_last_730": 250, "max_median_gap_last_365": 3,
         "max_flagged_share_last_365": 0.05}


@dataclass
class BuildResult:
    commodity: str
    since: date | None
    rows_read: int
    rows_written: int
    rows_invalid_date: int
    primary_rows_written: int
    flag_counts: dict
    alias_rules: dict
    series_total: int
    eligible_series: int
    origin: date | None

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, date) else v) for k, v in self.__dict__.items()}


def _copy_out(session: Session, query: sql.Composable) -> bytes:
    conn = session.connection().connection.driver_connection
    buf = io.BytesIO()
    stmt = sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT csv, HEADER true)").format(query)
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        for chunk in cp:
            buf.write(chunk)
    return buf.getvalue()


def read_source(session: Session, commodity: str, from_date: date | None) -> pl.DataFrame:
    q = sql.SQL("SELECT id, arrival_date, state_raw, district_raw, market_raw, commodity_code_raw, variety_raw, "
                "grade_raw, min_price_raw, max_price_raw, modal_price_raw FROM source_observations "
                "WHERE commodity_raw = {} AND superseded_by_raw_page_id IS NULL").format(sql.Literal(commodity))
    if from_date is not None:
        q = q + sql.SQL(" AND arrival_date >= {}").format(sql.Literal(from_date))
    raw = _copy_out(session, q)
    df = pl.read_csv(raw, infer_schema=False) if raw.count(b"\n") > 1 else pl.DataFrame(
        schema={c: pl.String for c in ("id", "arrival_date", "state_raw", "district_raw", "market_raw",
                                       "commodity_code_raw", "variety_raw", "grade_raw", "min_price_raw",
                                       "max_price_raw", "modal_price_raw")})
    return df.with_columns(pl.col(pl.String).fill_null("")).with_columns(
        pl.col("id").cast(pl.Int64), pl.col("arrival_date").str.to_date("%Y-%m-%d", strict=False).alias("date"))


def add_flags(df: pl.DataFrame, today: date) -> pl.DataFrame:
    def num(c: str) -> pl.Expr:
        return pl.col(c).str.strip_chars().cast(pl.Float64, strict=False)

    df = df.with_columns(num("min_price_raw").alias("min_p"), num("max_price_raw").alias("max_p"),
                         num("modal_price_raw").alias("modal_p"),
                         pl.col("variety_raw").str.strip_chars().alias("variety"),
                         pl.col("grade_raw").str.strip_chars().alias("grade"))
    nonnum = pl.any_horizontal([(pl.col(r).str.strip_chars() != "") & pl.col(p).is_null()
                                for r, p in (("min_price_raw", "min_p"), ("max_price_raw", "max_p"),
                                             ("modal_price_raw", "modal_p"))])
    both = (pl.col("min_p") > 0) & (pl.col("max_p") > 0)
    df = df.with_columns(
        nonnum.alias("f_non_numeric_price"),
        (pl.col("modal_p").is_not_null() & (pl.col("modal_p") <= 0)).alias("f_zero_or_negative_price"),
        ((pl.col("modal_p") > 0) & ((pl.col("min_p") == 0) | (pl.col("max_p") == 0))).fill_null(False)
        .alias("f_bounds_not_reported"),
        (both & (pl.col("min_p") > pl.col("max_p"))).fill_null(False).alias("f_min_gt_max"),
        (both & ((pl.col("modal_p") < pl.col("min_p")) | (pl.col("modal_p") > pl.col("max_p")))).fill_null(False)
        .alias("f_modal_outside_range"),
        (pl.col("date") > today).alias("f_future_date"),
    )
    # Bounds reported as 0 are not prices: store NULL (flagged above), never a substitute value.
    df = df.with_columns(pl.when(pl.col("min_p") > 0).then(pl.col("min_p")).otherwise(None).alias("min_p"),
                         pl.when(pl.col("max_p") > 0).then(pl.col("max_p")).otherwise(None).alias("max_p"))
    valid = ~(pl.col("f_non_numeric_price") | pl.col("f_zero_or_negative_price") | pl.col("f_min_gt_max")
              | pl.col("f_modal_outside_range") | pl.col("f_future_date") | pl.col("modal_p").is_null())
    df = df.with_columns(valid.alias("_valid"))
    key = ["series_key", "date", "variety", "grade"]
    df = df.with_columns(
        (pl.col("_valid") & (pl.col("id").rank("ordinal").over([*key, "modal_p", "min_p", "max_p"]) > 1))
        .alias("f_duplicate_after_normalisation"))
    cand = pl.col("_valid") & ~pl.col("f_duplicate_after_normalisation")
    df = df.with_columns(
        (cand & (pl.col("modal_p").filter(cand).n_unique().over(key) > 1)).fill_null(False).alias("f_conflict"),
        (~pl.col("_valid")).any().over(["series_key", "date"]).alias("_day_invalid"),
        cand.alias("_candidate"))
    return df


def choose_primary(df: pl.DataFrame) -> pl.DataFrame:
    """Mark one candidate row per (series, day) as primary, using only data before that day."""
    cand = df.filter(pl.col("_candidate")).select(["id", "series_key", "date", "variety", "grade", "modal_p"])
    cand = cand.with_columns((pl.col("variety") + "\x1f" + pl.col("grade")).alias("vg"))
    n_per_day = cand.group_by(["series_key", "date"]).len()
    multi = cand.join(n_per_day.filter(pl.col("len") > 1).select(["series_key", "date"]), on=["series_key", "date"])
    single_ids = cand.join(n_per_day.filter(pl.col("len") == 1).select(["series_key", "date"]),
                           on=["series_key", "date"]).select("id")
    if multi.height:
        hist = (cand.select(["series_key", "vg", "date"]).unique().sort(["series_key", "vg", "date"])
                .with_columns(pl.int_range(1, pl.len() + 1).over(["series_key", "vg"]).alias("cum")))
        m = multi.with_columns((pl.col("date") - timedelta(days=1)).alias("d_prev"),
                               (pl.col("date") - timedelta(days=366)).alias("d_old"))
        # Both sides are sorted by the as-of date globally, hence also within each (series, vg) group.
        m = (m.sort("d_prev").join_asof(hist.rename({"date": "d_prev", "cum": "cum_prev"}).sort("d_prev"),
                                         on="d_prev", by=["series_key", "vg"], strategy="backward",
                                         check_sortedness=False)
             .sort("d_old").join_asof(hist.rename({"date": "d_old", "cum": "cum_old"}).sort("d_old"),
                                       on="d_old", by=["series_key", "vg"], strategy="backward",
                                       check_sortedness=False)
             .with_columns((pl.col("cum_prev").fill_null(0) - pl.col("cum_old").fill_null(0)).alias("trailing")))
        m = m.with_columns(pl.col("trailing").max().over(["series_key", "date"]).alias("_best"))
        top = m.filter(pl.col("trailing") == pl.col("_best"))
        top = top.with_columns(pl.col("modal_p").rank("ordinal").over(["series_key", "date"]).alias("_r"),
                               pl.len().over(["series_key", "date"]).alias("_n"))
        lower_median = top.filter(pl.col("_r") == (pl.col("_n") + 1) // 2)
        chosen = (lower_median.sort("id").unique(subset=["series_key", "date"], keep="first").select("id"))
        primary_ids = pl.concat([single_ids, chosen])
    else:
        primary_ids = single_ids
    return df.with_columns(pl.col("id").is_in(primary_ids["id"].implode()).alias("is_primary"))


def flag_outliers(df: pl.DataFrame) -> pl.DataFrame:
    p = (df.filter(pl.col("is_primary")).select(["id", "series_key", "date", "modal_p"]).sort(["series_key", "date"])
         .with_columns(pl.col("modal_p").shift(1).rolling_median(window_size=15, min_samples=5)
                       .over("series_key").alias("_med")))
    out = p.filter((pl.col("modal_p") > 3 * pl.col("_med")) | (pl.col("modal_p") < pl.col("_med") / 3)).select("id")
    return df.with_columns((pl.col("is_primary") & pl.col("id").is_in(out["id"].implode())).alias("f_local_outlier"),
                           (pl.col("is_primary") & pl.col("_day_invalid")).alias("f_day_has_invalid_rows"),
                           (pl.col("is_primary") & pl.col("f_conflict").any().over(["series_key", "date"]))
                           .alias("f_day_has_conflicting_reports"))


FLAG_COLUMNS = ("non_numeric_price", "zero_or_negative_price", "bounds_not_reported", "min_gt_max",
                "modal_outside_range", "future_date", "duplicate_after_normalisation", "conflict", "local_outlier",
                "day_has_invalid_rows", "day_has_conflicting_reports")


def _upsert_dimensions(session: Session, commodity: str, code: str | None, aliases: pl.DataFrame) -> int:
    """Insert new states/districts/mandis and aliases; refresh display names only with newer spellings."""
    disp = names.display_names(aliases)
    cid = session.execute(text(
        "INSERT INTO commodities (name, agmarknet_code) VALUES (:n, :c) ON CONFLICT (name) DO UPDATE "
        "SET agmarknet_code = COALESCE(commodities.agmarknet_code, EXCLUDED.agmarknet_code) RETURNING id"),
        {"n": commodity, "c": code}).scalar_one()
    # A display name is replaced only by a spelling seen at least as recently, so a rebuild over a short window
    # (daily runs) never overwrites a newer name with an older one.
    for r in disp.filter(pl.col("entity") == "state").iter_rows(named=True):
        session.execute(text(
            "INSERT INTO states (name, display_name, display_seen) VALUES (:k, :dn, :ds) "
            "ON CONFLICT (name) DO UPDATE "
            "SET display_name = EXCLUDED.display_name, display_seen = EXCLUDED.display_seen "
            "WHERE states.display_seen IS NULL OR EXCLUDED.display_seen >= states.display_seen"),
            {"k": r["canonical_key"], "dn": r["display_name"], "ds": r["display_seen"]})
    for r in disp.filter(pl.col("entity") == "district").iter_rows(named=True):
        session.execute(text(
            "INSERT INTO districts (state_id, name, display_name, display_seen) "
            "SELECT id, :k, :dn, :ds FROM states WHERE name = :s "
            "ON CONFLICT (state_id, name) DO UPDATE "
            "SET display_name = EXCLUDED.display_name, display_seen = EXCLUDED.display_seen "
            "WHERE districts.display_seen IS NULL OR EXCLUDED.display_seen >= districts.display_seen"),
            {"s": r["parent_key"], "k": r["canonical_key"], "dn": r["display_name"], "ds": r["display_seen"]})
    for r in disp.filter(pl.col("entity") == "market").iter_rows(named=True):
        s_key, d_key = r["parent_key"].split("|", 1)
        session.execute(text(
            "INSERT INTO markets (district_id, name, display_name, display_seen) "
            "SELECT d.id, :k, :dn, :ds FROM districts d JOIN states s ON s.id = d.state_id "
            "WHERE s.name = :s AND d.name = :d "
            "ON CONFLICT (district_id, name) DO UPDATE "
            "SET display_name = EXCLUDED.display_name, display_seen = EXCLUDED.display_seen "
            "WHERE markets.display_seen IS NULL OR EXCLUDED.display_seen >= markets.display_seen"),
            {"s": s_key, "d": d_key, "k": r["canonical_key"], "dn": r["display_name"], "ds": r["display_seen"]})
    for r in aliases.iter_rows(named=True):
        session.execute(text(
            "INSERT INTO name_aliases (entity, parent_key, raw_name, canonical_name, rule, last_seen) "
            "VALUES (:e, :p, :raw, :c, :rule, :ls) ON CONFLICT (entity, parent_key, raw_name) DO UPDATE SET "
            "canonical_name = EXCLUDED.canonical_name, rule = EXCLUDED.rule, "
            "last_seen = GREATEST(name_aliases.last_seen, EXCLUDED.last_seen)"),
            {"e": r["entity"], "p": r["parent_key"], "raw": r["raw_name"], "c": r["canonical_key"], "rule": r["rule"],
             "ls": r["last_seen"]})
    return cid


def _series_ids(session: Session, commodity_id: int, keys: pl.DataFrame) -> pl.DataFrame:
    for r in keys.iter_rows(named=True):
        session.execute(text(
            "INSERT INTO price_series (commodity_id, market_id) SELECT :c, m.id FROM markets m "
            "JOIN districts d ON d.id = m.district_id JOIN states s ON s.id = d.state_id "
            "WHERE s.name = :s AND d.name = :d AND m.name = :m ON CONFLICT (commodity_id, market_id) DO NOTHING"),
            {"c": commodity_id, "s": r["state_key"], "d": r["district_key"], "m": r["market_key"]})
    rows = session.execute(text(
        "SELECT ps.id, s.name, d.name, m.name FROM price_series ps JOIN markets m ON m.id = ps.market_id "
        "JOIN districts d ON d.id = m.district_id JOIN states s ON s.id = d.state_id WHERE ps.commodity_id = :c"),
        {"c": commodity_id}).all()
    return pl.DataFrame([tuple(r) for r in rows], schema={"series_id": pl.Int64, "state_key": pl.String,
                                                          "district_key": pl.String, "market_key": pl.String},
                        orient="row")


def _write(session: Session, commodity_id: int, since: date | None, out: pl.DataFrame) -> None:
    session.execute(text(
        "DELETE FROM price_observations po USING price_series ps WHERE po.series_id = ps.id AND ps.commodity_id = :c "
        "AND (CAST(:since AS date) IS NULL OR po.date >= CAST(:since AS date))"), {"c": commodity_id, "since": since})
    # CSV through COPY: flags as a PostgreSQL array literal (flag names contain no commas or quotes);
    # NULL is written as \N so that empty strings stay empty strings.
    buf = io.BytesIO()
    out.with_columns(("{" + pl.col("flags").list.join(",") + "}").alias("flags")).write_csv(
        buf, include_header=False, null_value=r"\N")
    conn = session.connection().connection.driver_connection
    with conn.cursor() as cur, cur.copy(
            "COPY price_observations (series_id, date, variety, grade, min_price, max_price, modal_price, "
            r"source_observation_id, flags, is_primary) FROM STDIN WITH (FORMAT csv, NULL '\N')") as cp:
        cp.write(buf.getvalue())


def latest_complete_date(session: Session, commodity: str, today: date) -> date | None:
    """Latest date with ≥ 85% of the median row count of the same weekday over the previous four weeks."""
    rows = dict(session.execute(text(
        "SELECT arrival_date, count(*) FROM source_observations WHERE commodity_raw = :c "
        "AND superseded_by_raw_page_id IS NULL AND arrival_date BETWEEN :a AND :b GROUP BY 1"),
        {"c": commodity, "a": today - timedelta(days=120), "b": today}).all())
    for d in sorted(rows, reverse=True):
        base = statistics.median(rows.get(d - timedelta(days=7 * k), 0) for k in range(1, 5))
        if base > 0 and rows[d] >= 0.85 * base:
            return d
    return max(rows) if rows else None


def update_series(session: Session, commodity_id: int, origin: date | None) -> tuple[int, int]:
    session.execute(text(
        "UPDATE price_series ps SET first_date = s.f, last_date = s.l, n_days = s.n, updated_at = now() FROM ("
        " SELECT po.series_id, min(po.date) f, max(po.date) l, count(*) n FROM price_observations po"
        " JOIN price_series p ON p.id = po.series_id WHERE p.commodity_id = :c AND po.is_primary"
        " GROUP BY po.series_id) s WHERE ps.id = s.series_id"), {"c": commodity_id})
    session.execute(text(
        "UPDATE price_series ps SET dominant_variety = split_part(s.vg, chr(31), 1), "
        "dominant_grade = split_part(s.vg, chr(31), 2) FROM ("
        " SELECT po.series_id, mode() WITHIN GROUP (ORDER BY po.variety || chr(31) || po.grade) vg"
        " FROM price_observations po JOIN price_series p ON p.id = po.series_id"
        " WHERE p.commodity_id = :c AND po.is_primary AND po.date > p.last_date - 365 GROUP BY po.series_id) s"
        " WHERE ps.id = s.series_id"), {"c": commodity_id})
    if origin is None:
        return 0, 0
    session.execute(text(
        "UPDATE price_series ps SET eligible_for_forecast = (e.reason IS NULL), eligibility_reason = e.reason, "
        "eligibility_origin = :o FROM ("
        " WITH p AS (SELECT po.series_id, po.date, po.flags,"
        "   po.date - lag(po.date) OVER (PARTITION BY po.series_id ORDER BY po.date) AS gap"
        "   FROM price_observations po JOIN price_series s ON s.id = po.series_id"
        "   WHERE s.commodity_id = :c AND po.is_primary AND po.date <= :o AND po.date > CAST(:o AS date) - 800),"
        " agg AS (SELECT ps2.id AS series_id, max(p.date) AS last,"
        "   count(p.date) FILTER (WHERE p.date > CAST(:o AS date) - 730) AS d730,"
        "   percentile_cont(0.5) WITHIN GROUP (ORDER BY p.gap) FILTER (WHERE p.date > CAST(:o AS date) - 365) AS mg,"
        "   avg((p.flags && CAST(:qf AS text[]))::int) FILTER (WHERE p.date > CAST(:o AS date) - 365) AS fs"
        "   FROM price_series ps2 LEFT JOIN p ON p.series_id = ps2.id WHERE ps2.commodity_id = :c GROUP BY ps2.id)"
        " SELECT series_id, CASE"
        "   WHEN last IS NULL OR last < CAST(:o AS date) - :r1 THEN 'not reported in the last 14 days'"
        "   WHEN d730 < :r2 THEN 'fewer than 250 report days in the last 730'"
        "   WHEN mg IS NULL OR mg > :r3 THEN 'median gap between reports over 3 days'"
        "   WHEN fs > :r4 THEN 'more than 5% of the last 365 days flagged'"
        "   ELSE NULL END AS reason FROM agg) e WHERE ps.id = e.series_id"),
        {"c": commodity_id, "o": origin, "qf": list(QUALITY_FLAGS_FOR_ELIGIBILITY),
         "r1": RULES["max_days_since_last"], "r2": RULES["min_report_days_in_last_730"],
         "r3": RULES["max_median_gap_last_365"], "r4": RULES["max_flagged_share_last_365"]})
    total, eligible = session.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE eligible_for_forecast) FROM price_series WHERE commodity_id = :c"),
        {"c": commodity_id}).one()
    return total, eligible


def rebuild_commodity(session: Session, commodity: str, manual_aliases: list[dict], since: date | None = None,
                      today: date | None = None) -> BuildResult:
    today = today or date.today()
    src = read_source(session, commodity, since - timedelta(days=CONTEXT_DAYS) if since else None)
    n_read = src.height
    invalid_date = int(src["date"].is_null().sum())
    df = src.filter(pl.col("date").is_not_null())
    df = names.canonicalise(df, manual_aliases)
    df = df.with_columns(pl.concat_str(["state_key", "district_key", "market_key"], separator="|").alias("series_key"))
    aliases = names.alias_table(df)
    codes = df["commodity_code_raw"].unique().to_list()
    commodity_id = _upsert_dimensions(session, commodity, ",".join(sorted(codes)) if codes else None, aliases)
    series = _series_ids(session, commodity_id, df.select(["state_key", "district_key", "market_key"]).unique())
    df = add_flags(df, today)
    df = choose_primary(df)
    df = flag_outliers(df)
    df = df.join(series, on=["state_key", "district_key", "market_key"], how="left")
    if since:
        df = df.filter(pl.col("date") >= since)
    flags = pl.concat_list([pl.when(pl.col(f"f_{f}")).then(pl.lit(f)).otherwise(None) for f in FLAG_COLUMNS]
                           ).list.drop_nulls()
    out = df.select("series_id", "date", "variety", "grade", "min_p", "max_p", "modal_p", "id",
                    flags.alias("flags"), "is_primary")
    _write(session, commodity_id, since, out)
    origin = latest_complete_date(session, commodity, today)
    total, eligible = update_series(session, commodity_id, origin)
    flag_counts = {f: int(df[f"f_{f}"].sum()) for f in FLAG_COLUMNS}
    alias_rules = dict(aliases.group_by(["entity", "rule"]).len().select(
        pl.concat_str(["entity", "rule"], separator=":"), "len").iter_rows())
    res = BuildResult(commodity, since, n_read, out.height, invalid_date, int(df["is_primary"].sum()), flag_counts,
                      alias_rules, total, eligible, origin)
    log.info("cleaned %s: %s", commodity, res.as_dict())
    return res
