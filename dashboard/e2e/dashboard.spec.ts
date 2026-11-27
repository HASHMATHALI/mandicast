// End-to-end: real API, real database, production build. Every test also fails on any Content-Security-Policy
// violation or uncaught page error, and checks that nothing is wider than the screen.
import { test as base, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { python } from "../playwright.config";

const root = fileURLToPath(new URL("../..", import.meta.url));
const API = process.env.E2E_BASE_URL ? `${process.env.E2E_BASE_URL}/v1` : "http://127.0.0.1:8000/v1";

const test = base.extend<{ problems: string[] }>({
  problems: [async ({ page }, use) => {
    const problems: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" && /Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
    });
    page.on("pageerror", (e) => problems.push(e.message));
    await use(problems);
    expect(problems, "CSP violations or page errors").toEqual([]);
  }, { auto: true }],
});

function loginScript(action: "link" | "delete"): string {
  return execFileSync(python, ["scripts/e2e_login.py", action], { cwd: root, encoding: "utf8" }).trim();
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(scroll, "page wider than the screen").toBeLessThanOrEqual(client);
}

test("prices: search a mandi, read its history, forecast, ranges and accuracy @desktop", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Mandi prices" })).toBeVisible();
  await expect(page.locator('meta[http-equiv="Content-Security-Policy"]')).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Onion" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("latest-list").locator("li").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByLabel("Search mandis").fill("lasalgaon(n");
  await page.getByRole("button", { name: "Lasalgaon(Niphad)" }).click();
  await expect(page).toHaveURL(/\/mandi\/199\?crop=3$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Lasalgaon(Niphad)");
  await expect(page.getByTestId("latest-card")).toContainText("per quintal");
  await expect(page.getByTestId("forecast-summary")).toContainText("Day 7");
  await expect(page.getByTestId("forecast-summary")).toContainText("Not a guarantee");
  const chart = page.getByTestId("price-chart");
  await expect(chart.locator(".history-line").first()).toBeVisible();
  await expect(chart.getByTestId("forecast-line")).toHaveCount(1);
  await expect(chart.getByTestId("band-80")).toHaveCount(1);
  await expect(chart.getByTestId("band-90")).toHaveCount(0);
  await expect(page.getByTestId("accuracy-card")).toContainText("hold-out");
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "14 days" }).click();
  await expect(page.getByTestId("forecast-summary")).toContainText("Day 14");
  await page.getByRole("button", { name: "90%" }).click();
  await expect(chart.getByTestId("band-90")).toHaveCount(1);

  // Hover and keyboard give the same readout.
  await chart.focus();
  await page.keyboard.press("End");
  await expect(page.getByTestId("chart-tooltip")).toContainText("Forecast (day 14)");

  await page.getByRole("button", { name: "Show as table" }).click();
  await expect(page.getByTestId("forecast-table").locator("tbody tr")).toHaveCount(14);
  await expect(page.getByTestId("price-table").locator("tbody tr").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("a series without enough data explains why instead of forecasting", async ({ page }) => {
  await page.goto("/mandi/199?crop=4");
  await expect(page.getByText("No forecast for this mandi")).toBeVisible();
  await expect(page.getByTestId("eligibility-table")).toBeVisible();
  await expect(page.getByTestId("forecast-summary")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});

test("compare two mandis on common dates", async ({ page }) => {
  await page.goto("/compare?crop=3&m=199,200");
  await expect(page.getByTestId("compare-chart")).toBeVisible();
  const legend = page.getByRole("list", { name: "Legend" });
  await expect(legend).toContainText("Lasalgaon(Niphad)");
  await expect(legend).toContainText("Lasalgaon(Vinchur)");
  await page.getByRole("button", { name: "Show as table" }).click();
  await expect(page.getByTestId("compare-table").locator("tbody tr").first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("about the data: freshness, hold-out accuracy against the last price, live monitoring @desktop",
     async ({ page }) => {
       await page.goto("/data");
       await expect(page.getByTestId("freshness-table").locator("tbody tr")).toHaveCount(5);
       await expect(page.getByTestId("holdout-table").locator("tbody tr")).toHaveCount(10);
       await expect(page.getByTestId("holdout-table")).not.toContainText("Loading");
       await expect(page.getByTestId("models-table").locator("tbody tr")).toHaveCount(5);
       await expect(page.getByTestId("live-table")).toContainText("Not yet");
       await expectNoHorizontalOverflow(page);
     });

test.describe.serial("sign-in and alerts (test user, no Telegram)", () => {
  test.beforeAll(() => { loginScript("delete"); });
  test.afterAll(() => { loginScript("delete"); });

  test("sign in with a one-time link, create and delete an alert, delete the account", async ({ page, request }) => {
    // The script prints DASHBOARD_URL/login#code=…; only the path and fragment are used, so the same test runs
    // against the dev servers and a deployment.
    const link = loginScript("link");
    expect(link).toMatch(/^https?:\/\/[^/]+\/login#code=[A-Za-z0-9_-]{20,}$/);
    const loginPath = link.slice(link.indexOf("/login"));
    await page.goto(loginPath);
    await expect(page).toHaveURL(/\/alerts$/);
    await expect(page.getByText(/Signed in until/)).toBeVisible();
    expect(page.url()).not.toContain("code=");

    const form = page.locator("form");
    await form.getByLabel("Crop").selectOption({ label: "Onion" });
    await form.getByLabel("Mandi").fill("lasalgaon(n");
    await form.getByRole("button", { name: "Lasalgaon(Niphad)" }).click();
    await form.getByLabel("When").selectOption("price_above");
    await form.getByLabel(/Value/).fill("1000");
    await form.getByRole("button", { name: "Create alert" }).click();
    await expect(page.getByTestId("alert-list")).toContainText("Lasalgaon(Niphad)");
    await expect(page.getByTestId("alert-list")).toContainText("₹1,000");

    // The same alert twice is refused by the API (409) and explained.
    await form.getByLabel(/Value/).fill("1000");
    await form.getByRole("button", { name: "Create alert" }).click();
    await expect(form.getByRole("alert")).toBeVisible();

    await page.getByRole("button", { name: /^Delete alert \d+$/ }).click();
    await expect(page.getByText("No alerts yet.")).toBeVisible();

    // The link worked once only.
    const token = await page.evaluate(() => JSON.parse(localStorage.getItem("mandicast.session") ?? "{}").token);
    expect(typeof token).toBe("string");
    await page.goto(loginPath);
    await expect(page.getByText(/invalid, expired or already used/)).toBeVisible();

    await page.goto("/alerts");
    page.once("dialog", (d) => d.accept());
    await page.getByRole("button", { name: "Delete my data" }).click();
    await expect(page.getByText("Alerts are sent by the MandiCast Telegram bot")).toBeVisible();
    const after = await request.get(`${API}/alerts`, { headers: { Authorization: `Bearer ${token}` } });
    expect(after.status()).toBe(401);
    await expectNoHorizontalOverflow(page);
  });
});
