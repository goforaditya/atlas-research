"""Stage 1: resolve the query to one legal entity. Registries are searched in code; the LLM only chooses among
the candidate records, and code rejects any ticker or CIK that is not in them."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import ENTITY_RESOLUTION_SYSTEM, ENTITY_RESOLUTION_USER, EntityResolutionOut
from backend.schemas.common import ActorBadge, CurrencyUnit, StageId, StageStatus
from backend.schemas.state import ResolvedEntity
from backend.sources import SourceError

S = StageId.S01_ENTITY
C, L = ActorBadge.CODE, ActorBadge.LLM
SUFFIXES = re.compile(r"\b(ltd|limited|inc|incorporated|corp|corporation|pvt|private|plc|payments|communications|"
                      r"technologies|holdings)\b\.?", re.I)
GUARDS = ["Zero candidates: retry once with corporate suffixes stripped, then halt with an ambiguity error",
          "The LLM may only choose among registry records; a ticker or CIK not in them is discarded by code",
          "Currency is set by code from the jurisdiction (INR_CR for India, USD_M otherwise)"]


def strip_suffixes(query: str) -> str:
    return " ".join(SUFFIXES.sub(" ", query).split())


async def gather_candidates(ctx: RunContext, query: str) -> tuple[list[dict[str, Any]], list]:
    """search_indian_exchanges + search_sec_edgar_tickers + search_opencorporates_and_mca."""
    cands: list[dict[str, Any]] = []
    calls = []
    if ctx.india is not None:
        t = Timer()
        try:
            hits = await ctx.india.search_scrips(query)
            cands += [{"source": "BSE", "scrip_code": h.scrip_code, "ticker": h.symbol, "name": h.name, "isin": h.isin,
                       "industry": h.industry} for h in hits]
            calls.append(trace(ctx, "search_indian_exchanges", C, {"query": query}, t.ms, [h.name for h in hits]))
        except SourceError as exc:
            calls.append(trace(ctx, "search_indian_exchanges", C, {"query": query}, t.ms, str(exc), "error"))
    if ctx.sec is not None:
        t = Timer()
        try:
            hits = await ctx.sec.search_tickers(query)
            cands += [{"source": "SEC", "cik": h.cik, "ticker": h.ticker, "name": h.title} for h in hits]
            calls.append(trace(ctx, "search_sec_edgar_tickers", C, {"query": query}, t.ms, [h.title for h in hits]))
        except SourceError as exc:
            calls.append(trace(ctx, "search_sec_edgar_tickers", C, {"query": query}, t.ms, str(exc), "error"))
    # OpenCorporates needs an API token and MCA has no public search API; unlisted entities are not resolved live.
    calls.append(trace(ctx, "search_opencorporates_and_mca", C, {"query": query}, 0,
                       "No public registry API configured for unlisted entities", "fallback"))
    return cands, calls


def _match(cands: list[dict[str, Any]], out: EntityResolutionOut) -> Optional[dict[str, Any]]:
    if out.ticker:
        for c in cands:
            if c.get("ticker", "").upper() == out.ticker.upper():
                return c
    name = out.legal_name.lower()
    return next((c for c in cands if c["name"].lower() == name), None)


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    query = state["company_query"]
    cands, calls = await gather_candidates(ctx, query)
    tried = [query]
    if not cands and strip_suffixes(query) and strip_suffixes(query) != query:
        tried.append(strip_suffixes(query))
        more, more_calls = await gather_candidates(ctx, tried[-1])
        cands, calls = more, calls + more_calls
    if not cands:
        p = panel(S, [f"Query: {query}"], calls, [(C, "Searched BSE and SEC registries, then retried without suffixes")],
                  "Halted: no registry candidates", f"No listed entity matched {' or '.join(repr(q) for q in tried)}.", GUARDS)
        return StageResult(StageStatus.FAILED, f"No registry match for {query!r}", {}, p)

    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s01", system=ENTITY_RESOLUTION_SYSTEM,
                                       user=ENTITY_RESOLUTION_USER.format(company_query=query, candidates_json=json.dumps(cands)),
                                       schema=EntityResolutionOut)
    except LLMError as exc:
        calls.append(trace(ctx, "disambiguate_candidates", L, {"candidates": len(cands)}, t.ms, str(exc), "error"))
        p = panel(S, [f"Query: {query}"], calls, [(L, "Candidate disambiguation failed")], "Halted: model unavailable",
                  "The entity cannot be chosen without the model; the run stops rather than guessing.", GUARDS)
        return StageResult(StageStatus.FAILED, "Entity disambiguation failed", {}, p)
    out = res.parsed
    calls.append(trace(ctx, "disambiguate_candidates", L, {"candidates": len(cands)}, res.duration_ms, out))

    match = _match(cands, out)
    notes = []
    if out.ticker and (match is None or match.get("ticker", "").upper() != out.ticker.upper()):
        notes.append(f"Model ticker {out.ticker!r} is not in the registry records and was discarded")
    jurisdiction = {"BSE": "IN", "SEC": "US"}.get(match["source"], out.jurisdiction) if match else out.jurisdiction
    entity = ResolvedEntity(
        query=query, legal_name=out.legal_name,
        ticker=match.get("ticker") if match else None,
        exchange=("BSE" if match["source"] == "BSE" else (out.exchange or "SEC")) if match else None,
        jurisdiction=jurisdiction, is_listed=match is not None,
        cik=match.get("cik") if match else None, bse_scrip_code=match.get("scrip_code") if match else None,
        sector=out.sector or (match or {}).get("industry", ""), headquarters=out.headquarters, fy_end_month=out.fy_end_month,
        currency=CurrencyUnit.INR_CR if jurisdiction == "IN" else CurrencyUnit.USD_M,
        rejected_candidates=out.rejected_candidates,
    )
    actions = [(C, f"Queried exchange and regulator registries ({len(cands)} candidates)"),
               (L, "Chose the listed operating parent among the candidates"),
               (C, "Checked the chosen ticker/CIK against the registry records and set currency from jurisdiction")]
    if notes:
        actions.append((C, notes[0]))
    p = panel(S, [f"Query: {query}"] + ([f"Retried as {tried[-1]!r}"] if len(tried) > 1 else []), calls, actions,
              f"Selected {entity.legal_name}", out.decision_rationale, GUARDS)
    status = StageStatus.COMPLETED if match else StageStatus.WARNING
    log = (f"Matched {entity.legal_name} ({entity.exchange or 'unlisted'}: {entity.ticker or '—'}); "
           f"rejected {len(entity.rejected_candidates)} lookalike(s)")
    return StageResult(status, log, {"entity": entity}, p)
