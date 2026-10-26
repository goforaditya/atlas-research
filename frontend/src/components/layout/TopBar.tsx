import { Download, Moon, Play, Sun, Compass } from "lucide-react";
import { useRunStore } from "../../store/useRunStore";
import { exportUrl } from "../../api/client";

/** Run input, Live/Replay selector, Explain/Clean toggle, theme toggle, tour and export (B.10). */
export default function TopBar({ onRun, liveAvailable }: { onRun: () => void; liveAvailable: boolean }) {
  const s = useRunStore();
  const busy = s.phase === "starting" || s.phase === "streaming";
  const dark = s.theme === "dark" || (s.theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);

  return (
    <header className="sticky top-0 z-30 border-b border-ln bg-sf">
      <div className={`mx-auto flex flex-wrap items-center gap-2 px-4 py-2 md:px-6 ${s.explainMode ? "max-w-explain" : "max-w-report"}`}>
        <div className="mr-auto">
          <div className="font-serif text-h3 leading-none">Atlas Research</div>
          <div className="text-micro text-mu">Every number computed in code, every claim cited</div>
        </div>

        <div data-tour-id="mode-selector" className="order-last flex min-w-0 basis-full flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-ln" role="group" aria-label="Run mode">
            <button type="button" className="btn-ghost !border-0" aria-pressed={s.mode === "replay"}
                    onClick={() => s.setMode("replay")}>Replay</button>
            <button type="button" className="btn-ghost !border-0" aria-pressed={s.mode === "live"}
                    onClick={() => s.setMode("live")} disabled={!liveAvailable}
                    title={liveAvailable ? "Run the pipeline live" : "Live mode is not configured on this server"}>
              Live
            </button>
          </div>
          {s.mode === "replay" ? (
            <select aria-label="Replay company" className="w-0 min-w-0 flex-1 rounded-lg border border-ln bg-sf px-2 py-[7px] md:max-w-[480px]"
                    value={s.selectedReplayId ?? ""} onChange={(e) => s.selectReplay(e.target.value)}>
              {s.replays.length === 0 && <option value="">No replays available</option>}
              {s.replays.map((r) => (
                <option key={r.replay_id} value={r.replay_id}>
                  {r.company_name}{r.ticker ? ` (${r.ticker})` : ""}{r.synthetic && !/synthetic/i.test(r.company_name) ? " · synthetic sample" : ""}
                </option>
              ))}
            </select>
          ) : (
            <input aria-label="Company to research" className="w-0 min-w-0 flex-1 rounded-lg border border-ln bg-sf px-2 py-[7px] md:max-w-[480px]"
                   value={s.query} onChange={(e) => s.setQuery(e.target.value)} placeholder="Company name or ticker"
                   onKeyDown={(e) => e.key === "Enter" && !busy && onRun()} />
          )}
          <button type="button" className="btn inline-flex w-full items-center justify-center gap-1 md:w-auto" onClick={onRun}
                  disabled={busy || (s.mode === "replay" ? !s.selectedReplayId : !s.query.trim())}>
            <Play size={14} /> {busy ? "Running…" : "Run research"}
          </button>
        </div>

        <div data-tour-id="explain-clean-toggle" className="flex rounded-lg border border-ln" role="group" aria-label="Report view">
          <button type="button" className="btn-ghost !border-0" aria-pressed={s.explainMode} onClick={() => s.setExplainMode(true)}>
            Explain<span className="hidden sm:inline"> Mode{s.explainMode ? " (On)" : ""}</span>
          </button>
          <button type="button" className="btn-ghost !border-0" aria-pressed={!s.explainMode} onClick={() => s.setExplainMode(false)}>
            Clean<span className="hidden sm:inline"> Report</span>
          </button>
        </div>

        {!s.explainMode && s.runId && s.phase === "completed" && (
          <div className="flex gap-1">
            <a className="btn-ghost inline-flex items-center gap-1 no-underline" href={exportUrl(s.runId, "pdf")}>
              <Download size={14} /> Export PDF
            </a>
            <a className="btn-ghost inline-flex items-center gap-1 no-underline" href={exportUrl(s.runId, "md")}>
              <Download size={14} /> Export Markdown
            </a>
          </div>
        )}

        <button type="button" className="btn-ghost inline-flex items-center gap-1" onClick={() => s.setTourStep(0)}>
          <Compass size={14} /> Guided Tour (8 steps)
        </button>
        <button type="button" className="btn-ghost" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
                onClick={() => s.setTheme(dark ? "light" : "dark")}>
          {dark ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
