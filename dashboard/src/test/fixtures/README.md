# Test fixtures

Real responses of the local MandiCast API (`/v1/...`), recorded on 2026-11-27 with `curl` from the database built in
Phases 2–5 (AGMARKNET data, origin 2026-11-23 forecasts). They are used only by the component tests so the
dashboard is tested against the API's actual shapes and values. File names give the request: crop, market id
(199 = Lasalgaon(Niphad)), horizon or period. `forecast_tomato_199_422.json` is the API's `insufficient_data` error.
