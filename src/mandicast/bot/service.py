"""User, subscription and login logic shared by the Telegram bot and the authenticated API.

Privacy: a user is a Telegram chat id and nothing else. One-time login codes and session tokens are random
(`secrets`), shown once, and stored only as SHA-256 hashes. Deleting a user removes everything linked to them.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from mandicast.api.source import EXCLUDED_BY_DEFAULT

CONDITIONS = {"price_above": "price at or above", "price_below": "price at or below",
              "forecast_change_pct": "7-day forecast moving by at least"}
MAX_PRICE = Decimal("10000000")  # ₹ per quintal; anything above is a typo
MAX_PCT = Decimal("100")


class SubscriptionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def inr(x) -> str:
    return "₹" + f"{float(x):,.0f}"


def get_or_create_user(s: Session, chat_id: int) -> int:
    return s.execute(text(
        "INSERT INTO users (telegram_chat_id) VALUES (:c) ON CONFLICT (telegram_chat_id) "
        "DO UPDATE SET last_seen_at = now() RETURNING id"), {"c": chat_id}).scalar_one()


def find_user(s: Session, chat_id: int) -> int | None:
    return s.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar_one_or_none()


def delete_user(s: Session, user_id: int) -> None:
    """Remove the user with their sessions, login codes, subscriptions and notification history (cascade)."""
    s.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})


def commodities(s: Session) -> list[tuple[int, str]]:
    return [tuple(r) for r in s.execute(text("SELECT id, name FROM commodities ORDER BY name")).all()]


def find_commodity(s: Session, name: str) -> tuple[int, str] | None:
    name = name.strip().lower()
    rows = commodities(s)
    exact = [r for r in rows if r[1].lower() == name]
    if exact:
        return exact[0]
    pref = [r for r in rows if r[1].lower().startswith(name)] if len(name) >= 3 else []
    return pref[0] if len(pref) == 1 else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_series(s: Session, commodity_id: int, query: str, limit: int = 8) -> list[dict]:
    """Series of a commodity whose mandi name contains `query`, most recently reporting first."""
    rows = s.execute(text(
        "SELECT ps.id AS series_id, m.id AS market_id, coalesce(m.display_name, m.name) AS market, "
        "coalesce(d.display_name, d.name) AS district, coalesce(st.display_name, st.name) AS state, ps.last_date, "
        "ps.eligible_for_forecast FROM price_series ps JOIN markets m ON m.id = ps.market_id "
        "JOIN districts d ON d.id = m.district_id JOIN states st ON st.id = d.state_id "
        "WHERE ps.commodity_id = :c AND coalesce(m.display_name, m.name) ILIKE :q ESCAPE '\\' "
        "ORDER BY ps.last_date DESC NULLS LAST, 3 LIMIT :n"), {"c": commodity_id, "q": _like(query), "n": limit}).all()
    return [dict(r._mapping) for r in rows]


def series_label(s: Session, series_id: int) -> dict | None:
    row = s.execute(text(
        "SELECT ps.id AS series_id, ps.commodity_id, c.name AS commodity, m.id AS market_id, "
        "coalesce(m.display_name, m.name) AS market, coalesce(d.display_name, d.name) AS district, "
        "coalesce(st.display_name, st.name) AS state FROM price_series ps JOIN commodities c ON c.id = ps.commodity_id "
        "JOIN markets m ON m.id = ps.market_id JOIN districts d ON d.id = m.district_id "
        "JOIN states st ON st.id = d.state_id WHERE ps.id = :s"), {"s": series_id}).one_or_none()
    return dict(row._mapping) if row else None


def latest_price(s: Session, series_id: int) -> dict | None:
    row = s.execute(text(
        "SELECT date, modal_price, min_price, max_price, variety, grade FROM price_observations WHERE series_id = :s "
        "AND is_primary AND NOT (flags && CAST(:ex AS text[])) ORDER BY date DESC LIMIT 1"),
        {"s": series_id, "ex": EXCLUDED_BY_DEFAULT}).one_or_none()
    return dict(row._mapping) if row else None


def validate_condition(condition_type: str, value) -> Decimal:
    if condition_type not in CONDITIONS:
        raise SubscriptionError("invalid_condition", "Condition must be price_above, price_below or "
                                                     "forecast_change_pct.")
    try:
        v = Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001 - any parse failure is a user input error
        raise SubscriptionError("invalid_value", "The value must be a number.") from None
    limit = MAX_PCT if condition_type == "forecast_change_pct" else MAX_PRICE
    if not (Decimal("0") < v <= limit):
        raise SubscriptionError("invalid_value", f"The value must be above 0 and at most {limit}.")
    return v


def add_subscription(s: Session, user_id: int, series_id: int, condition_type: str, value,
                     max_per_user: int) -> int:
    v = validate_condition(condition_type, value)
    if series_label(s, series_id) is None:
        raise SubscriptionError("series_not_found", "No such price series.")
    n = s.execute(text("SELECT count(*) FROM alert_subscriptions WHERE user_id = :u"), {"u": user_id}).scalar_one()
    if n >= max_per_user:
        raise SubscriptionError("too_many_alerts", f"You can have at most {max_per_user} alerts.")
    new_id = s.execute(text(
        "INSERT INTO alert_subscriptions (user_id, series_id, condition_type, threshold) VALUES (:u, :s, :c, :v) "
        "ON CONFLICT ON CONSTRAINT uq_alert_subscription DO NOTHING RETURNING id"),
        {"u": user_id, "s": series_id, "c": condition_type, "v": v}).scalar_one_or_none()
    if new_id is None:
        raise SubscriptionError("subscription_exists", "You already have this alert.")
    return new_id


def list_subscriptions(s: Session, user_id: int) -> list[dict]:
    rows = s.execute(text(
        "SELECT a.id, a.condition_type, a.threshold, a.active, a.created_at, a.series_id, ps.commodity_id, "
        "c.name AS commodity, m.id AS market_id, coalesce(m.display_name, m.name) AS market "
        "FROM alert_subscriptions a JOIN price_series ps ON ps.id = a.series_id "
        "JOIN commodities c ON c.id = ps.commodity_id JOIN markets m ON m.id = ps.market_id "
        "WHERE a.user_id = :u ORDER BY a.id"), {"u": user_id}).all()
    return [dict(r._mapping) for r in rows]


def delete_subscription(s: Session, user_id: int, sub_id: int) -> bool:
    """Only the owner can delete; another user's id behaves exactly like a missing id."""
    return s.execute(text("DELETE FROM alert_subscriptions WHERE id = :i AND user_id = :u RETURNING id"),
                     {"i": sub_id, "u": user_id}).scalar_one_or_none() is not None


def describe(sub: dict) -> str:
    value = (f"{float(sub['threshold']):g}%" if sub["condition_type"] == "forecast_change_pct"
             else f"{inr(sub['threshold'])}/quintal")
    return f"{sub['commodity']} at {sub['market']}: {CONDITIONS[sub['condition_type']]} {value}"


def history(s: Session, user_id: int, limit: int = 100) -> list[dict]:
    rows = s.execute(text(
        "SELECT id, subscription_id, status, message, created_at, sent_at FROM notification_history "
        "WHERE user_id = :u ORDER BY id DESC LIMIT :n"), {"u": user_id, "n": limit}).all()
    return [dict(r._mapping) for r in rows]


def issue_login_code(s: Session, user_id: int, minutes: int) -> str:
    code = secrets.token_urlsafe(24)
    s.execute(text("INSERT INTO login_codes (user_id, code_sha256, expires_at) VALUES (:u, :h, :e)"),
              {"u": user_id, "h": sha256(code), "e": datetime.now(UTC) + timedelta(minutes=minutes)})
    return code


def exchange_code(s: Session, code: str, session_days: int) -> tuple[str, datetime] | None:
    """Trade an unused, unexpired one-time code for a session token (the code is consumed)."""
    if not code or len(code) > 200:
        return None
    user_id = s.execute(text(
        "UPDATE login_codes SET used_at = now() WHERE code_sha256 = :h AND used_at IS NULL AND expires_at > now() "
        "RETURNING user_id"), {"h": sha256(code)}).scalar_one_or_none()
    if user_id is None:
        return None
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=session_days)
    s.execute(text("INSERT INTO user_sessions (user_id, token_sha256, expires_at) VALUES (:u, :h, :e)"),
              {"u": user_id, "h": sha256(token), "e": expires})
    return token, expires


def user_for_token(s: Session, token: str) -> int | None:
    if not token or len(token) > 200:
        return None
    return s.execute(text(
        "UPDATE user_sessions SET last_used_at = now() WHERE token_sha256 = :h AND expires_at > now() "
        "RETURNING user_id"), {"h": sha256(token)}).scalar_one_or_none()


def end_session(s: Session, token: str) -> None:
    s.execute(text("DELETE FROM user_sessions WHERE token_sha256 = :h"), {"h": sha256(token)})


def purge(s: Session, retention_days: int, today: date | None = None) -> dict:
    """Retention (docs/04 §3): expired sessions and login codes, notifications older than the retention period."""
    today = today or date.today()
    out = {
        "sessions": s.execute(text("DELETE FROM user_sessions WHERE expires_at < now()")).rowcount,
        "login_codes": s.execute(text("DELETE FROM login_codes WHERE expires_at < now() - interval '1 day'")).rowcount,
        "notifications": s.execute(text("DELETE FROM notification_history WHERE created_at < :d"),
                                   {"d": today - timedelta(days=retention_days)}).rowcount,
    }
    return out
