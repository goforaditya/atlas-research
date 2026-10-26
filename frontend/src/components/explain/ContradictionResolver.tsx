import type { ContradictionRecord, EvidenceCitation, SourceTier } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { Prose } from "./EvidenceDrawer";
import { trustWeight } from "../../utils/formatters";

export function winnerLabel(c: ContradictionRecord): string {
  if (c.winning_source === "BOTH_FRAMED") return "Both Retained (Fact vs Framing)";
  const [w, l] = c.winning_source === "A" ? [c.source_a_tier, c.source_b_tier] : [c.source_b_tier, c.source_a_tier];
  return `Source ${c.winning_source} Won (${w} > ${l})`;
}

function SourceColumn({ name, claim, tier, weight, ev, won }: {
  name: string; claim: string; tier: SourceTier; weight: number; ev: EvidenceCitation; won: boolean;
}) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  return (
    <div className={`rounded-card border p-3 ${won ? "border-ps" : "border-ln"}`}>
      <div className="flex items-center justify-between gap-2 text-micro text-mu">
        <span>{name}</span>
        <span className={`pill ${tier === "T1" ? "pill-ps" : tier === "T4" ? "pill-wn" : ""}`}>{tier}</span>
      </div>
      <p className="sm my-2"><Prose text={claim} /></p>
      <div className="flex items-center gap-2 text-micro text-mu">
        trust {trustWeight(weight)}
        <span className="bar flex-1" aria-hidden="true"><i style={{ width: `${weight * 100}%` }} /></span>
      </div>
      <button type="button" className="num-link sm mt-2" onClick={() => openEvidence(ev.evidence_id)}>
        {ev.document_title}{ev.page_number ? ` p.${ev.page_number}` : ""}
      </button>
    </div>
  );
}

/** B.6: side-by-side conflicting claims, tier weights and the resolution. */
export default function ContradictionResolver({ records }: { records: ContradictionRecord[] }) {
  return (
    <section aria-labelledby="ct-h" className="mb-3">
      <h2 id="ct-h">Contradictions resolved</h2>
      {records.length === 0 && <p className="sm mu">No cross-source conflicts were found.</p>}
      {records.map((c) => (
        <article key={c.contradiction_id} className="card">
          <h3>{c.topic}</h3>
          <div className="grid gap-3 md:grid-cols-3">
            <SourceColumn name="Source A" claim={c.source_a_claim} tier={c.source_a_tier} weight={c.source_a_weight}
                          ev={c.source_a_evidence} won={c.winning_source === "A"} />
            <SourceColumn name="Source B" claim={c.source_b_claim} tier={c.source_b_tier} weight={c.source_b_weight}
                          ev={c.source_b_evidence} won={c.winning_source === "B"} />
            <div className="rounded-card bg-sf-elev p-3">
              <span className={`pill ${c.winning_source === "BOTH_FRAMED" ? "pill-wn" : "pill-ps"}`}>{winnerLabel(c)}</span>
              <p className="sm my-2">{c.resolution_rationale}</p>
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}
