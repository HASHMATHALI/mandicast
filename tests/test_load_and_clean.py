"""Loading and cleaning against the PostgreSQL test database."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import FakeClient, real_rows, to_csv_bytes
from sqlalchemy import text

from mandicast.clean.build import rebuild_commodity
from mandicast.db.session import session_scope
from mandicast.ingest import fetch
from mandicast.ingest.load import load_commodity
from mandicast.ingest.raw_store import RawStore, SchemaChangeError

pytestmark = pytest.mark.db
C = "Soyabean"


def _backfill(store: RawStore, rows, page_size=30) -> None:
    fetch.backfill_pages(FakeClient(rows), store, C, page_size=page_size, pause=0, sleep=lambda s: None)


def _load(engine, store):
    with session_scope(engine) as s:
        return load_commodity(s, store, C)


def _rebuild(engine, today=date(2025, 11, 21)):
    with session_scope(engine) as s:
        return rebuild_commodity(s, C, [], today=today)


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def artificial(d: str, market: str, variety: str, grade: str, lo: str, hi: str, modal: str) -> list[str]:
    """Artificial row (not real data) in a made-up mandi, used only to exercise one cleaning rule."""
    return [d, C, "13", "Test District", grade, market, hi, lo, modal, "Test State", variety]


# ---------------------------------------------------------------- loading
def test_load_is_idempotent(db, tmp_path):
    rows = real_rows()
    store = RawStore(tmp_path)
    _backfill(store, rows)
    first = _load(db, store)
    distinct = len({tuple(r) for r in rows})
    assert first.files_loaded == 4 and first.rows_inserted == distinct
    second = _load(db, store)
    assert second.files_loaded == 0 and second.files_skipped == 4 and second.rows_inserted == 0
    assert _q(db, "SELECT count(*) FROM source_observations")[0][0] == distinct


def test_date_snapshot_recovers_rows_missing_from_pages_without_duplicates(db, tmp_path):
    rows = real_rows()
    d = rows[40][0]
    day = [r for r in rows if r[0] == d]
    missing = day[0]
    store = RawStore(tmp_path)
    _backfill(store, [r for r in rows if r is not missing])
    _load(db, store)
    store.write_date(C, d, to_csv_bytes(day), {"filters[Arrival_Date]": d})
    st = _load(db, store)
    assert st.rows_inserted == 1 and st.rows_duplicate == len(day) - 1 and st.snapshots_applied == 1
    assert _q(db, "SELECT count(*) FROM source_observations")[0][0] == len({tuple(r) for r in rows})


def test_newer_snapshot_supersedes_rows_it_no_longer_contains(db, tmp_path):
    rows = real_rows()
    d = rows[60][0]
    day = [r for r in rows if r[0] == d]
    store = RawStore(tmp_path)
    store.write_date(C, d, to_csv_bytes(day), {})
    _load(db, store)
    corrected = [list(r) for r in day]
    corrected[0][8] = str(int(corrected[0][8]) + 1)  # artificial correction of one modal price
    store.write_date(C, d, to_csv_bytes(corrected), {})
    st = _load(db, store)
    assert st.rows_superseded == 1 and st.rows_inserted == 1
    active = _q(db, "SELECT count(*) FROM source_observations WHERE superseded_by_raw_page_id IS NULL")[0][0]
    assert active == len({tuple(r) for r in corrected})
    _rebuild(db)
    superseded = _q(db, "SELECT id FROM source_observations WHERE superseded_by_raw_page_id IS NOT NULL")
    assert len(superseded) == 1
    in_clean = _q(db, "SELECT count(*) FROM price_observations WHERE source_observation_id = :i", i=superseded[0][0])
    assert in_clean[0][0] == 0  # the superseded value is not in the cleaned layer
    assert _q(db, "SELECT count(*) FROM price_observations")[0][0] == active


def test_shrunken_snapshot_is_stored_but_not_applied(db, tmp_path):
    rows = real_rows()
    d = max({r[0] for r in rows}, key=lambda x: sum(1 for r in rows if r[0] == x))
    day = [r for r in rows if r[0] == d]
    assert len(day) >= 3
    store = RawStore(tmp_path)
    store.write_date(C, d, to_csv_bytes(day), {})
    _load(db, store)
    store.write_date(C, d, to_csv_bytes(day[:1]), {})  # e.g. a partial response during an outage
    st = _load(db, store)
    assert st.snapshots_applied == 0 and len(st.snapshots_not_applied) == 1 and st.rows_superseded == 0
    assert _q(db, "SELECT count(*) FROM source_observations WHERE superseded_by_raw_page_id IS NULL")[0][0] == len(day)


def test_schema_change_rolls_back_the_whole_load(db, tmp_path):
    rows = real_rows()
    store = RawStore(tmp_path)
    store.write_date(C, rows[0][0], to_csv_bytes([r for r in rows if r[0] == rows[0][0]]), {})
    bad = to_csv_bytes(rows[:2], header=[*rows[0][:0], "Date", *["x"] * 10])  # artificial changed header
    d = tmp_path / "soyabean" / "by_date" / "2025-11-01__bad.csv.gz"
    import gzip
    import hashlib
    d.write_bytes(gzip.compress(bad, mtime=0))
    with (tmp_path / "soyabean" / "by_date_manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": "01/11/2025", "rows": 2, "file": "by_date/2025-11-01__bad.csv.gz",
                            "sha256_raw": hashlib.sha256(bad).hexdigest(), "fetched_at": "2026-11-26T00:00:00Z",
                            "params": {}}) + "\n")
    with pytest.raises(SchemaChangeError):
        _load(db, store)
    assert _q(db, "SELECT count(*) FROM source_observations")[0][0] == 0
    assert _q(db, "SELECT count(*) FROM raw_pages")[0][0] == 0


# ---------------------------------------------------------------- cleaning
def test_apmc_rename_merges_series_and_nothing_is_invented(db, tmp_path):
    rows = real_rows()
    store = RawStore(tmp_path)
    _backfill(store, rows)
    _load(db, store)
    res = _rebuild(db)
    series = _q(db, "SELECT m.name FROM price_series ps JOIN markets m ON m.id = ps.market_id ORDER BY 1")
    assert [s[0] for s in series] == ["ashta", "sehore"]  # 4 source names, 2 mandis
    rules = dict(_q(db, "SELECT raw_name, rule FROM name_aliases WHERE entity = 'market'"))
    assert rules["Sehore APMC"] == "apmc_suffix" and rules["Sehore"] == "exact"
    shown = dict(_q(db, "SELECT name, display_name FROM markets"))
    assert shown == {"sehore": "Sehore APMC", "ashta": "Ashta APMC"}  # the most recent source spelling
    distinct = len({tuple(r) for r in rows})
    assert res.rows_written == distinct
    assert _q(db, "SELECT count(*) FROM price_observations")[0][0] == distinct
    # every cleaned (series, date) comes from a source row of that date: no filled-in days
    days_clean = {(r[0], r[1]) for r in _q(db, "SELECT m.name, po.date FROM price_observations po JOIN price_series "
                                                "ps ON ps.id = po.series_id JOIN markets m ON m.id = ps.market_id")}
    days_src = {(r[5].replace(" APMC", "").lower(), date(int(r[0][6:]), int(r[0][3:5]), int(r[0][:2]))) for r in rows}
    assert days_clean == days_src
    primary = _q(db, "SELECT count(*), count(DISTINCT (series_id, date)) FROM price_observations WHERE is_primary")[0]
    assert primary[0] == primary[1] == len(days_src)


def test_rebuild_is_deterministic(db, tmp_path):
    store = RawStore(tmp_path)
    _backfill(store, real_rows())
    _load(db, store)
    q = ("SELECT s.source_row_hash, po.date, po.modal_price, po.min_price, po.max_price, po.flags, po.is_primary "
         "FROM price_observations po JOIN source_observations s ON s.id = po.source_observation_id ORDER BY 1")
    _rebuild(db)
    a = _q(db, q)
    _rebuild(db)
    assert _q(db, q) == a


def test_flags_and_bounds_not_reported(db, tmp_path):
    m = "Artificial Mandi"
    rows = [
        artificial("01/11/2025", m, "Yellow", "FAQ", "0", "0", "4000"),        # only modal reported
        artificial("02/11/2025", m, "Yellow", "FAQ", "4200", "4100", "4150"),  # min > max
        artificial("03/11/2025", m, "Yellow", "FAQ", "4000", "4100", "4500"),  # modal outside range
        artificial("04/11/2025", m, "Yellow", "FAQ", "4000", "4100", "NR"),    # non-numeric modal
        artificial("04/11/2025", m, "Yellow", "FAQ", "4000", "4100", "4050"),
        artificial("05/11/2025", m, "Yellow", "FAQ", "4000", "4300", "4100"),  # conflicting same-day reports
        artificial("05/11/2025", m, "Yellow", "FAQ", "4000", "4300", "4200"),
    ]
    store = RawStore(tmp_path)
    for d in sorted({r[0] for r in rows}):
        store.write_date(C, d, to_csv_bytes([r for r in rows if r[0] == d]), {})
    _load(db, store)
    _rebuild(db)
    got = {(r[0].day, r[1]): (r[2], r[3], r[4], set(r[5])) for r in _q(
        db, "SELECT po.date, s.modal_price_raw, po.min_price, po.max_price, po.is_primary, po.flags "
            "FROM price_observations po JOIN source_observations s ON s.id = po.source_observation_id")}
    assert got[(1, "4000")][0] is None and got[(1, "4000")][1] is None and got[(1, "4000")][2] is True
    assert "bounds_not_reported" in got[(1, "4000")][3]
    assert "min_gt_max" in got[(2, "4150")][3] and got[(2, "4150")][2] is False
    assert "modal_outside_range" in got[(3, "4500")][3] and got[(3, "4500")][2] is False
    assert "non_numeric_price" in got[(4, "NR")][3] and got[(4, "NR")][2] is False
    assert got[(4, "4050")][2] is True and "day_has_invalid_rows" in got[(4, "4050")][3]
    assert "conflict" in got[(5, "4100")][3] and "conflict" in got[(5, "4200")][3]
    primary5 = [v for k, v in got.items() if k[0] == 5 and v[2]]
    assert len(primary5) == 1 and "day_has_conflicting_reports" in primary5[0][3]
    assert primary5[0][0] is not None  # lower-median modal of the two: 4100
    assert _q(db, "SELECT count(*) FROM price_observations WHERE date = '2025-11-02' OR date = '2025-11-03'")[0][0] == 2


def test_primary_choice_uses_only_past_data(db, tmp_path):
    """Variety A reported alone for 10 days, then A and B on the same day, then B alone for 30 days.
    On the shared day the past says A, the future says B: the primary must be A. (Artificial rows.)"""
    m = "Artificial Lookahead Mandi"
    rows = [artificial(f"{d:02d}/09/2025", m, "A", "FAQ", "100", "300", "200") for d in range(1, 11)]
    rows += [artificial("11/09/2025", m, "A", "FAQ", "100", "300", "210"),
             artificial("11/09/2025", m, "B", "FAQ", "100", "300", "190")]
    rows += [artificial(f"{d:02d}/10/2025", m, "B", "FAQ", "100", "300", "195") for d in range(1, 31)]
    store = RawStore(tmp_path)
    for d in sorted({r[0] for r in rows}):
        store.write_date(C, d, to_csv_bytes([r for r in rows if r[0] == d]), {})
    _load(db, store)
    _rebuild(db)
    chosen = _q(db, "SELECT variety FROM price_observations WHERE date = '2025-09-11' AND is_primary")
    assert chosen == [("A",)]


def test_local_outlier_is_flagged_from_past_values(db, tmp_path):
    m = "Artificial Spike Mandi"
    rows = [artificial(f"{d:02d}/08/2025", m, "Y", "FAQ", "900", "1100", "1000") for d in range(1, 11)]
    rows.append(artificial("11/08/2025", m, "Y", "FAQ", "4900", "5100", "5000"))  # artificial 5x spike
    store = RawStore(tmp_path)
    for d in sorted({r[0] for r in rows}):
        store.write_date(C, d, to_csv_bytes([r for r in rows if r[0] == d]), {})
    _load(db, store)
    _rebuild(db)
    flagged = _q(db, "SELECT date FROM price_observations WHERE 'local_outlier' = ANY(flags)")
    assert flagged == [(date(2025, 8, 11),)]


def test_rebuild_from_a_recent_date_keeps_alias_rules_and_display_names(db, tmp_path):
    store = RawStore(tmp_path)
    _backfill(store, real_rows())
    _load(db, store)
    _rebuild(db)
    before = (_q(db, "SELECT entity, parent_key, raw_name, canonical_name, rule FROM name_aliases ORDER BY 1, 2, 3"),
              _q(db, "SELECT name, display_name FROM markets ORDER BY 1"))
    with session_scope(db) as s:
        rebuild_commodity(s, C, [], since=date(2025, 11, 18), today=date(2025, 11, 21))
    after = (_q(db, "SELECT entity, parent_key, raw_name, canonical_name, rule FROM name_aliases ORDER BY 1, 2, 3"),
             _q(db, "SELECT name, display_name FROM markets ORDER BY 1"))
    assert after == before
    assert _q(db, "SELECT count(*) FROM price_observations")[0][0] == len({tuple(r) for r in real_rows()})


def test_snapshot_returning_to_earlier_content_is_applied_again(db, tmp_path):
    rows = real_rows()
    d = rows[60][0]
    a = [r for r in rows if r[0] == d]
    b = [list(r) for r in a]
    b[0][8] = str(int(b[0][8]) + 3)  # artificial correction
    store = RawStore(tmp_path)
    for version in (a, b, a):  # A -> B -> A
        assert store.write_date(C, d, to_csv_bytes(version), {}) is not None
        _load(db, store)
    active = {r[0] for r in _q(db, "SELECT modal_price_raw FROM source_observations "
                                   "WHERE superseded_by_raw_page_id IS NULL")}
    assert active == {r[8] for r in a} and b[0][8] not in active
