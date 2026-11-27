"""HTTP API against the PostgreSQL test database (prices loaded from the real fixture rows through the pipeline).

The model version, forecast run and forecast values inserted here are TEST FIXTURES: synthetic numbers that only
exercise the API's behaviour. They are not model results.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta

import pytest
from conftest import TABLES, FakeClient, db_url_for_tests, real_rows
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.api.app import client_address, create_app, read_only_engine
from mandicast.api.limits import RateLimiter
from mandicast.config import Settings, get_settings

pytestmark = pytest.mark.db
ORIGIN = date(2025, 11, 17)  # a Monday inside the fixture's date range
TEST_EVALUATION = {
    "period": "TEST FIXTURE (not an evaluation)", "report": "n/a",
    "served": [{"h": 7, "n": 100, "wape": 0.12, "mase_vs_last_value": 1.02, "dir_acc_moves_ge_2pct": 0.6,
                "coverage80": 0.7, "coverage90": 0.85},
               {"h": 14, "n": 90, "wape": 0.10, "mase_vs_last_value": 0.9, "dir_acc_moves_ge_2pct": 0.65,
                "coverage80": 0.8, "coverage90": 0.9}],
    "last_value": [{"h": 7, "n": 100, "wape": 0.11}, {"h": 14, "n": 90, "wape": 0.12}],
}


def _settings(tmp_path, **kw) -> Settings:
    return Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                    request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30, redis_url=None, **kw)


@pytest.fixture(scope="module")
def data(engine, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    saved, jobs.REPORT_DIR = jobs.REPORT_DIR, tmp / "reports"
    try:
        pipeline.backfill(engine, _settings(tmp), ["Soyabean"], client=FakeClient(real_rows()))
    finally:
        jobs.REPORT_DIR = saved
    with engine.begin() as c:
        cid = c.execute(text("SELECT id FROM commodities WHERE name = 'Soyabean'")).scalar_one()
        markets = dict(c.execute(text("SELECT m.name, m.id FROM markets m")).all())
        series = dict(c.execute(text("SELECT market_id, id FROM price_series")).all())
        sehore, ashta = markets["sehore"], markets["ashta"]
        mv = c.execute(text(
            "INSERT INTO model_versions (version, kind, commodity_id, horizon_days, training_cutoff, valid_from, "
            "valid_to, artifact_path, artifact_sha256, code_sha256, params, training, evaluation, interval_method, "
            "status) VALUES ('test-soyabean-2025q4', 'lightgbm_global', :c, 14, '2025-10-01', '2025-10-01', "
            "'2025-12-31', 'models/does-not-exist.txt', :z, :z, '{}', '{\"best_iteration\": 3}', CAST(:ev AS jsonb), "
            "'conditional', 'production') RETURNING id"), {"c": cid, "z": "0" * 64, "ev": json.dumps(TEST_EVALUATION)}
        ).scalar_one()
        run = c.execute(text(
            "INSERT INTO forecast_runs (commodity_id, model_version_id, origin, data_complete_through, "
            "series_eligible, series_forecast, calibration) VALUES (:c, :m, :o, :d, 1, 1, '{}') RETURNING id"),
            {"c": cid, "m": mv, "o": ORIGIN, "d": ORIGIN + timedelta(days=2)}).scalar_one()
        last_date, last_price = c.execute(text(
            "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary AND date <= :o "
            "ORDER BY date DESC LIMIT 1"), {"s": series[sehore], "o": ORIGIN}).one()
        for h in range(1, 15):
            p = float(last_price) * (1 + 0.001 * h)
            c.execute(text(
                "INSERT INTO forecasts (run_id, series_id, model_version_id, issued_on, target_date, horizon_days, "
                "last_date, last_price, point, lower80, upper80, lower90, upper90, volatility_tercile) VALUES "
                "(:r, :s, :m, :o, :t, :h, :ld, :lp, :p, :l8, :u8, :l9, :u9, 1)"),
                {"r": run, "s": series[sehore], "m": mv, "o": ORIGIN, "t": ORIGIN + timedelta(days=h), "h": h,
                 "ld": last_date, "lp": last_price, "p": round(p, 2), "l8": round(p * 0.9, 2),
                 "u8": round(p * 1.1, 2), "l9": round(p * 0.85, 2), "u9": round(p * 1.15, 2)})
    return {"commodity_id": cid, "sehore": sehore, "ashta": ashta, "series": series, "run": run}


@pytest.fixture(scope="module")
def client(data, tmp_path_factory):
    s = _settings(tmp_path_factory.mktemp("client"), api_rate_limit_per_minute=10_000)
    with TestClient(create_app(settings=s, engine=read_only_engine(s), use_redis=False)) as c:
        yield c


def _fresh_window() -> None:
    """Rate-limit windows are calendar minutes; avoid starting a counting test in the last seconds of one."""
    if 60 - time.time() % 60 < 5:
        time.sleep(60 - time.time() % 60 + 0.1)


def _q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def test_health_reports_database_and_missing_redis_and_artifact(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is False and body["status"] == "degraded"
    assert body["checks"]["models"]["missing_artifacts"] == ["test-soyabean-2025q4"]
    assert r.headers["Cache-Control"] == "no-store"


def test_security_headers_and_request_id(client):
    r = client.get("/v1/commodities")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy",
              "X-Request-ID"):
        assert h in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_commodities_and_search_escapes_wildcards(client, data):
    r = client.get("/v1/commodities")
    assert r.status_code == 200
    (soy,) = r.json()
    assert soy["name"] == "Soyabean" and soy["has_forecasts"] is True and soy["series"] == 2
    assert client.get("/v1/commodities?q=soya").json()[0]["id"] == data["commodity_id"]
    assert client.get("/v1/commodities?q=%25").json() == []  # a literal '%', not "match everything"
    assert client.get("/v1/commodities?has_forecasts=false").json() == []


def test_strict_parameters(client, data):
    base = f"/v1/prices?commodity_id={data['commodity_id']}&market_id={data['sehore']}"
    r = client.get(base + "&foo=1")
    assert r.status_code == 400 and r.json()["error"]["details"]["unknown"] == ["foo"]
    r = client.get(base + f"&market_id={data['ashta']}")
    assert r.status_code == 400 and r.json()["error"]["details"]["repeated"] == ["market_id"]
    probe = "<script>alert(1)</script>"
    r = client.get(f"/v1/prices?commodity_id={probe}&market_id=1")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_parameter"
    assert probe not in r.text  # input is never echoed back
    assert client.get("/v1/prices?commodity_id=0&market_id=1").status_code == 400
    assert client.get("/v1/nope").json()["error"]["code"] == "not_found"


def test_markets_pagination_and_detail(client, data):
    r1 = client.get(f"/v1/markets?commodity_id={data['commodity_id']}&limit=1").json()
    assert len(r1["items"]) == 1 and r1["next_cursor"]
    r2 = client.get(f"/v1/markets?commodity_id={data['commodity_id']}&limit=1&cursor={r1['next_cursor']}").json()
    assert r2["items"][0]["id"] != r1["items"][0]["id"] and r2["next_cursor"] is None
    assert {r1["items"][0]["name"], r2["items"][0]["name"]} == {"Ashta APMC", "Sehore APMC"}
    assert client.get("/v1/markets?cursor=not-a-cursor").status_code == 400
    with_fc = client.get(f"/v1/markets?commodity_id={data['commodity_id']}&has_forecast=true").json()["items"]
    without = client.get(f"/v1/markets?commodity_id={data['commodity_id']}&has_forecast=false").json()["items"]
    assert [m["id"] for m in with_fc] == [data["sehore"]] and [m["id"] for m in without] == [data["ashta"]]
    assert client.get("/v1/markets?has_forecast=true").status_code == 400
    d = client.get(f"/v1/markets/{data['sehore']}").json()
    assert {(a["name_as_reported"], a["rule"]) for a in d["aliases"]} >= {("Sehore APMC", "apmc_suffix"),
                                                                           ("Sehore", "exact")}
    assert d["commodities"][0]["commodity"] == "Soyabean"
    assert client.get("/v1/markets/987654").json()["error"]["code"] == "market_not_found"


def test_prices_are_primary_values_without_filling(client, data, engine):
    sid = data["series"][data["sehore"]]
    url = f"/v1/prices?commodity_id={data['commodity_id']}&market_id={data['sehore']}&limit=500"
    body = client.get(url).json()
    db_days = _q(engine, "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary "
                         "AND NOT ('local_outlier' = ANY(flags)) ORDER BY date", s=sid)
    assert [(i["date"], i["modal_price"]) for i in body["items"]] == [(str(d), float(p)) for d, p in db_days]
    assert body["rows"] == "primary" and body["unit"] == "INR_per_quintal" and body["source"]["licence_url"]
    page = client.get(url.replace("limit=500", "limit=5")).json()
    nxt = client.get(url.replace("limit=500", "limit=5") + f"&cursor={page['next_cursor']}").json()
    assert [i["date"] for i in page["items"] + nxt["items"]] == [str(d) for d, _ in db_days[:10]]
    yellow = client.get(url + "&variety=yellow").json()
    assert yellow["rows"] == "variety_grade" and {i["variety"] for i in yellow["items"]} == {"Yellow"}


def test_date_range_validation(client, data):
    base = f"/v1/prices?commodity_id={data['commodity_id']}&market_id={data['sehore']}"
    r = client.get(base + "&from=2010-01-01&to=2025-11-20")
    assert r.status_code == 400 and r.json()["error"]["code"] == "date_range_too_long"
    r = client.get(base + "&from=2025-11-20&to=2025-11-01")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_parameter"


def test_latest_and_compare(client, data, engine):
    latest = client.get(f"/v1/prices/latest?commodity_id={data['commodity_id']}").json()
    assert {i["market"]["id"] for i in latest["items"]} == {data["sehore"], data["ashta"]}
    m = f"{data['sehore']},{data['ashta']}"
    body = client.get(f"/v1/compare?commodity_id={data['commodity_id']}&market_ids={m}&from=2025-10-20").json()
    common = _q(engine, "SELECT date FROM price_observations WHERE series_id = ANY(:ids) AND is_primary "
                        "AND date >= '2025-10-20' GROUP BY date HAVING count(*) = 2 ORDER BY date",
                ids=list(data["series"].values()))
    assert [i["date"] for i in body["items"]] == [str(d) for (d,) in common]
    assert all(len(i["modal_prices"]) == 2 for i in body["items"])
    assert client.get(f"/v1/compare?commodity_id={data['commodity_id']}&market_ids={data['sehore']}").status_code == 400
    dup = f"{data['sehore']},{data['sehore']}"
    assert client.get(f"/v1/compare?commodity_id={data['commodity_id']}&market_ids={dup}").status_code == 400
    r = client.get(f"/v1/compare?commodity_id={data['commodity_id']}&market_ids={data['sehore']},999999")
    assert r.status_code == 404


def test_trends_match_a_direct_computation(client, data, engine):
    body = client.get(f"/v1/trends?commodity_id={data['commodity_id']}&market_id={data['sehore']}").json()
    rows = _q(engine, "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary "
                      "AND NOT ('local_outlier' = ANY(flags)) ORDER BY date", s=data["series"][data["sehore"]])
    last_d, last_p = rows[-1]
    prev7 = [float(p) for d, p in rows[:-1] if d >= last_d - timedelta(days=7)]
    assert body["latest"]["date"] == str(last_d)
    assert body["vs_previous_7_days"]["reports"] == len(prev7)
    assert body["vs_previous_7_days"]["change_pct"] == round(100 * (float(last_p) / (sum(prev7) / len(prev7)) - 1), 2)
    assert "not a forecast" in body["note"]


def test_forecast_response(client, data, engine):
    r = client.get(f"/v1/forecasts?commodity_id={data['commodity_id']}&market_id={data['sehore']}")
    assert r.status_code == 200
    f = r.json()
    assert f["issued_on"] == str(ORIGIN) and len(f["points"]) == 7
    assert [p["horizon_days"] for p in f["points"]] == list(range(1, 8))
    for p in f["points"]:
        (i80, i90) = p["intervals"]
        assert i90["lower"] <= i80["lower"] <= p["modal_price"] <= i80["upper"] <= i90["upper"]
    actual = dict(_q(engine, "SELECT date, modal_price FROM price_observations WHERE series_id = :s AND is_primary "
                             "AND date > :o", s=data["series"][data["sehore"]], o=ORIGIN))
    for p in f["points"]:
        a = actual.get(date.fromisoformat(p["date"]))
        assert p["actual_modal_price"] == (None if a is None else float(a))
    acc = f["accuracy"]
    assert acc["wape"] == 0.12 and acc["baseline_wape"] == 0.11 and acc["beats_baseline"] is False
    text_w = " ".join(f["warnings"])
    assert "not more accurate" in text_w and "80% range" in text_w and "3 rounds" in text_w and "days old" in text_w
    assert "Not a guarantee" in f["disclaimer"]
    f14 = client.get(f"/v1/forecasts?commodity_id={data['commodity_id']}&market_id={data['sehore']}&horizon=14").json()
    assert len(f14["points"]) == 14 and f14["accuracy"]["beats_baseline"] is True
    assert client.get(f"/v1/forecasts?commodity_id={data['commodity_id']}&market_id={data['sehore']}&horizon=10")\
        .status_code == 400


def test_forecast_insufficient_data_and_eligibility(client, data):
    r = client.get(f"/v1/forecasts?commodity_id={data['commodity_id']}&market_id={data['ashta']}")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "insufficient_data" and err["details"]["eligible_now"] is False
    # The fixture's dates are over 120 days before today, so the pipeline found no complete recent date and did not
    # evaluate eligibility; the API says so instead of returning an empty reason.
    assert err["details"]["reason"] == "eligibility has not been evaluated for this series yet"
    e = client.get(f"/v1/forecasts/eligibility?commodity_id={data['commodity_id']}&market_id={data['ashta']}").json()
    assert e["eligible"] is False and e["in_latest_forecast"] is False
    assert e["stats"]["report_days_last_730"] < 250


def test_forecast_model_unavailable(client, data, engine):
    with engine.begin() as c:
        cid = c.execute(text("INSERT INTO commodities (name) VALUES ('TestCrop') RETURNING id")).scalar_one()
        c.execute(text("INSERT INTO price_series (commodity_id, market_id) VALUES (:c, :m)"),
                  {"c": cid, "m": data["sehore"]})
    try:
        r = client.get(f"/v1/forecasts?commodity_id={cid}&market_id={data['sehore']}")
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_unavailable"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM price_series WHERE commodity_id = :c"), {"c": cid})
            c.execute(text("DELETE FROM commodities WHERE id = :c"), {"c": cid})


def test_models_and_freshness(client, data):
    (m,) = client.get("/v1/models/current").json()
    assert m["version"] == "test-soyabean-2025q4" and m["latest_forecast_origin"] == str(ORIGIN)
    ev = client.get("/v1/models/test-soyabean-2025q4/evaluation").json()
    assert ev["evaluation"]["period"].startswith("TEST FIXTURE")
    assert client.get("/v1/models/NOT_VALID!/evaluation").status_code in (400, 404)
    assert client.get("/v1/models/unknown-version/evaluation").json()["error"]["code"] == "model_not_found"
    fr = client.get("/v1/freshness").json()
    assert fr["last_successful_ingestion"]["kind"] == "backfill"
    assert fr["commodities"][0]["latest_forecast_origin"] == str(ORIGIN)


def test_api_database_connections_are_read_only(data, tmp_path):
    eng = read_only_engine(_settings(tmp_path))
    with pytest.raises(DBAPIError, match="read-only"), eng.begin() as c:
        c.execute(text("DELETE FROM forecasts"))
    eng.dispose()


def test_unexpected_errors_are_generic(data, tmp_path):
    s = _settings(tmp_path)
    app = create_app(settings=s, engine=read_only_engine(s), use_redis=False)

    @app.get("/v1/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/v1/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in r.text and r.json()["error"]["details"]["request_id"]


def test_rate_limit_returns_429_with_retry_after(data, tmp_path):
    s = _settings(tmp_path, api_rate_limit_per_minute=3)
    _fresh_window()
    with TestClient(create_app(settings=s, engine=read_only_engine(s), use_redis=False)) as c:
        codes = [c.get("/v1/commodities").status_code for _ in range(4)]
        r = c.get("/v1/commodities")
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert r.json()["error"]["code"] == "rate_limited" and 1 <= int(r.headers["Retry-After"]) <= 60


def test_in_process_rate_limiter_counts_per_client_and_window():
    rl = RateLimiter(2, None, b"k")
    hit = lambda client, now: asyncio.run(rl.hit(client, now=now))[0]  # noqa: E731
    assert [hit("a", 120.0) for _ in range(3)] == [True, True, False]
    assert hit("b", 120.0) is True
    assert hit("a", 180.0) is True  # next window
    assert rl.client_key("1.2.3.4") != "1.2.3.4" and len(rl.client_key("1.2.3.4")) == 32


def test_forwarded_for_is_ignored_unless_trusted():
    class R:
        headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.9"}
        client = type("C", (), {"host": "127.0.0.1"})()
    assert client_address(R(), trust_forwarded_for=False) == "127.0.0.1"
    assert client_address(R(), trust_forwarded_for=True) == "10.0.0.9"


def test_openapi_document(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/v1/forecasts", "/v1/prices", "/v1/compare", "/v1/trends", "/v1/freshness", "/v1/health"} <= set(paths)


# --- with Redis (the Compose service; database 15 is used and flushed) ---------------------------------------

def _redis_url() -> str | None:
    url = get_settings().redis_url
    return None if url is None else url.get_secret_value().rsplit("/", 1)[0] + "/15"


@pytest.fixture
def redis_settings(tmp_path):
    import redis
    url = _redis_url()
    if url is None:
        pytest.skip("REDIS_URL not configured")
    try:
        r = redis.Redis.from_url(url, socket_timeout=1)
        r.flushdb()
        r.close()
    except redis.RedisError:
        pytest.skip("Redis not reachable; run: docker compose up -d redis")
    return _settings(tmp_path, api_rate_limit_per_minute=5).model_copy(update={"redis_url": SecretStr(url)})


def test_cache_hit_then_invalidated_by_new_data(data, redis_settings, engine):
    s = redis_settings.model_copy(update={"api_rate_limit_per_minute": 1000})
    with TestClient(create_app(settings=s, engine=read_only_engine(s))) as c:
        c.app.state.data_version.ttl_s = 0
        url = f"/v1/trends?commodity_id={data['commodity_id']}&market_id={data['sehore']}"
        first, second = c.get(url), c.get(url)
        assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
        assert first.json() == second.json()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO ingestion_jobs (kind, status) VALUES ('test', 'succeeded')"))
        assert c.get(url).headers["X-Cache"] == "MISS"
        assert c.get("/v1/health").headers.get("X-Cache") is None  # health is never cached
        assert c.get("/v1/health").json()["checks"]["redis"]["ok"] is True


def test_rate_limit_is_shared_between_workers_through_redis(data, redis_settings):
    apps = [create_app(settings=redis_settings, engine=read_only_engine(redis_settings)) for _ in range(2)]
    _fresh_window()
    with TestClient(apps[0]) as a, TestClient(apps[1]) as b:
        codes = [(a if i % 2 else b).get("/v1/states").status_code for i in range(6)]
    assert codes == [200] * 5 + [429]


# --- failure modes (docs/15 §2): dependencies down ---------------------------------------------------------------

def test_database_outage_is_reported_and_leaks_nothing(tmp_path):
    """The database is unreachable: health says `down` (503) and data endpoints give the generic error, without the
    connection URL, host or password, and they answer within the connect timeout instead of hanging (without it,
    psycopg waited 130 s per attempt on the dev machine)."""
    s = Settings(database_url="postgresql+psycopg://mandicast:not-the-password@127.0.0.1:1/mandicast",
                 raw_dir=tmp_path / "raw", commodities=["Soyabean"], manual_aliases_path=tmp_path / "none.csv",
                 redis_url=None, db_connect_timeout_s=2)
    started = time.perf_counter()
    with TestClient(create_app(settings=s, engine=read_only_engine(s), use_redis=False),
                    raise_server_exceptions=False) as c:
        health = c.get("/v1/health")
        data = c.get("/v1/commodities")
    assert time.perf_counter() - started < 20
    assert health.status_code == 503 and health.json()["status"] == "down"
    assert health.json()["checks"]["database"]["ok"] is False
    assert data.status_code == 500 and data.json()["error"]["code"] == "internal_error"
    assert data.json()["error"]["details"]["request_id"]
    for leak in ("not-the-password", "127.0.0.1", "psycopg", "postgresql"):
        assert leak not in health.text and leak not in data.text


def test_api_keeps_serving_when_redis_is_unreachable(data, tmp_path):
    """Redis is down: responses are served uncached, rate limiting continues in-process, health says `degraded`."""
    from redis import asyncio as aioredis

    s = _settings(tmp_path)
    dead = aioredis.from_url("redis://:x@127.0.0.1:1/0", socket_timeout=0.2, socket_connect_timeout=0.2)
    with TestClient(create_app(settings=s, engine=read_only_engine(s), redis=dead)) as c:
        first = c.get("/v1/commodities")
        second = c.get("/v1/commodities")
        health = c.get("/v1/health")
    assert first.status_code == 200 and first.json()[0]["name"] == "Soyabean"
    assert second.status_code == 200 and second.headers.get("X-Cache") != "HIT"
    assert int(second.headers["X-RateLimit-Remaining"]) < int(first.headers["X-RateLimit-Remaining"])
    assert health.status_code == 200 and health.json()["status"] == "degraded"
    assert health.json()["checks"]["redis"]["ok"] is False
