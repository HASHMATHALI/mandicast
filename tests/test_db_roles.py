"""The least-privilege role for the API and the bot (docs/15 §4), checked on the test database: it reads everything,
writes only the account tables, cannot change prices, models or the schema, and the API and sign-in work with it."""

from __future__ import annotations

import secrets

import pytest
from conftest import FakeClient, db_url_for_tests, real_rows
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

import mandicast.jobs as jobs
from mandicast import pipeline
from mandicast.api.app import create_app, read_only_engine
from mandicast.bot import service
from mandicast.config import Settings
from mandicast.db.roles import drop_role, ensure_api_role

pytestmark = pytest.mark.db
ROLE = "mandicast_api_test"


@pytest.fixture
def role_url(db, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "REPORT_DIR", tmp_path / "reports")
    s = Settings(database_url=db_url_for_tests(), raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                 request_pause_s=0, manual_aliases_path=tmp_path / "none.csv", page_size=30, redis_url=None)
    pipeline.backfill(db, s, ["Soyabean"], client=FakeClient(real_rows()))
    password = secrets.token_urlsafe(24)
    result = ensure_api_role(db, ROLE, password)
    assert result["writable_tables"][0] == "users" and result["sequences"] == 5
    assert ensure_api_role(db, ROLE, password)["created"] is False  # idempotent
    url = make_url(db_url_for_tests()).set(username=ROLE, password=password)
    yield url.render_as_string(hide_password=False)
    drop_role(db, ROLE)


def test_api_role_reads_everything_and_writes_only_account_tables(role_url, db):
    eng = create_engine(role_url)
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT count(*) FROM price_observations")).scalar_one() > 0
        for sql in ("UPDATE price_observations SET modal_price = 1",
                    "DELETE FROM model_versions",
                    "INSERT INTO commodities (name) VALUES ('x')",
                    "CREATE TABLE intruder (id int)",
                    "DROP TABLE forecasts",
                    "TRUNCATE users"):
            with eng.begin() as c, pytest.raises(ProgrammingError, match="permission denied|must be owner"):
                c.execute(text(sql))
        with Session(eng) as c, c.begin():
            uid = service.get_or_create_user(c, 424242)
            code = service.issue_login_code(c, uid, 15)
            assert service.exchange_code(c, code, 30) is not None
            sid = c.execute(text("SELECT id FROM price_series LIMIT 1")).scalar_one()
            service.add_subscription(c, uid, sid, "price_above", 100, 20)
            service.delete_user(c, uid)  # cascades through all account tables
        with db.connect() as c:
            assert c.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    finally:
        eng.dispose()


def test_api_serves_and_signs_in_with_the_role(role_url, db, tmp_path):
    s = Settings(database_url=role_url, raw_dir=tmp_path / "raw", commodities=["Soyabean"],
                 manual_aliases_path=tmp_path / "none.csv", redis_url=None, api_rate_limit_per_minute=10_000)
    with Session(db) as c, c.begin():
        uid = service.get_or_create_user(c, 535353)
        code = service.issue_login_code(c, uid, 15)
    with TestClient(create_app(settings=s, engine=read_only_engine(s), use_redis=False)) as api:
        assert api.get("/v1/commodities").json()[0]["name"] == "Soyabean"
        assert api.get("/v1/health").json()["checks"]["database"]["ok"] is True
        token = api.post("/v1/auth/session", json={"code": code}).json()["token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert api.get("/v1/alerts", headers=auth).json() == []
        assert api.delete("/v1/me", headers=auth).status_code == 204
