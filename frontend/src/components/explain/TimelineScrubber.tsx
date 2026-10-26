import { useEffect, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import type { ResearchRunState, StageEvent, StageId } from "../../types/atlas";
import { STAGE_LABELS, STAGE_ORDER } from "../../types/atlas";
import { useRunStore, type Speed } from "../../store/useRunStore";
import { terminalIndexOf, unlockIndexOf } from "../../utils/stateProjector";
import { clock } from "../../utils/formatters";

const SPEEDS: Speed[] = [1, 2, 5, "instant"];

/** "Waiting for Stage 05 (…) — drag scrubber forward to step 10 to unlock." The step comes from the events array. */
export function waitingMessage(events: StageEvent[], stageId: StageId, key?: keyof ResearchRunState): string {
  const meta = STAGE_LABELS[stageId];
  const idx = (key ? unlockIndexOf(events, key) : null) ?? terminalIndexOf(events, stageId);
  const head = `Waiting for Stage ${meta.num} (${meta.title})`;
  return idx === null ? `${head} — it has not finished yet.` : `${head} — drag scrubber forward to step ${idx + 1} to unlock.`;
}

/** Progressive skeleton: renders children once the state key has been emitted at the scrubber position. */
export function WaitingFor({ stageId, stateKey, ready, children }: {
  stageId: StageId;
  stateKey?: keyof ResearchRunState;
  ready: boolean;
  children: ReactNode;
}) {
  const events = useRunStore((s) => s.events);
  const phase = useRunStore((s) => s.phase);
  if (ready) return <>{children}</>;
  if (phase === "idle") return <div className="skeleton">Start a run to fill this section.</div>;
  return <div className="skeleton" role="status">{waitingMessage(events, stageId, stateKey)}</div>;
}

/** B.3: play/pause, step and speed controls plus a range slider over events[]. */
export default function TimelineScrubber() {
  const events = useRunStore((s) => s.events);
  const index = useRunStore((s) => s.scrubberIndex);
  const playing = useRunStore((s) => s.isPlayingScrubber);
  const speed = useRunStore((s) => s.replaySpeed);
  const phase = useRunStore((s) => s.phase);
  const { setScrubber, stepScrubber, setPlaying, setSpeed } = useRunStore.getState();

  const last = events.length - 1;
  const atEnd = index >= last;

  // Local playback of already-received events, paced by their recorded gaps.
  useEffect(() => {
    if (!playing) return;
    if (atEnd) {
      setPlaying(false);
      return;
    }
    const gap = Math.max(0, events[index + 1].elapsed_ms - (events[index]?.elapsed_ms ?? 0));
    const ms = speed === "instant" ? 0 : Math.min(1500, gap / speed);
    const t = window.setTimeout(() => stepScrubber(1), ms);
    return () => window.clearTimeout(t);
  }, [playing, index, atEnd, events, speed, stepScrubber, setPlaying]);

  const togglePlay = () => {
    if (!playing && atEnd) setScrubber(0);
    setPlaying(!playing);
  };

  const ticks = STAGE_ORDER.map((id) => terminalIndexOf(events, id)).filter((i): i is number => i !== null);
  const now = events[index]?.elapsed_ms ?? 0;
  const total = events[last]?.elapsed_ms ?? 0;

  return (
    <div data-tour-id="timeline-scrubber" className="flex flex-wrap items-center gap-2 border-t border-ln bg-sf-elev px-3 py-2">
      <button type="button" className="btn-ghost" onClick={() => stepScrubber(-1)} disabled={index <= 0} aria-label="Step back">
        <ChevronLeft size={16} />
      </button>
      <button type="button" className="btn-ghost" onClick={togglePlay} disabled={events.length === 0}
              aria-label={playing ? "Pause" : "Play"}>
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <button type="button" className="btn-ghost" onClick={() => stepScrubber(1)} disabled={atEnd} aria-label="Step forward">
        <ChevronRight size={16} />
      </button>
      <div className="relative min-w-[160px] flex-1">
        <input type="range" className="w-full accent-[var(--ac)]" min={0} max={Math.max(0, last)} step={1}
               value={Math.max(0, index)} disabled={events.length === 0} list="scrubber-ticks"
               aria-label="Timeline position" aria-valuetext={`Event ${index + 1} of ${events.length}`}
               onChange={(e) => { setPlaying(false); setScrubber(Number(e.target.value)); }} />
        <datalist id="scrubber-ticks">
          {ticks.map((t) => <option key={t} value={t} />)}
        </datalist>
      </div>
      <div className="flex gap-1" role="group" aria-label="Replay speed">
        {SPEEDS.map((s) => (
          <button key={String(s)} type="button" className="btn-ghost !px-2 !py-1 sm" aria-pressed={speed === s}
                  onClick={() => setSpeed(s)}>
            {s === "instant" ? "Instant" : `${s}x`}
          </button>
        ))}
      </div>
      <span className="sm mu whitespace-nowrap" aria-live="off">
        {clock(now)} / {clock(total)} · Event {Math.max(0, index + 1)} of {events.length}
        {phase === "streaming" ? " · streaming" : ""}
      </span>
    </div>
  );
}
