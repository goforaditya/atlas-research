from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from .common import ClaimClassification, EvidenceCitation, Polarity, SourceTier


class NewsEvent(BaseModel):
    event_id: str
    event_date: str = Field(..., description="YYYY-MM or YYYY-MM-DD, e.g., 'Mar 2026'")
    category: Literal["Acquisition", "Partnership", "Management", "Regulatory", "Fundraise", "Controversy", "Product"]
    headline: str
    summary: str
    polarity: Polarity
    source_names: list[str] = Field(..., description="e.g., ['BSE filing', 'Business Ledger']")
    highest_tier: SourceTier
    corroborated_by_filing: bool
    article_count_merged: int
    evidence: EvidenceCitation


class SentimentItem(BaseModel):
    post_id: str
    platform: Literal["Reddit", "Hacker News", "Play Store", "Broker note", "Column"]
    audience: Literal["Customer", "Employee", "Investor"]
    quote_text: str
    polarity: Polarity
    verdict: Literal["signal", "noise"]
    verdict_reasons: list[str] = Field(..., description="e.g., ['Account 19 days old, 12 followers, 6 near-identical posts']")
    corroborating_ids: list[str] = Field(
        default_factory=list,
        description="IDs of MetricLedgerRow, NewsEvent, or LineItem that corroborate this post",
    )
    evidence: EvidenceCitation


class AudienceSentimentSummary(BaseModel):
    audience: Literal["Customers", "Employees", "Investors"]
    dominant_tone: Literal["positive", "neutral", "negative", "mixed"]
    trend_description: str


class SentimentAggregate(BaseModel):
    total_posts_fetched: int
    bots_and_duplicates_removed: int
    posts_classified: int
    positive_pct: int
    neutral_pct: int
    negative_pct: int
    audience_breakdown: list[AudienceSentimentSummary]
    items: list[SentimentItem]


class PeerCompRow(BaseModel):
    company_name: str
    ticker: str
    is_target: bool = False
    is_median_row: bool = False
    rev_growth_pct: Decimal | None = None
    ebitda_margin_pct: Decimal | None = None
    pe_multiple: Decimal | None = Field(None, description="Close price / trailing diluted EPS; None for loss-makers")
    price_as_of: str | None = Field(None, description="Date of the closing price used for P/E")
    roe_pct: Decimal | None = None
    formatted: dict[str, str] = Field(default_factory=dict, description="formatted_value per column")
    evidence_ids: list[str] = Field(default_factory=list)


class ContradictionRecord(BaseModel):
    contradiction_id: str
    topic: str = Field(..., description="e.g., 'FY26 Consolidated Revenue'")
    source_a_claim: str
    source_a_tier: SourceTier
    source_a_weight: float
    source_a_evidence: EvidenceCitation
    source_b_claim: str
    source_b_tier: SourceTier
    source_b_weight: float
    source_b_evidence: EvidenceCitation
    winning_source: Literal["A", "B", "BOTH_FRAMED"]
    resolution_rationale: str


class ProvenanceNode(BaseModel):
    node_id: str = Field(..., description="ID of MetricLedgerRow, RedFlagResult, NewsEvent, SentimentItem, or Insight")
    node_type: Literal["line_item", "metric", "red_flag", "news", "sentiment", "contradiction", "insight"]
    label: str
    classification: ClaimClassification
    source_tier: SourceTier


class ProvenanceEdge(BaseModel):
    source_node_id: str
    target_node_id: str
    relationship: Literal["proves", "corroborates", "contradicts", "contextualizes"]


class InsightCard(BaseModel):
    insight_id: str
    title: str
    body: str = Field(..., description="Prose containing inline citation tokens like [[met_dso_fy26]]")
    classification: ClaimClassification
    polarity: Polarity
    supporting_metric_ids: list[str]
    supporting_item_ids: list[str] = Field(default_factory=list)
    supporting_red_flag_ids: list[str] = Field(default_factory=list)
    supporting_news_ids: list[str]
    supporting_sentiment_ids: list[str]
    supporting_contradiction_ids: list[str] = Field(default_factory=list)


class ManagementPerson(BaseModel):
    name: str
    role: str = Field(..., description="e.g. 'Managing Director & CEO'")
    since: str | None = None
    evidence_id: str
