"""Build a .jsonl.gz replay fixture from hand-entered golden inputs using the real engines (Section 10.2).

    python -m scripts.build_golden_fixture --input data/golden_inputs/paytm_nse.yaml \
        --output data/golden_runs/paytm_nse.jsonl.gz

A human copies every figure from the filing into the YAML. The script refuses to build if any numeric
field is TODO or lacks `page`, a source URL (via its document) or `verified_by`. It never estimates.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.config import REPO_ROOT, get_settings
from backend.engine.derived_fields import compute_derived_fields, line_items_period_order
from backend.engine.formatters import format_currency
from backend.engine.metrics_calculator import compute_all_metrics, compute_quarterly_metrics
from backend.engine.reconciliation import run_reconciliation_suite
from backend.engine.red_flag_evaluator import evaluate_red_flag_rules
from backend.engine.unit_normaliser import normalise_units, parse_cell_decimal
from backend.engine.verification_gate import peer_token_id, run_verification_gate
from backend.pipeline.graph import STAGE_META, EventEmitter
from backend.pipeline.stages.s08_sentiment_filter import aggregate_sentiment
from backend.pipeline.stages.s09_market_peers import compute_peer_medians, peer_row_from_items
from backend.pipeline.stages.s10_contradiction_resolver import resolve_by_tier_weight
from backend.pipeline.stages.s11_synthesis import build_provenance_dag, count_claims
from backend.schemas import (
    TIER_WEIGHTS,
    ActorBadge,
    AudienceSentimentSummary,
    CodeVsLlmAction,
    CurrencyUnit,
    EvidenceCitation,
    ExecutiveSummary,
    ExplainStagePanelData,
    InsightCard,
    LineItem,
    ManagementPerson,
    NewsEvent,
    ResearchRunState,
    ResolvedEntity,
    SentimentItem,
    SourceTier,
    StageId,
    StageStatus,
    ToolCallTrace,
)

SCHEMA_VERSION = "2"
FIXTURE_RUN_ID = "run_fixture"
TODO = "TODO"


class GoldenInputError(ValueError):
    """The YAML is incomplete or inconsistent. The message lists every problem found."""


# ---------------------------------------------------------------- validation

def _is_todo(v: Any) -> bool:
    return v is None or (isinstance(v, str) and (v.strip() == "" or TODO in v))


def validate_inputs(data: dict) -> list[str]:
    """Every numeric field needs value, page, a document with source_url, and verified_by."""
    errors: list[str] = []
    docs = data.get("documents") or {}
    for doc_id, doc in docs.items():
        if _is_todo(doc.get("source_url")):
            errors.append(f"documents.{doc_id}: source_url missing")

    def numeric(path: str, row: dict) -> None:
        for key in ("value", "page", "verified_by", "doc"):
            if _is_todo(row.get(key)):
                errors.append(f"{path}: {key} is missing or TODO")
        if row.get("doc") and row["doc"] not in docs:
            errors.append(f"{path}: unknown document {row['doc']!r}")
        if "unit" in row and _is_todo(row.get("unit")):
            errors.append(f"{path}: unit header is missing or TODO")

    def fact(path: str, row: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            if _is_todo(row.get(key)):
                errors.append(f"{path}: {key} is missing or TODO")

    for section in ("line_items", "quarterly_items", "printed_ebitda"):
        for i, row in enumerate(data.get(section) or []):
            numeric(f"{section}[{i}] {row.get('period')} {row.get('field_key', 'ebitda')}", row)
    for p_i, peer in enumerate(data.get("peers") or []):
        for i, row in enumerate(peer.get("line_items") or []):
            numeric(f"peers[{p_i}].line_items[{i}] {row.get('period')} {row.get('field_key')}", row)
        for key in ("close_price", "diluted_eps"):
            if peer.get(key) is not None:
                numeric(f"peers[{p_i}].{key}", peer[key])
    for i, row in enumerate(data.get("evidence") or []):
        fact(f"evidence[{i}] {row.get('id')}", row, ("id", "doc", "snippet", "verified_by"))
    for i, row in enumerate(data.get("management_roster") or []):
        fact(f"management_roster[{i}]", row, ("name", "role", "evidence", "verified_by"))
    for i, row in enumerate(data.get("news_events") or []):
        fact(f"news_events[{i}] {row.get('id')}", row, ("id", "date", "headline", "summary", "evidence", "verified_by"))
    for i, row in enumerate((data.get("sentiment") or {}).get("items") or []):
        fact(f"sentiment.items[{i}] {row.get('id')}", row, ("id", "quote", "url", "verified_by"))
    if _is_todo(data.get("entity", {}).get("legal_name")):
        errors.append("entity.legal_name is missing or TODO")
    return errors


# ---------------------------------------------------------------- builders

@dataclass
class Ctx:
    data: dict
    currency: CurrencyUnit
    docs: dict[str, dict]
    out_dir: Path
    evidence: dict[str, EvidenceCitation] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    rendered: list[str] = field(default_factory=list)

    def doc(self, doc_id: str) -> dict:
        return self.docs[doc_id]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _render_page(ctx: Ctx, doc_id: str, page: Optional[int]) -> tuple[Optional[str], Optional[tuple[float, float]]]:
    """Render the cited page to PNG (144 dpi) and a one-page PDF excerpt when the document has a local PDF."""
    doc = ctx.doc(doc_id)
    pdf = doc.get("pdf")
    if not pdf or not page:
        return None, None
    src = (REPO_ROOT / pdf) if not Path(pdf).is_absolute() else Path(pdf)
    if not src.exists():
        ctx.gaps.append(f"{doc.get('title', doc_id)} p.{page} page image unavailable (PDF not found locally)")
        return None, None
    import pymupdf as fitz

    stem = f"{_slug(doc_id)}_p{page}"
    png, one_page = ctx.out_dir / f"{stem}.png", ctx.out_dir / f"{stem}.pdf"
    with fitz.open(src) as pdf_doc:
        pg = pdf_doc[page - 1]
        size = (float(pg.rect.width), float(pg.rect.height))
        if not png.exists():
            pg.get_pixmap(dpi=144).save(png)
            with fitz.open() as out:
                out.insert_pdf(pdf_doc, from_page=page - 1, to_page=page - 1)
                out.save(one_page)
    ctx.rendered.append(stem)
    return f"{stem}.png", size


def make_evidence(ctx: Ctx, evidence_id: str, doc_id: str, page: Optional[int], snippet: str,
                  raw: str, unit: Optional[str] = None, bbox: Optional[list] = None) -> EvidenceCitation:
    doc = ctx.doc(doc_id)
    tier = SourceTier(doc.get("tier", "T1"))
    image, size = _render_page(ctx, doc_id, page)
    ev = EvidenceCitation(
        evidence_id=evidence_id, document_id=doc_id, document_title=doc["title"], source_url=doc["source_url"],
        source_tier=tier, trust_weight=TIER_WEIGHTS[tier], page_number=page,
        table_id=doc.get("table_id"), bbox=tuple(bbox) if bbox else None, page_size=size,
        excerpt_image_path=image, verbatim_snippet=snippet, extracted_value_raw=raw, source_unit=unit,
        retrieved_at=doc.get("retrieved_at"),
    )
    ctx.evidence[evidence_id] = ev
    return ev


def build_line_item(ctx: Ctx, row: dict, prefix: str = "") -> LineItem:
    period, key = str(row["period"]), row["field_key"]
    raw = str(row["value"])
    value = normalise_units(parse_cell_decimal(raw), row["unit"], ctx.currency)
    seg = row.get("segment")
    suffix = f"_{_slug(seg)}" if seg else ""
    item_id = f"li_{prefix}{period.lower()}_{key}{suffix}"
    ev_id = f"ev_{_slug(row['doc'])}_p{row['page']}_{key}{suffix}_{period.lower()}{('_' + prefix.rstrip('_')) if prefix else ''}"
    snippet = row.get("snippet") or f"{row['label']} {raw}"
    ev = make_evidence(ctx, ev_id, row["doc"], int(row["page"]), snippet, raw, row["unit"], row.get("bbox"))
    return LineItem(
        item_id=item_id, fiscal_period=period, field_key=key, reported_label=row["label"], value=value,
        formatted_value=format_currency(value, ctx.currency), unit=ctx.currency,
        is_restated=bool(row.get("restated", False)), segment_name=seg, evidence=ev,
    )


def expand_tokens(text: Optional[str], formatted: dict[str, str]) -> Optional[str]:
    """`{{met_dso_fy26}}` -> '84.3 d [[met_dso_fy26]]', so no human or model types the number in prose."""
    if text is None:
        return None

    def repl(m: re.Match[str]) -> str:
        token = m.group(1)
        if token not in formatted:
            raise GoldenInputError(f"Prose references unknown token {{{{{token}}}}}")
        return f"{formatted[token]} [[{token}]]"

    return re.sub(r"\{\{([a-z0-9_]+)\}\}", repl, text)


# ---------------------------------------------------------------- explain panels

def _tc(rng: random.Random, n: int, tool: str, actor: ActorBadge, args: dict, lo: int, hi: int,
        sample: Any, status: str = "ok") -> ToolCallTrace:
    text = sample if isinstance(sample, str) else json.dumps(sample, ensure_ascii=False, default=str)
    return ToolCallTrace(call_id=f"tc_{n:03d}", tool_name=tool, actor=actor, arguments=args,
                         duration_ms=rng.randint(lo, hi), status=status, raw_output_sample=text[:1200])


def panel(stage: StageId, inputs: list[str], calls: list[ToolCallTrace], actions: list[tuple[ActorBadge, str]],
          decision: str, rationale: str, guards: list[str]) -> ExplainStagePanelData:
    meta = STAGE_META[stage]
    return ExplainStagePanelData(
        stage_id=stage, stage_title=meta.title, purpose_one_sentence=meta.purpose, inputs_summary=inputs,
        tool_calls=calls, actions=[CodeVsLlmAction(actor=a, action_summary=s) for a, s in actions],
        decision_made=decision, decision_rationale=rationale, failure_modes_and_guards=guards,
    )


# Plausible live durations per stage (ms); the replay player rescales them to a fixed demo length.
STAGE_MS = {
    StageId.S01_ENTITY: 1800, StageId.S02_PLAN: 3200, StageId.S03_RETRIEVE: 14500, StageId.S04_EXTRACT: 22800,
    StageId.S05_METRICS: 320, StageId.S06_RED_FLAGS: 140, StageId.S07_NEWS: 26500, StageId.S08_SENTIMENT: 12400,
    StageId.S09_MARKET: 9800, StageId.S10_CONTRADICTION: 4300, StageId.S11_SYNTHESIS: 16800, StageId.S12_VERIFY: 260,
}


# ---------------------------------------------------------------- main build

def build_fixture(data: dict, out_dir: Path) -> tuple[dict, list[dict]]:
    """Return (header, events as JSON-ready dicts). Raises GoldenInputError when the YAML is incomplete."""
    errors = validate_inputs(data)
    if errors:
        raise GoldenInputError("Refusing to build: " + str(len(errors)) + " problem(s)\n  - " + "\n  - ".join(errors))

    replay_id = data["replay_id"]
    rng = random.Random(replay_id)
    ent_raw = dict(data["entity"])
    currency = CurrencyUnit(ent_raw["currency"])
    ctx = Ctx(data=data, currency=currency, docs=data["documents"], out_dir=out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entity = ResolvedEntity(query=data["company_query"], **ent_raw)
    settings = get_settings()

    # Stage 3/4 inputs
    for row in data.get("evidence") or []:
        make_evidence(ctx, row["id"], row["doc"], row.get("page"), row["snippet"], row.get("raw", row["snippet"]))
    roster = [
        ManagementPerson(name=r["name"], role=r["role"], since=r.get("since"), evidence_id=r["evidence"])
        for r in data.get("management_roster") or []
    ]
    raw_items = [build_line_item(ctx, r) for r in data.get("line_items") or []]
    derived, derived_gaps = compute_derived_fields(raw_items, data.get("gaap"))
    line_items = sorted(raw_items + derived, key=lambda li: (line_items_period_order(li.fiscal_period), li.item_id))
    q_raw = [build_line_item(ctx, r, prefix="q_") for r in data.get("quarterly_items") or []]
    q_derived, _ = compute_derived_fields(q_raw, data.get("gaap"))
    quarterly_items = sorted(q_raw + q_derived, key=lambda li: (line_items_period_order(li.fiscal_period), li.item_id))
    printed_ebitda = {
        str(r["period"]): normalise_units(parse_cell_decimal(str(r["value"])), r["unit"], currency)
        for r in data.get("printed_ebitda") or []
    }
    annual_periods = sorted({li.fiscal_period for li in raw_items if li.field_key != "segment_revenue"},
                            key=line_items_period_order)
    if len(annual_periods) < 3:
        ctx.gaps.append(f"Only {len(annual_periods)} audited annual period(s) available; metrics needing 3 are N/M")
    recon = run_reconciliation_suite([li for li in line_items if li.field_key != "segment_revenue"], printed_ebitda)
    for check in recon:
        if check.status == "failed":
            ctx.gaps.append(f"{check.fiscal_period} {check.check_name} failed: {check.failure_reason}")
    ctx.gaps.extend(derived_gaps)
    extraction_gaps = list(ctx.gaps)

    # Stage 5/6
    ledger = compute_all_metrics([li for li in line_items if li.field_key != "segment_revenue"])
    q_metrics = compute_quarterly_metrics(quarterly_items)
    red_flags = evaluate_red_flag_rules(ledger, line_items, settings.atlas_rules_path)

    # Stage 7
    news = []
    for r in data.get("news_events") or []:
        ev = ctx.evidence[r["evidence"]]
        news.append(NewsEvent(
            event_id=r["id"], event_date=r["date"], category=r["category"], headline=r["headline"],
            summary=r["summary"], polarity=r["polarity"], source_names=r["source_names"],
            highest_tier=SourceTier(r.get("tier", ev.source_tier.value)),
            corroborated_by_filing=bool(r.get("corroborated_by_filing", False)),
            article_count_merged=int(r.get("article_count_merged", 1)), evidence=ev,
        ))

    # Stage 8 (counts and percentages by code)
    sent_raw = data.get("sentiment") or {}
    sent_items = []
    for r in sent_raw.get("items") or []:
        doc_id = r.get("doc", "social")
        if doc_id not in ctx.docs:
            ctx.docs[doc_id] = {"title": r["platform"], "source_url": r["url"], "tier": "T4"}
        ev = make_evidence(ctx, f"ev_post_{_slug(r['id'])}", doc_id, None, r["quote"], r["quote"])
        ev = ev.model_copy(update={"source_url": r["url"], "document_title": r["platform"]})
        ctx.evidence[ev.evidence_id] = ev
        sent_items.append(SentimentItem(
            post_id=r["id"], platform=r["platform"], audience=r["audience"], quote_text=r["quote"],
            polarity=r["polarity"], verdict=r["verdict"], verdict_reasons=r["reasons"],
            corroborating_ids=r.get("corroborating_ids", []), evidence=ev,
        ))
    removed = sent_raw.get("removed_posts") or []
    sentiment = aggregate_sentiment(
        sent_items, total_posts_fetched=len(sent_items) + len(removed), bots_and_duplicates_removed=len(removed),
        audience_breakdown=[AudienceSentimentSummary(**a) for a in sent_raw.get("audience_breakdown") or []],
    ) if sent_items else None
    if not sent_items:
        ctx.gaps.append("No public sentiment posts recorded for this fixture")

    # Stage 9
    peer_rows = []
    target_row = peer_row_from_items(
        data["display"]["company_name"], data["display"]["ticker"],
        [li for li in line_items if li.field_key != "segment_revenue"], is_target=True,
    )
    peer_rows.append(target_row)
    for p in data.get("peers") or []:
        items = [build_line_item(ctx, r, prefix=f"peer_{_slug(p['ticker'].split(':')[-1])}_") for r in p.get("line_items") or []]
        price = p.get("close_price")
        eps = p.get("diluted_eps")
        price_v = parse_cell_decimal(str(price["value"])) if price else None
        eps_v = parse_cell_decimal(str(eps["value"])) if eps else None
        peer_rows.append(peer_row_from_items(p["company_name"], p["ticker"], items, close_price=price_v,
                                             diluted_eps=eps_v, price_as_of=p.get("price_as_of")))
    median_row = compute_peer_medians(peer_rows) if len(peer_rows) > 1 else None
    if median_row:
        peer_rows.append(median_row)
    else:
        ctx.gaps.append("No peer filings recorded; peer comparison unavailable")

    # Token -> formatted value map for prose expansion
    formatted: dict[str, str] = {r.metric_id: r.formatted_value for r in [*ledger, *q_metrics]}
    formatted.update({li.item_id: li.formatted_value for li in [*line_items, *quarterly_items]})
    for row in peer_rows:
        for col, text in row.formatted.items():
            formatted[peer_token_id(row, col)] = text

    prose = data.get("prose") or {}
    mk_adv = [expand_tokens(x, formatted) for x in prose.get("market_advantages") or []]
    mk_dis = [expand_tokens(x, formatted) for x in prose.get("market_disadvantages") or []]
    mk_pos = expand_tokens(prose.get("market_positioning_prose"), formatted)

    # Stage 10
    contradictions = [
        resolve_by_tier_weight(
            c["id"], c["topic"], c["type"], expand_tokens(c["a_claim"], formatted), ctx.evidence[c["a_evidence"]],
            expand_tokens(c["b_claim"], formatted), ctx.evidence[c["b_evidence"]], c.get("note", ""),
        )
        for c in data.get("contradictions") or []
    ]

    # Stage 11
    insights = [
        InsightCard(
            insight_id=i["id"], title=expand_tokens(i["title"], formatted), body=expand_tokens(i["body"], formatted),
            classification=i["classification"], polarity=i["polarity"],
            supporting_metric_ids=i.get("metrics", []), supporting_item_ids=i.get("items", []),
            supporting_red_flag_ids=i.get("red_flags", []), supporting_news_ids=i.get("news", []),
            supporting_sentiment_ids=i.get("sentiment", []), supporting_contradiction_ids=i.get("contradictions", []),
        )
        for i in prose.get("insights") or []
    ]
    s = prose.get("summary")
    summary = None
    if s:
        f, inf, hyp = count_claims(insights)
        summary = ExecutiveSummary(
            verdict_headline=expand_tokens(s["verdict_headline"], formatted),
            verdict_prose=expand_tokens(s["verdict_prose"], formatted),
            fact_count=f, inference_count=inf, hypothesis_count=hyp,
            business_overview=expand_tokens(s.get("business_overview", ""), formatted),
            revenue_mix_summary=expand_tokens(s.get("revenue_mix_summary", ""), formatted),
            management_summary=expand_tokens(s.get("management_summary", ""), formatted),
            governance_watch_note=expand_tokens(s.get("governance_watch_note"), formatted),
            opportunities=[expand_tokens(x, formatted) for x in s.get("opportunities", [])],
            risks=[expand_tokens(x, formatted) for x in s.get("risks", [])],
            quarterly_direction_note=expand_tokens(s.get("quarterly_direction_note", ""), formatted),
            analyst_follow_up_notes=[expand_tokens(x, formatted) for x in s.get("analyst_follow_up_notes", [])],
        )
    ctx.gaps.extend(data.get("stated_gaps") or [])

    state = ResearchRunState(
        run_id=FIXTURE_RUN_ID, mode="replay", company_query=data["company_query"], status="running",
        started_at_ms=0, entity=entity, sub_questions=data.get("sub_questions") or [],
        fiscal_years=annual_periods, line_items=line_items, quarterly_items=quarterly_items,
        quarterly_metrics=q_metrics, management_roster=roster, reconciliation_checks=recon,
        metrics_ledger=ledger, red_flags=red_flags, news_events=news, sentiment=sentiment, peer_comps=peer_rows,
        market_advantages=mk_adv, market_disadvantages=mk_dis, market_positioning_prose=mk_pos,
        insights=insights, contradictions=contradictions, summary=summary,
        evidence_registry=dict(ctx.evidence), stated_gaps=list(ctx.gaps),
    )
    nodes, edges = build_provenance_dag(state)
    state = state.model_copy(update={"provenance_nodes": nodes, "provenance_edges": edges})
    report, gated = run_verification_gate(state)

    events = _emit_events(rng, entity, data, ctx, state, gated, report, extraction_gaps)
    header = {
        "_meta": True, "replay_id": replay_id, "company_query": data["company_query"],
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "built_by": "build_golden_fixture",
        "schema_version": SCHEMA_VERSION, "total_events": len(events), "synthetic": bool(data.get("synthetic", False)),
    }
    return header, events


def _dump(models: Any) -> Any:
    if isinstance(models, list):
        return [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in models]
    if isinstance(models, dict):
        return {k: v.model_dump(mode="json") for k, v in models.items()}
    return models.model_dump(mode="json") if hasattr(models, "model_dump") else models


def _emit_events(rng: random.Random, entity: ResolvedEntity, data: dict, ctx: Ctx,
                 state: ResearchRunState, gated: ResearchRunState, report, extraction_gaps: list[str]) -> list[dict]:
    em = EventEmitter(run_id=FIXTURE_RUN_ID, started_at_ms=0)
    clock = {"t": 0}
    n = {"tc": 0}

    def tick(stage: StageId, frac: float = 1.0) -> int:
        clock["t"] += int(STAGE_MS[stage] * frac * rng.uniform(0.85, 1.15))
        return clock["t"]

    def tc(*a, **k) -> ToolCallTrace:
        n["tc"] += 1
        return _tc(rng, n["tc"], *a, **k)

    S = StageId
    C, L = ActorBadge.CODE, ActorBadge.LLM
    running = StageStatus.RUNNING
    done = StageStatus.COMPLETED
    li_all = state.line_items
    raw_li = [li for li in li_all if not li.is_derived]
    derived_li = [li for li in li_all if li.is_derived]
    docs = ctx.docs
    t1_docs = [d for d in docs.values() if d.get("tier", "T1") in ("T1", "T2")]
    stated: list[str] = []
    registry: dict[str, EvidenceCitation] = {}

    def gaps_patch(new: list[str]) -> list[str]:
        stated.extend(g for g in new if g not in stated)
        return list(stated)

    # S1
    em.emit(S.S01_ENTITY, running, f'search_indian_exchanges("{data["company_query"]}") · search_sec_edgar_tickers',
            elapsed_ms=tick(S.S01_ENTITY, 0.1))
    rejected = entity.rejected_candidates
    em.emit(S.S01_ENTITY, done, f"Matched {entity.legal_name} ({entity.exchange or 'unlisted'}: {entity.ticker or '—'}); rejected {len(rejected)} lookalike(s)",
            elapsed_ms=tick(S.S01_ENTITY), state_patch={"entity": _dump(entity)},
            explain_panel=panel(S.S01_ENTITY, [f"Query: {data['company_query']}"], [
                tc("search_indian_exchanges" if entity.jurisdiction == "IN" else "search_sec_edgar_tickers", C,
                   {"query": data["company_query"]}, 180, 650,
                   {"candidates": [entity.legal_name, *rejected]}),
                tc("disambiguate_candidates", L, {"candidates": 1 + len(rejected)}, 700, 1500,
                   {"legal_name": entity.legal_name, "ticker": entity.ticker, "rejected_candidates": rejected}),
            ], [(C, "Queried exchange and regulator registries for candidate entities"),
                (L, "Selected the listed operating parent among the candidates; never invented a ticker or CIK")],
                f"Selected {entity.legal_name}", "Listed operating parent with current filings; lookalikes rejected by registry status.",
                ["Zero candidates: retry with corporate suffixes stripped, then halt with an ambiguity error",
                 "LLM may only choose among registry records, so a hallucinated ticker cannot enter the run"]))
    # S2
    em.emit(S.S02_PLAN, running, "Generating 12 sub-questions across 4 buckets", elapsed_ms=tick(S.S02_PLAN, 0.1))
    em.emit(S.S02_PLAN, done, f"{len(state.sub_questions)} sub-questions; numbers routed to T1/T2 filings only",
            elapsed_ms=tick(S.S02_PLAN), state_patch={"sub_questions": state.sub_questions},
            explain_panel=panel(S.S02_PLAN, [f"Entity: {entity.legal_name} ({entity.sector})"], [
                tc("generate_sub_questions", L, {"buckets": 4, "per_bucket": 3}, 1800, 3200, {"sub_questions": state.sub_questions[:4]}),
                tc("build_source_routing_plan", C, {"jurisdiction": entity.jurisdiction}, 3, 12,
                   {"numerical": ["T1", "T2"], "narrative": ["T3", "T4"]}),
            ], [(L, "Wrote research sub-questions without numbers or assumptions"),
                (C, "Enforced routing: numerical questions may only be answered from T1/T2 filings")],
                "Plan accepted", "Covers financial quality, governance, sentiment and peer valuation.",
                ["Schema failure: up to 2 retries, then a deterministic 12-question sector template",
                 "Timeout over 10 s falls back to the template"]))
    # S3 running, S7 running (parallel)
    em.emit(S.S03_RETRIEVE, running, "Fetching annual reports and statements (BSE first)" if entity.jurisdiction == "IN" else "Fetching 10-K filings and XBRL companyfacts",
            elapsed_ms=tick(S.S03_RETRIEVE, 0.05))
    em.emit(S.S07_NEWS, running, "Fetching Google News RSS, GDELT and exchange announcements", elapsed_ms=tick(S.S07_NEWS, 0.02))
    ev_s3 = {k: v for k, v in ctx.evidence.items() if v.source_tier in (SourceTier.T1, SourceTier.T2)}
    registry.update(ev_s3)
    em.emit(S.S03_RETRIEVE, done, f"{len(t1_docs)} official document(s) retrieved for {', '.join(state.fiscal_years)}",
            elapsed_ms=tick(S.S03_RETRIEVE),
            state_patch={"fiscal_years": state.fiscal_years, "sources_read_count": len(docs),
                         "evidence_registry": _dump(registry), "management_roster": _dump(state.management_roster)},
            explain_panel=panel(S.S03_RETRIEVE, [f"{entity.legal_name}, latest {len(state.fiscal_years)} audited years"], [
                *[tc("fetch_india_bse_nse_filings" if entity.jurisdiction == "IN" else "fetch_sec_10k_and_10q", C,
                     {"document": d["title"]}, 400, 2600, {"url": d["source_url"]}) for d in t1_docs[:6]],
                tc("verify_document_authenticity", C, {"documents": len(t1_docs)}, 20, 90,
                   {"whitelist_hosts": sorted({re.sub(r'^https?://([^/]+).*$', r'\1', d['source_url']) for d in t1_docs})}),
            ], [(C, f"Downloaded {len(t1_docs)} filing(s) and stored their SHA-256"),
                (C, "Checked each download host against the T1/T2 whitelist and discarded aggregator pages")],
                "Use audited consolidated statements", "Statements come only from regulator or company filing hosts.",
                ["HTTP 429/503: back off 1 s, 2 s, 4 s", "Fewer than 3 audited years: stated gap, continue with what exists",
                 "No fallback to aggregators or unofficial price endpoints for statement figures"]))
    # S4
    em.emit(S.S04_EXTRACT, running, "Locating statement pages and extracting tables", elapsed_ms=tick(S.S04_EXTRACT, 0.05))
    failed = [c for c in state.reconciliation_checks if c.status == "failed"]
    passed = [c for c in state.reconciliation_checks if c.status == "passed"]
    s4_status = StageStatus.WARNING if failed else done
    em.emit(S.S04_EXTRACT, s4_status,
            f"{len(raw_li)} line items, {len(derived_li)} derived; reconciliation {len(passed)} passed, {len(failed)} failed",
            elapsed_ms=tick(S.S04_EXTRACT),
            state_patch={"line_items": _dump(li_all), "quarterly_items": _dump(state.quarterly_items),
                         "reconciliation_checks": _dump(state.reconciliation_checks),
                         "stated_gaps": gaps_patch(extraction_gaps)},
            explain_panel=panel(S.S04_EXTRACT, [f"{len(t1_docs)} filing(s)", f"Unit header(s): {', '.join(sorted({li.evidence.source_unit or '—' for li in raw_li}))}"], [
                tc("locate_statement_pages", C, {"statements": 3}, 90, 400,
                   {li.field_key: f"p.{li.evidence.page_number}" for li in raw_li[:6]}),
                tc("extract_raw_tables_from_pdf", C, {"pages": sorted({li.evidence.page_number or 0 for li in raw_li})[:8]}, 600, 2400,
                   [li.evidence.verbatim_snippet for li in raw_li[:5]]),
                tc("map_table_rows_to_schema", L, {"fields": len({li.field_key for li in raw_li})}, 1500, 4200,
                   [{"field_key": li.field_key, "fiscal_period": li.fiscal_period, "reported_label": li.reported_label} for li in raw_li[:5]]),
                tc("normalise_units", C, {"target": ctx.currency.value}, 2, 9,
                   [{"raw": li.evidence.extracted_value_raw, "unit": li.evidence.source_unit, "value": str(li.value)} for li in raw_li[:4]]),
                tc("compute_derived_fields", C, {"keys": ["interest_expense", "ebit", "ebitda", "gross_debt", "capex"]}, 1, 6,
                   [{"item": li.item_id, "formula": li.reported_label, "value": li.formatted_value} for li in derived_li[:4]]),
                tc("run_reconciliation_suite", C, {"checks": len(state.reconciliation_checks)}, 1, 6,
                   [{"check": c.check_id, "status": c.status} for c in state.reconciliation_checks[:6]]),
            ], [(L, "Mapped table rows to standard field keys by row/column index only; output no numbers"),
                (C, "Parsed every cell to Decimal and normalised units"),
                (C, f"Derived {len(derived_li)} line items (EBIT, EBITDA, gross debt, capex, interest)"),
                (C, f"Ran {len(state.reconciliation_checks)} accounting identity checks")],
                "Failed checks discarded" if failed else "All extracted figures reconciled or skipped with a reason",
                "A figure that fails an accounting identity is discarded rather than guessed.",
                ["If the balance-sheet identity fails by more than 1%, the un-reconciled figure is discarded",
                 "No unit header detected: the table is rejected, never guessed",
                 "Total liabilities is never back-solved from Total equity and liabilities"]))
    # S5
    em.emit(S.S05_METRICS, running, "compute_all_metrics(line_items)", elapsed_ms=tick(S.S05_METRICS, 0.1))
    sample = next((r for r in state.metrics_ledger if r.metric_key == "dso" and r.fiscal_period == state.fiscal_years[-1]),
                  state.metrics_ledger[0] if state.metrics_ledger else None)
    # S7 completes while S5 runs
    news_ev = {n.evidence.evidence_id: n.evidence for n in state.news_events}
    em.emit(S.S07_NEWS, done, f"{len(state.news_events)} material events from {sum(n.article_count_merged for n in state.news_events)} articles",
            elapsed_ms=tick(S.S07_NEWS, 0.5), state_patch={"news_events": _dump(state.news_events)},
            explain_panel=panel(S.S07_NEWS, [f"{entity.legal_name}, last 12 months"], [
                tc("fetch_news_and_exchange_releases", C, {"window_months": 12}, 2200, 6400,
                   {"articles": sum(n.article_count_merged for n in state.news_events)}),
                tc("deduplicate_articles_minhash", C, {"threshold": 0.72}, 40, 160,
                   {"clusters": len(state.news_events)}),
                tc("classify_and_cluster_events", L, {"clusters": len(state.news_events)}, 2600, 5200,
                   [{"event_id": n.event_id, "category": n.category, "polarity": n.polarity.value} for n in state.news_events[:5]]),
            ], [(C, "Fetched press, exchange announcements and regulator orders"),
                (C, "Merged syndicated near-duplicates (Jaccard >= 0.72)"),
                (L, "Clustered events, tagged category and polarity, copying dates verbatim")],
                f"{sum(1 for n in state.news_events if n.corroborated_by_filing)} of {len(state.news_events)} events corroborated by a filing",
                "Events confirmed by T1/T2 documents are flagged; press-only events stay T3.",
                ["The LLM may not invent dates, penalties or deal values", "corroborated_by_filing is true only with a T1/T2 match"]))
    registry.update(news_ev)
    em.emit(S.S05_METRICS, done, f"Computed {len(state.metrics_ledger)} ledger rows in Decimal (0 LLM calls)",
            elapsed_ms=tick(S.S05_METRICS),
            state_patch={"metrics_ledger": _dump(state.metrics_ledger), "quarterly_metrics": _dump(state.quarterly_metrics)},
            explain_panel=panel(S.S05_METRICS, [f"{len(li_all)} line items across {', '.join(state.fiscal_years)}"], [
                tc("compute_metrics", C, {"metrics": 16, "periods": len(state.fiscal_years)}, 4, 30,
                   {"sample": {"metric_id": sample.metric_id, "formula": sample.formula_substituted,
                               "result": sample.formatted_value, "source": sample.primary_source_ref}} if sample else {}),
            ], [(C, f"Computed {len(state.metrics_ledger)} ratios using the Python Decimal module (prec 28, ROUND_HALF_UP)"),
                (C, "Caught zero and negative denominators deterministically as N/M")],
                "Ledger complete", "Every metric is reproducible from its formula and cited inputs.",
                ["Division by zero becomes N/M (Zero Base)", "Negative bases become N/M (Neg Base)", "No LLM involvement at all"]))
    # S6, S8, S9 run in parallel
    em.emit(S.S06_RED_FLAGS, running, "Evaluating default_red_flags.yaml", elapsed_ms=tick(S.S06_RED_FLAGS, 0.2))
    em.emit(S.S08_SENTIMENT, running, "Fetching Reddit, Hacker News and Play Store posts", elapsed_ms=tick(S.S08_SENTIMENT, 0.02))
    em.emit(S.S09_MARKET, running, "Selecting listed sector peers", elapsed_ms=tick(S.S09_MARKET, 0.02))
    fired = [r for r in state.red_flags if r.fired]
    em.emit(S.S06_RED_FLAGS, done, f"{len(fired)} of {len(state.red_flags)} rules fired",
            elapsed_ms=tick(S.S06_RED_FLAGS), state_patch={"red_flags": _dump(state.red_flags)},
            explain_panel=panel(S.S06_RED_FLAGS, [f"{len(state.metrics_ledger)} ledger rows", "data/rules/default_red_flags.yaml"], [
                tc("evaluate_red_flag_rules", C, {"rules": len(state.red_flags)}, 2, 14,
                   [{"rule": r.rule_id, "status": r.status} for r in state.red_flags]),
            ], [(C, "Parsed rule expressions with a whitelisted AST evaluator (no eval)"),
                (C, f"Evaluated {len(state.red_flags)} rules; filled explanations from formatted values")],
                f"Fired: {', '.join(r.rule_name for r in fired) or 'none'}", "Thresholds are declarative and versioned; no model opinion.",
                ["Missing inputs give insufficient_data instead of a guess", "Function calls or attribute access in a rule fail at load time"]))
    peers_only = [r for r in state.peer_comps if not r.is_target and not r.is_median_row]
    em.emit(S.S09_MARKET, done, f"{len(peers_only)} peer(s) benchmarked; medians computed in Decimal",
            elapsed_ms=tick(S.S09_MARKET),
            state_patch={"peer_comps": _dump(state.peer_comps), "market_advantages": state.market_advantages,
                         "market_disadvantages": state.market_disadvantages,
                         "market_positioning_prose": state.market_positioning_prose},
            explain_panel=panel(S.S09_MARKET, [f"Sector: {entity.sector}"], [
                tc("select_sector_peers", L, {"region": entity.jurisdiction}, 900, 2200, [r.company_name for r in peers_only]),
                tc("fetch_peer_filing_metrics", C, {"peers": len(peers_only)}, 1500, 5200,
                   {r.ticker: r.formatted for r in peers_only[:3]}),
                tc("compute_peer_medians", C, {"columns": 4}, 1, 4,
                   next((r.formatted for r in state.peer_comps if r.is_median_row), {})),
                tc("draft_positioning_summary", L, {"cite_only": "PEER_TABLE_WITH_MEDIANS"}, 1400, 3000,
                   state.market_positioning_prose or ""),
            ], [(L, "Selected listed peers in the same sector and region"),
                (C, "Ran peers through the same extraction and metrics engines"),
                (C, "Computed exact medians in Decimal"),
                (L, "Drafted positioning prose citing only peer-table values")],
                "Peer table built", "Market share is not computed: no reliable public denominator.",
                ["P/E is N/M when trailing EPS <= 0", "Peers limited to SEC XBRL / BSE filers"]))
    sent_patch = {"sentiment": _dump(state.sentiment)} if state.sentiment else {}
    sig = [i for i in (state.sentiment.items if state.sentiment else []) if i.verdict == "signal"]
    em.emit(S.S08_SENTIMENT, done,
            f"{state.sentiment.total_posts_fetched} posts fetched, {state.sentiment.bots_and_duplicates_removed} removed, {len(sig)} signal" if state.sentiment else "No sentiment data",
            elapsed_ms=tick(S.S08_SENTIMENT), state_patch=sent_patch,
            explain_panel=panel(S.S08_SENTIMENT, ["Reddit (OAuth), Hacker News Algolia, Play Store reviews", "Metrics ledger and news events for corroboration"], [
                tc("fetch_public_sentiment_posts", C, {"platforms": ["Reddit", "Hacker News", "Play Store"]}, 1800, 5200,
                   {"fetched": state.sentiment.total_posts_fetched if state.sentiment else 0}),
                tc("filter_bots_and_spam", C, {"jaccard": 0.85, "min_account_age_days": 30, "min_karma": 15}, 30, 120,
                   {"removed": state.sentiment.bots_and_duplicates_removed if state.sentiment else 0}),
                tc("evaluate_signal_vs_noise", L, {"posts": len(state.sentiment.items) if state.sentiment else 0}, 2200, 4800,
                   [{"post_id": i.post_id, "verdict": i.verdict, "corroborating_ids": i.corroborating_ids} for i in (state.sentiment.items[:4] if state.sentiment else [])]),
                tc("aggregate_sentiment", C, {"method": "largest_remainder"}, 1, 3,
                   {"positive_pct": state.sentiment.positive_pct, "neutral_pct": state.sentiment.neutral_pct,
                    "negative_pct": state.sentiment.negative_pct} if state.sentiment else {}),
            ], [(C, "Removed bots, near-duplicates and stale posts with per-platform rules"),
                (L, "Classified audience, polarity and signal vs noise, linking corroborating ids"),
                (C, "Counted posts and computed percentages (they always sum to 100)")],
                f"{len(sig)} signal item(s) retained", "Only posts corroborated by a metric, a verified event or 3+ independent posts count as signal.",
                ["No scraping behind login walls (LinkedIn, Glassdoor, X)", "The LLM never outputs counts or percentages"]))
    # S10
    em.emit(S.S10_CONTRADICTION, running, "Comparing T1 figures with T2/T3/T4 claims", elapsed_ms=tick(S.S10_CONTRADICTION, 0.1))
    em.emit(S.S10_CONTRADICTION, done, f"{len(state.contradictions)} contradiction(s) resolved by tier weight",
            elapsed_ms=tick(S.S10_CONTRADICTION), state_patch={"contradictions": _dump(state.contradictions)},
            explain_panel=panel(S.S10_CONTRADICTION, ["Ledger and line items (T1)", "Investor deck, call, press and broker claims"], [
                tc("detect_numeric_and_narrative_conflicts", L, {"claims": len(state.contradictions)}, 1200, 2800,
                   [{"id": c.contradiction_id, "topic": c.topic} for c in state.contradictions]),
                tc("resolve_by_tier_weight", C, {"weights": {t.value: w for t, w in TIER_WEIGHTS.items()}}, 1, 3,
                   [{"id": c.contradiction_id, "winner": c.winning_source} for c in state.contradictions]),
            ], [(L, "Extracted conflicting claims and their evidence ids"),
                (C, "Assigned the winner from tier weights (higher weight wins numeric conflicts)")],
                "Contradictions resolved", "Synthesis must know which claims lost before writing insights.",
                ["Management narrative against a T1 trend is kept as framing, not fact"]))
    # S11
    em.emit(S.S11_SYNTHESIS, running, "Synthesising insights with [[token]] citations", elapsed_ms=tick(S.S11_SYNTHESIS, 0.05))
    em.emit(S.S11_SYNTHESIS, done, f"{len(state.insights)} insight(s); provenance graph with {len(state.provenance_nodes)} nodes",
            elapsed_ms=tick(S.S11_SYNTHESIS),
            state_patch={"insights": _dump(state.insights), "provenance_nodes": _dump(state.provenance_nodes),
                         "provenance_edges": _dump(state.provenance_edges),
                         **({"summary": _dump(state.summary)} if state.summary else {})},
            explain_panel=panel(S.S11_SYNTHESIS, ["Ledger, red flags, news, signal sentiment, peers, contradictions"], [
                tc("synthesize_insights_and_summary", L, {"model": "synthesis"}, 6000, 12000,
                   [{"insight_id": i.insight_id, "classification": i.classification.value} for i in state.insights]),
                tc("build_provenance_dag", C, {"insights": len(state.insights)}, 2, 9,
                   {"nodes": len(state.provenance_nodes), "edges": len(state.provenance_edges)}),
            ], [(L, "Wrote insights and summary citing ledger tokens; no arithmetic"),
                (C, "Counted fact / inference / hypothesis claims"),
                (C, "Built the provenance graph from the ids each insight cites")],
                "Draft report ready for verification", "Every number in prose must carry a resolvable citation token.",
                ["Any uncited number is removed by the Verification Gate", "Business and management text may only use filing evidence"]))
    # S12
    em.emit(S.S12_VERIFY, running, "Scanning prose for numeric tokens", elapsed_ms=tick(S.S12_VERIFY, 0.2))
    final_gaps = gaps_patch(state.stated_gaps)
    s12_status = StageStatus.WARNING if report.unverified_tokens_blocked else done
    em.emit(S.S12_VERIFY, s12_status,
            f"{report.verified_against_ledger}/{report.total_numeric_tokens_scanned} numbers verified; {report.unverified_tokens_blocked} blocked",
            elapsed_ms=tick(S.S12_VERIFY),
            state_patch={"verification": _dump(report), "insights": _dump(gated.insights),
                         **({"summary": _dump(gated.summary)} if gated.summary else {}),
                         "news_events": _dump(gated.news_events), "market_advantages": gated.market_advantages,
                         "market_disadvantages": gated.market_disadvantages,
                         "market_positioning_prose": gated.market_positioning_prose,
                         "stated_gaps": final_gaps, "status": "completed"},
            explain_panel=panel(S.S12_VERIFY, ["All summary, insight, market and news prose"], [
                tc("run_verification_gate", C, {"fields": "summary, insights, market, news"}, 3, 18, _dump(report)),
            ], [(C, f"Scanned {report.total_numeric_tokens_scanned} numeric tokens against the ledger"),
                (C, f"Downgraded {len(report.claims_downgraded_to_hypothesis)} unsupported claim(s) to hypothesis")],
                "Passed" if report.passed else f"{report.unverified_tokens_blocked} unverified token(s) removed",
                "A number reaches the report only if it matches a cited ledger value exactly.",
                ["Unverified sentences are replaced with a stated-gap marker", "Claim counts are recounted after downgrades"]))
    return [e.model_dump(mode="json") for e in em.events]


def write_fixture(path: Path, header: dict, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def update_manifest(golden_dir: Path, data: dict, header: dict, fixture_name: str) -> None:
    path = golden_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"replays": []}
    entry = {
        "replay_id": data["replay_id"], "file": fixture_name, "company_query": data["company_query"],
        **data["display"], "synthetic": header["synthetic"], "total_events": header["total_events"],
    }
    manifest["replays"] = [r for r in manifest["replays"] if r["replay_id"] != data["replay_id"]] + [entry]
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args(argv)
    data = yaml.safe_load(args.input.read_text(encoding="utf-8"))
    golden_dir = args.output.parent
    try:
        header, events = build_fixture(data, golden_dir / "fixtures_pdfs")
    except GoldenInputError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_fixture(args.output, header, events)
    update_manifest(golden_dir, data, header, args.output.name)
    print(f"Wrote {args.output} ({header['total_events']} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
