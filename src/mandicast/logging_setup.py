"""Structured logging (one JSON object per line) with a filter that masks known secrets.

Secrets are never passed to log calls on purpose; the filter is a second line of defence in case an exception
message or a URL ever carries one.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_SECRETS: set[str] = set()
_KEY_PARAM_RE = re.compile(r"(api-key=)[^&\s'\"]+", re.IGNORECASE)
_URL_PASSWORD_RE = re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)")
_TELEGRAM_TOKEN_RE = re.compile(r"(bot)?\d{6,12}:[A-Za-z0-9_-]{30,}")  # Telegram bot tokens, e.g. in API URLs


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    text = _KEY_PARAM_RE.sub(r"\1***", text)
    text = _TELEGRAM_TOKEN_RE.sub("***", text)
    return _URL_PASSWORD_RE.sub(r"\1***\2", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                   "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        for key in ("job_id", "commodity", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update({k: v for k, v in fields.items() if k not in payload})
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    from mandicast.config import get_settings

    s = get_settings()
    if s.data_gov_in_api_key:
        register_secret(s.data_gov_in_api_key.get_secret_value())
    if s.telegram_bot_token:
        register_secret(s.telegram_bot_token.get_secret_value())
    for url in (s.database_url, s.redis_url):
        m = re.search(r"://[^:/]*:([^@]+)@", url.get_secret_value()) if url else None
        if m:
            register_secret(m.group(1))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO; Telegram URLs contain the bot token, so keep it at WARNING.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
