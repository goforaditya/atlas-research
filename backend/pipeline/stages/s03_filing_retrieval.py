"""Stage 3: fetch T1/T2 filings, reject aggregator hosts, and collect the business overview and KMP roster.

No LLM call decides anything here. The only model call is the roster fallback, which may copy names and roles
from the filing text; code drops any row whose name or role is not printed verbatim in that text.
"""
from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime
from typing import Any, Optional

import pymupdf as fitz

from backend.pipeline.graph import RunContext, StageResult, Timer, from_fetch, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import MANAGEMENT_COPY_SYSTEM, MANAGEMENT_COPY_USER, ManagementCopyOut
from backend.schemas.common import ActorBadge, EvidenceCitation, SourceTier, StageId, StageStatus
from backend.schemas.intelligence import ManagementPerson
from backend.schemas.state import ResolvedEntity
from backend.sources import TIER_WEIGHTS, SourceError, sha256_bytes, verify_document_authenticity
from backend.sources.sec_edgar import annual_period_ends, fiscal_label, recent_filings

S = StageId.S03_RETRIEVE
C, L = ActorBadge.CODE, ActorBadge.LLM
MAX_ANNUAL_REPORTS = 3
MAX_QUARTERLY = 5
GUARDS = ["HTTP 429/503: exponential backoff 1 s, 2 s, 4 s, then the source is skipped with a stated gap",
          "Hosts outside the T1/T2 whitelist (sec.gov, bseindia.com, nseindia.com, sebi.gov.in, rbi.org.in, "
          "mca.gov.in, company IR) are discarded; aggregators never supply figures",
          "Fewer than 3 audited years: a formal stated gap is added and the run continues",
          "Roster fallback: the model may only copy strings; rows not printed verbatim in the filing are dropped"]

ROLE_WORDS = re.compile(r"\b(director|officer|chief|ceo|cfo|coo|cto|president|chair(man|person)?|secretary|"
                        r"treasurer|founder|head|vice president|controller)\b", re.I)
HONORIFIC = re.compile(r"^(mr|ms|mrs|dr|shri|smt)\.?\s+", re.I)
NAME = re.compile(r"^[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,4}$")
KMP_HEADING = re.compile(r"key managerial personnel|board of directors\s*(&|and)\s*kmp|executive officers of the registrant|"
                         r"information about our executive officers|executive officers", re.I)
BUSINESS_HEADING = re.compile(r"business overview|corporate overview|company overview|about (the company|us)", re.I)


def slug_for(entity: ResolvedEntity) -> str:
    base = entity.ticker or entity.legal_name.split()[0]
    return re.sub(r"[^a-z0-9]+", "", base.lower()) or "co"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def first_sentences(text: str, n: int = 2, limit: int = 600) -> str:
    text = " ".join(text.split())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return " ".join(parts[:n])[:limit].strip()


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def html_table_rows(raw: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", raw):
        cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                 for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)
    return rows


def parse_roster_rows(rows: list[list[str]]) -> list[tuple[str, str]]:
    """Code parse of a KMP / executive-officer table: a person-name cell plus a cell with a role word.
    An age column (10-K Item 10 tables) is skipped."""
    out: list[tuple[str, str]] = []
    for cells in rows:
        cells = [c for c in cells if not re.fullmatch(r"\d{2}", c)]
        if len(cells) < 2:
            continue
        name = HONORIFIC.sub("", cells[0]).strip()
        role = next((c for c in cells[1:] if ROLE_WORDS.search(c)), None)
        if role and NAME.match(name) and not ROLE_WORDS.search(name):
            out.append((name, role.strip()))
    seen: set[str] = set()
    return [(n, r) for n, r in out if not (n in seen or seen.add(n))]


def parse_roster_lines(text: str) -> list[tuple[str, str]]:
    """'Name – Role', 'Name, Role' or 'Name: Role' lines (annual-report KMP lists)."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^\s*(.+?)\s*(?:[,:–—]|\s-\s)\s*(.+?)\s*$", line)
        if m:
            rows.append([m.group(1), m.group(2)])
    return parse_roster_rows(rows)


def section_after(text: str, heading: re.Pattern[str], length: int = 4000) -> Optional[str]:
    """Text after the last heading match (the first is usually the table of contents)."""
    matches = list(heading.finditer(text))
    if not matches:
        return None
    start = matches[-1].end() if len(matches) > 1 else matches[0].end()
    return text[start:start + length]


def evidence(ev_id: str, doc_id: str, title: str, url: str, tier: SourceTier, snippet: str, *,
             page: Optional[int] = None, raw: Optional[str] = None) -> EvidenceCitation:
    return EvidenceCitation(evidence_id=ev_id, document_id=doc_id, document_title=title, source_url=url, source_tier=tier,
                            trust_weight=TIER_WEIGHTS[tier], page_number=page, verbatim_snippet=snippet,
                            extracted_value_raw=raw if raw is not None else snippet, retrieved_at=now_iso())


async def copy_roster_with_llm(ctx: RunContext, text: str, calls: list) -> list[tuple[str, str, Optional[str]]]:
    t = Timer()
    try:
        res = await ctx.llm.structured(stage="s03_roster", system=MANAGEMENT_COPY_SYSTEM,
                                       user=MANAGEMENT_COPY_USER.format(text=text[:6000]), schema=ManagementCopyOut)
    except LLMError as exc:
        calls.append(trace(ctx, "copy_management_roster", L, {"chars": len(text)}, t.ms, str(exc), "error"))
        return []
    flat = " ".join(text.split())
    kept = [(p.name.strip(), p.role.strip(), p.since) for p in res.parsed.people
            if p.name.strip() and " ".join(p.name.split()) in flat and " ".join(p.role.split()) in flat]
    dropped = len(res.parsed.people) - len(kept)
    calls.append(trace(ctx, "copy_management_roster", L, {"chars": len(text)}, res.duration_ms,
                       f"{len(kept)} rows kept, {dropped} dropped (not printed verbatim)"))
    return kept


def quarter_label(period_end: date, jurisdiction: str) -> str:
    """Indian fiscal quarters: Apr–Jun is Q1 of the FY ending the next March."""
    if jurisdiction == "IN":
        q = {6: 1, 9: 2, 12: 3, 3: 4}[period_end.month]
        fy = period_end.year + (1 if period_end.month >= 4 else 0)
        return f"Q{q}FY{str(fy)[2:]}"
    q = (period_end.month - 1) // 3 + 1
    return f"Q{q}FY{period_end.year}"


def quarter_end_before(d: date) -> date:
    ends = [date(d.year - 1, 12, 31), date(d.year, 3, 31), date(d.year, 6, 30), date(d.year, 9, 30), date(d.year, 12, 31)]
    return max(e for e in ends if e < d)


async def _download(ctx: RunContext, url: str, calls: list, tool: str) -> Optional[bytes]:
    if ctx.download is None:
        calls.append(trace(ctx, tool, C, {"url": url}, 0, "No downloader configured", "fallback"))
        return None
    t = Timer()
    try:
        data = await ctx.download(url)
    except SourceError as exc:
        calls.append(trace(ctx, tool, C, {"url": url}, t.ms, str(exc), "error"))
        return None
    calls.append(trace(ctx, tool, C, {"url": url}, t.ms, f"{len(data):,} bytes, sha256 {sha256_bytes(data)[:16]}…"))
    return data


async def _us(ctx: RunContext, entity: ResolvedEntity) -> StageResult:
    calls, gaps, actions = [], [], []
    registry: dict[str, EvidenceCitation] = {}
    roster: list[ManagementPerson] = []
    slug = slug_for(entity)
    t = Timer()
    try:
        subs = await ctx.sec.fetch_submissions(entity.cik)
        facts = await ctx.sec.fetch_company_facts(entity.cik)
    except SourceError as exc:
        calls.append(trace(ctx, "fetch_sec_10k_and_10q", C, {"cik": entity.cik, "years": 5}, t.ms, str(exc), "error"))
        p = panel(S, [f"CIK {entity.cik}"], calls, [(C, "SEC EDGAR request failed after retries")],
                  "Halted: no T1 filings", "Statements cannot be extracted without the filings.", GUARDS)
        return StageResult(StageStatus.FAILED, "SEC EDGAR unavailable", {}, p)
    filings = recent_filings(subs, limit=60)
    ctx.scratch["xbrl_facts"] = facts
    ctx.scratch["sec_filings"] = filings
    ends = annual_period_ends(facts, 5)
    years = [fiscal_label(e) for e in ends]
    calls.append(trace(ctx, "fetch_sec_10k_and_10q", C, {"cik": entity.cik, "years": 5}, t.ms,
                       f"{len(filings)} filings; annual periods {', '.join(years)}"))

    tenks = [f for f in filings if f["form"] == "10-K"][:5]
    for f in tenks:
        check = verify_document_authenticity(f["url"])
        calls.append(trace(ctx, "verify_document_authenticity", C, {"url": f["url"]}, 0, check.reason,
                           "ok" if check.accepted else "fallback"))
        fy = fiscal_label(f["report_date"]) if f.get("report_date") else f["filing_date"][:4]
        ev_id = f"ev_{slug}_10k_{fy.lower()}"
        registry[ev_id] = evidence(ev_id, f"sec_{f['accession']}", f"10-K {fy}", f["url"], SourceTier.T1,
                                   f"Form 10-K for the fiscal year ended {f.get('report_date') or '—'}, filed {f['filing_date']}",
                                   raw=f["accession"])
    ctx.scratch["filing_events"] = [
        {"title": f"Form 8-K items {f['items']}" if f.get("items") else "Form 8-K", "date": f["filing_date"],
         "url": f["url"], "tier": "T2", "category": "8-K"} for f in filings if f["form"] == "8-K"]
    actions.append((C, f"Pulled XBRL companyfacts and {len(tenks)} Form 10-K filing(s) from sec.gov"))

    if tenks:
        latest = tenks[0]
        raw = await _download(ctx, latest["url"], calls, "download_latest_10k")
        if raw is not None:
            text_html = raw.decode("utf-8", errors="replace")
            text = html_to_text(text_html)
            doc_id, title = f"sec_{latest['accession']}", f"10-K {years[-1] if years else ''}".strip()
            business = section_after(text, re.compile(r"item\s*1\.?\s*business", re.I))
            if business:
                snippet = first_sentences(business)
                ev_id = f"ev_{slug}_business"
                registry[ev_id] = evidence(ev_id, doc_id, title, latest["url"], SourceTier.T1, snippet)
                actions.append((C, "Copied the Item 1 business overview opening as evidence"))
            people = parse_roster_rows(html_table_rows(text_html[_kmp_offset(text_html):][:60000]))
            source_text = section_after(text, KMP_HEADING, 5000) or ""
            roster_rows = [(n, r, None) for n, r in people]
            if not roster_rows and source_text:
                roster_rows = await copy_roster_with_llm(ctx, source_text, calls)
            if roster_rows:
                ev_id = f"ev_{slug}_kmp"
                registry[ev_id] = evidence(ev_id, doc_id, title, latest["url"], SourceTier.T1,
                                           "; ".join(f"{n} – {r}" for n, r, _ in roster_rows[:8]))
                roster = [ManagementPerson(name=n, role=r, since=s, evidence_id=ev_id) for n, r, s in roster_rows[:12]]
                actions.append((C, f"Parsed {len(roster)} executive officer(s) from the 10-K"))
    if not roster:
        gaps.append("Executive officer list not found in the latest 10-K (it may be incorporated from the proxy)")
    ctx.scratch["quarterly_source"] = "xbrl"
    return _finish(ctx, entity, years, registry, roster, calls, actions, gaps, len(tenks) + 2)


def _kmp_offset(raw_html: str) -> int:
    matches = list(KMP_HEADING.finditer(raw_html))
    return matches[-1].start() if matches else len(raw_html)


async def _india(ctx: RunContext, entity: ResolvedEntity, today: Optional[date]) -> StageResult:
    calls, actions = [], []
    registry: dict[str, EvidenceCitation] = {}
    roster: list[ManagementPerson] = []
    slug = slug_for(entity)
    docs, fetch_traces, gaps = await ctx.india.fetch_filings(entity.bse_scrip_code, entity.ticker, years=5, today=today)
    calls += [from_fetch(ctx, ft) for ft in fetch_traces]
    ir_domains = tuple(ctx.scratch.get("ir_domains", ()))

    accepted, rejected = [], 0
    for d in docs:
        check = verify_document_authenticity(d.url, ir_domains=ir_domains)
        if check.accepted:
            accepted.append(d)
        else:
            rejected += 1
            calls.append(trace(ctx, "verify_document_authenticity", C, {"url": d.url}, 0, check.reason, "fallback"))
    annual = sorted([d for d in accepted if d.category == "annual_report" and d.fiscal_year],
                    key=lambda d: d.fiscal_year)[-MAX_ANNUAL_REPORTS:]
    results = sorted([d for d in accepted if d.category == "results"], key=lambda d: d.filed_on)[-MAX_QUARTERLY:]
    ctx.scratch["filing_events"] = [{"title": d.title, "date": d.filed_on, "url": d.url, "tier": d.tier.value,
                                     "category": d.category} for d in accepted if d.category != "annual_report"]
    actions.append((C, f"Fetched {len(docs)} BSE/NSE document(s); {rejected} rejected by host check"))

    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for d in annual:
        data = await _download(ctx, d.url, calls, "download_annual_report")
        if data is None:
            continue
        check = verify_document_authenticity(d.url, data, ir_domains)
        path = ctx.work_dir / f"ar_{d.fiscal_year.lower()}.pdf"
        path.write_bytes(data)
        reports.append({"fiscal_year": d.fiscal_year, "path": str(path), "url": d.url, "sha256": check.sha256,
                        "doc_id": f"{slug}_ar_{d.fiscal_year.lower()}", "title": f"AR {d.fiscal_year}",
                        "tier": (check.tier or SourceTier.T1).value})
    ctx.scratch["annual_reports"] = reports
    quarterly = []
    for d in results:
        data = await _download(ctx, d.url, calls, "download_quarterly_results")
        if data is None:
            continue
        label = quarter_label(quarter_end_before(date.fromisoformat(d.filed_on)), "IN")
        path = ctx.work_dir / f"qr_{label.lower()}.pdf"
        path.write_bytes(data)
        quarterly.append({"label": label, "path": str(path), "url": d.url, "doc_id": f"{slug}_qr_{label.lower()}",
                          "title": f"Results {label}", "tier": d.tier.value, "filed_on": d.filed_on})
    ctx.scratch["quarterly_reports"] = quarterly
    ctx.scratch["quarterly_source"] = "pdf"
    if reports:
        actions.append((C, f"Downloaded {len(reports)} annual report PDF(s) and stored their SHA-256"))

    years: list[str] = []
    for r in reports:
        n = int(r["fiscal_year"][2:])
        years += [f"FY{n - 1:02d}", r["fiscal_year"]]
    years = sorted(set(years), key=lambda y: int(y[2:]))

    if reports:
        latest = reports[-1]
        business, kmp = _scan_annual_report(latest["path"])
        if business:
            page, snippet = business
            ev_id = f"ev_{slug}_business"
            registry[ev_id] = evidence(ev_id, latest["doc_id"], latest["title"], latest["url"], SourceTier.T1, snippet, page=page)
            actions.append((C, f"Copied the business overview opening from {latest['title']} p.{page}"))
        if kmp:
            page, text = kmp
            rows = [(n, r, None) for n, r in parse_roster_lines(text)]
            if not rows:
                rows = await copy_roster_with_llm(ctx, text, calls)
            if rows:
                ev_id = f"ev_{slug}_kmp"
                registry[ev_id] = evidence(ev_id, latest["doc_id"], latest["title"], latest["url"], SourceTier.T1,
                                           "; ".join(f"{n} – {r}" for n, r, _ in rows[:8]), page=page)
                roster = [ManagementPerson(name=n, role=r, since=s, evidence_id=ev_id) for n, r, s in rows[:12]]
                actions.append((C, f"Parsed {len(roster)} key managerial personnel from p.{page}"))
    if not roster:
        gaps.append("Key managerial personnel list not found in the latest annual report")
    return _finish(ctx, entity, years, registry, roster, calls, actions, gaps, len(accepted))


def _scan_annual_report(path: str) -> tuple[Optional[tuple[int, str]], Optional[tuple[int, str]]]:
    business = kmp = None
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            if business is None and (m := BUSINESS_HEADING.search(text)):
                snippet = first_sentences(text[m.end():])
                if len(snippet) > 40:
                    business = (i, snippet)
            if kmp is None and (m := KMP_HEADING.search(text)):
                kmp = (i, text[m.end():m.end() + 4000])
            if business and kmp:
                break
    return business, kmp


def _finish(ctx: RunContext, entity: ResolvedEntity, years: list[str], registry: dict[str, EvidenceCitation],
            roster: list[ManagementPerson], calls: list, actions: list, gaps: list[str], sources_read: int) -> StageResult:
    audited = len(years)
    if audited < 3 and not any("fewer than 3" in g for g in gaps):
        gaps.append(f"Only {audited} audited fiscal year(s) available; fewer than 3 years of statements")
    ctx.scratch["stage3_gaps"] = gaps
    patch = {"fiscal_years": years, "sources_read_count": sources_read, "evidence_registry": registry,
             "management_roster": roster}
    if gaps:
        patch["stated_gaps"] = gaps
    status = StageStatus.COMPLETED if audited >= 3 and not any("unavailable" in g for g in gaps) else StageStatus.WARNING
    if audited == 0:
        status = StageStatus.FAILED
    p = panel(S, [f"{entity.legal_name} ({entity.exchange or entity.jurisdiction}: {entity.ticker or '—'})"], calls, actions,
              f"{audited} fiscal year(s): {', '.join(years) or 'none'}",
              "Only regulator and exchange hosts supply statement documents; each download is hashed.", GUARDS)
    log = f"Retrieved {sources_read} T1/T2 source(s); {audited} fiscal years; {len(roster)} KMP"
    return StageResult(status, log, patch, p)


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    if entity.jurisdiction == "US" and entity.cik and ctx.sec is not None:
        return await _us(ctx, entity)
    if entity.jurisdiction == "IN" and entity.bse_scrip_code and ctx.india is not None:
        return await _india(ctx, entity, state.get("today"))
    p = panel(S, [entity.legal_name], [], [(C, "No filing source covers this entity")], "Halted: no filing source",
              "Live runs cover SEC filers and BSE-listed companies only.", GUARDS)
    return StageResult(StageStatus.FAILED, "No T1 filing source for this entity", {}, p)
