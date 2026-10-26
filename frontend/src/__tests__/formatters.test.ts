import { describe, expect, it } from "vitest";
import { clock, duration, geometry, groupDecimalString, showFormatted, splitProse } from "../utils/formatters";
import { symbolicFormula, substitutedWithResult } from "../components/explain/ShowTheMathPopover";
import type { MetricLedgerRow } from "../types/atlas";
import { projectStateAt } from "../utils/stateProjector";
import { loadSyntheticEvents } from "./fixture";

describe("display helpers never re-round DecimalString values", () => {
  it("groups digits without touching the fraction", () => {
    expect(groupDecimalString("3810.00")).toBe("3,810.00");
    expect(groupDecimalString("1234567.8999999")).toBe("1,234,567.8999999");
    expect(groupDecimalString("-0.125")).toBe("-0.125");
    expect(groupDecimalString("12")).toBe("12");
    expect(groupDecimalString(null)).toBe("—");
  });

  it("shows backend formatted strings verbatim", () => {
    expect(showFormatted("84.3 days")).toBe("84.3 days");
    expect(showFormatted("NM (negative base)")).toBe("NM (negative base)");
    expect(showFormatted("")).toBe("—");
    expect(showFormatted(undefined)).toBe("—");
  });

  it("every metric formatted_value in the fixture appears unchanged in the popover text", () => {
    const state = projectStateAt(loadSyntheticEvents(), 23);
    for (const row of [...state.metrics_ledger, ...state.quarterly_metrics]) {
      expect(substitutedWithResult(row).endsWith(`= ${row.formatted_value}`)).toBe(true);
    }
  });

  it("geometry is only a number for charts", () => {
    expect(geometry("3810.00")).toBe(3810);
    expect(geometry(null)).toBeNull();
    expect(geometry("abc")).toBeNull();
  });
});

describe("time helpers", () => {
  it("clock and duration", () => {
    expect(clock(102_000)).toBe("01:42");
    expect(clock(-5)).toBe("00:00");
    expect(duration(420)).toBe("420ms");
    expect(duration(14_200)).toBe("14.2s");
    expect(duration(null)).toBe("");
  });
});

describe("splitProse", () => {
  it("pairs a number with its token and keeps plain text", () => {
    const segs = splitProse("Revenue grew 17.6% [[met_rev_yoy_fy26]] to ₹3,810 cr [[li_fy26_revenue]].");
    expect(segs).toEqual([
      { kind: "text", text: "Revenue grew " },
      { kind: "cited", value: "17.6%", token: "met_rev_yoy_fy26" },
      { kind: "text", text: " to " },
      { kind: "cited", value: "₹3,810 cr", token: "li_fy26_revenue" },
      { kind: "text", text: "." },
    ]);
  });

  it("a bare token becomes a citation chip with no value", () => {
    expect(splitProse("CFO resigned [[ev_cfo_exit]]")).toEqual([
      { kind: "text", text: "CFO resigned " },
      { kind: "cited", value: null, token: "ev_cfo_exit" },
    ]);
  });

  it("handles empty input", () => {
    expect(splitProse(null)).toEqual([]);
  });
});

describe("symbolicFormula", () => {
  const row = (template: string, periods: string[], latest: string): MetricLedgerRow => ({
    metric_id: "m", metric_key: "k", metric_label: "L", fiscal_period: latest, formula_template: template,
    formula_substituted: "880 / 3,810 * 365", inputs: periods.map((p, i) => ({
      symbol: `S${i}`, line_item_id: `li_${i}`, fiscal_period: p, value: "1", evidence_id: "e",
    })), value: "84.3", formatted_value: "84.3 days", unit: "days", status: "computed", primary_source_ref: "AR FY26 p.116",
  });

  it("substitutes the row's own periods", () => {
    expect(symbolicFormula(row("Receivables_t / Revenue_t * 365", ["FY26"], "FY26"))).toBe("Receivables_FY26 / Revenue_FY26 × 365");
    expect(symbolicFormula(row("Revenue_t / Revenue_t_minus_1 - 1", ["FY25", "FY26"], "FY26"))).toBe("Revenue_FY26 / Revenue_FY25 - 1");
  });

  it("formats the substituted line with the backend result", () => {
    expect(substitutedWithResult(row("x", [], "FY26"))).toBe("880 / 3,810 × 365 = 84.3 days");
  });
});
