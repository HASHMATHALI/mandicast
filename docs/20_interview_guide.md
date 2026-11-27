# MandiCast — Interview guide

Date: 2026-11-27. For explaining the project in AI/ML/backend interviews. Every number here is in the linked
document; quote the document, not memory. The repository's commits record that the code was written with an AI
coding assistant: be ready to explain, defend and change any part of it.

## 1. The pitch (30 seconds)

MandiCast is a price-intelligence system for Indian wholesale agricultural markets (mandis). It ingests 14.8 million
official daily price records for five crops from data.gov.in, cleans them without overwriting the source, and serves
history, mandi comparisons and 7- and 14-day forecasts with ranges through an API, a mobile-first dashboard and a
Telegram bot. The forecasts were evaluated walk-forward on a hold-out year that was scored once, after every choice
was frozen: LightGBM beat "the price stays where it is" in 9 of 10 crop × horizon cases, and the report states
plainly where it falls short: direction accuracy 61–77%, ranges that under-cover.

## 2. The walk-through (2 minutes)

1. **Data** (docs/02, docs/09). AGMARKNET via the data.gov.in API, Jan 2001 – today. Every raw page is kept (gzip +
   SHA-256 manifest); every source row is stored once, keyed by a hash of its fields (`ON CONFLICT DO NOTHING`), so
   any job can be re-run. Cleaning is a separate layer: canonical names (including AGMARKNET's Nov 2025 " APMC"
   renames), quality flags, one primary price per mandi-day chosen with past data only.
2. **Evaluation** (docs/06, docs/10). The method was written down before fitting anything (pre-registered), with every
   later change dated. 150 development Mondays to choose; 52 hold-out Mondays to score once. Baselines first, then
   ETS / ARIMA, then a global LightGBM per crop. Tests prove features do not change when future data is deleted.
3. **Serving** (docs/11). The frozen method is retrained at the start of each quarter, its artifact hashed, and
   forecasts published weekly per crop with conformal 80% / 90% ranges and the measured accuracy next to them.
4. **MLOps** (docs/12, docs/16). Validation before training, a promotion gate (a deliberately worse model is
   rejected, tested on real data), rollback, live monitoring, drift, an audit trail, MLflow registry as a mirror.
5. **Product** (docs/13, docs/14). FastAPI with Redis caching and rate limits; Telegram bot with alerts; React
   dashboard that separates history, forecast and range and shows accuracy and a disclaimer on every forecast.
6. **Engineering** (docs/15, docs/17). Hardened Docker Compose, least-privilege database role, failure-mode tests,
   CI on GitHub (green), tested backups.

## 3. Numbers worth knowing

| Number | Meaning | Source |
|---|---|---|
| 14.8M | source rows for five crops (14,831,605 on 2026-11-27) | docs/09, `mandicast status` |
| 2,003 | mandis with a forecast at the first publication (origin 2026-11-23), of 11,310 series | docs/11 |
| 9 of 10 | crop × horizon cases where LightGBM beat the last price on the hold-out (MASE 0.885–0.993; Tomato 7-day 1.008) | docs/10 §2 |
| 2.3%–21.1% | hold-out WAPE (Wheat 7-day to Tomato 14-day) | docs/10 §2 |
| 61–77% | direction right for moves ≥ 2% (target was ≥ 90%: not met) | docs/10 §1 |
| 74.0–78.5% | how often four crops' 80% ranges held the price (Soyabean 55.7–61.5%) | docs/10 §1 |
| 15 of 15 | deliberately worse candidates rejected by the gate on real data | docs/12 |
| p95 ≤ 29 ms | API latency on a cache miss, one worker, laptop | docs/11 |
| 121 / 29 / 7 | backend tests / dashboard tests / end-to-end runs | docs/17 |

## 4. Design decisions you will be asked about

| Question | Answer |
|---|---|
| Why keep raw pages and source rows unchanged? | Every number shown can be traced to the row the government published, and every cleaning rule can be changed and replayed (`clean --since`) without re-downloading |
| How is ingestion idempotent? | Source rows are keyed by a SHA-256 of their normalised fields with a unique constraint; complete-date snapshots supersede rows only when they are not shrunken (an outage guard); a database advisory lock skips concurrent runs |
| What is a series, and which get forecasts? | (commodity, mandi). Eligible only if, with data up to the origin: last report ≤ 14 days old, ≥ 250 reports in 730 days, median gap ≤ 3 days, ≤ 5% flagged (docs/06 §2). Others get history and the failed rule |
| How did you avoid leakage? | Origins are Mondays; inputs use reports dated ≤ origin, targets the 14 days after; eligibility is recomputed per origin; the outlier flag is trailing-only; tests delete all later data and check that nothing changes (`tests/test_forecast.py`) |
| Why a one-time hold-out? | Choosing on the same data you report flatters results. Choices were written to `frozen_choices.json` with a hash of the code; the hold-out command refuses to run without it |
| Why a global LightGBM rather than one model per mandi? | Many short, gappy series: pooling a crop's mandis shares information. Target is log(price at t+h / last price), so it learns changes, not levels. Hyper-parameters fixed before the first run (C6), no tuning on results |
| Why MASE as well as WAPE? | WAPE looks good (1 − WAPE 79–98%) mostly because prices persist. MASE against the last price shows what the model adds: 1–12% less error |
| Why do the ranges under-cover? | Split conformal ranges assume the future is like the calibration period. Soyabean's volatility rose in the hold-out year; four crops are 1.5–6 points short. Reported, warned on, not tuned away after the fact (docs/10 §6) |
| Why retrain quarterly, not weekly? | Quarterly is what was evaluated. Weekly would serve a procedure with no evidence behind it |
| What does the promotion gate check? | Sane predictions (finite, positive, bounded change), not worse than the last price, and not more than 2% worse than the production model when that model could still serve (C10b). A crash or failed validation registers nothing |
| You dropped the automatic fallback. Why? | A rule to serve the last price when the model lost recently looked sensible; simulated on development data it helped in 1 of 6 triggered cases and hurt in 5. It now informs instead of switching, with a dated amendment |
| How do you know the served model is the evaluated one? | The forecasting code's SHA-256 is checked before training or serving; the model artifact's SHA-256 on every load. (The first GitHub CI run caught that line endings changed that hash on a Linux checkout; fixed by pinning them in `.gitattributes`, not by relaxing the check.) |
| How is the API protected? | Strict parameters (unknown ones are 400), read-only database sessions with a statement timeout, generic 500s with a request id, per-client rate limits in Redis with HMAC-hashed addresses, cache keyed by the latest data job, a least-privilege role, no CORS behind nginx |
| How does sign-in work without passwords? | The bot sends a one-time link with the code in the URL fragment (never sent to servers or logs); the dashboard exchanges it for a 30-day Bearer token. Only SHA-256 hashes of codes and tokens are stored; the only personal datum is the Telegram chat id |
| How are alerts not sent twice? | Each notification has an idempotency key (subscription + report date or forecast run) with a unique constraint; retries reuse the row |

## 5. Hard questions, honest answers

- **"So your model is 92% accurate?"** No. 1 − WAPE is 92% for Onion at 7 days, but the last price alone gets 92.0%;
  the model's gain is MASE 0.96 (4% less error). Direction accuracy is 70%.
- **"Why didn't you reach 90% direction accuracy?"** Daily mandi prices are noisy and driven by arrivals, weather and
  policy that are not in the data. No method tried came close; the report says so instead of changing the target.
- **"Would you ship ranges that under-cover?"** With the measured coverage shown next to them and a warning where it
  is worst, yes; without that, no. A next step is recalibrating faster after volatility changes, then re-evaluating.
- **"What went wrong?"** docs/17 §7: e.g. no connect timeout (130 s hangs during a database outage), a restore check
  that passed while comparing nothing, the line-ending hash difference found by CI, several data quirks of the source.
  Each was found by a test or a check and now has one.
- **"Is it in production?"** No: it runs locally in Docker Compose; it has no public URL and no real users; the bot has
  not been run against the real Telegram service yet; and a deployment from a fresh clone cannot publish forecasts
  yet, because range calibration starts from walk-forward predictions that are not in git (docs/19 §4).
- **"How would it scale?"** Partition `price_observations` by date beyond tens of millions of rows; more API workers
  (rate limits are already shared through Redis); split the 1.78 GB image; MLflow server with a database backend.

## 6. What to show (5 minutes)

1. Dashboard at http://localhost:8090: a mandi page (history, forecast, ranges, accuracy, disclaimer), a mandi
   without enough data, the data page. Screenshots in `docs/images/`.
2. `http://localhost:8090/v1/forecasts?commodity_id=3&market_id=199`: the accuracy and warnings in the response.
3. `docs/10_model_evaluation_report.md` §1–2 and `experiments/forecast/frozen_choices.json`.
4. The GitHub Actions run, and `docs/17_testing_report.md` §7.
5. MLflow (`docs/16`): runs per method and the registry alias.

## 7. What you would do next

Run the bot with a real token; let a fresh deployment build its own calibration predictions; recalibrate ranges after volatility shifts and re-evaluate; add arrivals or weather if
a bulk source appears; link the remaining renamed mandis after review; measure test coverage; put Playwright in CI on
a small fixture database; deploy behind HTTPS.
