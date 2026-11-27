import { describe, expect, it } from "vitest";
import forecastH7 from "../test/fixtures/forecast_onion_199_h7.json";
import type { ForecastResponse } from "../api/types";
import {
  bandPath, dateTicks, dayNumber, isoFromDay, linearScale, monthTickLabel, nearestIndex, niceTicks, splitRuns,
} from "./chart";
import { assignSlots, forecastPoints, lastObserved } from "./forecast";
import { addDays, daysBetween, formatDate, inr, inrPerKg, pct, relativeDays, signedPct, todayIso } from "./format";
import { hasCodeInLocation, takeCodeFromLocation } from "./login";

describe("format", () => {
  it("uses Indian digit grouping and rupees", () => {
    expect(inr(3650)).toBe("₹3,650");
    expect(inr(100000)).toBe("₹1,00,000");
    expect(inrPerKg(3650)).toBe("₹36.50");
  });
  it("formats shares and signed changes with a real minus sign", () => {
    expect(pct(0.7797)).toBe("78.0%");
    expect(signedPct(-10.1)).toBe("−10.1%");
    expect(signedPct(8.47)).toBe("+8.5%");
    expect(signedPct(-0.01)).toBe("0.0%");
  });
  it("treats API dates as calendar dates, independent of the time zone", () => {
    expect(formatDate("2026-11-26")).toBe("26 Nov 2026");
    expect(daysBetween("2026-11-23", "2026-11-30")).toBe(7);
    expect(addDays("2026-11-26", -35)).toBe("2026-10-22");
    expect(todayIso(new Date(2026, 10, 27, 23, 59))).toBe("2026-11-27");
    expect(relativeDays(0)).toBe("today");
    expect(relativeDays(1)).toBe("yesterday");
    expect(relativeDays(5121)).toBe("5,121 days ago");
  });
});

describe("chart geometry", () => {
  it("converts dates to day numbers and back", () => {
    expect(isoFromDay(dayNumber("2026-11-23"))).toBe("2026-11-23");
    expect(dayNumber("1970-01-05") % 7).toBe(4); // a Monday, used to align weekly ticks
  });

  it("breaks the history line at gaps longer than 3 days and keeps shorter ones", () => {
    const pts = [1, 2, 5, 9, 10, 20].map((x) => ({ x, y: x }));
    const runs = splitRuns(pts, 3);
    expect(runs.map((r) => r.map((p) => p.x))).toEqual([[1, 2, 5], [9, 10], [20]]);
  });

  it("makes clean ticks that cover the data", () => {
    const ticks = niceTicks(3447, 5338, 5);
    expect(ticks[0]).toBeLessThanOrEqual(3447);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(5338);
    expect(ticks).toEqual([3000, 3500, 4000, 4500, 5000, 5500]);
    expect(niceTicks(100, 100).length).toBeGreaterThan(1);
  });

  it("places weekly ticks on Mondays for short spans and month starts for long ones", () => {
    const start = dayNumber("2026-10-22");
    const weekly = dateTicks(start, dayNumber("2026-12-07"), 6);
    expect(weekly.length).toBeGreaterThan(1);
    for (const d of weekly) expect(new Date(d * 86_400_000).getUTCDay()).toBe(1);
    const monthly = dateTicks(dayNumber("2025-11-26"), dayNumber("2026-11-30"), 6);
    for (const d of monthly) expect(isoFromDay(d).slice(8)).toBe("01");
  });

  it("labels month ticks with a full year, never a two-digit one that reads like a date", () => {
    expect(monthTickLabel(dayNumber("2026-11-01"), 0)).toBe("Nov 2026");
    expect(monthTickLabel(dayNumber("2026-12-01"), 1)).toBe("Dec");
    expect(monthTickLabel(dayNumber("2027-01-01"), 2)).toBe("Jan 2027");
  });

  it("finds the nearest position and inverts scales", () => {
    expect(nearestIndex([10, 20, 30], 24)).toBe(1);
    expect(nearestIndex([10, 20, 30], 26)).toBe(2);
    expect(nearestIndex([], 5)).toBe(-1);
    const s = linearScale([0, 10], [100, 200]);
    expect(s(5)).toBe(150);
    expect(s.invert(150)).toBe(5);
  });

  it("draws a closed band from upper to lower", () => {
    const s = linearScale([0, 1], [0, 1]);
    expect(bandPath([{ x: 0, lower: 0, upper: 1 }, { x: 1, lower: 0, upper: 1 }], s, s)).toBe(
      "M0.0,1.0L1.0,1.0L1.0,0.0L0.0,0.0Z");
  });
});

describe("forecast helpers (recorded API response)", () => {
  const fc = forecastH7 as unknown as ForecastResponse;
  it("reads both ranges of every point", () => {
    const pts = forecastPoints(fc);
    expect(pts).toHaveLength(7);
    const day7 = pts[6]!;
    expect(day7).toMatchObject({ h: 7, date: "2026-11-30", y: 4043.24, l80: 3738.09, u80: 4574.95, l90: 3575.69,
                                 u90: 4841.05 });
    for (const p of pts) {
      expect(p.l90!).toBeLessThanOrEqual(p.l80!);
      expect(p.u90!).toBeGreaterThanOrEqual(p.u80!);
    }
  });
  it("reads the last input price and rejects malformed values", () => {
    expect(lastObserved(fc)).toEqual({ date: "2026-11-23", price: 4100 });
    expect(lastObserved({ ...fc, last_observed: { date: 5 } })).toBeNull();
  });
  it("keeps each mandi's colour when another is removed", () => {
    const a = assignSlots(new Map(), [11, 22, 33]);
    expect([...a.values()]).toEqual([0, 1, 2]);
    const b = assignSlots(a, [11, 33]);
    expect(b.get(33)).toBe(2);
    const c = assignSlots(b, [11, 33, 44]);
    expect(c.get(44)).toBe(1);
  });
});

describe("sign-in code", () => {
  it("is read from the fragment and removed from the address bar", () => {
    window.history.replaceState(null, "", "/login#code=abcDEF123_-xyz");
    expect(hasCodeInLocation()).toBe(true);
    expect(takeCodeFromLocation()).toBe("abcDEF123_-xyz");
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/login");
    expect(takeCodeFromLocation()).toBeNull();
  });
});
