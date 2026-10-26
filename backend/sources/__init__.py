"""Source registry: tier weights, the T1/T2 host whitelist and the shared HTTP client (Section 7)."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from backend.schemas.common import SourceTier

TIER_WEIGHTS: dict[SourceTier, float] = {
    SourceTier.T1: 1.00,
    SourceTier.T2: 0.85,
    SourceTier.T3: 0.65,
    SourceTier.T4: 0.30,
}

# verify_document_authenticity accepts statement documents only from these hosts (plus the company's own IR
# domain taken from the filing index). It checks the host and records a SHA-256; it is not a signature check.
T1_T2_HOSTS = ("sec.gov", "bseindia.com", "nseindia.com", "sebi.gov.in", "rbi.org.in", "mca.gov.in")

# Third-party aggregators are never a source for statement figures (Section 7.2).
AGGREGATOR_HOSTS = ("moneycontrol.com", "screener.in", "tickertape.in", "finance.yahoo.com", "macrotrends.net",
                    "stockanalysis.com", "marketscreener.com", "trendlyne.com")

DEFAULT_USER_AGENT = "AtlasResearchBot/1.0 (compliance@atlasresearch.demo)"


class SourceError(Exception):
    """A source could not be reached or returned something unusable."""


class SourceBlocked(SourceError):
    """The source refused a non-browser client (e.g. NSE's 403). Logged as `fallback`, not `error`."""


class SourceConfigError(SourceError):
    """A required credential or header is missing (e.g. SEC_EDGAR_USER_AGENT)."""


@dataclass
class FetchTrace:
    """One tool call, shaped for ToolCallTrace in the explain panel."""

    tool_name: str
    arguments: dict[str, Any]
    status: str = "ok"  # ok | error | fallback
    duration_ms: int = 0
    raw_output_sample: str = ""
    started: float = field(default_factory=time.perf_counter, repr=False)

    def finish(self, status: str = "ok", sample: Any = "") -> FetchTrace:
        self.status = status
        self.duration_ms = int((time.perf_counter() - self.started) * 1000)
        text = sample if isinstance(sample, str) else repr(sample)
        self.raw_output_sample = text[:600]
        return self


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def host_matches(url: str, hosts: tuple[str, ...]) -> bool:
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in hosts)


@dataclass(frozen=True)
class DocumentCheck:
    accepted: bool
    tier: Optional[SourceTier]
    sha256: Optional[str]
    reason: str


def verify_document_authenticity(url: str, content: Optional[bytes] = None, ir_domains: tuple[str, ...] = ()) -> DocumentCheck:
    """Accept a statement document only from a regulator/exchange host or the company's own IR domain."""
    digest = hashlib.sha256(content).hexdigest() if content is not None else None
    if host_matches(url, AGGREGATOR_HOSTS):
        return DocumentCheck(False, None, digest, f"Rejected aggregator host {host_of(url)}")
    if host_matches(url, ("sec.gov", "sebi.gov.in", "rbi.org.in", "mca.gov.in")):
        return DocumentCheck(True, SourceTier.T1, digest, f"Regulator host {host_of(url)}")
    if host_matches(url, ("bseindia.com", "nseindia.com")):
        return DocumentCheck(True, SourceTier.T1, digest, f"Exchange filing host {host_of(url)}")
    if ir_domains and host_matches(url, ir_domains):
        return DocumentCheck(True, SourceTier.T2, digest, f"Company IR domain {host_of(url)}")
    return DocumentCheck(False, None, digest, f"Host {host_of(url)} is not on the T1/T2 whitelist")


def make_client(user_agent: str = DEFAULT_USER_AGENT, timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                             timeout=timeout, follow_redirects=True)
