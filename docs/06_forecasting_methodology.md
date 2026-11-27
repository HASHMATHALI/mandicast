# MandiCast — Forecasting Methodology (pre-registered)

Version 0.1 (Phase 1, 2026-11-26). Written before any model is trained. Changes after Phase 3 results are
seen go into §9 as dated deviations, never by editing the rules above them.

## 1. What is forecast

- **Unit:** one series = (commodity, mandi). Variety and grade are kept as attributes, not as part of the
  series key, because the Phase 1 profile showed (a) variety labels were renamed in 2024–26 (Onion "Red" fell
  from 57k rows in 2025 to 10k in 2026 while "Bellary" rose), which splits variety-level series, and (b) only
  4.4% of Onion market-days in the last two years carry more than one variety.
- **One value per mandi-day:** rows with invalid prices are dropped; if several rows remain, the mandi's most
  frequent (variety, grade) over the previous 365 days is used, otherwise the median modal price of the rows.
- **Target:** the reported **modal price** (₹/quintal) on each calendar day of the next 14 days after the
  forecast origin `t` (the last date with data). Headline horizons are **h = 7** and **h = 14**; metrics are
  also reported for days 1–7 and 8–14.
- **Evaluated only on real reports.** Mandis do not report every day. A forecast for a day without a
  report is not scored, and no missing value is ever filled in to create a target.
- **Model inputs may use a regularised view** (e.g. last observation carried forward, with an "age of last
  report" feature) because statistical models need evenly spaced input. This view exists only inside the
  model input; it is never stored as data or shown to users.

## 2. Eligibility (sufficiency rules)

A series gets forecasts only if, at the forecast origin:

| Rule | Working threshold | Reason |
|---|---|---|
| Recently active | Last report ≤ 14 days before the origin | Stale series have no current level |
| Enough recent history | ≥ 250 reported days in the previous 730 days (about one report every 3 days) | Need a level, trend and some seasonality |
| Regular reporting | Median gap between reports ≤ 3 days over the previous 365 days | Large gaps make 7–14 day targets unreliable |
| Clean enough | ≤ 5% of the previous 365 days flagged (invalid price, conflicting same-day reports, local outlier) | Flag-heavy series are mostly noise |

Series failing any rule return `insufficient_data` with the rule that failed. The thresholds were set from
the Phase 1 coverage profile (§8), before any model was trained, and are now frozen. The history rule was
first drafted as ≥ 365 days; the profile showed that this leaves 128 of 981 active Onion mandis, while ≥ 250
days leaves 334, still with a median gap ≤ 3 days. It was chosen on reporting coverage only, not on any
forecast result.

**Provisional latest days.** The last one or two arrival dates are incomplete when downloaded (Onion: 547
and 633 rows on 25 and 24 Nov 2026 vs ~780 on other weekdays), because mandis report late. The forecast
origin is the latest date whose row count is at least 85% of the median count of the same weekday in the
previous four weeks (implemented in `latest_complete_date`, `src/mandicast/clean/build.py`).

## 3. Evaluation design: walk-forward, no future leakage

```
2001 ........ 2022-12-31 | 2023-01-01 ....... 2025-11-30 | 2025-12-01 ...... 2026-11-25
   training history only  |  development folds (model     |  final hold-out: scored once,
                          |  choice, tuning, intervals)   |  after all choices are frozen
```

- **Origins:** every 7 days (Mondays) inside each window. At an origin `t`, every feature uses data with
  `date ≤ t` only, and models are fitted on data `≤ t` (statistical models re-fitted at each origin;
  global models re-fitted at the start of each quarter and used for the origins inside it, which is still
  strictly before those origins).
- **Reporting delay:** prices are published with a lag. The data source assessment records the observed lag.
  The origin uses only rows whose arrival date is ≤ `t`. Rows published later for older dates (back-fills)
  are a second leakage risk, handled by using the data as it is stored now and reporting back-fill rates.
- **Hold-out:** the last 12 months (2025-12-01 to 2026-11-25) are used once, for the final report. No model,
  feature, threshold or interval setting is chosen by looking at it.
- **Splitting is by time only.** Cross-series leakage through same-day neighbours is impossible because
  every feature at origin `t` uses dates ≤ `t`.

## 4. Models (in order; each must beat the ones before it to be used)

| Model | Notes |
|---|---|
| **Last value (naive)** | Forecast = last reported modal price. The main baseline |
| **Seasonal naive** | Value reported nearest (±3 days) to the same date one year earlier, scaled by nothing. Only where the series has ≥ 1 year of history |
| **Moving average** | Mean of the reports in the last 7 and 28 days (two variants) |
| **Exponential smoothing (ETS)** | Per series on the regularised view, automatic model selection (statsforecast `AutoETS`) |
| **(S)ARIMA** | Per series, automatic order selection (`AutoARIMA`), weekly seasonality where supported |
| **Gradient boosting (global, per commodity)** | One model per commodity and horizon, trained on all eligible series. Target: log(price at t+h / last price). Features: recent lags and their ages, rolling means / volatility (7, 28, 91 days), year-ago level, min–max spread, day of week, month, week of year, festival and harvest-season indicators (from cited calendars), state-level median price of the same commodity, series identifiers as categorical features |

No deep learning unless all of the above have been evaluated and a clear reason exists.
Model choice per commodity × horizon: lowest mean WAPE across development folds, provided it beats the
last-value baseline in at least 60% of folds. Otherwise the baseline is served (and labelled as such).

## 5. Metrics (all reported per commodity, per mandi group and per horizon)

| Metric | Definition |
|---|---|
| MAE, RMSE | On ₹/quintal |
| WAPE | Σ|error| / Σ|actual| over the scored points |
| sMAPE | Mean of 2|e| / (|y| + |ŷ|) |
| MAPE | Reported because prices are strictly positive after cleaning; WAPE is the headline |
| MASE | MAE relative to the last-value baseline's MAE on the same points |
| Directional accuracy | Share of points where sign(ŷ − last) = sign(y − last). Reported two ways: all points, and only points where the actual move is ≥ 2% (smaller moves counted separately as "flat") |
| Interval coverage, width | Share of actuals inside the interval; mean width relative to the last price |

## 6. Prediction intervals

Split-conformal on log-ratio residuals: for each commodity × horizon (and volatility tercile, if coverage
is uneven without it), the interval is the point forecast adjusted by empirical quantiles of residuals from
the preceding development folds only. Nominal levels 80% and 90%. Coverage is measured on later folds and on
the hold-out; the target is within ±2 percentage points of nominal.

## 7. Targets and honesty

- Beat the naive baselines (WAPE and MASE < 1) on the hold-out, per commodity and horizon, or serve the baseline.
- Directional accuracy ≥ 90% is pursued but is not expected for daily mandi prices at 7–14 days; the measured
  value is reported as it is.
- Interval coverage within ±2 points of nominal.
- No metric is reported from training fit.

## 8. Profile-based settings (from `reports/profile/PROFILE_SUMMARY.md`, origin 2026-11-25)

Series counted with canonical mandi names (the Nov 2025 " APMC" rename linked; data source assessment §5).

| Commodity | Eligible mandis | States | Mandi-days of history | History, years p10 / p50 / p90 | ≥ 5 years |
|---|---|---|---|---|---|
| Onion | 501 | 20 | 1,383,404 | 2.4 / 16.1 / 23.9 | 335 |
| Tomato | 538 | 18 | 1,346,442 | 2.4 / 14.0 / 23.4 | 359 |
| Potato | 582 | 22 | 1,724,438 | 2.4 / 17.5 / 23.9 | 421 |
| Wheat | 324 | 8 | 1,138,677 | 12.1 / 20.5 / 23.9 | 303 |
| Soyabean | 88 | 7 | 246,480 | 2.8 / 20.6 / 23.8 | 74 |
| **Total** | **2,033** | | **5,839,441** | | **1,492** |

These counts come from the Phase 1 profiling scripts. The Phase 2 pipeline, whose flag definitions are the
authoritative ones (trailing-only outlier rule; same-day duplicates of renamed mandis not counted as invalid),
gives **2,003** eligible series at origins 23–24 Nov 2026: Onion 494, Tomato 519, Potato 578, Wheat 324,
Soyabean 88 (`docs/09_data_pipeline.md` §4). Phase 3 uses the pipeline's `price_series.eligible_for_forecast`.

Consequences fixed now:
- **The rename falls inside the evaluation design.** The 12-month hold-out (Dec 2025 – Nov 2026) is entirely
  after the 1–4 Nov 2025 rename, and the development folds are before it. Phase 2 must link old and new names
  before any Phase 3 evaluation; series whose link is not verified are evaluated separately and reported as such.
- Seasonal-naive and year-ago features need ≥ 1 year of history: 10% of eligible mandis have about 2.4 years.
- Soyabean has few eligible mandis (88: Madhya Pradesh 35, Rajasthan 20, Gujarat 18, Tamil Nadu 7, Maharashtra 6,
  Uttar Pradesh 1, Chhattisgarh 1); its results are reported
  separately and will have wide uncertainty.
- Median flagged share among eligible mandis is 0–1% (Wheat 0.5%), so the ≤ 5% rule removes few series.

## 9. Deviations and clarifications (recorded 2026-11-26, before any model was fitted or scored)

| # | Pre-registered text | Change | Reason |
|---|---|---|---|
| D1 | Statistical models re-fitted at each origin | ETS and ARIMA parameters are estimated at the start of each calendar quarter from data ≤ that date; at each weekly origin inside the quarter the fitted model is applied to the data ≤ origin (state updated, parameters not re-estimated). Same schedule as the global model | Measured on a real 2.5-year Onion series: AutoETS 0.45 s and AutoARIMA 2.7 s per fit. Refitting ~2,000 series at ~200 origins is ~400k fits (≈ 50 and 300 CPU-hours). Quarterly estimation is ~16 fits per series. Still strictly no future data |
| D2 | Harvest-season indicators "from cited calendars" | Seasonality enters through calendar features learned per commodity (month, ISO week, day of year as sine/cosine); festival indicators come from the `holidays` package (India, version recorded in each run) | No machine-readable, citable crop calendar per mandi was found; inventing harvest windows would be fabrication |
| D3 | ARIMA evaluated like the other models on every eligible series | **Recorded 2026-11-26 22:00, after ARIMA had run on 100 Onion series but before any ARIMA prediction was scored or looked at.** AutoARIMA is run on a fixed random sample of 60 series per commodity (seed 20261126, drawn from the series evaluated at ≥ 1 development origin), in the development and hold-out periods. It is compared with every other model on exactly those sample points and reported; model selection (§4) is made among the models that cover all series (baselines, ETS, LightGBM). If ARIMA beats the selected model on the sample in development, that is reported as a limitation, not used to change the choice | Measured cost: 100 Onion series in 18 min on 11 workers (each series has ~11 quarterly AutoARIMA fits on 730 daily points), i.e. ~12 h for the development period and ~5 h more for the hold-out — not feasible on the development laptop. The single-series timing used for planning (2.7 s per fit) under-estimated AutoARIMA's search cost across series |
| C1 | Which series are evaluated at an origin | A series is evaluated at origin *t* only if it passes the §2 rules computed with data ≤ *t* (not the rules as of Nov 2026, which would select series by their future) | Avoids selection on future activity |
| C2 | Origin timing | Origin *t* = a Monday; inputs use reports dated ≤ *t*; targets are reports dated *t*+1 … *t*+14. Horizon *h* = days after *t* | Fixes the convention in code and tests |
| C3 | Inputs vs targets | Primary values flagged `local_outlier` (a trailing-only flag) are not used as model inputs; every primary value counts as a target, flagged or not, so hard cases stay in the score | Cleaning inputs uses only past data; excluding hard targets would flatter the metrics |
| C4 | Training examples for the global model | One example per series per Monday origin (as in evaluation) from 2015 onward, with every reported target day *t*+1 … *t*+14 (horizon as a feature) | Bounded size on a laptop; same sampling as evaluation |
| C5 | C3: "every primary value counts as a target" (recorded the same day) | **Recorded after the four baselines were scored on development data, before any other model was run or compared.** Headline metrics exclude targets flagged `local_outlier` (trailing-only flag, identical for every model); metrics including them are always reported next to them, with the excluded share | Development data: in Onion at h = 7, 58 flagged targets (0.08%) carry 98.3% of the last-value absolute error, from one typo of ₹917,588,483/quintal; Tomato and Potato contain ₹560,000 and ₹640,000 (≈100× normal). With them, WAPE measures data-entry errors (Onion last-value WAPE 0.857 vs 0.094 without the flagged 0.08%) |
| C6 | Global gradient boosting: hyper-parameters not specified | Fixed before any run, no tuning on results: LightGBM, objective L1 on log(y / last price), learning rate 0.05, 63 leaves, min 200 rows per leaf, feature fraction 0.8, bagging 0.8, up to 1,000 trees with early stopping (50 rounds) on the examples whose origin lies in the 15–105 days before the quarter's training cutoff; trained on examples with origin ≥ 2015 and every target dated before the cutoff minus 105 days. Features use reports ≤ origin only: last price, its age, means / volatility / report counts over 7, 28, 91, 365 days, price changes over 7, 14, 28 days, year-ago value for the target date (±3 days), state and commodity-wide medians at the origin, target-date calendar (day of week, ISO week and day-of-year sine/cosine, month), `holidays` India indicators, horizon, state and series as categorical | Recorded before the model was written or run (2026-11-26) |
| C7 | §6 intervals: calibration window and volatility rule not specified | Fixed before computing any interval: residual r = log(y) − log(ŷ) on targets without the `local_outlier` flag; for a development fold, the calibration set is the residuals of the same commodity, model and horizon h from the 4 preceding quarters (at least 2, so 2023Q1–Q2 get no interval score); for the hold-out, the last 4 development quarters. Interval = ŷ · exp(q), with q the empirical (1−level)/2 and 1−(1−level)/2 quantiles (levels 80% and 90%). Volatility = standard deviation of daily log changes of the input reports over the 91 days before the origin; terciles are cut on the calibration set. Rule: if, on development folds, the coverage of any volatility tercile under the unconditional method is more than 5 percentage points from 80%, the tercile-conditional method is used for that commodity | Recorded 2026-11-26 before any interval was computed |
| C8 | §4 selection is per commodity × headline horizon; the served forecast covers days 1–14 | The model chosen at h = 7 serves days 1–7 and the model chosen at h = 14 serves days 8–14. Choices and interval methods are written to `experiments/forecast/frozen_choices.json` (with the code hash) before any hold-out prediction is made; the hold-out runs refuse to start without that file | Recorded 2026-11-26 before any selection was computed |


### 9a. Serving rules (C9, recorded 2026-11-27, after the hold-out evaluation)

These rules govern the forecasts the product publishes (Phase 4, `src/mandicast/serving/`). They were written after
the hold-out was scored, change no reported result, and are not evaluated claims: the accuracy shown with each
forecast is the hold-out accuracy of the frozen method (docs/10).

| # | Rule | Reason |
|---|---|---|
| C9a | **Origin and timing.** Forecasts are issued for the latest Monday whose data is complete (the pipeline's `latest_complete_date` ≥ that Monday), with inputs dated ≤ that Monday and targets the 14 days after it, exactly as C2. Publication therefore happens 1–3 days after the origin; days of the 14 that have already passed are shown with their reported price | Mandis report late; issuing at an incomplete Monday would use inputs the evaluation never saw |
| C9b | **Model.** The served model is the frozen method (C6, C8) trained at the start of the origin's calendar quarter on reports dated before that day; it may serve origins in that quarter only. Training refuses to start if `src/mandicast/forecast` differs from the frozen code, except for an edit listed in `experiments/forecast/post_freeze_changes.json` whose undoing reproduces the frozen SHA-256 exactly (checked on every use). The one listed edit is the docstring correction of 2026-11-26 23:50 (DEV_LOG) | The served model must be the evaluated one; a retrained model for the same quarter reproduced the hold-out fit exactly (training rows, validation rows, best iteration; `scripts/verify_phase4.py`) |
| C9c | **Intervals.** Same split-conformal method and volatility-conditional choice as frozen (C7), calibrated on the residuals of the served method for origins in the 364 days before the origin **with target dates before the origin** (removing the up-to-14-day overlap reported in docs/10 §8). Residuals come from the stored walk-forward predictions and, as they mature, from published forecasts (published values take precedence). If any (horizon, volatility tercile) group has fewer than 100 residuals, the window is extended by whole weeks, up to 728 days. **The extension was added at the first publication (2026-11-27), when Soyabean's smallest group had 97 residuals; its window became 385 days.** The minimum of 100 had itself been chosen the same day, before any publication, without a formal derivation | A fixed calibration cannot follow volatility changes (Soyabean, docs/10 §6); a rolling window does so partially. Phase 5 adds coverage monitoring |
| C9d | **Publication.** One run per (model version, origin); re-running does nothing, and published values are never overwritten, so the record of what was shown stays intact for error monitoring | Honest monitoring of live accuracy (Phase 5) |

### 9b. Operations rules (C10, recorded 2026-11-27 about 07:32 IST, before any gate, fallback or drift computation)

Written before the promotion gate, the fallback rule or the drift statistics were computed on any data. The
hold-out results (docs/10) were already known in aggregate; the thresholds below are conventional defaults chosen
without running them, and every backtest of them is reported as a simulation on stored walk-forward predictions.

| # | Rule | Reason |
|---|---|---|
| C10a | **Validation before training (G0).** Training is refused, and production left unchanged, unless: the latest complete arrival date is ≥ cutoff − 7 days; the candidate has ≥ 80% of the training examples of the current production model of the crop (if any); its early-stopping set has ≥ 1,000 examples; all labels and predictions are finite. The state list behind the categorical codes is recorded; if it differs from the champion's, the champion comparison (C10b iii) is skipped and the reason recorded | Catch data loss or stale data before a model is built from it |
| C10b | **Promotion gate.** Evaluated on the candidate's early-stopping examples E, exactly the set `gbm.train` uses: origins in the 105 days before the cutoff with targets before the cutoff, outlier targets excluded as in training (wording corrected about 07:38, see the note below the table). Promote only if (i) every prediction on E is finite and positive with \|log(ŷ / last price)\| ≤ 2; (ii) WAPE(candidate) ≤ WAPE(last value) on E; (iii) if a production model exists, WAPE(candidate) ≤ 1.02 × WAPE(production model) on E (E is out-of-sample for the production model). Otherwise the candidate is `rejected` with the reason. Known bias: E chose the candidate's number of trees, so its score on E is optimistic; the gate guards against broken or clearly worse models and is not an accuracy estimate | "Never promote a worse model" (architecture §3); roadmap exit criterion: a deliberately worse model is not promoted |
| C10c | **Fallback to last value.** At each publication for origin O, separately for days 1–7 and 8–14, compare the model with last value on the pool of points of C9c (published forecasts first, then walk-forward predictions of the same procedure) with origins in [O − 56 days, O) and targets before O, outlier targets excluded. If there are ≥ 500 points and WAPE(model) > WAPE(last value), that day range is served by last value at O. The model's forecast is still stored (`model_point`) so the comparison continues and the model returns automatically when it no longer loses. If no valid production model exists for O (none trained, rejected, or rolled back), all days are served by last value | Phase 3 recommendation (docs/10 §9); Tomato at 7 days lost to last value on the hold-out |
| C10d | **Ranges when serving last value.** Same split-conformal method, volatility-conditional choice and window rules as C9c, using the residuals of the last-value forecast from the same pool | The range must match the forecast actually shown |
| C10e | **Drift.** At each publication, population stability index (PSI, 10 bins from the reference deciles) between the origin's feature values and the model's reference (its early-stopping examples at h = 1) for `vol_28d`, `chg_7d`, `last_vs_mean_28d`, `last_vs_state_median`, `age_days`, and the number of eligible series vs the reference's mean per origin. PSI 0.10–0.25: notice; > 0.25: warning (conventional thresholds). Drift is reported (API, monitoring), never acted on automatically: no drift policy has been evaluated | Make input shifts visible without inventing an untested response |
| C10f | **Missing updates.** The monitor reports a warning when: no successful ingestion for more than 26 hours; the latest complete arrival date is more than 4 days old; the latest complete Monday has not been published; no production model is valid for the current quarter | Operations visibility |
| C10g | **Retraining cadence.** Quarterly, as evaluated (D1, C6): the scheduler checks daily and trains when no production model is valid for the latest complete origin; `mandicast retrain` trains on demand. Every training goes through G0 and the gate. (The Phase 1 roadmap said "weekly retraining"; weekly retraining was never evaluated, so it is not used) | Serve only the evaluated procedure |
| C10h | **Rollback.** `mandicast rollback` restores the version the current production model replaced (if it is valid for the current origin; otherwise the crop is served by last value until a valid model is promoted). Every registration, promotion, rejection and rollback is recorded in `model_events` and mirrored to the MLflow model registry (alias `production`) | Recoverability and an audit trail |

**Correction found while implementing C10b (2026-11-27 about 07:38 IST).** C6 (and a comment in `gbm.py`) describe the
early-stopping set as "examples whose origin lies in the 15–105 days before the cutoff". The evaluated code
(`gbm.train`) uses every example with origin in the 105 days before the cutoff and target before the cutoff, so
origins in the last two weeks contribute their shorter horizons. No data on or after the cutoff is used either way,
so the evaluation remains free of look-ahead; the description was wrong, not the procedure. The frozen code is left
unchanged; C10b uses the set the code uses. Recorded in docs/10 §7 and DEV_LOG.

**Amendment to C10c (recorded 2026-11-27 07:52 IST, after the development-period simulation, before any publication
used the rule).** `scripts/backtest_fallback.py` simulated C10c on the stored walk-forward predictions. On the
**development** origins it triggered in six crop × day-range cases and lowered WAPE in one (Onion days 1–7:
7.16% vs 7.23% for the model alone) and raised it in five (Onion days 8–14: 11.05% vs 10.93%; Tomato days 1–7:
12.98% vs 12.94%; Tomato days 8–14: 20.65% vs 20.38%; Wheat and Soyabean days 8–14 by ≤ 0.02 points); it never
triggered for Potato. A trailing 8-week comparison does not anticipate which weeks the model will lose. **Automatic
switching is therefore not applied:** the model is served whenever a valid production model exists, last value is
served only when none exists (none trained, rejected, rolled back), and the 8-week comparison is still computed at
each publication, stored in `forecast_runs.decisions` and shown as a warning when the model lost. (On the hold-out,
already known in aggregate, the rule was mixed: it helped Onion and Tomato days 1–7 slightly and hurt Tomato days
8–14, 19.73% vs 19.20%.) Tables: `reports/phase5/PHASE5_TABLES.md`.

**Amendment to C10b (iii) (recorded 2026-11-27 08:01 IST, after the gate backtest, before any live gate decision).**
In the backtest (`reports/phase5/PHASE5_TABLES.md`), 24 of 25 real quarterly candidates passed; the Soyabean
2026-10-01 candidate failed only (iii): 5.25% WAPE against 5.14% for the previous quarter's model (limit 5.24%).
Under C9b the previous quarter's model cannot serve the new quarter, so a rejection would make the crop serve last
value, whose WAPE on the same points was 5.61%, worse than the rejected candidate. Rule (iii) therefore blocks
promotion only when the production model can still serve the candidate's quarter (an on-demand retrain within the
same quarter); otherwise its result is recorded and shown but does not block, and (i) and (ii) decide. Every
deliberately worse candidate in the backtest also failed (ii), so the "worse model is not promoted" result is
unchanged. The live registry has no earlier production model for any crop, so this amendment changes no current
decision.

