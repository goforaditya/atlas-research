"""Tier-4 compliant sentiment sources and the code-only bot/spam filter (Sections 4 Stage 8 and 7.3).

Used: Reddit's official Data API with an OAuth script app, the Hacker News Algolia API, and public Play Store
review pages via google-play-scraper. Never used: LinkedIn, Glassdoor, AmbitionBox, X or anything behind a login.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import httpx

from backend.sources import FetchTrace, SourceConfigError, SourceError
from backend.sources.rate_limiter import DomainLimiter, get_with_retry

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_SEARCH_URL = "https://oauth.reddit.com/search"
REDDIT_ABOUT_URL = "https://oauth.reddit.com/user/{name}/about"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_USER_URL = "https://hn.algolia.com/api/v1/users/{name}"

MIN_ACCOUNT_AGE_DAYS = 30
MIN_KARMA = 15
DUP_JACCARD = 0.85
DUP_MIN_OTHER_ACCOUNTS = 3
BURST_SIZE = 20
BURST_WINDOW = timedelta(minutes=60)
MIN_REVIEW_WORDS = 6


@dataclass(frozen=True)
class Post:
    post_id: str
    platform: str  # "Reddit" | "Hacker News" | "Play Store"
    author: str
    text: str
    created_at: datetime
    url: str = ""
    rating: Optional[int] = None  # Play Store stars
    karma: Optional[int] = None
    account_created_at: Optional[datetime] = None


@dataclass
class FilterResult:
    kept: list[Post]
    removed: int
    reasons: Counter


def _shingles5(text: str) -> set[str]:
    w = re.findall(r"\w+", text.lower())
    return {" ".join(w[i:i + 5]) for i in range(len(w) - 4)} if len(w) >= 5 else ({" ".join(w)} if w else set())


def filter_bots_and_spam(posts: list[Post], now: Optional[datetime] = None) -> FilterResult:
    """Per-platform rules from Stage 8. Every removal is counted by reason; nothing is sent to the LLM."""
    now = now or datetime.now(UTC)
    reasons: Counter = Counter()
    drop: set[str] = set()

    for p in posts:
        if p.created_at < now - timedelta(days=365):
            drop.add(p.post_id)
            reasons["older than 12 months"] += 1
        elif p.platform == "Reddit" and (
            (p.account_created_at is not None and (p.created_at - p.account_created_at).days < MIN_ACCOUNT_AGE_DAYS)
            or (p.karma is not None and p.karma < MIN_KARMA)
        ):
            drop.add(p.post_id)
            reasons["new or low-karma Reddit account"] += 1
        elif p.platform == "Hacker News" and p.karma is not None and p.karma < MIN_KARMA:
            drop.add(p.post_id)
            reasons["low-karma HN account"] += 1
        elif p.platform == "Play Store" and len(p.text.split()) < MIN_REVIEW_WORDS:
            drop.add(p.post_id)
            reasons["review under 6 words"] += 1

    # Copy-paste campaigns: near-identical text from 3 or more other accounts.
    sh = {p.post_id: _shingles5(p.text) for p in posts}
    for p in posts:
        if p.post_id in drop:
            continue
        others = {q.author for q in posts if q.author != p.author and q.post_id != p.post_id
                  and sh[p.post_id] and sh[q.post_id]
                  and len(sh[p.post_id] & sh[q.post_id]) / len(sh[p.post_id] | sh[q.post_id]) >= DUP_JACCARD}
        if len(others) >= DUP_MIN_OTHER_ACCOUNTS:
            drop.add(p.post_id)
            reasons["duplicate text across accounts"] += 1

    # Play Store bursts: more than 20 reviews with the same star rating inside 60 minutes.
    play = sorted((p for p in posts if p.platform == "Play Store" and p.rating is not None), key=lambda p: p.created_at)
    for rating in {p.rating for p in play}:
        same = [p for p in play if p.rating == rating]
        start = 0
        for end in range(len(same)):
            while same[end].created_at - same[start].created_at > BURST_WINDOW:
                start += 1
            if end - start + 1 > BURST_SIZE:
                for p in same[start:end + 1]:
                    if p.post_id not in drop:
                        drop.add(p.post_id)
                        reasons["review burst"] += 1

    kept = [p for p in posts if p.post_id not in drop]
    return FilterResult(kept, len(posts) - len(kept), reasons)


class SentimentFetcher:
    def __init__(self, client: httpx.AsyncClient, limiter: DomainLimiter, *, reddit_client_id: str = "",
                 reddit_client_secret: str = "", user_agent: str = "AtlasResearchBot/1.0",
                 play_reviews: Optional[Callable[..., Any]] = None, play_search: Optional[Callable[..., Any]] = None):
        self.client, self.limiter = client, limiter
        self.reddit_id, self.reddit_secret, self.user_agent = reddit_client_id, reddit_client_secret, user_agent
        self._reddit_token: Optional[str] = None
        self._karma_cache: dict[str, tuple[Optional[int], Optional[datetime]]] = {}
        self._play_reviews, self._play_search = play_reviews, play_search

    # ---------------- Reddit (official Data API, OAuth app-only)
    async def _reddit_auth(self) -> str:
        if not (self.reddit_id and self.reddit_secret):
            raise SourceConfigError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        if self._reddit_token:
            return self._reddit_token
        await self.limiter.bucket(REDDIT_TOKEN_URL).acquire()
        resp = await self.client.post(REDDIT_TOKEN_URL, data={"grant_type": "client_credentials"},
                                      auth=(self.reddit_id, self.reddit_secret), headers={"User-Agent": self.user_agent})
        if not resp.is_success:
            raise SourceError(f"Reddit OAuth returned {resp.status_code}")
        self._reddit_token = resp.json()["access_token"]
        return self._reddit_token

    async def _reddit_author(self, name: str, headers: dict[str, str]) -> tuple[Optional[int], Optional[datetime]]:
        if name not in self._karma_cache:
            try:
                data = (await get_with_retry(self.client, REDDIT_ABOUT_URL.format(name=name), self.limiter, headers=headers)).json()["data"]
                karma = data.get("total_karma", (data.get("link_karma") or 0) + (data.get("comment_karma") or 0))
                self._karma_cache[name] = (karma, datetime.fromtimestamp(data["created_utc"], UTC))
            except (SourceError, KeyError):
                self._karma_cache[name] = (None, None)
        return self._karma_cache[name]

    async def reddit(self, query: str) -> list[Post]:
        token = await self._reddit_auth()
        headers = {"Authorization": f"bearer {token}", "User-Agent": self.user_agent}
        resp = await get_with_retry(self.client, REDDIT_SEARCH_URL, self.limiter, headers=headers,
                                    params={"q": query, "limit": 50, "sort": "new", "t": "year"})
        out = []
        for child in resp.json().get("data", {}).get("children", []):
            d = child.get("data", {})
            author = d.get("author", "")
            if not author or author == "[deleted]":
                continue
            karma, created = await self._reddit_author(author, headers)
            text = " ".join(x for x in (d.get("title", ""), d.get("selftext", "")) if x).strip()
            out.append(Post(f"rd_{d.get('id')}", "Reddit", author, text, datetime.fromtimestamp(d["created_utc"], UTC),
                            f"https://www.reddit.com{d.get('permalink', '')}", karma=karma, account_created_at=created))
        return out

    # ---------------- Hacker News (Algolia)
    async def hacker_news(self, query: str) -> list[Post]:
        resp = await get_with_retry(self.client, HN_SEARCH_URL, self.limiter, params={"query": query, "tags": "comment"})
        out = []
        for h in resp.json().get("hits", []):
            author = h.get("author") or ""
            if author not in self._karma_cache:
                try:
                    u = (await get_with_retry(self.client, HN_USER_URL.format(name=author), self.limiter)).json()
                    self._karma_cache[author] = (u.get("karma"), None)
                except SourceError:
                    self._karma_cache[author] = (None, None)
            text = re.sub(r"<[^>]+>", " ", h.get("comment_text") or "")
            out.append(Post(f"hn_{h.get('objectID')}", "Hacker News", author, " ".join(text.split()),
                            datetime.fromtimestamp(h["created_at_i"], UTC),
                            f"https://news.ycombinator.com/item?id={h.get('objectID')}", karma=self._karma_cache[author][0]))
        return out

    # ---------------- Play Store (public review pages, max 200 per app)
    async def play_store(self, query: str, developer_name: str, country: str = "in") -> list[Post]:
        if self._play_search is None or self._play_reviews is None:
            from google_play_scraper import Sort, reviews, search  # imported lazily: network library

            self._play_search = self._play_search or search
            self._play_reviews = self._play_reviews or (lambda app_id, **kw: reviews(app_id, sort=Sort.NEWEST, **kw))
        apps = await asyncio.to_thread(self._play_search, query, lang="en", country=country, n_hits=5)
        dev = re.sub(r"[^a-z0-9]", "", developer_name.lower())[:12]
        apps = [a for a in apps if dev and dev in re.sub(r"[^a-z0-9]", "", (a.get("developer") or "").lower())]
        out: list[Post] = []
        for app in apps[:2]:
            await self.limiter.sleep(2)  # Section 7.3: 1 request / 2 seconds
            result = await asyncio.to_thread(self._play_reviews, app["appId"], lang="en", country=country, count=200)
            rows = result[0] if isinstance(result, tuple) else result
            for r in rows:
                at = r["at"] if r["at"].tzinfo else r["at"].replace(tzinfo=UTC)
                out.append(Post(f"ps_{r['reviewId']}", "Play Store", r.get("userName", ""), r.get("content") or "", at,
                                f"https://play.google.com/store/apps/details?id={app['appId']}", rating=r.get("score")))
        return out

    async def fetch(self, query: str, developer_name: str, jurisdiction: str) -> tuple[list[Post], list[FetchTrace], list[str]]:
        """fetch_public_sentiment_posts: every compliant source; a missing source is a stated gap, not an error."""
        posts: list[Post] = []
        traces: list[FetchTrace] = []
        gaps: list[str] = []
        country = "in" if jurisdiction == "IN" else "us"
        calls = (("reddit_search", lambda: self.reddit(query)), ("hn_algolia_search", lambda: self.hacker_news(query)),
                 ("play_store_reviews", lambda: self.play_store(query, developer_name, country)))
        for name, fn in calls:
            t = FetchTrace(name, {"query": query})
            try:
                got = await fn()
                posts += got
                traces.append(t.finish("ok", f"{len(got)} posts"))
            except SourceConfigError as exc:
                traces.append(t.finish("fallback", str(exc)))
                gaps.append(f"{name.split('_')[0].title()} skipped: {exc}")
            except Exception as exc:  # noqa: BLE001 - google-play-scraper raises its own types
                traces.append(t.finish("error", str(exc)))
                gaps.append(f"{name.replace('_', ' ')} unavailable due to upstream error")
        return posts, traces, gaps
