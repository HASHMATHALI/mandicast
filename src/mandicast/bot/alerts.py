"""Alert evaluation and delivery (requirements F10; docs/03 §3 step 6).

- **Price alerts** (`price_above` / `price_below`): evaluated on the latest unflagged primary report of the series.
  A message is created when the condition holds for a report date newer than the last one evaluated and did not
  hold before (so a price that stays above the threshold produces one message, not one per day). Key:
  `sub:<id>:price:<report date>`.
- **Forecast alerts** (`forecast_change_pct`): on each new publication for the crop, the 7-day forecast of the
  series is compared with the last reported price; a move of at least the threshold (either direction) creates a
  message. Key: `sub:<id>:forecast:<run id>`.
- Messages are inserted with their idempotency key (`ON CONFLICT DO NOTHING`), then sent; a re-run never sends a
  message twice. Failed sends are retried up to 5 times in later runs. If Telegram says the user blocked the bot,
  that user's alerts are paused.
- Wording states facts literally and calls forecasts estimates (docs/07 §6).
"""

from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy import Engine, text

from mandicast.api.source import EXCLUDED_BY_DEFAULT
from mandicast.bot.service import inr
from mandicast.bot.telegram import TelegramClient, TelegramError
from mandicast.db.session import session_scope
from mandicast.jobs import SERVING_LOCK_ID, run_job

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
SEND_INTERVAL_S = 0.05  # ≤ 20 messages/second, below Telegram's global limit


def _queue(s, sub_id: int, user_id: int, key: str, message: str) -> bool:
    return s.execute(text(
        "INSERT INTO notification_history (subscription_id, user_id, idempotency_key, message) "
        "VALUES (:s, :u, :k, :m) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"),
        {"s": sub_id, "u": user_id, "k": key, "m": message}).scalar_one_or_none() is not None


def evaluate_price_alerts(s, today: date) -> int:
    subs = s.execute(text(
        "SELECT a.id, a.user_id, a.series_id, a.condition_type, a.threshold, a.last_evaluated_date, a.last_condition, "
        "c.name AS commodity, coalesce(m.display_name, m.name) AS market, lp.date AS report_date, "
        "lp.modal_price FROM alert_subscriptions a JOIN price_series ps ON ps.id = a.series_id "
        "JOIN commodities c ON c.id = ps.commodity_id JOIN markets m ON m.id = ps.market_id "
        "CROSS JOIN LATERAL (SELECT po.date, po.modal_price FROM price_observations po "
        "  WHERE po.series_id = a.series_id "
        "  AND po.is_primary AND po.date <= :today AND NOT (po.flags && CAST(:ex AS text[])) "
        "  ORDER BY po.date DESC LIMIT 1) lp "
        "WHERE a.active AND a.condition_type IN ('price_above', 'price_below')"),
        {"today": today, "ex": EXCLUDED_BY_DEFAULT}).all()
    queued = 0
    for a in subs:
        if a.last_evaluated_date is not None and a.report_date <= a.last_evaluated_date:
            continue
        above = a.condition_type == "price_above"
        cond = a.modal_price >= a.threshold if above else a.modal_price <= a.threshold
        if cond and not a.last_condition:
            side = "at or above" if above else "at or below"
            msg = (f"MandiCast alert {a.id}: the reported modal price of {a.commodity} at {a.market} on "
                   f"{a.report_date:%d %b %Y} was {inr(a.modal_price)}/quintal, {side}"
                   f" your threshold of {inr(a.threshold)}. Source: AGMARKNET (data.gov.in), as reported by the market "
                   f"committee. /stop {a.id} removes this alert.")
            queued += _queue(s, a.id, a.user_id, f"sub:{a.id}:price:{a.report_date}", msg)
        s.execute(text("UPDATE alert_subscriptions SET last_evaluated_date = :d, last_condition = :c WHERE id = :i"),
                  {"d": a.report_date, "c": bool(cond), "i": a.id})
    return queued


def evaluate_forecast_alerts(s) -> int:
    subs = s.execute(text(
        "SELECT a.id, a.user_id, a.series_id, a.threshold, a.last_forecast_run_id, c.name AS commodity, "
        "coalesce(m.display_name, m.name) AS market, run.id AS run_id, run.origin, f.point, f.last_price, f.lower80, "
        "f.upper80, f.target_date, f.served_by FROM alert_subscriptions a JOIN price_series ps ON ps.id = a.series_id "
        "JOIN commodities c ON c.id = ps.commodity_id JOIN markets m ON m.id = ps.market_id "
        "CROSS JOIN LATERAL (SELECT fr.id, fr.origin FROM forecast_runs fr WHERE fr.commodity_id = ps.commodity_id "
        "  ORDER BY fr.origin DESC, fr.id DESC LIMIT 1) run "
        "JOIN forecasts f ON f.run_id = run.id AND f.series_id = a.series_id AND f.horizon_days = 7 "
        "WHERE a.active AND a.condition_type = 'forecast_change_pct' "
        "AND (a.last_forecast_run_id IS NULL OR a.last_forecast_run_id < run.id)")).all()
    queued = 0
    for a in subs:
        change = float(a.point) / float(a.last_price) - 1
        if abs(change) * 100 >= float(a.threshold):
            how = ("This repeats the last reported price (no model was available)." if a.served_by == "last_value"
                   else "This is an estimate from past prices, not a guarantee.")
            msg = (f"MandiCast alert {a.id}: the 7-day forecast for {a.commodity} at {a.market} "
                   f"({a.target_date:%d %b %Y}) is {inr(a.point)}/quintal, {abs(change):.0%} "
                   f"{'above' if change > 0 else 'below'} the last reported price of {inr(a.last_price)}. "
                   "The 80% range "
                   f"is {inr(a.lower80)}–{inr(a.upper80)}. {how} /stop {a.id} removes this alert.")
            queued += _queue(s, a.id, a.user_id, f"sub:{a.id}:forecast:{a.run_id}", msg)
        s.execute(text("UPDATE alert_subscriptions SET last_forecast_run_id = :r WHERE id = :i"),
                  {"r": a.run_id, "i": a.id})
    return queued


def send_pending(engine: Engine, client: TelegramClient | None) -> dict:
    """Send queued messages (and retry failed ones). Without a client (no token) messages stay pending."""
    out = {"sent": 0, "failed": 0, "blocked_users": 0, "pending": 0}
    with session_scope(engine) as s:
        rows = s.execute(text(
            "SELECT n.id, n.user_id, n.message, n.attempts, u.telegram_chat_id FROM notification_history n "
            "JOIN users u ON u.id = n.user_id WHERE n.status = 'pending' OR (n.status = 'failed' AND n.attempts < :m) "
            "ORDER BY n.id"), {"m": MAX_ATTEMPTS}).all()
    if client is None:
        out["pending"] = len(rows)
        return out
    blocked: set[int] = set()
    for r in rows:
        if r.user_id in blocked:
            continue
        try:
            client.send_message(r.telegram_chat_id, r.message)
            status, error = "sent", None
            out["sent"] += 1
        except TelegramError as exc:
            status, error = "failed", exc.description
            out["failed"] += 1
            if exc.blocked:
                blocked.add(r.user_id)
        with session_scope(engine) as s:
            s.execute(text(
                "UPDATE notification_history SET status = :st, attempts = attempts + 1, error = :e, "
                "sent_at = CASE WHEN :sent THEN now() ELSE sent_at END WHERE id = :i"),
                {"st": status, "e": error, "i": r.id, "sent": status == "sent"})
        time.sleep(SEND_INTERVAL_S)
    if blocked:
        with session_scope(engine) as s:
            s.execute(text("UPDATE alert_subscriptions SET active = false WHERE user_id = ANY(:u)"),
                      {"u": list(blocked)})
        out["blocked_users"] = len(blocked)
    return out


def run_alerts(engine: Engine, client: TelegramClient | None, today: date | None = None) -> int:
    """Evaluate every active subscription and send what is due. Returns the job id."""
    today = today or date.today()
    with run_job(engine, "alerts", {"today": today, "telegram": client is not None}, lock_id=SERVING_LOCK_ID) as job:
        with session_scope(engine) as s:
            job.report["queued_price"] = evaluate_price_alerts(s, today)
            job.report["queued_forecast"] = evaluate_forecast_alerts(s)
        job.report["delivery"] = send_pending(engine, client)
        log.info("alerts: %s", job.report)
    return job.id
