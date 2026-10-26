"""Stage 11: cross-source synthesis. Code builds the provenance DAG from the ids each insight references."""
from __future__ import annotations

import json
from typing import Any

from backend.pipeline.graph import RunContext, StageResult, Timer, as_run_state, panel, to_json, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER, SynthesisOut
from backend.pipeline.stages.s09_market_peers import peer_table_json
from backend.schemas.common import ActorBadge, ClaimClassification, Polarity, SourceTier, StageId, StageStatus
from backend.schemas.intelligence import InsightCard, ProvenanceEdge, ProvenanceNode
from backend.schemas.state import ExecutiveSummary, ResearchRunState


def build_provenance_dag(state: ResearchRunState) -> tuple[list[ProvenanceNode], list[ProvenanceEdge]]:
    metrics = {m.metric_id: m for m in state.metrics_ledger}
    items = {li.item_id: li for li in state.line_items}
    flags = {f.rule_id: f for f in state.red_flags}
    news = {n.event_id: n for n in state.news_events}
    posts = {p.post_id: p for p in (state.sentiment.items if state.sentiment else [])}
    conflicts = {c.contradiction_id: c for c in state.contradictions}

    nodes: dict[str, ProvenanceNode] = {}
    edges: list[ProvenanceEdge] = []
    fact = ClaimClassification.FACT

    def add(node: ProvenanceNode) -> None:
        nodes.setdefault(node.node_id, node)

    for card in state.insights:
        for mid in card.supporting_metric_ids:
            if mid in metrics:
                add(ProvenanceNode(node_id=mid, node_type="metric", label=f"{metrics[mid].metric_label}: {metrics[mid].formatted_value}",
                                   classification=fact, source_tier=SourceTier.T1))
                edges.append(ProvenanceEdge(source_node_id=mid, target_node_id=card.insight_id, relationship="proves"))
        for iid in card.supporting_item_ids:
            if iid in items:
                li = items[iid]
                add(ProvenanceNode(node_id=iid, node_type="line_item", label=f"{li.reported_label} {li.fiscal_period}: {li.formatted_value}",
                                   classification=fact, source_tier=li.evidence.source_tier))
                edges.append(ProvenanceEdge(source_node_id=iid, target_node_id=card.insight_id, relationship="proves"))
        for rid in card.supporting_red_flag_ids:
            if rid in flags:
                add(ProvenanceNode(node_id=rid, node_type="red_flag", label=flags[rid].rule_name,
                                   classification=fact, source_tier=SourceTier.T1))
                edges.append(ProvenanceEdge(source_node_id=rid, target_node_id=card.insight_id, relationship="proves"))
        for nid in card.supporting_news_ids:
            if nid in news:
                n = news[nid]
                hard = n.highest_tier in (SourceTier.T1, SourceTier.T2)
                add(ProvenanceNode(node_id=nid, node_type="news", label=n.headline,
                                   classification=fact if hard else ClaimClassification.INFERENCE, source_tier=n.highest_tier))
                edges.append(ProvenanceEdge(source_node_id=nid, target_node_id=card.insight_id,
                                            relationship="corroborates" if hard else "contextualizes"))
        for pid in card.supporting_sentiment_ids:
            if pid in posts:
                p = posts[pid]
                add(ProvenanceNode(node_id=pid, node_type="sentiment", label=f"{p.platform}: {p.quote_text[:60]}",
                                   classification=ClaimClassification.INFERENCE if p.verdict == "signal" else ClaimClassification.HYPOTHESIS,
                                   source_tier=p.evidence.source_tier))
                edges.append(ProvenanceEdge(source_node_id=pid, target_node_id=card.insight_id, relationship="contextualizes"))
        for cid in card.supporting_contradiction_ids:
            if cid in conflicts:
                c = conflicts[cid]
                add(ProvenanceNode(node_id=cid, node_type="contradiction", label=c.topic,
                                   classification=ClaimClassification.INFERENCE, source_tier=SourceTier.T1))
                edges.append(ProvenanceEdge(source_node_id=cid, target_node_id=card.insight_id, relationship="contradicts"))
        add(ProvenanceNode(node_id=card.insight_id, node_type="insight", label=card.title,
                           classification=card.classification, source_tier=_best_tier(card, nodes)))
    return list(nodes.values()), edges


def _best_tier(card: InsightCard, nodes: dict[str, ProvenanceNode]) -> SourceTier:
    ids = [*card.supporting_metric_ids, *card.supporting_item_ids, *card.supporting_red_flag_ids,
           *card.supporting_news_ids, *card.supporting_sentiment_ids, *card.supporting_contradiction_ids]
    tiers = [nodes[i].source_tier for i in ids if i in nodes]
    return min(tiers, key=lambda t: t.value) if tiers else SourceTier.T4


def count_claims(insights: list[InsightCard]) -> tuple[int, int, int]:
    return (
        sum(1 for i in insights if i.classification == ClaimClassification.FACT),
        sum(1 for i in insights if i.classification == ClaimClassification.INFERENCE),
        sum(1 for i in insights if i.classification == ClaimClassification.HYPOTHESIS),
    )


# ---------------------------------------------------------------- live stage

S = StageId.S11_SYNTHESIS
C, L = ActorBadge.CODE, ActorBadge.LLM
GUARDS = ["The model may not compute or write any number without a citation token; Stage 12 removes any that slip",
          "Supporting ids that do not exist in the run are dropped by code",
          "Fact, inference and hypothesis counts and every provenance node and edge are built by code"]


def _j(rows: list[Any]) -> str:
    return json.dumps(to_json(rows), ensure_ascii=False, default=str)


def synthesis_inputs(state: dict[str, Any]) -> dict[str, str]:
    items = state.get("line_items", [])
    sentiment = state.get("sentiment")
    registry = state.get("evidence_registry", {})
    return {
        "metrics_ledger_json": _j([{"metric_id": m.metric_id, "label": m.metric_label, "formatted_value": m.formatted_value,
                                    "status": m.status} for m in state.get("metrics_ledger", [])]),
        "line_items_json": _j([{"item_id": li.item_id, "label": li.reported_label, "formatted_value": li.formatted_value}
                               for li in items if li.field_key != "segment_revenue"]),
        "segment_items_json": _j([{"item_id": li.item_id, "segment": li.segment_name, "formatted_value": li.formatted_value}
                                  for li in items if li.field_key == "segment_revenue"]),
        "quarterly_metrics_json": _j([{"metric_id": m.metric_id, "label": m.metric_label, "formatted_value": m.formatted_value}
                                      for m in state.get("quarterly_metrics", [])]),
        "red_flags_json": _j([{"rule_id": f.rule_id, "name": f.rule_name, "explanation": f.explanation,
                               "metric_ids": f.supporting_metric_ids} for f in state.get("red_flags", []) if f.fired]),
        "news_events_json": _j([{"event_id": n.event_id, "date": n.event_date, "headline": n.headline, "tier": n.highest_tier,
                                 "corroborated": n.corroborated_by_filing} for n in state.get("news_events", [])]),
        "signal_sentiment_json": _j([{"post_id": p.post_id, "audience": p.audience, "quote": p.quote_text,
                                      "corroborating_ids": p.corroborating_ids}
                                     for p in (sentiment.items if sentiment else []) if p.verdict == "signal"]),
        "peer_comps_json": _j(peer_table_json(state.get("peer_comps", []))),
        "contradictions_json": _j([{"contradiction_id": c.contradiction_id, "topic": c.topic, "winner": c.winning_source,
                                    "resolution": c.resolution_rationale} for c in state.get("contradictions", [])]),
        "management_roster_json": _j(state.get("management_roster", [])),
        "business_snippets_json": _j([{"evidence_id": k, "text": v.verbatim_snippet} for k, v in registry.items()
                                      if k.endswith(("_business", "_kmp"))]),
    }


def clean_insights(out: SynthesisOut, state: dict[str, Any]) -> tuple[list[InsightCard], list[str]]:
    known = {
        "supporting_metric_ids": {m.metric_id for m in state.get("metrics_ledger", [])} |
                                 {m.metric_id for m in state.get("quarterly_metrics", [])},
        "supporting_item_ids": {li.item_id for li in state.get("line_items", [])},
        "supporting_red_flag_ids": {f.rule_id for f in state.get("red_flags", []) if f.fired},
        "supporting_news_ids": {n.event_id for n in state.get("news_events", [])},
        "supporting_sentiment_ids": {p.post_id for p in (state["sentiment"].items if state.get("sentiment") else [])},
        "supporting_contradiction_ids": {c.contradiction_id for c in state.get("contradictions", [])},
    }
    cards, dropped = [], []
    for i, ins in enumerate(out.insights, start=1):
        fields = {}
        for name, ok in known.items():
            ids = getattr(ins, name)
            fields[name] = [x for x in ids if x in ok]
            dropped += [f"{ins.insight_id}: {x}" for x in ids if x not in ok]
        cards.append(InsightCard(insight_id=f"ins_{i:02d}", title=ins.title, body=ins.body,
                                 classification=ClaimClassification(ins.classification), polarity=Polarity(ins.polarity),
                                 **fields))
    return cards, dropped


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity = state["entity"]
    calls = []
    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s11", schema=SynthesisOut, model=ctx.settings.synthesis_model,
                                       system=SYNTHESIS_SYSTEM.format(legal_name=entity.legal_name),
                                       user=SYNTHESIS_USER.format(**synthesis_inputs(state)))
    except LLMError as exc:
        calls.append(trace(ctx, "synthesize_insights_and_summary", L, {}, t.ms, str(exc), "error"))
        p = panel(S, [entity.legal_name], calls, [(L, "Synthesis failed")], "No insights",
                  "The report keeps every computed table; no narrative is written without the model.", GUARDS)
        return StageResult(StageStatus.WARNING, "Synthesis failed; tables only",
                           {"insights": [], "provenance_nodes": [], "provenance_edges": [], "summary": None,
                            "stated_gaps": ["Narrative synthesis unavailable: model call failed"]}, p)
    calls.append(trace(ctx, "synthesize_insights_and_summary", L, {"model": ctx.settings.synthesis_model},
                       res.duration_ms, res.parsed.executive_summary.verdict_headline))
    cards, dropped = clean_insights(res.parsed, state)
    if dropped:
        calls.append(trace(ctx, "validate_insight_ids", C, {"insights": len(cards)}, 0,
                       "Dropped unknown ids: " + ", ".join(dropped[:10]), "fallback"))
    facts, inferences, hypotheses = count_claims(cards)
    summary = ExecutiveSummary(**res.parsed.executive_summary.model_dump(), fact_count=facts, inference_count=inferences,
                               hypothesis_count=hypotheses)
    nodes, edges = build_provenance_dag(as_run_state({**state, "insights": cards}))
    calls.append(trace(ctx, "build_provenance_dag", C, {"insights": len(cards)}, 0, f"{len(nodes)} nodes, {len(edges)} edges"))
    actions = [(L, f"Wrote {len(cards)} insight(s) and the executive summary citing ledger tokens"),
               (C, f"Checked supporting ids; counted {facts} fact, {inferences} inference, {hypotheses} hypothesis"),
               (C, "Built the provenance graph from the ids each insight cites")]
    p = panel(S, ["Ledger, red flags, events, signals, peers, contradictions"], calls, actions,
              res.parsed.executive_summary.verdict_headline,
              "Every figure in the prose is a copied formatted value followed by its citation token.", GUARDS)
    return StageResult(StageStatus.COMPLETED, f"{len(cards)} insights: {facts} fact, {inferences} inference, "
                       f"{hypotheses} hypothesis", {"insights": cards, "provenance_nodes": nodes,
                                                   "provenance_edges": edges, "summary": summary}, p)
