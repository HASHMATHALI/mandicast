# MandiCast — Product Requirements

Version 0.1 (Phase 1 draft, 2026-11-26).

## 1. Problem

Farmers and small traders often sell without knowing recent price movements, how nearby mandis compare, or
where prices may head over the next one to two weeks. Official prices exist (AGMARKNET, reported daily by
APMCs) but are hard to explore, have gaps and inconsistent names, and come with no forward view.

MandiCast makes that official data easy to read, compares mandis, and offers 7- and 14-day forecasts only
where the history supports them, with intervals that say how uncertain they are.

## 2. Users

| User | Main question | Typical device |
|---|---|---|
| Farmer | "Is today's price in my mandi good, and is a nearby mandi better?" | Low-end Android phone, mobile data |
| Farmer Producer Organisation (FPO) staff | "Where and when should our members sell this crop?" | Phone and laptop |
| Small trader | "How are prices moving across mandis for this crop?" | Phone |
| Market researcher / student | "What do the series look like, and how good are the forecasts?" | Laptop |

## 3. Scope for version 1

**Data:** the official AGMARKNET daily price resource on data.gov.in (min, max and modal price per mandi,
commodity, variety and grade). v1 starts with the five commodities profiled in Phase 1 (Onion, Tomato,
Potato, Wheat, Soyabean) and can add more through configuration once each passes the same profiling checks.
Arrival quantities are out of scope (no bulk official source; see data source assessment §1).

**Features:**

| # | Feature | Requirement |
|---|---|---|
| F1 | Crop, state, district and mandi discovery | Search and filters using cleaned names; source spellings kept and shown on request |
| F2 | Historical prices | Daily min / modal / max chart and table per mandi and crop, with gaps shown as gaps, never filled silently |
| F3 | Current reported prices | Latest reported date per mandi, clearly labelled with its date |
| F4 | Mandi comparison | Same crop across several mandis, on the same dates |
| F5 | 7- and 14-day forecasts | Point forecast of the modal price plus a prediction interval at a stated level, for series that pass the sufficiency rules |
| F6 | "Not enough data" state | Series that fail the sufficiency rules show why, instead of a forecast |
| F7 | Forecast accuracy information | For every forecast: the model, its walk-forward error for that series group, and how it compares with the naive baseline |
| F8 | Trends | Plain-language summaries computed from data (e.g. "modal price is 12% above its 30-day average"), with no predictions in them |
| F9 | Data freshness | Last successful ingestion, latest arrival date per mandi, and a warning when a mandi has stopped reporting |
| F10 | Price alerts (Telegram) | User-defined thresholds on reported prices (e.g. "Onion modal at Lasalgaon ≥ ₹2,500/quintal") and explicitly described forecast conditions; never phrased as guarantees |
| F11 | Dashboard | Mobile-first web UI covering F1–F10, usable by non-technical users |

## 4. Non-goals
- No trading, selling or financial advice, and no "best day to sell" claims.
- No guaranteed or single-number futures: every forecast carries an interval and a disclaimer.
- No deep learning only for appearance; models are chosen by walk-forward results against baselines.
- No personal data beyond what alerts need (Telegram chat id and chosen preferences).

## 5. Honesty and safety requirements
1. Historical observations, current reported prices, forecasts and intervals are visually distinct everywhere.
2. Missing prices are never invented. Cleaned or corrected values are stored separately from the source values.
3. Forecasts are shown only for series meeting the sufficiency rules in the forecasting methodology.
4. Accuracy shown to users comes from walk-forward evaluation, never from training fit.
5. Source attribution (GODL-India and agmarknet.gov.in) is shown on every page that shows data, with the
   source's disclaimer that prices are reported by the APMCs.

## 6. Success measures (reported truthfully, per crop / mandi group / horizon)

| Measure | Target |
|---|---|
| Forecast error vs naive baseline (MAE, WAPE) | Better than last-value and seasonal-naive baselines on walk-forward folds, per horizon; where it is not, the baseline is served |
| Directional accuracy (up / down vs last observed) | ≥ 90% if achievable; reported as measured, with the share of cases where the move was too small to call |
| Prediction-interval coverage | Within ±2 percentage points of the stated nominal level (e.g. 80% or 90%) on walk-forward folds |
| Data freshness | Daily ingestion completes the same day the source updates; lag reported |
| Ingestion correctness | Re-running a job creates no duplicate rows (tested) |

The 90% directional target is ambitious for daily mandi prices; the evaluation report will state the measured
value and the reasons if it is not met.

## 7. Constraints
- Source licence: Government Open Data License – India (attribution, non-endorsement, no warranty).
- Source quality: APMC-reported values contain errors, renamed places, and gaps (data source assessment §5).
- Runs on a laptop without GPU; everything ships in Docker Compose.
- Secrets (data.gov.in key, Telegram token) only in `.env`, never committed or logged.

## 8. Open questions for later phases
- Which interval level to show by default (80% is easier to read, 90% is safer).
- Whether to show ₹/kg next to ₹/quintal by default (likely yes for farmers).
- Hindi and other language support for the dashboard and bot (v1: English, with labels kept translatable).
