// frontend/src/types/atlas.ts
export type RunMode = "live" | "replay";
/** Exact decimal serialised by Pydantic v2 as a JSON string, e.g. "3810.00". Never do arithmetic on it;
 *  display `formatted_value`. Number(x) is allowed only for chart geometry. */
export type DecimalString = string;
export type CurrencyUnit = "INR_CR" | "USD_M";
export type SourceTier = "T1" | "T2" | "T3" | "T4";
export type StageId =
  | "s01_entity_resolution"
  | "s02_research_planning"
  | "s03_filing_retrieval"
  | "s04_statement_extraction"
  | "s05_metrics_engine"
  | "s06_red_flag_engine"
  | "s07_news_intelligence"
  | "s08_sentiment_filter"
  | "s09_market_peers"
  | "s10_contradiction_resolver"
  | "s11_synthesis"
  | "s12_verification_gate";

export type StageStatus = "pending" | "running" | "completed" | "warning" | "failed";
export type ActorBadge = "CODE" | "LLM";
export type ClaimClassification = "fact" | "inference" | "hypothesis";
export type Polarity = "positive" | "neutral" | "negative";

export type RawFieldKey =
  | "revenue" | "other_income" | "finance_cost" | "d_and_a" | "pbt" | "tax_expense"
  | "pat_before_nci" | "pat" | "cfo" | "purchase_of_ppe" | "purchase_of_intangibles"
  | "total_assets" | "total_liabilities" | "total_equity" | "non_controlling_interest"
  | "borrowings_current" | "borrowings_noncurrent" | "cash_and_equivalents"
  | "trade_receivables" | "current_assets" | "current_liabilities" | "segment_revenue"
  | "operating_income";
export type DerivedFieldKey = "ebit" | "ebitda" | "interest_expense" | "gross_debt" | "capex";
export type StandardFieldKey = RawFieldKey | DerivedFieldKey;

export interface EvidenceCitation {
  evidence_id: string;
  document_id: string;
  document_title: string;
  source_url: string;
  source_tier: SourceTier;
  trust_weight: number;
  page_number?: number | null;
  table_id?: string | null;
  bbox?: [number, number, number, number] | null;
  page_size?: [number, number] | null;
  excerpt_image_path?: string | null;
  verbatim_snippet: string;
  extracted_value_raw: string;
  source_unit?: string | null;
  retrieved_at?: string | null;
}

export interface LineItem {
  item_id: string;
  fiscal_period: string;
  field_key: StandardFieldKey;
  reported_label: string;
  value: DecimalString | null;
  formatted_value: string;
  unit: CurrencyUnit;
  is_restated: boolean;
  is_derived: boolean;
  derived_from_item_ids: string[];
  segment_name?: string | null;
  evidence: EvidenceCitation;
}

export interface ReconciliationCheck {
  check_id: string;
  fiscal_period: string;
  check_name: string;
  formula_desc: string;
  lhs_value: DecimalString | null;
  rhs_value: DecimalString | null;
  discrepancy: DecimalString | null;
  tolerance_pct: DecimalString;
  status: "passed" | "failed" | "skipped";
  passed: boolean;
  failure_reason?: string | null;
  skip_reason?: string | null;
}

export interface MetricInputRef {
  symbol: string;
  line_item_id: string;
  fiscal_period: string;
  value: DecimalString | null;
  evidence_id: string;
}

export interface MetricLedgerRow {
  metric_id: string;
  metric_key: string;
  metric_label: string;
  fiscal_period: string;
  formula_template: string;
  formula_substituted: string;
  inputs: MetricInputRef[];
  value: DecimalString | null;
  formatted_value: string;
  unit: "pct" | "multiple" | "days" | "bps" | "currency";
  status: "computed" | "nm_negative_base" | "nm_zero_div" | "missing_input";
  status_note?: string | null;
  primary_source_ref: string;
}

export interface RedFlagResult {
  rule_id: string;
  rule_name: string;
  severity: "high" | "medium" | "low";
  threshold_desc: string;
  observed_value_formatted: string;
  status: "fired" | "clear" | "insufficient_data";
  fired: boolean;
  explanation: string;
  missing_inputs: string[];
  supporting_metric_ids: string[];
}

export interface NewsEvent {
  event_id: string;
  event_date: string;
  category: "Acquisition" | "Partnership" | "Management" | "Regulatory" | "Fundraise" | "Controversy" | "Product";
  headline: string;
  summary: string;
  polarity: Polarity;
  source_names: string[];
  highest_tier: SourceTier;
  corroborated_by_filing: boolean;
  article_count_merged: number;
  evidence: EvidenceCitation;
}

export interface SentimentItem {
  post_id: string;
  platform: "Reddit" | "Hacker News" | "Play Store" | "Broker note" | "Column";
  audience: "Customer" | "Employee" | "Investor";
  quote_text: string;
  polarity: Polarity;
  verdict: "signal" | "noise";
  verdict_reasons: string[];
  corroborating_ids: string[];
  evidence: EvidenceCitation;
}

export interface AudienceSentimentSummary {
  audience: "Customers" | "Employees" | "Investors";
  dominant_tone: "positive" | "neutral" | "negative" | "mixed";
  trend_description: string;
}

export interface SentimentAggregate {
  total_posts_fetched: number;
  bots_and_duplicates_removed: number;
  posts_classified: number;
  positive_pct: number;
  neutral_pct: number;
  negative_pct: number;
  audience_breakdown: AudienceSentimentSummary[];
  items: SentimentItem[];
}

export interface PeerCompRow {
  company_name: string;
  ticker: string;
  is_target: boolean;
  is_median_row: boolean;
  rev_growth_pct: DecimalString | null;
  ebitda_margin_pct: DecimalString | null;
  pe_multiple: DecimalString | null;
  price_as_of?: string | null;
  roe_pct: DecimalString | null;
  formatted: Record<string, string>;
  evidence_ids: string[];
}

export interface ContradictionRecord {
  contradiction_id: string;
  topic: string;
  source_a_claim: string;
  source_a_tier: SourceTier;
  source_a_weight: number;
  source_a_evidence: EvidenceCitation;
  source_b_claim: string;
  source_b_tier: SourceTier;
  source_b_weight: number;
  source_b_evidence: EvidenceCitation;
  winning_source: "A" | "B" | "BOTH_FRAMED";
  resolution_rationale: string;
}

export interface ProvenanceNode {
  node_id: string;
  node_type: "line_item" | "metric" | "red_flag" | "news" | "sentiment" | "contradiction" | "insight";
  label: string;
  classification: ClaimClassification;
  source_tier: SourceTier;
}

export interface ProvenanceEdge {
  source_node_id: string;
  target_node_id: string;
  relationship: "proves" | "corroborates" | "contradicts" | "contextualizes";
}

export interface InsightCard {
  insight_id: string;
  title: string;
  body: string;
  classification: ClaimClassification;
  polarity: Polarity;
  supporting_metric_ids: string[];
  supporting_item_ids: string[];
  supporting_red_flag_ids: string[];
  supporting_news_ids: string[];
  supporting_sentiment_ids: string[];
  supporting_contradiction_ids: string[];
}

export interface ManagementPerson {
  name: string;
  role: string;
  since?: string | null;
  evidence_id: string;
}

export interface ToolCallTrace {
  call_id: string;
  tool_name: string;
  actor: ActorBadge;
  arguments: Record<string, unknown>;
  duration_ms: number;
  status: "ok" | "error" | "fallback";
  raw_output_sample: string;
}

export interface CodeVsLlmAction {
  actor: ActorBadge;
  action_summary: string;
}

export interface ExplainStagePanelData {
  stage_id: StageId;
  stage_title: string;
  purpose_one_sentence: string;
  inputs_summary: string[];
  tool_calls: ToolCallTrace[];
  actions: CodeVsLlmAction[];
  decision_made: string;
  decision_rationale: string;
  failure_modes_and_guards: string[];
}

export interface StageEvent {
  event_seq: number;
  run_id: string;
  timestamp_ms: number;
  elapsed_ms: number;
  stage_id: StageId;
  stage_status: StageStatus;
  progress_pct: number;
  log_line: string;
  explain_panel?: ExplainStagePanelData | null;
  state_patch: Partial<ResearchRunState>;
}

export interface ResolvedEntity {
  query: string;
  legal_name: string;
  ticker?: string | null;
  exchange?: string | null;
  jurisdiction: "IN" | "US" | "GLOBAL_PRIVATE";
  is_listed: boolean;
  cik?: string | null;
  bse_scrip_code?: string | null;
  listing_date?: string | null;
  sector: string;
  headquarters: string;
  fy_end_month: string;
  currency: CurrencyUnit;
  rejected_candidates: string[];
}

export interface ExecutiveSummary {
  verdict_headline: string;
  verdict_prose: string;
  fact_count: number;
  inference_count: number;
  hypothesis_count: number;
  business_overview: string;
  revenue_mix_summary: string;
  management_summary: string;
  governance_watch_note?: string | null;
  opportunities: string[];
  risks: string[];
  quarterly_direction_note: string;
  analyst_follow_up_notes: string[];
}

export interface VerificationGateReport {
  total_numeric_tokens_scanned: number;
  verified_against_ledger: number;
  unverified_tokens_blocked: number;
  claims_downgraded_to_hypothesis: string[];
  passed: boolean;
}

export interface ResearchRunState {
  run_id: string;
  mode: RunMode;
  company_query: string;
  status: "running" | "completed" | "failed";
  started_at_ms: number;
  completed_at_ms?: number | null;
  sources_read_count: number;
  entity?: ResolvedEntity | null;
  sub_questions: string[];
  fiscal_years: string[];
  line_items: LineItem[];
  quarterly_items: LineItem[];
  quarterly_metrics: MetricLedgerRow[];
  management_roster: ManagementPerson[];
  reconciliation_checks: ReconciliationCheck[];
  metrics_ledger: MetricLedgerRow[];
  red_flags: RedFlagResult[];
  news_events: NewsEvent[];
  sentiment?: SentimentAggregate | null;
  peer_comps: PeerCompRow[];
  market_advantages: string[];
  market_disadvantages: string[];
  market_positioning_prose?: string | null;
  insights: InsightCard[];
  provenance_nodes: ProvenanceNode[];
  provenance_edges: ProvenanceEdge[];
  contradictions: ContradictionRecord[];
  summary?: ExecutiveSummary | null;
  verification?: VerificationGateReport | null;
  evidence_registry: Record<string, EvidenceCitation>;
  stated_gaps: string[];
  events: StageEvent[];
}

// ---- API envelopes (Section 8) ----
export interface ReplayInfo {
  replay_id: string;
  company_name: string;
  ticker: string;
  sector: string;
  jurisdiction: "IN" | "US" | "GLOBAL_PRIVATE";
  cached_at: string;
  total_events: number;
  headline_verdict: string;
  company_query: string;
  synthetic: boolean;
  unconfirmed?: boolean;
}

export interface CreateRunRequest {
  company_query: string;
  mode: RunMode;
  replay_id?: string | null;
  replay_speed?: number;
}

export interface CreateRunResponse {
  run_id: string;
  mode: RunMode;
  company_query: string;
  stream_url: string;
  state_url: string;
}

export interface ApiErrorBody {
  error_code: string;
  message: string;
  details?: Record<string, unknown> | null;
}

export interface RunStartedPayload {
  run_id: string;
  mode: RunMode;
  company_query: string;
  started_at_ms: number;
}

export interface RunCompletedPayload {
  run_id: string;
  elapsed_ms: number;
  sources_read_count: number;
  metrics_count: number;
}

export interface RunErrorPayload {
  run_id: string;
  stage_id: StageId;
  error_code: string;
  message: string;
}

export interface EvidenceResponse {
  evidence: EvidenceCitation;
  excerpt_image_url: string | null;
  source_pdf_url: string | null;
  linked_line_items: LineItem[];
  linked_reconciliation_checks: ReconciliationCheck[];
}

export const STAGE_ORDER: StageId[] = [
  "s01_entity_resolution", "s02_research_planning", "s03_filing_retrieval", "s04_statement_extraction",
  "s05_metrics_engine", "s06_red_flag_engine", "s07_news_intelligence", "s08_sentiment_filter",
  "s09_market_peers", "s10_contradiction_resolver", "s11_synthesis", "s12_verification_gate",
];

export const STAGE_LABELS: Record<StageId, { num: string; short: string; title: string }> = {
  s01_entity_resolution: { num: "01", short: "Entity", title: "Entity Resolution" },
  s02_research_planning: { num: "02", short: "Plan", title: "Research Planning" },
  s03_filing_retrieval: { num: "03", short: "Filings", title: "Filing Retrieval" },
  s04_statement_extraction: { num: "04", short: "Extract", title: "Statement Extraction & Reconciliation" },
  s05_metrics_engine: { num: "05", short: "Metrics", title: "Deterministic Metrics Engine" },
  s06_red_flag_engine: { num: "06", short: "Red Flags", title: "Red-Flag Rules Engine" },
  s07_news_intelligence: { num: "07", short: "News", title: "News Intelligence" },
  s08_sentiment_filter: { num: "08", short: "Sentiment", title: "Sentiment with Signal/Noise Filter" },
  s09_market_peers: { num: "09", short: "Peers", title: "Market and Peers" },
  s10_contradiction_resolver: { num: "10", short: "Conflicts", title: "Contradiction Resolver" },
  s11_synthesis: { num: "11", short: "Synthesise", title: "Cross-Source Synthesis" },
  s12_verification_gate: { num: "12", short: "Verify", title: "Verification Gate" },
};
