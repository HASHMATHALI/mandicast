# MandiCast — Known limitations

Date: 2026-11-27. The current list in one place. Each phase report keeps its own list as written at the time; items
resolved later are marked there. Evidence for every number is in the linked document.

## 1. What the forecasts can and cannot do

| Limitation | Evidence | Consequence |
|---|---|---|
| **Direction of moves ≥ 2% is right 61–77% of the time**, against an aspiration of ≥ 90%. No method in the project came close to 90% (best development figure about 75%) | docs/10 §1–2 | Do not read the forecast as "up" or "down" advice. The dashboard and bot say so |
| **Ranges under-cover.** 80% ranges held 74.0–78.5% of hold-out prices for four crops and 55.7–61.5% for Soyabean; 90% ranges 86.1–88.5% and 69.0–75.1% | docs/10 §6 | The stated level is optimistic, most of all for Soyabean after its volatility changed; Soyabean forecasts carry a warning |
| **The model adds a little over the last price.** MASE 0.885–0.993: 1–12% less error than "price stays where it was"; Tomato at 7 days is slightly worse (1.008) | docs/10 §2 | Most of the apparent closeness (1 − WAPE 79–98%) comes from prices being persistent, not from the model. Tomato 7-day forecasts carry a warning |
| Only past prices and calendar features: no arrivals (no bulk official source), weather, policy or festival data | docs/02 §1, docs/06 | Shocks are not anticipated |
| One hold-out year (52 Monday origins); Soyabean rests on fewer points (≈ 4,700 per horizon) | docs/10 §3 | Results could differ in other years |
| Forecasts only for eligible (commodity, mandi) series: 2,003 of 11,310 at the first publication | docs/09, docs/06 §2 | Most mandis show history only, with the failed rule explained |

## 2. Data

- Prices are as reported by the APMCs to AGMARKNET; the source does not guarantee them. 0.01–0.66% of targets are
  flagged as likely data-entry errors and kept visible.
- Mandis renamed in Nov 2025 whose new name is not "old name + APMC" (11–17% of active "APMC" names) are not linked;
  they appear as two mandis until reviewed entries are added to `config/manual_aliases.csv` (empty).
- Five crops (Onion, Tomato, Potato, Wheat, Soyabean); the source has many more. History: Jan 2001 – present.

## 3. Operations and MLOps

- **Live monitoring has about one week of published forecasts** (first publication 2026-11-23); it becomes
  informative after about four publications. Live figures are flagged "not yet reliable" below 500 points.
- The promotion gate's evaluation set is optimistic for the candidate (it chose the number of trees); the gate stops
  broken or clearly worse models, not small differences.
- Drift (PSI) warnings are frequent by construction (a market-wide move shifts every mandi's features) and are
  reported, not acted on.
- Two operating rules were changed after seeing simulations (automatic fallback dropped; one gate condition made
  non-blocking). Both are dated and conservative (docs/06 §9b), but they are post-hoc choices.
- Retraining is quarterly (the evaluated procedure), not weekly.
- MLflow uses a local SQLite store; its history is not in git (docs/16).
- The worker has no health check (the scheduler has no endpoint); a stalled scheduler shows up as missing-update
  warnings in `/v1/monitoring`.
- Nobody is notified of monitoring warnings, failed jobs or drift: they are shown in `/v1/monitoring`, the dashboard
  and the job records, and someone has to look.

## 4. Not yet run or measured

- **A deployment from a fresh clone cannot publish forecasts yet.** Range calibration starts from walk-forward
  predictions that are git-ignored and tied to the development database's series ids (docs/18 §2, note). Ingestion,
  history, comparisons and the API work; retraining is expected to work but was run only on the development database.
  Fix: a command that regenerates the calibration predictions for the current database, tested on a fresh load.
- **The Telegram bot has not been run against the real Telegram service**: it is tested with a mocked Bot API and a
  simulated chat on the real database. Running it needs a bot token in `.env` (docs/13, docs/18 step 10).
- The Playwright tests are not in CI (they need the full 10 GB database); they pass locally and against the Compose
  stack.
- Test coverage is not measured; no load or soak test beyond a single-worker latency benchmark (docs/17 §8).
- No automated accessibility audit (docs/14 §7 lists what was built for accessibility).

## 5. Deployment and security

- **Not deployed publicly.** Everything binds to 127.0.0.1; there is no TLS. A public deployment needs HTTPS in front
  of nginx, unpublished database and Redis ports, off-machine encrypted backups and a firewall (docs/18 §4).
- The dashboard's session token is in `localStorage`, readable by page scripts; mitigated by the CSP, no third-party
  code and no raw HTML.
- The app image is 1.78 GB (one image for all Python services); the hash-locked requirements are for linux/amd64 only.
- Backups stay on the same machine.
- Rate limits: 60 requests/minute per address at the API and 5/s (burst 40) at nginx; a mandi page makes about 6
  requests, so very fast browsing can hit the limit (the dashboard shows the wait).

## 6. Product scope

- English only (strings kept in one module for translation).
- Forecast alerts look at the 7-day point only and fire at most once per weekly publication.
- Mandi search is by name substring; comparison is limited to 4 mandis in the dashboard (10 in the API).
- One user role; no admin interface (operations are CLI commands).
