"""Response models (they generate the OpenAPI document at /openapi.json and validate every response)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Source(BaseModel):
    provider: str
    dataset: str
    repository: str
    url: str
    licence: str
    licence_url: str
    notice: str


class DataAsOf(BaseModel):
    latest_arrival_date: date | None
    last_successful_ingestion_at: datetime | None


class Commodity(BaseModel):
    id: int
    name: str
    price_unit: str
    series: int
    eligible_series: int
    first_date: date | None
    last_date: date | None
    has_forecasts: bool


class Named(BaseModel):
    id: int
    name: str


class StateItem(Named):
    markets: int


class DistrictItem(Named):
    state_id: int
    markets: int


class MarketItem(Named):
    district: Named
    state: Named


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(None, description="Pass as `cursor` to get the next page; null on the last page")


class Alias(BaseModel):
    name_as_reported: str
    rule: str = Field(description="exact, whitespace, apmc_suffix, state_rename or manual")
    last_seen: date | None


class MarketCommodity(BaseModel):
    commodity_id: int
    commodity: str
    series_id: int
    first_date: date | None
    last_date: date | None
    report_days: int
    eligible_for_forecast: bool
    eligibility_reason: str | None


class MarketDetail(Named):
    district: Named
    state: Named
    aliases: list[Alias]
    commodities: list[MarketCommodity]


class SeriesRef(BaseModel):
    series_id: int
    commodity_id: int
    commodity: str
    market_id: int
    market: str
    district_id: int
    district: str
    state_id: int
    state: str
    dominant_variety: str | None
    dominant_grade: str | None


class PriceItem(BaseModel):
    date: date
    min_price: float | None
    max_price: float | None
    modal_price: float
    variety: str
    grade: str
    flags: list[str]
    source_observation_id: int = Field(description="Row in source_observations (the value exactly as received)")


class PricesResponse(BaseModel):
    series: SeriesRef
    unit: str
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    rows: Literal["primary", "variety_grade"]
    include_flagged: bool
    items: list[PriceItem]
    next_cursor: str | None
    data_as_of: DataAsOf
    source: Source

    model_config = {"populate_by_name": True}


class LatestItem(BaseModel):
    series_id: int
    market: MarketItem
    date: date
    days_behind_latest: int
    min_price: float | None
    max_price: float | None
    modal_price: float
    variety: str
    grade: str
    flags: list[str]


class LatestResponse(BaseModel):
    commodity: Named
    unit: str
    latest_arrival_date: date | None
    max_age_days: int
    items: list[LatestItem]
    next_cursor: str | None
    data_as_of: DataAsOf
    source: Source


class CompareMarket(BaseModel):
    market_id: int
    market: str
    state: str
    series_id: int
    days_reported_in_range: int


class CompareItem(BaseModel):
    date: date
    modal_prices: list[float] = Field(description="Same order as `markets`")


class CompareResponse(BaseModel):
    commodity: Named
    unit: str
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    markets: list[CompareMarket]
    common_dates: int
    items: list[CompareItem]
    data_as_of: DataAsOf
    source: Source

    model_config = {"populate_by_name": True}


class WindowStat(BaseModel):
    reports: int
    average: float | None
    change_pct: float | None = Field(description="Latest price vs the window average, in percent")


class RangeStat(BaseModel):
    reports: int
    low: float | None
    high: float | None
    position_pct: float | None = Field(description="0 = at the 52-week low, 100 = at the high")


class TrendsResponse(BaseModel):
    series: SeriesRef
    unit: str
    latest: PriceItem | None
    vs_previous_7_days: WindowStat
    vs_previous_30_days: WindowStat
    range_52_weeks: RangeStat
    volatility_91_days_pct: float | None = Field(
        description="Standard deviation of log changes between consecutive reports over 91 days, in percent")
    definitions: dict[str, str]
    note: str
    data_as_of: DataAsOf
    source: Source


class Interval(BaseModel):
    level: float
    lower: float
    upper: float


class ForecastPoint(BaseModel):
    date: date
    horizon_days: int
    modal_price: float
    served_by: Literal["model", "last_value"] = "model"
    model_modal_price: float | None = Field(
        None, description="The model's own forecast when the last value was served instead (fallback rule C10c)")
    intervals: list[Interval]
    actual_modal_price: float | None = Field(
        None, description="The reported price on that date, once known (unflagged primary value)")


class ModelRef(BaseModel):
    version: str
    kind: str
    training_cutoff: date
    valid_from: date
    valid_to: date


class Accuracy(BaseModel):
    evaluated_on: str
    horizon_days: int
    method: Literal["model", "last_value"] = Field(description="The method that served this horizon")
    points: int
    wape: float = Field(description="Weighted absolute percentage error of the serving method on the hold-out year")
    baseline_wape: float = Field(description="Same, for the last reported price used as the forecast")
    beats_baseline: bool | None
    mase_vs_last_value: float
    direction_accuracy_moves_ge_2pct: float | None
    interval_coverage: dict[str, float] | None = Field(
        description="Share of actual prices inside the model's 80% and 90% ranges on the hold-out year")
    report: str


class LiveAccuracy(BaseModel):
    as_of: date
    day_range: str
    origins: int
    points: int
    wape_model: float | None
    wape_last_value: float | None
    wape_served: float | None
    coverage80: float | None
    coverage90: float | None
    note: str


class ForecastResponse(BaseModel):
    series: SeriesRef
    unit: str
    horizon_days: int
    issued_on: date = Field(description="Forecast origin: inputs are reports dated on or before this Monday")
    data_complete_through: date
    published_at: datetime
    last_observed: dict
    model: ModelRef | None = Field(description="null when no valid model existed and the last value was served")
    served_by: dict[str, str] = Field(description="Method per day range: model or last_value")
    fallback: dict | None = Field(None, description="Fallback decisions (C10c) for the day ranges shown")
    points: list[ForecastPoint]
    accuracy: Accuracy | None
    live_accuracy: LiveAccuracy | None
    drift: dict | None
    warnings: list[str]
    disclaimer: str
    source: Source


class EligibilityResponse(BaseModel):
    series: SeriesRef
    eligible: bool
    reason: str | None
    evaluated_at_origin: date | None
    rules: dict[str, int | float]
    stats: dict
    latest_forecast_origin: date | None
    in_latest_forecast: bool


class ModelSummary(BaseModel):
    version: str
    kind: str
    commodity_id: int
    commodity: str
    status: str
    horizon_days: int
    training_cutoff: date
    valid_from: date
    valid_to: date
    promoted_at: datetime | None
    interval_method: str
    best_iteration: int | None
    training_examples: int | None
    artifact_sha256: str
    code_sha256: str
    latest_forecast_origin: date | None
    status_reason: str | None = None
    gate_passed: bool | None = None
    mlflow_registry_version: str | None = None


class ModelEventItem(BaseModel):
    created_at: datetime
    event: str
    version: str | None
    reason: str | None


class ModelHistory(BaseModel):
    commodity: Named
    versions: list[ModelSummary]
    events: list[ModelEventItem]


class MonitoringCommodity(BaseModel):
    commodity_id: int
    commodity: str
    live: list[dict] = Field(description="Latest live accuracy per day range (published forecasts only)")
    latest_publication: dict | None
    production_model: dict | None
    recent_events: list[ModelEventItem]
    warnings: list[dict]


class MonitoringResponse(BaseModel):
    checked_at: datetime | None = Field(description="When the last monitor job ran (missing-update checks)")
    global_warnings: list[dict]
    commodities: list[MonitoringCommodity]
    rules: str


class FreshnessCommodity(BaseModel):
    commodity_id: int
    commodity: str
    latest_arrival_date: date | None
    latest_complete_date: date | None
    series: int
    reported_last_14_days: int
    stopped_reporting: int = Field(description="Series whose last report is 15–90 days before the latest date")
    eligible_series: int
    latest_forecast_origin: date | None


class FreshnessResponse(BaseModel):
    last_successful_ingestion: dict | None
    last_forecast_publication: dict | None
    commodities: list[FreshnessCommodity]
    source: Source


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checks: dict
