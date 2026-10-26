"""Stage 7: press and exchange releases for the last 12 months. Code fetches, deduplicates, dates and checks
corroboration; the LLM only groups clusters into events and writes headline and summary text."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

from backend.pipeline.graph import RunContext, StageResult, Timer, from_fetch, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import NEWS_CLUSTERING_SYSTEM, NEWS_CLUSTERING_USER, NewsClusteringOut
from backend.pipeline.stages.s03_filing_retrieval import now_iso, slug_for
from backend.schemas.common import ActorBadge, EvidenceCitation, Polarity, SourceTier, StageId, StageStatus
from backend.schemas.intelligence import NewsEvent
from backend.schemas.state import ResolvedEntity
from backend.sources import TIER_WEIGHTS, SourceError
from backend.sources.news_fetcher import Article, deduplicate_articles
from backend.sources.sec_edgar import recent_filings

S = StageId.S07_NEWS
C, L = ActorBadge.CODE, ActorBadge.LLM
MAX_EVENTS = 10
CORROBORATION_WINDOW_DAYS = 45
GUARDS = ["Syndicated copies are merged in code (Jaccard on 3-word shingles at 0.72 or above)",
          "Event dates come from the primary source's own timestamp, never from the model",
          "Corroborated only when a T1/T2 filing of the same kind falls within 45 days; otherwise the flag is cleared",
          "An event whose primary source id is not in the input is dropped"]

# Exchange-announcement categories (india_filings.categorise) and 8-K items that can corroborate an event category.
FILING_CATEGORY = {"management_change": "Management", "acquisition": "Acquisition", "fundraise": "Fundraise",
                   "regulatory": "Regulatory"}
EIGHT_K_ITEMS = {"5.02": "Management", "2.01": "Acquisition", "1.01": "Partnership", "3.02": "Fundraise",
                 "8.01": None, "7.01": None}


def month_label(d: date | datetime) -> str:
    return d.strftime("%b %Y")


def _as_date(x: Any) -> Optional[date]:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


async def fetch_exchange_releases(ctx: RunContext, entity: ResolvedEntity, today: date, calls: list) -> list[dict[str, Any]]:
    """BSE announcements (India) or Form 8-K filings (US), as T1/T2 filing rows for corroboration."""
    rows: list[dict[str, Any]] = []
    t = Timer()
    try:
        if entity.jurisdiction == "IN" and entity.bse_scrip_code and ctx.india is not None:
            docs = await ctx.india.bse_announcements(entity.bse_scrip_code, today=today)
            rows = [{"title": d.title, "date": d.filed_on, "url": d.url, "tier": d.tier.value,
                     "event_category": FILING_CATEGORY.get(d.category), "source": "BSE filing"} for d in docs]
        elif entity.jurisdiction == "US" and entity.cik and ctx.sec is not None:
            subs = await ctx.sec.fetch_submissions(entity.cik)
            cutoff = (today - timedelta(days=365)).isoformat()
            for f in recent_filings(subs, forms=("8-K",), limit=40):
                if f["filing_date"] < cutoff:
                    continue
                items = [i.strip() for i in (f.get("items") or "").split(",") if i.strip()]
                cat = next((EIGHT_K_ITEMS[i] for i in items if EIGHT_K_ITEMS.get(i)), None)
                rows.append({"title": f"Form 8-K items {', '.join(items)}" if items else "Form 8-K", "date": f["filing_date"],
                             "url": f["url"], "tier": "T2", "event_category": cat, "source": "SEC 8-K"})
        else:
            calls.append(trace(ctx, "fetch_exchange_releases", C, {"entity": entity.legal_name}, 0,
                               "No exchange source for this entity", "fallback"))
            return []
    except SourceError as exc:
        calls.append(trace(ctx, "fetch_exchange_releases", C, {"entity": entity.legal_name}, t.ms, str(exc), "error"))
        return []
    for i, r in enumerate(rows, start=1):
        r["filing_id"] = f"fl_{i:02d}"
    calls.append(trace(ctx, "fetch_exchange_releases", C, {"entity": entity.legal_name}, t.ms,
                       [r["title"] for r in rows[:5]]))
    return rows


def corroborating_filing(category: str, when: Optional[date], filings: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if when is None:
        return None
    for f in filings:
        fd = _as_date(f["date"])
        if f.get("event_category") == category and fd and abs((fd - when).days) <= CORROBORATION_WINDOW_DAYS:
            return f
    return None


def build_events(out: NewsClusteringOut, clusters: dict[str, list[Article]], articles: dict[str, Article],
                 filings: dict[str, dict[str, Any]], slug: str) -> tuple[list[NewsEvent], list[str]]:
    events: list[NewsEvent] = []
    dropped: list[str] = []
    cluster_of = {a.article_id: cid for cid, arts in clusters.items() for a in arts}
    for i, e in enumerate(out.events[:MAX_EVENTS], start=1):
        pid = e.primary_article_id
        event_id = f"news_{slug}_{i:02d}"
        if pid in filings:
            f = filings[pid]
            when = _as_date(f["date"])
            tier = SourceTier(f["tier"])
            source_names, merged = [f["source"]], 1
            ev = EvidenceCitation(evidence_id=f"ev_{event_id}", document_id=f"filing_{pid}", document_title=f["source"],
                                  source_url=f["url"], source_tier=tier, trust_weight=TIER_WEIGHTS[tier],
                                  verbatim_snippet=f["title"], extracted_value_raw=f["title"], retrieved_at=now_iso())
            corroborated = True
        elif pid in articles:
            art = articles[pid]
            group = clusters.get(cluster_of.get(pid, ""), [art])
            when = _as_date(art.published)
            match = corroborating_filing(e.category, when, list(filings.values()))
            corroborated = e.corroborated_by_filing and match is not None
            tier = SourceTier(match["tier"]) if corroborated else SourceTier.T3
            source_names = sorted({a.publisher for a in group}) + ([match["source"]] if corroborated else [])
            merged = len(group)
            ev = EvidenceCitation(evidence_id=f"ev_{event_id}", document_id=f"article_{pid}", document_title=art.publisher,
                                  source_url=art.url, source_tier=SourceTier.T3, trust_weight=TIER_WEIGHTS[SourceTier.T3],
                                  verbatim_snippet=art.title, extracted_value_raw=art.title, retrieved_at=now_iso())
        else:
            dropped.append(f"{e.headline!r}: primary source {pid!r} not in the input")
            continue
        events.append(NewsEvent(
            event_id=event_id, event_date=month_label(when) if when else e.event_date, category=e.category,
            headline=e.headline, summary=e.summary, polarity=Polarity(e.polarity), source_names=source_names,
            highest_tier=tier, corroborated_by_filing=corroborated, article_count_merged=merged, evidence=ev))
    return events, dropped


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    today: date = state.get("today") or datetime.now(UTC).date()
    now = datetime(today.year, today.month, today.day, tzinfo=UTC)
    slug = slug_for(entity)
    calls, gaps = [], []
    articles: list[Article] = []
    if ctx.news is not None:
        articles, traces, gaps = await ctx.news.fetch(entity.legal_name, entity.jurisdiction, now)
        calls += [from_fetch(ctx, t) for t in traces]
    else:
        gaps.append("Press search not configured; news is limited to exchange filings")
    filings = await fetch_exchange_releases(ctx, entity, today, calls)

    t = Timer()
    groups = deduplicate_articles(articles)
    clusters = {f"cl_{i:02d}": g for i, g in enumerate(groups, start=1)}
    calls.append(trace(ctx, "deduplicate_articles_minhash", C, {"articles": len(articles)}, t.ms,
                       f"{len(articles)} articles -> {len(clusters)} clusters"))
    if not clusters and not filings:
        p = panel(S, [entity.legal_name], calls, [(C, "No press articles or exchange releases in the last 12 months")],
                  "No events", "Nothing to classify.", GUARDS)
        return StageResult(StageStatus.WARNING, "No news or filings found in the last 12 months",
                           {"news_events": [], "stated_gaps": gaps + ["No material news events found in the last 12 months"]}, p)

    clusters_json = [{"cluster_id": cid, "primary_article_id": g[0].article_id,
                      "articles": [{"article_id": a.article_id, "headline": a.title, "publisher": a.publisher,
                                    "date": a.published.date().isoformat() if a.published else None} for a in g]}
                     for cid, g in clusters.items()]
    filings_json = [{"filing_id": f["filing_id"], "title": f["title"], "date": f["date"], "tier": f["tier"]} for f in filings]
    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s07", schema=NewsClusteringOut,
                                       system=NEWS_CLUSTERING_SYSTEM.format(legal_name=entity.legal_name),
                                       user=NEWS_CLUSTERING_USER.format(clusters_json=json.dumps(clusters_json),
                                                                        filings_summary_json=json.dumps(filings_json)))
    except LLMError as exc:
        calls.append(trace(ctx, "classify_and_cluster_events", L, {"clusters": len(clusters)}, t.ms, str(exc), "error"))
        p = panel(S, [entity.legal_name], calls, [(L, "Event classification failed")], "No events",
                  "Without classification no event is reported; nothing is guessed.", GUARDS)
        return StageResult(StageStatus.WARNING, "News classification failed",
                           {"news_events": [], "stated_gaps": gaps + ["News events unavailable: classification failed"]}, p)
    calls.append(trace(ctx, "classify_and_cluster_events", L, {"clusters": len(clusters), "filings": len(filings)},
                       res.duration_ms, [e.headline for e in res.parsed.events[:5]]))
    by_article = {a.article_id: a for g in groups for a in g}
    events, dropped = build_events(res.parsed, clusters, by_article, {f["filing_id"]: f for f in filings}, slug)
    if dropped:
        calls.append(trace(ctx, "validate_events", C, {"events": len(res.parsed.events)}, 0, "; ".join(dropped), "fallback"))
    corroborated = sum(e.corroborated_by_filing for e in events)
    actions = [(C, f"Fetched {len(articles)} tier-1 press article(s) and {len(filings)} exchange release(s)"),
               (C, f"Merged syndicated copies into {len(clusters)} cluster(s)"),
               (L, "Grouped clusters into material events and wrote headlines and summaries"),
               (C, f"Set dates from source timestamps; {corroborated} event(s) corroborated by a filing")]
    p = panel(S, [f"{entity.legal_name}, last 12 months"], calls, actions, f"{len(events)} material event(s)",
              "Press is T3; only a matching exchange filing lifts an event to T1/T2.", GUARDS)
    status = StageStatus.COMPLETED if events else StageStatus.WARNING
    patch: dict[str, Any] = {"news_events": events}
    if gaps:
        patch["stated_gaps"] = gaps
    return StageResult(status, f"{len(events)} events from {len(clusters)} clusters; {corroborated} filing-corroborated",
                       patch, p)
