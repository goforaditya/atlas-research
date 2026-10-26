import { useMemo, useState } from "react";
import type { InsightCard, ProvenanceEdge, ProvenanceNode } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { ClaimBadge } from "../tabs/SummaryTab";

const EDGE_STYLE: Record<ProvenanceEdge["relationship"], { stroke: string; dash?: string; label: string }> = {
  proves: { stroke: "var(--ps)", label: "proves" },
  corroborates: { stroke: "var(--ac)", dash: "6 4", label: "corroborates" },
  contextualizes: { stroke: "var(--wn)", dash: "2 4", label: "contextualises" },
  contradicts: { stroke: "var(--ng)", label: "contradicts" },
};

const CLASS_FILL = { fact: "var(--ps)", inference: "var(--ac)", hypothesis: "var(--wn)" } as const;

const W = 1000;
const LEFT_W = 400;
const RIGHT_X = 640;
const ROW = 34;
const INS_ROW = 64;
const PAD = 8;

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Deterministic 3-column layout: evidence nodes in insight order on the left, insights on the right. */
export function provenanceLayout(nodes: ProvenanceNode[], edges: ProvenanceEdge[], insights: InsightCard[]) {
  const insightIds = new Set(insights.map((i) => i.insight_id));
  const order = new Map<string, number>();
  insights.forEach((ins) => {
    edges.filter((e) => e.target_node_id === ins.insight_id).forEach((e) => {
      if (!order.has(e.source_node_id)) order.set(e.source_node_id, order.size);
    });
  });
  const left = nodes.filter((n) => !insightIds.has(n.node_id))
    .sort((a, b) => (order.get(a.node_id) ?? 1e6) - (order.get(b.node_id) ?? 1e6));
  const leftY = new Map(left.map((n, i) => [n.node_id, PAD + i * ROW]));
  const height = Math.max(left.length * ROW, insights.length * INS_ROW) + PAD * 2;
  const insOffset = (height - insights.length * INS_ROW) / 2;
  const rightY = new Map(insights.map((ins, i) => [ins.insight_id, insOffset + i * INS_ROW]));
  return { left, leftY, rightY, height };
}

/** B.9: hover or click an insight to highlight its upstream evidence; click evidence to open the drawer. */
export default function InsightProvenanceGraph() {
  const state = useRunStore((s) => s.projectedState);
  const openEvidence = useRunStore((s) => s.openEvidence);
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const focus = hovered ?? pinned;
  const { provenance_nodes: nodes, provenance_edges: edges, insights } = state;
  const layout = useMemo(() => provenanceLayout(nodes, edges, insights), [nodes, edges, insights]);
  const upstream = useMemo(
    () => new Set(focus ? edges.filter((e) => e.target_node_id === focus).map((e) => e.source_node_id) : []),
    [focus, edges],
  );
  const lit = (id: string) => !focus || id === focus || upstream.has(id);

  return (
    <section className="card" aria-labelledby="prov-h">
      <h2 id="prov-h">Insight provenance</h2>
      <div className="mb-2 flex flex-wrap gap-3 sm mu">
        {Object.entries(EDGE_STYLE).map(([k, s]) => (
          <span key={k} className="inline-flex items-center gap-1">
            <svg width="22" height="6" aria-hidden="true"><line x1="0" x2="22" y1="3" y2="3" stroke={s.stroke} strokeWidth="2" strokeDasharray={s.dash} /></svg>
            {s.label}
          </span>
        ))}
      </div>

      {/* Desktop: SVG DAG */}
      <div className="hidden md:block">
        <svg viewBox={`0 0 ${W} ${layout.height}`} className="block h-auto w-full" role="img"
             aria-label="Provenance graph linking evidence to insights">
          {edges.map((e, i) => {
            const y1 = layout.leftY.get(e.source_node_id);
            const y2 = layout.rightY.get(e.target_node_id);
            if (y1 === undefined || y2 === undefined) return null;
            const a = { x: LEFT_W, y: y1 + 13 };
            const b = { x: RIGHT_X, y: y2 + 24 };
            const st = EDGE_STYLE[e.relationship];
            const on = !focus || e.target_node_id === focus;
            return (
              <path key={i} d={`M${a.x},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x},${b.y}`}
                    fill="none" stroke={st.stroke} strokeWidth={on && focus ? 2 : 1.5} strokeDasharray={st.dash}
                    opacity={on ? 1 : 0.25} />
            );
          })}
          {layout.left.map((n) => {
            const y = layout.leftY.get(n.node_id)!;
            return (
              <g key={n.node_id} opacity={lit(n.node_id) ? 1 : 0.25} style={{ cursor: "pointer" }}
                 onClick={() => openEvidence(n.node_id)} role="button" tabIndex={0} aria-label={`Open evidence: ${n.label}`}
                 onKeyDown={(ev) => ev.key === "Enter" && openEvidence(n.node_id)}>
                <rect x={0} y={y} width={LEFT_W} height={26} rx={6} fill="var(--sf-elev)" stroke="var(--ln)" />
                <text x={8} y={y + 17} fontSize={12} fill="var(--mu)" fontFamily="IBM Plex Mono, monospace">{n.source_tier}</text>
                <text x={34} y={y + 17} fontSize={13} fill="var(--tx)">{clip(n.label, 52)}</text>
                <title>{`${n.node_type}: ${n.label}`}</title>
              </g>
            );
          })}
          {insights.map((ins) => {
            const y = layout.rightY.get(ins.insight_id)!;
            const active = focus === ins.insight_id;
            return (
              <g key={ins.insight_id} opacity={lit(ins.insight_id) ? 1 : 0.25} style={{ cursor: "pointer" }}
                 onMouseEnter={() => setHovered(ins.insight_id)} onMouseLeave={() => setHovered(null)}
                 onClick={() => setPinned(pinned === ins.insight_id ? null : ins.insight_id)}
                 role="button" tabIndex={0} aria-pressed={pinned === ins.insight_id} aria-label={`Highlight evidence for ${ins.title}`}
                 onKeyDown={(ev) => ev.key === "Enter" && setPinned(pinned === ins.insight_id ? null : ins.insight_id)}>
                <rect x={RIGHT_X} y={y} width={W - RIGHT_X} height={48} rx={8} fill="var(--sf)"
                      stroke={active ? "var(--ac)" : "var(--ln)"} strokeWidth={active ? 2 : 1} />
                <rect x={RIGHT_X} y={y} width={5} height={48} rx={2} fill={CLASS_FILL[ins.classification]} />
                <text x={RIGHT_X + 14} y={y + 19} fontSize={11} fontWeight={600} fill={CLASS_FILL[ins.classification]}>
                  {ins.classification.toUpperCase()}
                </text>
                <text x={RIGHT_X + 14} y={y + 37} fontSize={13} fill="var(--tx)">{clip(ins.title, 44)}</text>
                <title>{ins.title}</title>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Mobile: stacked list */}
      <ul className="m-0 list-none p-0 md:hidden">
        {insights.map((ins) => (
          <li key={ins.insight_id} className="border-b border-ln py-2">
            <div className="flex items-center gap-2"><ClaimBadge kind={ins.classification} /><b className="sm">{ins.title}</b></div>
            <div className="mt-1 flex flex-wrap gap-1">
              {edges.filter((e) => e.target_node_id === ins.insight_id).map((e) => {
                const n = nodes.find((x) => x.node_id === e.source_node_id);
                return (
                  <button key={e.source_node_id} type="button" className="cite" onClick={() => openEvidence(e.source_node_id)}
                          style={{ borderLeft: `3px solid ${EDGE_STYLE[e.relationship].stroke}` }}>
                    {n ? clip(n.label, 40) : e.source_node_id}
                  </button>
                );
              })}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
