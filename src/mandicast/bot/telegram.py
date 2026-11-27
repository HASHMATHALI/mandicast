"""Minimal Telegram Bot API client (long polling and sending), on httpx.

- The bot token appears only in request URLs. It is never put in exceptions or log messages: errors carry the
  method name and Telegram's description only, httpx's URL logging is kept at WARNING (logging_setup), and the
  redaction filter also masks anything shaped like a bot token.
- 429 responses are retried after Telegram's `retry_after`; network errors and 5xx with backoff.
- Messages are sent as plain text (no parse mode), so mandi names or user input can never inject formatting.
- `transport` lets tests replace the network with httpx.MockTransport.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)
MAX_TEXT = 4096


class TelegramError(RuntimeError):
    def __init__(self, method: str, description: str, status: int | None = None) -> None:
        super().__init__(f"Telegram {method} failed: {description}")
        self.method, self.description, self.status = method, description, status

    @property
    def blocked(self) -> bool:
        """The user blocked the bot or deleted the chat; messages to them will keep failing."""
        return self.status == 403


class TelegramClient:
    def __init__(self, token: str, base: str = "https://api.telegram.org", transport: httpx.BaseTransport | None = None,
                 timeout_s: float = 40.0, retries: int = 4) -> None:
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
        self._url = f"{base.rstrip('/')}/bot{token}/"
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self.retries = retries

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = self._http.post(self._url + method, json=params)
            except httpx.HTTPError as exc:
                if attempt == self.retries:
                    raise TelegramError(method, f"network error ({type(exc).__name__})") from None
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"ok": False, "description": f"non-JSON response (HTTP {r.status_code})"}
            if body.get("ok"):
                return body.get("result")
            desc = str(body.get("description", "unknown error"))[:300]
            if r.status_code == 429 and attempt < self.retries:
                wait = float((body.get("parameters") or {}).get("retry_after", delay))
                time.sleep(min(wait, 60))
                continue
            if r.status_code >= 500 and attempt < self.retries:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise TelegramError(method, desc, r.status_code)
        raise TelegramError(method, "retries exhausted")

    def get_updates(self, offset: int | None, timeout_s: int = 30) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout_s,
                         allowed_updates=["message", "callback_query"]) or []

    def send_message(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> dict:
        markup = None
        if buttons:
            markup = {"inline_keyboard": [[{"text": t[:64], "callback_data": d[:64]} for t, d in row]
                                          for row in buttons]}
        return self.call("sendMessage", chat_id=chat_id, text=text[:MAX_TEXT], reply_markup=markup,
                         disable_web_page_preview=True)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
