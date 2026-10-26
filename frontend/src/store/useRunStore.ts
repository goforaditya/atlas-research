import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ReplayInfo, ResearchRunState, RunMode, StageEvent, StageId } from "../types/atlas";
import { emptyState, projectStateAt, type RunBase } from "../utils/stateProjector";

export type TabId = "sum" | "fin" | "news" | "sen" | "mkt" | "ins" | "ai" | "met";
export type Speed = 1 | 2 | 5 | "instant";
export type Theme = "light" | "dark" | "system";
export type RunPhase = "idle" | "starting" | "streaming" | "completed" | "error";

export interface RunStoreState {
  runId: string | null;
  mode: RunMode;
  explainMode: boolean;
  activeTab: TabId;
  events: StageEvent[];
  scrubberIndex: number;
  isPlayingScrubber: boolean;
  replaySpeed: Speed;
  theme: Theme;
  selectedStageId: StageId | null;
  activeEvidenceId: string | null;
  tourStep: number | null;
  projectedState: ResearchRunState;

  // app-level state
  replays: ReplayInfo[];
  selectedReplayId: string | null;
  query: string;
  phase: RunPhase;
  errorMessage: string | null;
  base: RunBase | null;
  synthetic: boolean;
  unconfirmed: boolean;
}

export interface RunStoreActions {
  setReplays: (r: ReplayInfo[]) => void;
  selectReplay: (id: string) => void;
  setQuery: (q: string) => void;
  setMode: (m: RunMode) => void;
  beginRun: (base: RunBase, synthetic: boolean, unconfirmed?: boolean) => void;
  appendEvent: (e: StageEvent) => void;
  completeRun: () => void;
  failRun: (message: string) => void;
  setScrubber: (i: number) => void;
  stepScrubber: (delta: number) => void;
  setPlaying: (p: boolean) => void;
  setSpeed: (s: Speed) => void;
  setExplainMode: (on: boolean) => void;
  setActiveTab: (t: TabId) => void;
  setTheme: (t: Theme) => void;
  selectStage: (id: StageId | null) => void;
  openEvidence: (id: string | null) => void;
  setTourStep: (s: number | null) => void;
  reset: () => void;
}

const initial: RunStoreState = {
  runId: null,
  mode: "replay",
  explainMode: false,
  activeTab: "sum",
  events: [],
  scrubberIndex: -1,
  isPlayingScrubber: false,
  replaySpeed: 5,
  theme: "dark",
  selectedStageId: null,
  activeEvidenceId: null,
  tourStep: null,
  projectedState: emptyState(),
  replays: [],
  selectedReplayId: null,
  query: "One97 Communications Ltd (Paytm)",
  phase: "idle",
  errorMessage: null,
  base: null,
  synthetic: false,
  unconfirmed: false,
};

function project(s: RunStoreState): ResearchRunState {
  return projectStateAt(s.events, s.scrubberIndex, s.base ?? undefined);
}

export const useRunStore = create<RunStoreState & RunStoreActions>()(
  immer((set) => ({
    ...initial,
    setReplays: (r) =>
      set((s) => {
        s.replays = r;
        if (!s.selectedReplayId && r.length) {
          // Landing default (Section 14, decision 6): Paytm when available, otherwise the first replay.
          const pick = r.find((x) => x.replay_id === "paytm_nse") ?? r[0];
          s.selectedReplayId = pick.replay_id;
          s.query = pick.company_name;
        }
      }),
    selectReplay: (id) =>
      set((s) => {
        s.selectedReplayId = id;
        const r = s.replays.find((x) => x.replay_id === id);
        if (r) s.query = r.company_name;
      }),
    setQuery: (q) =>
      set((s) => {
        s.query = q;
      }),
    setMode: (m) =>
      set((s) => {
        s.mode = m;
      }),
    beginRun: (base, synthetic, unconfirmed = false) =>
      set((s) => {
        s.unconfirmed = unconfirmed;
        s.runId = base.run_id;
        s.base = base;
        s.synthetic = synthetic;
        s.events = [];
        s.scrubberIndex = -1;
        s.isPlayingScrubber = false;
        s.selectedStageId = null;
        s.activeEvidenceId = null;
        s.phase = "streaming";
        s.errorMessage = null;
        s.activeTab = "sum";
        s.projectedState = emptyState(base);
      }),
    appendEvent: (e) =>
      set((s) => {
        if (s.events.some((x) => x.event_seq === e.event_seq)) return;
        const following = s.scrubberIndex >= s.events.length - 1;
        s.events.push(e);
        if (following) s.scrubberIndex = s.events.length - 1;
        s.projectedState = project(s as RunStoreState);
      }),
    completeRun: () =>
      set((s) => {
        s.phase = "completed";
      }),
    failRun: (message) =>
      set((s) => {
        s.phase = "error";
        s.errorMessage = message;
      }),
    setScrubber: (i) =>
      set((s) => {
        s.scrubberIndex = Math.max(-1, Math.min(i, s.events.length - 1));
        s.projectedState = project(s as RunStoreState);
      }),
    stepScrubber: (delta) =>
      set((s) => {
        s.scrubberIndex = Math.max(0, Math.min(s.scrubberIndex + delta, s.events.length - 1));
        s.projectedState = project(s as RunStoreState);
      }),
    setPlaying: (p) =>
      set((s) => {
        s.isPlayingScrubber = p;
      }),
    setSpeed: (sp) =>
      set((s) => {
        s.replaySpeed = sp;
      }),
    setExplainMode: (on) =>
      set((s) => {
        s.explainMode = on;
        if (!on) s.selectedStageId = null;
      }),
    setActiveTab: (t) =>
      set((s) => {
        s.activeTab = t;
      }),
    setTheme: (t) =>
      set((s) => {
        s.theme = t;
      }),
    selectStage: (id) =>
      set((s) => {
        s.selectedStageId = s.selectedStageId === id ? null : id;
      }),
    openEvidence: (id) =>
      set((s) => {
        s.activeEvidenceId = id;
      }),
    setTourStep: (st) =>
      set((s) => {
        s.tourStep = st;
      }),
    reset: () => set(() => ({ ...initial })),
  })),
);
