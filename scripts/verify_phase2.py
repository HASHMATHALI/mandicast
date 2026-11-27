"""Verify the loaded database against the raw files and the Phase 2 invariants.

    .venv/Scripts/python -I scripts/verify_phase2.py

Writes reports/phase2_verification.json and prints a table. Every check is computed here from the database and
the raw manifests; nothing is copied from earlier reports.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys

from sqlalchemy import text

from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.db.session import get_engine
from mandicast.ingest.raw_store import RawStore

OUT = PROJECT_ROOT / "reports" / "phase2_verification.json"


def distinct_raw_rows(store: RawStore, commodity: str) -> tuple[int, int, int]:
    """(rows in pages, distinct rows over pages + date snapshots, latest API total in the page manifest)."""
    seen: set[tuple] = set()
    page_rows = 0
    for e in store.entries(commodity):
        rows = list(csv.reader(io.StringIO(gzip.decompress((store.root / e.rel_path).read_bytes()).decode("utf-8"))))[1:]
        if e.kind == "page":
            page_rows += len(rows)
        seen.update(tuple(r) for r in rows)
    total = max(r["total_at_request"] for r in store.page_manifest(commodity).values())
    return page_rows, len(seen), total


def main() -> None:
    settings = get_settings()
    store = RawStore(settings.raw_dir)
    eng = get_engine()
    out: dict = {"commodities": {}, "global": {}}
    with eng.connect() as c:
        for com in settings.commodities:
            page_rows, distinct, total = distinct_raw_rows(store, com)
            def q(sql: str, com: str = com) -> object:
                return c.execute(text(sql), {"c": com}).scalar_one()
            src = q("SELECT count(*) FROM source_observations WHERE commodity_raw = :c")
            active = q("SELECT count(*) FROM source_observations WHERE commodity_raw = :c "
                       "AND superseded_by_raw_page_id IS NULL AND arrival_date IS NOT NULL")
            files_db = q("SELECT count(*) FROM raw_pages WHERE commodity = :c")
            files_raw = len(store.entries(com))
            clean = q("SELECT count(*) FROM price_observations po JOIN price_series ps ON ps.id = po.series_id "
                      "JOIN commodities k ON k.id = ps.commodity_id WHERE k.name = :c")
            primary = q("SELECT count(*) FROM price_observations po JOIN price_series ps ON ps.id = po.series_id "
                        "JOIN commodities k ON k.id = ps.commodity_id WHERE k.name = :c AND po.is_primary")
            days_with_valid = q(
                "SELECT count(*) FROM (SELECT DISTINCT po.series_id, po.date FROM price_observations po "
                "JOIN price_series ps ON ps.id = po.series_id JOIN commodities k ON k.id = ps.commodity_id "
                "WHERE k.name = :c AND NOT (po.flags && ARRAY['non_numeric_price','zero_or_negative_price',"
                "'min_gt_max','modal_outside_range','future_date','duplicate_after_normalisation']::text[])) d")
            series = q("SELECT count(*) FROM price_series ps JOIN commodities k ON k.id = ps.commodity_id "
                       "WHERE k.name = :c")
            eligible = q("SELECT count(*) FROM price_series ps JOIN commodities k ON k.id = ps.commodity_id "
                         "WHERE k.name = :c AND ps.eligible_for_forecast")
            origin = q("SELECT max(eligibility_origin) FROM price_series ps JOIN commodities k "
                       "ON k.id = ps.commodity_id WHERE k.name = :c")
            out["commodities"][com] = {
                "api_total": total, "raw_page_rows": page_rows, "raw_distinct_rows": distinct,
                "source_rows_in_db": src, "check_source_equals_distinct_raw": src == distinct,
                "raw_files": files_raw, "raw_files_loaded": files_db, "check_all_files_loaded": files_raw == files_db,
                "active_source_rows": active, "cleaned_rows": clean, "check_cleaned_equals_active": clean == active,
                "primary_rows": primary, "days_with_a_valid_row": days_with_valid,
                "check_one_primary_per_valid_day": primary == days_with_valid,
                "series": series, "eligible_series": eligible, "origin": str(origin),
            }
        g = lambda sql: c.execute(text(sql)).scalar_one()  # noqa: E731
        out["global"] = {
            "source_rows": g("SELECT count(*) FROM source_observations"),
            "superseded_rows": g("SELECT count(*) FROM source_observations WHERE superseded_by_raw_page_id IS NOT NULL"),
            "cleaned_rows": g("SELECT count(*) FROM price_observations"),
            "orphan_cleaned_rows": g("SELECT count(*) FROM price_observations po LEFT JOIN source_observations s "
                                     "ON s.id = po.source_observation_id WHERE s.id IS NULL"),
            "cleaned_rows_from_superseded_sources": g(
                "SELECT count(*) FROM price_observations po JOIN source_observations s ON s.id = po.source_observation_id "
                "WHERE s.superseded_by_raw_page_id IS NOT NULL"),
            "duplicate_primary_days": g("SELECT count(*) FROM (SELECT series_id, date FROM price_observations "
                                        "WHERE is_primary GROUP BY 1, 2 HAVING count(*) > 1) d"),
            "alias_rules": dict(c.execute(text("SELECT entity || ':' || rule, count(*) FROM name_aliases GROUP BY 1 "
                                               "ORDER BY 1")).all()),
            "jobs": [dict(r._mapping) for r in c.execute(text(
                "SELECT id, kind, status, rows_inserted, rows_duplicate, rows_rejected FROM ingestion_jobs ORDER BY id"))],
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for com, v in out["commodities"].items():
        checks = {k: v[k] for k in v if k.startswith("check_")}
        print(f"{com:9s} src {v['source_rows_in_db']:>9,} (raw distinct {v['raw_distinct_rows']:,}, API {v['api_total']:,}) "
              f"cleaned {v['cleaned_rows']:,} primary {v['primary_rows']:,} series {v['series']:,} "
              f"eligible {v['eligible_series']} origin {v['origin']} | " + ("ALL CHECKS OK" if all(checks.values())
                                                                           else f"FAILED {checks}"))
    gl = out["global"]
    print({k: gl[k] for k in ("source_rows", "superseded_rows", "cleaned_rows", "orphan_cleaned_rows",
                              "cleaned_rows_from_superseded_sources", "duplicate_primary_days")})
    print(gl["alias_rules"])


if __name__ == "__main__":
    main()
