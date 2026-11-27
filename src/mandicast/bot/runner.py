"""Long-polling loop for the bot (`mandicast bot`). No public webhook or open port is needed."""

from __future__ import annotations

import logging
import time

from sqlalchemy import Engine

from mandicast.bot.handlers import Bot
from mandicast.bot.telegram import TelegramClient, TelegramError
from mandicast.config import Settings

log = logging.getLogger(__name__)


def client_from_settings(settings: Settings, transport=None) -> TelegramClient | None:
    if settings.telegram_bot_token is None or not settings.telegram_bot_token.get_secret_value():
        return None
    return TelegramClient(settings.telegram_bot_token.get_secret_value(), settings.telegram_api_base,
                          transport=transport)


def poll_once(bot: Bot, offset: int | None, timeout_s: int = 30) -> int | None:
    """Fetch and handle one batch of updates; returns the next offset. One bad update never stops the loop."""
    for update in bot.client.get_updates(offset, timeout_s):
        offset = update["update_id"] + 1
        try:
            bot.handle_update(update)
        except Exception:  # noqa: BLE001 - logged (redacted); the loop must keep serving other users
            log.exception("update %s failed", update.get("update_id"))
    return offset


def run_bot(engine: Engine, settings: Settings) -> None:
    client = client_from_settings(settings)
    if client is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env (create a bot with @BotFather; see .env.example)")
    me = client.call("getMe")
    log.info("bot started as @%s (long polling)", me.get("username"))
    bot, offset = Bot(engine, client, settings), None
    while True:
        try:
            offset = poll_once(bot, offset)
        except TelegramError as exc:
            log.warning("polling error: %s; retrying in 5 s", exc)
            time.sleep(5)
