import { useState } from "react";
import type { DecimalString } from "../../types/atlas";
import { geometry } from "../../utils/formatters";
import { useRunStore } from "../../store/useRunStore";

export interface BarDatum {
  label: string;
  value: DecimalString | null;
  formatted: string;
  token?: string;
}

const W = 560;
const H = 220;
const PAD = { top: 22, right: 8, bottom: 26, left: 8 };

/** Pure layout so the geometry is testable: zero baseline, bars grow up for positive and down for negative values. */
export function barLayout(data: BarDatum[]) {
  const nums = data.map((d) => geometry(d.value));
  const max = Math.max(0, ...nums.map((n) => n ?? 0));
  const min = Math.min(0, ...nums.map((n) => n ?? 0));
  const span = max - min || 1;
  const plotH = H - PAD.top - PAD.bottom;
  const zeroY = PAD.top + (max / span) * plotH;
  const band = (W - PAD.left - PAD.right) / Math.max(1, data.length);
  const barW = Math.min(56, band * 0.6);
  return data.map((d, i) => {
    const n = nums[i];
    const h = n === null ? 0 : (Math.abs(n) / span) * plotH;
    const x = PAD.left + band * i + (band - barW) / 2;
    const y = n !== null && n < 0 ? zeroY : zeroY - h;
    return { ...d, n, x, y, w: barW, h, cx: PAD.left + band * i + band / 2, zeroY, band };
  });
}

/** Rect with 4px rounding on the data end only, anchored square on the baseline. */
function barPath(x: number, y: number, w: number, h: number, down: boolean): string {
  const r = Math.min(4, h, w / 2);
  if (h <= 0) return "";
  if (down) {
    return `M${x},${y} H${x + w} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x + r} Q${x},${y + h} ${x},${y + h - r} Z`;
  }
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/** Zero-dependency deterministic bar chart. Values come from the backend; labels show its formatted strings. */
export default function SvgBarChart({ title, data }: { title: string; data: BarDatum[] }) {
  const openEvidence = useRunStore((s) => s.openEvidence);
  const [hover, setHover] = useState<number | null>(null);
  const bars = barLayout(data);
  const zeroY = bars[0]?.zeroY ?? H - PAD.bottom;

  return (
    <figure className="m-0">
      <figcaption className="text-micro text-mu mb-1">{title}</figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-label={`${title}: ${data.map((d) => `${d.label} ${d.formatted}`).join(", ")}`}>
          <line x1={PAD.left} x2={W - PAD.right} y1={zeroY} y2={zeroY} stroke="var(--ln)" strokeWidth={1} />
          {bars.map((b, i) => {
            const down = b.n !== null && b.n < 0;
            return (
              <g key={b.label} opacity={hover === null || hover === i ? 1 : 0.55}>
                <path d={barPath(b.x, b.y, b.w, b.h, down)} fill={down ? "var(--ng)" : "var(--c1)"} />
                <text x={b.cx} y={down ? b.y + b.h + 13 : b.y - 6} textAnchor="middle" fontSize={12} fill="var(--tx)">
                  {b.n === null ? "—" : b.formatted}
                </text>
                <text x={b.cx} y={H - 8} textAnchor="middle" fontSize={12} fill="var(--mu)">{b.label}</text>
                {/* Hit target spans the whole band, larger than the mark. */}
                <rect x={b.cx - b.band / 2} y={0} width={b.band} height={H} fill="transparent"
                      style={{ cursor: b.token ? "pointer" : "default" }}
                      onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                      onClick={() => b.token && openEvidence(b.token)}>
                  <title>{`${b.label}: ${b.formatted}`}</title>
                </rect>
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute top-0 rounded border border-ln bg-sf-elev px-2 py-1 sm shadow"
               style={{ left: `${(bars[hover].cx / W) * 100}%`, transform: "translateX(-50%)" }}>
            <b>{bars[hover].label}</b> {bars[hover].formatted}
            {bars[hover].token && <span className="mu"> · click for source</span>}
          </div>
        )}
      </div>
    </figure>
  );
}
