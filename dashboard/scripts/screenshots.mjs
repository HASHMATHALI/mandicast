// Screenshots of the running dashboard for the README (docs/images/). They show whatever the database holds when
// they are taken, so the capture date is written next to them in the README.
//
//   BASE_URL=http://localhost:8090 npm run screenshots      (default: the Compose stack)
//
// Uses the installed Edge, like the end-to-end tests (PW_CHANNEL=chrome to use Chrome).
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const base = (process.env.BASE_URL ?? "http://localhost:8090").replace(/\/+$/, "");
const out = resolve(dirname(fileURLToPath(import.meta.url)), "../../docs/images");
mkdirSync(out, { recursive: true });

const phone = { viewport: { width: 375, height: 812 }, mobile: true };
const laptop = { viewport: { width: 1280, height: 800 }, mobile: false };
// `card`: screenshot only the card that contains this test id; otherwise the visible window.
const shots = [
  { file: "forecast-mobile.png", path: "/mandi/199?crop=3", ready: "[data-testid=price-chart]", card: "forecast-summary", ...phone },
  { file: "accuracy-mobile.png", path: "/mandi/199?crop=3", ready: "[data-testid=accuracy-card]", card: "accuracy-card", ...phone },
  { file: "prices-mobile.png", path: "/", ready: "main", ...phone },
  { file: "compare-desktop.png", path: "/compare?crop=3&m=199,200", ready: "svg", ...laptop },
  { file: "data-desktop.png", path: "/data", ready: "table", ...laptop },
];

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? "msedge" });
try {
  for (const s of shots) {
    const context = await browser.newContext({
      viewport: s.viewport, isMobile: s.mobile, hasTouch: s.mobile, deviceScaleFactor: 2, colorScheme: "light",
    });
    const page = await context.newPage();
    await page.goto(base + s.path, { waitUntil: "networkidle" });
    await page.locator(s.ready).first().waitFor({ state: "visible", timeout: 20_000 });
    if (s.card) {
      // The sticky header would cover the top of a card scrolled into view.
      await page.evaluate(() => document.querySelectorAll("header").forEach((h) => { h.style.position = "static"; }));
      const testId = page.getByTestId(s.card);
      const card = s.card.endsWith("-card") ? testId : page.locator(".card", { has: testId });
      await card.first().screenshot({ path: resolve(out, s.file) });
    } else {
      await page.screenshot({ path: resolve(out, s.file) });
    }
    console.log(`${s.file}  <-  ${s.path}`);
    await context.close();
  }
} finally {
  await browser.close();
}
