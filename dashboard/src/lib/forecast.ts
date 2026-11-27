// Forecast helpers shared by the chart, the summary and the table.
import type { ForecastResponse } from "../api/types";
import { dayNumber } from "./chart";

/** The chart joins reports at most this many days apart; longer gaps stay empty (docs/14 §3). */
export const MAX_GAP_DAYS = 3;

/** Categorical slots 1–4 (validated as a set, docs/14 §3), as CSS variables so dark mode swaps them. */
export const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"] as const;

export interface FcPoint {
  x: number;
  y: number;
  date: string;
  h: number;
  l80?: number;
  u80?: number;
  l90?: number;
  u90?: number;
  servedBy?: string;
  actual?: number | null;
}

function interval(p: ForecastResponse["points"][number], level: number) {
  return p.intervals.find((i) => Math.abs(i.level - level) < 1e-6);
}

export function forecastPoints(forecast: ForecastResponse): FcPoint[] {
  return forecast.points.map((p) => {
    const i80 = interval(p, 0.8);
    const i90 = interval(p, 0.9);
    return { x: dayNumber(p.date), y: p.modal_price, date: p.date, h: p.horizon_days, l80: i80?.lower,
             u80: i80?.upper, l90: i90?.lower, u90: i90?.upper, servedBy: p.served_by,
             actual: p.actual_modal_price ?? null };
  });
}

/** The last price the forecast was made from (`last_observed` is a free-form object in the schema). */
export function lastObserved(forecast: ForecastResponse): { date: string; price: number } | null {
  const lo = forecast.last_observed as { date?: unknown; modal_price?: unknown };
  if (typeof lo?.date !== "string" || typeof lo?.modal_price !== "number") return null;
  return { date: lo.date, price: lo.modal_price };
}

/** Assigns each selected id a colour slot, keeping the slots of ids that stay selected (colour follows the mandi,
 * never its position, so removing one never repaints the others). */
export function assignSlots(prev: ReadonlyMap<number, number>, ids: number[], slots = 4): Map<number, number> {
  const next = new Map<number, number>();
  for (const id of ids) {
    const s = prev.get(id);
    if (s !== undefined) next.set(id, s);
  }
  for (const id of ids) {
    if (next.has(id)) continue;
    const used = new Set(next.values());
    next.set(id, Array.from({ length: slots }, (_, i) => i).find((i) => !used.has(i)) ?? 0);
  }
  return next;
}
