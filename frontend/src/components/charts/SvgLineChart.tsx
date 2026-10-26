import { useState } from "react";
import type { DecimalString } from "../../types/atlas";
import { geometry } from "../../utils/formatters";
import { useRunStore } from "../../store/useRunStore";

export interface LinePoint {
  label: string;
  value: DecimalString | null;
  formatted: string;
  token?: string;
}

export interface LineSeries {
  name: string;
  /** CSS variable for the series colour, in fixed order: --c1 then --c2. */
  color: string;
  points: LinePoint[];
}

const W = 560;
const H = 220;
const PAD = { top: 16, right: 96, bottom: 26, left: 8 };

export function lineLayout(series: LineSeries[], labels: string[]) {
  const all = series.flatMap((s) => s.points.map((p) => geometry(p.value))).filter((n): n is number => n !== null);
  let max = Math.max(...all, 0);
  let min = Math.min(...all, 0);
  if (max === min) { max += 1; min -= 1; }
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (labels.length <= 1 ? plotW / 2 : (plotW * i) / (labels.length - 1));
  const y = (n: number) => PAD.top + ((max - n) / (max - min)) * plotH;
  return { x, y, zeroY: y(0) };
}

/** Zero-dependency trend chart with one shared y-axis, crosshair hover and direct end labels. */
export default function SvgLineChart({ title, series }: { title: string; series: LineSeries[] }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const [hover, setHover] = useState<number | null>(null);
  const labels = series[0]?.points.map((p) => p.label) ?? [];
  const { x, y, zeroY } = lineLayout(series, labels);
  const step = labels.length > 1 ? x(1) - x(0) : W;

  return (
    <figure className="m-0">
      <figcaption className="text-micro text-mu mb-1 flex flex-wrap gap-3">
        <span>{title}</span>
        {series.length > 1 && series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <svg width="14" height="4" aria-hidden="true"><line x1="0" x2="14" y1="2" y2="2" stroke={s.color} strokeWidth="2" /></svg>
            {s.name}
          </span>
        ))}
      </figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img"
             aria-label={`${title}: ${series.map((s) => `${s.name} ${s.points.map((p) => `${p.label} ${p.formatted}`).join(", ")}`).join("; ")}`}>
          <line x1={PAD.left} x2={W - PAD.right} y1={zeroY} y2={zeroY} stroke="var(--ln)" strokeWidth={1} />
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--mu)" strokeDasharray="3 3" />}
          {series.map((s) => {
            const pts = s.points.map((p, i) => ({ p, i, n: geometry(p.value) })).filter((q) => q.n !== null) as { p: LinePoint; i: number; n: number }[];
            const d = pts.map((q, k) => `${k ? "L" : "M"}${x(q.i)},${y(q.n)}`).join(" ");
            const last = pts[pts.length - 1];
            return (
              <g key={s.name}>
                <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
                {pts.map((q) => (
                  <circle key={q.i} cx={x(q.i)} cy={y(q.n)} r={hover === q.i ? 5 : 4} fill={s.color} stroke="var(--sf)" strokeWidth={2} />
                ))}
                {last && (
                  <text x={x(last.i) + 8} y={y(last.n) + 4} fontSize={12} fill="var(--tx)">{`${s.name} ${last.p.formatted}`}</text>
                )}
              </g>
            );
          })}
          {labels.map((l, i) => (
            <g key={l}>
              <text x={x(i)} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--mu)">{l}</text>
              <rect x={x(i) - step / 2} y={0} width={step} height={H - PAD.bottom} fill="transparent" style={{ cursor: "pointer" }}
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                    onClick={() => { const t = series[0]?.points[i]?.token; if (t) openEvidence(t); }} />
            </g>
          ))}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute top-0 rounded border border-ln bg-sf-elev px-2 py-1 sm shadow"
               style={{ left: `${(x(hover) / W) * 100}%`, transform: "translateX(-50%)" }}>
            <b>{labels[hover]}</b>
            {series.map((s) => <div key={s.name}>{s.name}: {s.points[hover]?.formatted ?? "—"}</div>)}
          </div>
        )}
      </div>
    </figure>
  );
}
