import { useId, useState, type ReactNode } from "react";
import type { MetricLedgerRow } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";

/** "(Receivables_t / Revenue_t) * 365" -> "Receivables_FY26 / Revenue_FY26 × 365" using the row's own input periods. */
export function symbolicFormula(row: MetricLedgerRow): string {
  const periods = [...new Set(row.inputs.map((i) => i.fiscal_period))];
  const latest = row.fiscal_period;
  const others = periods.filter((p) => p !== latest);
  let out = row.formula_template;
  out = out.replace(/\b([A-Za-z]+)_t_minus_n\b/g, (_m, s: string) => `${s}_${others[others.length - 1] ?? "t-n"}`);
  out = out.replace(/\b([A-Za-z]+)_t_minus_(\d)\b/g, (_m, s: string, k: string) => `${s}_${others[Number(k) - 1] ?? `t-${k}`}`);
  out = out.replace(/\b([A-Za-z]+)_t\b/g, (_m, s: string) => `${s}_${latest}`);
  out = out.replace(/\b([A-Za-z]+)_q\b/g, (_m, s: string) => `${s}_${latest}`);
  out = out.replace(/\b([A-Za-z]+)_prior\b/g, (_m, s: string) => `${s}_${others[0] ?? "prior"}`);
  return out.replace(/\s\*\s/g, " × ");
}

export function substitutedWithResult(row: MetricLedgerRow): string {
  return `${row.formula_substituted.replace(/\s\*\s/g, " × ")} = ${row.formatted_value}`;
}

/** B.5: hover / focus popover with the exact formula, substituted inputs and source; click opens the Evidence Drawer. */
export default function ShowTheMathPopover({ row, children, className = "" }: {
  row: MetricLedgerRow;
  children?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const openEvidence = useRunStore((s) => s.openEvidence);
  return (
    <span className={`relative inline-block ${className}`}
          onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="num-link"
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => openEvidence(row.metric_id)}
        data-metric-id={row.metric_id}
      >
        {children ?? row.formatted_value}
      </button>
      {open && (
        <span role="tooltip" id={id}
              className="absolute left-0 top-full z-40 mt-1 block w-max max-w-[320px] rounded-card border border-ln bg-sf-elev p-3 text-left shadow-lg">
          <span className="block text-micro text-mu">Show the math · computed in code</span>
          <span className="block font-medium">{row.metric_label}</span>
          <code className="mt-1 block whitespace-normal text-tx">{symbolicFormula(row)}</code>
          <code className="mt-1 block whitespace-normal text-ac">{substitutedWithResult(row)}</code>
          {row.status !== "computed" && row.status_note && <span className="mt-1 block sm mu">{row.status_note}</span>}
          <span className="mt-1 block sm mu">{row.primary_source_ref} — click to open the Evidence Drawer</span>
        </span>
      )}
    </span>
  );
}
