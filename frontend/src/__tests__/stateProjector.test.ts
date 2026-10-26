import { describe, expect, it } from "vitest";
import { emptyState, projectStateAt, stageStatusesAt, terminalIndexOf, unlockIndexOf } from "../utils/stateProjector";
import { STAGE_ORDER, type ResearchRunState, type StageEvent } from "../types/atlas";
import { waitingMessage } from "../components/explain/TimelineScrubber";
import { resolveToken } from "../components/explain/EvidenceDrawer";
import { loadSyntheticEvents } from "./fixture";

const events = loadSyntheticEvents();

function naive(upTo: number): Record<string, unknown> {
  const out: Record<string, unknown> = { ...emptyState() };
  for (const e of events.slice(0, upTo + 1)) Object.assign(out, e.state_patch);
  return out;
}

describe("projectStateAt", () => {
  it("fixture has 24 events with strictly increasing event_seq", () => {
    expect(events).toHaveLength(24);
    events.forEach((e, i) => expect(e.event_seq).toBe(i + 1));
  });

  it("before any event the state is empty", () => {
    const s = projectStateAt(events, -1);
    expect(s.line_items).toEqual([]);
    expect(s.summary).toBeNull();
    expect(s.events).toEqual([]);
  });

  it("matches a naive shallow merge at every scrubber index", () => {
    for (let i = 0; i < events.length; i++) {
      const s = projectStateAt(events, i) as unknown as Record<string, unknown>;
      const expected = naive(i);
      for (const key of Object.keys(expected)) {
        if (key === "events") continue;
        expect(s[key], `key ${key} at index ${i}`).toEqual(expected[key]);
      }
      expect((s as unknown as ResearchRunState).events).toHaveLength(i + 1);
    }
  });

  it("later patches replace earlier top-level keys (verification gate rewrites the summary)", () => {
    const synth = events.findIndex((e) => e.stage_id === "s11_synthesis" && e.stage_status === "completed");
    const final = projectStateAt(events, events.length - 1);
    expect(projectStateAt(events, synth).summary).toEqual(events[synth].state_patch.summary);
    expect(final.summary).toEqual(events[events.length - 1].state_patch.summary);
    expect(final.status).toBe("completed");
  });

  it("does not mutate the events it projects", () => {
    const before = JSON.stringify(events);
    projectStateAt(events, 10);
    projectStateAt(events, 23);
    expect(JSON.stringify(events)).toBe(before);
  });

  it("clamps a cursor past the end", () => {
    expect(projectStateAt(events, 999).events).toHaveLength(24);
  });

  it("carries the run base", () => {
    const s = projectStateAt(events, 0, { run_id: "run_x", mode: "replay", company_query: "Q", started_at_ms: 5 });
    expect(s.run_id).toBe("run_x");
    expect(s.company_query).toBe("Q");
  });
});

describe("stageStatusesAt", () => {
  it("all stages are pending before the first event", () => {
    const v = stageStatusesAt(events, -1);
    STAGE_ORDER.forEach((id) => expect(v[id].status).toBe("pending"));
  });

  it("all 12 stages finish by the last event, each with an explain panel and a duration", () => {
    const v = stageStatusesAt(events, events.length - 1);
    STAGE_ORDER.forEach((id) => {
      expect(["completed", "warning"]).toContain(v[id].status);
      expect(v[id].terminalEvent?.explain_panel?.stage_id).toBe(id);
      expect(v[id].durationMs).not.toBeNull();
    });
  });

  it("reports a running stage mid-stream", () => {
    const firstRunning = events.findIndex((e: StageEvent) => e.stage_status === "running");
    expect(stageStatusesAt(events, firstRunning)[events[firstRunning].stage_id].status).toBe("running");
  });
});

describe("unlock steps", () => {
  it("waiting message uses the index of the stage's terminal event, never a fixed number", () => {
    const idx = terminalIndexOf(events, "s05_metrics_engine")!;
    expect(waitingMessage(events, "s05_metrics_engine")).toBe(
      `Waiting for Stage 05 (Deterministic Metrics Engine) — drag scrubber forward to step ${idx + 1} to unlock.`,
    );
    expect(unlockIndexOf(events, "metrics_ledger")).toBe(idx);
  });

  it("says the stage has not finished when its event has not arrived", () => {
    expect(waitingMessage(events.slice(0, 3), "s12_verification_gate")).toMatch(/has not finished yet/);
  });
});

describe("resolveToken", () => {
  const final = projectStateAt(events, events.length - 1);
  it("resolves every [[token]] cited in the final prose", () => {
    const prose = JSON.stringify([final.summary, final.insights, final.market_positioning_prose, final.news_events]);
    const tokens = [...prose.matchAll(/\[\[([a-z0-9_]+)\]\]/g)].map((m) => m[1]);
    expect(tokens.length).toBeGreaterThan(5);
    for (const t of new Set(tokens)) expect(resolveToken(final, t), t).not.toBeNull();
  });

  it("a metric token carries its formula and first input's evidence", () => {
    const r = resolveToken(final, "met_rev_yoy_fy26")!;
    expect(r.metric?.metric_key).toBe("rev_yoy");
    expect(r.evidence?.evidence_id).toBe(r.metric?.inputs[0].evidence_id);
  });
});
