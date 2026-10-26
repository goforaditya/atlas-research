import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Bot, X } from "lucide-react";
import type { ResearchRunState } from "../../types/atlas";
import { useRunStore, type TabId } from "../../store/useRunStore";
import { exportUrl } from "../../api/client";
import { Prose } from "./EvidenceDrawer";
import { findExample } from "./EngineeringDeepDive";

/*
 * A scripted audit-partner chat. There is no model behind it yet: each intent is a fixed answer template filled from
 * the current run's state (verdict, red flags, gate report, contradictions, gaps), and free text is routed to an
 * intent by keyword. Answers only quote figures that are already in the state, so the gate's guarantee still holds.
 */

type Part = { p: string } | { prose: string } | { list: ReactNode[] };
type Action = { label: string; tab?: TabId; workings?: boolean; evidence?: string; href?: string };
type Answer = { parts: Part[]; actions?: Action[]; next?: IntentId[] };
type IntentId = "opinion" | "flags" | "trust" | "conflicts" | "gaps" | "questions" | "walk" | "memo";
type Msg = { from: "auditor" | "you"; answer?: Answer; text?: string };

const PROMPTS: Record<IntentId, string> = {
  opinion: "What's your audit opinion?",
  flags: "Which red flags should worry me?",
  trust: "Can I trust these numbers?",
  conflicts: "Where do the sources disagree?",
  gaps: "What's missing from the evidence?",
  questions: "What should I ask management?",
  walk: "Walk me through one figure",
  memo: "Draft my audit memo",
};

const KEYWORDS: [IntentId, RegExp][] = [
  ["flags", /flag|risk|worr|concern|warning/i],
  ["trust", /trust|verif|accura|reliab|correct|hallucin/i],
  ["conflicts", /conflict|disagree|contradict|differ/i],
  ["gaps", /gap|missing|lack|unknown|not sourced/i],
  ["questions", /ask|management|question|diligence|cfo/i],
  ["memo", /memo|export|report|download|write.?up/i],
  ["walk", /figure|number|calculat|formula|how.*(get|derive)|math/i],
  ["opinion", /opinion|verdict|summary|overall|think|view/i],
];

/** Follow-up questions an auditor would put to management when a rule fires. */
const MGMT_Q: Record<string, string> = {
  RF_01: "Receivables are growing faster than revenue. Have credit terms been extended, and how concentrated is the debtor book?",
  RF_02: "Profit isn't turning into cash. Which working-capital lines absorbed it, and is that timing or structural?",
  RF_03: "Free cash flow is negative. What's the funding runway, and which capex is discretionary?",
  RF_04: "Debt is outgrowing revenue. What is the new borrowing financing, and what are the covenant headrooms?",
  RF_05: "Interest cover is thin. What's the refinancing plan and the fixed versus floating mix?",
  RF_06: "Revenue is up but operating cash is down. Walk us through revenue recognition and any one-offs in cash flow.",
  RF_07: "The current ratio is below comfort. Which short-term obligations fall due in the next two quarters?",
  RF_08: "EBITDA margin has compressed. Is that pricing, input costs or mix, and when does it reverse?",
};

const shortName = (s: ResearchRunState) => s.entity?.legal_name ?? s.company_query ?? "this company";

function answerFor(id: IntentId, s: ResearchRunState, runId: string | null): Answer {
  const fired = s.red_flags.filter((f) => f.fired);
  const v = s.verification;
  const name = shortName(s);
  if (!s.summary && id !== "trust" && id !== "flags") {
    return { parts: [{ p: `The run on ${name} is still in progress. I'll have a view once synthesis and the verification gate have finished.` }],
             next: ["trust", "flags"] };
  }
  switch (id) {
    case "opinion": {
      const clean = v?.passed ?? false;
      const untested = s.red_flags.filter((f) => f.status === "insufficient_data").length;
      return {
        parts: [
          { p: `On ${name}, my read is: ${s.summary!.verdict_headline}` },
          { prose: s.summary!.verdict_prose },
          { p: clean
            ? `Every figure in the narrative tied back to the ledger${v ? ` (${v.verified_against_ledger} of ${v.total_numeric_tokens_scanned})` : ""}, so I'd call the numbers clean. ${fired.length ? `I'd still flag ${fired.length} matter${fired.length === 1 ? "" : "s"} for emphasis.` : untested ? `No forensic rule fired, though ${untested} of ${s.red_flags.length} couldn't be tested for lack of inputs.` : "No forensic rule fired."}`
            : `The gate blocked ${v?.unverified_tokens_blocked ?? 0} figure${v?.unverified_tokens_blocked === 1 ? "" : "s"}, so treat the affected sentences as unverified.` },
        ],
        actions: [{ label: "Open summary", tab: "sum" }],
        next: fired.length ? ["flags", "questions"] : ["trust", "gaps"],
      };
    }
    case "flags":
      if (fired.length === 0) {
        const clear = s.red_flags.filter((f) => f.status === "clear").length;
        const blind = s.red_flags.filter((f) => f.status === "insufficient_data");
        return {
          parts: [
            { p: `None of the ${s.red_flags.length} forensic rules fired for ${name}.` },
            { p: blind.length === 0
              ? "Cash conversion, leverage, liquidity and margins all sat inside their thresholds."
              : `But only ${clear} could actually be tested. ${blind.length} couldn't be evaluated because their inputs aren't sourced yet, so I'd treat that as "not tested", not "clean":` },
            ...(blind.length ? [{ list: blind.map((f) => <span key={f.rule_id}>{f.rule_name}{f.missing_inputs.length ? `: needs ${f.missing_inputs.slice(0, 3).join(", ")}` : ""}</span>) }] : []),
          ],
          actions: [{ label: "See all rules", tab: "fin" }], next: blind.length ? ["gaps", "trust"] : ["trust", "opinion"],
        };
      }
      return {
        parts: [
          { p: `${fired.length} of ${s.red_flags.length} rules fired. In order of severity:` },
          { list: [...fired].sort((a, b) => "hml".indexOf(a.severity[0]) - "hml".indexOf(b.severity[0])).map((f) => (
            <span key={f.rule_id}><b>{f.rule_name}</b> ({f.severity}): observed {f.observed_value_formatted} against {f.threshold_desc}.</span>)) },
          { p: "Each one is a fixed expression over the calculation ledger, so the same inputs always give the same result." },
        ],
        actions: [{ label: "Open Financials", tab: "fin" }, { label: "Show the thresholds", workings: true, tab: "fin" }],
        next: ["questions", "trust"],
      };
    case "trust": {
      const tiers: Record<string, number> = {};
      for (const ev of Object.values(s.evidence_registry ?? {})) tiers[ev.source_tier] = (tiers[ev.source_tier] ?? 0) + 1;
      const rc = s.reconciliation_checks;
      const failedRc = rc.filter((c) => c.status === "failed").length;
      return {
        parts: [
          { p: "Three layers of control stand behind every number:" },
          { list: [
            <span key="1"><b>Source.</b> Statement figures come only from T1/T2 filings{Object.keys(tiers).length ? ` (${Object.entries(tiers).sort().map(([t, n]) => `${n} ${t}`).join(", ")} evidence items in this run)` : ""}.</span>,
            <span key="2"><b>Arithmetic.</b> All metrics are Decimal code with fixed formulas. {rc.length ? `${rc.length - failedRc} of ${rc.length} accounting identities reconciled${failedRc ? `, and ${failedRc} failed and were set aside` : ""}.` : "Accounting identities run where the statements allow it."}</span>,
            <span key="3"><b>Narrative.</b> {v ? `The gate checked ${v.total_numeric_tokens_scanned} figures in the prose, matched ${v.verified_against_ledger} and blocked ${v.unverified_tokens_blocked}.` : "The verification gate hasn't run yet."}</span>,
          ] },
        ],
        actions: [{ label: "How the gate works", tab: "met" }, { label: "Show workings", workings: true }],
        next: ["walk", "conflicts"],
      };
    }
    case "conflicts":
      if (s.contradictions.length === 0) {
        return { parts: [{ p: "The sources agreed on every point I cross-checked. No conflicts needed resolving." }], next: ["gaps", "opinion"] };
      }
      return {
        parts: [
          { p: `I found ${s.contradictions.length} conflict${s.contradictions.length === 1 ? "" : "s"}. In each case code picked the winner by source-tier weight, not by judgement:` },
          { list: s.contradictions.map((c) => <span key={c.contradiction_id}><b>{c.topic}.</b> {c.resolution_rationale}</span>) },
        ],
        actions: [{ label: "Open the resolver", tab: "met" }],
        next: ["trust", "gaps"],
      };
    case "gaps":
      return {
        parts: s.stated_gaps.length
          ? [{ p: "These are stated openly rather than estimated. I wouldn't sign off on them without more evidence:" }, { list: s.stated_gaps.map((g, i) => <span key={i}>{g}</span>) }]
          : [{ p: "No material gaps. Every section was sourced." }],
        actions: [{ label: "See stated gaps", tab: "met" }],
        next: ["questions", "memo"],
      };
    case "questions": {
      const qs = [...fired.map((f) => MGMT_Q[f.rule_id.slice(0, 5)]).filter(Boolean), ...s.summary!.analyst_follow_up_notes];
      return {
        parts: qs.length
          ? [{ p: `For the management meeting with ${name}, I'd lead with these:` }, { list: qs.slice(0, 6).map((q, i) => <span key={i}>{q}</span>) }]
          : [{ p: "Nothing pressing. I'd use the meeting to confirm the growth drivers and capital allocation plans." }],
        next: ["memo", "flags"],
      };
    }
    case "walk": {
      const ex = findExample(s);
      if (!ex) return { parts: [{ p: "There isn't a cited figure in the narrative yet. Ask me again once synthesis finishes." }], next: ["trust"] };
      const row = ex.resolved.metric;
      return {
        parts: [
          { p: `Take ${ex.token} in this sentence:` },
          { prose: ex.sentence },
          row
            ? { p: `That's ${row.metric_label} for ${row.fiscal_period}: ${row.formula_template}, which with the filed values is ${row.formula_substituted}. The Decimal engine gives ${row.value ?? "n/a"}, formatted as ${row.formatted_value}. The gate confirmed the prose says exactly that.` }
            : { p: `That's ${ex.resolved.title}, read from ${ex.resolved.evidence?.document_title ?? "the filing"}${ex.resolved.evidence?.page_number ? ` on page ${ex.resolved.evidence.page_number}` : ""}. The gate confirmed the prose matches its formatted value.` },
        ],
        actions: [{ label: "Open the source", evidence: ex.cite }],
        next: ["trust", "flags"],
      };
    }
    case "memo":
      return {
        parts: [
          { p: `Here's the shape of the memo on ${name}:` },
          { list: [
            <span key="o"><b>Opinion:</b> {s.summary!.verdict_headline}</span>,
            <span key="f"><b>Matters for emphasis:</b> {fired.length ? fired.map((f) => f.rule_name).join("; ") : "none"}</span>,
            <span key="v"><b>Figures verified:</b> {v ? `${v.verified_against_ledger}/${v.total_numeric_tokens_scanned}` : "pending"}</span>,
            <span key="g"><b>Scope limitations:</b> {s.stated_gaps.length ? `${s.stated_gaps.length} stated gap${s.stated_gaps.length === 1 ? "" : "s"}` : "none"}</span>,
          ] },
          { p: "The full report exports with numbered source footnotes for every figure." },
        ],
        actions: runId ? [{ label: "Export Markdown", href: exportUrl(runId, "md") }, { label: "Export PDF", href: exportUrl(runId, "pdf") }] : [],
        next: ["questions", "opinion"],
      };
  }
}

function route(text: string): IntentId | null {
  for (const [id, re] of KEYWORDS) if (re.test(text)) return id;
  return null;
}

export default function AuditorChat() {
  const state = useRunStore((s) => s.projectedState);
  const runId = useRunStore((s) => s.runId);
  const phase = useRunStore((s) => s.phase);
  const setTab = useRunStore((s) => s.setActiveTab);
  const setExplain = useRunStore((s) => s.setExplainMode);
  const openEvidence = useRunStore((s) => s.openEvidence);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const name = shortName(state);

  useEffect(() => { setMsgs([]); }, [runId]);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [msgs, typing]);

  const greeting: Answer = {
    parts: [{ p: phase === "completed"
      ? `I've been through the ${name} file: filings, ledger, rule pack and the verification gate. What would you like to look at?`
      : `I'm following the ${name} run as it streams. Ask me anything, and I'll answer from the evidence gathered so far.` }],
    next: ["opinion", "flags", "trust", "questions"],
  };
  const thread: Msg[] = msgs.length ? msgs : [{ from: "auditor", answer: greeting }];

  const ask = (text: string, id: IntentId | null) => {
    setMsgs((m) => [...(m.length ? m : thread), { from: "you", text }]);
    setTyping(true);
    window.setTimeout(() => {
      const answer: Answer = id ? answerFor(id, state, runId) : {
        parts: [{ p: "I'm in guided mode for this demo, so I answer a fixed set of audit questions about this run. Try one of these:" }],
        next: ["opinion", "flags", "trust", "conflicts", "gaps", "walk"],
      };
      setMsgs((m) => [...m, { from: "auditor", answer }]);
      setTyping(false);
    }, 650 + Math.random() * 500);
  };

  const act = (a: Action) => {
    if (a.workings) setExplain(true);
    if (a.tab) setTab(a.tab);
    if (a.evidence) openEvidence(a.evidence);
    if (!a.href && window.innerWidth < 640) setOpen(false);
  };

  return (
    <>
      {!open && (
        <button type="button" className="chat-fab" onClick={() => setOpen(true)} aria-label="Ask the auditor">
          <Bot size={20} aria-hidden="true" /><span className="hidden sm:inline">Ask the auditor</span>
        </button>
      )}
      {open && (
        <section className="chat-sheet" role="dialog" aria-label="Atlas auditor">
          <header className="flex items-center gap-3 border-b border-ln px-4 py-3">
            <span className="avatar !h-9 !w-9" aria-hidden="true"><Bot size={18} /></span>
            <div className="min-w-0 flex-1">
              <div className="font-semibold leading-tight">Atlas Auditor</div>
              <div className="flex items-center gap-1.5 text-[12px] mu"><span className="h-1.5 w-1.5 rounded-full bg-ps" />Reviewing {name}</div>
            </div>
            <button type="button" className="btn-ghost !p-2" aria-label="Close chat" onClick={() => setOpen(false)}><X size={18} /></button>
          </header>

          <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {thread.map((m, i) => m.from === "you" ? (
              <div key={i} className="flex justify-end"><div className="bubble-you">{m.text}</div></div>
            ) : (
              <div key={i} className="space-y-2">
                <div className="bubble-ai">
                  {m.answer!.parts.map((part, j) =>
                    "p" in part ? <p key={j} className="m-0 [&+*]:mt-2">{part.p}</p>
                    : "prose" in part ? <p key={j} className="quote !my-2"><Prose text={part.prose} /></p>
                    : <ul key={j} className="my-2 space-y-1.5 pl-5">{part.list.map((li, k) => <li key={k}>{li}</li>)}</ul>)}
                  {m.answer!.actions && m.answer!.actions.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {m.answer!.actions.map((a) => a.href
                        ? <a key={a.label} className="chat-action no-underline" href={a.href}>{a.label}</a>
                        : <button key={a.label} type="button" className="chat-action" onClick={() => act(a)}>{a.label}</button>)}
                    </div>
                  )}
                </div>
                {i === thread.length - 1 && !typing && m.answer!.next && (
                  <div className="flex flex-wrap gap-1.5 pl-1">
                    {m.answer!.next.map((n) => (
                      <button key={n} type="button" className="chip-btn !text-[13px]" onClick={() => ask(PROMPTS[n], n)}>{PROMPTS[n]}</button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {typing && <div className="bubble-ai !w-16" aria-label="Auditor is typing"><span className="typing"><i /><i /><i /></span></div>}
          </div>

          <form className="flex items-center gap-2 border-t border-ln p-3"
                onSubmit={(e) => { e.preventDefault(); const t = draft.trim(); if (!t || typing) return; setDraft(""); ask(t, route(t)); }}>
            <input className="picker !rounded-full !py-2.5 !pl-4 outline-none" placeholder="Ask about this audit…" aria-label="Message the auditor"
                   value={draft} onChange={(e) => setDraft(e.target.value)} />
            <button type="submit" className="btn btn-accent !p-2.5" aria-label="Send" disabled={!draft.trim() || typing}><ArrowUp size={18} /></button>
          </form>
        </section>
      )}
    </>
  );
}
