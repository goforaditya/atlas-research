import { Download, HelpCircle, Moon, Sun } from "lucide-react";
import { useRunStore } from "../../store/useRunStore";
import { exportUrl } from "../../api/client";
import { emptyState } from "../../utils/stateProjector";

/** One quiet bar: company picker and run button, then the workings toggle, export, tour and theme (B.10). */
export default function TopBar({ onRun, liveAvailable }: { onRun: () => void; liveAvailable: boolean }) {
  const s = useRunStore();
  const busy = s.phase === "starting" || s.phase === "streaming";
  const dark = s.theme === "dark" || (s.theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);

  return (
    <header className="sticky top-0 z-30 border-b border-ln bg-[color-mix(in_srgb,var(--sf)_80%,transparent)] backdrop-blur-xl">
      <div className={`mx-auto flex flex-wrap items-center gap-x-1 gap-y-2 px-4 py-3 sm:gap-x-3 md:px-8 ${s.explainMode ? "max-w-explain" : "max-w-report"}`}>
        <button type="button" className="mr-auto flex cursor-pointer items-center gap-2 border-0 bg-transparent p-0 text-[19px] font-extrabold tracking-tight"
                title="Back to the start" disabled={busy}
                onClick={() => useRunStore.setState({ phase: "idle", events: [], runId: null, scrubberIndex: -1, projectedState: emptyState(),
                                                     activeTab: "sum", synthetic: false, unconfirmed: false, errorMessage: null, base: null })}>
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-full bg-ac shadow-[0_0_12px_var(--ac)]" />Atlas
        </button>

        <div data-tour-id="mode-selector" className="order-last flex min-w-0 basis-full flex-wrap items-center gap-2 md:order-none md:basis-auto md:flex-1 md:flex-nowrap">
          <div className="seg" role="group" aria-label="Run mode">
            <button type="button" className="btn-ghost" aria-pressed={s.mode === "replay"}
                    onClick={() => s.setMode("replay")}>Replay</button>
            <button type="button" className="btn-ghost" aria-pressed={s.mode === "live"}
                    onClick={() => s.setMode("live")} disabled={!liveAvailable}
                    title={liveAvailable ? "Run the pipeline live" : "Live mode is not configured on this server"}>
              Live
            </button>
          </div>
          {s.mode === "replay" ? (
            <select aria-label="Replay company" className="field w-0 min-w-0 flex-1 md:min-w-[200px] md:max-w-[420px]"
                    value={s.selectedReplayId ?? ""} onChange={(e) => s.selectReplay(e.target.value)}>
              {s.replays.length === 0 && <option value="">No replays available</option>}
              {s.replays.map((r) => (
                <option key={r.replay_id} value={r.replay_id}>
                  {r.company_name}{r.ticker ? ` (${r.ticker})` : ""}{r.synthetic && !/synthetic/i.test(r.company_name) ? " · synthetic sample" : ""}
                </option>
              ))}
            </select>
          ) : (
            <input aria-label="Company to research" className="field w-0 min-w-0 flex-1 md:min-w-[200px] md:max-w-[420px]"
                   value={s.query} onChange={(e) => s.setQuery(e.target.value)} placeholder="Company name or ticker"
                   onKeyDown={(e) => e.key === "Enter" && !busy && onRun()} />
          )}
          <button type="button" className="btn w-full md:w-auto" onClick={onRun}
                  disabled={busy || (s.mode === "replay" ? !s.selectedReplayId : !s.query.trim())}>
            {busy ? "Researching…" : "Run research"}
          </button>
        </div>

        <div className="flex shrink-0 items-center gap-0 sm:gap-1">
          <button type="button" data-tour-id="explain-clean-toggle" className="btn-ghost sm !px-2.5 sm:!px-3.5" aria-pressed={s.explainMode}
                  onClick={() => s.setExplainMode(!s.explainMode)}>
            How it works
          </button>
          {s.runId && s.phase === "completed" && (
            <>
              <a className="btn-ghost sm inline-flex items-center gap-1 !px-2.5 no-underline" href={exportUrl(s.runId, "pdf")}
                 aria-label="Export PDF" title="Export PDF">
                <Download size={15} />PDF
              </a>
              <a className="btn-ghost sm !px-2.5 no-underline" href={exportUrl(s.runId, "md")} aria-label="Export Markdown" title="Export Markdown">
                MD
              </a>
            </>
          )}
          <button type="button" className="btn-ghost !px-2" aria-label="Guided tour" title="Guided tour" onClick={() => s.setTourStep(0)}>
            <HelpCircle size={18} />
          </button>
          <button type="button" className="btn-ghost !px-2" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
                  onClick={() => s.setTheme(dark ? "light" : "dark")}>
            {dark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </div>
    </header>
  );
}
