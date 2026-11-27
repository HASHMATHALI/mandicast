import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearSession, getSession } from "../auth/session";
import eligibilityTomato from "../test/fixtures/eligibility_tomato_199.json";
import forecastH7 from "../test/fixtures/forecast_onion_199_h7.json";
import forecastTomato422 from "../test/fixtures/forecast_tomato_199_422.json";
import market199 from "../test/fixtures/market_199.json";
import pricesOnion from "../test/fixtures/prices_onion_199_1m.json";
import pricesTomato from "../test/fixtures/prices_tomato_199_1m.json";
import trendsOnion from "../test/fixtures/trends_onion_199.json";
import trendsTomato from "../test/fixtures/trends_tomato_199.json";
import { mockFetch, renderAt, reply } from "../test/utils";
import { LoginPage } from "./LoginPage";
import { MandiPage } from "./MandiPage";

afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});

const byCrop = (onion: unknown, tomato: unknown) => ({ url }: { url: URL }) =>
  ({ body: url.searchParams.get("commodity_id") === "4" ? tomato : onion });

function mandiRoutes() {
  return {
    "GET /v1/markets/199": market199,
    "GET /v1/trends": byCrop(trendsOnion, trendsTomato),
    "GET /v1/prices": byCrop(pricesOnion, pricesTomato),
    "GET /v1/forecasts": ({ url }: { url: URL }) => url.searchParams.get("commodity_id") === "4"
      ? { status: 422, body: forecastTomato422 } : { body: forecastH7 },
    "GET /v1/forecasts/eligibility": eligibilityTomato,
  };
}

describe("Mandi page (recorded API responses)", () => {
  it("shows the latest price, the forecast with its range, accuracy against the last price and the disclaimer",
     async () => {
       mockFetch(mandiRoutes());
       renderAt("/mandi/199?crop=3", "/mandi/:marketId", <MandiPage />);
       const latest = await screen.findByTestId("latest-card");
       expect(latest).toHaveTextContent("₹3,650");
       expect(latest).toHaveTextContent("₹36.50 per kg");
       expect(latest).toHaveTextContent("26 Nov 2026");
       const summary = await screen.findByTestId("forecast-summary");
       expect(summary).toHaveTextContent("Day 7 (30 Nov 2026)");
       expect(summary).toHaveTextContent("₹4,043");
       expect(summary).toHaveTextContent("80% range: ₹3,738–₹4,575");
       expect(summary).toHaveTextContent("Not a guarantee of future prices");
       const acc = screen.getByTestId("accuracy-card");
       expect(acc).toHaveTextContent("7.7%");
       expect(acc).toHaveTextContent("8.0%");
       expect(within(acc).getByText("Yes")).toBeInTheDocument();
       expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
       expect(screen.getByText(/Prices as reported by the market committees/)).toBeInTheDocument();
     });

  it("explains why there is no forecast instead of showing one", async () => {
    mockFetch(mandiRoutes());
    renderAt("/mandi/199?crop=4", "/mandi/:marketId", <MandiPage />);
    expect(await screen.findByText("No forecast for this mandi")).toBeInTheDocument();
    expect(await screen.findByText(/Reason: not reported in the last 14 days/)).toBeInTheDocument();
    const table = await screen.findByTestId("eligibility-table");
    expect(table).toHaveTextContent("≥ 250");
    expect(screen.queryByTestId("forecast-summary")).toBeNull();
    expect(screen.getByText(/has not reported this crop for/)).toBeInTheDocument();
  });
});

describe("Login page", () => {
  it("exchanges the one-time code once, after removing it from the address bar", async () => {
    const seen: string[] = [];
    const calls = mockFetch({
      "POST /v1/auth/session": () => {
        seen.push(window.location.href);
        return { body: { token: "session-token", token_type: "bearer", expires_at: "2099-01-01T00:00:00Z" } };
      },
    });
    window.history.replaceState(null, "", "/login#code=one-time-code-abc");
    renderAt("/login", "/login", <LoginPage />);
    expect(await screen.findByText("other page")).toBeInTheDocument(); // navigated to /alerts
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(calls[0]!.body).toEqual({ code: "one-time-code-abc" });
    expect(seen[0]).not.toContain("code=");
    expect(getSession()?.token).toBe("session-token");
  });

  it("explains a used or expired link", async () => {
    mockFetch({ "POST /v1/auth/session": reply(401, { error: { code: "invalid_code",
      message: "The sign-in link is invalid, expired or already used." } }) });
    window.history.replaceState(null, "", "/login#code=used-code-1234");
    renderAt("/login", "/login", <LoginPage />);
    expect(await screen.findByText(/invalid, expired or already used/)).toBeInTheDocument();
    await waitFor(() => expect(getSession()).toBeNull());
  });

  it("asks for the bot's link when there is no code", async () => {
    const calls = mockFetch({});
    window.history.replaceState(null, "", "/login");
    renderAt("/login", "/login", <LoginPage />);
    expect(await screen.findByText(/needs the link from the bot/)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });
});
