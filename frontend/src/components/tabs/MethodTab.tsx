import { useMemo } from "react";
import type { StageEvent } from "../../types/atlas";
import { STAGE_LABELS } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import ContradictionResolver from "../explain/ContradictionResolver";
import EngineeringDeepDive from "../explain/EngineeringDeepDive";
import { ActorPill } from "../explain/PipelineStageTracker";
import { WaitingFor } from "../explain/TimelineScrubber";

const TIERS = [
  { tier: "T1", weight: "1.00", use: "Source of truth for all numbers: audited statements, regulator orders." },
  { tier: "T2", weight: "0.85", use: "Context and corporate actions: exchange disclosures, investor presentations." },
  { tier: "T3", weight: "0.65", use: "Events only; numeric claims need a T1/T2 match. Tier-1 business press." },
  { tier: "T4", weight: "0.30", use: "Themes only; must be corroborated before it counts as signal. Reddit, HN, app reviews." },
];

const GUARDRAILS = [
  "The language model never produces or computes a financial number; every figure comes from the Decimal engine.",
  "Every number in the prose must match the calculation ledger, or the verification gate blocks it and downgrades the claim.",
  "Statement figures come only from T1/T2 filings; there is no aggregator fallback.",
  "Accounting identities are checked in code; un-reconciled figures are discarded, not smoothed.",
  "A missing source becomes a stated gap; the model is never asked to estimate.",
  "No scraping behind login walls (LinkedIn, Glassdoor, X).",
];

export function codeVsLlm(events: StageEvent[]) {
  return events.filter((e) => e.explain_panel).map((e) => {
    const p = e.explain_panel!;
    return {
      stage: e.stage_id,
      code: p.actions.filter((a) => a.actor === "CODE").length + p.tool_calls.filter((t) => t.actor === "CODE").length,
      llm: p.actions.filter((a) => a.actor === "LLM").length + p.tool_calls.filter((t) => t.actor === "LLM").length,
    };
  });
}

export default function MethodTab() {
  const state = useRunStore((s) => s.projectedState);
  const explain = useRunStore((s) => s.explainMode);
  const split = useMemo(() => codeVsLlm(state.events), [state.events]);
  const v = state.verification;

  return (
    <div>
      <EngineeringDeepDive />
      <h2 className="mb-4 mt-10">This run</h2>
      {state.entity && (
        <section className="card">
          <h2>Entity and plan</h2>
          <p className="sm my-1">
            Resolved <b>{state.entity.query}</b> to <b>{state.entity.legal_name}</b>
            {state.entity.ticker ? ` (${state.entity.exchange}: ${state.entity.ticker})` : ""}.
          </p>
          {state.entity.rejected_candidates.length > 0 && (
            <p className="sm my-1 mu">Rejected: {state.entity.rejected_candidates.join("; ")}</p>
          )}
          {state.sub_questions.length > 0 && (
            <ol className="sm my-1 pl-5 list-decimal">{state.sub_questions.map((q, i) => <li key={i}>{q}</li>)}</ol>
          )}
        </section>
      )}

      <section className="card">
        <h2>Source trust</h2>
        <div className="scroll-x">
          <table>
            <thead><tr><th>Tier</th><th>Weight</th><th className="!text-left">Use</th></tr></thead>
            <tbody>
              {TIERS.map((t) => (
                <tr key={t.tier}><td>{t.tier}</td><td>{t.weight}</td><td className="!text-left !whitespace-normal">{t.use}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        {state.sources_read_count > 0 && <p className="sm mu mb-0">{state.sources_read_count} sources read in this run.</p>}
      </section>

      <WaitingFor stageId="s10_contradiction_resolver" stateKey="contradictions"
                  ready={state.events.some((e) => e.stage_id === "s10_contradiction_resolver" && e.stage_status !== "running")}>
        <ContradictionResolver records={state.contradictions} />
      </WaitingFor>

      {explain && split.length > 0 && (
        <section className="card">
          <h2>Model vs. code</h2>
          <div className="scroll-x">
            <table>
              <thead><tr><th>Stage</th><th><ActorPill actor="CODE" /></th><th><ActorPill actor="LLM" /></th></tr></thead>
              <tbody>
                {split.map((r) => (
                  <tr key={r.stage}><td>{STAGE_LABELS[r.stage].num} {STAGE_LABELS[r.stage].title}</td><td>{r.code}</td><td>{r.llm}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <h2>Guardrails</h2>
        <ul className="sm my-1 pl-5">{GUARDRAILS.map((g, i) => <li key={i}>{g}</li>)}</ul>
        {v && (
          <p className={`sm mb-0 ${v.passed ? "up" : "dn"}`}>
            Verification gate: scanned {v.total_numeric_tokens_scanned} figures, {v.verified_against_ledger} matched the ledger,
            {" "}{v.unverified_tokens_blocked} blocked{v.claims_downgraded_to_hypothesis.length ? `, ${v.claims_downgraded_to_hypothesis.length} claims downgraded` : ""}.
          </p>
        )}
      </section>

      {state.stated_gaps.length > 0 && (
        <section className="card">
          <h2>Stated gaps</h2>
          <ul className="sm my-1 pl-5">{state.stated_gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
