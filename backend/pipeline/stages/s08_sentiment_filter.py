"""Stage 8: sentiment with signal/noise filter. Code computes every count and percentage."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from backend.pipeline.graph import RunContext, StageResult, Timer, from_fetch, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import SENTIMENT_SIGNAL_NOISE_SYSTEM, SENTIMENT_SIGNAL_NOISE_USER, SentimentOut
from backend.schemas.common import ActorBadge, EvidenceCitation, Polarity, SourceTier, StageId, StageStatus
from backend.schemas.intelligence import AudienceSentimentSummary, SentimentAggregate, SentimentItem
from backend.schemas.state import ResolvedEntity
from backend.sources import TIER_WEIGHTS
from backend.sources.sentiment_fetcher import Post, filter_bots_and_spam


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


# ---------------------------------------------------------------- live stage

S = StageId.S08_SENTIMENT
C, L = ActorBadge.CODE, ActorBadge.LLM
MAX_POSTS_TO_MODEL = 60
GUARDS = ["Bots, new or low-karma accounts, copy-paste campaigns and review bursts are removed in code first",
          "Counts and percentages are computed in code (largest remainder), never by the model",
          "Quotes are the post text itself; a post id the model invents is dropped",
          "A signal needs a real metric or event id, or at least 3 posts on the same theme; otherwise it is noise"]
PLATFORM_TIER = SourceTier.T4


def build_items(out: SentimentOut, kept: dict[str, Post], known_ids: set[str]) -> tuple[list[SentimentItem], list[str]]:
    items: list[SentimentItem] = []
    notes: list[str] = []
    seen: set[str] = set()
    for it in out.items:
        post = kept.get(it.post_id)
        if post is None or it.post_id in seen:
            notes.append(f"{it.post_id}: not among the filtered posts")
            continue
        seen.add(it.post_id)
        ids = [i for i in it.corroborating_ids if i in known_ids]
        quote = " ".join(post.text.split())[:280]
        ev = EvidenceCitation(evidence_id=f"ev_post_{_slug(it.post_id)}", document_id=f"post_{_slug(it.post_id)}",
                              document_title=post.platform, source_url=post.url or "", source_tier=PLATFORM_TIER,
                              trust_weight=TIER_WEIGHTS[PLATFORM_TIER], verbatim_snippet=quote, extracted_value_raw=quote,
                              retrieved_at=post.created_at.isoformat())
        items.append(SentimentItem(post_id=it.post_id, platform=post.platform, audience=it.audience, quote_text=quote,
                                   polarity=Polarity(it.polarity), verdict=it.verdict, verdict_reasons=it.verdict_reasons,
                                   corroborating_ids=ids, evidence=ev))
    # A signal with no valid corroboration survives only as part of a recurring theme (>= 3 such posts).
    themes = Counter((i.audience, i.polarity) for i in items if i.verdict == "signal" and not i.corroborating_ids)
    final = []
    for i in items:
        if i.verdict == "signal" and not i.corroborating_ids and themes[(i.audience, i.polarity)] < 3:
            notes.append(f"{i.post_id}: signal without corroboration or a recurring theme, set to noise")
            i = i.model_copy(update={"verdict": "noise",
                                     "verdict_reasons": i.verdict_reasons + ["No corroborating metric or filing; isolated post"]})
        final.append(i)
    return final, notes


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    today = state.get("today") or datetime.now(UTC).date()
    now = datetime(today.year, today.month, today.day, tzinfo=UTC)
    calls, gaps = [], []
    if ctx.sentiment is None:
        posts, traces, gaps = [], [], ["Public sentiment sources not configured"]
    else:
        posts, traces, gaps = await ctx.sentiment.fetch(entity.legal_name, entity.legal_name, entity.jurisdiction)
    calls += [from_fetch(ctx, t) for t in traces]
    t = Timer()
    filtered = filter_bots_and_spam(posts, now)
    calls.append(trace(ctx, "filter_bots_and_spam", C, {"posts": len(posts)}, t.ms,
                       f"kept {len(filtered.kept)}, removed {filtered.removed}: {dict(filtered.reasons)}"))
    ledger = state.get("metrics_ledger", [])
    news = state.get("news_events", [])
    actions = [(C, f"Fetched {len(posts)} public post(s); removed {filtered.removed} bot, duplicate or stale post(s)")]

    items: list[SentimentItem] = []
    breakdown: list[AudienceSentimentSummary] = []
    if filtered.kept:
        kept = {p.post_id: p for p in filtered.kept[:MAX_POSTS_TO_MODEL]}
        posts_json = [{"post_id": p.post_id, "platform": p.platform, "text": p.text[:500],
                       "date": p.created_at.date().isoformat(), "rating": p.rating} for p in kept.values()]
        metrics_json = [{"metric_id": m.metric_id, "label": m.metric_label, "value": m.formatted_value}
                        for m in ledger if m.status == "computed"]
        news_json = [{"event_id": n.event_id, "headline": n.headline, "date": n.event_date,
                      "tier": n.highest_tier.value} for n in news]
        t = Timer()
        try:
            res = await ctx.llm.structured(stage="s08", schema=SentimentOut,
                                           system=SENTIMENT_SIGNAL_NOISE_SYSTEM.format(legal_name=entity.legal_name),
                                           user=SENTIMENT_SIGNAL_NOISE_USER.format(posts_json=json.dumps(posts_json),
                                                                                    metrics_summary_json=json.dumps(metrics_json),
                                                                                    news_summary_json=json.dumps(news_json)))
            calls.append(trace(ctx, "evaluate_signal_vs_noise", L, {"posts": len(kept)}, res.duration_ms,
                               [{"post": i.post_id, "verdict": i.verdict} for i in res.parsed.items[:8]]))
            known = {m.metric_id for m in ledger} | {n.event_id for n in news} | {li.item_id for li in state.get("line_items", [])}
            items, notes = build_items(res.parsed, kept, known)
            if notes:
                calls.append(trace(ctx, "validate_sentiment_items", C, {"items": len(res.parsed.items)}, 0,
                                   "; ".join(notes[:8]), "fallback"))
            breakdown = [AudienceSentimentSummary(**a.model_dump()) for a in res.parsed.audience_breakdown]
            actions.append((L, "Classified audience and polarity and judged signal versus noise"))
        except LLMError as exc:
            calls.append(trace(ctx, "evaluate_signal_vs_noise", L, {"posts": len(kept)}, t.ms, str(exc), "error"))
            gaps.append("Sentiment classification failed; posts are counted but not classified")
    else:
        gaps.append("No public posts survived filtering; sentiment is unavailable")
    agg = aggregate_sentiment(items, len(posts), filtered.removed, breakdown)
    signal = sum(i.verdict == "signal" for i in items)
    actions.append((C, f"Counted {len(items)} classified post(s): {agg.positive_pct}% positive, {agg.neutral_pct}% neutral, "
                       f"{agg.negative_pct}% negative"))
    p = panel(S, [f"{len(ledger)} metrics, {len(news)} events for corroboration"], calls, actions,
              f"{signal} signal, {len(items) - signal} noise",
              "Only posts tied to a metric, a filing or a recurring theme count as signal.", GUARDS)
    patch: dict[str, Any] = {"sentiment": agg}
    if gaps:
        patch["stated_gaps"] = gaps
    status = StageStatus.COMPLETED if items else StageStatus.WARNING
    return StageResult(status, f"Kept {len(filtered.kept)} of {len(posts)} posts; {signal} signal", patch, p)
