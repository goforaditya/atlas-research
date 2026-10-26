import type { RedFlagResult } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";

function StatusBadge({ f }: { f: RedFlagResult }) {
  if (f.status === "fired") return <span className="pill pill-ng">FIRED</span>;
  if (f.status === "clear") return <span className="pill pill-ps">CLEAR</span>;
  return <span className="pill pill-mu">INSUFFICIENT DATA</span>;
}

function MetricChips({ ids }: { ids: string[] }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <button key={id} type="button" className="cite" onClick={() => openEvidence(id)}>{id}</button>
      ))}
    </span>
  );
}

/** B.8: fired rules as red banner cards, then the full rule matrix from default_red_flags.yaml. */
export default function RedFlagRulesTable({ flags }: { flags: RedFlagResult[] }) {
  const fired = flags.filter((f) => f.fired);
  return (
    <section data-tour-id="red-flags-panel" className="mb-3" aria-labelledby="rf-h">
      <h2 id="rf-h">Red flags <span className="sm mu font-sans font-normal">evaluated in Python from YAML thresholds</span></h2>
      {fired.length === 0 && <p className="sm mu">No rule fired.</p>}
      {fired.map((f) => (
        <div key={f.rule_id} className="rf">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <b>{f.rule_name}</b>
            <span className={`pill ${f.severity === "high" ? "pill-ng" : f.severity === "medium" ? "pill-wn" : "pill-mu"}`}>{f.severity}</span>
          </div>
          <div className="sm">{f.explanation}</div>
          <div className="sm mu mt-1">Threshold: {f.threshold_desc}</div>
          <div className="mt-1"><MetricChips ids={f.supporting_metric_ids} /></div>
        </div>
      ))}
      <details className="card mt-2">
        <summary className="cursor-pointer font-medium">All {flags.length} rules evaluated</summary>
        <div className="scroll-x mt-2">
          <table>
            <thead>
              <tr><th>Rule</th><th>Severity</th><th className="!text-left">Threshold condition</th><th className="!text-left">Observed in filings</th><th>Status</th></tr>
            </thead>
            <tbody>
              {flags.map((f) => (
                <tr key={f.rule_id}>
                  <td>{f.rule_name}</td>
                  <td>{f.severity}</td>
                  <td className="!whitespace-normal !text-left min-w-[220px]">{f.threshold_desc}</td>
                  <td className="!whitespace-normal !text-left min-w-[220px]">
                    {f.observed_value_formatted || "—"}
                    {f.missing_inputs.length > 0 && <div className="mu">Missing: {f.missing_inputs.join(", ")}</div>}
                  </td>
                  <td><StatusBadge f={f} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
