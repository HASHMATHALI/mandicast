# MandiCast — Deployment guide

Date: 2026-11-27. From a fresh clone to a running system with data, forecasts and the dashboard, on one machine with
Docker. Reference for every service, the hardening and operations: docs/15. The column "Verified" says how each step
was actually exercised; nothing here was deployed on a public server.

## 1. Requirements

| Need | Notes |
|---|---|
| Docker Desktop (Windows / macOS) or Docker Engine with Compose v2 (Linux), linux/amd64 | The Python image installs from `requirements-linux.lock.txt` (amd64 hashes) |
| ~15 GB free disk | PostgreSQL holds ~10 GB for five crops (14.8M source rows + cleaned rows + indexes); raw pages under `data/raw/` ~190 MB (gzip); images ~2.5 GB |
| A data.gov.in API key | Free; see `.env.example` for where to get it |
| Optional: a Telegram bot token | From @BotFather; without it alerts are queued but not sent |
| Optional for development: Python 3.12+ (developed on 3.14.5), Node 22 | Only to run outside containers |

## 2. Steps

| # | Step | Command | Verified |
|---|---|---|---|
| 1 | Get the code | `git clone https://github.com/HASHMATHALI/mandicast.git; cd mandicast` | clone + CI checkout (GitHub Actions run 46034190212) |
| 2 | Configure | `copy .env.example .env`, then fill in `DATA_GOV_IN_API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `API_DB_PASSWORD` (≥ 16 characters), optionally `TELEGRAM_BOT_TOKEN`. Generate passwords with `python -c "import secrets; print(secrets.token_urlsafe(24))"` | CI generates a throwaway `.env` the same way |
| 3 | Linux hosts only | `mkdir -p data models reports logs && sudo chown -R 10001 data models reports logs experiments` | CI images job |
| 4 | Start | `docker compose up -d --build` → db, redis, migrate (migrations + API role), api, worker, dashboard | fresh empty database: CI and locally (Phase 8) |
| 5 | Check | `docker compose ps` (all healthy; `migrate` exited 0), `curl http://localhost:8090/v1/health` | CI smoke test |
| 6 | Load the price history | `docker compose run --rm worker backfill --commodity Onion` (repeat per crop, or omit `--commodity` for all five). Resumable and idempotent: re-running loads nothing twice | the live database was loaded with the same command from the development venv (Phase 2); containerised commands run against it (`status`, checked) |
| 7 | Train the models | `docker compose run --rm worker retrain` (frozen method, current quarter, through the promotion gate; about 5 minutes) | venv on the development database (Phases 4–5); **not yet on a newly loaded database** |
| 8 | Publish forecasts | `docker compose run --rm worker publish-forecasts` (latest complete Monday; `--dry-run` to preview; safe to repeat). **On a newly loaded database this stops: see the note below** | development database: venv (jobs 17, 18) and the worker container's schedule (job 25) |

**Fresh databases cannot publish forecasts yet.** The ranges are calibrated on walk-forward predictions of the frozen
method and of the last price (methodology C9c) until enough published forecasts have matured. Those predictions are
`experiments/forecast/<period>/<model>/<crop>.parquet` files: 77 MB, git-ignored, and keyed by the development
database's series ids, which a newly loaded database does not reproduce. Without them `publish-forecasts` stops with
"fewer than 100 calibration residuals" and the API and dashboard serve history, comparisons and models without
forecasts. Regenerating them for a new database (the Phase 3 runner, `python -m mandicast.forecast.run`, could; it also
rewrites the tracked evaluation summaries next to them) has not been automated or tested. Listed in docs/19.
| 9 | Open the dashboard | http://localhost:8090 (phone on the same network: not exposed; see §4) | Playwright against the Compose stack: 7 passed |
| 10 | Telegram bot (optional) | put the token in `.env`, then `docker compose --profile bot up -d bot worker`; in Telegram send `/start` | without a token: exits with a clear message. **With a real token: not yet run** |
| 11 | MLflow UI (optional) | `docker compose --profile tools up -d mlflow` → http://localhost:5000 (docs/16) | Phase 8 |
| 12 | Backups | `scripts/backup.sh`; prove one restores: `scripts/restore_check.sh backups/<file>.dump` | 1.1 GB backup restored with identical counts (docs/15 §6) |

Download time depends on data.gov.in: Soyabean (1.3M rows) took about 20 minutes on 2026-11-26; the five crops are
14.8M rows. Loading and cleaning already-downloaded pages for all five took 43 minutes.

## 3. What runs by itself afterwards

The `worker` container runs the scheduler (UTC): ingestion 01:30 and 13:30, forecast publication 02:30 and 14:30,
model check (`ensure-models`, quarterly retraining through the gate) 02:00, alerts 02:45 and 14:45, monitoring 03:00
and 15:00, privacy purge 04:00. Job outcomes: `docker compose run --rm worker status`, `GET /v1/freshness`,
`GET /v1/monitoring`, and JSON quality reports under `reports/quality/`.

## 4. Before exposing it beyond this machine

Everything binds to 127.0.0.1. A public deployment needs, at least: HTTPS in front of nginx (then add
Strict-Transport-Security), the database and Redis left unpublished (remove their development ports), backups copied
off the machine and encrypted, and a host firewall. None of this was done or tested here (docs/19).

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `migrate` exits non-zero | `API_DB_PASSWORD` missing or shorter than 16 characters; `docker compose logs migrate` |
| A port is taken | set `DASHBOARD_PORT`, `POSTGRES_PORT`, `REDIS_PORT` or `MLFLOW_PORT` in `.env` |
| `retrain` refuses: "forecasting code changed after freezing" | the files under `src/mandicast/forecast/` differ from the evaluated ones (including their line endings, pinned in `.gitattributes`); restore them with `git checkout -- src/mandicast/forecast` |
| `publish-forecasts`: "fewer than 100 calibration residuals" | a newly loaded database has no walk-forward predictions to calibrate ranges on (note under §2) |
| Health `degraded` | Redis unreachable: responses still served, uncached, with an in-process rate limit |
| Health 503 `down` | database unreachable; requests fail within `DB_CONNECT_TIMEOUT_S` (5 s) |
| Forecasts marked "last value" after 1 Jan / 1 Apr / 1 Jul / 1 Oct | a model may serve only its own quarter; until the 02:00 `ensure-models` job has trained and gated the next one (or you run step 7), the last price is served with its own ranges |
| Permission denied on `data/` (Linux) | step 3 |
