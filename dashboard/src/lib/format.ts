// Number and date formatting. Dates from the API are calendar dates (YYYY-MM-DD) and are handled in UTC so a
// browser time zone never shifts them by a day.

const inrFormat = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inrKgFormat = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const intFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const dateFormat = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortDateFormat = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("en-IN", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

/** "₹3,650" (Indian digit grouping: ₹1,00,000). */
export function inr(value: number): string {
  return inrFormat.format(value);
}

/** Price per kilogram from a price per quintal (100 kg): "₹36.50". */
export function inrPerKg(perQuintal: number): string {
  return inrKgFormat.format(perQuintal / 100);
}

export function int(value: number): string {
  return intFormat.format(value);
}

/** A share (0.779) as "77.9%". */
export function pct(share: number, digits = 1): string {
  return `${(share * 100).toFixed(digits)}%`;
}

/** A change already in percent (-10.1) as "−10.1%" / "+10.1%" (true minus sign). */
export function signedPct(changePct: number, digits = 1): string {
  const s = Math.abs(changePct).toFixed(digits);
  if (Number(s) === 0) return `0${digits ? "." + "0".repeat(digits) : ""}%`;
  return `${changePct < 0 ? "−" : "+"}${s}%`;
}

export function toUtcDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

/** "26 Nov 2026" */
export function formatDate(iso: string): string {
  return dateFormat.format(toUtcDate(iso));
}

/** "26 Nov" */
export function formatShortDate(iso: string): string {
  return shortDateFormat.format(toUtcDate(iso));
}

/** A timestamp in the viewer's time zone: "27 Nov 2026, 06:40 am". */
export function formatDateTime(isoTimestamp: string): string {
  return dateTimeFormat.format(new Date(isoTimestamp));
}

/** Today's calendar date in the viewer's time zone, as YYYY-MM-DD. */
export function todayIso(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Whole days from `fromIso` to `toIso` (calendar dates). */
export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((toUtcDate(toIso).getTime() - toUtcDate(fromIso).getTime()) / 86_400_000);
}

/** "today", "yesterday", "5 days ago" */
export function relativeDays(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${int(days)} days ago`;
}

/** YYYY-MM-DD `days` before/after `iso`. */
export function addDays(iso: string, days: number): string {
  const t = toUtcDate(iso).getTime() + days * 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}
