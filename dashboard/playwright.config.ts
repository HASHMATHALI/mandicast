// End-to-end tests: the production build (with its Content-Security-Policy) against the real MandiCast API and
// database. Needs PostgreSQL and Redis running (`docker compose up -d db redis`) and the Python venv in ../.venv.
// Uses the installed Microsoft Edge (PW_CHANNEL=chrome for Chrome), so no browser download is needed.
import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
export const python = process.env.MANDICAST_PYTHON
  ?? (process.platform === "win32" ? `${root}.venv/Scripts/python.exe` : `${root}.venv/bin/python`);
const channel = process.env.PW_CHANNEL ?? "msedge";
// E2E_BASE_URL=http://localhost:8090 runs the tests against an already running deployment (the Compose stack, whose
// web server proxies /v1 to the API) instead of starting the API and the preview server.
const deployed = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: deployed ?? "http://localhost:5173", channel, trace: "retain-on-failure" },
  projects: [
    { name: "mobile-375", use: { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true,
                                 deviceScaleFactor: 2 } },
    { name: "desktop", grep: /@desktop/, use: { viewport: { width: 1280, height: 800 } } },
  ],
  webServer: deployed ? [] : [
    {
      // The tests make well over 60 requests a minute from one address, so the API runs with a higher limit.
      command: `"${python}" -m mandicast api`,
      cwd: root,
      url: "http://127.0.0.1:8000/v1/health",
      env: { API_RATE_LIMIT_PER_MINUTE: "3000" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npm run build && npx vite preview",
      url: "http://localhost:5173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
