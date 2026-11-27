# MandiCast dashboard

Mobile-first React + TypeScript (Vite) web app on the MandiCast API: mandi search and latest prices, price history,
7- and 14-day forecasts with their 80% / 90% ranges and measured accuracy, "not enough data" explanations, mandi
comparison, data freshness and model monitoring, and alert management after signing in with the Telegram bot's
one-time link. Design, checks and limitations: `../docs/14_dashboard.md`.

```powershell
npm ci
npm run dev        # http://localhost:5173, API at http://127.0.0.1:8000 (`python -m mandicast api` in ..)
npm test           # unit and component tests (Vitest)
npm run test:e2e   # end-to-end in Edge against the real API (needs `docker compose up -d db redis` in ..)
npm run build      # production build in dist/ (adds the Content-Security-Policy)
npm run gen:api    # refresh src/api/openapi.json and src/api/schema.d.ts from the running API
npm run screenshots   # README screenshots into ../docs/images from the Compose stack (BASE_URL to change it)
```

Settings (`.env.local`, see `.env.example`): `VITE_API_BASE` (the API's origin must allow this dashboard in
`API_CORS_ORIGINS`), optional `VITE_BOT_USERNAME`. Only `VITE_*` values reach the browser, so no secrets go here.

| Path | Contents |
|---|---|
| `src/api/` | Fetch client, TanStack Query hooks, generated schema types |
| `src/components/` | Price chart (history, forecast, ranges), comparison chart, notices, mandi search |
| `src/pages/` | Prices, mandi, compare, about the data, alerts, sign-in |
| `src/lib/` | Formatting, chart geometry, forecast helpers |
| `src/strings.ts` | Every fixed UI text (for translation) |
| `src/test/fixtures/` | Real API responses recorded on 2026-11-27, used by component tests |
| `e2e/` | Playwright tests (375 px and desktop) |
