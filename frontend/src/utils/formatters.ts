/** Display helpers. Backend `formatted_value` strings are shown verbatim: nothing here re-rounds a
 *  DecimalString. Number() is used only for chart geometry (Section 3.2). */
import type { CurrencyUnit, DecimalString, SourceTier } from "../types/atlas";

export const MISSING = "—";

/** Show a backend-formatted string unchanged; only fills the missing marker. */
export function showFormatted(value: string | null | undefined): string {
  return value === null || value === undefined || value === "" ? MISSING : value;
}

/** Group the integer part of a DecimalString for display without changing any digit: "3810.00" -> "3,810.00". */
export function groupDecimalString(value: DecimalString | null | undefined): string {
  if (value === null || value === undefined || value === "") return MISSING;
  const neg = value.startsWith("-");
  const body = neg ? value.slice(1) : value;
  const [whole, frac] = body.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}${grouped}${frac !== undefined ? "." + frac : ""}`;
}

/** Chart geometry only. Never format the result for display. */
export function geometry(value: DecimalString | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function currencyLabel(unit: CurrencyUnit): string {
  return unit === "INR_CR" ? "₹ crore" : "$ million";
}

export function trustWeight(w: number): string {
  return w.toFixed(2);
}

export function tierTitle(tier: SourceTier): string {
  return {
    T1: "Regulator / audited filing",
    T2: "Exchange disclosure / company",
    T3: "Tier-1 press",
    T4: "Social / reviews / broker",
  }[tier];
}

/** "01:42" from milliseconds. */
export function clock(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** "420ms" or "14.2s" for stage durations. */
export function duration(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export type ProseSegment = { kind: "text"; text: string } | { kind: "cited"; value: string | null; token: string };

const CITED = /((?:[+\-−]?(?:[₹$]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|bps|x|cr|crore|M|bn|d|days))?)\s*)?\[\[([a-z0-9_]+)\]\]/g;

/** Split prose into plain text and "number + [[token]]" segments so numbers can open the Evidence Drawer. */
export function splitProse(text: string | null | undefined): ProseSegment[] {
  if (!text) return [];
  const out: ProseSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(CITED)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ kind: "text", text: text.slice(last, start) });
    out.push({ kind: "cited", value: m[1] ? m[1].trimEnd() : null, token: m[2] });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}
