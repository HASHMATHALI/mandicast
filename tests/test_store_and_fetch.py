"""Raw store integrity and download logic (no network, no database)."""

from __future__ import annotations

import gzip

import pytest
from conftest import EXPECTED_HEADER, FakeClient, real_rows, to_csv_bytes

from mandicast.ingest import fetch
from mandicast.ingest.client import FetchError
from mandicast.ingest.raw_store import RawStore, SchemaChangeError, parse_csv


def test_header_change_is_rejected():
    changed = [*EXPECTED_HEADER[:-1], "Variety_Name"]  # artificial: a renamed source column
    with pytest.raises(SchemaChangeError, match="unexpected header"):
        parse_csv(to_csv_bytes(real_rows()[:2], header=changed))
    with pytest.raises(SchemaChangeError, match="empty"):
        parse_csv(b"")


def test_tampered_file_is_detected(tmp_path):
    store = RawStore(tmp_path)
    store.write_date("Soyabean", "24/10/2025", to_csv_bytes(real_rows()[:3]), {"x": 1})
    entry = store.entries("Soyabean")[0]
    path = tmp_path / entry.rel_path
    path.write_bytes(gzip.compress(to_csv_bytes(real_rows()[:2]), mtime=0))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        store.read_verified(entry)


def test_identical_date_snapshot_is_not_stored_twice(tmp_path):
    store = RawStore(tmp_path)
    raw = to_csv_bytes(real_rows()[:3])
    assert store.write_date("Soyabean", "24/10/2025", raw, {}) is not None
    assert store.write_date("Soyabean", "24/10/2025", raw, {}) is None
    assert store.write_date("Soyabean", "24/10/2025", to_csv_bytes(real_rows()[:2]), {}) is not None
    assert len(store.date_manifest("Soyabean")) == 2


def test_backfill_pages_then_resume_without_refetching(tmp_path):
    rows = real_rows()
    client = FakeClient(rows)
    store = RawStore(tmp_path)
    res = fetch.backfill_pages(client, store, "Soyabean", page_size=30, pause=0, sleep=lambda s: None)
    assert res["rows_stored"] == len(rows) == 100 and res["pages"] == 4
    client.calls.clear()
    res2 = fetch.backfill_pages(client, store, "Soyabean", page_size=30, pause=0, sleep=lambda s: None)
    pages_fetched = [c for c in client.calls if c[0] == "page"]
    assert pages_fetched == [("page", 90, 30)]  # only the short last page is re-checked
    assert res2["rows_stored"] == 100


def test_boundary_dates_and_repair(tmp_path):
    client = FakeClient(real_rows())
    store = RawStore(tmp_path)
    fetch.backfill_pages(client, store, "Soyabean", page_size=30, pause=0, sleep=lambda s: None)
    manifest = store.page_manifest("Soyabean")
    dates = fetch.boundary_dates(manifest)
    offs = sorted(manifest)
    expected = {manifest[a]["last_date"] for a, b in zip(offs, offs[1:], strict=False)
                if manifest[a]["last_date"] == manifest[b]["first_date"]}
    assert set(dates) == expected and dates
    res = fetch.repair_boundaries(client, store, "Soyabean", page_size=10_000, pause=0, sleep=lambda s: None)
    assert res["boundary_dates"] == len(dates) and res["dates_stored"] == len(dates)
    again = fetch.repair_boundaries(client, store, "Soyabean", page_size=10_000, pause=0, sleep=lambda s: None)
    assert again["dates_stored"] == 0  # already repaired dates are skipped


def test_short_page_is_retried_then_fails(tmp_path):
    class Shrinking(FakeClient):
        def csv_page(self, commodity, offset, limit):
            self.calls.append(("page", offset, limit))
            return to_csv_bytes(self._sorted()[offset:offset + limit - 5])  # always 5 rows short
    with pytest.raises(FetchError, match="short page"):
        fetch.backfill_pages(Shrinking(real_rows()), RawStore(tmp_path), "Soyabean", page_size=30, pause=0,
                             sleep=lambda s: None)


def test_date_with_too_many_rows_needs_pagination(tmp_path):
    rows = real_rows()
    d = rows[0][0]
    with pytest.raises(FetchError, match="pagination"):
        fetch.fetch_dates(FakeClient(rows), RawStore(tmp_path), "Soyabean", [d], page_size=1, pause=0,
                          sleep=lambda s: None)


def test_date_without_records_is_counted_not_stored(tmp_path):
    store = RawStore(tmp_path)
    res = fetch.fetch_dates(FakeClient(real_rows()), store, "Soyabean", ["26/11/2026", real_rows()[0][0]],
                            page_size=10_000, pause=0, sleep=lambda s: None)
    assert res["dates_empty"] == 1 and res["dates_stored"] == 1
    assert [r["date"] for r in store.date_manifest("Soyabean")] == [real_rows()[0][0]]


def test_same_rows_in_a_different_order_are_not_stored_again(tmp_path):
    store = RawStore(tmp_path)
    rows = real_rows()[:6]
    assert store.write_date("Soyabean", "24/10/2025", to_csv_bytes(rows), {}) is not None
    assert store.write_date("Soyabean", "24/10/2025", to_csv_bytes(list(reversed(rows))), {}) is None
    assert len(store.date_manifest("Soyabean")) == 1
