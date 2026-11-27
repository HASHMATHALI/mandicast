"""Scheduled worker (APScheduler).

The source resource was last updated at 23:20 UTC and the current-day resource at 05:00 UTC in Phase 1, and
mandis report late, so the daily job runs twice a day (01:30 and 13:30 UTC). Each run re-fetches the last week of
arrival dates, so a missed run is caught up by the next one. Runs never overlap: the job holds a database lock,
and APScheduler is set to one instance at a time with coalescing of missed runs.

Phase 4–5 jobs, after each morning ingestion (UTC):
- 02:00 `ensure_models`: if no production model is valid for the latest complete origin (a new quarter), retrain
  through validation and the promotion gate (methodology C10a, C10b, C10g). A no-op on most days.
- 02:30 and 14:30 `publish_forecasts`: the latest complete Monday, once; model or last-value fallback (C10c).
- 03:00 and 15:00 `monitor`: live accuracy of published forecasts and missing-update checks (C10f).
- 02:45 and 14:45 `alerts` (Phase 6): evaluate subscriptions after each ingestion and publication; send via Telegram
  (messages stay queued when no bot token is configured).
- 04:00 `privacy_purge`: expired sessions and login codes, notifications older than 12 months.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine

from mandicast import pipeline
from mandicast.config import Settings
from mandicast.jobs import JobLockedError

log = logging.getLogger(__name__)


def daily_run(engine: Engine, settings: Settings) -> None:
    try:
        pipeline.daily(engine, settings, settings.commodities)
    except JobLockedError:
        log.warning("daily run skipped: another run holds the lock")
    except Exception:
        # The failure is already recorded in ingestion_jobs; keep the worker alive for the next run.
        log.exception("daily run failed")


def publish_run(engine: Engine, settings: Settings) -> None:
    from mandicast.serving.publish import publish
    try:
        publish(engine, settings.commodities)
    except JobLockedError:
        log.warning("forecast publication skipped: another serving run holds the lock")
    except Exception:
        log.exception("forecast publication failed")  # recorded in ingestion_jobs; retried at the next run


def ensure_models_run(engine: Engine, settings: Settings) -> None:
    from mandicast.serving.lifecycle import ensure_models
    try:
        ensure_models(engine, settings.commodities)
    except JobLockedError:
        log.warning("model check skipped: another serving run holds the lock")
    except Exception:
        log.exception("model check failed")


def monitor_run(engine: Engine, settings: Settings) -> None:
    from mandicast.serving.monitor import monitor
    try:
        monitor(engine, settings.commodities)
    except JobLockedError:
        log.warning("monitoring skipped: another serving run holds the lock")
    except Exception:
        log.exception("monitoring failed")


def alerts_run(engine: Engine, settings: Settings) -> None:
    from mandicast.bot.alerts import run_alerts
    from mandicast.bot.runner import client_from_settings
    try:
        run_alerts(engine, client_from_settings(settings))
    except JobLockedError:
        log.warning("alerts skipped: another serving run holds the lock")
    except Exception:
        log.exception("alerts failed")


def purge_run(engine: Engine, settings: Settings) -> None:
    from mandicast.bot.service import purge
    from mandicast.db.session import session_scope
    try:
        with session_scope(engine) as s:
            log.info("privacy purge: %s", purge(s, settings.notification_retention_days))
    except Exception:
        log.exception("privacy purge failed")


def build_scheduler(engine: Engine, settings: Settings) -> BlockingScheduler:
    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                           "misfire_grace_time": 3600})
    sched.add_job(daily_run, CronTrigger(hour="1,13", minute=30, timezone="UTC"), args=[engine, settings],
                  id="daily_ingest", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(publish_run, CronTrigger(hour="2,14", minute=30, timezone="UTC"), args=[engine, settings],
                  id="publish_forecasts", replace_existing=True, max_instances=1, coalesce=True,
                  misfire_grace_time=3600)
    sched.add_job(ensure_models_run, CronTrigger(hour=2, minute=0, timezone="UTC"), args=[engine, settings],
                  id="ensure_models", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(monitor_run, CronTrigger(hour="3,15", minute=0, timezone="UTC"), args=[engine, settings],
                  id="monitor", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(alerts_run, CronTrigger(hour="2,14", minute=45, timezone="UTC"), args=[engine, settings],
                  id="alerts", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(purge_run, CronTrigger(hour=4, minute=0, timezone="UTC"), args=[engine, settings],
                  id="privacy_purge", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=3600)
    return sched


def run_worker(engine: Engine, settings: Settings) -> None:
    sched = build_scheduler(engine, settings)
    log.info("worker started; jobs: %s", [(j.id, str(j.trigger)) for j in sched.get_jobs()])
    sched.start()
