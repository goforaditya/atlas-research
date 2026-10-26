"""Per-domain token-bucket rate limiting, a circuit breaker and retrying GETs (Section 7.2)."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend.sources import SourceBlocked, SourceError, host_of

# Requests per second per host. SEC allows 10/s; we stay under it. Social sources follow Section 7.3 (1 req / 2 s).
DOMAIN_RATES: dict[str, float] = {
    "sec.gov": 8.0,
    "bseindia.com": 1.0,
    "nseindia.com": 1.0,
    "news.google.com": 1.0,
    "gdeltproject.org": 1.0,
    "reddit.com": 0.5,
    "hn.algolia.com": 2.0,
    "finnhub.io": 1.0,
    "play.google.com": 0.5,
}
DEFAULT_RATE = 1.0
RETRY_STATUSES = {429, 503}
BACKOFF_SEC = (1.0, 2.0, 4.0)


@dataclass
class TokenBucket:
    rate: float  # tokens per second
    capacity: float = 1.0
    tokens: float = 1.0
    updated: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await self.sleep((1 - self.tokens) / self.rate)


@dataclass
class CircuitBreaker:
    """Opens after `threshold` consecutive failures and stays open for `cooldown` seconds."""

    threshold: int = 5
    cooldown: float = 60.0
    failures: int = 0
    opened_at: Optional[float] = None
    clock: Callable[[], float] = time.monotonic

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self.clock() - self.opened_at >= self.cooldown:
            self.opened_at, self.failures = None, 0  # half-open: allow the next try
            return False
        return True

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


def _domain_key(host: str) -> str:
    for d in DOMAIN_RATES:
        if host == d or host.endswith("." + d):
            return d
    return host


@dataclass
class DomainLimiter:
    buckets: dict[str, TokenBucket] = field(default_factory=dict)
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    rate_override: Optional[float] = None  # tests use a high rate instead of waiting

    def bucket(self, url: str) -> TokenBucket:
        key = _domain_key(host_of(url))
        if key not in self.buckets:
            rate = self.rate_override or DOMAIN_RATES.get(key, DEFAULT_RATE)
            self.buckets[key] = TokenBucket(rate=rate, capacity=max(1.0, rate), sleep=self.sleep)
        return self.buckets[key]

    def breaker(self, url: str) -> CircuitBreaker:
        return self.breakers.setdefault(_domain_key(host_of(url)), CircuitBreaker())


async def get_with_retry(client: httpx.AsyncClient, url: str, limiter: DomainLimiter, *,
                         params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None,
                         blocked_statuses: tuple[int, ...] = ()) -> httpx.Response:
    """GET with per-domain rate limiting and 1s/2s/4s backoff on 429/503. Statuses in `blocked_statuses` raise
    SourceBlocked immediately (a known refusal, e.g. NSE 403) so the caller can fall back without retrying."""
    breaker = limiter.breaker(url)
    if breaker.is_open:
        raise SourceError(f"Circuit open for {host_of(url)}")
    last: Optional[Exception] = None
    for attempt in range(len(BACKOFF_SEC) + 1):
        await limiter.bucket(url).acquire()
        try:
            resp = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            last = exc
        else:
            if resp.status_code in blocked_statuses:
                breaker.record(True)  # the host answered; it just refuses this client
                raise SourceBlocked(f"{host_of(url)} returned {resp.status_code}")
            if resp.status_code not in RETRY_STATUSES:
                breaker.record(resp.is_success)
                if not resp.is_success:
                    raise SourceError(f"{host_of(url)} returned {resp.status_code}")
                return resp
            last = SourceError(f"{host_of(url)} returned {resp.status_code}")
        if attempt < len(BACKOFF_SEC):
            await limiter.sleep(BACKOFF_SEC[attempt])
    breaker.record(False)
    raise SourceError(str(last))
