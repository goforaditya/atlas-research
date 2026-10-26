import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import type {
  EvidenceCitation, EvidenceResponse, LineItem, MetricLedgerRow, PeerCompRow, ResearchRunState, SourceTier,
} from "../../types/atlas";
import { fetchEvidence } from "../../api/client";
import { useRunStore } from "../../store/useRunStore";
import { currencyLabel, groupDecimalString, splitProse, tierTitle, trustWeight } from "../../utils/formatters";
import { symbolicFormula } from "./ShowTheMathPopover";

/** What a citation token points at, resolved against the projected report state. */
export interface ResolvedToken {
  token: string;
  title: string;
  evidence: EvidenceCitation | null;
  metric: MetricLedgerRow | null;
  lineItem: LineItem | null;
  note: string | null;
  relatedTokens: string[];
}

export function resolveToken(state: ResearchRunState, token: string): ResolvedToken | null {
  const reg = state.evidence_registry ?? {};
  const base = { token, metric: null, lineItem: null, note: null, relatedTokens: [] as string[] };
  const metric = [...state.metrics_ledger, ...state.quarterly_metrics].find((m) => m.metric_id === token);
  if (metric) {
    const ev = metric.inputs.length ? reg[metric.inputs[0].evidence_id] ?? null : null;
    return { ...base, title: metric.metric_label, evidence: ev, metric };
  }
  const item = [...state.line_items, ...state.quarterly_items].find((i) => i.item_id === token);
  if (item) {
    return {
      ...base, title: `${item.reported_label} ${item.fiscal_period}`, evidence: item.evidence, lineItem: item,
      note: item.is_derived ? "Derived in code from the line items below; it is not read from the filing." : null,
      relatedTokens: item.derived_from_item_ids,
    };
  }
  if (reg[token]) return { ...base, title: reg[token].document_title, evidence: reg[token] };
  const news = state.news_events.find((n) => n.event_id === token);
  if (news) return { ...base, title: news.headline, evidence: news.evidence, note: news.summary };
  const post = state.sentiment?.items.find((p) => p.post_id === token);
  if (post) return { ...base, title: `${post.platform} · ${post.audience}`, evidence: post.evidence, note: post.quote_text };
  const flag = state.red_flags.find((f) => f.rule_id === token);
  if (flag) {
    return { ...base, title: flag.rule_name, evidence: null, note: flag.explanation, relatedTokens: flag.supporting_metric_ids };
  }
  const con = state.contradictions.find((c) => c.contradiction_id === token);
  if (con) {
    return { ...base, title: con.topic, evidence: con.source_b_evidence, note: con.resolution_rationale,
             relatedTokens: [con.source_a_evidence.evidence_id, con.source_b_evidence.evidence_id] };
  }
  if (token.startsWith("peer_")) {
    const row = state.peer_comps.find((r) => token.startsWith(`peer_${peerSlug(r)}_`));
    if (row) {
      const column = token.slice(`peer_${peerSlug(row)}_`.length);
      const cell = row.formatted[column];
      const how = row.is_median_row ? "Median of the peer rows, computed in code." : "Computed by the same metrics engine as the target.";
      const evId = peerEvidenceId(row.evidence_ids, column);
      return { ...base, title: `${row.company_name}${cell ? ` · ${cell}` : ""}`, evidence: evId ? reg[evId] ?? null : null,
               note: how, relatedTokens: row.evidence_ids };
    }
  }
  return null;
}

const PEER_COLUMN_FIELD: Record<string, string> = { rev_growth_pct: "revenue", ebitda_margin_pct: "revenue", roe_pct: "pat", pe_multiple: "pat" };

/** Mirrors backend exporter.peer_evidence_id: latest evidence for the column's main input, else the first id. */
export function peerEvidenceId(ids: string[], column: string): string | null {
  const f = PEER_COLUMN_FIELD[column] ?? "";
  const matches = ids.filter((e) => e.includes(`_${f}_`));
  return matches[matches.length - 1] ?? ids[0] ?? null;
}

/** Mirrors backend verification_gate.peer_slug: "NSE: NXRA" -> "nxra", the median row -> "median". */
export function peerSlug(row: PeerCompRow): string {
  if (row.is_median_row) return "median";
  return (row.ticker.toLowerCase().split(":").pop() ?? "").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

/** Citation chip that opens the Evidence Drawer. */
export function Cite({ token, label }: { token: string; label?: string }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  return (
    <button type="button" className="cite" onClick={() => openEvidence(token)} aria-label={`Open evidence ${token}`}
            title={token}>
      {label ?? "src"}
    </button>
  );
}

/** Prose with "number [[token]]" pairs rendered as clickable figures (no raw tokens are ever shown). */
export function Prose({ text, className = "" }: { text: string | null | undefined; className?: string }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const explain = useRunStore((s) => s.explainMode);
  return (
    <span className={className}>
      {splitProse(text).map((seg, i) =>
        seg.kind === "text" ? (
          <span key={i}>{seg.text}</span>
        ) : seg.value ? (
          <button key={i} type="button" className="num-link" onClick={() => openEvidence(seg.token)} data-token={seg.token}
                  title={explain ? `Source: ${seg.token}` : undefined}>
            {seg.value}
          </button>
        ) : (
          <Cite key={i} token={seg.token} />
        ),
      )}
    </span>
  );
}

function TierBadge({ tier, weight }: { tier: SourceTier; weight: number }) {
  const cls = tier === "T1" ? "pill-ps" : tier === "T4" ? "pill-wn" : "";
  return <span className={`pill ${cls}`} title={tierTitle(tier)}>{tier} · trust {trustWeight(weight)}</span>;
}

/** B.4: slide-over drawer showing the source excerpt beside the extracted value. */
export default function EvidenceDrawer() {
  const token = useRunStore((s) => s.activeEvidenceId);
  const state = useRunStore((s) => s.projectedState);
  const runId = useRunStore((s) => s.runId);
  const openEvidence = useRunStore((s) => s.openEvidence);
  const [detail, setDetail] = useState<EvidenceResponse | null>(null);
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const resolved = token ? resolveToken(state, token) : null;
  const evId = resolved?.evidence?.evidence_id ?? null;

  useEffect(() => {
    setDetail(null);
    setImgSize(null);
    if (!runId || !evId) return;
    let live = true;
    fetchEvidence(runId, evId).then((d) => live && setDetail(d)).catch(() => live && setDetail(null));
    return () => { live = false; };
  }, [runId, evId]);

  useEffect(() => {
    if (!token) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && openEvidence(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [token, openEvidence]);

  if (!token) return null;
  const ev = resolved?.evidence ?? null;
  const unit = resolved?.lineItem?.unit ?? state.entity?.currency ?? null;
  const checks = detail?.linked_reconciliation_checks ?? [];

  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <div className="absolute inset-0 bg-black/30" onClick={() => openEvidence(null)} aria-hidden="true" />
      <aside role="dialog" aria-modal="true" aria-labelledby="ev-title"
             className="absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-ln bg-sf p-4 shadow-xl md:w-[520px] drawer-in">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <div className="text-micro text-mu">Evidence</div>
            <h3 id="ev-title" className="mb-1">{resolved?.title ?? token}</h3>
            {ev && (
              <div className="flex flex-wrap items-center gap-2 sm">
                <span>{ev.document_title}{ev.page_number ? ` · p.${ev.page_number}` : ""}</span>
                <TierBadge tier={ev.source_tier} weight={ev.trust_weight} />
              </div>
            )}
          </div>
          <button ref={closeRef} type="button" className="btn-ghost" onClick={() => openEvidence(null)} aria-label="Close evidence">
            <X size={16} />
          </button>
        </div>

        {!resolved && <p className="sm mu">This citation is not in the report yet at the current scrubber position.</p>}
        {resolved?.note && <p className="sm">{resolved.note}</p>}

        {resolved?.metric && (
          <div className="card bg-sf-elev">
            <div className="text-micro text-mu">Formula and inputs · computed in Python Decimal</div>
            <code className="block">{symbolicFormula(resolved.metric)}</code>
            <code className="block text-ac">{resolved.metric.formula_substituted} = {resolved.metric.formatted_value}</code>
            {resolved.metric.status_note && <p className="sm mu mt-1">{resolved.metric.status_note}</p>}
            <div className="mt-2 flex flex-wrap gap-1">
              {resolved.metric.inputs.map((inp) => (
                <button key={inp.symbol} type="button" className="pill cursor-pointer border-0"
                        onClick={() => openEvidence(inp.line_item_id)}>
                  {inp.symbol} = {groupDecimalString(inp.value)}
                </button>
              ))}
            </div>
          </div>
        )}

        {resolved && resolved.relatedTokens.length > 0 && !resolved.metric && (
          <div className="mb-3 flex flex-wrap gap-1">
            {resolved.relatedTokens.map((t) => (
              <button key={t} type="button" className="pill cursor-pointer border-0" onClick={() => openEvidence(t)}>{t}</button>
            ))}
          </div>
        )}

        {ev && (
          <div className="grid gap-3 md:grid-cols-2">
            <section aria-label="Source page excerpt">
              <div className="text-micro text-mu mb-1">Source page excerpt</div>
              {detail?.excerpt_image_url ? (
                <div className="relative">
                  <img src={detail.excerpt_image_url} alt={`${ev.document_title} page ${ev.page_number ?? ""}`}
                       className="block w-full rounded border border-ln"
                       onLoad={(e) => setImgSize({ w: e.currentTarget.clientWidth, h: e.currentTarget.clientHeight })} />
                  {imgSize && ev.bbox && ev.page_size && (
                    <span aria-hidden="true" className="absolute rounded-sm"
                          style={{
                            left: (ev.bbox[0] / ev.page_size[0]) * imgSize.w,
                            top: (ev.bbox[1] / ev.page_size[1]) * imgSize.h,
                            width: ((ev.bbox[2] - ev.bbox[0]) / ev.page_size[0]) * imgSize.w,
                            height: ((ev.bbox[3] - ev.bbox[1]) / ev.page_size[1]) * imgSize.h,
                            background: "rgba(250, 204, 21, 0.35)", outline: "2px solid rgba(202, 138, 4, 0.9)",
                          }} />
                  )}
                </div>
              ) : (
                <pre className="whitespace-pre-wrap rounded border border-ln bg-sf-elev p-2">{ev.verbatim_snippet}</pre>
              )}
              {ev.source_url && (
                <a className="sm text-ac" href={ev.source_url} target="_blank" rel="noreferrer">Open source document</a>
              )}
            </section>
            <section aria-label="Extracted and normalised value">
              <div className="text-micro text-mu mb-1">Extracted &amp; normalised value</div>
              <dl className="sm grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <dt className="mu">As printed</dt><dd className="m-0">{ev.extracted_value_raw || "—"}</dd>
                {ev.source_unit && (<><dt className="mu">Source unit</dt><dd className="m-0">{ev.source_unit}</dd></>)}
                {resolved?.lineItem && (
                  <><dt className="mu">Normalised</dt><dd className="m-0">{groupDecimalString(resolved.lineItem.value)}</dd></>
                )}
                {unit && (<><dt className="mu">Unit</dt><dd className="m-0">{currencyLabel(unit)}</dd></>)}
              </dl>
              {checks.length > 0 && (
                <ul className="mt-2 list-none p-0 sm">
                  {checks.map((c) => (
                    <li key={c.check_id} className={c.passed ? "up" : c.status === "skipped" ? "mu" : "dn"}>
                      {c.status === "passed" ? "Passed" : c.status === "skipped" ? "Skipped" : "Failed"}: {c.check_name} ({c.fiscal_period})
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}
