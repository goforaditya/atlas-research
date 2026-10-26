import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { useRunStore } from "../../store/useRunStore";
import type { ReplayInfo } from "../../types/atlas";

/** "Block, Inc. (formerly Square) (unconfirmed seed figures)" -> "Block, Inc." */
export const shortName = (name: string) => name.replace(/\s*\(.*?\)\s*$/, "");

function matches(r: ReplayInfo, q: string): boolean {
  const hay = `${r.company_name} ${r.ticker} ${r.sector} ${r.company_query}`.toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}

function tag(r: ReplayInfo): string | null {
  if (r.unconfirmed) return "Seed figures";
  return null;
}

/**
 * Type-to-search company field (an ARIA combobox). In Replay mode it filters the recorded companies by name,
 * ticker or sector; Enter runs the highlighted one. In Live mode it is a plain free-text field.
 */
export default function CompanyPicker({ onRun, size = "bar", disabled = false }: {
  onRun: () => void; size?: "hero" | "bar"; disabled?: boolean;
}) {
  const s = useRunStore();
  const listId = useId();
  const [text, setText] = useState<string | null>(null); // null = not editing, show the current selection
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);

  const selected = s.replays.find((r) => r.replay_id === s.selectedReplayId) ?? null;
  const results = useMemo(() => (text ? s.replays.filter((r) => matches(r, text)) : s.replays), [s.replays, text]);
  const hero = size === "hero";

  const field = `min-w-0 flex-1 border-0 bg-transparent outline-none placeholder:text-mu ${hero ? "py-3 text-[17px]" : "py-2 text-[15px]"}`;

  if (s.mode === "live") {
    return (
      <div className={`picker ${hero ? "picker-hero" : ""}`}>
        <Search size={hero ? 20 : 16} className="shrink-0 text-mu" aria-hidden="true" />
        <input aria-label="Company to research" className={field} value={s.query} disabled={disabled}
               onChange={(e) => s.setQuery(e.target.value)} placeholder="Company name or ticker"
               onKeyDown={(e) => e.key === "Enter" && s.query.trim() && onRun()} />
      </div>
    );
  }

  const pick = (r: ReplayInfo, thenRun = false) => {
    s.selectReplay(r.replay_id);
    setText(null);
    setOpen(false);
    if (thenRun) onRun();
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setOpen(true); setHi(0); return; }
      const n = results.length;
      if (n) setHi((h) => (h + (e.key === "ArrowDown" ? 1 : -1) + n) % n);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && results[hi]) pick(results[hi], true);
      else if (!open && selected) onRun();
    } else if (e.key === "Escape") {
      setText(null);
      setOpen(false);
    }
  };

  return (
    <div className={`picker relative ${hero ? "picker-hero" : ""}`}>
      <Search size={hero ? 20 : 16} className="shrink-0 text-mu" aria-hidden="true" />
      <input
        role="combobox" aria-label="Company" aria-autocomplete="list" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={open && results[hi] ? `${listId}-${results[hi].replay_id}` : undefined}
        className={field} disabled={disabled} autoComplete="off" spellCheck={false}
        placeholder={s.replays.length ? "Search a company, ticker or sector" : "No replays available"}
        value={text ?? (selected ? shortName(selected.company_name) : "")}
        onFocus={(e) => { e.currentTarget.select(); setOpen(true); setHi(0); }}
        onBlur={() => { setOpen(false); setText(null); }}
        onChange={(e) => { setText(e.target.value); setOpen(true); setHi(0); }}
        onKeyDown={onKey}
      />
      {open && (
        <ul id={listId} role="listbox" aria-label="Companies"
            className={`menu absolute left-0 right-0 z-50 m-0 list-none p-1.5 ${hero ? "top-[calc(100%+10px)]" : "top-[calc(100%+6px)]"}`}>
          {results.length === 0 && <li className="px-3 py-2.5 sm mu">No recorded company matches “{text}”.</li>}
          {results.map((r, i) => (
            <li key={r.replay_id} id={`${listId}-${r.replay_id}`} role="option" aria-selected={i === hi}
                data-replay-id={r.replay_id}
                className={`flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 ${i === hi ? "bg-chip" : ""}`}
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => { e.preventDefault(); pick(r); }}>
              <span className="avatar" aria-hidden="true">{shortName(r.company_name).charAt(0)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{shortName(r.company_name)}</span>
                <span className="block truncate sm mu">{[r.ticker, r.sector].filter(Boolean).join(" · ")}</span>
              </span>
              {tag(r) && <span className="pill pill-mu shrink-0 !text-[12px]">{tag(r)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
