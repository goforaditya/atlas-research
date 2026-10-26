import { useRef, type KeyboardEvent } from "react";
import { useRunStore, type TabId } from "../../store/useRunStore";

export const TABS: { id: TabId; label: string }[] = [
  { id: "sum", label: "Summary" },
  { id: "fin", label: "Financials" },
  { id: "news", label: "News" },
  { id: "sen", label: "Sentiment" },
  { id: "mkt", label: "Market" },
  { id: "ins", label: "Insights" },
  { id: "ai", label: "AI at work" },
  { id: "met", label: "Method" },
];

/** Accessible ARIA tablist with arrow-key, Home and End navigation. */
export default function TabBar() {
  const active = useRunStore((s) => s.activeTab);
  const setActive = useRunStore((s) => s.setActiveTab);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === active);
    let next = i;
    if (e.key === "ArrowRight") next = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    setActive(TABS[next].id);
    refs.current[next]?.focus();
  };

  return (
    <div role="tablist" aria-label="Report sections" onKeyDown={onKey}
         className="no-scrollbar -mx-4 mb-8 mt-2 flex gap-1 overflow-x-auto px-4 md:mx-0 md:px-0">
      {TABS.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => { refs.current[i] = el; }}
          role="tab"
          id={`tab-${t.id}`}
          aria-selected={active === t.id}
          aria-controls={`panel-${t.id}`}
          tabIndex={active === t.id ? 0 : -1}
          onClick={() => setActive(t.id)}
          className={`cursor-pointer whitespace-nowrap rounded-full border-0 px-4 py-2 text-[15px] font-medium transition-colors ${
            active === t.id ? "bg-[var(--tab-on)] text-[var(--tab-on-tx)]" : "bg-transparent text-mu hover:bg-chip hover:text-tx"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
