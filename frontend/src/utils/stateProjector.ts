import type { ResearchRunState, RunMode, StageEvent, StageId, StageStatus } from "../types/atlas";
import { STAGE_ORDER } from "../types/atlas";

export interface RunBase {
  run_id: string;
  mode: RunMode;
  company_query: string;
  started_at_ms: number;
}

export function emptyState(base?: Partial<RunBase>): ResearchRunState {
  return {
    run_id: base?.run_id ?? "",
    mode: base?.mode ?? "replay",
    company_query: base?.company_query ?? "",
    status: "running",
    started_at_ms: base?.started_at_ms ?? 0,
    completed_at_ms: null,
    sources_read_count: 0,
    entity: null,
    sub_questions: [],
    fiscal_years: [],
    line_items: [],
    quarterly_items: [],
    quarterly_metrics: [],
    management_roster: [],
    reconciliation_checks: [],
    metrics_ledger: [],
    red_flags: [],
    news_events: [],
    sentiment: null,
    peer_comps: [],
    market_advantages: [],
    market_disadvantages: [],
    market_positioning_prose: null,
    insights: [],
    provenance_nodes: [],
    provenance_edges: [],
    contradictions: [],
    summary: null,
    verification: null,
    evidence_registry: {},
    stated_gaps: [],
    events: [],
  };
}

/** Pure projection of the report at a scrubber position: shallow-merge events[0..cursorIndex].state_patch.
 *  Each top-level key in a patch replaces the previous value (Section 4 merge rule). O(k) in the cursor. */
export function projectStateAt(events: StageEvent[], cursorIndex: number, base?: Partial<RunBase>): ResearchRunState {
  const state = emptyState(base) as unknown as Record<string, unknown>;
  const last = Math.min(cursorIndex, events.length - 1);
  for (let i = 0; i <= last; i++) {
    const patch = events[i].state_patch as Record<string, unknown>;
    for (const key of Object.keys(patch)) state[key] = patch[key];
  }
  const out = state as unknown as ResearchRunState;
  out.events = events.slice(0, last + 1);
  return out;
}

export interface StageView {
  status: StageStatus;
  startedMs: number | null;
  durationMs: number | null;
  terminalIndex: number | null;
  terminalEvent: StageEvent | null;
}

/** Status of every stage as of the scrubber position. */
export function stageStatusesAt(events: StageEvent[], cursorIndex: number): Record<StageId, StageView> {
  const out = {} as Record<StageId, StageView>;
  for (const id of STAGE_ORDER) {
    out[id] = { status: "pending", startedMs: null, durationMs: null, terminalIndex: null, terminalEvent: null };
  }
  const last = Math.min(cursorIndex, events.length - 1);
  for (let i = 0; i <= last; i++) {
    const e = events[i];
    const v = out[e.stage_id];
    if (e.stage_status === "running") {
      v.status = "running";
      v.startedMs = e.elapsed_ms;
    } else {
      v.status = e.stage_status;
      v.terminalIndex = i;
      v.terminalEvent = e;
      v.durationMs = v.startedMs === null ? null : e.elapsed_ms - v.startedMs;
    }
  }
  return out;
}

/** Index of the stage's terminal event in the full events array, or null when not yet emitted. */
export function terminalIndexOf(events: StageEvent[], stageId: StageId): number | null {
  const i = events.findIndex((e) => e.stage_id === stageId && e.stage_status !== "running");
  return i === -1 ? null : i;
}

/** First event index whose patch carries the key; used by progressive skeletons. */
export function unlockIndexOf(events: StageEvent[], key: keyof ResearchRunState): number | null {
  const i = events.findIndex((e) => Object.prototype.hasOwnProperty.call(e.state_patch, key));
  return i === -1 ? null : i;
}
