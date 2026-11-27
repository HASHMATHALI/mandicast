"""Download the full price history of selected commodities from the official AGMARKNET resource on data.gov.in.

Phase 1 uses it to pull the profiling sample; Phase 2 replaces it with the scheduled, database-backed pipeline,
which can load these raw pages unchanged.

    python -I scripts/fetch_agmarknet_history.py Onion Tomato Potato Wheat Soyabean

Design (each point was checked against the API on 2026-11-26, see docs/02_data_source_assessment.md):
- one commodity at a time, sorted by Arrival_Date ascending, CSV pages of 10,000 rows. Repeating a request gave
  byte-identical output, and consecutive pages join exactly at the boundary;
- every page is stored as received (gzip) with its SHA-256 in an append-only manifest, so the raw data can be
  re-verified and the download resumed: pages already in the manifest are skipped;
- the number of rows per page is checked against what the reported total implies; a short page is retried;
- after the last page the total is queried again, and new rows published meanwhile (latest dates) are fetched;
- retries with exponential backoff on HTTP 429/5xx, timeouts and connection errors. TLS is verified as normal;
- the API key is read from .env and never written to logs, the manifest or error messages.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"
BASE_URL = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
USER_AGENT = "MandiCast-ingest/0.1"
EXPECTED_HEADER = ["Arrival_Date", "Commodity", "Commodity_Code", "District", "Grade", "Market", "Max_Price",
                   "Min_Price", "Modal_Price", "State", "Variety"]
RETRYABLE_HTTP = {429, 500, 502, 503, 504}

log = logging.getLogger("fetch")


class FetchError(RuntimeError):
    pass


def read_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if line.startswith("DATA_GOV_IN_API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("DATA_GOV_IN_API_KEY missing: copy .env.example to .env and add your data.gov.in key.")


def request(key: str, params: dict, timeout: int = 300, retries: int = 10) -> bytes:
    query = urllib.parse.urlencode({"api-key": key, **params})
    req = urllib.request.Request(f"{BASE_URL}?{query}", headers={"User-Agent": USER_AGENT})  # noqa: S310 - https only
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
                return resp.read()
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}"
            if exc.code not in RETRYABLE_HTTP:
                raise FetchError(f"{reason} for {params}") from None
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            # URLError.reason and socket errors never contain the request URL, so the key cannot leak here.
            detail = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            reason = f"{type(exc).__name__} ({detail})"
        if attempt == retries:
            raise FetchError(f"{reason} after {retries} retries for {params}")
        wait = min(600, 5 * 2 ** attempt)  # 5 s ... 10 min; ~40 min in total before giving up
        log.warning("%s for %s; retry %d in %ds", reason, params, attempt + 1, wait)
        time.sleep(wait)
    raise AssertionError("unreachable")


def total_rows(key: str, commodity: str) -> int:
    body = json.loads(request(key, {"format": "json", "limit": 1, "filters[Commodity]": commodity}))
    return int(body["total"])


def load_manifest(path: Path) -> dict[int, dict]:
    done: dict[int, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            done[rec["offset"]] = rec
    return done


def fetch_commodity(key: str, commodity: str, out_root: Path, page_size: int, pause: float) -> dict:
    out = out_root / commodity.lower().replace(" ", "_")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.jsonl"
    done = load_manifest(manifest_path)
    total = total_rows(key, commodity)
    log.info("%s: %d rows reported; %d pages already stored", commodity, total, len(done))
    offset = 0
    while True:
        while offset < total:
            if offset in done and done[offset]["rows"] == page_size:
                offset += page_size
                continue
            params = {"format": "csv", "limit": page_size, "offset": offset, "filters[Commodity]": commodity,
                      "sort[Arrival_Date]": "asc"}
            expected = min(page_size, total - offset)
            for attempt in range(4):
                t = time.perf_counter()
                raw = request(key, params)
                rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
                if not rows or rows[0] != EXPECTED_HEADER:
                    raise FetchError(f"unexpected header at offset {offset}: {rows[0] if rows else 'empty'}")
                n = len(rows) - 1
                if n >= expected:
                    break
                log.warning("%s offset %d: %d rows, expected %d; retrying", commodity, offset, n, expected)
                time.sleep(10 * (attempt + 1))
            else:
                raise FetchError(f"{commodity} offset {offset}: short page ({n} < {expected}) after retries")
            name = f"page_{offset:09d}.csv.gz"
            (out / name).write_bytes(gzip.compress(raw, mtime=0))
            rec = {"commodity": commodity, "offset": offset, "limit": page_size, "rows": n, "file": name,
                   "sha256_raw": hashlib.sha256(raw).hexdigest(), "bytes_raw": len(raw),
                   "first_date": rows[1][0] if n else None, "last_date": rows[-1][0] if n else None,
                   "total_at_request": total, "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "seconds": round(time.perf_counter() - t, 2),
                   "params": {k: v for k, v in params.items()}, "resource_id": RESOURCE_ID}
            with manifest_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            done[offset] = rec
            log.info("%s %d/%d rows (%s .. %s) %.1fs", commodity, offset + n, total, rec["first_date"],
                     rec["last_date"], rec["seconds"])
            offset += page_size
            time.sleep(pause)
        new_total = total_rows(key, commodity)
        last = max(done) if done else 0
        if new_total <= total:
            # Nothing new. A short last page is re-fetched on the next run, in case it has grown since.
            break
        log.info("%s: total grew from %d to %d during the download; fetching the rest", commodity, total, new_total)
        total = new_total
        offset = last if done[last]["rows"] < page_size else last + page_size
        done.pop(offset, None)
    stored = sum(r["rows"] for o, r in done.items())
    summary = {"commodity": commodity, "total_reported_at_end": total, "rows_stored": stored, "pages": len(done)}
    log.info("%s done: %s", commodity, summary)
    return summary


def boundary_dates(manifest: dict[int, dict]) -> list[str]:
    """Dates split across two consecutive pages (rows with equal sort keys can shift between requests)."""
    offs = sorted(manifest)
    return sorted({manifest[a]["last_date"] for a, b in zip(offs, offs[1:], strict=False)
                   if manifest[a]["last_date"] == manifest[b]["first_date"]})


def repair_boundaries(key: str, commodity: str, out_root: Path, pause: float, page_size: int = 10_000) -> dict:
    """Re-fetch every page-boundary date in full with the exact Arrival_Date filter (one request per date).

    Found in Phase 1: on a live index, rows that share a sort key (the same arrival date) can move between
    pages fetched at different times, so a boundary date can get duplicated and missing rows. The loader uses
    these per-date files instead of the page rows for those dates.
    """
    out = out_root / commodity.lower().replace(" ", "_")
    dates = boundary_dates(load_manifest(out / "manifest.jsonl"))
    (out / "by_date").mkdir(exist_ok=True)
    bman = out / "by_date_manifest.jsonl"
    done = ({json.loads(line)["date"] for line in bman.read_text(encoding="utf-8").splitlines()}
            if bman.exists() else set())
    for d in dates:
        if d in done:
            continue
        params = {"format": "csv", "limit": page_size, "offset": 0, "filters[Commodity]": commodity,
                  "filters[Arrival_Date]": d}
        raw = request(key, params)
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        if not rows or rows[0] != EXPECTED_HEADER:
            raise FetchError(f"unexpected header for {commodity} {d}")
        n = len(rows) - 1
        if n >= page_size:
            raise FetchError(f"{commodity} {d}: {n} rows on one date; per-date pagination needed")
        iso = datetime.strptime(d, "%d/%m/%Y").strftime("%Y-%m-%d")
        name = f"by_date/{iso}.csv.gz"
        (out / name).write_bytes(gzip.compress(raw, mtime=0))
        rec = {"commodity": commodity, "date": d, "rows": n, "file": name,
               "sha256_raw": hashlib.sha256(raw).hexdigest(),
               "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "params": params,
               "resource_id": RESOURCE_ID}
        with bman.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        log.info("%s boundary date %s: %d rows", commodity, d, n)
        time.sleep(pause)
    summary = {"commodity": commodity, "boundary_dates": len(dates)}
    log.info("%s boundaries repaired: %s", commodity, summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commodities", nargs="+")
    ap.add_argument("--repair-only", action="store_true",
                    help="only re-fetch page-boundary dates for already downloaded commodities")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "agmarknet_variety_daily")
    ap.add_argument("--page-size", type=int, default=10_000)
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests (politeness)")
    ap.add_argument("--no-keep-awake", action="store_true",
                    help="on Windows the script asks the OS not to idle-sleep while it runs; this turns that off")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    key = read_key()
    if sys.platform == "win32" and not args.no_keep_awake:
        # Per-process power request (like a media player); Windows drops it when the process exits.
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | SYSTEM_REQUIRED
        log.info("asked Windows not to idle-sleep while this download runs")
    summaries = []
    for c in args.commodities:
        s = {} if args.repair_only else fetch_commodity(key, c, args.out, args.page_size, args.pause)
        summaries.append({**s, **repair_boundaries(key, c, args.out, args.pause)})
    name = "repair_summary.json" if args.repair_only else "summary.json"
    (args.out / name).write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    log.info("ALL DONE %s", json.dumps(summaries))


if __name__ == "__main__":
    main()
