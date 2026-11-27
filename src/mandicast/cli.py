"""Command line.

    python -m mandicast db-upgrade
    python -m mandicast db-roles                 (least-privilege role for the API and the bot; needs API_DB_PASSWORD)
    python -m mandicast load-raw [--commodity Onion ...] [--no-clean]
    python -m mandicast backfill [--commodity Onion ...]
    python -m mandicast daily [--commodity Onion ...]
    python -m mandicast clean [--commodity Onion ...] [--since 2026-01-01]
    python -m mandicast status
    python -m mandicast worker
    python -m mandicast retrain [--commodity Onion ...] [--cutoff 2026-10-01] [--no-promote]   (alias: train-model)
    python -m mandicast rollback --commodity Onion --reason "..."
    python -m mandicast ensure-models
    python -m mandicast publish-forecasts [--commodity Onion ...] [--origin 2026-11-23] [--dry-run]
    python -m mandicast monitor
    python -m mandicast bot                      (Telegram long polling; needs TELEGRAM_BOT_TOKEN)
    python -m mandicast alerts                   (evaluate alerts and send due messages)
    python -m mandicast privacy-purge            (retention: expired sessions, old notifications)
    python -m mandicast api [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from mandicast import pipeline
from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.db.session import get_engine
from mandicast.jobs import JobLockedError
from mandicast.logging_setup import setup_logging

log = logging.getLogger("mandicast")


def _status(engine) -> dict:
    from sqlalchemy import text
    with engine.connect() as c:
        jobs = [dict(r._mapping) for r in c.execute(text(
            "SELECT id, kind, status, started_at, finished_at, rows_fetched, rows_inserted, rows_duplicate, "
            "rows_rejected, left(error, 200) AS error FROM ingestion_jobs ORDER BY id DESC LIMIT 5"))]
        per = [dict(r._mapping) for r in c.execute(text(
            "SELECT c.name AS commodity, count(ps.id) AS series, count(*) FILTER (WHERE ps.eligible_for_forecast) "
            "AS eligible, max(ps.last_date) AS latest, max(ps.eligibility_origin) AS origin "
            "FROM commodities c LEFT JOIN price_series ps ON ps.commodity_id = c.id GROUP BY c.name ORDER BY 1"))]
        totals = dict(c.execute(text(
            "SELECT (SELECT count(*) FROM source_observations) AS source_rows, "
            "(SELECT count(*) FROM source_observations WHERE superseded_by_raw_page_id IS NOT NULL) AS superseded, "
            "(SELECT count(*) FROM price_observations) AS cleaned_rows, "
            "(SELECT count(*) FROM price_observations WHERE is_primary) AS primary_rows")).one()._mapping)
    return {"totals": totals, "commodities": per, "recent_jobs": jobs}


def _keep_awake() -> None:
    if sys.platform == "win32":
        # Per-process request (like a media player) so a long run is not interrupted by idle sleep;
        # Windows drops it when the process exits. Changes no settings.
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | SYSTEM_REQUIRED


def _serving(engine, args, commodities: list[str]) -> int:
    from mandicast.db.session import session_scope
    from mandicast.jobs import SERVING_LOCK_ID, run_job
    from mandicast.serving import lifecycle, monitor, publish, registry
    try:
        if args.cmd == "publish-forecasts" and args.dry_run:
            for c in commodities:
                r = publish.preview(engine, c, args.origin)
                print(json.dumps({k: v for k, v in r.items() if k != "calibration"}, indent=2, default=str))
            return 0
        if args.cmd == "publish-forecasts":
            job = publish.publish(engine, commodities, args.origin)
        elif args.cmd == "monitor":
            job = monitor.monitor(engine, commodities)
        elif args.cmd == "ensure-models":
            job = lifecycle.ensure_models(engine, commodities)
        elif args.cmd == "rollback":
            with run_job(engine, "rollback", {"commodities": commodities, "reason": args.reason},
                         lock_id=SERVING_LOCK_ID) as j, session_scope(engine) as s:
                for c in commodities:
                    j.report[c] = registry.rollback(s, c, args.reason)
                    print(c, j.report[c])
            job = j.id
        else:  # retrain / train-model: always through validation (C10a) and the promotion gate (C10b)
            with run_job(engine, "retrain", {"commodities": commodities, "cutoff": args.cutoff},
                         lock_id=SERVING_LOCK_ID) as j:
                for c in commodities:
                    cutoff = args.cutoff
                    if cutoff is None:
                        with session_scope(engine) as s:
                            origin, _ = publish.choose_origin(s, c, date.today())
                        cutoff = registry.quarter_start(origin)
                    res = lifecycle.retrain(engine, c, cutoff, promote_if_passed=not args.no_promote)
                    j.report[c] = res
                    print(c, {k: v for k, v in res.items() if k != "gate"},
                          "| failed:", res.get("gate", {}).get("failed"))
            job = j.id
        print(f"job {job} succeeded; report: reports/quality/job_{job:06d}_*.json")
        return 0
    except JobLockedError as exc:
        log.warning("%s", exc)
        return 3


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mandicast", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("db-upgrade", help="apply database migrations")
    sub.add_parser("db-roles", help="create or update the least-privilege role for the API and the bot")
    for name in ("load-raw", "backfill", "daily", "clean"):
        sp = sub.add_parser(name)
        sp.add_argument("--commodity", action="append", help="repeatable; default: all configured commodities")
        if name == "load-raw":
            sp.add_argument("--no-clean", action="store_true")
        if name == "clean":
            sp.add_argument("--since", type=date.fromisoformat)
    sub.add_parser("status", help="row counts, eligibility and recent jobs")
    sub.add_parser("worker", help="run the scheduler (daily ingestion, forecast publication)")
    for name in ("retrain", "train-model"):
        tm = sub.add_parser(name, help="train the frozen method for a quarter; validate, gate, promote or reject")
        tm.add_argument("--commodity", action="append")
        tm.add_argument("--cutoff", type=date.fromisoformat,
                        help="quarter start (default: quarter of the latest Monday with complete data)")
        tm.add_argument("--no-promote", action="store_true", help="register and gate, but do not promote")
    rb = sub.add_parser("rollback", help="restore the model the production model replaced (C10h)")
    rb.add_argument("--commodity", action="append", required=True)
    rb.add_argument("--reason", required=True)
    sub.add_parser("ensure-models", help="retrain where no production model is valid for the current quarter")
    sub.add_parser("monitor", help="live accuracy of published forecasts and missing-update checks")
    sub.add_parser("bot", help="run the Telegram bot (long polling)")
    sub.add_parser("alerts", help="evaluate alert subscriptions and send due messages")
    sub.add_parser("privacy-purge", help="delete expired sessions and login codes, and old notifications")
    pf = sub.add_parser("publish-forecasts", help="publish forecasts (model or last-value fallback)")
    pf.add_argument("--commodity", action="append")
    pf.add_argument("--origin", type=date.fromisoformat, help="a Monday with complete data (default: the latest)")
    pf.add_argument("--dry-run", action="store_true", help="show what would be published; write nothing")
    ap_ = sub.add_parser("api", help="run the HTTP API (uvicorn)")
    ap_.add_argument("--host", default="127.0.0.1")
    ap_.add_argument("--port", type=int, default=8000)
    ap_.add_argument("--workers", type=int, default=1)
    args = p.parse_args(argv)

    setup_logging()
    settings = get_settings()
    if args.cmd == "db-upgrade":
        from alembic import command
        from alembic.config import Config
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")
        return 0
    if args.cmd == "db-roles":
        from mandicast.db.roles import ensure_api_role
        if settings.api_db_password is None or not settings.api_db_password.get_secret_value():
            log.error("API_DB_PASSWORD is not set; nothing changed")
            return 2
        result = ensure_api_role(get_engine(), settings.api_db_role, settings.api_db_password.get_secret_value())
        print(json.dumps(result))
        return 0
    if args.cmd == "api":
        import uvicorn
        uvicorn.run("mandicast.api.app:create_app", factory=True, host=args.host, port=args.port,
                    workers=args.workers, access_log=False, server_header=False, proxy_headers=False)
        return 0
    engine = get_engine()
    if args.cmd == "bot":
        from mandicast.bot.runner import run_bot
        run_bot(engine, settings)
        return 0
    if args.cmd == "alerts":
        from mandicast.bot.alerts import run_alerts
        from mandicast.bot.runner import client_from_settings
        client = client_from_settings(settings)
        if client is None:
            log.warning("TELEGRAM_BOT_TOKEN not set: alerts are evaluated and queued but not sent")
        job = run_alerts(engine, client)
        print(f"job {job} succeeded; report: reports/quality/job_{job:06d}_alerts.json")
        return 0
    if args.cmd == "privacy-purge":
        from mandicast.bot.service import purge
        from mandicast.db.session import session_scope
        with session_scope(engine) as s:
            print(purge(s, settings.notification_retention_days))
        return 0
    if args.cmd == "status":
        print(json.dumps(_status(engine), indent=2, default=str))
        return 0
    if args.cmd == "worker":
        from mandicast.scheduler import run_worker
        run_worker(engine, settings)
        return 0
    commodities = getattr(args, "commodity", None) or settings.commodities
    _keep_awake()
    if args.cmd in ("retrain", "train-model", "publish-forecasts", "rollback", "ensure-models", "monitor"):
        return _serving(engine, args, commodities)
    try:
        if args.cmd == "load-raw":
            job = pipeline.load_raw(engine, settings, commodities, clean=not args.no_clean)
        elif args.cmd == "backfill":
            job = pipeline.backfill(engine, settings, commodities)
        elif args.cmd == "daily":
            job = pipeline.daily(engine, settings, commodities)
        else:
            job = pipeline.clean(engine, settings, commodities, args.since)
    except JobLockedError as exc:
        log.warning("%s", exc)
        return 3
    print(f"job {job} succeeded; report: reports/quality/job_{job:06d}_*.json")
    return 0
