"""Raw response store: every API response is kept exactly as received (gzip) with its SHA-256 in a manifest.

Layout (same as the Phase 1 downloads, so those files load unchanged):

    <raw_dir>/<commodity>/page_<offset>.csv.gz          sorted pages of the full history
    <raw_dir>/<commodity>/manifest.jsonl                 one line per page fetch (last line per offset wins)
    <raw_dir>/<commodity>/by_date/<date>[__<sha8>__v<n>].csv.gz  one complete arrival date, version n
    <raw_dir>/<commodity>/by_date_manifest.jsonl         one line per by-date fetch

Files are never edited. A by-date fetch whose content equals the latest stored snapshot of that date is not
stored again.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

EXPECTED_HEADER = ["Arrival_Date", "Commodity", "Commodity_Code", "District", "Grade", "Market", "Max_Price",
                   "Min_Price", "Modal_Price", "State", "Variety"]


class SchemaChangeError(ValueError):
    """The source returned a header different from the one the pipeline was built and verified against."""


@dataclass(frozen=True)
class RawEntry:
    commodity: str
    kind: str  # "page" or "by_date"
    rel_path: str  # relative to raw_dir, with forward slashes
    sha256: str
    rows: int
    fetched_at: str
    params: dict
    arrival_date: date | None = None
    offset: int | None = None


def content_sha256(rows: list[list[str]]) -> str:
    """Order-independent hash of a response's data rows (header excluded)."""
    lines = sorted("\x1f".join(r) for r in rows[1:])
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def slug(commodity: str) -> str:
    return commodity.strip().lower().replace(" ", "_")


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_csv(raw: bytes) -> list[list[str]]:
    """Rows of a response, header first. Raises SchemaChangeError if the header is not the verified one."""
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    if not rows:
        raise SchemaChangeError("empty response (no header)")
    if rows[0] != EXPECTED_HEADER:
        raise SchemaChangeError(f"unexpected header {rows[0]!r}; expected {EXPECTED_HEADER!r}")
    return rows


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, commodity: str) -> Path:
        return self.root / slug(commodity)

    @staticmethod
    def _append(path: Path, rec: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    @staticmethod
    def _lines(path: Path) -> list[dict]:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    # ----- pages -----
    def page_manifest(self, commodity: str) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for rec in self._lines(self._dir(commodity) / "manifest.jsonl"):
            out[rec["offset"]] = rec
        return out

    def write_page(self, commodity: str, offset: int, limit: int, raw: bytes, params: dict, total: int) -> dict:
        rows = parse_csv(raw)
        d = self._dir(commodity)
        d.mkdir(parents=True, exist_ok=True)
        name = f"page_{offset:09d}.csv.gz"
        (d / name).write_bytes(gzip.compress(raw, mtime=0))
        rec = {"commodity": commodity, "offset": offset, "limit": limit, "rows": len(rows) - 1, "file": name,
               "sha256_raw": hashlib.sha256(raw).hexdigest(), "bytes_raw": len(raw),
               "first_date": rows[1][0] if len(rows) > 1 else None, "last_date": rows[-1][0] if len(rows) > 1 else None,
               "total_at_request": total, "fetched_at": now_utc(), "params": params}
        self._append(d / "manifest.jsonl", rec)
        return rec

    # ----- complete dates -----
    def date_manifest(self, commodity: str) -> list[dict]:
        return self._lines(self._dir(commodity) / "by_date_manifest.jsonl")

    def latest_date_snapshot(self, commodity: str, arrival_date_ddmmyyyy: str) -> dict | None:
        recs = [r for r in self.date_manifest(commodity) if r["date"] == arrival_date_ddmmyyyy]
        return recs[-1] if recs else None

    def _content_sha(self, commodity: str, rec: dict) -> str:
        if "content_sha256" in rec:
            return rec["content_sha256"]
        raw = gzip.decompress((self._dir(commodity) / rec["file"]).read_bytes())
        return content_sha256(parse_csv(raw))

    def write_date(self, commodity: str, arrival_date_ddmmyyyy: str, raw: bytes, params: dict) -> dict | None:
        """Store a complete arrival date. Returns None if it has the same rows as the latest snapshot of that date.

        Exact-date responses list their rows in a different order on each request (seen on the live API,
        2026-11-26), so "same" means the same set of rows, compared by an order-independent content hash."""
        rows = parse_csv(raw)
        sha = hashlib.sha256(raw).hexdigest()
        content = content_sha256(rows)
        latest = self.latest_date_snapshot(commodity, arrival_date_ddmmyyyy)
        if latest and (latest["sha256_raw"] == sha or self._content_sha(commodity, latest) == content):
            return None
        iso = datetime.strptime(arrival_date_ddmmyyyy, "%d/%m/%Y").strftime("%Y-%m-%d")
        fetched = now_utc()
        # Unique per stored version (sequence number per date, not a clock): if a date's content changes
        # A -> B -> A, the second A is a new file and is applied again.
        seq = sum(1 for r in self.date_manifest(commodity) if r["date"] == arrival_date_ddmmyyyy)
        name = f"by_date/{iso}__{sha[:8]}__v{seq:04d}.csv.gz"
        d = self._dir(commodity)
        (d / "by_date").mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(gzip.compress(raw, mtime=0))
        rec = {"commodity": commodity, "date": arrival_date_ddmmyyyy, "rows": len(rows) - 1, "file": name,
               "sha256_raw": sha, "content_sha256": content, "fetched_at": fetched, "params": params}
        self._append(d / "by_date_manifest.jsonl", rec)
        return rec

    # ----- reading -----
    def entries(self, commodity: str) -> list[RawEntry]:
        """All stored files of a commodity: pages (latest per offset), then date snapshots in fetch order."""
        s = slug(commodity)
        out = [RawEntry(commodity, "page", f"{s}/{r['file']}", r["sha256_raw"], r["rows"], r["fetched_at"],
                        r["params"], offset=off)
               for off, r in sorted(self.page_manifest(commodity).items())]
        seen = {e.rel_path for e in out}
        for r in self.date_manifest(commodity):
            path = f"{s}/{r['file']}"
            if path in seen:  # same file listed twice (identical bytes; possible before unique names, Phase 2)
                continue
            seen.add(path)
            out.append(RawEntry(commodity, "by_date", f"{s}/{r['file']}", r["sha256_raw"], r["rows"], r["fetched_at"],
                                r["params"], arrival_date=datetime.strptime(r["date"], "%d/%m/%Y").date()))
        return out

    def read_verified(self, entry: RawEntry) -> bytes:
        raw = gzip.decompress((self.root / entry.rel_path).read_bytes())
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ValueError(f"SHA-256 mismatch for {entry.rel_path}: file differs from its manifest")
        return raw
