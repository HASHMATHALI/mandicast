import { afterEach, describe, expect, it, vi } from "vitest";
import { clearSession, getSession, saveSession } from "../auth/session";
import { mockFetch, reply } from "../test/utils";
import { api, ApiError, buildUrl, errorMessage } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  clearSession();
});

describe("API client", () => {
  it("builds URLs without empty parameters (the API rejects unknown or repeated ones)", () => {
    const url = new URL(buildUrl("/markets", { commodity_id: 3, q: "", state_id: undefined, has_forecast: null }));
    expect(url.pathname).toBe("/v1/markets");
    expect([...url.searchParams.keys()]).toEqual(["commodity_id"]);
  });

  it("turns the API error format into ApiError and explains rate limits", async () => {
    mockFetch({
      "GET /v1/forecasts": reply(422, { error: { code: "insufficient_data", message: "No forecast.",
                                                 details: { reason: "not reported" } } }),
      "GET /v1/commodities": reply(429, { error: { code: "rate_limited", message: "Too many" } },
                                   { "Retry-After": "17" }),
    });
    const err = await api.get("/forecasts").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 422, code: "insufficient_data", details: { reason: "not reported" } });
    const limited = await api.get("/commodities").catch((e) => e);
    expect(errorMessage(limited)).toBe("Too many requests. Try again in 17 seconds.");
  });

  it("sends the session token only on authenticated calls, never cookies", async () => {
    saveSession("tok-123", new Date(Date.now() + 86_400_000).toISOString());
    const calls = mockFetch({ "GET /v1/alerts": [], "GET /v1/commodities": [] });
    await api.get("/alerts", {}, undefined, true);
    await api.get("/commodities");
    expect(calls[0]!.headers.authorization).toBe("Bearer tok-123");
    expect(calls[1]!.headers.authorization).toBeUndefined();
    const init = (vi.mocked(fetch).mock.calls[0]![1]) as RequestInit;
    expect(init.credentials).toBe("omit");
  });

  it("drops the session when the API rejects it", async () => {
    saveSession("expired-on-server", new Date(Date.now() + 86_400_000).toISOString());
    mockFetch({ "GET /v1/alerts": reply(401, { error: { code: "auth_required", message: "Sign in" } }) });
    await expect(api.get("/alerts", {}, undefined, true)).rejects.toMatchObject({ status: 401 });
    expect(getSession()).toBeNull();
  });

  it("ignores stored sessions that have expired", () => {
    saveSession("old", new Date(Date.now() - 1000).toISOString());
    expect(getSession()).toBeNull();
  });

  it("reports network failures without technical details", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const err = await api.get("/commodities").catch((e) => e);
    expect(err).toMatchObject({ status: 0, code: "network_error" });
    expect(errorMessage(err)).toMatch(/Could not reach/);
  });
});
