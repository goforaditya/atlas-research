"""Stage 11: cross-source synthesis. Code builds the provenance DAG from the ids each insight references."""
from __future__ import annotations

from backend.schemas.common import ClaimClassification, SourceTier
from backend.schemas.intelligence import InsightCard, ProvenanceEdge, ProvenanceNode
from backend.schemas.state import ResearchRunState


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
