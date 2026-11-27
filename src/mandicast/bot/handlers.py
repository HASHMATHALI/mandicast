"""Telegram bot commands (private chats only, plain-text replies).

    /start /help /crops /price <crop> <mandi> /forecast <crop> <mandi>
    /alert <crop> <mandi> above|below <₹ per quintal>   /alert <crop> <mandi> forecast <percent>
    /alerts  /stop <alert id>  /login  /privacy  /delete

Every forecast message states the forecast, its range, the measured accuracy and that it is an estimate, not a
guarantee (requirements F10, honesty rules). Input is length-limited and validated; group chats are ignored so no
one's data is exposed in a group; each chat is limited to 20 commands a minute.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque

from sqlalchemy import Engine

from mandicast.api.errors import ApiError
from mandicast.api.routes import forecasts as api_forecasts
from mandicast.bot import service
from mandicast.bot.telegram import TelegramClient, TelegramError
from mandicast.config import Settings
from mandicast.db.session import session_scope

log = logging.getLogger(__name__)
MAX_INPUT = 200
RATE_PER_MINUTE = 20
HELP = (
    "MandiCast: official mandi prices (AGMARKNET, data.gov.in) and price forecasts with their measured accuracy.\n\n"
    "/crops – crops covered\n"
    "/price onion lasalgaon – latest reported price\n"
    "/forecast onion lasalgaon – 7- and 14-day forecast with range\n"
    "/alert onion lasalgaon above 2500 – message when the reported price is at or above ₹2,500/quintal\n"
    "/alert onion lasalgaon below 1800 – … at or below\n"
    "/alert onion lasalgaon forecast 5 – when the 7-day forecast moves 5% or more from the last price\n"
    "/alerts – your alerts   /stop 3 – remove alert 3\n"
    "/login – one-time link to the dashboard\n"
    "/privacy – what is stored   /delete – delete all your data\n\n"
    "Forecasts are estimates from past prices, not guarantees.")
PRIVACY = (
    "MandiCast stores only your Telegram chat id, the alerts you create and the alert messages sent to you "
    "(kept 12 months). No name, username or phone number is stored. /delete removes everything immediately.")
ALERT_RE = re.compile(r"^(?P<crop>\S+)\s+(?P<mandi>.+?)\s+(?P<kind>above|below|forecast)\s+(?P<value>[\d.,]+)%?$",
                      re.IGNORECASE)
KIND = {"above": ("price_above", "A"), "below": ("price_below", "B"), "forecast": ("forecast_change_pct", "F")}
CODE_KIND = {v[1]: v[0] for v in KIND.values()}


class Bot:
    def __init__(self, engine: Engine, client: TelegramClient, settings: Settings) -> None:
        self.engine, self.client, self.settings = engine, client, settings
        self._recent: dict[int, deque] = defaultdict(deque)

    # --- dispatch ---------------------------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            cq = update["callback_query"]
            chat = (cq.get("message") or {}).get("chat") or {}
            if chat.get("type") != "private":
                return
            self.client.answer_callback(cq["id"])
            if self._allowed(chat["id"]):
                self.on_callback(chat["id"], str(cq.get("data", ""))[:64])
            return
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private" or "text" not in msg:
            return
        if not self._allowed(chat["id"]):
            self.reply(chat["id"], "Too many requests; please wait a minute.")
            return
        self.on_message(chat["id"], msg["text"][:MAX_INPUT])

    def _allowed(self, chat_id: int) -> bool:
        q, now = self._recent[chat_id], time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MINUTE:
            return False
        q.append(now)
        return True

    def reply(self, chat_id: int, text: str, buttons=None) -> None:
        try:
            self.client.send_message(chat_id, text, buttons)
        except TelegramError as exc:
            log.warning("reply failed: %s", exc)

    # --- messages ---------------------------------------------------------------------------------------------

    def on_message(self, chat_id: int, text: str) -> None:
        cmd, _, arg = text.strip().partition(" ")
        cmd = cmd.split("@", 1)[0].lower()  # /price@MandiCastBot → /price
        arg = " ".join(arg.split())
        with session_scope(self.engine) as s:
            user_id = service.get_or_create_user(s, chat_id)
            if cmd in ("/start", "/help"):
                self.reply(chat_id, HELP if cmd == "/help" else "Welcome!\n\n" + HELP + "\n\n" + PRIVACY)
            elif cmd == "/privacy":
                self.reply(chat_id, PRIVACY)
            elif cmd == "/crops":
                self.reply(chat_id, "Crops: " + ", ".join(n for _, n in service.commodities(s)))
            elif cmd in ("/price", "/forecast"):
                self._lookup(s, chat_id, cmd, arg)
            elif cmd == "/alert":
                self._alert(s, chat_id, user_id, arg)
            elif cmd == "/alerts":
                self._list_alerts(s, chat_id, user_id)
            elif cmd == "/stop":
                if not arg.isdigit():
                    self.reply(chat_id, "Use /stop <alert number>, e.g. /stop 3 (see /alerts).")
                elif service.delete_subscription(s, user_id, int(arg)):
                    self.reply(chat_id, f"Alert {arg} removed.")
                else:
                    self.reply(chat_id, f"You have no alert {arg}.")
            elif cmd == "/login":
                code = service.issue_login_code(s, user_id, self.settings.login_code_minutes)
                self.reply(chat_id, f"Open this link within {self.settings.login_code_minutes} minutes to sign in to "
                                    f"the dashboard (it works once):\n{self.settings.dashboard_url}/login#code={code}")
            elif cmd == "/delete":
                self.reply(chat_id, "Delete your alerts, alert history and account? This cannot be undone.",
                           [[("Yes, delete everything", "x:del"), ("No", "x:no")]])
            else:
                self.reply(chat_id, "Unknown command. " + HELP)

    def _resolve(self, s, chat_id: int, arg: str, prefix: str, usage: str):
        crop, _, query = arg.partition(" ")
        commodity = service.find_commodity(s, crop) if crop else None
        if commodity is None:
            names = ", ".join(n for _, n in service.commodities(s))
            self.reply(chat_id, f"{usage}\nCrops: {names}")
            return None
        if len(query) < 2:
            self.reply(chat_id, f"{usage}\nType at least 2 letters of the mandi name.")
            return None
        found = service.search_series(s, commodity[0], query)
        if not found:
            self.reply(chat_id, f"No mandi matching '{query}' reports {commodity[1]}.")
            return None
        if len(found) > 1:
            self.reply(chat_id, f"Which mandi? ({len(found)} shown, most recently reporting first)",
                       [[(f"{f['market']}, {f['state']}", f"{prefix}{f['series_id']}")] for f in found])
            return None
        return found[0]["series_id"]

    def _lookup(self, s, chat_id: int, cmd: str, arg: str) -> None:
        usage = f"Use {cmd} <crop> <mandi>, e.g. {cmd} onion lasalgaon"
        sid = self._resolve(s, chat_id, arg, "p:" if cmd == "/price" else "f:", usage)
        if sid is not None:
            self.reply(chat_id, self.price_text(s, sid) if cmd == "/price" else self.forecast_text(s, sid))

    def _alert(self, s, chat_id: int, user_id: int, arg: str) -> None:
        m = ALERT_RE.match(arg)
        usage = ("Use /alert <crop> <mandi> above|below <₹ per quintal>, or /alert <crop> <mandi> forecast <percent>, "
                 "e.g. /alert onion lasalgaon above 2500")
        if not m:
            self.reply(chat_id, usage)
            return
        ctype, code = KIND[m["kind"].lower()]
        value = m["value"].replace(",", "")
        try:
            service.validate_condition(ctype, value)
        except service.SubscriptionError as exc:
            self.reply(chat_id, str(exc))
            return
        sid = self._resolve(s, chat_id, f"{m['crop']} {m['mandi']}", f"a:{code}:{value}:", usage)
        if sid is not None:
            self._create_alert(s, chat_id, user_id, sid, ctype, value)

    def _create_alert(self, s, chat_id: int, user_id: int, sid: int, ctype: str, value: str) -> None:
        try:
            new_id = service.add_subscription(s, user_id, sid, ctype, value, self.settings.max_alerts_per_user)
        except service.SubscriptionError as exc:
            self.reply(chat_id, str(exc))
            return
        sub = next(x for x in service.list_subscriptions(s, user_id) if x["id"] == new_id)
        note = (" Forecast alerts are checked when a new weekly forecast is published." if ctype ==
                "forecast_change_pct" else " Checked after each data update (twice a day).")
        self.reply(chat_id, f"Alert {new_id} created: {service.describe(sub)}.{note} /stop {new_id} removes it.")

    def _list_alerts(self, s, chat_id: int, user_id: int) -> None:
        subs = service.list_subscriptions(s, user_id)
        if not subs:
            self.reply(chat_id, "You have no alerts. Example: /alert onion lasalgaon above 2500")
            return
        lines = [f"{x['id']}. {service.describe(x)}" + ("" if x["active"] else " (paused)") for x in subs]
        self.reply(chat_id, "Your alerts:\n" + "\n".join(lines) + "\n\n/stop <number> removes one.")

    # --- buttons ----------------------------------------------------------------------------------------------

    def on_callback(self, chat_id: int, data: str) -> None:
        with session_scope(self.engine) as s:
            user_id = service.get_or_create_user(s, chat_id)
            kind, _, rest = data.partition(":")
            if kind in ("p", "f") and rest.isdigit():
                sid = int(rest)
                if service.series_label(s, sid) is None:
                    return
                self.reply(chat_id, self.price_text(s, sid) if kind == "p" else self.forecast_text(s, sid))
            elif kind == "a":
                code, value, sid = (rest.split(":") + ["", "", ""])[:3]
                if code in CODE_KIND and sid.isdigit():
                    self._create_alert(s, chat_id, user_id, int(sid), CODE_KIND[code], value)
            elif data == "x:del":
                service.delete_user(s, user_id)
                self.reply(chat_id, "All your data has been deleted. Send /start to use MandiCast again.")
            elif data == "x:no":
                self.reply(chat_id, "Nothing was deleted.")

    # --- texts ------------------------------------------------------------------------------------------------

    @staticmethod
    def _where(lbl: dict) -> str:
        return f"{lbl['commodity']} at {lbl['market']}, {lbl['district']}, {lbl['state']}"

    def price_text(self, s, series_id: int) -> str:
        lbl = service.series_label(s, series_id)
        p = service.latest_price(s, series_id)
        if p is None:
            return f"{self._where(lbl)}: no reported price."
        rng = (f" (range {service.inr(p['min_price'])}–{service.inr(p['max_price'])})"
               if p["min_price"] is not None and p["max_price"] is not None else "")
        return (f"{self._where(lbl)}\nLatest reported modal price: {service.inr(p['modal_price'])}/quintal{rng} on "
                f"{p['date']:%d %b %Y} ({p['variety']}, {p['grade']}).\n"
                "Source: AGMARKNET via data.gov.in (GODL-India), as reported by the market committee.")

    def forecast_text(self, s, series_id: int) -> str:
        lbl = service.series_label(s, series_id)
        try:
            f = api_forecasts.forecast(s, lbl["commodity_id"], lbl["market_id"], 14)
        except ApiError as exc:
            return f"{self._where(lbl)}: no forecast. {exc.message}"
        pts = {p["horizon_days"]: p for p in f["points"]}
        lines = [self._where(lbl),
                 f"Forecast issued {f['issued_on']:%d %b %Y}; last reported price "
                 f"{service.inr(f['last_observed']['modal_price'])}/quintal on {f['last_observed']['date']:%d %b}."]
        for h in (7, 14):
            p = pts.get(h)
            if p:
                i80 = p["intervals"][0]
                how = "repeats the last price" if p["served_by"] == "last_value" else "model"
                lines.append(f"Day {h} ({p['date']:%d %b}): {service.inr(p['modal_price'])}/quintal, 80% range "
                             f"{service.inr(i80['lower'])}–{service.inr(i80['upper'])} ({how}).")
        acc = f["accuracy"]
        if acc and acc["method"] == "model":
            lines.append(f"Measured on a past year (14 days ahead): typical error {acc['wape']:.1%} vs "
                         f"{acc['baseline_wape']:.1%} for repeating the last price; "
                         f"{acc['interval_coverage']['0.8']:.0%} of prices fell inside the 80% range.")
        lines += [f"Note: {w}" for w in f["warnings"][:3]]
        lines.append(f["disclaimer"])
        return "\n".join(lines)
