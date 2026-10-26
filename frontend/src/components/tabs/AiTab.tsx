import { useMemo } from "react";
import type { StageId } from "../../types/atlas";
import { STAGE_ORDER } from "../../types/atlas";
import { useRunStore, type TabId } from "../../store/useRunStore";

const STAGE_PLAIN: Record<StageId, string> = {
  s01_entity_resolution: "Identified the company", s02_research_planning: "Planned the research",
  s03_filing_retrieval: "Found the filings", s04_statement_extraction: "Read the statements",
  s05_metrics_engine: "Computed the metrics", s06_red_flag_engine: "Checked for red flags",
  s07_news_intelligence: "Read the news", s08_sentiment_filter: "Read the sentiment",
  s09_market_peers: "Chose and compared peers", s10_contradiction_resolver: "Settled conflicting sources",
  s11_synthesis: "Wrote the report", s12_verification_gate: "Checked every number",
};

/** The GenAI story for this run: what the model did, what code guaranteed, and what Atlas raised on its own. */
export default function AiTab() {
  const state = useRunStore((s) => s.projectedState);
  const setTab = useRunStore((s) => s.setActiveTab);
  const v = state.verification;

  const stages = useMemo(() => {
    const byStage = new Map<StageId, { llm: string[]; code: number; llmCalls: number }>();
    for (const e of state.events) {
      const p = e.explain_panel;
      if (!p) continue;
      byStage.set(e.stage_id, {
        llm: p.actions.filter((a) => a.actor === "LLM").map((a) => a.action_summary),
        code: p.actions.filter((a) => a.actor === "CODE").length + p.tool_calls.filter((t) => t.actor === "CODE").length,
        llmCalls: p.tool_calls.filter((t) => t.actor === "LLM").length,
      });
    }
    return STAGE_ORDER.filter((id) => byStage.has(id)).map((id) => ({ id, ...byStage.get(id)! }));
  }, [state.events]);

  if (stages.length === 0) {
    return <div className="skeleton">This view fills in as the run progresses.</div>;
  }

  const aiSteps = stages.reduce((n, s) => n + s.llm.length, 0);
  const codeSteps = stages.reduce((n, s) => n + s.code, 0);
  const fired = state.red_flags.filter((f) => f.fired);
  const sen = state.sentiment;
  const noise = sen ? sen.items.filter((i) => i.verdict === "noise").length + sen.bots_and_duplicates_removed : 0;
  const signals = sen ? sen.items.filter((i) => i.verdict === "signal").length : 0;
  const corroborated = state.news_events.filter((e) => e.corroborated_by_filing).length;
  const downgraded = v?.claims_downgraded_to_hypothesis.length ?? 0;

  const proactive: { n: number; label: string; detail: string; tab: TabId }[] = [
    { n: fired.length, label: fired.length === 1 ? "red flag raised" : "red flags raised", tab: "fin",
      detail: fired.length ? fired.map((f) => f.rule_name).join(" · ") : "No accounting rule fired." },
    { n: state.contradictions.length, label: state.contradictions.length === 1 ? "source conflict settled" : "source conflicts settled", tab: "met",
      detail: state.contradictions.length ? state.contradictions.map((c) => c.topic).join(" · ") : "Sources agreed." },
    { n: noise, label: "noisy posts filtered out", tab: "sen",
      detail: sen ? `${signals} signal${signals === 1 ? "" : "s"} kept only where a filing or news event backs them up.` : "No social data in this run." },
    { n: corroborated, label: corroborated === 1 ? "news event confirmed by a filing" : "news events confirmed by a filing", tab: "news",
      detail: `${state.news_events.length - corroborated} press-only event${state.news_events.length - corroborated === 1 ? "" : "s"} kept at lower trust.` },
    { n: downgraded, label: downgraded === 1 ? "claim downgraded" : "claims downgraded", tab: "ins",
      detail: downgraded ? "A figure could not be matched, so the claim is shown as a hypothesis." : "Every claim's figures matched." },
    { n: state.stated_gaps.length, label: state.stated_gaps.length === 1 ? "gap stated openly" : "gaps stated openly", tab: "met",
      detail: "Missing data is said out loud, never estimated." },
  ];

  return (
    <div>
      <section className="mb-10">
        <div className="label">How Atlas thinks</div>
        <h2 className="mt-2 !text-[30px] !font-extrabold !leading-[1.1] text-[var(--hl)] md:!text-[38px]">
          GenAI does the reading and reasoning. Code owns every number.
        </h2>
        <p className="mt-3 max-w-[68ch] text-[17px] leading-relaxed">
          Twelve steps ran for {state.entity?.legal_name ?? "this company"}. The language model chose sources, read filings,
          classified news and wrote the report. Python computed every figure, and a final gate checked each number in the text
          against the ledger before it was shown.
        </p>
      </section>

      <div className="kgrid">
        <div className="kp"><span>GenAI decisions</span><b>{aiSteps}</b><span>reading, judging and writing</span></div>
        <div className="kp"><span>Code steps</span><b>{codeSteps}</b><span>fetching, computing and checking</span></div>
        <div className="kp"><span>Figures checked</span><b>{v ? `${v.verified_against_ledger}/${v.total_numeric_tokens_scanned}` : "—"}</b>
          <span>{v ? `${v.unverified_tokens_blocked} blocked` : "after the final gate"}</span></div>
        <div className="kp"><span>Numbers written by AI</span><b>0</b><span>by design</span></div>
      </div>

      <section className="card" aria-labelledby="pro-h">
        <h2 id="pro-h">Proactive intelligence</h2>
        <p className="sm mu mt-0">What Atlas raised on its own, without being asked.</p>
        <ul className="m-0 list-none p-0">
          {proactive.map((p) => (
            <li key={p.label} className="border-b border-ln py-3 last:border-b-0">
              <button type="button" className="flex w-full cursor-pointer items-baseline gap-4 border-0 bg-transparent p-0 text-left"
                      onClick={() => setTab(p.tab)}>
                <span className="w-12 shrink-0 text-right text-[28px] font-light leading-none tracking-tight">{p.n}</span>
                <span className="min-w-0">
                  <span className="block font-medium">{p.label}</span>
                  <span className="block sm mu">{p.detail}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card" aria-labelledby="genai-h">
        <h2 id="genai-h">Where GenAI did the work</h2>
        <p className="sm mu mt-0">Each step of this run, with what the model decided. Turn on Show workings to inspect every call.</p>
        <ol className="m-0 list-none p-0">
          {stages.map((st, i) => (
            <li key={st.id} className="flex gap-4 border-b border-ln py-3 last:border-b-0">
              <span className="w-6 shrink-0 pt-0.5 text-right sm mu">{i + 1}</span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{STAGE_PLAIN[st.id]}</span>
                  {st.llm.length > 0
                    ? <span className="pill !bg-ac2 !text-ac">GenAI</span>
                    : <span className="pill pill-mu">Code only</span>}
                </div>
                {st.llm.length > 0 && <ul className="mb-0 mt-1 pl-5 sm">{st.llm.map((a, j) => <li key={j}>{a}</li>)}</ul>}
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
