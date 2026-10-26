"""Stage 2: 12 research sub-questions (LLM) and the source routing rule (code)."""
from __future__ import annotations

import json
import re
from typing import Any

from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import RESEARCH_PLANNING_SYSTEM, RESEARCH_PLANNING_USER, ResearchPlanOut
from backend.schemas.common import ActorBadge, StageId, StageStatus
from backend.schemas.state import ResolvedEntity

S = StageId.S02_PLAN
C, L = ActorBadge.CODE, ActorBadge.LLM
PLAN_TIMEOUT_SEC = 10.0
GUARDS = ["Schema failure: up to 2 retries, then a deterministic 12-question sector template",
          "Timeout over 10 s falls back to the template",
          "Questions containing digits are rejected: the plan may not carry numbers or assumptions"]

TEMPLATE = [
    "How closely does operating cash flow track reported profit over the available years?",
    "Are receivables growing faster than revenue, and what explains the gap?",
    "How is capital expenditure funded: internal cash, debt or equity?",
    "Have key managerial personnel changed recently, and were reasons disclosed?",
    "What regulatory actions, penalties or licence conditions apply to the company?",
    "Which material events (acquisitions, fundraises, restructurings) were disclosed to the exchange?",
    "What recurring operational complaints do customers raise in public reviews?",
    "What do employees say publicly about workload, targets and leadership?",
    "How do investors frame the stock, and is that framing grounded in reported cash flows?",
    "Which listed peers share the business model and region?",
    "How do growth, margins and returns compare with the peer median?",
    "What competitive advantages are visible in filings rather than in management commentary?",
]


def build_source_routing_plan(entity: ResolvedEntity) -> dict[str, Any]:
    """Numbers only from T1/T2 filings; narrative and sentiment from T3/T4 (Section 7.1)."""
    return {"numerical": ["T1", "T2"], "events": ["T1", "T2", "T3"], "sentiment": ["T4"],
            "jurisdiction": entity.jurisdiction}


def valid_questions(qs: list[str]) -> bool:
    return len(qs) == 12 and all(q.strip() and not re.search(r"\d", q) for q in qs)


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    calls = []
    t = Timer()
    used_template = False
    try:
        res = await ctx.llm.structured(
            stage="s02", schema=ResearchPlanOut, timeout=PLAN_TIMEOUT_SEC,
            system=RESEARCH_PLANNING_SYSTEM.format(legal_name=entity.legal_name, sector=entity.sector,
                                                   jurisdiction=entity.jurisdiction),
            user=RESEARCH_PLANNING_USER.format(entity_json=entity.model_dump_json()))
        questions = [q.strip() for q in res.parsed.sub_questions]
        calls.append(trace(ctx, "generate_sub_questions", L, {"buckets": 4, "per_bucket": 3}, res.duration_ms, questions[:4]))
        if not valid_questions(questions):
            used_template = True
            calls[-1].status = "fallback"
    except LLMError as exc:
        used_template = True
        calls.append(trace(ctx, "generate_sub_questions", L, {"buckets": 4, "per_bucket": 3}, t.ms, str(exc), "error"))
    if used_template:
        questions = list(TEMPLATE)
    plan = build_source_routing_plan(entity)
    calls.append(trace(ctx, "build_source_routing_plan", C, {"jurisdiction": entity.jurisdiction}, 0, json.dumps(plan)))
    actions = [(L, "Wrote research sub-questions without numbers or assumptions") if not used_template else
               (C, "Used the deterministic 12-question template (model output failed validation or timed out)"),
               (C, "Enforced routing: numerical questions may only be answered from T1/T2 filings")]
    p = panel(S, [f"Entity: {entity.legal_name} ({entity.sector})"], calls, actions,
              "Plan accepted" if not used_template else "Template plan used",
              "Covers financial quality, governance, sentiment and peer valuation.", GUARDS)
    status = StageStatus.WARNING if used_template else StageStatus.COMPLETED
    return StageResult(status, f"{len(questions)} sub-questions; numbers routed to T1/T2 filings only",
                       {"sub_questions": questions}, p)
