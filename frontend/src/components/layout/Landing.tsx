import { ArrowRight, HelpCircle, Moon, Sun } from "lucide-react";
import { useRunStore } from "../../store/useRunStore";

const shortName = (name: string) => name.replace(/\s*\(.*?\)\s*$/, "");

/** First screen: the wordmark, one company picker and one button. Everything else waits until a run starts. */
export default function Landing({ onRun, liveAvailable }: { onRun: () => void; liveAvailable: boolean }) {
  const s = useRunStore();
  const dark = s.theme === "dark" || (s.theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  const ready = s.mode === "replay" ? !!s.selectedReplayId : !!s.query.trim();

  return (
    <div className="flex min-h-screen flex-col">
      <div className="flex justify-end gap-1 p-4">
        <button type="button" className="btn-ghost !px-2" aria-label="Guided tour" title="Guided tour" onClick={() => s.setTourStep(0)}>
          <HelpCircle size={18} />
        </button>
        <button type="button" className="btn-ghost !px-2" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
                onClick={() => s.setTheme(dark ? "light" : "dark")}>
          {dark ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </div>

      <main className="flex flex-1 flex-col items-center justify-center px-4 pb-24">
        <h1 className="flex items-center gap-3 !text-[56px] !font-extrabold md:!text-[72px]">
          <span aria-hidden="true" className="inline-block h-4 w-4 rounded-full bg-ac shadow-[0_0_24px_var(--ac)] md:h-5 md:w-5" />
          Atlas
        </h1>
        <p className="mb-8 mt-3 text-center text-[17px] mu">Research any company in minutes. Every number checked against the filings.</p>

        <div className="seg mb-4" role="group" aria-label="Run mode">
          <button type="button" className="btn-ghost sm" aria-pressed={s.mode === "replay"} onClick={() => s.setMode("replay")}>Replay</button>
          <button type="button" className="btn-ghost sm" aria-pressed={s.mode === "live"} onClick={() => s.setMode("live")}
                  disabled={!liveAvailable} title={liveAvailable ? "Run the pipeline live" : "Live mode is not configured on this server"}>
            Live
          </button>
        </div>

        <form className="card !mb-0 flex w-full max-w-[640px] items-center gap-2 !rounded-full !p-2 !pl-6"
              onSubmit={(e) => { e.preventDefault(); if (ready) onRun(); }}>
          {s.mode === "replay" ? (
            <select aria-label="Replay company" className="min-w-0 flex-1 cursor-pointer border-0 bg-transparent py-3 text-[17px] outline-none"
                    value={s.selectedReplayId ?? ""} onChange={(e) => s.selectReplay(e.target.value)}>
              {s.replays.length === 0 && <option value="">No replays available</option>}
              {s.replays.map((r) => (
                <option key={r.replay_id} value={r.replay_id}>
                  {r.company_name}{r.ticker ? ` (${r.ticker})` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input aria-label="Company to research" className="min-w-0 flex-1 border-0 bg-transparent py-3 text-[17px] outline-none"
                   value={s.query} onChange={(e) => s.setQuery(e.target.value)} placeholder="Company name or ticker" autoFocus />
          )}
          <button type="submit" disabled={!ready}
                  className="inline-flex shrink-0 cursor-pointer items-center gap-2 rounded-full border-0 bg-ac px-5 py-3 font-semibold text-[var(--tab-on-tx)] disabled:opacity-40">
            Run research <ArrowRight size={18} />
          </button>
        </form>

        {s.mode === "replay" && s.replays.length > 1 && (
          <div className="mt-6 flex max-w-[640px] flex-wrap justify-center gap-2">
            {s.replays.map((r) => (
              <button key={r.replay_id} type="button" className="pill cursor-pointer border-0 !px-4 !py-1.5"
                      onClick={() => { s.selectReplay(r.replay_id); onRun(); }}>
                {shortName(r.company_name)}
              </button>
            ))}
          </div>
        )}
      </main>

      <footer className="pb-6 text-center sm mu">GenAI reads, reasons and writes. Code computes and checks every number.</footer>
    </div>
  );
}
