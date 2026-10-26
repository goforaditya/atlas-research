import { useEffect, useMemo, useRef } from "react";
import { AlertTriangle, Check, Circle, XCircle } from "lucide-react";
import type { ActorBadge, ExplainStagePanelData, StageStatus } from "../../types/atlas";
import { STAGE_LABELS, STAGE_ORDER } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { stageStatusesAt } from "../../utils/stateProjector";
import { duration } from "../../utils/formatters";
import TimelineScrubber from "./TimelineScrubber";

export function ActorPill({ actor }: { actor: ActorBadge }) {
  return (
    <span className={`pill ${actor === "LLM" ? "pill-wn" : ""} font-mono`} title={actor === "LLM" ? "Done by the language model" : "Done by Python code"}>
      {actor}
    </span>
  );
}

function StatusIcon({ status }: { status: StageStatus }) {
  if (status === "completed") return <Check size={14} className="text-ps" aria-label="completed" />;
  if (status === "warning") return <AlertTriangle size={14} className="text-wn" aria-label="warning" />;
  if (status === "failed") return <XCircle size={14} className="text-ng" aria-label="failed" />;
  if (status === "running") return <span className="pulse-dot" aria-label="running" />;
  return <Circle size={14} className="text-mu" aria-label="pending" />;
}

function InspectionPanel({ panel }: { panel: ExplainStagePanelData }) {
  return (
    <div className="card mt-2" role="region" aria-label={`${panel.stage_title} inspection`}>
      <h3>{panel.stage_title}</h3>
      <p className="sm mu mt-0">{panel.purpose_one_sentence}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="text-micro text-mu">Inputs</div>
          <ul className="sm mt-1 pl-5">{panel.inputs_summary.map((x, i) => <li key={i}>{x}</li>)}</ul>
          <div className="text-micro text-mu mt-3">Tool calls</div>
          {panel.tool_calls.length === 0 && <p className="sm mu">No external tool calls in this stage.</p>}
          {panel.tool_calls.map((tc) => (
            <details key={tc.call_id} className="mt-1 rounded border border-ln bg-sf-elev p-2 sm">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                <code>{tc.tool_name}</code>
                <ActorPill actor={tc.actor} />
                <span className="mu">{duration(tc.duration_ms)}</span>
                {tc.status !== "ok" && <span className={`pill ${tc.status === "error" ? "pill-ng" : "pill-wn"}`}>{tc.status}</span>}
              </summary>
              <div className="text-micro text-mu mt-2">Arguments</div>
              <pre className="m-0 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(tc.arguments, null, 2)}</pre>
              <div className="text-micro text-mu mt-2">Raw output sample</div>
              <pre className="m-0 max-h-48 overflow-auto whitespace-pre-wrap">{tc.raw_output_sample}</pre>
            </details>
          ))}
        </div>
        <div>
          <div className="text-micro text-mu">What code did vs. what the LLM did</div>
          <ul className="mt-1 list-none p-0 sm">
            {panel.actions.map((a, i) => (
              <li key={i} className="mb-1 flex items-start gap-2"><ActorPill actor={a.actor} /><span>{a.action_summary}</span></li>
            ))}
          </ul>
          <div className="mt-3 rounded-card border border-ac bg-ac2 p-3 sm">
            <div className="text-micro text-ac">Decision made &amp; why</div>
            <div className="font-medium">{panel.decision_made}</div>
            <div>{panel.decision_rationale}</div>
          </div>
          <div className="text-micro text-mu mt-3">What could go wrong</div>
          <ul className="sm mt-1 pl-5">{panel.failure_modes_and_guards.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      </div>
    </div>
  );
}

/** B.2: 12-node stepper with the stage inspection card, plus the B.3 scrubber attached below. */
export default function PipelineStageTracker() {
  const events = useRunStore((s) => s.events);
  const index = useRunStore((s) => s.scrubberIndex);
  const selected = useRunStore((s) => s.selectedStageId);
  const selectStage = useRunStore((s) => s.selectStage);
  const views = useMemo(() => stageStatusesAt(events, index), [events, index]);
  const progress = events[Math.min(index, events.length - 1)]?.progress_pct ?? 0;
  const nodeRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const running = STAGE_ORDER.find((id) => views[id].status === "running");
  useEffect(() => {
    if (running) nodeRefs.current[running]?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [running]);

  const panel = selected ? views[selected].terminalEvent?.explain_panel ?? null : null;

  return (
    <div className="mb-3">
      <div data-tour-id="pipeline-tracker" className="overflow-hidden rounded-card border border-ln bg-sf">
        <div className="bar !rounded-none !h-1" aria-hidden="true"><i style={{ width: `${progress}%` }} /></div>
        <ol className="m-0 flex snap-x list-none gap-1 overflow-x-auto p-2" aria-label="Pipeline stages">
          {STAGE_ORDER.map((id) => {
            const v = views[id];
            const meta = STAGE_LABELS[id];
            const active = selected === id;
            return (
              <li key={id} className="snap-start">
                <button ref={(el) => { nodeRefs.current[id] = el; }} type="button"
                        onClick={() => selectStage(id)} aria-expanded={active}
                        aria-label={`Stage ${meta.num} ${meta.title}: ${v.status}`}
                        className={`flex min-w-[84px] cursor-pointer flex-col items-start rounded-lg border px-2 py-1 text-left ${
                          active ? "border-ac bg-ac2" : v.status === "running" ? "border-ac bg-sf" : "border-transparent bg-sf hover:border-ln"
                        }`}>
                  <span className="flex items-center gap-1 text-micro text-mu">{meta.num}<StatusIcon status={v.status} /></span>
                  <span className="sm font-medium">{meta.short}</span>
                  <span className="text-micro text-mu">{v.durationMs !== null ? duration(v.durationMs) : " "}</span>
                </button>
              </li>
            );
          })}
        </ol>
        <TimelineScrubber />
      </div>
      {selected && !panel && (
        <div className="skeleton mt-2">Stage {STAGE_LABELS[selected].num} has not finished at this point in the timeline.</div>
      )}
      {panel && <InspectionPanel panel={panel} />}
    </div>
  );
}
