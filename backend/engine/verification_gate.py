"""Deterministic Verification Gate (Section 4, Stage 12).

Every numeric token in report prose must be immediately followed by a citation token that resolves,
and the token text must equal the cited row's formatted_value (or appear verbatim in the cited
evidence snippet). Sentences that fail are replaced with a stated-gap marker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.schemas.common import ClaimClassification, SourceTier
from backend.schemas.intelligence import InsightCard, PeerCompRow
from backend.schemas.state import ExecutiveSummary, ResearchRunState, VerificationGateReport

GAP_MARKER = "[Stated Gap: Unverified numeric claim removed by Verification Gate]"

CITATION = re.compile(r"\[\[([a-z0-9_]+)\]\]")
FISCAL_LABEL = re.compile(r"\b(?:Q[1-4])?FY\d{2,4}\b")
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATES = re.compile(
    rf"\b\d{{4}}-\d{{2}}(?:-\d{{2}})?\b"          # ISO 2026-03 / 2026-03-31
    rf"|\b\d{{1,2}}\s+{_MONTHS}\s+\d{{4}}\b"       # 14 Nov 2025
    rf"|\b{_MONTHS}\s+\d{{1,2}},\s+\d{{4}}\b"      # November 14, 2025
    rf"|\b{_MONTHS}\s+\d{{4}}\b",                  # Mar 2026
)
NUM_TOKEN = re.compile(
    r"(?<![\w.])"                          # not inside a word or a longer number
    r"(?P<tok>[+\-−]?[₹$]?\s?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|[+\-−]?[₹$]?\s?\d+(?:\.\d+)?)"
    r"(?P<unit>\s?(?:%|bps|x|cr|crore|M|bn|d|days))?"
    r"(?![\w])"
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FOLLOWING_CITATION = re.compile(r"\s*\[\[([a-z0-9_]+)\]\]")


def _mask(text: str) -> str:
    """Blank out citation tokens, fiscal labels and dates, keeping string length (and offsets) intact."""
    out = text
    for pat in (CITATION, FISCAL_LABEL, DATES):
        out = pat.sub(lambda m: " " * len(m.group(0)), out)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("−", "-")).strip()


@dataclass
class TokenCheck:
    text: str
    citation: str | None
    exempt: bool
    verified: bool


def extract_numeric_tokens(text: str) -> list[tuple[re.Match[str], str]]:
    """All numeric tokens in text (after masking), with their full token text including unit."""
    masked = _mask(text)
    return [(m, text[m.start():m.end()]) for m in NUM_TOKEN.finditer(masked)]


def _is_exempt(m: re.Match[str]) -> bool:
    tok, unit = m.group("tok"), m.group("unit")
    if unit:
        return False
    bare = tok.strip()
    if not re.fullmatch(r"\d+", bare):  # any sign, currency symbol, comma or decimal point
        return False
    n = int(bare)
    return 1990 <= n <= 2035 or 1 <= n <= 12


@dataclass
class CitationRegistry:
    """token id -> acceptable formatted strings; evidence id -> verbatim snippet."""
    formatted: dict[str, set[str]] = field(default_factory=dict)
    snippets: dict[str, str] = field(default_factory=dict)

    def add_formatted(self, token_id: str, value: str) -> None:
        self.formatted.setdefault(token_id, set()).add(_norm(value))

    def accepts(self, token_id: str, token_text: str) -> bool:
        t = _norm(token_text)
        if token_id in self.formatted and t in self.formatted[token_id]:
            return True
        snippet = self.snippets.get(token_id)
        return snippet is not None and t in _norm(snippet)


def peer_slug(row: PeerCompRow) -> str:
    if row.is_median_row:
        return "median"
    return re.sub(r"[^a-z0-9]+", "_", row.ticker.lower().split(":")[-1]).strip("_")


def peer_token_id(row: PeerCompRow, column: str) -> str:
    return f"peer_{peer_slug(row)}_{column}"


def build_registry(state: ResearchRunState) -> CitationRegistry:
    reg = CitationRegistry()
    for row in [*state.metrics_ledger, *state.quarterly_metrics]:
        reg.add_formatted(row.metric_id, row.formatted_value)
    for li in [*state.line_items, *state.quarterly_items]:
        reg.add_formatted(li.item_id, li.formatted_value)
        reg.snippets.setdefault(li.evidence.evidence_id, li.evidence.verbatim_snippet)
    for row in state.peer_comps:
        for column, text in row.formatted.items():
            reg.add_formatted(peer_token_id(row, column), text)
    for ev_id, ev in state.evidence_registry.items():
        reg.snippets[ev_id] = ev.verbatim_snippet
    for ev in [n.evidence for n in state.news_events] + [s.evidence for s in (state.sentiment.items if state.sentiment else [])]:
        reg.snippets.setdefault(ev.evidence_id, ev.verbatim_snippet)
    return reg


@dataclass
class _Counters:
    scanned: int = 0
    verified: int = 0
    blocked: int = 0


def check_sentence(sentence: str, reg: CitationRegistry) -> list[TokenCheck]:
    checks = []
    for m, text in extract_numeric_tokens(sentence):
        if _is_exempt(m):
            checks.append(TokenCheck(text, None, True, True))
            continue
        follow = _FOLLOWING_CITATION.match(sentence, m.end())
        cite = follow.group(1) if follow else None
        ok = cite is not None and reg.accepts(cite, text)
        checks.append(TokenCheck(text, cite, False, ok))
    return checks


def gate_text(text: str | None, reg: CitationRegistry, counters: _Counters) -> str | None:
    """Return text with every sentence containing an unverified numeric token replaced by the gap marker."""
    if not text:
        return text
    parts = SENTENCE_SPLIT.split(text)
    out = []
    for sentence in parts:
        checks = [c for c in check_sentence(sentence, reg) if not c.exempt]
        counters.scanned += len(checks)
        failed = [c for c in checks if not c.verified]
        counters.verified += len(checks) - len(failed)
        if failed:
            counters.blocked += len(failed)
            out.append(GAP_MARKER)
        else:
            out.append(sentence)
    return " ".join(out)


def _has_hard_support(card: InsightCard, news_tiers: dict[str, SourceTier]) -> bool:
    if card.supporting_metric_ids or card.supporting_item_ids or card.supporting_red_flag_ids:
        return True
    return any(news_tiers.get(nid) in (SourceTier.T1, SourceTier.T2) for nid in card.supporting_news_ids)


def run_verification_gate(state: ResearchRunState) -> tuple[VerificationGateReport, ResearchRunState]:
    """Audit every prose field; return the report and a new state with cleaned prose and recounted claims."""
    reg = build_registry(state)
    c = _Counters()
    news_tiers = {n.event_id: n.highest_tier for n in state.news_events}

    insights: list[InsightCard] = []
    downgraded: list[str] = []
    for card in state.insights:
        update: dict = {"title": gate_text(card.title, reg, c), "body": gate_text(card.body, reg, c)}
        if card.classification in (ClaimClassification.FACT, ClaimClassification.INFERENCE) and not _has_hard_support(card, news_tiers):
            update["classification"] = ClaimClassification.HYPOTHESIS
            downgraded.append(card.insight_id)
        insights.append(card.model_copy(update=update))

    summary: ExecutiveSummary | None = None
    if state.summary is not None:
        s = state.summary
        upd: dict = {}
        for name in ("verdict_headline", "verdict_prose", "business_overview", "revenue_mix_summary",
                     "management_summary", "governance_watch_note", "quarterly_direction_note"):
            upd[name] = gate_text(getattr(s, name), reg, c)
        for name in ("opportunities", "risks", "analyst_follow_up_notes"):
            upd[name] = [gate_text(x, reg, c) for x in getattr(s, name)]
        upd["fact_count"] = sum(1 for i in insights if i.classification == ClaimClassification.FACT)
        upd["inference_count"] = sum(1 for i in insights if i.classification == ClaimClassification.INFERENCE)
        upd["hypothesis_count"] = sum(1 for i in insights if i.classification == ClaimClassification.HYPOTHESIS)
        summary = s.model_copy(update=upd)

    positioning = gate_text(state.market_positioning_prose, reg, c)
    advantages = [gate_text(x, reg, c) for x in state.market_advantages]
    disadvantages = [gate_text(x, reg, c) for x in state.market_disadvantages]
    news = [n.model_copy(update={"summary": gate_text(n.summary, reg, c)}) for n in state.news_events]

    report = VerificationGateReport(
        total_numeric_tokens_scanned=c.scanned,
        verified_against_ledger=c.verified,
        unverified_tokens_blocked=c.blocked,
        claims_downgraded_to_hypothesis=downgraded,
        passed=c.blocked == 0,
    )
    new_state = state.model_copy(update={
        "insights": insights,
        "summary": summary,
        "market_positioning_prose": positioning,
        "market_advantages": advantages,
        "market_disadvantages": disadvantages,
        "news_events": news,
        "verification": report,
    })
    return report, new_state
