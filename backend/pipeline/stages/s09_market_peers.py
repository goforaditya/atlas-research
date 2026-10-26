"""Stage 9: market and peers. Peer metrics come from the same engines; medians are computed in Decimal."""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from backend.engine.decimal_math import median, safe_div
from backend.engine.derived_fields import compute_derived_fields
from backend.engine.formatters import MISSING, format_multiple, format_pct
from backend.engine.metrics_calculator import compute_all_metrics
from backend.engine.unit_normaliser import parse_cell_decimal
from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import (
    MARKET_POSITIONING_SYSTEM,
    MARKET_POSITIONING_USER,
    PEER_CANDIDATES_SYSTEM,
    PEER_CANDIDATES_USER,
    PEER_SELECTION_SYSTEM,
    PEER_SELECTION_USER,
    PeerCandidatesOut,
    PeerSelectionOut,
    PositioningOut,
)
from backend.schemas.common import ActorBadge, CurrencyUnit, EvidenceCitation, StageId, StageStatus
from backend.schemas.financials import LineItem
from backend.schemas.intelligence import PeerCompRow
from backend.schemas.state import ResolvedEntity
from backend.sources import SourceError
from backend.sources.pdf_extractor import extract_raw_table
from backend.sources.sec_edgar import extract_annual_line_items

PEER_COLUMNS = ("rev_growth_pct", "ebitda_margin_pct", "pe_multiple", "roe_pct")


def pe_multiple(close_price: Optional[Decimal], diluted_eps: Optional[Decimal]) -> Optional[Decimal]:
    """Close / trailing diluted EPS; None when EPS <= 0 or either is missing."""
    if close_price is None or diluted_eps is None or diluted_eps <= 0:
        return None
    return safe_div(close_price, diluted_eps)[0]


def format_peer_cells(row: PeerCompRow) -> dict[str, str]:
    return {
        "rev_growth_pct": format_pct(row.rev_growth_pct),
        "ebitda_margin_pct": format_pct(row.ebitda_margin_pct),
        "pe_multiple": format_multiple(row.pe_multiple, 1) if row.pe_multiple is not None else "N/M",
        "roe_pct": format_pct(row.roe_pct),
    }


def peer_row_from_items(
    company_name: str, ticker: str, raw_items: list[LineItem], *, is_target: bool = False,
    close_price: Optional[Decimal] = None, diluted_eps: Optional[Decimal] = None, price_as_of: Optional[str] = None,
    evidence_ids: Optional[list[str]] = None,
) -> PeerCompRow:
    """Run the standard engines on a peer's raw line items and take the latest-period values."""
    derived, _ = compute_derived_fields(raw_items)
    ledger = compute_all_metrics(raw_items + derived)
    latest = ledger[-1].fiscal_period if ledger else None
    by_key = {r.metric_key: r for r in ledger if r.fiscal_period == latest}

    def val(key: str) -> Optional[Decimal]:
        r = by_key.get(key)
        return r.value if r is not None else None

    row = PeerCompRow(
        company_name=company_name, ticker=ticker, is_target=is_target,
        rev_growth_pct=val("rev_yoy"), ebitda_margin_pct=val("ebitda_margin"),
        pe_multiple=pe_multiple(close_price, diluted_eps), price_as_of=price_as_of, roe_pct=val("roe"),
        evidence_ids=evidence_ids or sorted({li.evidence.evidence_id for li in raw_items}),
    )
    return row.model_copy(update={"formatted": format_peer_cells(row)})


def compute_peer_medians(rows: list[PeerCompRow]) -> PeerCompRow:
    """Median row over the peers only (the target is excluded)."""
    peers = [r for r in rows if not r.is_target and not r.is_median_row]
    row = PeerCompRow(
        company_name="Peer median", ticker="MEDIAN", is_median_row=True,
        rev_growth_pct=median(r.rev_growth_pct for r in peers),
        ebitda_margin_pct=median(r.ebitda_margin_pct for r in peers),
        pe_multiple=median(r.pe_multiple for r in peers),
        roe_pct=median(r.roe_pct for r in peers),
    )
    cells = format_peer_cells(row)
    if row.pe_multiple is None:
        cells["pe_multiple"] = MISSING
    return row.model_copy(update={"formatted": cells})


# ---------------------------------------------------------------- live stage

S = StageId.S09_MARKET
C, L = ActorBadge.CODE, ActorBadge.LLM
MAX_PEERS = 4
GUARDS = ["Peer names from the model are looked up in the BSE and SEC registries; unknown names are dropped",
          "The model may select only registry-matched candidates; any other ticker is discarded",
          "Peer metrics come from the same extraction and metrics engines as the target; medians are Decimal code",
          "P/E is close / trailing diluted EPS, N/M when EPS is zero or negative or no close price is available",
          "Positioning prose may only quote cells from the peer table with their citation tokens"]


def xbrl_diluted_eps(facts: dict[str, Any]) -> Optional[Decimal]:
    """Latest full-year us-gaap:EarningsPerShareDiluted from a 10-K."""
    rows = facts.get("facts", {}).get("us-gaap", {}).get("EarningsPerShareDiluted", {}).get("units", {}).get("USD/shares", [])
    annual = [r for r in rows if r.get("form") in ("10-K", "10-K/A") and r.get("start")
              and (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days >= 350]
    if not annual:
        return None
    best = max(annual, key=lambda r: (r["end"], r.get("filed", "")))
    return Decimal(str(best["val"]))


def diluted_eps_from_pnl(path: str, page: int) -> Optional[Decimal]:
    """Code parse of the printed 'Diluted' EPS row (current-year column) on the P&L page."""
    table = extract_raw_table(path, page)
    for row in table.grid:
        if re.search(r"\bdiluted\b", row[0], re.I) and len(row) > 1 and row[1]:
            return parse_cell_decimal(row[1])
    return None


async def resolve_candidates(ctx: RunContext, entity: ResolvedEntity, names: list[str], calls: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen = {(entity.ticker or "").upper()}
    for name in names[:8]:
        t = Timer()
        hit = None
        try:
            if entity.jurisdiction == "IN" and ctx.india is not None:
                m = (await ctx.india.search_scrips(name))[:1]
                hit = {"ticker": m[0].symbol, "company_name": m[0].name, "scrip_code": m[0].scrip_code,
                       "jurisdiction": "IN", "industry": m[0].industry} if m else None
            elif ctx.sec is not None:
                m = (await ctx.sec.search_tickers(name))[:1]
                hit = {"ticker": m[0].ticker, "company_name": m[0].title, "cik": m[0].cik, "jurisdiction": "US"} if m else None
        except SourceError as exc:
            calls.append(trace(ctx, "lookup_peer_in_registry", C, {"name": name}, t.ms, str(exc), "error"))
            continue
        calls.append(trace(ctx, "lookup_peer_in_registry", C, {"name": name}, t.ms,
                           f"{hit['company_name']} ({hit['ticker']})" if hit else "not found",
                           "ok" if hit else "fallback"))
        if hit and hit["ticker"].upper() not in seen:
            seen.add(hit["ticker"].upper())
            out.append(hit)
    return out


async def peer_raw_items(ctx: RunContext, peer: dict[str, Any], calls: list, gaps: list[str]
                         ) -> tuple[list[LineItem], Optional[Decimal]]:
    """fetch_peer_filing_metrics: the latest annual filing through the same Stage 3-4 code."""
    from backend.pipeline.stages import s03_filing_retrieval as s03
    from backend.pipeline.stages import s04_statement_extraction as s04

    name = peer["company_name"]
    if peer["jurisdiction"] == "US":
        t = Timer()
        try:
            facts = await ctx.sec.fetch_company_facts(peer["cik"])
        except SourceError as exc:
            calls.append(trace(ctx, "fetch_peer_filing_metrics", C, {"ticker": peer["ticker"]}, t.ms, str(exc), "error"))
            gaps.append(f"Peer {name}: SEC companyfacts unavailable")
            return [], None
        xs, _ = extract_annual_line_items(facts, 2)
        slug = re.sub(r"[^a-z0-9]+", "", peer["ticker"].lower())
        calls.append(trace(ctx, "fetch_peer_filing_metrics", C, {"ticker": peer["ticker"]}, t.ms, f"{len(xs)} XBRL facts"))
        return [s04.xbrl_to_line_item(x, slug) for x in xs], xbrl_diluted_eps(facts)

    child = RunContext(settings=ctx.settings, llm=ctx.llm, emitter=ctx.emitter, india=ctx.india, download=ctx.download,
                       work_dir=ctx.work_dir / f"peer_{peer['ticker'].lower()}",
                       scratch={"max_annual_reports": 1, "max_quarterly": 0, "skip_segments": True})
    child.call_seq = ctx.call_seq
    ent = ResolvedEntity(query=name, legal_name=name, ticker=peer["ticker"], exchange="BSE", jurisdiction="IN",
                         is_listed=True, bse_scrip_code=peer["scrip_code"], sector=peer.get("industry", ""),
                         headquarters="", fy_end_month="March", currency=CurrencyUnit.INR_CR)
    r3 = await s03.run(child, {"entity": ent})
    raw: list[LineItem] = []
    if child.scratch.get("annual_reports"):
        raw, *_ = await s04._india(child, ent)
    ctx.call_seq = child.call_seq
    eps = None
    pnl = child.scratch.get("pnl_pages", {})
    if pnl:
        path, page = pnl[max(pnl, key=lambda fy: int(fy[2:]))]
        eps = diluted_eps_from_pnl(path, page)
    calls.append(trace(ctx, "fetch_peer_filing_metrics", C, {"ticker": peer["ticker"]}, 0,
                       f"{len(raw)} line items from {len(child.scratch.get('annual_reports', []))} annual report(s)",
                       "ok" if raw else ("error" if r3.status == StageStatus.FAILED else "fallback")))
    if not raw:
        gaps.append(f"Peer {name}: no audited statements could be extracted")
    return raw, eps


async def _price(ctx: RunContext, row: dict[str, Any], calls: list) -> tuple[Optional[Decimal], Optional[str]]:
    if ctx.close_price is None:
        return None, None
    t = Timer()
    try:
        got = await ctx.close_price(**row)
    except SourceError as exc:
        calls.append(trace(ctx, "fetch_close_price", C, {"ticker": row.get("ticker")}, t.ms, str(exc), "error"))
        return None, None
    if not got:
        calls.append(trace(ctx, "fetch_close_price", C, {"ticker": row.get("ticker")}, t.ms, "no close", "fallback"))
        return None, None
    price, as_of = got
    calls.append(trace(ctx, "fetch_close_price", C, {"ticker": row.get("ticker")}, t.ms, f"{price} as of {as_of}"))
    return price, as_of


def peer_table_json(rows: list[PeerCompRow]) -> list[dict[str, Any]]:
    from backend.engine.verification_gate import peer_token_id

    return [{"company": r.company_name, "ticker": r.ticker, "is_target": r.is_target, "is_median": r.is_median_row,
             "cells": {c: {"formatted_value": r.formatted.get(c, MISSING), "token": f"[[{peer_token_id(r, c)}]]"}
                       for c in PEER_COLUMNS}} for r in rows]


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    calls, gaps, actions = [], [], []
    target_raw = [li for li in state.get("line_items", []) if not li.is_derived and li.field_key != "segment_revenue"]
    t = Timer()
    names: list[str] = []
    try:
        res = await ctx.llm.structured(stage="s09_candidates", schema=PeerCandidatesOut,
                                       system=PEER_CANDIDATES_SYSTEM.format(legal_name=entity.legal_name, sector=entity.sector,
                                                                            jurisdiction=entity.jurisdiction),
                                       user=PEER_CANDIDATES_USER.format(entity_json=entity.model_dump_json()))
        names = res.parsed.names
        calls.append(trace(ctx, "propose_peer_names", L, {"sector": entity.sector}, res.duration_ms, names))
    except LLMError as exc:
        calls.append(trace(ctx, "propose_peer_names", L, {"sector": entity.sector}, t.ms, str(exc), "error"))
    candidates = await resolve_candidates(ctx, entity, names, calls)

    chosen: list[dict[str, Any]] = []
    if candidates:
        t = Timer()
        try:
            res = await ctx.llm.structured(stage="s09_select", schema=PeerSelectionOut,
                                           system=PEER_SELECTION_SYSTEM.format(legal_name=entity.legal_name, sector=entity.sector,
                                                                               jurisdiction=entity.jurisdiction),
                                           user=PEER_SELECTION_USER.format(candidates_json=json.dumps(candidates)))
            by_ticker = {c["ticker"].upper(): c for c in candidates}
            for pick in res.parsed.peers:
                c = by_ticker.get(pick.ticker.upper())
                if c and c not in chosen:
                    chosen.append(c)
            calls.append(trace(ctx, "select_sector_peers", L, {"candidates": len(candidates)}, res.duration_ms,
                               [f"{p.ticker}: {p.reason}" for p in res.parsed.peers]))
        except LLMError as exc:
            calls.append(trace(ctx, "select_sector_peers", L, {"candidates": len(candidates)}, t.ms, str(exc), "error"))
            chosen = candidates[:MAX_PEERS]
    chosen = chosen[:MAX_PEERS]
    actions.append((L, "Proposed competitor names; code matched them to exchange or SEC registry records"))
    actions.append((L, f"Selected {len(chosen)} peer(s) from the registry-matched candidates"))

    registry: dict[str, EvidenceCitation] = {}
    rows: list[PeerCompRow] = []
    for peer in chosen:
        raw, eps = await peer_raw_items(ctx, peer, calls, gaps)
        if not raw:
            continue
        price, as_of = await _price(ctx, peer, calls)
        row = peer_row_from_items(peer["company_name"], peer["ticker"], raw, close_price=price, diluted_eps=eps,
                                  price_as_of=as_of)
        rows.append(row)
        registry.update({li.evidence.evidence_id: li.evidence for li in raw})

    target_eps = None
    if entity.jurisdiction == "US" and ctx.scratch.get("xbrl_facts"):
        target_eps = xbrl_diluted_eps(ctx.scratch["xbrl_facts"])
    elif ctx.scratch.get("pnl_pages"):
        pnl = ctx.scratch["pnl_pages"]
        target_eps = diluted_eps_from_pnl(*pnl[max(pnl, key=lambda fy: int(fy[2:]))])
    price, as_of = await _price(ctx, {"ticker": entity.ticker, "company_name": entity.legal_name, "jurisdiction":
                                      entity.jurisdiction, "cik": entity.cik, "scrip_code": entity.bse_scrip_code}, calls)
    target = peer_row_from_items(entity.legal_name, f"{entity.exchange}: {entity.ticker}" if entity.exchange else
                                 (entity.ticker or entity.legal_name), target_raw, is_target=True, close_price=price,
                                 diluted_eps=target_eps, price_as_of=as_of)
    if not rows:
        gaps.append("No listed peers could be benchmarked from filings")
        p = panel(S, [entity.legal_name], calls, actions, "No peers benchmarked",
                  "Without peer filings no medians are computed.", GUARDS)
        return StageResult(StageStatus.WARNING, "No peers benchmarked",
                           {"peer_comps": [target], "market_advantages": [], "market_disadvantages": [],
                            "market_positioning_prose": None, "stated_gaps": gaps}, p)
    if all(r.pe_multiple is None for r in rows + [target]):
        gaps.append("P/E unavailable: no closing price or positive diluted EPS for the peer set")
    median_row = compute_peer_medians(rows)
    table = [target, *rows, median_row]
    calls.append(trace(ctx, "compute_peer_medians", C, {"peers": len(rows)}, 0, median_row.formatted))
    actions.append((C, f"Ran the statement and metrics engines for {len(rows)} peer(s) and took Decimal medians"))

    advantages: list[str] = []
    disadvantages: list[str] = []
    prose = None
    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s09_positioning", schema=PositioningOut,
                                       system=MARKET_POSITIONING_SYSTEM.format(legal_name=entity.legal_name),
                                       user=MARKET_POSITIONING_USER.format(peer_table_json=json.dumps(peer_table_json(table))))
        advantages, disadvantages = res.parsed.market_advantages[:4], res.parsed.market_disadvantages[:4]
        prose = res.parsed.market_positioning_prose
        calls.append(trace(ctx, "draft_positioning_summary", L, {"rows": len(table)}, res.duration_ms, prose or ""))
        actions.append((L, "Wrote advantages, disadvantages and positioning prose quoting table cells with tokens"))
    except LLMError as exc:
        calls.append(trace(ctx, "draft_positioning_summary", L, {"rows": len(table)}, t.ms, str(exc), "error"))
        gaps.append("Market positioning summary unavailable: model call failed")
    p = panel(S, [f"{entity.legal_name} ({entity.sector})"], calls, actions,
              f"{len(rows)} peer(s): {', '.join(r.company_name for r in rows)}",
              "Peers are benchmarked on audited filings run through the same engines; market share is not computed.", GUARDS)
    patch: dict[str, Any] = {"peer_comps": table, "market_advantages": advantages, "market_disadvantages": disadvantages,
                             "market_positioning_prose": prose, "evidence_registry": registry}
    if gaps:
        patch["stated_gaps"] = gaps
    status = StageStatus.COMPLETED if len(rows) >= 2 and prose else StageStatus.WARNING
    return StageResult(status, f"Benchmarked against {len(rows)} peer(s); medians computed in code", patch, p)
