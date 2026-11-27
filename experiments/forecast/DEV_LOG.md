# Phase 3 development log

Events during the development-period runs, in order. Nothing here involves the hold-out period, which had not
been run when these entries were written.

| Time (IST) | Event |
|---|---|
| 2026-11-26 18:52 | Baselines scored on all development origins (5 crops) |
| 18:52–20:07 | ETS (quarterly estimation + forward) for all crops: all pairs forecast, 0 failed fits |
| 20:07–20:54 | LightGBM development run (first). The hand-over that stopped the first chain before ARIMA killed the parent process but not its 11 pool workers, which kept running beside LightGBM (slower, results unaffected) |
| 21:00 | **Bug found while reviewing the LightGBM development results, before any freezing:** the `state` feature was encoded with a per-frame categorical cast, so the same state got different integer codes in training, early-stopping and prediction frames (Polars 2.2 codes follow the order of appearance). Not look-ahead, but a garbled feature that did not match the pre-registered definition (C6). Fixed: stable codes from the sorted state list (`gbm.origin_features`), test `test_state_codes_are_stable_across_frames`. The first LightGBM development predictions are discarded and the run is repeated. ARIMA, which had just started, was stopped (with its 22 orphaned workers) and is run after LightGBM |
| 20:56–21:30 | LightGBM development run repeated with the state-code fix (alone on the CPU). Effect on development metrics was small (mean quarterly WAPE within ±0.0005 of the first run for every commodity and horizon; e.g. Tomato h7 0.1641 → 0.1636), consistent with the state feature having low importance; the corrected predictions replace the first ones |
| 21:30 | ARIMA development run started (11 workers) |
| 21:57 | Full ARIMA run stopped after 100 of 974 Onion series (18 min): ~12 h for development + ~5 h for the hold-out. **Deviation D3 recorded before any ARIMA prediction was scored**: ARIMA on a fixed random sample of 60 series per commodity (seed 20261126), compared with all models on those points; selection among models covering all series. No partial ARIMA predictions were saved |
| 21:58 | ARIMA (sample) development run started |
| 21:58–22:27 | ARIMA (sample, 60 series per commodity): all pairs forecast, 0 failed fits, 29 min. On the sample points it is never the best model (LightGBM is, for every commodity) |
| 22:27 | **Choices frozen** (`frozen_choices.json`, code SHA-256 `8ec35fd0855b…`): LightGBM serves days 1–14 for all five commodities; intervals volatility-conditional for all (unconditional 80% coverage by volatility tercile ranged 66–96%, beyond the 5-point rule). Development coverage with the frozen method: 78–84% (80% intervals), 87–92% (90% intervals). The hold-out had not been run when this was written |
| 22:28–23:38 | **Hold-out run once** (all models; ARIMA on its sample), then `final.py`: code SHA-256 at evaluation equal to the frozen one |
| 23:50 | After evaluation, documentation-only correction in `walkforward.py` (docstring said the last development origin was 2025-11-16; it is 2025-11-10, 150 origins). No logic changed; this changes the code hash after evaluation |

## Phase 4 (serving), 2026-11-27

| Time (IST) | Event |
|---|---|
| 06:36–06:40 | Daily ingestion (job 11): latest complete date 2026-11-25, so the serving origin is Monday 2026-11-23 |
| 06:44 | First `train-model` run stopped by the code guard: the forecasting code hash (`7ac578a8c097…`) differs from the frozen one (`8ec35fd0855b…`). Checked that undoing the 23:50 docstring edit reproduces the frozen hash exactly; recorded that edit in `post_freeze_changes.json`, where the guard re-verifies it on every use |
| 06:45–06:50 | Serving models trained for 2026Q4 (cutoff 2026-10-01). Each reproduces the hold-out 2026Q4 fit exactly (training rows, validation rows, best iteration; Tomato's best iteration is 2, as in the hold-out) |
| 06:50–06:52 | Forecasts published for Onion (494 mandis), Tomato (517), Potato (580), Wheat (324). Soyabean refused: smallest calibration group 97 < 100. Window-extension rule C9c added and recorded; Soyabean published (88 mandis, window 385 days) |
| 06:54 | Published forecasts at 2026-11-23 compared with the hold-out predictions for the same origin: same series for every crop; 99.8–100% of points within 0.01%; largest difference 0.6% (Onion), attributed to reports added by the 06:35 ingestion |

## Phase 5 (MLOps), 2026-11-27

| Time (IST) | Event |
|---|---|
| ~07:32 | Operations rules C10a–C10h (validation before training, promotion gate, fallback, last-value ranges, drift, missing updates, cadence, rollback) recorded in docs/06 §9b before any of them was computed |
| ~07:38 | While implementing the gate: the C6 description of the early-stopping set ("origins 15–105 days before the cutoff") does not match `gbm.train` (origins in the 105 days before the cutoff, targets before it). No look-ahead either way; disclosed in docs/06 §9b and docs/10 §7; C10b wording corrected to the code's set |
| — | (Times in this section follow the system clock; the first two entries were first written as 07:35 and 07:50 and corrected to the clock, both before the gate backtest started at 07:41.) |
| 07:41–08:01 | Gate backtest on real data (`scripts/backtest_gate.py`): 24 of 25 real quarterly candidates passed (Soyabean 2026-10-01 failed only (iii), 5.25% vs limit 5.24%); all 15 deliberately worse candidates rejected |
| 07:51 | Fallback simulation (`scripts/backtest_fallback.py`); model-only hold-out WAPE by day range reproduces docs/10 (`final.json`) exactly |
| 07:52 | **C10c amended** after the development simulation (helped in 1 of 6 triggered cases, hurt in 5): no automatic switching; last value only when no valid model; the comparison is published as a warning (docs/06 §9b) |
| 08:01 | **C10b (iii) amended** after the backtest: blocks only when the production model can still serve the candidate's quarter (otherwise a rejection would serve last value, worse than the candidate: Soyabean 5.61% vs 5.25%). No live decision affected |
| 08:02–08:07 | Live re-evaluation (`retrain --cutoff 2026-10-01`, job 19): artifacts reproduced exactly; all five production models passed validation and the gate; registered in the MLflow registry |
| 08:08 | `scripts/verify_phase5.py` found the MLflow alias `production` unset for re-confirmed models (only `promote` set it); fixed (`sync_mlflow_alias`), aliases set, verification passes |
| 08:09 | Publication preview for 2026-11-23 (`--dry-run`, nothing written): model served everywhere; Onion days 1–7 lost to last value over the previous 8 weeks (informational); drift warnings Onion, Wheat, Soyabean |
| 08:10 | First live monitoring snapshot (job 20); no missing-update warnings |
| 08:11–08:12 | Rollback drill on Soyabean (job 21): no earlier version → last value would be served; `ensure-models` (job 22) did not reinstate it; manual `retrain` passed the gate and restored it |

