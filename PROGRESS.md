# MandiCast — PROGRESS

This file lets work resume without losing context. Update it at the end of every phase.

## Current state: Phases 1–9 done (2026-11-27). CI green on GitHub. Open: the real-bot run (Phase 6, needs the owner's bot token) and publication from a fresh clone (docs/19 §4)

Repository: https://github.com/HASHMATHALI/mandicast (public, branch `main`). Commits so far: `ffc5c01` (Phases 1–8),
`c61c4f4` (line-ending fix found by CI), then Phase 9. Commit and push only when the owner asks.

Run state: the whole stack runs in Docker Compose (`docker compose up -d`: db, redis, migrate, api, worker,
dashboard at http://localhost:8090); PostgreSQL and Redis are still published on 127.0.0.1:5433 / 6380 for the venv.
Production models for 2026Q4 in `models/` (gate passed, MLflow alias `production`); forecasts published for origin
2026-11-23. `.env` has the data.gov.in key and `API_DB_PASSWORD` (generated 2026-11-27); `TELEGRAM_BOT_TOKEN` is
empty. Role `mandicast_api` exists. Migrations at `0005`. Backup `backups/mandicast-20261127T143344Z.dump` (1.1 GB).
No users or alerts in the database (test users are deleted).

## Phase 9: Final documentation ✅ (2026-11-27)

**Index:** `docs/README.md` maps every roadmap deliverable to its document.

**Written / rewritten**
* `README.md` (results table, screenshots, run, tests, layout), `docs/03_architecture.md` (as built, v1.0),
  `docs/16_mlflow_guide.md`, `docs/17_testing_report.md`, `docs/18_deployment_guide.md`, `docs/19_known_limitations.md`,
  `docs/20_interview_guide.md`, `docs/README.md`; roadmap status in docs/08; CI results in docs/15.
* `docs/05` §5: every column of the 20 tables, generated from the database (`python -m mandicast.db.schema_doc`);
  docs/04 diagram redrawn from the database's foreign keys.
* Screenshots `docs/images/` from the running stack (`cd dashboard; npm run screenshots`).
* Resolved items marked in the older phase reports' limitation lists (docs/11–14).

**Verified (executed 2026-11-27)**
| Check | Result |
|---|---|
| `tests/test_docs.py` (5 tests: generated schema reference = migrated database; docs/07 = API operations; CLI commands, paths, links, cited test names exist) | pass; on first run it found a renamed test cited in docs/09 and a non-existent `name_aliases.entity_id` in docs/04 (fixed) |
| `pytest` / `ruff` | **121 passed** (34.5 s) / clean |
| Every number in the new documents | taken from reports, the database, MLflow or a command run that day (sources named in each document) |

**Found while documenting:** a deployment from a fresh clone cannot publish forecasts: range calibration starts from
walk-forward predictions (`experiments/forecast/**/*.parquet`, 77 MB, git-ignored, keyed by this database's series
ids). Documented in docs/18 §2 and docs/19 §4; fix not built.

## Phase 8: Testing, infrastructure, deployment ✅ (2026-11-27; CI green on GitHub)

**Report:** `docs/15_infrastructure.md` (deployment, failure-mode matrix, database role, CI, verification, backups,
operations, limitations).

**Implemented**
* Docker: `docker/app/Dockerfile` (Python services; digest-pinned base, hash-checked `requirements-linux.lock.txt`,
  uid 10001; `test` target), `docker/dashboard/Dockerfile` (build + nginx-unprivileged; security headers incl.
  `frame-ancestors`; `/v1` proxy, per-address limit), `.dockerignore`.
* `docker-compose.yml`: db, redis, migrate (db-upgrade + db-roles), api (2 workers, API role, not published), worker,
  bot (profile `bot`), dashboard (127.0.0.1:8090), mlflow (profile `tools`); read-only file systems, dropped
  capabilities, no-new-privileges, health checks, log rotation, only needed variables per container.
* `mandicast db-roles` (`db/roles.py`): least-privilege role for the API and the bot (read all, write 5 account tables).
* `DB_CONNECT_TIMEOUT_S` (default 5 s) on every engine: an unreachable database used to hang requests 130 s per attempt.
* 6 tests added: database down, Redis down, training crash in the scheduled job, Telegram 5xx retried then given up,
  two role tests. Failure-mode matrix in docs/15 §2.
* `.github/workflows/ci.yml`: backend (PostgreSQL + Redis services, ruff, pytest, pip-audit), dashboard (typecheck,
  lint, tests, build, npm audit), images (build + fresh deployment smoke test). Actions pinned to SHAs.
* `scripts/backup.sh` (pg_dump in the container, SHA-256, integrity check, keeps 7) and `scripts/restore_check.sh`.
* Dashboard: `VITE_API_BASE=same-origin` for the proxied deployment; e2e can target a deployment (`E2E_BASE_URL`).

**Verified (executed 2026-11-27)** — details in docs/15 §5–6
| Check | Result |
|---|---|
| `pytest` Windows / Linux test image from a clone-equivalent file set | **116 passed** / **116 passed**; ruff clean |
| Dashboard CI commands | 29 passed, build, 0 npm vulnerabilities |
| `pip-audit` (hash-locked Linux requirements) | no known vulnerabilities; wrong hash refused |
| Fresh deployment (separate project, empty volume) | migrations 0001→0005, role, API and dashboard healthy |
| Full stack on the real DB + Playwright against it | all healthy; **7 passed** |
| Backup + restore check | 1.1 GB dump in 163 s; restored in 310 s; 21 tables, 29,717,867 rows, identical counts (first check run passed on 0 tables: script bug, fixed and rerun) |
| GitHub Actions | first run 46032340358 **failed** (frozen-code hash differs on a Linux checkout: line endings; fixed with `.gitattributes`, commit `c61c4f4`); run **46034190212: all 3 jobs passed** |

## Phase 7: Dashboard ✅ (2026-11-27)

**Report:** `docs/14_dashboard.md`; how to run: `dashboard/README.md`.

**Implemented** (`dashboard/`, React 19 + TypeScript 5.9 strict, Vite 8, React Router, TanStack Query; charts as own SVG)
* Pages: prices (crop, state, mandi search, latest prices with date, freshness), mandi (latest price in ₹/quintal and
  ₹/kg, trend sentences, chart of history + forecast + 80%/90% ranges with gaps left empty, keyboard readout and table
  view, hold-out accuracy vs the last price, live accuracy, warnings, disclaimer, "not enough data" with the failed
  rules, names as reported), compare (up to 4 mandis on common dates), about the data (freshness, models and gate,
  hold-out and live accuracy), alerts (sign-in with the bot's one-time link, create/delete alerts, messages, sign
  out, delete my data), `/login#code=`.
* Security: CSP meta tag in production builds, no raw HTML (lint rule), code removed from the address before use,
  token only to the API origin, `credentials: omit`, no third-party code; dependencies resolved with
  `--before=2026-11-20`, exact versions, `npm audit` clean.
* Types generated from the API's OpenAPI document (`npm run gen:api`); `tests/test_dashboard_contract.py` fails when
  the API schema and the dashboard's copy differ.
* `scripts/e2e_login.py`: one-time sign-in link for a test user (negative chat id) without Telegram, and its deletion.

**Verified (executed 2026-11-27)**
| Check | Result |
|---|---|
| `npm test` | **29 passed** (component tests on real API responses recorded 2026-11-27) |
| `npm run test:e2e` (Edge, production build, real API and DB) | **7 passed** (5 at 375 px with touch, 2 desktop); fails on CSP violations, page errors or horizontal overflow |
| `pytest` / `ruff` | **110 passed** / clean |
| typecheck, ESLint, `npm audit`, build | clean; 0 vulnerabilities; 111 kB gzip JavaScript |
| Browser check (375 px, dark + light) | real data on every page; alert created in the dashboard was queued by `mandicast alerts` (job 24) and shown as "waiting to be sent" |

**Defects found and fixed:** forecast unreadable at a 3-month default on a phone (default now 1 month); overlapping end
labels; month ticks "Nov 26" read as a date; trend wording; unsent messages listed as "sent"; "not enough data" page
541 px wide on a 375 px screen (found by the end-to-end overflow check).

## Phase 6: Telegram bot and price alerts ✅ built and tested; ⏳ real-bot run (2026-11-27)

**Report:** `docs/13_telegram_alerts.md`; API `docs/07_api_specification.md` §6; schema docs/04 (Alembic `0005`).

**Implemented**
* Alembic `0005`: `users` (chat id only), `login_codes`, `user_sessions` (hashes only), `alert_subscriptions`,
  `notification_history` (idempotency keys); cascading deletion.
* `bot/telegram.py` (httpx Bot API client: retries, 429 handling, token never in errors or logs), `bot/handlers.py`
  (commands, buttons, private chats only, per-chat rate limit), `bot/service.py` (users, search, subscriptions,
  one-time login codes, sessions, retention purge), `bot/alerts.py` (price alerts on crossing, forecast alerts per
  publication, idempotent delivery, retries, pause on block), `bot/runner.py` (long polling).
* API: `POST /v1/auth/session`, `POST /v1/auth/logout`, `GET/POST /v1/alerts`, `DELETE /v1/alerts/{id}`,
  `GET /v1/alerts/history`, `DELETE /v1/me`; Bearer auth; separate writable engine; never cached.
* CLI `bot`, `alerts`, `privacy-purge`; scheduler `alerts` 02:45/14:45 and `privacy_purge` 04:00 UTC; logging
  redacts bot tokens and keeps httpx URL logs at WARNING.

**Verified (executed 2026-11-27)**
| Check | Result |
|---|---|
| `pytest` | **109 passed** (95 earlier + 14 Phase 6, Telegram mocked); ruff clean |
| Cache isolation with real Redis | two users' `/v1/alerts` never served from the shared cache; public data still cached |
| Simulated chat on the real database | `/price`, `/forecast`, `/alert` produce correct messages on real data; temporary user deleted |
| Real Telegram end-to-end | **pending: owner to add `TELEGRAM_BOT_TOKEN` to `.env`** |

## Next (exact steps)
1. **Owner:** create a bot with @BotFather and put the token in `.env` (`TELEGRAM_BOT_TOKEN=`) with a text editor.
   Then `docker compose --profile bot up -d bot worker`, send `/start`, `/forecast onion lasalgaon`, an
   `/alert … above 1000`, `/login` (open the link: http://localhost:8090), and run `docker compose run --rm worker alerts`;
   record the outcome here (Phase 6 exit criterion).
2. Fresh-clone publication (docs/19 §4): a command that regenerates the calibration predictions for the current
   database without touching the tracked evaluation files, tested on a freshly loaded database.
3. Optional: VeriTrace resumes at its Phase 2 write-up (`../veritrace/PROGRESS.md`).

## Phase 5: MLOps — retraining, gate, rollback, monitoring ✅ (2026-11-27)

**Report:** `docs/12_mlops.md`; rules `docs/06_forecasting_methodology.md` §9b (C10a–C10h, recorded before computing,
with two dated amendments); tables `reports/phase5/PHASE5_TABLES.md`; events `experiments/forecast/DEV_LOG.md`.

**Implemented**
* Alembic `0004`: `model_events` (audit trail), `monitoring_snapshots`; gate result, status reason and MLflow
  version on `model_versions`; `served_by` and `model_point` on `forecasts`; `decisions` and `drift` on
  `forecast_runs`; one publication per crop and week (model optional: last-value-only runs).
* `serving/gate.py`: validation before training (C10a) and the promotion gate (C10b: integrity; not worse than last
  value; not > 2% worse than the production model when that model could still serve).
* `serving/lifecycle.py`: `retrain` (train → validate → register → gate → promote / reject; a production model that
  fails re-evaluation is rolled back), `ensure_models` (quarterly, never reinstates a rejected or rolled-back model).
* `serving/registry.py`: promote / reject / rollback with events; MLflow tracking (experiment `mandicast-models`,
  artifacts under `experiments/mlruns`) and model registry mirror (`mandicast-lightgbm-<crop>`, alias `production`).
* `serving/publish.py`: last-value forecasts and ranges (C10d) always computed; served when no valid model; 8-week
  model vs last-value comparison stored and shown (C10c as amended); drift PSI (C10e); `--dry-run` preview.
* `serving/monitor.py`: live WAPE (model, last value, served), coverage, direction per crop and day range; missing-
  update checks (C10f).
* CLI `retrain` (alias `train-model`), `rollback`, `ensure-models`, `monitor`, `publish-forecasts --dry-run`;
  scheduler adds `ensure_models` 02:00 and `monitor` 03:00/15:00 UTC.
* API: `/v1/monitoring`, `/v1/models/history`; forecasts gain `served_by`, `fallback`, `live_accuracy`, `drift`.

**Verified (executed 2026-11-27)**
| Check | Result |
|---|---|
| `pytest` | **95 passed** (77 earlier + 18 Phase 5); ruff clean |
| Gate backtest, real data (`scripts/backtest_gate.py`) | 24 of 25 quarterly candidates passed as first recorded (Soyabean 2026Q4 failed only (iii); passes under the amendment); **all 15 deliberately worse candidates rejected** |
| Fallback simulation (`scripts/backtest_fallback.py`) | rule helped in 1 of 6 triggered dev cases, hurt in 5 → automatic switching dropped (dated amendment); model-only numbers reproduce docs/10 exactly |
| Live re-evaluation (`retrain --cutoff 2026-10-01`) | artifacts reproduced exactly; all five models passed and stay in production |
| `scripts/verify_phase5.py` | artifacts, gate, MLflow alias and events consistent for all crops |
| Rollback drill (Soyabean) | rolled back → last value; scheduler did not reinstate; manual retrain restored it through the gate |

**Findings to remember:** Onion days 1–7 lost to last value over the 8 weeks before 2026-11-23 (5.18% vs 5.09%,
shown as a warning); drift warnings for Onion, Wheat and Soyabean at that origin; live monitoring has one week of
data so far (not yet reliable).

## Phase 4: Backend, serving and API ✅ (2026-11-27)

**Report:** `docs/11_backend_api.md`; API reference `docs/07_api_specification.md` (and `/docs` on the running API);
serving rules `docs/06_forecasting_methodology.md` §9a (C9); events `experiments/forecast/DEV_LOG.md` (Phase 4).

**Implemented**
* Alembic `0003`: `model_versions` (artifact SHA-256, code hash, validity quarter, hold-out evaluation, one production
  model per crop), `forecast_runs` (one per model and origin), `forecasts` (80% / 90% ranges; never overwritten).
* `serving/registry.py` + `mandicast train-model`: frozen LightGBM retrained for the origin's quarter, saved under
  `models/` (git-ignored) with SHA-256, MLflow (`mandicast-serving`), registered and promoted. Guard: refuses if
  `src/mandicast/forecast` changed after freezing, except the documented docstring edit (allow-list
  `experiments/forecast/post_freeze_changes.json`, re-verified on every use by reproducing the frozen hash).
* `serving/publish.py` + `mandicast publish-forecasts`: latest complete Monday, eligible series at that origin,
  conformal ranges from residuals whose targets precede the origin (removes the docs/10 §8 overlap), 364-day window
  extended by weeks to ≥ 100 residuals per group (rule added when Soyabean had 97). Idempotent.
* `api/`: FastAPI, 15 endpoints (discovery, prices, latest, compare, trends, forecasts with measured accuracy and
  warnings, eligibility, models, evaluation, freshness, health); read-only DB sessions with statement timeout;
  strict parameters; one error format; Redis cache invalidated by new data; per-client rate limit in Redis
  (HMAC-hashed addresses) with in-process fallback; security headers; JSON access log without addresses or query
  values. `mandicast api` runs it (uvicorn).
* Redis 8 in Docker Compose (digest-pinned, password in `.env`, localhost only, no persistence).
* Scheduler: publication at 02:30 and 14:30 UTC after each ingestion.

**Verified (executed 2026-11-27)**
| Check | Result |
|---|---|
| `pytest` | **77 passed** (47 earlier + 21 API + 9 serving), including Redis cache invalidation and a rate limit shared by two app instances |
| `ruff check src tests scripts migrations` | all checks passed |
| `scripts/verify_phase4.py` | artifacts match; each 2026Q4 serving model reproduces the hold-out 2026Q4 fit (rows, best iteration) |
| First publication (origin 2026-11-23) | 2,003 mandis (Onion 494, Tomato 517, Potato 580, Wheat 324, Soyabean 88), 28,042 points; same series as the hold-out predictions for that origin, 99.8–100% of points within 0.01% (max 0.6%) |
| Re-publication | all crops `already_published`, 0 rows written (job 18) |
| Latency (`scripts/benchmark_api.py`, one worker, laptop) | cache miss p50 10–22 ms, p95 ≤ 29 ms; cache hit p50 ≈ 3 ms |

**Known gaps (by design, later phases):** quarterly retraining is manual until Phase 5 (publication fails from
2027-01-01 until the 2027Q1 model is trained); no live accuracy/coverage monitoring or fallback yet; no alerts or
authentication (Phase 6); API runs from the venv, no containers/CI (Phase 8); dedicated read-only DB role and
`pip-audit` planned for Phase 8.

## Phase 3: Forecasting engine ✅ (2026-11-26)

**Report:** `docs/10_model_evaluation_report.md` (tables generated from `experiments/forecast/`; full tables in
`experiments/forecast/EVALUATION_TABLES.md`; development log `experiments/forecast/DEV_LOG.md`; MLflow
`experiments/mlflow.db`).

**Implemented** (`src/mandicast/forecast/`): per-origin eligibility from data up to the origin (matches the
pipeline's SQL exactly); walk-forward design (150 development Mondays 2023-01-02 … 2025-11-10; 52 hold-out Mondays
2025-12-01 … 2026-11-23); baselines; AutoETS / AutoARIMA (quarterly estimation + forward, parallel workers);
global LightGBM per crop (quarterly, early stopping); split-conformal intervals (volatility-conditional option);
common-point scoring and selection; `freeze.py` (frozen choices + code hash); `final.py` (single hold-out
evaluation); `report.py` (generated tables); `run.py` guards (hold-out needs `--confirm-holdout` and the frozen file).

**Verified (executed 2026-11-26)**
| Check | Result |
|---|---|
| `pytest` | **47 passed** (33 pipeline + 14 forecasting: no look-ahead, baselines, metrics, intervals, guards, stable state codes) |
| `ruff check src tests scripts migrations` | all checks passed |
| Development runs | all pairs forecast by every model; 0 failed fits (ETS on all series, ARIMA on its sample) |
| Freeze → hold-out | frozen 22:27 IST; hold-out run once; code hash at evaluation equal to the frozen one |

**Results (hold-out year, frozen LightGBM for all crops)**
* Beats last value in 9 of 10 crop × horizon cases (MASE 0.885–0.993); slightly worse for Tomato at 7 days (1.008).
* 1 − WAPE: Onion 92.3% / 89.7%, Potato 92.9% / 91.2%, Wheat 97.7% / 97.2%, Soyabean 95.0% / 93.0%,
  Tomato 84.1% / 78.9% (7 / 14 days).
* Direction of moves ≥ 2%: 61–77% (the ≥ 90% aspiration is not met).
* Interval coverage below nominal: 80% intervals 74.0–78.5% (four crops), Soyabean 55.7–61.5% after a volatility
  regime change; 90% intervals 86.1–88.5% (four crops), Soyabean 69.0–75.1%.

**Deviations / issues:** D1 quarterly ETS/ARIMA estimation; D2 calendar features; D3 ARIMA on a fixed sample of
60 series per crop; C5 outlier targets excluded from headline metrics (both reported); LightGBM state-code bug found
and fixed before freezing; development-interval calibration overlapped by up to 14 days (hold-out unaffected;
fix in Phase 5).

## Phase 2: Data pipeline ✅ (2026-11-26)

**Implemented** (`src/mandicast/`, see `docs/09_data_pipeline.md`)
* PostgreSQL 16.16 in Docker Compose (localhost only, digest-pinned, 1 GB shm); credentials in `.env`.
* SQLAlchemy models + Alembic migrations `0001` (ingestion, source, dimensions, cleaned observations) and `0002`
  (display names, alias last_seen).
* `ingest/`: API client (retries/backoff, User-Agent, no key in errors), raw store (gzip + SHA-256 manifests,
  order-independent content hashes, versioned date snapshots), fetch (backfill pages, boundary repair,
  recent-date refresh, empty-date handling), load (idempotent row-hash inserts, snapshot supersession with an
  outage guard).
* `clean/`: canonical names (Keralam/Uttrakhand, " APMC" rename, manual file), flags, primary row per mandi-day
  without look-ahead, trailing outlier flag, series stats, eligibility per the methodology.
* `jobs.py` (job records, advisory lock, redacted errors, JSON quality reports), `pipeline.py`, `scheduler.py`
  (APScheduler, 01:30 and 13:30 UTC), `cli.py` (`db-upgrade`, `load-raw`, `backfill`, `daily`, `clean`,
  `status`, `worker`), JSON logging with secret redaction.

**Verified (commands executed 2026-11-26)**
| Check | Result |
|---|---|
| `pytest` (unit + PostgreSQL integration) | **33 passed**; snapshot tests 10/10 repeated runs |
| `ruff check src tests scripts migrations` | all checks passed |
| `scripts/verify_phase2.py` (DB vs raw files) | all checks OK for all five commodities |
| Source rows | 14,828,006 = API total 14,828,035 minus 29 rows the source itself repeats |
| Cleaned rows / primary mandi-days | 14,828,006 / 13,978,435; 0 orphans, 0 duplicate primary days |
| Series / eligible series | 11,310 / **2,003** (Onion 494, Tomato 519, Potato 578, Wheat 324, Soyabean 88) |
| Live daily runs (real API) | 4 runs; 23,629 rows re-fetched each; last two stored and changed nothing |
| Locking | concurrent `daily` during a load recorded as `skipped`, exit code 3 |
| Timing | full load 43 min, full rebuild 24 min, daily 2.5–3 min |

**Problems found on real data and fixed** (each now has a test): empty-body responses for dates without
records; row order varying between identical exact-date requests; snapshot names colliding (now per-date
versions); correction snapshots compared with the wrong baseline; window-dependent name labels; Docker's 64 MB
`/dev/shm`; day-level conflicts missing from eligibility. See `docs/09_data_pipeline.md` §4a.

## Phase 1: Research and architecture ✅ (2026-11-26)
Docs `docs/01`–`08`; 14,828,035 official rows for Onion, Tomato, Potato, Wheat, Soyabean downloaded with
SHA-256 manifests (`data/raw/`, git-ignored) and profiled (`reports/profile/PROFILE_SUMMARY.md`). Key findings:
live-index pagination needs boundary-date re-fetch; mandis renamed with " APMC" on 1–4 Nov 2025; variety labels
renamed; zero min/max = not reported. Details in `docs/02_data_source_assessment.md`.

## Known issues / open items
* Unlinked renamed mandis need reviewed entries in `config/manual_aliases.csv` (none added yet).
* Arrival quantities are not available in bulk from the official API (out of scope).
* Raw snapshot files from the first two live daily runs (same content, different row order) remain in
  `data/raw/`; harmless (identical rows, loaded once).
* The data.gov.in key was pasted into the chat once; the owner may regenerate it and update `.env`.
* From 2027-01-01 the scheduler's `ensure-models` retrains through the gate (or run `mandicast retrain`).
* A fresh clone cannot publish forecasts until calibration predictions exist for its database (docs/18 §2).
* The Phase 4 MLflow experiment `mandicast-serving` stored artifact copies under `./mlruns` (git-ignored); Phase 5
  uses `mandicast-models` with an absolute artifact location under `experiments/mlruns`.
* Laptop Wi-Fi dropped repeatedly on 2026-11-26; the client retries for up to ~40 minutes.

## Requirements recorded from the master instruction
* 9 phases: research/architecture → data pipeline → forecasting (7/14-day) → backend → MLOps/retraining →
  Telegram alerts → dashboard → testing/infra → documentation.
* ≥1M genuine price observations; keep source records, track cleaned values separately; idempotent ingestion;
  never invent missing prices.
* Forecasting: baselines first, then ETS / (S)ARIMA / gradient boosting; walk-forward validation; MAE, RMSE,
  MAPE/sMAPE/WAPE, directional accuracy, interval coverage and width; beat naive baselines; forecast only series
  with enough reliable data; ≥90% directional accuracy if achievable and ≥90% interval coverage at the stated
  nominal level, reported truthfully. Never present forecasts as guaranteed prices.
