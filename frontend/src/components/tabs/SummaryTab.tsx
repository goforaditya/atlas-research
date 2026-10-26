import type { ClaimClassification, MetricLedgerRow, ResearchRunState } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import ShowTheMathPopover from "../explain/ShowTheMathPopover";
import { Prose } from "../explain/EvidenceDrawer";
import { WaitingFor } from "../explain/TimelineScrubber";
import { currencyLabel } from "../../utils/formatters";

const CLAIM_STYLE: Record<ClaimClassification, { label: string; plural: string; cls: string }> = {
  fact: { label: "Fact", plural: "Facts", cls: "pill-ps" },
  inference: { label: "Inference", plural: "Inferences", cls: "" },
  hypothesis: { label: "Hypothesis", plural: "Hypotheses", cls: "pill-wn" },
};

export function ClaimBadge({ kind, count }: { kind: ClaimClassification; count?: number }) {
  const st = CLAIM_STYLE[kind];
  return <span className={`pill ${st.cls}`}>{count !== undefined ? `${count} ${count === 1 ? st.label : st.plural}` : st.label}</span>;
}

const KPI_KEYS: { key: string; label: string }[] = [
  { key: "rev_yoy", label: "Revenue growth" },
  { key: "ebitda_margin", label: "EBITDA margin" },
  { key: "cfo_to_pat", label: "CFO / PAT" },
  { key: "dso", label: "Receivable days" },
  { key: "roe", label: "Return on equity" },
  { key: "net_debt_to_ebitda", label: "Net debt / EBITDA" },
];

export function latestMetric(state: ResearchRunState, key: string): MetricLedgerRow | null {
  const fy = state.fiscal_years[state.fiscal_years.length - 1];
  return state.metrics_ledger.find((m) => m.metric_key === key && m.fiscal_period === fy)
    ?? [...state.metrics_ledger].reverse().find((m) => m.metric_key === key) ?? null;
}

function EntityHeader({ state }: { state: ResearchRunState }) {
  const e = state.entity;
  if (!e) return null;
  return (
    <div className="mb-3">
      <h1>{e.legal_name}</h1>
      <div className="sm mu">
        {[e.ticker && e.exchange ? `${e.exchange}: ${e.ticker}` : e.ticker, e.sector, e.headquarters,
          `FY ends ${e.fy_end_month}`, currencyLabel(e.currency)].filter(Boolean).join(" · ")}
      </div>
    </div>
  );
}

function KpiStrip({ state }: { state: ResearchRunState }) {
  const revenue = [...state.line_items].reverse().find((i) => i.field_key === "revenue" && !i.segment_name);
  const openEvidence = useRunStore((s) => s.openEvidence);
  return (
    <div className="kgrid" data-tour-id="kpi-strip">
      {revenue && (
        <div className="kp">
          <span>Revenue {revenue.fiscal_period}</span>
          <b><button type="button" className="num-link" onClick={() => openEvidence(revenue.item_id)}>{revenue.formatted_value}</button></b>
          <span>{revenue.evidence.document_title}{revenue.evidence.page_number ? ` p.${revenue.evidence.page_number}` : ""}</span>
        </div>
      )}
      {KPI_KEYS.map(({ key, label }) => {
        const m = latestMetric(state, key);
        if (!m) return null;
        return (
          <div className="kp" key={key}>
            <span>{label} {m.fiscal_period}</span>
            <b><ShowTheMathPopover row={m} /></b>
            <span>{m.status === "computed" ? m.primary_source_ref : m.status_note ?? "Not meaningful"}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function SummaryTab() {
  const state = useRunStore((s) => s.projectedState);
  const setTab = useRunStore((s) => s.setActiveTab);
  const sum = state.summary;
  const fired = state.red_flags.filter((f) => f.fired);
  const v = state.verification;

  return (
    <div>
      <WaitingFor stageId="s01_entity_resolution" stateKey="entity" ready={!!state.entity}>
        <EntityHeader state={state} />
      </WaitingFor>

      <WaitingFor stageId="s11_synthesis" stateKey="summary" ready={!!sum}>
        {sum && (
          <section className="card" aria-labelledby="verdict-h">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="text-micro text-mu">Verdict</div>
              <div className="flex flex-wrap gap-1" data-tour-id="verdict-badges" aria-label="Claim classification counts">
                <ClaimBadge kind="fact" count={sum.fact_count} />
                <ClaimBadge kind="inference" count={sum.inference_count} />
                <ClaimBadge kind="hypothesis" count={sum.hypothesis_count} />
              </div>
            </div>
            <h2 id="verdict-h" className="mt-1">{sum.verdict_headline}</h2>
            <p className="my-1"><Prose text={sum.verdict_prose} /></p>
            <div className="mt-2 flex flex-wrap gap-2 sm">
              {fired.length > 0 && (
                <button type="button" className="pill pill-ng cursor-pointer border-0" onClick={() => setTab("fin")}>
                  {fired.length} red flag{fired.length === 1 ? "" : "s"} fired
                </button>
              )}
              {state.contradictions.length > 0 && (
                <button type="button" className="pill pill-wn cursor-pointer border-0" onClick={() => setTab("met")}>
                  {state.contradictions.length} source conflict{state.contradictions.length === 1 ? "" : "s"} resolved
                </button>
              )}
              {v && (
                <span className={`pill ${v.passed ? "pill-ps" : "pill-ng"}`}>
                  Verification gate: {v.verified_against_ledger}/{v.total_numeric_tokens_scanned} figures matched the ledger
                </span>
              )}
            </div>
          </section>
        )}
      </WaitingFor>

      <WaitingFor stageId="s05_metrics_engine" stateKey="metrics_ledger" ready={state.metrics_ledger.length > 0}>
        <KpiStrip state={state} />
      </WaitingFor>

      {sum && (
        <>
          <div className="cgrid">
            <section className="card">
              <h3>Business</h3>
              <p className="sm my-1">{sum.business_overview}</p>
              <p className="sm my-1"><Prose text={sum.revenue_mix_summary} /></p>
              {sum.quarterly_direction_note && (
                <p className="sm my-1"><span className="mu">Latest quarter: </span><Prose text={sum.quarterly_direction_note} /></p>
              )}
            </section>
            <section className="card">
              <h3>Management</h3>
              <p className="sm my-1">{sum.management_summary}</p>
              {state.management_roster.length > 0 && (
                <ul className="sm my-1 list-none p-0">
                  {state.management_roster.map((p) => (
                    <li key={p.name} className="flex justify-between gap-2 border-b border-ln py-1">
                      <span>{p.name}</span>
                      <span className="mu">{p.role}{p.since ? ` · since ${p.since}` : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
              {sum.governance_watch_note && <div className="banner !mb-0 mt-2">Governance watch: {sum.governance_watch_note}</div>}
            </section>
          </div>
          <div className="cgrid">
            <section className="card">
              <h3>Opportunities</h3>
              <ul className="sm my-1 pl-5">{sum.opportunities.map((o, i) => <li key={i}><Prose text={o} /></li>)}</ul>
            </section>
            <section className="card">
              <h3>Risks</h3>
              <ul className="sm my-1 pl-5">{sum.risks.map((r, i) => <li key={i}><Prose text={r} /></li>)}</ul>
            </section>
          </div>
          {state.stated_gaps.length > 0 && (
            <section className="card">
              <h3>What we could not verify</h3>
              <ul className="sm my-1 pl-5">{state.stated_gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
