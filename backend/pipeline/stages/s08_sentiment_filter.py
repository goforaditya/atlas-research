"""Stage 8: sentiment with signal/noise filter. Code computes every count and percentage."""
from __future__ import annotations

from decimal import Decimal

from backend.schemas.intelligence import AudienceSentimentSummary, SentimentAggregate, SentimentItem


def largest_remainder_pcts(counts: list[int]) -> list[int]:
    """Integer percentages that sum to exactly 100 (largest-remainder / Hamilton method)."""
    total = sum(counts)
    if total == 0:
        return [0 for _ in counts]
    raw = [Decimal(c) * 100 / Decimal(total) for c in counts]
    floors = [int(r) for r in raw]
    short = 100 - sum(floors)
    order = sorted(range(len(counts)), key=lambda i: (raw[i] - floors[i], -i), reverse=True)
    for i in order[:short]:
        floors[i] += 1
    return floors


def aggregate_sentiment(
    items: list[SentimentItem],
    total_posts_fetched: int,
    bots_and_duplicates_removed: int,
    audience_breakdown: list[AudienceSentimentSummary],
) -> SentimentAggregate:
    """Percentages are over all classified items (signal and noise)."""
    counts = [sum(1 for i in items if i.polarity.value == p) for p in ("positive", "neutral", "negative")]
    pos, neu, neg = largest_remainder_pcts(counts)
    return SentimentAggregate(
        total_posts_fetched=total_posts_fetched,
        bots_and_duplicates_removed=bots_and_duplicates_removed,
        posts_classified=len(items),
        positive_pct=pos,
        neutral_pct=neu,
        negative_pct=neg,
        audience_breakdown=audience_breakdown,
        items=items,
    )
