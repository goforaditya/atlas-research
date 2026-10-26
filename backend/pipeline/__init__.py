"""Exports the live LangGraph runner used by `POST /api/v1/runs` in live mode."""
from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional

LIVE_PIPELINE_READY = True
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024


@asynccontextmanager
async def live_context(run_id: str, settings: Any, db: Any = None, work_dir: Optional[Path] = None):
    """Real source clients, the Gemini client and a per-run work directory. A source that cannot be configured
    is left as None and its stage records a stated gap."""
    from backend.pipeline.graph import EventEmitter, RunContext
    from backend.pipeline.llm_client import GeminiClient
    from backend.sources import DEFAULT_USER_AGENT, SourceConfigError, SourceError, make_client
    from backend.sources.india_filings import IndiaFilingsClient
    from backend.sources.news_fetcher import NewsFetcher
    from backend.sources.prices import PriceFetcher
    from backend.sources.rate_limiter import DomainLimiter, get_with_retry
    from backend.sources.sec_edgar import SecEdgarClient
    from backend.sources.sentiment_fetcher import SentimentFetcher

    ua = settings.sec_edgar_user_agent or DEFAULT_USER_AGENT
    limiter = DomainLimiter()
    async with make_client(ua) as client:
        try:
            sec = SecEdgarClient(client, limiter, ua)
        except SourceConfigError:
            sec = None

        async def download(url: str) -> bytes:
            resp = await get_with_retry(client, url, limiter)
            if len(resp.content) > MAX_DOWNLOAD_BYTES:
                raise SourceError(f"{url} is larger than {MAX_DOWNLOAD_BYTES // 2**20} MB")
            return resp.content

        with tempfile.TemporaryDirectory(prefix=f"atlas_{run_id}_") as tmp:
            yield RunContext(
                settings=settings, llm=GeminiClient(settings, db), emitter=EventEmitter(run_id), sec=sec,
                india=IndiaFilingsClient(client, limiter), news=NewsFetcher(client, limiter),
                sentiment=SentimentFetcher(client, limiter, reddit_client_id=settings.reddit_client_id,
                                           reddit_client_secret=settings.reddit_client_secret, user_agent=ua),
                download=download, close_price=PriceFetcher(client, limiter, settings.finnhub_api_key),
                work_dir=work_dir or Path(tmp))


async def run_live_pipeline(run_id: str, company_query: str, on_event: Callable[[Any], Awaitable[None]],
                            settings: Any, db: Any, today: Optional[date] = None) -> dict[str, Any]:
    from backend.pipeline.graph import execute

    async with live_context(run_id, settings, db) as ctx:
        return await execute(ctx, company_query, on_event, today=today)
