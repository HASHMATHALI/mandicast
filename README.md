# MandiCast

Agricultural market price intelligence for Indian mandis: historical prices, mandi comparison, and carefully
evaluated 7- and 14-day price forecasts with honest uncertainty. Forecasts are estimates, never guarantees.

**Status (2026-11-27):** Phases 1–8 of 9 built (Phase 6 awaits a real-bot run; Phase 8's CI awaits a GitHub repository). Official AGMARKNET prices for five crops are ingested into
PostgreSQL (Phase 2); 7/14-day forecasts were evaluated walk-forward with a one-time hold-out year (Phase 3,
`docs/10_model_evaluation_report.md`: the frozen LightGBM beats the last-price forecast in 9 of 10 crop × horizon
cases; direction accuracy is 61–77%; prediction intervals under-cover). Weekly forecasts for 2,003 mandis are served
by a FastAPI service with their measured accuracy (Phase 4, `docs/11_backend_api.md`). Models are retrained
quarterly through a promotion gate (a deliberately worse model is rejected; tested on real data), can be rolled
back, and are monitored live with drift checks (Phase 5, `docs/12_mlops.md`). A Telegram bot gives prices, forecasts and
price alerts, with dashboard sign-in links (Phase 6, `docs/13_telegram_alerts.md`; the real-bot run needs a token).
A mobile-first React dashboard shows prices, forecasts with their ranges and measured accuracy, comparisons, data
freshness and alerts (Phase 7, `docs/14_dashboard.md`, `dashboard/`). Every service runs in Docker Compose behind
one web server, with a least-privilege database role for the public services, failure-mode tests, a CI workflow and
tested backups (Phase 8, `docs/15_infrastructure.md`). Final documentation (Phase 9) is next.

## Run everything with Docker Compose

```powershell
copy .env.example .env     # fill in POSTGRES_PASSWORD, REDIS_PASSWORD, API_DB_PASSWORD (and the API key / bot token)
docker compose up -d --build
```

Open http://localhost:8090. The database needs data first (`backfill`, `retrain`, `publish-forecasts` below, run
from the virtual environment or as `docker compose run --rm worker <command>`). Operations, backups and the CI
workflow: `docs/15_infrastructure.md`.

## Data

Prices come from AGMARKNET via the Open Government Data Platform India (data.gov.in), published by the
Directorate of Marketing and Inspection, Ministry of Agriculture and Farmers Welfare, under the
Government Open Data License – India (https://www.data.gov.in/Godl). Details: `docs/02_data_source_assessment.md`.
Prices are in ₹ per quintal, as reported by the APMCs; the source does not guarantee their accuracy.

## Quick start (Windows PowerShell or any shell)

Requirements: Python 3.12+ (developed on 3.14.5), Docker Desktop, a free data.gov.in API key.

```powershell
copy .env.example .env        # then fill in DATA_GOV_IN_API_KEY and POSTGRES_PASSWORD / DATABASE_URL
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
docker compose up -d db
.venv\Scripts\python -m mandicast db-upgrade
.venv\Scripts\python -m mandicast backfill --commodity Soyabean   # downloads ~1.3M rows (about 20 min)
.venv\Scripts\python -m mandicast status
.venv\Scripts\python -m pytest                                    # needs the db container running
```

`daily` keeps the data current (re-fetches the last week of arrival dates); `worker` runs it on a schedule.
All commands: `docs/09_data_pipeline.md`.

Forecasts and the API (Phase 4; needs `REDIS_PASSWORD` / `REDIS_URL` in `.env`):

```powershell
docker compose up -d db redis
.venv\Scripts\python -m mandicast db-upgrade
.venv\Scripts\python -m mandicast retrain              # this quarter's models, through the promotion gate (~5 min)
.venv\Scripts\python -m mandicast publish-forecasts    # latest complete Monday; safe to re-run (--dry-run to preview)
.venv\Scripts\python -m mandicast monitor              # live accuracy and missing-update checks
.venv\Scripts\python -m mandicast api                  # http://127.0.0.1:8000/docs
```

`worker` runs ingestion, model checks, publication and monitoring on a schedule; `rollback --commodity X --reason "…"`
restores the previous model (docs/12). With `TELEGRAM_BOT_TOKEN` set, `bot` runs the Telegram bot and
`alerts` sends due alerts (docs/13).

Dashboard (Phase 7; Node 22): with the API running, `cd dashboard`, `npm ci`, `npm run dev`, then open
http://localhost:5173. Tests: `npm test`, `npm run test:e2e` (details in `dashboard/README.md`).

## Layout

| Path | Contents |
|---|---|
| `src/mandicast/` | Package: `ingest/` (API client, raw store, fetch, load), `clean/` (names, flags, cleaned layer), `forecast/` (Phase 3 evaluation), `serving/` (training, registry, publication, MLOps), `bot/` (Telegram, alerts), `api/` (FastAPI), `db/`, `jobs.py`, `pipeline.py`, `scheduler.py`, `cli.py` |
| `migrations/` | Alembic migrations |
| `tests/` | Pytest suite: 116 tests (unit + PostgreSQL and Redis integration; Telegram mocked; failure modes; database roles; dashboard schema contract) |
| `dashboard/` | React + TypeScript dashboard (Vite): 29 unit/component tests, 7 Playwright end-to-end tests |
| `docker/`, `docker-compose.yml` | Images (Python services; dashboard on nginx) and the Compose project |
| `.github/workflows/ci.yml` | CI: backend tests on PostgreSQL + Redis, dashboard checks, image builds and a fresh deployment |
| `scripts/` | Research and verification scripts (download, profiling, eligibility, `verify_phase2.py`, `verify_phase4.py`, `verify_phase5.py`, `benchmark_api.py`, gate and fallback backtests, `e2e_login.py` test sign-in links, `backup.sh`, `restore_check.sh`) |
| `docs/` | Requirements, data sources, architecture, schema, data dictionary, methodology, API, roadmap, pipeline, evaluation report (10), backend and API (11), MLOps (12), Telegram alerts (13), dashboard (14), infrastructure and operations (15) |
| `requirements-linux.lock.txt` | Exact, hash-checked dependency versions for the containers and CI (`requirements.lock.txt`: the Windows development freeze) |
| `reports/` | Profile summary (Phase 1) and per-job quality reports (`reports/quality/`) |
| `config/manual_aliases.csv` | Reviewed name mappings (empty until reviewed) |

## Configuration and secrets

All secrets live in `.env` (git-ignored): the data.gov.in key, the Telegram bot token, and the local database, API
database role and Redis passwords. They are never logged (log lines pass through a redaction filter), never written to
tracked files, never copied into images (`.dockerignore`), and each container receives only the ones it needs.
