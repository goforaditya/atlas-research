import { useCallback, useEffect, useRef, useState, type ComponentType } from "react";
import { ApiError, createRun, fetchHealth, fetchReplays, subscribe } from "./api/client";
import { useRunStore, type TabId } from "./store/useRunStore";
import TopBar from "./components/layout/TopBar";
import TabBar, { TABS } from "./components/layout/TabBar";
import PipelineStageTracker from "./components/explain/PipelineStageTracker";
import EvidenceDrawer from "./components/explain/EvidenceDrawer";
import SummaryTab from "./components/tabs/SummaryTab";

const TAB_COMPONENTS: Partial<Record<TabId, ComponentType>> = {
  sum: SummaryTab,
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

export default function App() {
  const s = useRunStore();
  const [liveAvailable, setLiveAvailable] = useState(false);
  const closeStream = useRef<(() => void) | null>(null);
  useThemeEffect();

  useEffect(() => {
    fetchReplays().then(useRunStore.getState().setReplays).catch(() => useRunStore.getState().failRun("Could not load replays from the server."));
    fetchHealth().then((h) => setLiveAvailable(h.llm_configured)).catch(() => setLiveAvailable(false));
    return () => closeStream.current?.();
  }, []);

  const run = useCallback(async () => {
    const st = useRunStore.getState();
    closeStream.current?.();
    const replay = st.replays.find((r) => r.replay_id === st.selectedReplayId) ?? null;
    useRunStore.setState({ phase: "starting", errorMessage: null });
    try {
      const speed = st.replaySpeed;
      const res = await createRun({
        company_query: st.mode === "replay" && replay ? replay.company_query : st.query.trim(),
        mode: st.mode,
        replay_id: st.mode === "replay" ? st.selectedReplayId : null,
        replay_speed: speed === "instant" ? 1 : speed,
      });
      const url = speed === "instant" ? res.stream_url.replace(/speed=[^&]*/, "speed=instant") : res.stream_url;
      const store = useRunStore.getState();
      store.beginRun({ run_id: res.run_id, mode: res.mode, company_query: res.company_query, started_at_ms: Date.now() },
                     st.mode === "replay" && !!replay?.synthetic);
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

  const Active = TAB_COMPONENTS[s.activeTab];
  const width = s.explainMode ? "max-w-explain" : "max-w-report";

  return (
    <div className="min-h-screen">
      <TopBar onRun={run} liveAvailable={liveAvailable} />
      <main className={`mx-auto px-4 py-3 md:px-6 ${width}`}>
        {s.synthetic && (
          <div className="banner" role="note">
            Synthetic sample: {s.projectedState.company_query || "this company"} is made up to demonstrate the pipeline.
            None of its figures are real.
          </div>
        )}
        {s.phase === "error" && s.errorMessage && <div className="rf sm" role="alert">{s.errorMessage}</div>}
        {s.explainMode && <PipelineStageTracker />}
        {s.events.length > 0 && (
          <div className="mb-1 sm mu" aria-live="polite">{s.events[Math.max(0, s.scrubberIndex)]?.log_line}</div>
        )}
        <TabBar />
        {TABS.map((t) => (
          <div key={t.id} role="tabpanel" id={`panel-${t.id}`} aria-labelledby={`tab-${t.id}`} hidden={s.activeTab !== t.id}>
            {s.activeTab === t.id && (Active ? <Active /> : <div className="skeleton">This section arrives in a later build step.</div>)}
          </div>
        ))}
      </main>
      <EvidenceDrawer />
    </div>
  );
}
