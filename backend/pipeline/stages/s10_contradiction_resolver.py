"""Stage 10: contradiction handling. The winner is assigned by code from tier weights, never by the LLM."""
from __future__ import annotations

from typing import Literal

from backend.schemas.common import TIER_WEIGHTS, EvidenceCitation
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
