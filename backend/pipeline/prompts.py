"""Exact system and user prompt templates for every LLM call (Section 4), plus the output schemas.

The output models are what the LLM may return. None of them carries a computed financial figure: numbers in
prose are copied formatted_value strings followed by a citation token, and the verification gate checks them.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.schemas.financials import RawFieldKey

# ---------------------------------------------------------------- Stage 1

ENTITY_RESOLUTION_SYSTEM = """You are an institutional entity resolution agent. Given a user query and raw registry candidate records from NSE/BSE, SEC EDGAR, and corporate registries, select the exact target company.
Rules:
1. Prefer listed operating parent entities or active DRHP/S-1 filers over dormant subsidiaries.
2. Never invent a ticker or CIK. Use only values present in CANDIDATE_RECORDS.
3. Set currency to "INR_CR" for Indian entities and "USD_M" for US/Global entities.
4. List all non-selected candidates in `rejected_candidates` with the exact reason for rejection."""

ENTITY_RESOLUTION_USER = """QUERY: {company_query}
CANDIDATE_RECORDS: {candidates_json}"""


class EntityResolutionOut(BaseModel):
    legal_name: str
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    jurisdiction: Literal["IN", "US", "GLOBAL_PRIVATE"]
    is_listed: bool
    sector: str
    headquarters: str
    fy_end_month: str
    currency: Literal["INR_CR", "USD_M"]
    rejected_candidates: list[str] = Field(default_factory=list)
    decision_rationale: str


# ---------------------------------------------------------------- Stage 2

RESEARCH_PLANNING_SYSTEM = """You are a Principal Equity Research Analyst planning an investigation into {legal_name} ({sector}, {jurisdiction}).
Generate exactly 12 specific research sub-questions across 4 buckets (3 per bucket):
1. Financial Quality & Cash Conversion
2. Governance, Regulatory & Material Events
3. Customer/Merchant & Employee Sentiment
4. Competitive Moat & Peer Valuation
Do not include any numbers or assumptions."""

RESEARCH_PLANNING_USER = """ENTITY: {entity_json}"""


class ResearchPlanOut(BaseModel):
    sub_questions: list[str]
    routing_rationale: str


# ---------------------------------------------------------------- Stage 3 (fallback only: copy strings)

MANAGEMENT_COPY_SYSTEM = """You copy the names and roles of key managerial personnel / executive officers from the text below.
Copy each name and role exactly as printed. Do not add anyone who is not in the text. Do not add dates unless printed."""

MANAGEMENT_COPY_USER = """TEXT: {text}"""


class ManagementCopyRow(BaseModel):
    name: str
    role: str
    since: Optional[str] = None


class ManagementCopyOut(BaseModel):
    people: list[ManagementCopyRow]


# ---------------------------------------------------------------- Stage 4

STATEMENT_ROW_MAPPING_SYSTEM = """You are a financial table coordinate mapper. You are given a raw 2D array of strings extracted from an audited financial statement table on page {page_num} of {doc_title}.
Your ONLY task is to map the row index and column index for each requested `StandardFieldKey` and `fiscal_period`.
CRITICAL RULE: Do NOT output numeric values, do NOT sum rows, and do NOT convert units. Output ONLY the integer `row_idx`, integer `col_idx`, and the exact `reported_label` string from column 0. If a field is not present in the table, set `row_idx` and `col_idx` to null."""

STATEMENT_ROW_MAPPING_USER = """REQUESTED_FIELDS: {fields_json}
REQUESTED_PERIODS: {periods_json}
UNIT_HEADER_DETECTED: {unit_header}
RAW_TABLE_GRID: {grid_json}"""


class RowMapping(BaseModel):
    field_key: RawFieldKey
    fiscal_period: str
    reported_label: str
    row_idx: Optional[int] = None
    col_idx: Optional[int] = None
    segment_name: Optional[str] = None


class RowMappingOut(BaseModel):
    mappings: list[RowMapping]


# ---------------------------------------------------------------- Stage 7

NEWS_CLUSTERING_SYSTEM = """You are a financial news classifier. Given deduplicated article clusters and official T1/T2 regulatory filings for {legal_name}, output the top 6 to 10 material corporate events from the last 12 months.
Rules:
1. Never invent dates, penalties, or deal values; copy exact strings from the source snippets.
2. Set `corroborated_by_filing` to true ONLY if a T1 or T2 document in the input confirms the event.
3. Tag each event with a category (Acquisition, Partnership, Management, Regulatory, Fundraise, Controversy, Product) and polarity (positive, neutral, negative)."""

NEWS_CLUSTERING_USER = """ARTICLE_CLUSTERS: {clusters_json}
T1_T2_TITLES_AND_SNIPPETS: {filings_summary_json}"""


class NewsEventOut(BaseModel):
    event_id: str
    event_date: str
    category: Literal["Acquisition", "Partnership", "Management", "Regulatory", "Fundraise", "Controversy", "Product"]
    headline: str
    summary: str
    polarity: Literal["positive", "neutral", "negative"]
    source_names: list[str]
    highest_tier: Literal["T1", "T2", "T3"]
    corroborated_by_filing: bool
    article_count_merged: int
    primary_article_id: str


class NewsClusteringOut(BaseModel):
    events: list[NewsEventOut]


# ---------------------------------------------------------------- Stage 8

SENTIMENT_SIGNAL_NOISE_SYSTEM = """You are a forensic sentiment analyst evaluating public posts about {legal_name}.
You are provided with:
1. Pre-filtered public posts/reviews.
2. Verified financial metrics (`METRICS_LEDGER`) and verified news events (`NEWS_EVENTS`).
Rules for Signal vs Noise:
- Mark a post as "signal" ONLY if it reflects a recurring operational theme (>=3 independent posts) OR is corroborated by a specific metric (e.g. DSO spike) or verified news event (e.g. outage, CFO exit).
- Mark hype, price targets without cash flow grounding, rumors (e.g. takeover bids without filings), and isolated rants as "noise".
- Link `corroborating_ids` to the exact `metric_id` or `event_id` that supports each "signal" item."""

SENTIMENT_SIGNAL_NOISE_USER = """CLEAN_POSTS: {posts_json}
METRICS_LEDGER_SUMMARY: {metrics_summary_json}
NEWS_EVENTS_SUMMARY: {news_summary_json}"""


class AudienceOut(BaseModel):
    audience: Literal["Customers", "Employees", "Investors"]
    dominant_tone: Literal["positive", "neutral", "negative", "mixed"]
    trend_description: str


class SentimentItemOut(BaseModel):
    post_id: str
    platform: Literal["Reddit", "Hacker News", "Play Store", "Broker note", "Column"]
    audience: Literal["Customer", "Employee", "Investor"]
    quote_text: str
    polarity: Literal["positive", "neutral", "negative"]
    verdict: Literal["signal", "noise"]
    verdict_reasons: list[str]
    corroborating_ids: list[str]


class SentimentOut(BaseModel):
    audience_breakdown: list[AudienceOut]
    items: list[SentimentItemOut]


# ---------------------------------------------------------------- Stage 9

PEER_SELECTION_SYSTEM = """You select 4 direct listed peers for {legal_name} ({sector}, {jurisdiction}).
Choose only from CANDIDATE_PEERS, which are filers reachable through SEC XBRL or BSE. Never invent a ticker.
Prefer companies with the same business model and region. Give one short reason per peer."""

PEER_SELECTION_USER = """CANDIDATE_PEERS: {candidates_json}"""


class PeerChoice(BaseModel):
    ticker: str
    company_name: str
    reason: str


class PeerSelectionOut(BaseModel):
    peers: list[PeerChoice]


MARKET_POSITIONING_SYSTEM = """You are an equity research analyst writing the competitive positioning summary for {legal_name}.
CRITICAL RULE: You may ONLY cite numbers that appear verbatim in `PEER_TABLE_WITH_MEDIANS`. Never compute a difference or average yourself. Reference metrics using their `formatted_value` followed by the row's citation token, e.g. `17.6% [[peer_paytm_rev_growth_pct]]`."""

MARKET_POSITIONING_USER = """PEER_TABLE_WITH_MEDIANS: {peer_table_json}"""


class PositioningOut(BaseModel):
    market_advantages: list[str]
    market_disadvantages: list[str]
    market_positioning_prose: str


# ---------------------------------------------------------------- Stage 10

CONTRADICTION_EXTRACTION_SYSTEM = """Identify all material contradictions between official filings/metrics (T1) and earnings call claims (T2), press reports (T3), or broker notes (T4).
For each contradiction, extract the exact conflicting claims and their `evidence_id`s. Python code will assign the winner based on tier weights."""

CONTRADICTION_EXTRACTION_USER = """T1_METRICS_AND_ITEMS: {t1_summary_json}
T2_T3_T4_CLAIMS: {other_claims_json}"""


class CandidateContradiction(BaseModel):
    contradiction_id: str
    topic: str
    source_a_claim: str
    source_a_evidence_id: str
    source_b_claim: str
    source_b_evidence_id: str
    conflict_type: Literal["NUMERIC_DISCREPANCY", "NARRATIVE_VS_METRIC"]
    explanation_note: str


class ContradictionOut(BaseModel):
    candidate_contradictions: list[CandidateContradiction]


# ---------------------------------------------------------------- Stage 11

SYNTHESIS_SYSTEM = """You are the Lead Investment Committee Author synthesizing the research report for {legal_name}.
NON-NEGOTIABLE NUMERICAL RULE:
1. You are FORBIDDEN from performing any arithmetic or writing any raw number that is not followed by a citation token: `[[metric_id]]`, `[[item_id]]`, `[[peer_*]]`, or `[[evidence_id]]` (the last only for a number quoted verbatim from that evidence's snippet, e.g. an RBI penalty amount).
2. When referencing a figure in prose, write the exact `formatted_value` from the ledger followed immediately by its token, e.g., `84.3 d [[met_dso_fy26]]` or `₹3,810 cr [[li_fy26_revenue]]`.
3. Business overview, revenue mix and management text may only use facts present in BUSINESS_EVIDENCE, SEGMENT_ITEMS and MANAGEMENT_ROSTER. If a fact is not there, omit it.
3. Classify each insight strictly:
   - "fact": Directly proven by T1/T2 filings and deterministic metrics.
   - "inference": Logical synthesis combining >=2 corroborated facts/signals.
   - "hypothesis": Plausible timing or causal link where official filings do not state the reason (e.g., an unexplained executive resignation preceding a capital raise)."""

SYNTHESIS_USER = """METRICS_LEDGER: {metrics_ledger_json}
LINE_ITEMS: {line_items_json}
SEGMENT_ITEMS: {segment_items_json}
QUARTERLY_METRICS: {quarterly_metrics_json}
FIRED_RED_FLAGS: {red_flags_json}
NEWS_EVENTS: {news_events_json}
SIGNAL_SENTIMENT: {signal_sentiment_json}
PEER_COMPS: {peer_comps_json}
CONTRADICTIONS: {contradictions_json}
MANAGEMENT_ROSTER: {management_roster_json}
BUSINESS_EVIDENCE: {business_snippets_json}"""


class InsightOut(BaseModel):
    insight_id: str
    title: str
    body: str
    classification: Literal["fact", "inference", "hypothesis"]
    polarity: Literal["positive", "neutral", "negative"]
    supporting_metric_ids: list[str] = Field(default_factory=list)
    supporting_item_ids: list[str] = Field(default_factory=list)
    supporting_red_flag_ids: list[str] = Field(default_factory=list)
    supporting_news_ids: list[str] = Field(default_factory=list)
    supporting_sentiment_ids: list[str] = Field(default_factory=list)
    supporting_contradiction_ids: list[str] = Field(default_factory=list)


class ExecutiveSummaryOut(BaseModel):
    verdict_headline: str
    verdict_prose: str
    business_overview: str
    revenue_mix_summary: str
    management_summary: str
    governance_watch_note: Optional[str] = None
    opportunities: list[str]
    risks: list[str]
    quarterly_direction_note: str
    analyst_follow_up_notes: list[str]


class SynthesisOut(BaseModel):
    insights: list[InsightOut]
    executive_summary: ExecutiveSummaryOut
