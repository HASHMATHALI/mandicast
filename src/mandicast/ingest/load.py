"""Load stored raw files into `source_observations`, idempotently.

- Each file is loaded once (`raw_pages.file_path` is unique) and verified against its SHA-256 first.
- Each source row is identified by the SHA-256 of its eleven raw fields. Inserts use
  `ON CONFLICT (source_row_hash) DO NOTHING`, so re-running a load, or loading overlapping files (pages and
  date snapshots), never creates duplicates. Identical rows that the source itself repeats are stored once.
- A complete date snapshot is authoritative for its date: rows of that commodity and date that it does not
  contain are marked superseded (never deleted), and rows it contains are active again. Safety rule: a snapshot
  with no rows, or with fewer than 90% of the rows currently active for that date, is stored and reported but
  does not supersede anything (an outage or partial response must not hide data).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mandicast.db.models import RawPage
from mandicast.ingest.raw_store import RawEntry, RawStore, parse_csv

log = logging.getLogger(__name__)
SUPERSEDE_MIN_SHARE = 0.9
_COLS = ["arrival_date_raw", "commodity_raw", "commodity_code_raw", "district_raw", "grade_raw", "market_raw",
         "max_price_raw", "min_price_raw", "modal_price_raw", "state_raw", "variety_raw"]  # EXPECTED_HEADER order


def row_hash(fields: list[str]) -> str:
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def parse_date(raw: str):
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


@dataclass
class LoadStats:
    files_loaded: int = 0
    files_skipped: int = 0
    rows_read: int = 0
    rows_inserted: int = 0
    rows_duplicate: int = 0
    snapshots_applied: int = 0
    snapshots_not_applied: list[dict] = field(default_factory=list)
    rows_superseded: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _copy_rows(session: Session, rows: list[list[str]]) -> None:
    conn = session.connection().connection.driver_connection
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _stage (source_row_hash text, arrival_date date, "
                    + ", ".join(f"{c} text" for c in _COLS) + ") ON COMMIT DELETE ROWS")
        cur.execute("TRUNCATE _stage")
        with cur.copy(f"COPY _stage (source_row_hash, arrival_date, {', '.join(_COLS)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([row_hash(r), parse_date(r[0]), *r])


def load_entry(session: Session, store: RawStore, entry: RawEntry, job_id: int | None, stats: LoadStats) -> None:
    raw = store.read_verified(entry)
    rows = parse_csv(raw)[1:]
    page = RawPage(job_id=job_id, commodity=entry.commodity, kind=entry.kind, file_path=entry.rel_path,
                   sha256=entry.sha256, rows=len(rows), arrival_date=entry.arrival_date, request_params=entry.params,
                   fetched_at=datetime.strptime(entry.fetched_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC))
    session.add(page)
    session.flush()
    _copy_rows(session, rows)
    # Column names come from the constant _COLS, never from input.
    inserted = session.execute(text(
        f"INSERT INTO source_observations (raw_page_id, source_row_hash, arrival_date, {', '.join(_COLS)}) "  # noqa: S608
        f"SELECT DISTINCT ON (source_row_hash) :pid, source_row_hash, arrival_date, {', '.join(_COLS)} FROM _stage "
        "ON CONFLICT (source_row_hash) DO NOTHING"), {"pid": page.id}).rowcount
    page.rows_inserted = inserted
    stats.files_loaded += 1
    stats.rows_read += len(rows)
    stats.rows_inserted += inserted
    stats.rows_duplicate += len(rows) - inserted
    if entry.kind == "by_date":
        _apply_snapshot(session, entry, page.id, len(rows), inserted, stats)


def _apply_snapshot(session: Session, entry: RawEntry, page_id: int, n_rows: int, inserted: int,
                    stats: LoadStats) -> None:
    params = {"c": entry.commodity, "d": entry.arrival_date, "pid": page_id}
    active = session.execute(text(
        "SELECT count(*) FROM source_observations WHERE commodity_raw = :c AND arrival_date = :d "
        "AND superseded_by_raw_page_id IS NULL"), params).scalar_one()
    # Compare with the rows that were active before this snapshot (its newly inserted rows excluded).
    active_before = active - inserted
    in_snapshot = session.execute(text("SELECT count(DISTINCT source_row_hash) FROM _stage")).scalar_one()
    if n_rows == 0 or in_snapshot < SUPERSEDE_MIN_SHARE * active_before:
        stats.snapshots_not_applied.append({"commodity": entry.commodity, "date": str(entry.arrival_date),
                                            "snapshot_rows": in_snapshot, "active_rows_before": active_before})
        log.warning("snapshot %s not applied: %d rows vs %d active before", entry.rel_path, in_snapshot,
                    active_before)
        return
    session.execute(text(
        "UPDATE source_observations s SET superseded_by_raw_page_id = NULL "
        "WHERE s.commodity_raw = :c AND s.arrival_date = :d AND s.superseded_by_raw_page_id IS NOT NULL "
        "AND s.source_row_hash IN (SELECT source_row_hash FROM _stage)"), params)
    stats.rows_superseded += session.execute(text(
        "UPDATE source_observations s SET superseded_by_raw_page_id = :pid "
        "WHERE s.commodity_raw = :c AND s.arrival_date = :d AND s.superseded_by_raw_page_id IS NULL "
        "AND s.source_row_hash NOT IN (SELECT source_row_hash FROM _stage)"), params).rowcount
    stats.snapshots_applied += 1


def load_commodity(session: Session, store: RawStore, commodity: str, job_id: int | None = None,
                   commit_every: int = 50) -> LoadStats:
    """Load every stored file of a commodity not yet in `raw_pages`. Commits every `commit_every` files."""
    loaded = set(session.scalars(select(RawPage.file_path).where(RawPage.commodity == commodity)))
    stats = LoadStats()
    for entry in store.entries(commodity):
        if entry.rel_path in loaded:
            stats.files_skipped += 1
            continue
        load_entry(session, store, entry, job_id, stats)
        if stats.files_loaded % commit_every == 0:
            session.commit()
            log.info("%s: %d files loaded (%d rows inserted)", commodity, stats.files_loaded, stats.rows_inserted)
    return stats
