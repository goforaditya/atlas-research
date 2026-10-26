import type { InsightCard } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { Prose } from "../explain/EvidenceDrawer";
import { WaitingFor } from "../explain/TimelineScrubber";
import InsightProvenanceGraph from "../explain/InsightProvenanceGraph";
import { ClaimBadge } from "./SummaryTab";

function supportCount(i: InsightCard): number {
  return i.supporting_metric_ids.length + i.supporting_item_ids.length + i.supporting_red_flag_ids.length
    + i.supporting_news_ids.length + i.supporting_sentiment_ids.length + i.supporting_contradiction_ids.length;
}

function InsightCardView({ ins, downgraded }: { ins: InsightCard; downgraded: boolean }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const explain = useRunStore((s) => s.explainMode);
  const links = [...ins.supporting_red_flag_ids, ...ins.supporting_news_ids, ...ins.supporting_sentiment_ids, ...ins.supporting_contradiction_ids];
  return (
    <article className="card !mb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span aria-label={`${ins.polarity} insight`} title={`${ins.polarity} insight`}
              className={`inline-block h-2 w-2 rounded-full ${ins.polarity === "negative" ? "bg-ng" : ins.polarity === "positive" ? "bg-ps" : "bg-mu"}`} />
        <ClaimBadge kind={ins.classification} />
        {downgraded && <span className="pill pill-wn" title="The verification gate found a figure it could not match">downgraded by the gate</span>}
        <span className="sm mu">{supportCount(ins)} supporting items</span>
      </div>
      <h3 className="mt-2">{ins.title}</h3>
      <p className="sm my-1"><Prose text={ins.body} /></p>
      {explain && links.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {links.map((id) => <button key={id} type="button" className="cite" onClick={() => openEvidence(id)}>{id}</button>)}
        </div>
      )}
    </article>
  );
}

export default function InsightsTab() {
  const state = useRunStore((s) => s.projectedState);
  const explain = useRunStore((s) => s.explainMode);
  const downgraded = new Set(state.verification?.claims_downgraded_to_hypothesis ?? []);
  return (
    <WaitingFor stageId="s11_synthesis" stateKey="insights" ready={state.insights.length > 0}>
      {explain && <InsightProvenanceGraph />}
      <div className="cgrid mb-3">
        {state.insights.map((ins) => <InsightCardView key={ins.insight_id} ins={ins} downgraded={downgraded.has(ins.insight_id)} />)}
      </div>
      {state.summary && state.summary.analyst_follow_up_notes.length > 0 && (
        <section className="card">
          <h3>Analyst follow-up</h3>
          <ul className="sm my-1 pl-5">{state.summary.analyst_follow_up_notes.map((n, i) => <li key={i}><Prose text={n} /></li>)}</ul>
        </section>
      )}
    </WaitingFor>
  );
}
