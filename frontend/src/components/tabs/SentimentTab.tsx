import { useRunStore } from "../../store/useRunStore";
import SignalVsNoiseView from "../explain/SignalVsNoiseView";
import { WaitingFor } from "../explain/TimelineScrubber";

export default function SentimentTab() {
  const sentiment = useRunStore((s) => s.projectedState.sentiment);
  const explain = useRunStore((s) => s.explainMode);
  return (
    <WaitingFor stageId="s08_sentiment_filter" stateKey="sentiment" ready={!!sentiment}>
      {sentiment && (
        <>
          <section className="card" aria-labelledby="mix-h">
            <h2 id="mix-h">Overall mix</h2>
            {/* Percentages are integers from the backend (largest-remainder rounding, so they sum to 100). */}
            <div className="flex h-3 overflow-hidden rounded" role="img"
                 aria-label={`Positive ${sentiment.positive_pct}%, neutral ${sentiment.neutral_pct}%, negative ${sentiment.negative_pct}%`}>
              <span className="bg-ps" style={{ width: `${sentiment.positive_pct}%` }} />
              <span className="bg-mu opacity-50" style={{ width: `${sentiment.neutral_pct}%`, marginLeft: 2 }} />
              <span className="bg-ng" style={{ width: `${sentiment.negative_pct}%`, marginLeft: 2 }} />
            </div>
            <div className="mt-2 flex flex-wrap gap-4 sm">
              <span><b className="up">{sentiment.positive_pct}%</b> positive</span>
              <span><b className="mu">{sentiment.neutral_pct}%</b> neutral</span>
              <span><b className="dn">{sentiment.negative_pct}%</b> negative</span>
              <span className="mu">of {sentiment.posts_classified} classified posts{!explain ? " (signal and noise; only signal posts are shown below)" : ""}</span>
            </div>
          </section>
          <section className="card" aria-labelledby="aud-h">
            <h2 id="aud-h">By audience</h2>
            <div className="scroll-x">
              <table>
                <thead><tr><th>Audience</th><th>Dominant tone</th><th className="!text-left">Trend</th></tr></thead>
                <tbody>
                  {sentiment.audience_breakdown.map((a) => (
                    <tr key={a.audience}>
                      <td>{a.audience}</td>
                      <td><span className={a.dominant_tone === "positive" ? "up" : a.dominant_tone === "negative" ? "dn" : "mu"}>{a.dominant_tone}</span></td>
                      <td className="!text-left !whitespace-normal">{a.trend_description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <SignalVsNoiseView sentiment={sentiment} />
        </>
      )}
    </WaitingFor>
  );
}
