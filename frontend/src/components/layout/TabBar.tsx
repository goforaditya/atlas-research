import { useRef, type KeyboardEvent } from "react";
import { BarChart3, FlaskConical, LayoutGrid, Lightbulb, MessagesSquare, Newspaper, Sparkles, TrendingUp, type LucideIcon } from "lucide-react";
import { useRunStore, type TabId } from "../../store/useRunStore";

export const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "sum", label: "Summary", icon: LayoutGrid },
  { id: "fin", label: "Financials", icon: BarChart3 },
  { id: "news", label: "News", icon: Newspaper },
  { id: "sen", label: "Sentiment", icon: MessagesSquare },
  { id: "mkt", label: "Market", icon: TrendingUp },
  { id: "ins", label: "Insights", icon: Lightbulb },
  { id: "ai", label: "AI at work", icon: Sparkles },
  { id: "met", label: "How it works", icon: FlaskConical },
];

/**
 * Floating glass tab bar pinned to the bottom of the screen, after the iOS 26 tab bar.
 * Accessible ARIA tablist with arrow-key, Home and End navigation.
 */
export default function TabBar() {
  const active = useRunStore((s) => s.activeTab);
  const setActive = useRunStore((s) => s.setActiveTab);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const go = (id: TabId) => {
    setActive(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === active);
    let next = i;
    if (e.key === "ArrowRight") next = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = TABS.length - 1;
    else return;
    e.preventDefault();
    go(TABS[next].id);
    refs.current[next]?.focus();
  };

  return (
    <nav className="tabbar-wrap" aria-label="Report sections">
      <div role="tablist" aria-label="Report sections" onKeyDown={onKey} className="tabbar no-scrollbar">
        {TABS.map((t, i) => {
          const on = active === t.id;
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              ref={(el) => { refs.current[i] = el; }}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={on}
              aria-controls={`panel-${t.id}`}
              tabIndex={on ? 0 : -1}
              onClick={() => go(t.id)}
              className="tabbar-item"
            >
              <Icon size={20} strokeWidth={on ? 2.3 : 1.8} aria-hidden="true" />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
