"""SQLAlchemy models for the Phase 2 tables (docs/04_database_schema.md).

Source data is append-only: `source_observations` rows are never updated except to record that a newer complete
snapshot of the same date no longer contains them (`superseded_by_raw_page_id`). Cleaned values live in
`price_observations`, one row per source row, with quality flags and one primary row per series and day.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


JOB_STATUSES = ("running", "succeeded", "failed", "skipped")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    scope: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_fetched: Mapped[int] = mapped_column(BigInteger, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(BigInteger, server_default="0")
    rows_duplicate: Mapped[int] = mapped_column(BigInteger, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(BigInteger, server_default="0")
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint(f"status in {JOB_STATUSES}", name="ck_job_status"),)


class RawPage(Base):
    """One stored response file (a page of a commodity, or one complete arrival date)."""

    __tablename__ = "raw_pages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id"))
    commodity: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    request_params: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rows_inserted: Mapped[int] = mapped_column(Integer, server_default="0")
    __table_args__ = (CheckConstraint("kind in ('page', 'by_date')", name="ck_raw_kind"),
                      Index("ix_raw_pages_commodity_date", "commodity", "kind", "arrival_date"))


class SourceObservation(Base):
    """A source row exactly as received (all fields as text), identified by the hash of those fields."""

    __tablename__ = "source_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_page_id: Mapped[int] = mapped_column(ForeignKey("raw_pages.id"))
    source_row_hash: Mapped[str] = mapped_column(String(64), unique=True)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    arrival_date_raw: Mapped[str] = mapped_column(Text)
    state_raw: Mapped[str] = mapped_column(Text)
    district_raw: Mapped[str] = mapped_column(Text)
    market_raw: Mapped[str] = mapped_column(Text)
    commodity_raw: Mapped[str] = mapped_column(Text)
    commodity_code_raw: Mapped[str] = mapped_column(Text)
    variety_raw: Mapped[str] = mapped_column(Text)
    grade_raw: Mapped[str] = mapped_column(Text)
    min_price_raw: Mapped[str] = mapped_column(Text)
    max_price_raw: Mapped[str] = mapped_column(Text)
    modal_price_raw: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_by_raw_page_id: Mapped[int | None] = mapped_column(ForeignKey("raw_pages.id"))
    __table_args__ = (Index("ix_source_commodity_date", "commodity_raw", "arrival_date"),)


class State(Base):
    """`name` is the canonical key (lower case); `display_name` the most recently seen source spelling."""

    __tablename__ = "states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    display_seen: Mapped[date | None] = mapped_column(Date)


class District(Base):
    __tablename__ = "districts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("states.id"))
    name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    display_seen: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("state_id", "name"),)


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"))
    name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    display_seen: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("district_id", "name"), Index("ix_markets_district", "district_id"))


class Commodity(Base):
    __tablename__ = "commodities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    agmarknet_code: Mapped[str | None] = mapped_column(Text)
    price_unit: Mapped[str] = mapped_column(Text, server_default="INR_per_quintal")


class NameAlias(Base):
    """Every source spelling and the canonical entity it maps to, with the rule that mapped it."""

    __tablename__ = "name_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(16))
    parent_key: Mapped[str] = mapped_column(Text, server_default="")
    raw_name: Mapped[str] = mapped_column(Text)
    canonical_name: Mapped[str] = mapped_column(Text)
    rule: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("entity", "parent_key", "raw_name"),
                      CheckConstraint("entity in ('state', 'district', 'market', 'commodity')", name="ck_alias_entity"))


class PriceSeries(Base):
    __tablename__ = "price_series"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commodity_id: Mapped[int] = mapped_column(ForeignKey("commodities.id"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    first_date: Mapped[date | None] = mapped_column(Date)
    last_date: Mapped[date | None] = mapped_column(Date)
    n_days: Mapped[int] = mapped_column(Integer, server_default="0")
    dominant_variety: Mapped[str | None] = mapped_column(Text)
    dominant_grade: Mapped[str | None] = mapped_column(Text)
    eligible_for_forecast: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    eligibility_reason: Mapped[str | None] = mapped_column(Text)
    eligibility_origin: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("commodity_id", "market_id"),)


class PriceObservation(Base):
    """One cleaned row per (non-superseded) source row. Flags explain anything unusual; nothing is filled in."""

    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("price_series.id"))
    date: Mapped[date] = mapped_column(Date)
    variety: Mapped[str] = mapped_column(Text)
    grade: Mapped[str] = mapped_column(Text)
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    modal_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    source_observation_id: Mapped[int] = mapped_column(ForeignKey("source_observations.id"), unique=True)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    __table_args__ = (
        Index("uq_price_obs_primary", "series_id", "date", unique=True, postgresql_where=text("is_primary")),
        Index("ix_price_obs_series_date", "series_id", "date"),
        Index("ix_price_obs_date_brin", "date", postgresql_using="brin"),
    )


# --- Phase 4: forecasting (Alembic revision 0003) -------------------------------------------------------------

MODEL_STATUSES = ("candidate", "production", "archived", "rejected")


class ModelVersion(Base):
    """A trained forecasting model: artifact file (+ SHA-256), training data window, the code hash, and the
    evaluation it is served with. One `production` version per commodity (partial unique index)."""

    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    commodity_id: Mapped[int] = mapped_column(ForeignKey("commodities.id"))
    horizon_days: Mapped[int] = mapped_column(Integer)
    training_cutoff: Mapped[date] = mapped_column(Date)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    artifact_path: Mapped[str] = mapped_column(Text)
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    code_sha256: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSONB)
    training: Mapped[dict] = mapped_column(JSONB)
    evaluation: Mapped[dict] = mapped_column(JSONB)
    interval_method: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    gate: Mapped[dict | None] = mapped_column(JSONB)  # promotion gate result (Phase 5, C10b)
    status_reason: Mapped[str | None] = mapped_column(Text)
    mlflow_registry_version: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        CheckConstraint(f"status in {MODEL_STATUSES}", name="ck_model_status"),
        CheckConstraint("interval_method in ('conditional', 'unconditional')", name="ck_model_interval_method"),
        CheckConstraint("horizon_days between 1 and 14", name="ck_model_horizon"),
        CheckConstraint("valid_from <= valid_to", name="ck_model_validity"),
        Index("uq_model_production", "commodity_id", unique=True, postgresql_where=text("status = 'production'")),
    )


class ForecastRun(Base):
    """One publication of forecasts by one model version at one origin (a Monday), with its interval calibration."""

    __tablename__ = "forecast_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id"))
    commodity_id: Mapped[int] = mapped_column(ForeignKey("commodities.id"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))  # None: last-value only
    origin: Mapped[date] = mapped_column(Date)
    data_complete_through: Mapped[date] = mapped_column(Date)
    series_eligible: Mapped[int] = mapped_column(Integer)
    series_forecast: Mapped[int] = mapped_column(Integer)
    calibration: Mapped[dict] = mapped_column(JSONB)
    decisions: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # fallback per day range
    drift: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("model_version_id", "origin", name="uq_forecast_run_model_origin"),
                      UniqueConstraint("commodity_id", "origin", name="uq_forecast_run_commodity_origin"))


class Forecast(Base):
    """One forecast point. Kept after its target date so real forecast error can be monitored (Phase 5)."""

    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("forecast_runs.id"))
    series_id: Mapped[int] = mapped_column(ForeignKey("price_series.id"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    issued_on: Mapped[date] = mapped_column(Date)
    target_date: Mapped[date] = mapped_column(Date)
    horizon_days: Mapped[int] = mapped_column(Integer)
    last_date: Mapped[date] = mapped_column(Date)
    last_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    point: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    lower80: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    upper80: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    lower90: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    upper90: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    volatility_tercile: Mapped[int | None] = mapped_column(Integer)
    model_point: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # the model's value, also when not served
    served_by: Mapped[str] = mapped_column(String(16), server_default="model")
    __table_args__ = (
        UniqueConstraint("series_id", "model_version_id", "issued_on", "target_date", name="uq_forecast_point"),
        UniqueConstraint("run_id", "series_id", "target_date", name="uq_forecast_run_series_target"),
        CheckConstraint("served_by in ('model', 'last_value')", name="ck_forecast_served_by"),
        CheckConstraint("horizon_days between 1 and 14", name="ck_forecast_horizon"),
        CheckConstraint("target_date = issued_on + horizon_days", name="ck_forecast_target"),
        CheckConstraint("lower90 <= lower80 AND lower80 <= upper80 AND upper80 <= upper90",
                        name="ck_forecast_interval_order"),
        Index("ix_forecasts_series_issued", "series_id", "issued_on"),
        Index("ix_forecasts_run", "run_id"),
    )


# --- Phase 5: MLOps (Alembic revision 0004) -------------------------------------------------------------------

MODEL_EVENTS = ("registered", "promoted", "rejected", "rolled_back", "archived", "fallback")


class ModelEvent(Base):
    """Audit trail of model lifecycle decisions (registration, gate outcome, promotion, rollback)."""

    __tablename__ = "model_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commodity_id: Mapped[int] = mapped_column(ForeignKey("commodities.id"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"))
    event: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint(f"event in {MODEL_EVENTS}", name="ck_model_event"),
                      Index("ix_model_events_commodity", "commodity_id", "created_at"))


class MonitoringSnapshot(Base):
    """Live accuracy of published forecasts whose targets have been reported, per crop and day range."""

    __tablename__ = "monitoring_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date)
    commodity_id: Mapped[int] = mapped_column(ForeignKey("commodities.id"))
    day_range: Mapped[str] = mapped_column(String(8))
    origins: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    wape_model: Mapped[float | None]
    wape_last_value: Mapped[float | None]
    wape_served: Mapped[float | None]
    coverage80: Mapped[float | None]
    coverage90: Mapped[float | None]
    direction_moves_ge_2pct: Mapped[float | None]
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("as_of", "commodity_id", "day_range", name="uq_monitoring_day"),
                      CheckConstraint("day_range in ('1-7', '8-14')", name="ck_monitoring_day_range"))


# --- Phase 6: users and alerts (Alembic revision 0005) ---------------------------------------------------------

class User(Base):
    """A Telegram user. Only the chat id is stored (no names or usernames)."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    language: Mapped[str] = mapped_column(String(8), server_default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginCode(Base):
    """One-time dashboard login code issued by the bot; only its SHA-256 is stored."""

    __tablename__ = "login_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    """Dashboard session; only the token's SHA-256 is stored."""

    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


ALERT_CONDITIONS = ("price_above", "price_below", "forecast_change_pct")


class AlertSubscription(Base):
    __tablename__ = "alert_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    series_id: Mapped[int] = mapped_column(ForeignKey("price_series.id"))
    condition_type: Mapped[str] = mapped_column(String(24))
    threshold: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_evaluated_date: Mapped[date | None] = mapped_column(Date)
    last_condition: Mapped[bool | None] = mapped_column(Boolean)
    last_forecast_run_id: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("user_id", "series_id", "condition_type", "threshold", name="uq_alert_subscription"),
        CheckConstraint(f"condition_type in {ALERT_CONDITIONS}", name="ck_alert_condition"),
        CheckConstraint("threshold > 0", name="ck_alert_threshold_positive"),
        Index("ix_alert_subscriptions_user", "user_id"),
    )


class NotificationHistory(Base):
    """Every alert message, keyed by an idempotency key so a re-run never messages a user twice."""

    __tablename__ = "notification_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("alert_subscriptions.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status in ('pending', 'sent', 'failed')", name="ck_notification_status"),
                      Index("ix_notification_user_created", "user_id", "created_at"),
                      Index("ix_notification_status", "status"))
