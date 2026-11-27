// Pure geometry for the SVG charts: day numbers, scales, ticks, paths and gap handling. No React here, so every
// rule that decides what the reader sees (for example where a line is broken) is unit-tested.

import { toUtcDate } from "./format";

const DAY_MS = 86_400_000;
export const WEEKLY_TICKS_MAX_SPAN = 70;

/** Days since 1970-01-01 for a calendar date. */
export function dayNumber(iso: string): number {
  return Math.round(toUtcDate(iso).getTime() / DAY_MS);
}

export function isoFromDay(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

export interface Scale {
  (value: number): number;
  domain: [number, number];
  range: [number, number];
  invert: (pixel: number) => number;
}

export function linearScale(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  const f = ((v: number) => r0 + ((v - d0) / span) * (r1 - r0)) as Scale;
  f.domain = domain;
  f.range = range;
  f.invert = (p: number) => d0 + ((p - r0) / (r1 - r0 || 1)) * span;
  return f;
}

/** Clean tick values covering [min, max] (1, 2, 2.5 or 5 × 10^k steps). */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) {
    const pad = Math.abs(min) * 0.05 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  if (ticks.length && (ticks[ticks.length - 1] ?? 0) < max) ticks.push(Number((start + ticks.length * step).toFixed(10)));
  return ticks;
}

/** Date ticks: Mondays (every 1+ weeks) for spans up to 70 days, month starts for longer spans. */
export function dateTicks(startDay: number, endDay: number, maxTicks: number): number[] {
  const span = endDay - startDay;
  const ticks: number[] = [];
  if (span <= 0 || maxTicks < 1) return ticks;
  if (span <= WEEKLY_TICKS_MAX_SPAN) {
    const step = Math.max(7, Math.ceil(span / maxTicks / 7) * 7);
    // 1970-01-05 (day 4) was a Monday.
    let d = startDay + ((4 - (startDay % 7) + 7) % 7);
    for (; d <= endDay; d += step) ticks.push(d);
    return ticks;
  }
  const months: number[] = [];
  const s = new Date(startDay * DAY_MS);
  let y = s.getUTCFullYear();
  let m = s.getUTCMonth() + 1;
  for (;;) {
    if (m > 11) { m = 0; y += 1; }
    const d = Math.round(Date.UTC(y, m, 1) / DAY_MS);
    if (d > endDay) break;
    months.push(d);
    m += 1;
  }
  const every = Math.max(1, Math.ceil(months.length / maxTicks));
  return months.filter((_, i) => i % every === 0);
}

export interface XY {
  x: number;
  y: number;
}

/** Splits points (sorted by x, in days) into runs where neighbours are at most `maxGapDays` apart. A longer gap ends
 * the run, so the chart leaves it empty instead of drawing a line across days without reports. */
export function splitRuns<T extends XY>(points: T[], maxGapDays: number): T[][] {
  const runs: T[][] = [];
  let run: T[] = [];
  for (const p of points) {
    const prev = run[run.length - 1];
    if (prev && p.x - prev.x > maxGapDays) {
      runs.push(run);
      run = [];
    }
    run.push(p);
  }
  if (run.length) runs.push(run);
  return runs;
}

export function linePath(points: XY[], sx: Scale, sy: Scale): string {
  return points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
}

/** Closed area between `upper` and `lower` values at the same x positions. */
export function bandPath(points: { x: number; lower: number; upper: number }[], sx: Scale, sy: Scale): string {
  if (!points.length) return "";
  const top = points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.upper).toFixed(1)}`).join("");
  const bottom = [...points].reverse().map((p) => `L${sx(p.x).toFixed(1)},${sy(p.lower).toFixed(1)}`).join("");
  return `${top}${bottom}Z`;
}

/** Index of the value in a sorted array closest to `target` (-1 when empty). */
export function nearestIndex(sorted: number[], target: number): number {
  if (!sorted.length) return -1;
  let lo = 0;
  let hi = sorted.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((sorted[mid] ?? 0) < target) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs((sorted[lo - 1] ?? 0) - target) <= Math.abs((sorted[lo] ?? 0) - target)) return lo - 1;
  return lo;
}

/** Compact axis label for rupees: 950, 4,000, 12K. */
export function axisRupees(value: number, maxAbs: number): string {
  if (maxAbs >= 10_000) return `${Number((value / 1000).toFixed(1))}K`;
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
}

const monthFmt = new Intl.DateTimeFormat("en-IN", { month: "short", timeZone: "UTC" });

/** Label of the i-th month tick: "Nov", with the full year on the first tick and on January ("Jan 2027"). A
 * two-digit year ("Nov 26") would read like a date. */
export function monthTickLabel(day: number, index: number): string {
  const iso = isoFromDay(day);
  const m = monthFmt.format(new Date(day * DAY_MS));
  return index === 0 || iso.slice(5, 7) === "01" ? `${m} ${iso.slice(0, 4)}` : m;
}
