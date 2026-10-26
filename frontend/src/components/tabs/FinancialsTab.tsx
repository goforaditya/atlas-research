import { useMemo } from "react";
import type { LineItem, MetricLedgerRow, ResearchRunState, StandardFieldKey } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import RedFlagRulesTable from "../explain/RedFlagRulesTable";
import ShowTheMathPopover from "../explain/ShowTheMathPopover";
import { WaitingFor } from "../explain/TimelineScrubber";
import SvgBarChart from "../charts/SvgBarChart";
import SvgLineChart from "../charts/SvgLineChart";
import { currencyLabel, groupDecimalString } from "../../utils/formatters";

const FIELD_ORDER: StandardFieldKey[] = [
  "revenue", "operating_income", "other_income", "d_and_a", "finance_cost", "interest_expense", "ebitda", "ebit", "pbt",
  "tax_expense", "pat_before_nci", "pat", "cfo", "purchase_of_ppe", "purchase_of_intangibles", "capex", "total_assets",
  "total_liabilities", "total_equity", "non_controlling_interest", "current_assets", "current_liabilities",
  "trade_receivables", "cash_and_equivalents", "borrowings_current", "borrowings_noncurrent", "gross_debt", "segment_revenue",
];

const DERIVED_LABEL: Partial<Record<StandardFieldKey, string>> = {
  ebit: "EBIT", ebitda: "EBITDA", interest_expense: "Interest expense", gross_debt: "Gross debt", capex: "Capex",
};

/** Rows of a period x field grid in statement order; derived rows carry their formula as a tooltip. */
export function pivotLineItems(items: LineItem[]) {
  const periods: string[] = [];
  const rows = new Map<string, { label: string; formula: string | null; field: StandardFieldKey; derived: boolean; cells: Record<string, LineItem> }>();
  for (const li of items) {
    if (!periods.includes(li.fiscal_period)) periods.push(li.fiscal_period);
    const key = li.segment_name ? `${li.field_key}:${li.segment_name}` : li.field_key;
    const label = li.segment_name ? `Segment: ${li.segment_name}` : li.is_derived ? DERIVED_LABEL[li.field_key] ?? li.reported_label : li.reported_label;
    const row = rows.get(key) ?? { label, formula: li.is_derived ? li.reported_label : null, field: li.field_key, derived: li.is_derived, cells: {} as Record<string, LineItem> };
    row.cells[li.fiscal_period] = li;
    rows.set(key, row);
  }
  const rank = (f: StandardFieldKey) => { const i = FIELD_ORDER.indexOf(f); return i === -1 ? FIELD_ORDER.length : i; };
  const sorted = [...rows.entries()].map(([key, r]) => ({ key, ...r })).sort((a, b) => rank(a.field) - rank(b.field));
  return { periods, rows: sorted };
}

export function pivotMetrics(rows: MetricLedgerRow[], periods: string[]) {
  const byKey = new Map<string, { label: string; cells: Record<string, MetricLedgerRow> }>();
  for (const m of rows) {
    const label = m.metric_label.replace(new RegExp(`\\s*${m.fiscal_period}$`), "");
    const r = byKey.get(m.metric_key) ?? { label, cells: {} };
    r.cells[m.fiscal_period] = m;
    byKey.set(m.metric_key, r);
  }
  const cols = periods.filter((p) => rows.some((m) => m.fiscal_period === p));
  return { cols, rows: [...byKey.entries()].map(([key, r]) => ({ key, ...r })) };
}

function ItemCell({ li }: { li?: LineItem }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  if (!li) return <td className="mu">—</td>;
  return (
    <td>
      <button type="button" className="num-link" onClick={() => openEvidence(li.item_id)}
              title={`${li.evidence.document_title}${li.evidence.page_number ? ` p.${li.evidence.page_number}` : ""}`}>
        {li.formatted_value}
      </button>
      {li.is_restated && <sup className="text-wn" title="Restated">R</sup>}
    </td>
  );
}

function Charts({ state }: { state: ResearchRunState }) {
  const metric = (key: string, fy: string) => state.metrics_ledger.find((m) => m.metric_key === key && m.fiscal_period === fy);
  const revenue = state.line_items.filter((li) => li.field_key === "revenue" && !li.segment_name);
  const qRev = state.quarterly_items.filter((li) => li.field_key === "revenue");
  const marginSeries = (key: string, name: string, color: string) => ({
    name, color,
    points: state.fiscal_years.map((fy) => {
      const m = metric(key, fy);
      return { label: fy, value: m?.value ?? null, formatted: m?.formatted_value ?? "—", token: m?.metric_id };
    }),
  });
  const cfoPat = state.fiscal_years.map((fy) => metric("cfo_to_pat", fy)).filter((m): m is MetricLedgerRow => !!m);
  return (
    <div className="mb-4 grid gap-4 md:grid-cols-2">
      <div className="card !mb-0">
        <SvgBarChart title={`Revenue (${state.entity ? currencyLabel(state.entity.currency) : ""})`}
                     data={revenue.map((li) => ({ label: li.fiscal_period, value: li.value, formatted: li.formatted_value, token: li.item_id }))} />
      </div>
      <div className="card !mb-0">
        <SvgLineChart title="Margins" series={[marginSeries("ebitda_margin", "EBITDA", "var(--c1)"), marginSeries("pat_margin", "PAT", "var(--c2)")]} />
      </div>
      {cfoPat.length > 0 && (
        <div className="card !mb-0">
          <SvgBarChart title="Cash conversion (CFO / PAT)"
                       data={cfoPat.map((m) => ({ label: m.fiscal_period, value: m.value, formatted: m.formatted_value, token: m.metric_id }))} />
        </div>
      )}
      {qRev.length > 0 && (
        <div className="card !mb-0">
          <SvgBarChart title="Quarterly revenue"
                       data={qRev.map((li) => ({ label: li.fiscal_period, value: li.value, formatted: li.formatted_value, token: li.item_id }))} />
        </div>
      )}
    </div>
  );
}

export default function FinancialsTab() {
  const state = useRunStore((s) => s.projectedState);
  const statements = useMemo(() => pivotLineItems(state.line_items), [state.line_items]);
  const quarters = useMemo(() => pivotLineItems(state.quarterly_items), [state.quarterly_items]);
  const ledger = useMemo(() => pivotMetrics(state.metrics_ledger, state.fiscal_years), [state.metrics_ledger, state.fiscal_years]);
  const explain = useRunStore((s) => s.explainMode);
  const passed = state.reconciliation_checks.filter((c) => c.status === "passed").length;
  const failed = state.reconciliation_checks.filter((c) => c.status === "failed").length;

  return (
    <div>
      <WaitingFor stageId="s06_red_flag_engine" stateKey="red_flags" ready={state.red_flags.length > 0}>
        <RedFlagRulesTable flags={state.red_flags} />
      </WaitingFor>

      <WaitingFor stageId="s05_metrics_engine" stateKey="metrics_ledger" ready={state.metrics_ledger.length > 0}>
        <Charts state={state} />
        <section className="card" data-tour-id="calculation-ledger" aria-labelledby="ledger-h">
          <h2 id="ledger-h">Calculation ledger</h2>
          <p className="sm mu mt-0">{explain
            ? "Every ratio is computed in Python Decimal from the cited line items. Hover a value for its formula; click it for the source."
            : "Every ratio is computed from the filed statements. Tap a figure to see how."}</p>
          <div className="scroll-x">
            <table>
              <thead><tr><th>Metric</th>{ledger.cols.map((p) => <th key={p}>{p}</th>)}</tr></thead>
              <tbody>
                {ledger.rows.map((r) => (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    {ledger.cols.map((p) => (
                      <td key={p}>{r.cells[p] ? <ShowTheMathPopover row={r.cells[p]} /> : <span className="mu">—</span>}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {state.quarterly_metrics.length > 0 && (
            <ul className="sm mt-2 list-none p-0 flex flex-wrap gap-3">
              {state.quarterly_metrics.map((m) => (
                <li key={m.metric_id}><span className="mu">{m.metric_label}: </span><ShowTheMathPopover row={m} /></li>
              ))}
            </ul>
          )}
        </section>
      </WaitingFor>

      <WaitingFor stageId="s04_statement_extraction" stateKey="line_items" ready={state.line_items.length > 0}>
        <section className="card" aria-labelledby="stmt-h">
          <h2 id="stmt-h">Statements</h2>
          <p className="sm mu mt-0">
            {state.entity ? `${currencyLabel(state.entity.currency)}. ` : ""}Consolidated figures as printed; rows marked derived are computed in code from the rows above them.
          </p>
          <div className="scroll-x">
            <table>
              <thead><tr><th>Line item</th>{statements.periods.map((p) => <th key={p}>{p}</th>)}</tr></thead>
              <tbody>
                {statements.rows.map((r) => (
                  <tr key={r.key}>
                    <td title={r.formula ?? undefined}>{r.label}{r.derived && <span className="pill pill-mu ml-2">derived</span>}</td>
                    {statements.periods.map((p) => <ItemCell key={p} li={r.cells[p]} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {quarters.rows.length > 0 && (
          <section className="card" aria-labelledby="q-h">
            <h2 id="q-h">Quarterly</h2>
            <div className="scroll-x">
              <table>
                <thead><tr><th>Line item</th>{quarters.periods.map((p) => <th key={p}>{p}</th>)}</tr></thead>
                <tbody>
                  {quarters.rows.map((r) => (
                    <tr key={r.key}>
                      <td title={r.formula ?? undefined}>{r.label}{r.derived && <span className="pill pill-mu ml-2">derived</span>}</td>
                      {quarters.periods.map((p) => <ItemCell key={p} li={r.cells[p]} />)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <details className="card" aria-labelledby="rc-h" open={explain}>
          <summary className="flex flex-wrap items-baseline gap-x-3">
            <h2 id="rc-h" className="inline !mb-0">Reconciliation checks</h2>
            <span className="sm mu">{passed} passed{failed ? `, ${failed} failed` : ""}</span>
          </summary>
          <div className="scroll-x mt-3">
            <table>
              <thead><tr><th>Check</th><th>Period</th><th>LHS</th><th>RHS</th><th>Gap</th><th>Status</th></tr></thead>
              <tbody>
                {state.reconciliation_checks.map((c) => (
                  <tr key={c.check_id}>
                    <td title={c.formula_desc}>{c.check_name}</td>
                    <td>{c.fiscal_period}</td>
                    <td>{groupDecimalString(c.lhs_value)}</td>
                    <td>{groupDecimalString(c.rhs_value)}</td>
                    <td>{groupDecimalString(c.discrepancy)}</td>
                    <td>
                      <span className={`pill ${c.status === "passed" ? "pill-ps" : c.status === "failed" ? "pill-ng" : "pill-mu"}`}
                            title={c.failure_reason ?? c.skip_reason ?? undefined}>{c.status}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </WaitingFor>
    </div>
  );
}
