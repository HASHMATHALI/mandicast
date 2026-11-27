# MandiCast — Forecasting Evaluation Report (Phase 3)

Date: 2026-11-26. Method: `docs/06_forecasting_methodology.md` (pre-registered; every change is dated in §9 before
the results it governs). Full tables: `experiments/forecast/EVALUATION_TABLES.md` (generated). Raw results:
`experiments/forecast/` (predictions per model, crop and period as Parquet, 77 MB, kept on the development machine and
not in git; the JSON reports, `frozen_choices.json` and `DEV_LOG.md` are in git).
MLflow: `experiments/mlflow.db`, experiment `mandicast-forecast`. Every table below is generated from those files.

## 1. Summary

- **Hold-out year (1 Dec 2025 – 23 Nov 2026), scored once, after every choice was frozen.** The frozen model is the
  global LightGBM for all five crops and both day ranges. The code SHA-256 at evaluation equals the one at freezing.
- **Against the naive "last price" forecast it is better in 9 of 10 crop × horizon cases** (MASE 0.885–0.993) and
  slightly worse in one: Tomato at 7 days (MASE 1.008; WAPE 15.9% vs 15.8%).
- **Direction of moves of at least 2%: 61–77% correct.** The ≥ 90% aspiration is **not met**; no method in this
  project came close (best development figure about 75%).
- **Intervals under-cover.** 80% intervals contained 74.0–78.5% of hold-out prices for four crops (1.5–6.0 points
  short) and 55.7–61.5% for Soyabean; 90% intervals contained 86.1–88.5% (four crops) and 69.0–75.1% (Soyabean).
  The ±2-point target is met only for Potato h7 (80%: 78.5%; 90%: 88.5%) and, exactly at the limit, Onion h7
  (80%: 78.0%). Cause analysed in §6.
- **Closeness of the forecast price (1 − WAPE):** at least 90% for Onion (7 days), Potato, Wheat and Soyabean;
  89.7% for Onion at 14 days; 84.1% and 78.9% for Tomato. Most of this closeness is already achieved by the last
  price; the model's contribution is the improvement over it that MASE shows.

## 2. Hold-out results of the frozen forecast

Points are (mandi, origin) pairs with a report on day h; targets flagged as data-entry outliers are excluded
(methodology C5; including them changes WAPE by at most 0.17 points, Tomato h7). Intervals: volatility-conditional
split conformal, calibrated on the residuals of the last four development quarters (2025Q1–2025Q4).

| Crop | h | Points | WAPE (LightGBM) | WAPE (last value) | MASE | 1 − WAPE | Direction, moves ≥ 2% | 80% coverage | 90% coverage | 80% width |
|---|---|---|---|---|---|---|---|---|---|---|
| Onion | 7 | 23,314 | 7.7% | 8.0% | 0.960 | 92.3% | 69.9% | 78.0% | 87.8% | 27.0% |
| Onion | 14 | 22,656 | 10.3% | 11.1% | 0.923 | 89.7% | 71.6% | 75.9% | 86.1% | 33.5% |
| Tomato | 7 | 23,321 | 15.9% | 15.8% | 1.008 | 84.1% | 62.9% | 76.1% | 87.6% | 50.5% |
| Tomato | 14 | 22,648 | 21.1% | 21.3% | 0.993 | 78.9% | 64.6% | 76.9% | 87.1% | 69.7% |
| Potato | 7 | 24,643 | 7.1% | 7.4% | 0.963 | 92.9% | 68.7% | 78.5% | 88.5% | 23.8% |
| Potato | 14 | 23,951 | 8.8% | 9.3% | 0.947 | 91.2% | 67.3% | 77.4% | 87.4% | 29.4% |
| Wheat | 7 | 15,605 | 2.3% | 2.6% | 0.893 | 97.7% | 77.3% | 76.4% | 87.9% | 6.6% |
| Wheat | 14 | 15,189 | 2.8% | 3.2% | 0.885 | 97.2% | 76.6% | 74.0% | 87.2% | 7.8% |
| Soyabean | 7 | 4,786 | 5.0% | 5.5% | 0.909 | 95.0% | 67.8% | 61.5% | 75.1% | 10.3% |
| Soyabean | 14 | 4,624 | 7.0% | 7.4% | 0.935 | 93.0% | 61.0% | 55.7% | 69.0% | 12.7% |

## 3. Setup

- **Data:** 14.8M official AGMARKNET rows (Phase 2), one primary price per mandi-day; a mandi is evaluated at an
  origin only if eligible with data up to that origin (C1). Development: 150 Monday origins (2 Jan 2023 – 10 Nov
  2025, so every target falls by 30 Nov 2025); hold-out: 52 origins (1 Dec 2025 – 23 Nov 2026).
- **Models:** last value, seasonal naive, 7- and 28-day means; AutoETS (all series) and AutoARIMA (fixed sample of
  60 series per crop, D3), both re-estimated quarterly (D1); global LightGBM per crop, retrained quarterly with early
  stopping (C6). Features use only reports dated up to the origin; tests check that deleting all later data changes
  nothing.
- **Selection** (development only): lowest mean quarterly WAPE, provided the model beats last value in at least 60% of
  quarters (§4). **Intervals:** split conformal on log residuals; volatility-conditional where the unconditional
  method missed nominal coverage by more than 5 points in any volatility tercile (C7), which was the case for every crop.

## 4. Development results (model choice)

Mean of quarterly WAPE, headline horizons, outlier targets excluded. ARIMA is compared on its sample only (D3).

| Crop | h | LightGBM | ETS | last value | 7-day mean | ARIMA (sample) |
|---|---|---|---|---|---|---|
| Onion | 7 | 9.0% | 9.2% | 9.3% | 9.9% | 9.3% (LightGBM on the same sample 9.0%) |
| Onion | 14 | 11.9% | 12.5% | 12.6% | 13.1% | 12.6% (LightGBM on the same sample 12.0%) |
| Tomato | 7 | 16.4% | 18.0% | 18.1% | 19.9% | 17.9% (LightGBM on the same sample 16.4%) |
| Tomato | 14 | 23.0% | 26.3% | 26.4% | 27.8% | 26.3% (LightGBM on the same sample 22.8%) |
| Potato | 7 | 7.0% | 7.4% | 7.6% | 7.9% | 7.7% (LightGBM on the same sample 7.1%) |
| Potato | 14 | 8.9% | 9.8% | 10.0% | 10.2% | 10.4% (LightGBM on the same sample 9.2%) |
| Wheat | 7 | 2.4% | 2.7% | 2.8% | 2.7% | 2.6% (LightGBM on the same sample 2.4%) |
| Wheat | 14 | 3.0% | 3.3% | 3.4% | 3.3% | 3.2% (LightGBM on the same sample 2.9%) |
| Soyabean | 7 | 2.9% | 3.2% | 3.4% | 3.1% | 3.2% (LightGBM on the same sample 2.9%) |
| Soyabean | 14 | 3.4% | 3.8% | 4.0% | 3.8% | 3.9% (LightGBM on the same sample 3.5%) |

LightGBM had the lowest development error for every crop and horizon and beat last value in 83–100% of quarters;
it was frozen for all crops and both day ranges. ETS was consistently a little better than last value; ARIMA was
never better than ETS on its sample; seasonal naive was far worse than everything else (development MASE about 1.1–5.0).

## 5. All models on the hold-out (7 days, WAPE, same points)

| Crop | LightGBM | ETS | last value | 7-day mean | 28-day mean | seasonal naive | best |
|---|---|---|---|---|---|---|---|
| Onion | 7.70% | 8.04% | 8.02% | 8.67% | 11.87% | 43.65% | gbm |
| Tomato | 15.93% | 15.89% | 15.81% | 17.26% | 21.00% | 38.92% | last_value |
| Potato | 7.11% | 7.24% | 7.38% | 7.65% | 9.59% | 31.25% | gbm |
| Wheat | 2.34% | 2.53% | 2.62% | 2.52% | 3.03% | 7.86% | gbm |
| Soyabean | 5.04% | 5.53% | 5.54% | 5.65% | 7.30% | 28.22% | gbm |

## 6. Where it fails, and why

**Soyabean: a change of regime.** LightGBM's 7-day WAPE rose quarter by quarter through the hold-out year: 3.5% in
Dec 2025, then 4.4%, 5.4%, 5.6% and 5.9% in 2026Q4, against 2.8% in development. Soyabean prices became much more
volatile than in the calibration year (2025), so intervals calibrated on 2025 covered 78% of prices in Dec 2025 but
only 52% by 2026Q4. A calibration fixed in advance cannot follow such a shift. Fewer Soyabean mandis were eligible in
the hold-out (4,786 points at h7 vs 17,121 in development), which also makes its figures noisier.

**Tomato at 7 days.** In development LightGBM cut WAPE from 18.1% (last value) to 16.4%; in the hold-out it was
marginally worse than last value (15.9% vs 15.8%), with its worst quarter in 2026Q2 (WAPE 18.9%). Tomato prices
jump with weather and supply shocks that none of the features capture.

**Direction.** For moves of at least 2%, 61–77% were called correctly. Mandi prices move on information that is not
in past prices (arrivals, weather, policy); the 90% aspiration would need such data, and even then is unlikely at
7–14 days.

**Intervals, all crops.** Every crop under-covered somewhat in the hold-out, consistent with 2026 being more volatile
than the 2025 calibration window. Development coverage had been close to nominal (76.6–84.1% at 80%), but see §8.

## 7. Deviations, bugs found, and how they were handled

All recorded in `docs/06_forecasting_methodology.md` §9 and `experiments/forecast/DEV_LOG.md` before the results
they affect, except where stated:
- D1 quarterly re-estimation of ETS and ARIMA (cost); D2 calendar features instead of uncited harvest calendars;
  D3 ARIMA on a fixed sample of 60 series per crop (about 12 hours for development otherwise).
- C5 headline metrics exclude data-entry outliers (recorded after the baselines were scored and before any other
  model; both versions are reported).
- **Bug found in development review, before freezing:** the LightGBM `state` feature had order-dependent codes;
  fixed, test added, LightGBM development run repeated (effect on WAPE at most 0.0005).
- Orphaned worker processes from stopped runs slowed one run; process trees are now stopped as a whole.
- **Description error found in Phase 5 (2026-11-27):** C6 describes LightGBM's early-stopping set as origins 15–105
  days before the training cutoff; the evaluated code uses origins in the 105 days before the cutoff with targets
  before it. No look-ahead either way; the results above are those of the code as run.

## 8. Limitations

- **Development interval calibration overlapped by up to 14 days:** a quarter's calibration residuals came from the
  preceding quarters' origins, whose targets can fall up to 14 days into the quarter being covered. This affects only
  the development coverage figures, and therefore the C7 choice of conditional intervals; the **hold-out intervals are
  clean** (calibration targets end 30 Nov 2025, hold-out origins start 1 Dec 2025). It was found while writing this
  report, after the hold-out was scored, and is not changed retroactively; Phase 5 fixes it (calibration residuals
  restricted to target dates before the origin).
- One hold-out year; Soyabean results rest on fewer points.
- Only past prices and calendar features: no arrivals (not available in bulk), weather or policy data.
- AGMARKNET values are as reported by the APMCs; 0.01–0.66% of targets are flagged as likely data-entry errors.

## 9. Consequences for the next phases

- Serve LightGBM, but **monitor its rolling error against last value per crop** and fall back to last value when the
  model is worse over recent weeks (Tomato h7 would have triggered this) — Phase 5.
- **Recalibrate intervals continuously** on recent residuals whose target dates are before the origin, and monitor
  coverage; Soyabean shows that a fixed calibration breaks under a change of volatility — Phase 5.
- In the product, show the measured accuracy, direction rate and interval coverage next to each forecast.
