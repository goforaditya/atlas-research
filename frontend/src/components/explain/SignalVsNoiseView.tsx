import { useState } from "react";
import type { SentimentAggregate, SentimentItem } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";

type Filter = "all" | "signal" | "noise";

function PostCard({ p }: { p: SentimentItem }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const tone = p.polarity === "positive" ? "up" : p.polarity === "negative" ? "dn" : "mu";
  return (
    <article className="card !mb-0">
      <div className="flex flex-wrap items-center gap-2 sm">
        <span className={`pill ${p.verdict === "signal" ? "pill-ps" : "pill-wn"}`}>{p.verdict.toUpperCase()}</span>
        <span className="pill pill-mu">{p.audience}</span>
        <span className="mu">{p.platform}</span>
        <span className={tone}>{p.polarity}</span>
      </div>
      <blockquote className="my-2 border-l-2 border-ln pl-3">“{p.quote_text}”</blockquote>
      <ul className="sm my-1 pl-5">{p.verdict_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      <div className="flex flex-wrap items-center gap-1 sm">
        {p.corroborating_ids.length > 0 && <span className="mu">Corroborated by</span>}
        {p.corroborating_ids.map((id) => (
          <button key={id} type="button" className="cite" onClick={() => openEvidence(id)}>{id}</button>
        ))}
        <button type="button" className="num-link ml-auto mu" onClick={() => openEvidence(p.post_id)}>source</button>
      </div>
    </article>
  );
}

/** B.7: filter funnel, then post cards with verdict reasons and corroboration links. Clean mode hides noise. */
export default function SignalVsNoiseView({ sentiment }: { sentiment: SentimentAggregate }) {
  const explain = useRunStore((s) => s.explainMode);
  const [filter, setFilter] = useState<Filter>("all");
  const signal = sentiment.items.filter((i) => i.verdict === "signal");
  const noise = sentiment.items.filter((i) => i.verdict === "noise");
  const shown = !explain ? signal : filter === "signal" ? signal : filter === "noise" ? noise : sentiment.items;

  return (
    <section data-tour-id="signal-noise-view" aria-labelledby="svn-h">
      <h2 id="svn-h">Signal vs. noise</h2>
      {explain && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2 sm" aria-label="Filter funnel">
            <span className="pill pill-mu">{sentiment.total_posts_fetched} posts fetched</span>
            <span aria-hidden="true">→</span>
            <span className="pill pill-ng">{sentiment.bots_and_duplicates_removed} bots &amp; duplicates removed in code</span>
            <span aria-hidden="true">→</span>
            <span className="pill">{sentiment.posts_classified} classified</span>
          </div>
          <div className="mb-3 flex gap-1" role="group" aria-label="Filter posts">
            {([["all", `All (${sentiment.items.length})`], ["signal", `Signal Only (${signal.length})`], ["noise", `Noise Only (${noise.length})`]] as const)
              .map(([id, label]) => (
                <button key={id} type="button" className="btn-ghost !py-1 sm" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
              ))}
          </div>
        </>
      )}
      <div className="cgrid">{shown.map((p) => <PostCard key={p.post_id} p={p} />)}</div>
    </section>
  );
}
