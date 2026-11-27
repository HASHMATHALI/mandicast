# MandiCast — Dashboard (Phase 7)

Date: 2026-11-27. Code: `dashboard/` (React + TypeScript, Vite). Data: the MandiCast API only (docs/07). Requirements:
docs/01 F1–F11 and §5; roadmap exit criteria (docs/08): usable on a 375 px wide screen; history, forecast and ranges
visually distinct; an end-to-end test.

## 1. Pages

| Route | Requirement | Content | API |
|---|---|---|---|
| `/` | F1, F3, F9 | Crop chips, mandi search (name, state), latest reported price per mandi with its date and age; data freshness strip | `/commodities`, `/states`, `/markets`, `/prices/latest`, `/freshness` |
| `/mandi/:marketId?crop=` | F2, F3, F5–F8 | Latest price (₹/quintal and ₹/kg) with date; trend sentences; one chart with history, the forecast and its 80% / 90% range; accuracy card; warnings; disclaimer; "not enough data" state with the failed rule; price table; names as reported | `/markets/{id}`, `/prices`, `/trends`, `/forecasts`, `/forecasts/eligibility` |
| `/compare?crop=&m=` | F4 | Up to 4 mandis of one crop on the dates when all reported; chart + table | `/compare`, `/markets` |
| `/data` | F7, F9 | Freshness per crop, production models and their gate result, hold-out accuracy vs last value, live monitoring (flagged when not yet reliable) | `/freshness`, `/models/current`, `/monitoring` |
| `/alerts` | F10 | Signed out: how to get a link from the bot. Signed in: my alerts (create, delete), messages sent, sign out, delete my data | §6 endpoints |
| `/login#code=…` | F10 | Exchanges the one-time code from the bot's `/login` for a session | `POST /auth/session` |

## 2. Design decisions

| Decision | Reason |
|---|---|
| Vite + React 19 + TypeScript (strict), React Router, TanStack Query | Architecture choice (docs/03); Query gives caching, retries and loading states without hand-written effects |
| Charts drawn as SVG components in the app, no chart library | Full control over how history, forecast and ranges are told apart; small bundle on mobile data; testable pure scale and path functions |
| API types generated from the API's own OpenAPI document (`openapi-typescript`) | The dashboard cannot drift silently from the backend; a test fails when the saved schema and the API differ |
| Dependencies resolved with `npm install --before=2026-11-20` (7 days earlier), exact versions in `package.json` and `package-lock.json`, `npm audit` | Avoids installing a package version published in the last week (supply-chain hygiene); exact versions reproducible |
| TypeScript 5.9 | typescript-eslint supports TypeScript < 6.1 and openapi-typescript 7.13 requires 5.x (an install with 6.0.3 failed on that peer dependency) |
| Default view: last month + forecast, 80% range; 3M / 1Y / 3Y and the 90% range on request | On a 375 px screen a 3-month window left the 7-day forecast about 20 px wide (first browser check); with one month it is readable. docs/01 §8: 80% is easier to read; the hold-out coverage of both ranges is shown |
| ₹/quintal first, ₹/kg beside it | docs/01 §8: farmers think in ₹/kg |
| Every UI string in one module (`src/strings.ts`) | v1 is English; labels kept translatable (docs/01 §8) |
| Session token in `localStorage` with its expiry; cleared on 401 or sign-out | The API uses Bearer tokens on another origin; risk mitigated by a strict Content-Security-Policy, no third-party scripts and no HTML injection anywhere |

## 3. How the chart keeps history, forecast and ranges apart

| Element | Encoding |
|---|---|
| Reported modal price (history) | Solid 2 px blue line with small dots on each report; line drawn only between reports at most 3 days apart, so longer gaps stay visibly empty (stated under the chart) |
| Latest reported price | 8 px dot with a surface ring and a label "₹3,650 · 26 Nov" |
| Forecast | Orange, dashed 2 px line from the last input price, ending in a dot; named "Forecast" in the legend (hue and dash both differ, so it does not rely on colour alone). Its day 7 / day 14 values and ranges are printed above the chart rather than as an end label, which collided with the latest-price label on a phone |
| 80% / 90% range | Orange washes behind the forecast (stronger for 80%, lighter for 90%), labelled in the legend with the level |
| Issue date | Hairline with "Forecast issued 23 Nov"; reports after it stay on the blue line, so misses are visible |
| Hover / keyboard | Crosshair snaps to the nearest date; one readout lists every value at that date; the same values are in the table view |

Colours: blue `#2a78d6` / orange `#eb6834` (dark mode `#3987e5` / `#d95926`), validated with the dataviz palette
checker (colour-vision-deficiency separation ΔE 24.7 light / 26.8 dark). Comparison uses the first four categorical
slots; in light mode two of them are below 3:1 contrast, so the comparison always has a legend and a table view, plus
direct end labels when they do not overlap. A mandi keeps its colour while it stays selected.

## 4. Security and privacy

| Requirement | Implementation |
|---|---|
| No HTML injection | Everything rendered through React text nodes; no `dangerouslySetInnerHTML`; lint rule forbids it |
| Content-Security-Policy | Production build adds `default-src 'self'; connect-src 'self' <API origin>; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'` and `Referrer-Policy: no-referrer` |
| Sign-in code | Read from the URL fragment (never sent to a server by the browser), removed from the address bar before the request, used once |
| Session | Bearer token sent only to the configured API origin; expiry checked locally; 401 clears it; sign-out ends it on the server |
| Third parties | None: no fonts, analytics or CDNs; external links use `rel="noopener noreferrer"` |
| Rate limits | 429 responses shown as "too many requests, try again in N s"; queries cached and not retried on 4xx |

## 5. Verification (executed 2026-11-27)

| Check | Result |
|---|---|
| `npm run typecheck` (strict, `noUncheckedIndexedAccess`), `npm run lint` | no errors (lint forbids `dangerouslySetInnerHTML`) |
| `npm test` (Vitest, jsdom) | **29 passed**: formatting, chart geometry (gap splitting, ticks, bands), API client (error format, 429 message, token only on authenticated calls, `credentials: omit`, 401 clears the session), chart marks and keyboard readout, mandi page and "not enough data" page, sign-in page (code removed from the address before the request, used once). Component tests use real API responses recorded on 2026-11-27 (`src/test/fixtures/`) |
| `npm run test:e2e` (Playwright, installed Edge, production build with its CSP, real API and database) | **7 passed**: 5 at 375 × 812 (touch), 2 at 1280 × 800. Search → mandi page with history, forecast, 80% and 90% ranges, table view, keyboard readout; "not enough data" page; comparison; data page (5 crops × 2 horizons of hold-out accuracy); sign-in with a one-time link → create alert → duplicate refused (409) → delete → link refused on reuse → delete account (old token then gets 401). Each test fails on any CSP violation, page error, or a page wider than the screen |
| `pytest` (backend) | **110 passed** (109 + `test_dashboard_contract.py`: the dashboard's schema copy equals the API's; checked to fail when the copy is edited) |
| `npm audit` | 0 vulnerabilities (dependencies resolved with `--before=2026-11-20`, exact versions) |
| Production build | 360 kB JavaScript (111 kB gzip), 10 kB CSS; CSP meta tag present |
| Manual check in the browser (375 px, dark and light) | Pages render real data; no console errors except the expected 422 (no forecast) and 401 (reused link); alert created in the dashboard was evaluated by `mandicast alerts` (job 24) and its message appeared in the dashboard as "waiting to be sent" (no bot token) |

**Defects found by these checks and fixed:** the 3-month default made the forecast unreadable on a phone (now 1 month);
two end labels overlapped (forecast end label removed; values are printed above the chart); month ticks read "Nov 26"
like a date (now "Nov 2026"); trend sentences said "1 reports" and started with "It is" when the 7-day comparison was
missing; "Messages sent" listed unsent messages (now "Alert messages" with a status); the "not enough data" table
made the page 541 px wide on a 375 px screen (flex item without `min-width: 0`; found by the end-to-end overflow check).

## 6. Running it

```powershell
cd dashboard
npm ci
npm run dev            # http://localhost:5173 (API at http://127.0.0.1:8000; VITE_API_BASE to change)
npm test               # unit and component tests
npm run test:e2e       # starts the API (rate limit raised for the test) and the production build; needs db + redis
npm run gen:api        # after an API change, with the API running: refresh src/api/openapi.json and the types
```

Sign-in normally comes from the bot (`/login`). Without a bot token, `python scripts/e2e_login.py link` prints a
one-time link for a test user (negative chat id, which no real Telegram private chat can have) and
`python scripts/e2e_login.py delete` removes it; the end-to-end test uses the same script.

## 7. Limitations

- The real-bot sign-in path (Telegram → `/login` → dashboard) has not been run: `TELEGRAM_BOT_TOKEN` is not set yet
  (Phase 6). The dashboard side of sign-in was tested with codes issued by the same service function.
- The CSP is a meta tag; `frame-ancestors` and other header-only protections need the web server that hosts the
  build (Phase 8). The session token is readable by page scripts (Bearer API on another origin); the CSP and the
  absence of third-party code and raw HTML are the mitigation.
- No automated accessibility audit was run. Implemented: labels on every control, 44 px touch targets, visible focus,
  notices with icon and text, chart values from the keyboard and as tables, legends for every chart.
- English only (strings kept in one module for translation).
- A visitor moving quickly between mandi pages can reach the API's 60 requests/minute per address (each mandi page
  makes about 6 requests); the dashboard shows the wait time. To be revisited with the deployment (Phase 8).
- Comparison is limited to 4 mandis (the API allows 10) so the lines stay distinguishable.
