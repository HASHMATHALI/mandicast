"""Download jobs: full-history backfill (sorted pages + boundary repair) and refresh of recent arrival dates.

Why boundary repair: on the live index, rows sharing an arrival date can move between pages requested at
different times (Phase 1: 79 rows across four crops would have been missed). Every date split across two pages
is therefore re-fetched whole with the exact-date filter. Daily refreshes use only exact-date requests, so they
have no pagination at all.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from datetime import date

from mandicast.ingest.client import DataGovClient, FetchError
from mandicast.ingest.raw_store import RawStore, parse_csv

log = logging.getLogger(__name__)


def ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def boundary_dates(page_manifest: dict[int, dict]) -> list[str]:
    offs = sorted(page_manifest)
    return sorted({page_manifest[a]["last_date"] for a, b in zip(offs, offs[1:], strict=False)
                   if page_manifest[a]["last_date"] == page_manifest[b]["first_date"]})


def backfill_pages(client: DataGovClient, store: RawStore, commodity: str, page_size: int, pause: float,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    done = store.page_manifest(commodity)
    total = client.total(commodity)
    fetched = 0
    offset = 0
    while True:
        while offset < total:
            if offset in done and done[offset]["rows"] == page_size:
                offset += page_size
                continue
            expected = min(page_size, total - offset)
            for attempt in range(4):
                raw = client.csv_page(commodity, offset, page_size)
                n = len(parse_csv(raw)) - 1
                if n >= expected:
                    break
                log.warning("%s offset %d: %d rows, expected %d; retrying", commodity, offset, n, expected)
                sleep(10 * (attempt + 1))
            else:
                raise FetchError(f"{commodity} offset {offset}: short page ({n} < {expected}) after retries")
            params = {"format": "csv", "limit": page_size, "offset": offset, "filters[Commodity]": commodity,
                      "sort[Arrival_Date]": "asc"}
            done[offset] = store.write_page(commodity, offset, page_size, raw, params, total)
            fetched += n
            offset += page_size
            sleep(pause)
        new_total = client.total(commodity)
        if new_total <= total:
            break
        last = max(done)
        total = new_total
        offset = last if done[last]["rows"] < page_size else last + page_size
    return {"commodity": commodity, "total": total, "rows_fetched": fetched,
            "rows_stored": sum(r["rows"] for r in done.values()), "pages": len(done)}


def fetch_dates(client: DataGovClient, store: RawStore, commodity: str, dates: Iterable[str], page_size: int,
                pause: float, skip_existing: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Fetch complete arrival dates (dd/mm/yyyy). Unchanged snapshots are not stored again.

    A date without records comes back as HTTP 200 with an empty body (verified on the live API, 2026-11-26):
    it is counted in `dates_empty` and nothing is stored, so it can never supersede existing rows."""
    stored = unchanged = rows = empty = 0
    for d in dates:
        if skip_existing and store.latest_date_snapshot(commodity, d):
            continue
        params = {"format": "csv", "limit": page_size, "offset": 0, "filters[Commodity]": commodity,
                  "filters[Arrival_Date]": d}
        raw = client.csv_date(commodity, d, page_size)
        if not raw.strip():
            empty += 1
            sleep(pause)
            continue
        n = len(parse_csv(raw)) - 1
        if n >= page_size:
            raise FetchError(f"{commodity} {d}: {n} rows on one date; per-date pagination needed")
        rows += n
        if store.write_date(commodity, d, raw, params) is None:
            unchanged += 1
        else:
            stored += 1
        sleep(pause)
    return {"commodity": commodity, "dates_stored": stored, "dates_unchanged": unchanged, "dates_empty": empty,
            "rows_fetched": rows}


def repair_boundaries(client: DataGovClient, store: RawStore, commodity: str, page_size: int, pause: float,
                      sleep: Callable[[float], None] = time.sleep) -> dict:
    dates = boundary_dates(store.page_manifest(commodity))
    res = fetch_dates(client, store, commodity, dates, page_size, pause, skip_existing=True, sleep=sleep)
    return {**res, "boundary_dates": len(dates)}
