"""Stage 10: contradiction handling. The winner is assigned by code from tier weights, never by the LLM."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.engine.derived_fields import line_items_period_order
from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import (
    CONTRADICTION_EXTRACTION_SYSTEM,
    CONTRADICTION_EXTRACTION_USER,
    ContradictionOut,
)
from backend.schemas.common import TIER_WEIGHTS, ActorBadge, EvidenceCitation, StageId, StageStatus
from backend.schemas.intelligence import ContradictionRecord

ConflictType = Literal["NUMERIC_DISCREPANCY", "NARRATIVE_VS_METRIC"]


def resolve_by_tier_weight(
    contradiction_id: str, topic: str, conflict_type: ConflictType,
    source_a_claim: str, source_a_evidence: EvidenceCitation,
    source_b_claim: str, source_b_evidence: EvidenceCitation,
    explanation_note: str = "",
) -> ContradictionRecord:
    """Higher tier weight wins numeric conflicts; T2 narrative vs T1 metric keeps both (fact vs framing)."""
    wa, wb = TIER_WEIGHTS[source_a_evidence.source_tier], TIER_WEIGHTS[source_b_evidence.source_tier]
    ta, tb = source_a_evidence.source_tier.value, source_b_evidence.source_tier.value
    if conflict_type == "NARRATIVE_VS_METRIC":
        winner: Literal["A", "B", "BOTH_FRAMED"] = "BOTH_FRAMED"
        rationale = "The metric is reported as fact; management's statement is retained as management framing."
    elif wa == wb:
        winner = "BOTH_FRAMED"
        rationale = f"Both sources carry the same tier ({ta}); both are shown and the gap is noted."
    elif wa > wb:
        winner = "A"
        rationale = f"Source A ({ta}, weight {wa:.2f}) outranks Source B ({tb}, weight {wb:.2f}); the report uses Source A."
    else:
        winner = "B"
        rationale = f"Source B ({tb}, weight {wb:.2f}) outranks Source A ({ta}, weight {wa:.2f}); the report uses Source B."
    if explanation_note:
        rationale = f"{explanation_note} {rationale}"
    return ContradictionRecord(
        contradiction_id=contradiction_id, topic=topic,
        source_a_claim=source_a_claim, source_a_tier=source_a_evidence.source_tier, source_a_weight=wa,
        source_a_evidence=source_a_evidence,
        source_b_claim=source_b_claim, source_b_tier=source_b_evidence.source_tier, source_b_weight=wb,
        source_b_evidence=source_b_evidence,
        winning_source=winner, resolution_rationale=rationale,
    )


# ---------------------------------------------------------------- live stage

S = StageId.S10_CONTRADICTION
C, L = ActorBadge.CODE, ActorBadge.LLM
GUARDS = ["Both evidence ids must resolve to a ledger row, line item, event, post or registry record, or the pair is dropped",
          "Every number in a claim must appear in that source's own snippet or formatted value",
          "The winner is assigned by code from tier weights; T2 narrative against a T1 metric keeps both as framing"]
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
FISCAL = re.compile(r"\b(?:Q[1-4])?FY\d{2,4}\b")


@dataclass
class Source:
    evidence: EvidenceCitation
    texts: list[str]


def evidence_lookup(state: dict[str, Any]) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for ev_id, ev in state.get("evidence_registry", {}).items():
        out[ev_id] = Source(ev, [ev.verbatim_snippet])
    items = {li.item_id: li for li in state.get("line_items", [])}
    for li in items.values():
        src = Source(li.evidence, [li.formatted_value, li.evidence.verbatim_snippet])
        out[li.item_id] = src
        out.setdefault(li.evidence.evidence_id, src)
    for m in state.get("metrics_ledger", []):
        first = items.get(m.inputs[0].line_item_id) if m.inputs else None
        if first is not None:
            out[m.metric_id] = Source(first.evidence, [m.formatted_value])
    for n in state.get("news_events", []):
        out[n.event_id] = out[n.evidence.evidence_id] = Source(n.evidence, [n.evidence.verbatim_snippet])
    sentiment = state.get("sentiment")
    for p in (sentiment.items if sentiment else []):
        out[p.post_id] = out[p.evidence.evidence_id] = Source(p.evidence, [p.quote_text])
    return out


def numbers_grounded(claim: str, src: Source) -> bool:
    return all(any(tok in t for t in src.texts) for tok in NUM.findall(FISCAL.sub(" ", claim)))


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity = state["entity"]
    ledger = state.get("metrics_ledger", [])
    items = state.get("line_items", [])
    latest = max((li.fiscal_period for li in items), key=line_items_period_order, default=None)
    t1 = {"metrics": [{"id": m.metric_id, "label": m.metric_label, "value": m.formatted_value} for m in ledger
                      if m.status == "computed"],
          "line_items": [{"id": li.item_id, "label": li.reported_label, "value": li.formatted_value} for li in items
                         if li.fiscal_period == latest and li.value is not None],
          "red_flags": [{"id": f.rule_id, "name": f.rule_name, "observed": f.observed_value_formatted}
                        for f in state.get("red_flags", []) if f.fired]}
    sentiment = state.get("sentiment")
    others = ([{"evidence_id": n.event_id, "tier": n.highest_tier.value, "headline": n.headline, "summary": n.summary}
               for n in state.get("news_events", [])] +
              [{"evidence_id": p.post_id, "tier": "T4", "platform": p.platform, "quote": p.quote_text}
               for p in (sentiment.items if sentiment else [])])
    calls = []
    if not others:
        p = panel(S, ["No T2/T3/T4 claims to compare"], calls, [(C, "Nothing to compare against the T1 ledger")],
                  "No contradictions", "Only T1 figures are present.", GUARDS)
        return StageResult(StageStatus.COMPLETED, "No non-filing claims to compare", {"contradictions": []}, p)
    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s10", schema=ContradictionOut, system=CONTRADICTION_EXTRACTION_SYSTEM,
                                       user=CONTRADICTION_EXTRACTION_USER.format(t1_summary_json=json.dumps(t1),
                                                                                 other_claims_json=json.dumps(others)))
    except LLMError as exc:
        calls.append(trace(ctx, "detect_numeric_and_narrative_conflicts", L, {"claims": len(others)}, t.ms, str(exc), "error"))
        p = panel(S, [entity.legal_name], calls, [(L, "Conflict extraction failed")], "No contradictions recorded",
                  "Without extraction no conflict is asserted.", GUARDS)
        return StageResult(StageStatus.WARNING, "Contradiction extraction failed",
                           {"contradictions": [], "stated_gaps": ["Contradiction check unavailable: model call failed"]}, p)
    cands = res.parsed.candidate_contradictions
    calls.append(trace(ctx, "detect_numeric_and_narrative_conflicts", L, {"claims": len(others)}, res.duration_ms,
                       [c.topic for c in cands]))
    lookup = evidence_lookup(state)
    records, dropped = [], []
    for i, c in enumerate(cands, start=1):
        a, b = lookup.get(c.source_a_evidence_id), lookup.get(c.source_b_evidence_id)
        if a is None or b is None:
            dropped.append(f"{c.topic}: unknown evidence id")
            continue
        if not (numbers_grounded(c.source_a_claim, a) and numbers_grounded(c.source_b_claim, b)):
            dropped.append(f"{c.topic}: a number in the claims is not in its source")
            continue
        records.append(resolve_by_tier_weight(f"ct_{i:02d}", c.topic, c.conflict_type, c.source_a_claim, a.evidence,
                                              c.source_b_claim, b.evidence, c.explanation_note))
    if dropped:
        calls.append(trace(ctx, "validate_contradictions", C, {"candidates": len(cands)}, 0, "; ".join(dropped), "fallback"))
    calls.append(trace(ctx, "resolve_by_tier_weight", C, {"conflicts": len(records)}, 0,
                       [f"{r.contradiction_id}: {r.winning_source}" for r in records]))
    actions = [(L, "Extracted conflicting claim pairs with their evidence ids"),
               (C, f"Checked ids and numbers; kept {len(records)} of {len(cands)}"),
               (C, "Assigned each winner from tier weights")]
    p = panel(S, [f"{len(t1['metrics'])} T1 metrics", f"{len(others)} T2/T3/T4 claims"], calls, actions,
              f"{len(records)} contradiction(s) resolved", "Higher-tier sources win numeric conflicts.", GUARDS)
    return StageResult(StageStatus.COMPLETED, f"{len(records)} contradictions resolved by tier weight",
                       {"contradictions": records}, p)
