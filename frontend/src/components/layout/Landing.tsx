import { ArrowRight, HelpCircle, Moon, ShieldCheck, Sun } from "lucide-react";
import { useRunStore } from "../../store/useRunStore";
import CompanyPicker, { shortName } from "./CompanyPicker";

/** First screen: the wordmark, one company search and one button. Everything else waits until a run starts. */
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
        <h1 className="flex items-center gap-3 !text-[52px] !font-bold md:!text-[68px]">
          <span aria-hidden="true" className="brand-mark h-9 w-9 md:h-11 md:w-11" />
          Atlas
        </h1>
        <p className="mb-8 mt-3 max-w-[520px] text-center text-[17px] mu">
          Research any company in minutes. Every number checked against the filings.
        </p>

        <div className="seg mb-5" role="group" aria-label="Run mode">
          <button type="button" className="btn-ghost sm" aria-pressed={s.mode === "replay"} onClick={() => s.setMode("replay")}>Replay</button>
          <button type="button" className="btn-ghost sm" aria-pressed={s.mode === "live"} onClick={() => s.setMode("live")}
                  disabled={!liveAvailable} title={liveAvailable ? "Run the pipeline live" : "Live mode is not configured on this server"}>
            Live
          </button>
        </div>

        <div className="flex w-full max-w-[640px] flex-col gap-3 sm:flex-row sm:items-stretch">
          <CompanyPicker onRun={onRun} size="hero" />
          <button type="button" disabled={!ready} onClick={onRun}
                  className="btn btn-accent inline-flex shrink-0 items-center justify-center gap-2 !px-6 !py-3.5 text-[16px]">
            Run research <ArrowRight size={18} />
          </button>
        </div>

        {s.mode === "replay" && s.replays.length > 1 && (
          <div className="mt-6 flex max-w-[640px] flex-wrap justify-center gap-2">
            <span className="w-full pb-1 text-center label">Recorded runs</span>
            {s.replays.map((r) => (
              <button key={r.replay_id} type="button" className="chip-btn"
                      onClick={() => { s.selectReplay(r.replay_id); onRun(); }}>
                {shortName(r.company_name)}
              </button>
            ))}
          </div>
        )}
      </main>

      <footer className="flex items-center justify-center gap-2 pb-6 text-center sm mu">
        <ShieldCheck size={15} aria-hidden="true" />
        GenAI reads, reasons and writes. Code computes and checks every number.
      </footer>
    </div>
  );
}
