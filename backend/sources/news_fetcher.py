"""Tier-3 news: Google News RSS and the GDELT DOC API, filtered to tier-1 publishers and de-duplicated in code."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus

import feedparser
import httpx

from backend.sources import FetchTrace, SourceError
from backend.sources.rate_limiter import DomainLimiter, get_with_retry

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}+when:365d&hl={hl}&gl={gl}&ceid={gl}:en"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

# Section 7.1, T3 row: only these publishers count; everything else is dropped before clustering.
T3_PUBLISHERS: dict[str, tuple[str, ...]] = {
    "IN": ("Economic Times", "The Economic Times", "ET Markets", "Mint", "Livemint", "Business Standard", "Moneycontrol",
           "Reuters", "Inc42", "Entrackr", "TechCrunch"),
    "US": ("Reuters", "Bloomberg", "The Wall Street Journal", "WSJ", "Financial Times", "CNBC"),
    "GLOBAL_PRIVATE": ("Economic Times", "Mint", "TechCrunch", "Inc42", "Entrackr", "Reuters"),
}
T3_DOMAINS = {"economictimes.indiatimes.com": "Economic Times", "livemint.com": "Mint", "business-standard.com":
              "Business Standard", "moneycontrol.com": "Moneycontrol", "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
              "wsj.com": "The Wall Street Journal", "ft.com": "Financial Times", "cnbc.com": "CNBC", "inc42.com": "Inc42",
              "entrackr.com": "Entrackr", "techcrunch.com": "TechCrunch"}
DEDUP_THRESHOLD = 0.72


@dataclass(frozen=True)
class Article:
    article_id: str
    title: str
    publisher: str
    url: str
    published: Optional[datetime]
    source: str  # "google_news" | "gdelt"


def _clean_title(title: str, publisher: str) -> str:
    # Google News appends " - Publisher" to every title.
    if publisher and title.endswith(f" - {publisher}"):
        return title[: -len(publisher) - 3]
    return title


def allowed_publisher(publisher: str, jurisdiction: str) -> bool:
    allow = T3_PUBLISHERS.get(jurisdiction, T3_PUBLISHERS["US"])
    p = publisher.lower()
    return any(a.lower() == p or a.lower() in p for a in allow)


def parse_google_news_rss(xml: str, jurisdiction: str) -> list[Article]:
    feed = feedparser.parse(xml)
    out = []
    for i, e in enumerate(feed.entries):
        publisher = (e.get("source") or {}).get("title", "") if isinstance(e.get("source"), dict) else ""
        if not publisher or not allowed_publisher(publisher, jurisdiction):
            continue
        try:
            published = parsedate_to_datetime(e.published) if e.get("published") else None
        except (TypeError, ValueError):
            published = None
        out.append(Article(f"gn_{i}", _clean_title(e.get("title", ""), publisher), publisher, e.get("link", ""), published,
                           "google_news"))
    return out


def parse_gdelt(payload: dict, jurisdiction: str) -> list[Article]:
    out = []
    for i, a in enumerate(payload.get("articles", [])):
        domain = (a.get("domain") or "").lower().removeprefix("www.")
        publisher = T3_DOMAINS.get(domain)
        if not publisher or not allowed_publisher(publisher, jurisdiction):
            continue
        try:
            published = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            published = None
        out.append(Article(f"gd_{i}", a.get("title", ""), publisher, a.get("url", ""), published, "gdelt"))
    return out


def _shingles(text: str, n: int = 3) -> set[str]:
    words = re.findall(r"[a-z0-9₹$%.]+", text.lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate_articles(articles: list[Article], threshold: float = DEDUP_THRESHOLD) -> list[list[Article]]:
    """deduplicate_articles_minhash: exact Jaccard on word 3-shingles (the article counts here are small enough
    that MinHash's approximation buys nothing). Union-find groups every pair at or above the threshold."""
    parent = list(range(len(articles)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sh = [_shingles(a.title) for a in articles]
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            if jaccard(sh[i], sh[j]) >= threshold:
                parent[find(j)] = find(i)
    groups: dict[int, list[Article]] = {}
    for i, a in enumerate(articles):
        groups.setdefault(find(i), []).append(a)
    return sorted(groups.values(), key=lambda g: -len(g))


class NewsFetcher:
    def __init__(self, client: httpx.AsyncClient, limiter: DomainLimiter):
        self.client, self.limiter = client, limiter

    async def google_news(self, query: str, jurisdiction: str) -> list[Article]:
        hl, gl = ("en-IN", "IN") if jurisdiction == "IN" else ("en-US", "US")
        url = GOOGLE_NEWS_RSS.format(q=quote_plus(f'"{query}"'), hl=hl, gl=gl)
        resp = await get_with_retry(self.client, url, self.limiter)
        return parse_google_news_rss(resp.text, jurisdiction)

    async def gdelt(self, query: str, jurisdiction: str) -> list[Article]:
        resp = await get_with_retry(self.client, GDELT_DOC, self.limiter, params={
            "query": f'"{query}"', "mode": "ArtList", "format": "json", "timespan": "12m", "maxrecords": 75, "sort": "DateDesc"})
        return parse_gdelt(resp.json(), jurisdiction)

    async def fetch(self, query: str, jurisdiction: str, now: Optional[datetime] = None) -> tuple[list[Article], list[FetchTrace], list[str]]:
        """fetch_news_and_exchange_releases (press part): both feeds, last 12 months, tier-1 publishers only."""
        cutoff = (now or datetime.now(UTC)) - timedelta(days=365)
        articles: list[Article] = []
        traces: list[FetchTrace] = []
        gaps: list[str] = []
        for name, fn in (("google_news_rss", self.google_news), ("gdelt_doc_api", self.gdelt)):
            t = FetchTrace(name, {"query": query, "jurisdiction": jurisdiction})
            try:
                got = await fn(query, jurisdiction)
                got = [a for a in got if a.published is None or a.published >= cutoff]
                articles += got
                traces.append(t.finish("ok", [a.title for a in got[:5]]))
            except SourceError as exc:
                traces.append(t.finish("error", str(exc)))
                gaps.append(f"{name.replace('_', ' ')} unavailable due to upstream error")
        return articles, traces, gaps
