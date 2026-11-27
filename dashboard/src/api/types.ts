// Names for the API's schemas (generated from its OpenAPI document, see scripts/gen-api.mjs).
import type { components } from "./schema";

type S = components["schemas"];

export type Commodity = S["Commodity"];
export type StateItem = S["StateItem"];
export type MarketItem = S["MarketItem"];
export type MarketPage = S["Page_MarketItem_"];
export type MarketDetail = S["MarketDetail"];
export type MarketCommodity = S["MarketCommodity"];
export type PriceItem = S["PriceItem"];
export type PricesResponse = S["PricesResponse"];
export type LatestItem = S["LatestItem"];
export type LatestResponse = S["LatestResponse"];
export type CompareResponse = S["CompareResponse"];
export type TrendsResponse = S["TrendsResponse"];
export type ForecastResponse = S["ForecastResponse"];
export type ForecastPoint = S["ForecastPoint"];
export type Accuracy = S["Accuracy"];
export type LiveAccuracy = S["LiveAccuracy"];
export type EligibilityResponse = S["EligibilityResponse"];
export type FreshnessResponse = S["FreshnessResponse"];
export type ModelSummary = S["ModelSummary"];
export type MonitoringResponse = S["MonitoringResponse"];
export type Source = S["Source"];
export type AlertOut = S["AlertOut"];
export type AlertIn = S["AlertIn"];
export type ConditionType = S["Condition"]["type"];
export type NotificationOut = S["NotificationOut"];
export type SessionOut = S["SessionOut"];

/** `/v1/models/{version}/evaluation` is published as a free-form object; these are the fields the dashboard reads. */
export interface HorizonScore {
  h: number;
  n: number;
  wape: number;
  coverage80?: number;
  coverage90?: number;
  mase_vs_last_value?: number;
  dir_acc_moves_ge_2pct?: number | null;
}

export interface ModelEvaluation {
  model: ModelSummary;
  evaluation: { period: string; served: HorizonScore[]; last_value: HorizonScore[]; report: string };
  gate: { passed: boolean | null; failed: string[]; note?: string } | null;
}
