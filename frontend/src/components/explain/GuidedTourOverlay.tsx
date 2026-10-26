import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useRunStore, type TabId } from "../../store/useRunStore";

export interface TourStep {
  target: string;
  title: string;
  body: string;
  tab?: TabId;
}

export const TOUR_STEPS: TourStep[] = [
  { target: "mode-selector", title: "Live vs. Guaranteed Replay",
    body: "Run live against public registries or replay pre-cached companies with realistic timing. Replays use the same stream and UI as live runs." },
  { target: "pipeline-tracker", title: "12-Stage Glass-Box Pipeline",
    body: "Click any stage to inspect its tool arguments, execution time and what Python code did vs. what the LLM did. Nothing happens off-screen." },
  { target: "timeline-scrubber", title: "Step-by-Step Timeline Scrubber",
    body: "Drag the slider back and forth to watch the report assemble as each stage completes. Sections that are not ready yet say which step unlocks them." },
  { target: "verdict-badges", title: "Epistemic Claim Labelling", tab: "sum",
    body: "Every synthesised claim is badged as Fact, Inference or Hypothesis. The counts are made by code after the verification gate, not by the model." },
  { target: "red-flags-panel", title: "Deterministic Red-Flag Rules", tab: "fin",
    body: "Accounting anomalies fire from declarative YAML thresholds evaluated in pure Python. They are never the model's opinion." },
  { target: "calculation-ledger", title: "Zero-LLM Calculation Ledger", tab: "fin",
    body: "Hover any metric for the Show-the-Math popover, or click a figure to open the source page beside the extracted value." },
  { target: "signal-noise-view", title: "Signal vs. Noise Sentiment Filter", tab: "sen",
    body: "Posts are stripped of bots and duplicates in code, and only marked Signal when a filing or news event corroborates them." },
  { target: "explain-clean-toggle", title: "Explain Mode vs. Clean Report Export",
    body: "Switch to Clean Report for a distraction-free reading layout, then export it as PDF or Markdown with numbered source footnotes." },
];

const TOUR_KEY = "atlas_tour_completed";
const PAD = 6;
const CARD_W = 360;

export function tourCompleted(): boolean | null {
  try {
    return localStorage.getItem(TOUR_KEY) === "true";
  } catch {
    return null; // storage unavailable: do not auto-launch
  }
}

function markCompleted() {
  try {
    localStorage.setItem(TOUR_KEY, "true");
  } catch {
    /* ignore */
  }
}

type Rect = { x: number; y: number; w: number; h: number };

/** B.1: full-viewport SVG mask with a cutout around the step's data-tour-id target and a floating card. */
export default function GuidedTourOverlay() {
  const step = useRunStore((s) => s.tourStep);
  const setStep = useRunStore((s) => s.setTourStep);
  const setTab = useRunStore((s) => s.setActiveTab);
  const setExplain = useRunStore((s) => s.setExplainMode);
  const eventsCount = useRunStore((s) => s.events.length);
  const [rect, setRect] = useState<Rect | null>(null);
  const [vp, setVp] = useState({ w: window.innerWidth, h: window.innerHeight });
  const nextRef = useRef<HTMLButtonElement>(null);
  const current = step !== null ? TOUR_STEPS[step] : null;

  const close = useCallback(() => {
    markCompleted();
    setStep(null);
  }, [setStep]);

  // Bring the target on screen: right tab, Explain mode on, then scroll it into view.
  useEffect(() => {
    if (!current) return;
    setExplain(true);
    if (current.tab) setTab(current.tab);
    const t = window.setTimeout(() => {
      const el = document.querySelector(`[data-tour-id="${current.target}"]`);
      const tall = !!el && el.getBoundingClientRect().height > window.innerHeight * 0.5;
      el?.scrollIntoView?.({ block: tall ? "start" : "center", behavior: "auto" });
    }, 30);
    return () => window.clearTimeout(t);
  }, [current, setTab, setExplain]);

  const measure = useCallback(() => {
    setVp({ w: window.innerWidth, h: window.innerHeight });
    if (!current) return setRect(null);
    const el = document.querySelector(`[data-tour-id="${current.target}"]`);
    if (!el) return setRect(null);
    const r = el.getBoundingClientRect();
    setRect({ x: r.left - PAD, y: r.top - PAD, w: r.width + PAD * 2, h: r.height + PAD * 2 });
  }, [current]);

  useLayoutEffect(() => {
    if (!current) return;
    measure();
    const t = window.setTimeout(measure, 80);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [current, measure, eventsCount]);

  useEffect(() => {
    if (step === null) return;
    nextRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") setStep(step < TOUR_STEPS.length - 1 ? step + 1 : step);
      else if (e.key === "ArrowLeft") setStep(Math.max(0, step - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, setStep, close]);

  if (step === null || !current) return null;

  const cardW = Math.min(CARD_W, vp.w - 24);
  let left = vp.w / 2 - cardW / 2;
  let top = vp.h / 2 - 100;
  if (rect && rect.h > vp.h * 0.5) {
    // Tall targets: keep the card in the bottom corner so it never covers the start of the section.
    left = vp.w - cardW - 16;
    top = vp.h - 230;
  } else if (rect) {
    left = Math.min(Math.max(12, rect.x), vp.w - cardW - 12);
    const below = rect.y + rect.h + 10;
    top = below + 200 < vp.h ? below : Math.max(12, rect.y - 210);
  }
  const last = step === TOUR_STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      <svg className="absolute inset-0 h-full w-full" aria-hidden="true">
        <defs>
          <mask id="tour-mask">
            <rect width="100%" height="100%" fill="white" />
            {rect && <rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={10} fill="black" />}
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="rgba(14, 20, 32, 0.68)" mask="url(#tour-mask)" />
        {rect && <rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={10} fill="none" stroke="var(--ac)" strokeWidth={2} />}
      </svg>
      <div className="absolute rounded-card border border-ln bg-sf p-4 shadow-xl" style={{ left, top, width: cardW }}>
        <div className="mb-1 flex items-center gap-2">
          <span className="pill">Step {step + 1} of {TOUR_STEPS.length}</span>
        </div>
        <h3 id="tour-title">{current.title}</h3>
        <p className="sm my-1">{current.body}</p>
        {!rect && <p className="sm mu my-1">This part appears once a run has started.</p>}
        <div className="mt-3 flex items-center gap-2">
          <button type="button" className="btn-ghost !py-1 sm" onClick={close}>Skip tour</button>
          <span className="flex-1" />
          <button type="button" className="btn-ghost !py-1 sm" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
          <button ref={nextRef} type="button" className="btn !py-1 sm" onClick={() => (last ? close() : setStep(step + 1))}>
            {last ? "Finish" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
