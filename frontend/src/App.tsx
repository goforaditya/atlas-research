import { useCallback, useEffect, useRef, useState, type ComponentType } from "react";
import { ApiError, createRun, fetchHealth, fetchReplays, subscribe } from "./api/client";
import { useRunStore, type Speed, type TabId } from "./store/useRunStore";
import TopBar from "./components/layout/TopBar";
import TabBar, { TABS } from "./components/layout/TabBar";
import PipelineStageTracker from "./components/explain/PipelineStageTracker";
import EvidenceDrawer from "./components/explain/EvidenceDrawer";
import GuidedTourOverlay from "./components/explain/GuidedTourOverlay";
import { STAGE_ORDER, type StageId } from "./types/atlas";
import { stageStatusesAt } from "./utils/stateProjector";
import SummaryTab from "./components/tabs/SummaryTab";
import FinancialsTab from "./components/tabs/FinancialsTab";
import NewsTab from "./components/tabs/NewsTab";
import SentimentTab from "./components/tabs/SentimentTab";
import MarketTab from "./components/tabs/MarketTab";
import InsightsTab from "./components/tabs/InsightsTab";
import MethodTab from "./components/tabs/MethodTab";
import AiTab from "./components/tabs/AiTab";
import Landing from "./components/layout/Landing";

const TAB_COMPONENTS: Record<TabId, ComponentType> = {
  sum: SummaryTab,
  fin: FinancialsTab,
  news: NewsTab,
  sen: SentimentTab,
  mkt: MarketTab,
  ins: InsightsTab,
  ai: AiTab,
  met: MethodTab,
};

function useThemeEffect() {
  const theme = useRunStore((s) => s.theme);
  const setTheme = useRunStore((s) => s.setTheme);
  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    try {
      const t = localStorage.getItem("atlas_theme");
      if (t === "light" || t === "dark") setTheme(t);
    } catch {
      /* storage unavailable: follow the system theme */
    }
  }, [setTheme]);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      if (theme === "system") localStorage.removeItem("atlas_theme");
      else localStorage.setItem("atlas_theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
}

const PLAIN_STAGE: Record<StageId, string> = {
  s01_entity_resolution: "Identifying the company", s02_research_planning: "Planning the research",
  s03_filing_retrieval: "Reading the filings", s04_statement_extraction: "Extracting the statements",
  s05_metrics_engine: "Computing the metrics", s06_red_flag_engine: "Checking for red flags",
  s07_news_intelligence: "Scanning the news", s08_sentiment_filter: "Reading sentiment",
  s09_market_peers: "Comparing peers", s10_contradiction_resolver: "Resolving conflicting sources",
  s11_synthesis: "Writing the report", s12_verification_gate: "Verifying every number",
};

/** Clean view: one thin progress line while a run streams, instead of the stage tracker and scrubber. */
function QuietProgress() {
  const events = useRunStore((s) => s.events);
  const index = useRunStore((s) => s.scrubberIndex);
  const phase = useRunStore((s) => s.phase);
  if (phase !== "starting" && phase !== "streaming") return null;
  const statuses = stageStatusesAt(events, index);
  const done = STAGE_ORDER.filter((id) => ["completed", "warning", "failed"].includes(statuses[id]?.status)).length;
  const current = STAGE_ORDER.find((id) => statuses[id]?.status === "running");
  return (
    <div className="mb-6" aria-live="polite">
      <div className="mb-2 flex justify-between sm">
        <span>{current ? `${PLAIN_STAGE[current]}…` : "Starting…"}</span>
        <span className="mu">{done} of {STAGE_ORDER.length}</span>
      </div>
      <div className="bar"><i style={{ width: `${(done / STAGE_ORDER.length) * 100}%` }} /></div>
    </div>
  );
}

export default function App() {
  const s = useRunStore();
  const [liveAvailable, setLiveAvailable] = useState(false);
  const closeStream = useRef<(() => void) | null>(null);
  useThemeEffect();

  useEffect(() => {
    fetchReplays().then(useRunStore.getState().setReplays).catch(() => useRunStore.getState().failRun("Could not load replays from the server."));
    // ?speed=instant (or 1, 2, 5) presets the replay speed, for scripted demos and the browser tests.
    const sp = new URLSearchParams(window.location.search).get("speed");
    if (sp === "instant") useRunStore.getState().setSpeed("instant");
    else if (sp === "1" || sp === "2" || sp === "5") useRunStore.getState().setSpeed(Number(sp) as Speed);
    fetchHealth().then((h) => setLiveAvailable(h.llm_configured)).catch(() => setLiveAvailable(false));
    return () => closeStream.current?.();
  }, []);

  const run = useCallback(async (speedOverride?: Speed) => {
    const st = useRunStore.getState();
    closeStream.current?.();
    const replay = st.replays.find((r) => r.replay_id === st.selectedReplayId) ?? null;
    useRunStore.setState({ phase: "starting", errorMessage: null });
    try {
      const speed = speedOverride ?? st.replaySpeed;
      const res = await createRun({
        company_query: st.mode === "replay" && replay ? replay.company_query : st.query.trim(),
        mode: st.mode,
        replay_id: st.mode === "replay" ? st.selectedReplayId : null,
        replay_speed: speed === "instant" ? 1 : speed,
      });
      const url = speed === "instant" ? res.stream_url.replace(/speed=[^&]*/, "speed=instant") : res.stream_url;
      const store = useRunStore.getState();
      store.beginRun({ run_id: res.run_id, mode: res.mode, company_query: res.company_query, started_at_ms: Date.now() },
                     st.mode === "replay" && !!replay?.synthetic, st.mode === "replay" && !!replay?.unconfirmed);
      closeStream.current = subscribe(url, {
        onStarted: (p) => useRunStore.setState((x) => {
          x.base = { ...p };
        }),
        onEvent: (e) => useRunStore.getState().appendEvent(e),
        onCompleted: () => useRunStore.getState().completeRun(),
        onError: (p) => useRunStore.getState().failRun(p.message),
      });
    } catch (err) {
      const msg = err instanceof ApiError ? err.body.message : "Could not start the run.";
      useRunStore.getState().failRun(msg);
    }
  }, []);

  // The tour points at report sections, so a tour started before any run plays the selected replay instantly.
  useEffect(() => {
    if (s.tourStep !== null && s.phase === "idle" && s.mode === "replay" && s.selectedReplayId) void run("instant");
  }, [s.tourStep, s.phase, s.mode, s.selectedReplayId, run]);

  const Active = TAB_COMPONENTS[s.activeTab];
  const width = s.explainMode ? "max-w-explain" : "max-w-report";

  if (s.phase === "idle" && s.events.length === 0 && s.tourStep === null) {
    return <Landing onRun={() => void run()} liveAvailable={liveAvailable} />;
  }

  return (
    <div className="min-h-screen">
      <TopBar onRun={() => void run()} liveAvailable={liveAvailable} />
      <main className={`mx-auto px-4 pb-16 pt-6 md:px-8 md:pt-8 ${width}`}>
        {s.synthetic && (
          <div className="banner" role="note">
            Synthetic sample: {s.projectedState.company_query || "this company"} is made up to demonstrate the pipeline.
            None of its figures are real.
          </div>
        )}
        {s.unconfirmed && (
          <div className="banner" role="note">
            Unconfirmed seed figures: this partial replay uses numbers from the build specification's seed table
            that have not yet been checked against the filings. Most sections are stated gaps.
          </div>
        )}
        {s.phase === "error" && s.errorMessage && <div className="rf sm" role="alert">{s.errorMessage}</div>}
        {s.explainMode && <PipelineStageTracker />}
        {s.explainMode && s.events.length > 0 && (
          <div className="mb-1 sm mu" aria-live="polite">{s.events[Math.max(0, s.scrubberIndex)]?.log_line}</div>
        )}
        {!s.explainMode && <QuietProgress />}
        <TabBar />
        {TABS.map((t) => (
          <div key={t.id} role="tabpanel" id={`panel-${t.id}`} aria-labelledby={`tab-${t.id}`} hidden={s.activeTab !== t.id}>
            {s.activeTab === t.id && <Active />}
          </div>
        ))}
      </main>
      <EvidenceDrawer />
      <GuidedTourOverlay />
    </div>
  );
}
