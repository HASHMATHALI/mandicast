"""Phase 6: Telegram client, bot commands, alert evaluation and delivery (idempotent), dashboard sign-in and the
authenticated API, privacy (deletion, retention, no caching of personal data).

Telegram is replaced by httpx.MockTransport (a test double that records requests; nothing leaves the machine).
Prices come from the real fixture rows; the model version, run and forecast values are synthetic TEST fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from conftest import FakeClient, db_url_for_tests, real_rows
from fastapi.testclient import TestClient
from sqlalchemy import text

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.api.app import create_app, read_only_engine
from mandicast.bot import alerts, service
from mandicast.bot.handlers import Bot
from mandicast.bot.runner import poll_once
from mandicast.bot.telegram import TelegramClient, TelegramError
from mandicast.config import Settings
from mandicast.db.session import session_scope
from mandicast.logging_setup import redact

TOKEN = "123456789:TEST-token-for-the-mock-transport-only-xx"  # noqa: S105 - fake token for the mock
ORIGIN = date(2025, 11, 17)
TODAY = date(2025, 11, 21)


class FakeTelegram:
    """Records Bot API calls; `fail` maps a chat id to an (HTTP status, description) error for sendMessage."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[int, tuple[int, str]] = {}
        self.updates: list[dict] = []
        self.rate_limit_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method == "sendMessage":
            if self.rate_limit_once:
                self.rate_limit_once = False
                return httpx.Response(429, json={"ok": False, "description": "Too Many Requests: retry after 1",
                                                 "parameters": {"retry_after": 1}})
            if body.get("chat_id") in self.fail:
                status, desc = self.fail[body["chat_id"]]
                return httpx.Response(status, json={"ok": False, "error_code": status, "description": desc})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": ups})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self, chat_id: int | None = None) -> list[str]:
        return [b["text"] for m, b in self.calls if m == "sendMessage" and (chat_id is None or b["chat_id"] == chat_id)]

    def buttons(self) -> list[list[dict]]:
        last = [b for m, b in self.calls if m == "sendMessage"][-1]
        return last.get("reply_markup", {}).get("inline_keyboard", [])


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr("mandicast.bot.telegram.time.sleep", lambda s: None)
    monkeypatch.setattr("mandicast.bot.alerts.time.sleep", lambda s: None)
    fake = FakeTelegram()
    return fake, TelegramClient(TOKEN, transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "REPORT_DIR", tmp_path / "reports")
    s = Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                 request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30, redis_url=None,
                 dashboard_url="https://dash.example.test", api_rate_limit_per_minute=10_000)
    pipeline.backfill(db, s, ["Soyabean"], client=FakeClient(real_rows()))
    with db.begin() as c:
        cid = c.execute(text("SELECT id FROM commodities WHERE name = 'Soyabean'")).scalar_one()
        markets = dict(c.execute(text("SELECT name, id FROM markets")).all())
        series = dict(c.execute(text("SELECT market_id, id FROM price_series")).all())
        sehore = series[markets["sehore"]]
        mv = c.execute(text(
            "INSERT INTO model_versions (version, kind, commodity_id, horizon_days, training_cutoff, valid_from, "
            "valid_to, artifact_path, artifact_sha256, code_sha256, params, training, evaluation, interval_method, "
            "status) VALUES ('test-v', 'lightgbm_global', :c, 14, '2025-10-01', '2025-10-01', '2025-12-31', 'x', :z, "
            ":z, '{}', '{\"best_iteration\": 50}', CAST(:ev AS jsonb), 'conditional', 'production') RETURNING id"),
            {"c": cid, "z": "0" * 64, "ev": json.dumps({
                "period": "TEST FIXTURE", "report": "n/a",
                "served": [{"h": h, "n": 10, "wape": 0.05, "mase_vs_last_value": 0.9, "dir_acc_moves_ge_2pct": 0.7,
                            "coverage80": 0.8, "coverage90": 0.9} for h in (7, 14)],
                "last_value": [{"h": h, "n": 10, "wape": 0.06} for h in (7, 14)]})}).scalar_one()
        run = c.execute(text(
            "INSERT INTO forecast_runs (commodity_id, model_version_id, origin, data_complete_through, "
            "series_eligible, series_forecast, calibration) VALUES (:c, :m, :o, :d, 1, 1, '{}') RETURNING id"),
            {"c": cid, "m": mv, "o": ORIGIN, "d": ORIGIN + timedelta(days=2)}).scalar_one()
        ld, lp = c.execute(text("SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary "
                                "AND date <= :o ORDER BY date DESC LIMIT 1"), {"s": sehore, "o": ORIGIN}).one()
        for h in range(1, 15):
            p = round(float(lp) * (1 + 0.001 * h), 2)
            c.execute(text(
                "INSERT INTO forecasts (run_id, series_id, model_version_id, issued_on, target_date, horizon_days, "
                "last_date, last_price, point, lower80, upper80, lower90, upper90, model_point, served_by) VALUES "
                "(:r, :s, :m, :o, :t, :h, :ld, :lp, :p, :l8, :u8, :l9, :u9, :p, 'model')"),
                {"r": run, "s": sehore, "m": mv, "o": ORIGIN, "t": ORIGIN + timedelta(days=h), "h": h, "ld": ld,
                 "lp": lp, "p": p, "l8": round(p * 0.9, 2), "u8": round(p * 1.1, 2), "l9": round(p * 0.85, 2),
                 "u9": round(p * 1.15, 2)})
    return {"settings": s, "commodity_id": cid, "sehore_market": markets["sehore"], "sehore": sehore,
            "ashta": series[markets["ashta"]], "last_price": float(lp)}


def _msg(chat_id: int, txt: str, chat_type: str = "private", update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": chat_type}, "text": txt}}


def _cb(chat_id: int, data: str) -> dict:
    return {"update_id": 99, "callback_query": {"id": "cb1", "data": data,
                                                 "message": {"chat": {"id": chat_id, "type": "private"}}}}


# --- Telegram client -------------------------------------------------------------------------------------------

def test_client_retries_rate_limits_and_never_puts_the_token_in_errors(tg):
    fake, client = tg
    fake.rate_limit_once = True
    client.send_message(5, "hello")
    assert [m for m, _ in fake.calls] == ["sendMessage", "sendMessage"]  # retried after retry_after
    fake.fail[6] = (403, "Forbidden: bot was blocked by the user")
    with pytest.raises(TelegramError) as exc:
        client.send_message(6, "x")
    assert exc.value.blocked and TOKEN not in str(exc.value)
    assert TOKEN not in redact(f"POST https://api.telegram.org/bot{TOKEN}/sendMessage")


def test_client_refuses_to_start_without_a_token():
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        TelegramClient("")


# --- bot commands ----------------------------------------------------------------------------------------------

@pytest.mark.db
def test_start_registers_only_the_chat_id_and_groups_are_ignored(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/start"))
    bot.handle_update(_msg(2002, "/start", chat_type="group"))
    with db.connect() as c:
        users = c.execute(text("SELECT telegram_chat_id FROM users")).scalars().all()
        cols = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'")
                         ).scalars().all()
    assert users == [1001] and fake.sent(2002) == []
    assert set(cols) == {"id", "telegram_chat_id", "language", "created_at", "last_seen_at"}  # no name, username
    assert "No name, username or phone number is stored" in fake.sent(1001)[0]


@pytest.mark.db
def test_price_lookup_with_disambiguation_buttons(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/price soyabean sehore"))
    with session_scope(db) as s:
        latest = service.latest_price(s, world["sehore"])
    assert service.inr(latest["modal_price"]) in fake.sent(1001)[-1]
    assert "AGMARKNET" in fake.sent(1001)[-1]
    bot.handle_update(_msg(1001, "/price soyabean apmc"))  # matches both mandis
    data = [row[0]["callback_data"] for row in fake.buttons()]
    assert len(data) == 2 and all(d.startswith("p:") for d in data)
    bot.handle_update(_cb(1001, data[0]))
    assert "Latest reported modal price" in fake.sent(1001)[-1]
    bot.handle_update(_msg(1001, "/price wheatx sehore"))
    assert "Crops:" in fake.sent(1001)[-1]


@pytest.mark.db
def test_forecast_message_states_range_accuracy_and_disclaimer(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/forecast soyabean sehore"))
    out = fake.sent(1001)[-1]
    assert "Day 7" in out and "Day 14" in out and "80% range" in out
    assert "typical error 5.0% vs 6.0%" in out and "Not a guarantee" in out
    bot.handle_update(_msg(1001, "/forecast soyabean ashta"))
    assert "no forecast" in fake.sent(1001)[-1]


@pytest.mark.db
def test_alert_commands_validate_and_only_touch_own_alerts(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/alert soyabean sehore above 100"))
    assert "Alert 1 created" in fake.sent(1001)[-1]
    bot.handle_update(_msg(1001, "/alert soyabean sehore above 100"))
    assert "already have" in fake.sent(1001)[-1]
    bot.handle_update(_msg(1001, "/alert soyabean sehore above 0"))
    assert "above 0" in fake.sent(1001)[-1]
    bot.handle_update(_msg(1001, "/alert soyabean sehore sideways 5"))
    assert fake.sent(1001)[-1].startswith("Use /alert")
    bot.handle_update(_msg(1001, "/alerts"))
    assert "1. Soyabean at Sehore APMC: price at or above ₹100/quintal" in fake.sent(1001)[-1]
    bot.handle_update(_msg(3003, "/stop 1"))  # someone else
    assert fake.sent(3003)[-1] == "You have no alert 1."
    bot.handle_update(_msg(1001, "/stop 1"))
    assert fake.sent(1001)[-1] == "Alert 1 removed."


@pytest.mark.db
def test_bot_rate_limit_per_chat(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    for i in range(21):
        bot.handle_update(_msg(1001, "/help", update_id=i))
    assert fake.sent(1001)[-1] == "Too many requests; please wait a minute."


@pytest.mark.db
def test_polling_loop_advances_offset_and_survives_a_bad_update(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    fake.updates = [{"update_id": 10, "message": {"chat": {"id": 1, "type": "private"}}},  # no text: ignored
                    {"update_id": 11, "bogus": True}, _msg(1001, "/help", update_id=12)]
    assert poll_once(bot, None, timeout_s=0) == 13
    assert len(fake.sent(1001)) == 1


@pytest.mark.db
def test_delete_removes_everything(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/alert soyabean sehore above 100"))
    bot.handle_update(_msg(1001, "/delete"))
    assert [b[0]["callback_data"] for b in fake.buttons()] == ["x:del"]
    bot.handle_update(_cb(1001, "x:del"))
    with db.connect() as c:
        counts = c.execute(text(
            "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM alert_subscriptions)")).one()
    assert tuple(counts) == (0, 0)


# --- alerts ----------------------------------------------------------------------------------------------------

def _subscribe(db, chat_id: int, series_id: int, ctype: str, value) -> int:
    with session_scope(db) as s:
        uid = service.get_or_create_user(s, chat_id)
        return service.add_subscription(s, uid, series_id, ctype, value, 20)


@pytest.mark.db
def test_price_and_forecast_alerts_are_sent_once(world, tg, db):
    fake, client = tg
    above = _subscribe(db, 1001, world["sehore"], "price_above", 100)          # true: one message
    _subscribe(db, 1001, world["sehore"], "price_below", 1)                    # false: nothing
    fc = _subscribe(db, 1001, world["sehore"], "forecast_change_pct", "0.5")   # test forecast is +0.7% at day 7
    _subscribe(db, 1001, world["sehore"], "forecast_change_pct", 5)            # not reached
    alerts.run_alerts(db, client, TODAY)
    msgs = fake.sent(1001)
    assert len(msgs) == 2
    assert any(f"alert {above}:" in m and "at or above your threshold of ₹100" in m for m in msgs)
    assert any(f"alert {fc}:" in m and "1% above" in m and "estimate" in m and "not a guarantee" in m for m in msgs)
    alerts.run_alerts(db, client, TODAY)  # re-run: nothing new
    assert len(fake.sent(1001)) == 2
    with db.connect() as c:
        rows = c.execute(text("SELECT idempotency_key, status FROM notification_history ORDER BY id")).all()
    assert [r.status for r in rows] == ["sent", "sent"] and rows[0].idempotency_key.startswith(f"sub:{above}:price:")


@pytest.mark.db
def test_blocked_user_alerts_are_paused_and_without_a_token_messages_wait(world, tg, db):
    fake, client = tg
    _subscribe(db, 4004, world["sehore"], "price_above", 100)
    alerts.run_alerts(db, None, TODAY)  # no token: queued only
    with db.connect() as c:
        assert c.execute(text("SELECT status FROM notification_history")).scalar_one() == "pending"
    fake.fail[4004] = (403, "Forbidden: bot was blocked by the user")
    alerts.run_alerts(db, client, TODAY)
    with db.connect() as c:
        st = c.execute(text("SELECT status, attempts FROM notification_history")).one()
        active = c.execute(text("SELECT active FROM alert_subscriptions")).scalar_one()
    assert tuple(st) == ("failed", 1) and active is False


# --- sign-in and authenticated API -------------------------------------------------------------------------------

def _app(world):
    s = world["settings"]
    return create_app(settings=s, engine=read_only_engine(s), use_redis=False)


@pytest.mark.db
def test_login_link_session_and_alert_api(world, tg, db):
    fake, client = tg
    bot = Bot(db, client, world["settings"])
    bot.handle_update(_msg(1001, "/login"))
    link = fake.sent(1001)[-1].split("\n")[-1]
    assert link.startswith("https://dash.example.test/login#code=")  # in the fragment: not sent to servers or logs
    code = link.split("#code=", 1)[1]
    with TestClient(_app(world)) as api:
        assert api.get("/v1/alerts").status_code == 401
        assert api.get("/v1/alerts").headers["WWW-Authenticate"] == "Bearer"
        r = api.post("/v1/auth/session", json={"code": code})
        assert r.status_code == 200
        token = r.json()["token"]
        assert api.post("/v1/auth/session", json={"code": code}).status_code == 401  # one-time
        assert api.post("/v1/auth/session", json={"code": code, "extra": 1}).status_code == 400
        h = {"Authorization": f"Bearer {token}"}
        body = {"commodity_id": world["commodity_id"], "market_id": world["sehore_market"],
                "condition": {"type": "price_above", "value": 2500}}
        created = api.post("/v1/alerts", json=body, headers=h)
        assert created.status_code == 201 and created.json()["condition"] == {"type": "price_above", "value": 2500.0}
        assert api.post("/v1/alerts", json=body, headers=h).status_code == 409
        listed = api.get("/v1/alerts", headers=h)
        assert [a["id"] for a in listed.json()] == [created.json()["id"]]
        assert listed.headers["Cache-Control"] == "no-store" and "X-Cache" not in listed.headers
        assert api.get("/v1/alerts/history", headers=h).json() == []
        # another user cannot see or delete it
        with session_scope(db) as s:
            other = service.issue_login_code(s, service.get_or_create_user(s, 5005), 15)
        t2 = api.post("/v1/auth/session", json={"code": other}).json()["token"]
        h2 = {"Authorization": f"Bearer {t2}"}
        assert api.get("/v1/alerts", headers=h2).json() == []
        assert api.delete(f"/v1/alerts/{created.json()['id']}", headers=h2).status_code == 404
        assert api.delete(f"/v1/alerts/{created.json()['id']}", headers=h).status_code == 204
        assert api.delete("/v1/me", headers=h).status_code == 204
        assert api.get("/v1/alerts", headers=h).status_code == 401  # sessions removed with the account
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM users WHERE telegram_chat_id = 1001")).scalar_one() == 0
        stored = c.execute(text("SELECT token_sha256 FROM user_sessions")).scalars().all()
    assert token not in stored and t2 not in stored  # only hashes are stored


@pytest.mark.db
def test_expired_codes_and_sessions_are_refused_and_purged(world, db):
    with session_scope(db) as s:
        uid = service.get_or_create_user(s, 1001)
        code = service.issue_login_code(s, uid, 15)
        s.execute(text("UPDATE login_codes SET expires_at = now() - interval '2 days'"))
        assert service.exchange_code(s, code, 30) is None
        s.execute(text("INSERT INTO user_sessions (user_id, token_sha256, expires_at) VALUES (:u, 'x', :e)"),
                  {"u": uid, "e": datetime.now(UTC) - timedelta(days=1)})
        sid = service.add_subscription(s, uid, world["sehore"], "price_above", 100, 20)
        s.execute(text("INSERT INTO notification_history (subscription_id, user_id, idempotency_key, message, "
                       "created_at) VALUES (:s, :u, 'old', 'm', now() - interval '400 days')"), {"s": sid, "u": uid})
        out = service.purge(s, 365)
    assert out == {"sessions": 1, "login_codes": 1, "notifications": 1}


@pytest.mark.db
def test_personal_responses_are_never_served_from_the_shared_cache(world, db):
    import redis
    from pydantic import SecretStr

    from mandicast.config import get_settings
    url = get_settings().redis_url
    if url is None:
        pytest.skip("REDIS_URL not configured")
    url15 = url.get_secret_value().rsplit("/", 1)[0] + "/15"
    try:
        redis.Redis.from_url(url15, socket_timeout=1).flushdb()
    except redis.RedisError:
        pytest.skip("Redis not reachable")
    s = world["settings"].model_copy(update={"redis_url": SecretStr(url15)})
    tokens = []
    with session_scope(db) as ses:
        for chat in (1001, 1002):
            uid = service.get_or_create_user(ses, chat)
            service.add_subscription(ses, uid, world["sehore"], "price_above", 100 + chat, 20)
            tokens.append(service.exchange_code(ses, service.issue_login_code(ses, uid, 15), 30)[0])
    with TestClient(create_app(settings=s, engine=read_only_engine(s))) as api:
        api.app.state.data_version.ttl_s = 0
        seen = []
        for t in tokens + tokens:
            r = api.get("/v1/alerts", headers={"Authorization": f"Bearer {t}"})
            assert "X-Cache" not in r.headers and r.headers["Cache-Control"] == "no-store"
            seen.append(r.json()[0]["condition"]["value"])
        assert seen == [1101.0, 1102.0, 1101.0, 1102.0]
        public = api.get("/v1/commodities")
        assert public.headers["X-Cache"] == "MISS"  # the shared cache still works for public data


@pytest.mark.db
def test_transient_telegram_failures_are_retried_on_later_runs_then_given_up(world, tg, db):
    """Failure mode: Telegram keeps answering 5xx. Every run tries again (after the client's own retries), up to
    MAX_ATTEMPTS runs; then the message stays `failed` and is not sent again. Nothing is queued twice, and the alert
    stays active (unlike a user who blocked the bot)."""
    fake, client = tg
    _subscribe(db, 5005, world["sehore"], "price_above", 100)
    fake.fail[5005] = (502, "Bad Gateway")
    for run in range(1, alerts.MAX_ATTEMPTS + 2):
        alerts.run_alerts(db, client, TODAY)
        with db.connect() as c:
            status, attempts = c.execute(text("SELECT status, attempts FROM notification_history")).one()
        assert status == "failed" and attempts == min(run, alerts.MAX_ATTEMPTS)
    tries = len(fake.sent(5005))
    assert tries == alerts.MAX_ATTEMPTS * (client.retries + 1)
    del fake.fail[5005]
    alerts.run_alerts(db, client, TODAY)
    assert len(fake.sent(5005)) == tries  # given up: not resent once Telegram recovers
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM notification_history")).scalar_one() == 1
        assert c.execute(text("SELECT active FROM alert_subscriptions")).scalar_one() is True
