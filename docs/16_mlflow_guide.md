# MandiCast — MLflow guide

Date: 2026-11-27. MLflow is used for **experiment tracking** (Phase 3 evaluation, Phase 4–5 training runs) and as a
**mirror of the model registry**. The source of truth for which model serves is the database (`model_versions`,
`model_events`, docs/12); MLflow failures are logged and never block training or serving
(`serving/registry.py: log_mlflow`, `mlflow_set_production`).

## 1. Where things are

| Item | Location | In git? |
|---|---|---|
| Tracking store | `experiments/mlflow.db` (SQLite; URI built in `forecast/freeze.py: MLFLOW_URI`) | No (git-ignored) |
| Artifacts of `mandicast-models` | `experiments/mlruns/` (absolute path fixed when the experiment was created) | No |
| Artifacts of `mandicast-forecast`, `mandicast-serving` | `./mlruns/` (relative; Phase 3–4) | No |
| Evaluation results the reports are generated from | `experiments/forecast/` (predictions summaries, JSON reports, `frozen_choices.json`, `DEV_LOG.md`) | **Yes** |
| Model files that serve | `models/` (SHA-256 checked on every load) | No |

A fresh clone therefore has **no MLflow history**: the evaluation evidence travels as the JSON and Markdown files in
`experiments/forecast/`, which the reports (docs/10) are generated from. MLflow fills up again as soon as `retrain`
runs.

## 2. Experiments (contents on 2026-11-27)

| Experiment | Runs | What a run is | Logged |
|---|---|---|---|
| `mandicast-forecast` (Phase 3) | 65 | 60 development runs: 6 methods (`last_value`, `seasonal_naive`, `mean_7d`, `mean_28d`, `ets`, `gbm`) × 5 crops × 2 horizons (`dev-<Crop>-<method>-h7/h14`); 5 hold-out runs of the frozen, served forecast (`holdout-<Crop>-served`) | tags `period`, `commodity`, `model`, `code_sha256`; params `h`, `points`; metrics MAE, RMSE, MAPE, sMAPE, WAPE, MASE vs last value, direction accuracy (all moves and moves ≥ 2%), share of moves ≥ 2%; hold-out runs also 80% / 90% coverage and relative width, per horizon (`h7_*`, `h14_*`) |
| `mandicast-serving` (Phase 4) | 5 | first training of each crop's 2026Q4 serving model | as below (superseded by `mandicast-models`) |
| `mandicast-models` (Phase 5 on) | 5 | one training of a serving model (`lightgbm-<crop>-<quarter>-<hash>`) | tags `commodity`, `version`, `artifact_sha256`, `forecast_code_sha256`; params: the LightGBM settings (`lgb_*`), `cutoff`, `early_stop`; metrics `best_iteration`, `train_rows`, `valid_rows`, `examples`, `seconds`; the LightGBM model |

AutoARIMA (fixed sample of 60 series per crop, methodology D3) is compared in docs/10 §4 but has no MLflow runs.

**Registered models:** `mandicast-lightgbm-onion`, `-potato`, `-soyabean`, `-tomato`, `-wheat`, each version 1 with the
alias `production` (checked by `scripts/verify_phase5.py`).

## 3. Opening the UI

From the development environment (shows everything, including artifacts):

```powershell
.venv\Scripts\mlflow ui --backend-store-uri sqlite:///experiments/mlflow.db --port 5000
```

With Docker Compose (profile `tools`, read-only use of the mounted `experiments/`):

```powershell
docker compose --profile tools up -d mlflow     # http://localhost:5000
docker compose --profile tools stop mlflow
```

The Compose UI lists every run, metric and registered model (verified in Phase 8). Artifacts logged from Windows keep
their `file:///C:/...` paths, so their files open only in the development UI; runs logged inside the containers
(`docker compose run --rm worker retrain`) use container paths and open in the Compose UI.

## 4. Common tasks

| Task | How |
|---|---|
| Compare methods for a crop | UI → `mandicast-forecast` → filter `tags.commodity = "Onion" and tags.period = "dev"` → compare `wape`, `mase_vs_last_value` |
| See the hold-out result | filter `tags.period = "holdout"`; the same numbers are in docs/10 §2 |
| Which code produced a result | tag `code_sha256` / `forecast_code_sha256`; must equal `experiments/forecast/frozen_choices.json` (or a documented edit in `post_freeze_changes.json`) |
| Which model is serving | `GET /v1/models/current` (database; the dashboard's "About the data" page shows it too); the MLflow alias `production` mirrors it |
| Re-point the alias after a manual change | it follows the database automatically on `retrain`, `rollback` and promotion (`sync_mlflow_alias`) |
| Turn tracking off (tests) | `MLFLOW_ENABLED=false` |

## 5. Limits

- SQLite tracking store and local artifacts: one machine, one writer at a time. A shared deployment would use an
  MLflow server with a database backend and object storage.
- The registry is a mirror. If it disagrees with `model_versions`, the database wins and `sync_mlflow_alias` repairs
  the alias.
