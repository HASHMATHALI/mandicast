"""Pipeline operations used by the CLI and the scheduler.

- `load_raw`  : load stored raw files into source_observations (no network).
- `backfill`  : download a commodity's full history (pages + boundary repair), then load and clean it.
- `daily`     : for each commodity, re-fetch the last `refetch_days` arrival dates up to today (late reports and
                corrections), load them, and rebuild the cleaned layer from the earliest re-fetched date.
- `clean`     : rebuild the cleaned layer (all history, or from a date).
Each runs as one recorded job (jobs.run_job) under the pipeline lock.
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, text

from mandicast.clean import names
from mandicast.clean.build import rebuild_commodity
from mandicast.config import Settings
from mandicast.db.session import session_scope
from mandicast.ingest import fetch
from mandicast.ingest.client import DataGovClient
from mandicast.ingest.load import load_commodity
from mandicast.ingest.raw_store import RawStore
from mandicast.jobs import JobContext, run_job

log = logging.getLogger(__name__)
IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    return datetime.now(IST).date()


def make_client(settings: Settings) -> DataGovClient:
    key = settings.data_gov_in_api_key.get_secret_value() if settings.data_gov_in_api_key else ""
    return DataGovClient(settings.api_base_url, key, settings.user_agent, settings.request_timeout_s,
                         settings.request_retries)


def _load(engine: Engine, store: RawStore, commodity: str, job: JobContext) -> dict:
    with session_scope(engine) as s:
        st = load_commodity(s, store, commodity, job.id)
    job.counts["rows_inserted"] += st.rows_inserted
    job.counts["rows_duplicate"] += st.rows_duplicate
    return st.as_dict()


def _clean(engine: Engine, settings: Settings, commodity: str, since: date | None, job: JobContext) -> dict:
    manual = names.load_manual(settings.manual_aliases_path)
    with session_scope(engine) as s:
        res = rebuild_commodity(s, commodity, manual, since=since, today=today_ist())
    job.counts["rows_rejected"] += res.rows_invalid_date
    return res.as_dict()


def load_raw(engine: Engine, settings: Settings, commodities: list[str], clean: bool = True) -> int:
    store = RawStore(settings.raw_dir)
    with run_job(engine, "load_raw", {"commodities": commodities, "clean": clean}) as job:
        for c in commodities:
            job.report[c] = {"load": _load(engine, store, c, job)}
            if clean:
                job.report[c]["clean"] = _clean(engine, settings, c, None, job)
        return job.id


def backfill(engine: Engine, settings: Settings, commodities: list[str], client: DataGovClient | None = None) -> int:
    client = client or make_client(settings)
    store = RawStore(settings.raw_dir)
    with run_job(engine, "backfill", {"commodities": commodities}) as job:
        for c in commodities:
            pages = fetch.backfill_pages(client, store, c, settings.page_size, settings.request_pause_s)
            repair = fetch.repair_boundaries(client, store, c, settings.page_size, settings.request_pause_s)
            job.counts["rows_fetched"] += pages["rows_fetched"] + repair["rows_fetched"]
            job.report[c] = {"pages": pages, "boundary_repair": repair, "load": _load(engine, store, c, job),
                             "clean": _clean(engine, settings, c, None, job)}
        return job.id


def _last_loaded_date(engine: Engine, commodity: str) -> date | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT max(arrival_date) FROM source_observations WHERE commodity_raw = :c "
                                 "AND superseded_by_raw_page_id IS NULL"), {"c": commodity}).scalar_one()


def daily(engine: Engine, settings: Settings, commodities: list[str], client: DataGovClient | None = None,
          today: date | None = None) -> int:
    client = client or make_client(settings)
    store = RawStore(settings.raw_dir)
    today = today or today_ist()
    with run_job(engine, "daily", {"commodities": commodities, "today": str(today)}) as job:
        for c in commodities:
            last = _last_loaded_date(engine, c)
            if last is None:
                raise RuntimeError(f"{c}: no data loaded yet; run backfill or load-raw first")
            start = min(last, today) - timedelta(days=settings.refetch_days)
            dates = [start + timedelta(days=i) for i in range((today - start).days + 1)]
            got = fetch.fetch_dates(client, store, c, [fetch.ddmmyyyy(d) for d in dates], settings.page_size,
                                    settings.request_pause_s)
            job.counts["rows_fetched"] += got["rows_fetched"]
            job.report[c] = {"refetched_from": str(start), "fetch": got, "load": _load(engine, store, c, job),
                             "clean": _clean(engine, settings, c, start, job)}
        return job.id


def clean(engine: Engine, settings: Settings, commodities: list[str], since: date | None = None) -> int:
    with run_job(engine, "clean", {"commodities": commodities, "since": str(since) if since else None}) as job:
        for c in commodities:
            job.report[c] = {"clean": _clean(engine, settings, c, since, job)}
        return job.id
