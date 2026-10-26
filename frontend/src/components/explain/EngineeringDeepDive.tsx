import { useMemo, useState } from "react";
import { BadgeCheck, Ban, Braces, Calculator, Database, FileSearch, GitBranch, Radio, Scale, ShieldCheck } from "lucide-react";
import type { ResearchRunState, StageId } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { resolveToken } from "./EvidenceDrawer";

/*
 * The engineering story behind a run, written for a technical reader. Every claim here mirrors the backend:
 * backend/pipeline/graph.py (LangGraph topology), llm_client.py (Gemini structured output), engine/*.py
 * (Decimal metrics, reconciliation, safe_expr, verification_gate) and sources/*.py (fetchers and filters).
 */

type Actor = "LLM" | "CODE" | "HYBRID";

const LANES: { title: string; stages: { id: StageId; name: string; actor: Actor; does: string }[] }[] = [
  { title: "Resolve and plan", stages: [
    { id: "s01_entity_resolution", name: "Entity resolution", actor: "HYBRID",
      does: "Code searches SEC and BSE registries; the model only picks among returned candidates, and code rejects any ticker or CIK not in them." },
    { id: "s02_research_planning", name: "Research planning", actor: "HYBRID",
      does: "The model drafts 12 sub-questions; a code routing rule sends every numeric question to T1/T2 filings only." },
  ] },
  { title: "Filings lane", stages: [
    { id: "s03_filing_retrieval", name: "Filing retrieval", actor: "CODE",
      does: "SEC EDGAR XBRL companyfacts and BSE/NSE disclosures; aggregator hosts are rejected outright." },
    { id: "s04_statement_extraction", name: "Statement extraction", actor: "HYBRID",
      does: "PyMuPDF builds a table grid with bounding boxes; the model returns row/column coordinates only and code reads the cell." },
    { id: "s05_metrics_engine", name: "Metrics engine", actor: "CODE",
      does: "16 fixed formulas in Python Decimal (28-digit precision, ROUND_HALF_UP) build the calculation ledger." },
    { id: "s06_red_flag_engine", name: "Red-flag rules", actor: "CODE",
      does: "Declarative YAML thresholds run through a whitelisted AST evaluator with three-valued logic. eval() is never called." },
  ] },
  { title: "Signals lane", stages: [
    { id: "s07_news_intelligence", name: "News intelligence", actor: "HYBRID",
      does: "Code fetches Google News RSS and GDELT, de-duplicates and dates; the model clusters events and writes headlines." },
    { id: "s08_sentiment_filter", name: "Signal vs noise", actor: "HYBRID",
      does: "Code strips bots (account age, karma, 5-gram Jaccard ≥ 0.85 duplicates); the model labels signal only when a filing or news event corroborates it." },
    { id: "s09_market_peers", name: "Market and peers", actor: "HYBRID",
      does: "Peers run through the same Decimal engines; medians are computed in code and exposed to the model as citable tokens." },
  ] },
  { title: "Reason and audit", stages: [
    { id: "s10_contradiction_resolver", name: "Contradiction resolver", actor: "HYBRID",
      does: "The model extracts conflicting claims; code assigns the winner by source-tier weight, never the model." },
    { id: "s11_synthesis", name: "Synthesis", actor: "LLM",
      does: "The model writes insights that must quote ledger formatted_values followed by [[token]] citations; code builds the provenance DAG." },
    { id: "s12_verification_gate", name: "Verification gate", actor: "CODE",
      does: "Every numeric token in the prose is matched to its cited ledger row. Mismatches are cut and the claim is downgraded." },
  ] },
];

const STACK: { group: string; items: string[] }[] = [
  { group: "Orchestration", items: ["LangGraph StateGraph", "12 nodes, fan-out / fan-in", "asyncio", "Lock-ordered event bus"] },
  { group: "Model layer", items: ["Google Gemini (google-genai)", "JSON-schema structured output", "temperature 0", "Pydantic v2 validation", "2 retries, 1 s / 2 s backoff", "Token and cost ledger"] },
  { group: "Deterministic engines", items: ["Python Decimal (prec 28)", "AST-whitelisted rule evaluator", "YAML rule packs", "Reconciliation suite"] },
  { group: "Data sources", items: ["SEC EDGAR XBRL", "BSE / NSE disclosures", "PyMuPDF + pdfplumber", "GDELT + Google News RSS", "Reddit API, HN Algolia, Play Store"] },
  { group: "Serving and audit", items: ["FastAPI", "Server-Sent Events", "SQLite (WAL)", "Event-sourced replays (JSONL.gz)", "WeasyPrint / Markdown export"] },
  { group: "Quality", items: ["pytest + respx (no live network)", "Scripted LLM test double", "Vitest", "Playwright E2E", "ruff"] },
];

const GUARDS: { icon: typeof ShieldCheck; title: string; body: string }[] = [
  { icon: Calculator, title: "The model can't do arithmetic",
    body: "Prompts forbid computing or typing any number without a [[token]]. Every figure comes from the Decimal engine, quantised only in formatters.py." },
  { icon: FileSearch, title: "Coordinates, not values",
    body: "In extraction the model points to a table cell (row, column). Code reads the printed string, checks the row label matches column 0, parses it and normalises units to INR crore or USD million." },
  { icon: Scale, title: "Accounting identities in code",
    body: "Assets = liabilities + equity (±1%), operating-profit and net-profit roll-ups (±1% / ±2%). A failed identity retries the comparative column, then blanks the items and states a gap." },
  { icon: Braces, title: "Schema-locked outputs",
    body: "Every model call returns JSON against a Pydantic schema at temperature 0. Invalid JSON is retried twice, then the stage falls back or reports a warning. It never guesses." },
  { icon: Ban, title: "Stated gaps over estimates",
    body: "Missing data becomes an explicit stated gap. There is no aggregator fallback and no Q4-from-nine-months back-solving." },
  { icon: Radio, title: "Full audit trail",
    body: "Each stage emits running and terminal events with an explain panel, tool-call traces and a state patch. Runs are stored and can be replayed event by event." },
];

const RULES = [
  ["RF_01", "Receivables stretch", "(dso_t − dso_t−2 > 15) or (receivables_yoy − rev_yoy > 0.15)"],
  ["RF_02", "Low cash conversion", "pat_t > 0 and cfo_to_pat_t < 0.80"],
  ["RF_03", "Negative free cash flow", "fcf_t < 0"],
  ["RF_04", "Debt outgrowing revenue", "gross_debt_yoy − rev_yoy > 0.20"],
  ["RF_05", "Weak interest cover", "interest_cover_t < 2.5 or drop > 2.0"],
  ["RF_06", "Profit–cash divergence", "rev_yoy > 0.10 and cfo_yoy < −0.10"],
  ["RF_07", "Liquidity squeeze", "current_ratio_t < 1.10"],
  ["RF_08", "Margin compression", "ebitda_margin_chg_bps < −150"],
];

const CITED = /([+\-−]?[₹$]?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|bps|x|cr|crore|M|bn|d|days)?)\s*\[\[([a-z0-9_]+)\]\]/;

/** Find one cited figure in this run's prose to walk through the gate. */
export function findExample(state: ResearchRunState) {
  const texts = [state.summary?.verdict_prose, ...state.insights.map((i) => i.body), state.summary?.verdict_headline];
  for (const t of texts) {
    if (!t) continue;
    for (const sentence of t.split(/(?<=[.!?])\s+/)) {
      const m = CITED.exec(sentence);
      if (!m) continue;
      const resolved = resolveToken(state, m[2]);
      if (resolved && (resolved.metric || resolved.lineItem)) return { sentence, token: m[1].trim(), cite: m[2], resolved };
    }
  }
  return null;
}

function ActorTag({ actor }: { actor: Actor }) {
  const cls = actor === "CODE" ? "pill-mu" : actor === "LLM" ? "pill-ac" : "pill-hy";
  return <span className={`pill ${cls} !text-[11px] !font-semibold uppercase tracking-wide`}>{actor === "HYBRID" ? "LLM + code" : actor === "LLM" ? "GenAI" : "Code"}</span>;
}

function Highlight({ sentence, token, cite }: { sentence: string; token: string; cite: string }) {
  const i = sentence.indexOf(token);
  const j = sentence.indexOf(`[[${cite}]]`);
  if (i < 0 || j < 0) return <>{sentence}</>;
  return (
    <>
      {sentence.slice(0, i)}
      <mark className="rounded bg-ac2 px-1 text-ac">{token}</mark>
      {sentence.slice(i + token.length, j)}
      <code className="rounded bg-chip px-1">[[{cite}]]</code>
      {sentence.slice(j + cite.length + 4)}
    </>
  );
}

export default function EngineeringDeepDive() {
  const state = useRunStore((s) => s.projectedState);
  const [open, setOpen] = useState<StageId | null>(null);
  const example = useMemo(() => findExample(state), [state]);
  const v = state.verification;
  const llmCalls = state.events.reduce((n, e) => n + (e.explain_panel?.tool_calls.filter((t) => t.actor === "LLM").length ?? 0), 0);
  const codeCalls = state.events.reduce((n, e) => n + (e.explain_panel?.tool_calls.filter((t) => t.actor === "CODE").length ?? 0), 0);

  return (
    <div>
      <section className="mb-10">
        <div className="label">Engineering deep-dive</div>
        <h2 className="mt-2 !text-[30px] !font-bold !leading-[1.12] md:!text-[36px]">
          An agentic research pipeline where the model is never allowed to invent a number.
        </h2>
        <p className="mt-3 max-w-[72ch] text-[17px] leading-relaxed">
          Atlas splits the work along a hard line. The language model handles judgement: resolving entities, planning,
          reading tables, clustering news and writing prose. Deterministic Python owns every number, every accounting
          check and every decision about which source wins. A final verification gate audits the finished report against
          the calculation ledger before anything reaches the reader.
        </p>
      </section>

      <div className="kgrid">
        <div className="kp"><span>Pipeline</span><b>12</b><span>LangGraph nodes, 3 parallel lanes</span></div>
        <div className="kp"><span>Model calls</span><b>{llmCalls || "—"}</b><span>schema-locked, temperature 0</span></div>
        <div className="kp"><span>Code tool calls</span><b>{codeCalls || "—"}</b><span>fetch, parse, compute, check</span></div>
        <div className="kp"><span>Gate result</span><b>{v ? `${v.verified_against_ledger}/${v.total_numeric_tokens_scanned}` : "—"}</b>
          <span>{v ? `${v.unverified_tokens_blocked} blocked · ${v.claims_downgraded_to_hypothesis.length} downgraded` : "figures matched to ledger"}</span></div>
      </div>

      <section className="card" aria-labelledby="arch-h">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="arch-h" className="flex items-center gap-2"><GitBranch size={20} aria-hidden="true" />Agent graph</h2>
          <span className="sm mu">S1 → S2 → {"{"}filings ∥ news{"}"} → peers → S10 → S11 → S12</span>
        </div>
        <p className="sm mu mt-0">
          A LangGraph StateGraph with fan-out after planning and fan-in before reasoning. Each node streams a running and a
          terminal event over SSE; event order comes from one lock-protected counter, and recorded runs replay the same event stream in the same order.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          {LANES.map((lane) => (
            <div key={lane.title} className="lane">
              <div className="label mb-2">{lane.title}</div>
              {lane.stages.map((st) => (
                <button key={st.id} type="button" className="stage-node" aria-expanded={open === st.id}
                        onClick={() => setOpen(open === st.id ? null : st.id)}>
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-medium">{st.name}</span>
                    <ActorTag actor={st.actor} />
                  </span>
                  {open === st.id && <span className="mt-2 block text-left sm mu">{st.does}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
        <p className="sm mu mb-0 mt-3">Tap a node for what the model does and what code enforces.</p>
      </section>

      <section className="card" aria-labelledby="gate-h">
        <h2 id="gate-h" className="flex items-center gap-2"><BadgeCheck size={20} aria-hidden="true" />Figure verification, traced live</h2>
        <p className="sm mu mt-0">
          A real figure from this run, followed through the stage-12 gate. The gate is about 200 lines of regex and set
          lookups, with no model in the loop.
        </p>
        {example ? (
          <ol className="trace m-0 list-none p-0">
            <li>
              <span className="trace-n">1</span>
              <div><b>The model writes prose</b> and must put the ledger's exact formatted value, followed by a citation token.
                <blockquote className="quote"><Highlight {...example} /></blockquote></div>
            </li>
            <li>
              <span className="trace-n">2</span>
              <div><b>Tokenise.</b> Citations, fiscal labels (FY2024) and dates are masked, then a numeric regex extracts
                <code className="mx-1 rounded bg-chip px-1">{example.token}</code>. Bare years 1990–2035 and months 1–12 are exempt;
                anything with a sign, currency, comma, decimal or unit is checked.</div>
            </li>
            <li>
              <span className="trace-n">3</span>
              <div><b>Resolve the citation</b> <code className="rounded bg-chip px-1">[[{example.cite}]]</code> in the registry built from the
                ledger, line items, peer table and evidence snippets.
                {example.resolved.metric && (
                  <div className="mt-2 scroll-x"><table>
                    <tbody>
                      <tr><td>Ledger row</td><td>{example.resolved.metric.metric_label} · {example.resolved.metric.fiscal_period}</td></tr>
                      <tr><td>Formula</td><td><code>{example.resolved.metric.formula_template}</code></td></tr>
                      <tr><td>Substituted</td><td><code>{example.resolved.metric.formula_substituted}</code></td></tr>
                      <tr><td>Decimal value</td><td><code>{example.resolved.metric.value ?? "—"}</code></td></tr>
                      <tr><td>formatted_value</td><td><b>{example.resolved.metric.formatted_value}</b></td></tr>
                    </tbody>
                  </table></div>
                )}
                {example.resolved.lineItem && (
                  <div className="mt-2 scroll-x"><table>
                    <tbody>
                      <tr><td>Line item</td><td>{example.resolved.lineItem.reported_label} · {example.resolved.lineItem.fiscal_period}</td></tr>
                      <tr><td>Decimal value</td><td><code>{example.resolved.lineItem.value ?? "—"}</code> {example.resolved.lineItem.unit}</td></tr>
                      <tr><td>formatted_value</td><td><b>{example.resolved.lineItem.formatted_value}</b></td></tr>
                    </tbody>
                  </table></div>
                )}
              </div>
            </li>
            <li>
              <span className="trace-n">4</span>
              <div><b>Match.</b> The whitespace-normalised token must equal the row's formatted_value, or appear verbatim in
                the cited evidence snippet.
                {(() => {
                  const want = example.resolved.metric?.formatted_value ?? example.resolved.lineItem?.formatted_value ?? "";
                  const ok = want.replace(/\s+/g, " ").trim() === example.token.replace(/\s+/g, " ").trim();
                  return <span className={`pill ml-2 ${ok ? "pill-ps" : "pill-wn"}`}>{ok ? "match: passes" : "matched via evidence snippet"}</span>;
                })()}
              </div>
            </li>
            {example.resolved.evidence && (
              <li>
                <span className="trace-n">5</span>
                <div><b>Provenance.</b> {example.resolved.evidence.document_title}
                  {example.resolved.evidence.page_number ? `, page ${example.resolved.evidence.page_number}` : ""} ·
                  <span className="pill pill-mu ml-1">{example.resolved.evidence.source_tier} · weight {example.resolved.evidence.trust_weight.toFixed(2)}</span>
                  <blockquote className="quote">{example.resolved.evidence.verbatim_snippet}</blockquote>
                </div>
              </li>
            )}
          </ol>
        ) : (
          <p className="skeleton">The walkthrough appears once synthesis has written cited prose.</p>
        )}
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className="rule-box"><b>On a mismatch</b><span className="sm mu">The whole sentence is replaced with
            “[Stated Gap: Unverified numeric claim removed by Verification Gate]” and the blocked count goes up.</span></div>
          <div className="rule-box"><b>Claim downgrade</b><span className="sm mu">A fact or inference with no metric, line item,
            red flag or T1/T2 news support is reclassified as a hypothesis.</span></div>
        </div>
      </section>

      <section className="card" aria-labelledby="guard-h">
        <h2 id="guard-h" className="flex items-center gap-2"><ShieldCheck size={20} aria-hidden="true" />Anti-hallucination controls</h2>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          {GUARDS.map((g) => (
            <div key={g.title} className="flex gap-3">
              <span className="icon-tile" aria-hidden="true"><g.icon size={18} /></span>
              <div><b className="block">{g.title}</b><span className="sm mu">{g.body}</span></div>
            </div>
          ))}
        </div>
      </section>

      <section className="card" aria-labelledby="rules-h">
        <h2 id="rules-h">Forensic rule pack</h2>
        <p className="sm mu mt-0">
          <code>data/rules/default_red_flags.yaml</code>, parsed into an AST and checked against a node whitelist at load time
          (BoolOp, Compare, BinOp ±, UnaryOp, Name, Constant). A missing input makes its sub-expression MISSING
          rather than false, so a rule never fires on absent data.
        </p>
        <div className="scroll-x">
          <table>
            <thead><tr><th>Rule</th><th className="!text-left">Signal</th><th className="!text-left">Expression</th></tr></thead>
            <tbody>
              {RULES.map(([id, name, expr]) => (
                <tr key={id}><td>{id}</td><td className="!text-left">{name}</td><td className="!text-left"><code>{expr}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card" aria-labelledby="stack-h">
        <h2 id="stack-h" className="flex items-center gap-2"><Database size={20} aria-hidden="true" />Technical stack</h2>
        <div className="mt-3 grid gap-5 md:grid-cols-2">
          {STACK.map((s) => (
            <div key={s.group}>
              <div className="label mb-2">{s.group}</div>
              <div className="flex flex-wrap gap-1.5">{s.items.map((i) => <span key={i} className="pill">{i}</span>)}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
