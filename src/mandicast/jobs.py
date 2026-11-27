"""Job records and locking.

Every pipeline run is a row in `ingestion_jobs` (running → succeeded / failed / skipped). Its data changes run in
their own transactions; if a run fails, those are rolled back and the failure (type and message, secrets
redacted) is still recorded in a separate transaction. A PostgreSQL advisory lock stops two pipeline runs from
working at the same time.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, text

from mandicast.config import PROJECT_ROOT
from mandicast.logging_setup import redact

log = logging.getLogger(__name__)
PIPELINE_LOCK_ID = 7_301_011  # arbitrary constant for pg_advisory_lock
SERVING_LOCK_ID = 7_301_012  # model training and forecast publication (read the cleaned data; never write it)
REPORT_DIR = PROJECT_ROOT / "reports" / "quality"


class JobLockedError(RuntimeError):
    pass


@dataclass
class JobContext:
    id: int
    kind: str
    scope: dict
    counts: dict = field(default_factory=lambda: {"rows_fetched": 0, "rows_inserted": 0, "rows_duplicate": 0,
                                                  "rows_rejected": 0})
    report: dict = field(default_factory=dict)


@contextmanager
def pipeline_lock(engine: Engine, lock_id: int = PIPELINE_LOCK_ID) -> Iterator[None]:
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_id}).scalar_one()
        if not got:
            what = "pipeline" if lock_id == PIPELINE_LOCK_ID else "serving"
            raise JobLockedError(f"another MandiCast {what} run holds the lock")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_id})


@contextmanager
def run_job(engine: Engine, kind: str, scope: dict, report_dir: Path | None = None,
            lock_id: int = PIPELINE_LOCK_ID) -> Iterator[JobContext]:
    with engine.begin() as conn:
        job_id = conn.execute(
            text("INSERT INTO ingestion_jobs (kind, scope) VALUES (:k, CAST(:s AS jsonb)) RETURNING id"),
            {"k": kind, "s": json.dumps(scope, default=str)}).scalar_one()
    ctx = JobContext(job_id, kind, scope)
    log.info("job %d (%s) started: %s", job_id, kind, scope)
    try:
        with pipeline_lock(engine, lock_id):
            yield ctx
    except JobLockedError as exc:
        _finish(engine, ctx, "skipped", str(exc))
        log.warning("job %d skipped: %s", job_id, exc)
        raise
    except BaseException as exc:
        _finish(engine, ctx, "failed", redact(f"{type(exc).__name__}: {exc}")[:4000])
        log.error("job %d failed: %s", job_id, redact(f"{type(exc).__name__}: {exc}"))
        raise
    else:
        _finish(engine, ctx, "succeeded", None)
        log.info("job %d succeeded: %s", job_id, ctx.counts)
    finally:
        report_dir = report_dir or REPORT_DIR
        if ctx.report:
            report_dir.mkdir(parents=True, exist_ok=True)
            payload = {"job_id": job_id, "kind": kind, "scope": scope, "counts": ctx.counts, "report": ctx.report,
                       "written_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
            (report_dir / f"job_{job_id:06d}_{kind}.json").write_text(json.dumps(payload, indent=2, default=str) + "\n",
                                                                      encoding="utf-8")


def _finish(engine: Engine, ctx: JobContext, status: str, error: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE ingestion_jobs SET status = :st, finished_at = now(), error = :e, rows_fetched = :f, "
            "rows_inserted = :i, rows_duplicate = :d, rows_rejected = :r, quality_report = CAST(:q AS jsonb) "
            "WHERE id = :id"),
            {"st": status, "e": error, "f": ctx.counts["rows_fetched"], "i": ctx.counts["rows_inserted"],
             "d": ctx.counts["rows_duplicate"], "r": ctx.counts["rows_rejected"],
             "q": json.dumps(ctx.report, default=str), "id": ctx.id})
