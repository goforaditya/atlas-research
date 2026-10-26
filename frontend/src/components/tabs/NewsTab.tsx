import type { NewsEvent, Polarity } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { Prose } from "../explain/EvidenceDrawer";
import { WaitingFor } from "../explain/TimelineScrubber";
import { tierTitle } from "../../utils/formatters";

const POLARITY: Record<Polarity, { dot: string; label: string }> = {
  positive: { dot: "bg-ps", label: "Positive" },
  neutral: { dot: "bg-mu", label: "Neutral" },
  negative: { dot: "bg-ng", label: "Negative" },
};

function EventRow({ e }: { e: NewsEvent }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const p = POLARITY[e.polarity];
  return (
    <li className="relative pb-4 pl-6">
      <span aria-hidden="true" className={`absolute left-0 top-[7px] h-3 w-3 rounded-full ring-2 ring-[var(--sf)] ${p.dot}`} />
      <div className="flex flex-wrap items-center gap-2 sm">
        <span className="mu">{e.event_date}</span>
        <span className={`pill ${e.category === "Controversy" || e.category === "Regulatory" ? "pill-ng" : "pill-mu"}`}>{e.category}</span>
        <span className="mu">{p.label}</span>
        {e.corroborated_by_filing
          ? <span className="pill pill-ps">Corroborated by filing</span>
          : <span className="pill pill-wn">Press only</span>}
      </div>
      <h3 className="mt-1 mb-0">{e.headline}</h3>
      <p className="sm my-1"><Prose text={e.summary} /></p>
      <div className="sm mu">
        {e.source_names.join(", ")} · highest tier {e.highest_tier} ({tierTitle(e.highest_tier)})
        {e.article_count_merged > 1 ? ` · ${e.article_count_merged} articles merged` : ""}
        {" · "}
        <button type="button" className="num-link" onClick={() => openEvidence(e.event_id)}>source</button>
      </div>
    </li>
  );
}

export default function NewsTab() {
  const state = useRunStore((s) => s.projectedState);
  const events = state.news_events;
  const counts = { positive: 0, neutral: 0, negative: 0 } as Record<Polarity, number>;
  events.forEach((e) => { counts[e.polarity] += 1; });
  return (
    <WaitingFor stageId="s07_news_intelligence" stateKey="news_events" ready={events.length > 0}>
      <div className="kgrid">
        <div className="kp"><span>Events after de-duplication</span><b>{events.length}</b></div>
        <div className="kp"><span>Articles merged</span><b>{events.reduce((a, e) => a + e.article_count_merged, 0)}</b></div>
        <div className="kp"><span>Corroborated by a filing</span><b>{events.filter((e) => e.corroborated_by_filing).length}</b></div>
        <div className="kp"><span>Positive / neutral / negative</span><b>{counts.positive} / {counts.neutral} / {counts.negative}</b></div>
      </div>
      <section className="card">
        <h2>Timeline</h2>
        <ol className="m-0 list-none border-l border-ln p-0 pl-0 ml-[5px]">
          {events.map((e) => <EventRow key={e.event_id} e={e} />)}
        </ol>
      </section>
    </WaitingFor>
  );
}
