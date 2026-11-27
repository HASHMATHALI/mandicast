"""Shared fixtures. Database tests run against the separate `mandicast_test` database in the Compose
PostgreSQL (created by docker/postgres/init); they are skipped if it is not reachable."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from mandicast.config import PROJECT_ROOT, get_settings
from mandicast.ingest.raw_store import EXPECTED_HEADER

FIXTURES = Path(__file__).parent / "fixtures"
REAL_CSV = FIXTURES / "soyabean_sehore_2025-10-20_2025-11-20.csv"
TABLES = ["notification_history", "alert_subscriptions", "user_sessions", "login_codes", "users", "model_events",
          "monitoring_snapshots", "forecasts", "forecast_runs", "model_versions", "price_observations",
          "price_series", "name_aliases", "markets", "districts", "states", "commodities", "source_observations",
          "raw_pages", "ingestion_jobs"]


def real_rows() -> list[list[str]]:
    with REAL_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == EXPECTED_HEADER
    return rows[1:]


def to_csv_bytes(rows: list[list[str]], header: list[str] = EXPECTED_HEADER) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def db_url_for_tests() -> str:
    url = get_settings().database_url.get_secret_value()
    base, _, _ = url.rpartition("/")
    return f"{base}/mandicast_test"


@pytest.fixture(scope="session")
def engine():
    url = db_url_for_tests()
    eng = create_engine(url, hide_parameters=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database not reachable ({type(exc).__name__}); run: docker compose up -d db")
    from alembic import command
    from alembic.config import Config

    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    return engine


class FakeClient:
    """Stands in for DataGovClient in tests: serves responses built from the fixture rows (or rows a test passes).

    Clearly a test double: it never touches the network."""

    def __init__(self, rows: list[list[str]], commodity: str = "Soyabean") -> None:
        self.rows = rows
        self.commodity = commodity
        self.calls: list[tuple] = []

    def _sorted(self) -> list[list[str]]:
        from datetime import datetime
        return sorted(self.rows, key=lambda r: datetime.strptime(r[0], "%d/%m/%Y"))

    def total(self, commodity: str) -> int:
        self.calls.append(("total", commodity))
        return len(self.rows)

    def csv_page(self, commodity: str, offset: int, limit: int) -> bytes:
        self.calls.append(("page", offset, limit))
        return to_csv_bytes(self._sorted()[offset:offset + limit])

    def csv_date(self, commodity: str, d: str, limit: int) -> bytes:
        self.calls.append(("date", d))
        rows = [r for r in self.rows if r[0] == d][:limit]
        # Like the real API (verified 2026-11-26): a date without records is an empty body, not a header.
        return to_csv_bytes(rows) if rows else b""


@pytest.fixture(autouse=True)
def no_mlflow(monkeypatch):
    """Tests never write to the project's MLflow store (experiments/mlflow.db)."""
    from mandicast.serving import registry
    monkeypatch.setattr(registry, "log_mlflow", lambda t, version: (None, None))
    monkeypatch.setattr(registry, "mlflow_set_production", lambda commodity, version: None)
