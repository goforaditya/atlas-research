from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RunMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"


class CurrencyUnit(str, Enum):
    INR_CR = "INR_CR"       # ₹ Crore (10^7 INR)
    USD_M = "USD_M"         # $ Million (10^6 USD)


class SourceTier(str, Enum):
    T1 = "T1"  # Weight 1.00: Regulator, audited annual reports, 10-K, DRHP
    T2 = "T2"  # Weight 0.85: Exchange disclosures, investor decks, earnings calls
    T3 = "T3"  # Weight 0.65: Tier-1 business press (ET, Mint, Reuters, Bloomberg)
    T4 = "T4"  # Weight 0.30: Social (Reddit, X, HN), app reviews, broker notes


TIER_WEIGHTS: dict[SourceTier, float] = {
    SourceTier.T1: 1.00,
    SourceTier.T2: 0.85,
    SourceTier.T3: 0.65,
    SourceTier.T4: 0.30,
}


class StageId(str, Enum):
    S01_ENTITY = "s01_entity_resolution"
    S02_PLAN = "s02_research_planning"
    S03_RETRIEVE = "s03_filing_retrieval"
    S04_EXTRACT = "s04_statement_extraction"
    S05_METRICS = "s05_metrics_engine"
    S06_RED_FLAGS = "s06_red_flag_engine"
    S07_NEWS = "s07_news_intelligence"
    S08_SENTIMENT = "s08_sentiment_filter"
    S09_MARKET = "s09_market_peers"
    S10_CONTRADICTION = "s10_contradiction_resolver"
    S11_SYNTHESIS = "s11_synthesis"
    S12_VERIFY = "s12_verification_gate"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"


class ActorBadge(str, Enum):
    CODE = "CODE"
    LLM = "LLM"


class ClaimClassification(str, Enum):
    FACT = "fact"               # Directly backed by T1/T2 or deterministic ledger
    INFERENCE = "inference"     # Derived from >=2 corroborated sources
    HYPOTHESIS = "hypothesis"   # Plausible link, unconfirmed by T1/T2


class Polarity(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class EvidenceCitation(BaseModel):
    evidence_id: str = Field(..., description="Unique ID, e.g., 'ev_ar26_p112_rev'")
    document_id: str = Field(..., description="ID of parent SourceDocument")
    document_title: str = Field(..., description="Human-readable title, e.g., 'AR FY26'")
    source_url: str = Field(..., description="Public URL or local fixture path")
    source_tier: SourceTier
    trust_weight: float = Field(..., ge=0.0, le=1.0)
    page_number: int | None = None
    table_id: str | None = None
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="(x0, top, x1, bottom) in PDF points, TOP-LEFT origin (pdfplumber convention)"
    )
    page_size: tuple[float, float] | None = Field(None, description="(width, height) of the page in points")
    excerpt_image_path: str | None = Field(None, description="Rendered page PNG used by the Evidence Drawer")
    verbatim_snippet: str = Field(..., description="Exact text/row from source document")
    extracted_value_raw: str = Field(..., description="Exact string extracted before normalization")
    source_unit: str | None = Field(None, description="Unit header as printed, e.g. '₹ in million', 'in thousands'")
    retrieved_at: str | None = Field(None, description="ISO timestamp the document was fetched")
