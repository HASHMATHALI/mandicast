"""Job records, locking, and the backfill → daily pipeline end to end (fake API, real test database)."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import FakeClient, db_url_for_tests, real_rows
from sqlalchemy import text

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.config import Settings
from mandicast.jobs import PIPELINE_LOCK_ID, JobLockedError, run_job

pytestmark = pytest.mark.db


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "REPORT_DIR", tmp_path / "reports")
    return Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                    request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30)


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def test_job_success_and_failure_are_recorded_with_secrets_redacted(db, settings):
    with run_job(db, "test", {"a": 1}) as job:
        job.counts["rows_fetched"] = 5
        job.report["x"] = 1
    with pytest.raises(RuntimeError), run_job(db, "test", {}):
        raise RuntimeError("upstream said https://api.data.gov.in/x?api-key=SECRETKEY123456&format=csv")
    rows = _q(db, "SELECT status, rows_fetched, error FROM ingestion_jobs ORDER BY id")
    assert rows[0][:2] == ("succeeded", 5)
    assert rows[1][0] == "failed" and "SECRETKEY123456" not in rows[1][2] and "api-key=***" in rows[1][2]
    assert (jobs.REPORT_DIR / "job_000001_test.json").exists()


def test_concurrent_run_is_skipped(db, settings):
    with db.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PIPELINE_LOCK_ID})
        with pytest.raises(JobLockedError), run_job(db, "test", {}):
            pass
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PIPELINE_LOCK_ID})
    assert _q(db, "SELECT status FROM ingestion_jobs") == [("skipped",)]


def test_backfill_then_daily_is_idempotent_and_picks_up_corrections(db, settings):
    rows = real_rows()
    client = FakeClient(rows)
    pipeline.backfill(db, settings, ["Soyabean"], client=client)
    n_src = _q(db, "SELECT count(*) FROM source_observations")[0][0]
    n_clean = _q(db, "SELECT count(*) FROM price_observations")[0][0]
    assert n_src == n_clean == len({tuple(r) for r in rows})

    # Daily run with unchanged source: re-fetches the last week, stores nothing new, changes nothing.
    pipeline.daily(db, settings, ["Soyabean"], client=client, today=date(2025, 11, 20))
    assert _q(db, "SELECT count(*) FROM source_observations")[0][0] == n_src
    assert _q(db, "SELECT count(*) FROM price_observations")[0][0] == n_clean
    report = _q(db, "SELECT quality_report FROM ingestion_jobs WHERE kind = 'daily'")[0][0]
    assert report["Soyabean"]["fetch"]["dates_stored"] <= 8 and report["Soyabean"]["load"]["rows_inserted"] == 0

    # The source corrects one recent price (artificial correction): the old value is superseded, the new one used.
    corrected = [list(r) for r in rows]
    target = next(r for r in corrected if r[0] == "18/11/2025")
    old_modal = target[8]
    target[8] = str(int(target[8]) + 7)
    pipeline.daily(db, settings, ["Soyabean"], client=FakeClient(corrected), today=date(2025, 11, 20))
    assert _q(db, "SELECT count(*) FROM source_observations WHERE superseded_by_raw_page_id IS NOT NULL")[0][0] == 1
    modals = {float(m[0]) for m in _q(db, "SELECT po.modal_price FROM price_observations po JOIN source_observations s "
                                          "ON s.id = po.source_observation_id WHERE s.arrival_date = '2025-11-18' "
                                          "AND s.market_raw = :m AND s.variety_raw = :v AND s.grade_raw = :g",
                                          m=target[5], v=target[10], g=target[4])}
    assert float(target[8]) in modals and (float(old_modal) not in modals)
    assert _q(db, "SELECT count(*) FROM price_observations")[0][0] == n_clean
    statuses = [r[0] for r in _q(db, "SELECT status FROM ingestion_jobs ORDER BY id")]
    assert statuses == ["succeeded"] * 3


def test_daily_without_loaded_data_fails_and_is_recorded(db, settings):
    with pytest.raises(RuntimeError, match="no data loaded"):
        pipeline.daily(db, settings, ["Soyabean"], client=FakeClient(real_rows()), today=date(2025, 11, 20))
    assert _q(db, "SELECT status FROM ingestion_jobs") == [("failed",)]


def test_scheduler_registers_ingestion_and_publication_without_overlap(settings):
    from mandicast.scheduler import build_scheduler
    sched = build_scheduler(None, settings)
    jobs_ = {j.id: j for j in sched.get_jobs()}
    assert set(jobs_) == {"daily_ingest", "publish_forecasts", "ensure_models", "monitor", "alerts", "privacy_purge"}
    assert str(jobs_["daily_ingest"].trigger) == "cron[hour='1,13', minute='30']"
    assert str(jobs_["ensure_models"].trigger) == "cron[hour='2', minute='0']"  # before the morning publication
    assert str(jobs_["publish_forecasts"].trigger) == "cron[hour='2,14', minute='30']"  # an hour after ingestion
    assert str(jobs_["monitor"].trigger) == "cron[hour='3,15', minute='0']"
    assert str(jobs_["alerts"].trigger) == "cron[hour='2,14', minute='45']"  # after publication
    assert all(j.max_instances == 1 and j.coalesce is True for j in jobs_.values())
