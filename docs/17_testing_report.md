# MandiCast — Testing report

Date: 2026-11-27. Every number below comes from a run on that date unless another date is given. Failure-mode matrix:
docs/15 §2. How to run each suite: §6.

## 1. Summary

| Suite | Size | Runs on | Latest result |
|---|---|---|---|
| Backend: pytest (`tests/`) | **121 tests** in 12 files | real PostgreSQL 16 (`mandicast_test`, rebuilt from the migrations per session) and Redis 8; Telegram and data.gov.in mocked | 121 passed on Windows (34.5 s); the 116 without `test_docs.py` also on a Linux checkout and on **GitHub Actions**; the 121 on GitHub after the Phase 9 push (§4) |
| Dashboard: Vitest + Testing Library (`dashboard/src/**/*.test.*`) | **29 tests** in 4 files | jsdom; component tests use real API responses recorded 2026-11-27 (`src/test/fixtures/`) | 29 passed (local and GitHub Actions) |
| End-to-end: Playwright (`dashboard/e2e/`) | **5 tests, 7 runs** (5 at 375 px with touch, 2 also on desktop) | installed Edge, production build, real API and full database | 7 passed (local dev servers; and against the Compose stack) |
| Static checks | — | ruff (Python), `tsc` strict, ESLint (no raw HTML), `pip-audit`, `npm audit` | clean / 0 known vulnerabilities |
| CI (`.github/workflows/ci.yml`) | 3 jobs | GitHub-hosted Ubuntu 24.04 | **run 46034190212: all 3 jobs passed** (§4) |

Test coverage (lines or branches) is **not measured**: no coverage tool is in the locked dependencies. The tables below
list what is exercised instead.

## 2. Backend tests by file

| File | Tests | What they establish |
|---|---|---|
| `test_store_and_fetch.py` | 9 | Raw store: header change rejected, tampered file detected, identical or re-ordered snapshots not stored twice; fetcher: resume without re-fetching, boundary-date repair, short pages retried, dates without records |
| `test_load_and_clean.py` | 12 | Idempotent loads; snapshots recover missing rows, supersede, are refused when shrunken; a schema change rolls back the whole load; mandi renames merge without inventing values; deterministic rebuild; quality flags; primary value and outlier flag use only past data |
| `test_client_and_logging.py` | 7 | data.gov.in client retries and gives up without leaking the key; log redaction of keys, URLs with passwords, exceptions |
| `test_jobs_and_pipeline.py` | 5 | Job records with redacted errors; a concurrent run is skipped; backfill then daily is idempotent and picks up corrections; scheduler registration |
| `test_forecast.py` | 14 | **No look-ahead:** dev and hold-out periods do not overlap; eligibility, features and the "regular view" do not change when later reports are deleted; baselines and metrics on known values; hold-out run needs `--confirm-holdout` and the frozen file; conformal intervals use only earlier folds |
| `test_serving.py` | 9 | Frozen-code guard (accepts only a verifiable documented edit); calibration uses only targets before the origin; artifact SHA-256 checked; publication idempotent and never overwrites; database refuses inconsistent intervals and a second production model |
| `test_mlops.py` | 19 | Promotion gate: promotes a good candidate, **rejects deliberately worse ones**, rejects broken predictions; validation before training; PSI drift; rollback restores the previous model; a production model failing re-evaluation is rolled back; a training crash in the scheduled job changes nothing; last-value publication, monitoring and their API |
| `test_api.py` | 23 | Every endpoint family; strict parameters; pagination; primary values without filling; trends equal a direct computation; insufficient data / model unavailable; read-only DB sessions; generic 500s; rate limit (per client, shared through Redis, `X-Forwarded-For` trusted only when configured); cache hit then invalidation by new data; database and Redis outages |
| `test_bot.py` | 15 | Telegram client (rate limits, token never in errors); only chat ids stored; groups ignored; commands; alerts sent once (idempotency keys), paused when blocked, retried on 5xx up to 5 times; login link → session → alert API; expiry and purge; personal responses never cached |
| `test_db_roles.py` | 2 | The `mandicast_api` role reads everything, writes only the 5 account tables, cannot create, drop or truncate; the API serves and signs in with it |
| `test_dashboard_contract.py` | 1 | The dashboard's copy of the OpenAPI document equals the API's |
| `test_docs.py` (Phase 9) | 5 | The documents match what exists: the column reference in docs/05 equals a database built from the migrations; docs/07 lists exactly the API's operations; every documented CLI command, file path, link and cited test name exists |

## 3. Dashboard tests

| File | Tests | What they establish |
|---|---|---|
| `src/api/client.test.ts` | 6 | URLs without empty parameters; API errors and rate limits explained; token only on authenticated calls, never cookies; expired or rejected sessions dropped; network failures without technical details |
| `src/lib/lib.test.ts` | 14 | Indian digit grouping; dates as calendar dates independent of the time zone; **history line broken at gaps > 3 days**; ticks; month labels with a full year; interval bands; stable series colours |
| `src/components/PriceChart.test.tsx` | 4 | History, forecast and ranges are separate marks; 90% range only when asked; keyboard readout equals the hover tooltip |
| `src/pages/pages.test.tsx` | 5 | Mandi page shows price, forecast with range, accuracy against the last price and the disclaimer; "not enough data" explains why; one-time sign-in code removed from the address bar, exchanged once; expired links explained |
| `e2e/dashboard.spec.ts` | 5 (7 runs) | Search → history → forecast → ranges → accuracy; insufficient data; compare on common dates; data page; sign in with a one-time link, create and delete an alert, delete the account. Every test fails on a CSP violation, a page error or horizontal overflow at 375 px |

## 4. Runs of record

| Date | Run | Result |
|---|---|---|
| 2026-11-26 | Phases 2–3 suites | 33 → 47 passed |
| 2026-11-27 | Phases 4–8 suites (Windows venv) | 77 → 95 → 109 → 110 → **116 passed** |
| 2026-11-27 | Linux test image built from the clone-equivalent file set (Phase 8) | 116 passed |
| 2026-11-27 | GitHub Actions run 46032340358 (commit `ffc5c01`) | **failed**: backend Tests step, 1 test (`test_code_guard_accepts_only_a_verifiable_post_freeze_edit`); dashboard and images jobs passed |
| 2026-11-27 | Reproduction: fresh Linux checkout, empty PostgreSQL and Redis | same failure (115 passed, 1 failed) |
| 2026-11-27 | Same reproduction after the fix | all passed; ruff clean |
| 2026-11-27 | **GitHub Actions run 46034190212** (commit `c61c4f4`) | **all 3 jobs passed** (backend 1 min 38 s, dashboard 25 s, images + fresh deployment 1 min 52 s) |
| 2026-11-27 | Phase 9: dashboard rebuilt (live-accuracy note shown as a sentence); Playwright against the Compose stack | first run straight after the screenshot script: 5 of 7 (2 hit the rate limit, "Too many requests"); rerun a minute later: **7 passed** |
| 2026-11-27 | Phase 9: `test_docs.py` added (Windows venv) | **121 passed** in 34.5 s; on first run it found stale documentation (a renamed test cited in docs/09; a non-existent `name_aliases.entity_id` in docs/04), fixed |
| 2026-11-27 | **GitHub Actions run 46037678158** (commit `8f56e96`, Phase 9) | **all 3 jobs passed**, including the 121 backend tests on Linux (the generated schema reference matches a database migrated on Linux too) |

**What the first GitHub run found.** The frozen forecasting code is identified by the SHA-256 of its exact bytes. It
was frozen on Windows, where 8 of its 13 files had CRLF line endings and 5 had LF; git stores LF, so every Linux
checkout (CI and the Compose images) and every fresh Windows clone with `core.autocrlf` produced other bytes, and the
guard refused to train or serve. The earlier Linux reproduction had copied the Windows working files into the image,
so it could not see this. Fix: `.gitattributes` pins each frozen file's line ending; a fresh Windows clone and a Linux
checkout now pass the guard. The guard itself was not weakened.

## 5. Verification on real data (scripts, not unit tests)

| Script | What it checks | Latest result |
|---|---|---|
| `scripts/verify_phase2.py` | database vs raw files: counts, hashes, no orphans or duplicate primary days | all checks OK for all five crops (2026-11-26) |
| `scripts/verify_phase4.py` | serving artifacts reproduce the hold-out 2026Q4 fit | all crops match (2026-11-27) |
| `scripts/verify_phase5.py` | artifacts, gate results, MLflow alias and events consistent | all checks OK after fixing a missing alias it found |
| `scripts/backtest_gate.py` | promotion gate on 25 real quarterly candidates and 15 deliberately worse ones | 24 of 25 passed as first recorded; **all 15 worse candidates rejected** |
| `scripts/backtest_fallback.py` | automatic last-value fallback on the development period | helped in 1 of 6 triggered cases, hurt in 5 → automatic switching dropped (dated amendment, docs/06 §9b) |
| `scripts/benchmark_api.py` | API latency, one worker, laptop | cache miss p50 10–22 ms, p95 ≤ 29 ms; cache hit p50 ≈ 3 ms |
| `scripts/restore_check.sh` | a backup restores with identical row counts | 21 tables, 29,717,867 rows identical |

## 6. Running the suites

```powershell
docker compose up -d db redis                 # the test database is created by docker/postgres/init
.venv\Scripts\python -m ruff check src tests scripts migrations
.venv\Scripts\python -m pytest
cd dashboard; npm test; npm run typecheck; npm run lint
npm run test:e2e                              # needs the full database; starts the API and a production build
$env:E2E_BASE_URL="http://localhost:8090"; npm run test:e2e   # or against the running Compose stack
```

Against the Compose stack all tests come from one address, so they share the API's limit of 60 requests a minute.
Start them with a fresh minute (on 2026-11-27 a run straight after the screenshot script failed 2 of 7 with "Too many
requests"; the rerun a minute later is in §4), or raise `API_RATE_LIMIT_PER_MINUTE` in `.env` for test runs. The
self-contained `npm run test:e2e` starts its own API with a higher limit.

## 7. Defects found by testing (all fixed, each now covered)

| Phase | Defect | Found by |
|---|---|---|
| 2 | Empty-body responses for dates without records; row order differing between identical requests; colliding snapshot names; corrections compared with the wrong baseline; window-dependent name labels; Docker's 64 MB `/dev/shm`; day-level conflicts missing from eligibility | runs on real data (docs/09 §4a) |
| 3 | LightGBM state codes not stable across frames (fixed before freezing) | `test_state_codes_are_stable_across_frames` |
| 5 | MLflow alias missing for a promoted model | `scripts/verify_phase5.py` |
| 7 | Forecast unreadable on a phone at 3 months; overlapping labels; month ticks read as dates; unsent messages listed as sent; a page 541 px wide on a 375 px screen | browser checks; the e2e overflow check |
| 8 | No database connect timeout: requests hung 130 s per attempt during an outage | `test_database_outage_is_reported_and_leaks_nothing` |
| 8 | Restore check reported "OK" after comparing nothing | its own first run (now requires a count for every table) |
| 8 | Frozen-code hash differed on any fresh checkout (line endings) | the first GitHub Actions run |

## 8. Not tested

- The bot against the real Telegram service (mocked only; waits for a bot token, docs/13).
- A complete deployment from a fresh clone with data: backfill, retrain and publication were run on the development
  database only; publication on a newly loaded database is known not to work yet (docs/18 §2).
- Load beyond the single-worker benchmark; no soak or concurrency test of the full stack.
- Playwright in CI (needs the full 10 GB database).
- Line or branch coverage (not measured).
- Upgrading a populated database across future migrations (only 0001→0005 from scratch and the live upgrades made
  during development).
