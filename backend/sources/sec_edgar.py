"""US Tier-1 fetcher: SEC EDGAR ticker map, submissions and XBRL companyfacts (Section 7.1).

Line items come from companyfacts; this module only selects facts and converts units. It never estimates a
missing value, and it never fills a Q4 by subtracting nine months from a full year.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

import httpx

from backend.engine.unit_normaliser import normalise_units
from backend.sources import SourceConfigError
from backend.sources.rate_limiter import DomainLimiter, get_with_retry

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"

# RawFieldKey -> us-gaap tags in priority order. The first tag with facts for a period wins.
XBRL_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "other_income": ("NonoperatingIncomeExpense", "OtherNonoperatingIncomeExpense"),
    "finance_cost": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
    "d_and_a": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                "DepreciationAmortizationAndAccretionNet"),
    "pbt": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "tax_expense": ("IncomeTaxExpenseBenefit",),
    "pat_before_nci": ("ProfitLoss", "NetIncomeLoss"),
    "pat": ("NetIncomeLoss",),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",),
    "purchase_of_ppe": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "purchase_of_intangibles": ("PaymentsToAcquireIntangibleAssets", "PaymentsToDevelopSoftware"),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"),
    "non_controlling_interest": ("MinorityInterest",),
    "borrowings_current": ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
    "borrowings_noncurrent": ("LongTermDebtNoncurrent", "LongTermNotesPayable"),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "trade_receivables": ("AccountsReceivableNetCurrent",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
}
QUARTERLY_FIELDS = ("revenue", "operating_income", "d_and_a", "finance_cost", "other_income", "pbt")
INSTANT_FIELDS = {"total_assets", "total_liabilities", "total_equity", "non_controlling_interest", "borrowings_current",
                  "borrowings_noncurrent", "cash_and_equivalents", "trade_receivables", "current_assets",
                  "current_liabilities"}


@dataclass(frozen=True)
class TickerMatch:
    cik: str  # zero-padded to 10 digits
    ticker: str
    title: str


@dataclass(frozen=True)
class XbrlLineItem:
    field_key: str
    fiscal_period: str  # "FY2025" or "Q2FY2025"
    period_end: str
    value: Optional[Decimal]  # USD_M
    raw_value: str  # as reported in the fact, plain USD units
    tag: str
    form: str
    accession: str
    filed: str
    is_restated: bool
    source_url: str


def pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    s = re.sub(r"\b(inc|corp|corporation|co|ltd|limited|plc|holdings|the)\b", " ", s)
    return " ".join(s.split())


class SecEdgarClient:
    def __init__(self, client: httpx.AsyncClient, limiter: DomainLimiter, user_agent: str):
        if not user_agent or "@" not in user_agent:
            raise SourceConfigError("SEC_EDGAR_USER_AGENT must name the app and a monitored contact email")
        self.client, self.limiter = client, limiter
        self.headers = {"User-Agent": user_agent}

    async def _json(self, url: str) -> Any:
        resp = await get_with_retry(self.client, url, self.limiter, headers=self.headers)
        return resp.json()

    async def search_tickers(self, query: str, limit: int = 8) -> list[TickerMatch]:
        """search_sec_edgar_tickers: exact ticker match first, then name containment."""
        data = await self._json(TICKERS_URL)
        rows = [TickerMatch(pad_cik(r["cik_str"]), r["ticker"], r["title"]) for r in data.values()]
        q, nq = query.strip().upper(), _norm(query)
        exact = [r for r in rows if r.ticker.upper() == q]
        named = [r for r in rows if nq and (nq in _norm(r.title) or _norm(r.title) in nq) and r not in exact]
        named.sort(key=lambda r: (_norm(r.title) != nq, len(r.title)))
        return (exact + named)[:limit]

    async def fetch_submissions(self, cik: str) -> dict[str, Any]:
        return await self._json(SUBMISSIONS_URL.format(cik=pad_cik(cik)))

    async def fetch_company_facts(self, cik: str) -> dict[str, Any]:
        return await self._json(FACTS_URL.format(cik=pad_cik(cik)))


def recent_filings(submissions: dict[str, Any], forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"), limit: int = 40) -> list[dict[str, str]]:
    """Flatten submissions.filings.recent into rows with a document URL (fetch_sec_10k_and_10q)."""
    recent = submissions.get("filings", {}).get("recent", {})
    cik_int = int(submissions.get("cik", 0))
    out = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in forms:
            continue
        acc = recent["accessionNumber"][i]
        doc = recent["primaryDocument"][i]
        out.append({
            "form": form, "accession": acc, "filing_date": recent["filingDate"][i],
            "report_date": recent.get("reportDate", [""] * (i + 1))[i], "items": recent.get("items", [""] * (i + 1))[i],
            "url": ARCHIVE_URL.format(cik_int=cik_int, acc=acc.replace("-", ""), doc=doc),
        })
        if len(out) >= limit:
            break
    return out


def _days(f: dict[str, Any]) -> Optional[int]:
    if "start" not in f:
        return None
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def _facts_for(facts: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    return list(node.get("units", {}).get("USD", [])) if node else []


def annual_period_ends(facts: dict[str, Any], years: int = 5) -> list[str]:
    """Fiscal year-end dates, newest last, taken from annual revenue (or assets) facts in 10-K filings."""
    ends: set[str] = set()
    for tag in (*XBRL_TAGS["revenue"], "NetIncomeLoss"):
        for f in _facts_for(facts, tag):
            d = _days(f)
            if f.get("form") == "10-K" and d is not None and 350 <= d <= 380:
                ends.add(f["end"])
        if ends:
            break
    return sorted(ends)[-years:]


def fiscal_label(period_end: str) -> str:
    return f"FY{date.fromisoformat(period_end).year}"


def _pick(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    """Latest filing wins (a restatement supersedes); flag a restatement when filed values differ."""
    candidates = sorted(candidates, key=lambda f: f.get("filed", ""))
    chosen = candidates[-1]
    restated = len({str(f["val"]) for f in candidates}) > 1
    return chosen, restated


def _item(field_key: str, label: str, fact: dict[str, Any], tag: str, restated: bool, cik_int: int) -> XbrlLineItem:
    raw = Decimal(str(fact["val"]))
    acc = fact.get("accn", "")
    return XbrlLineItem(
        field_key=field_key, fiscal_period=label, period_end=fact["end"],
        value=normalise_units(raw, "USD", "USD_M"), raw_value=str(fact["val"]), tag=tag, form=fact.get("form", ""),
        accession=acc, filed=fact.get("filed", ""), is_restated=restated,
        source_url=f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc.replace('-', '')}/" if acc else "",
    )


def extract_annual_line_items(facts: dict[str, Any], years: int = 5) -> tuple[list[XbrlLineItem], list[str]]:
    """Select one fact per (field, fiscal year). Returns items and stated gaps for fields with no tag present."""
    ends = annual_period_ends(facts, years)
    cik_int = int(facts.get("cik", 0))
    items: list[XbrlLineItem] = []
    gaps: list[str] = []
    for field_key, tags in XBRL_TAGS.items():
        found_any = False
        for end in ends:
            for tag in tags:
                pool = [f for f in _facts_for(facts, tag) if f["end"] == end and f.get("form") in ("10-K", "10-K/A")]
                if field_key not in INSTANT_FIELDS:
                    pool = [f for f in pool if (_days(f) or 0) >= 350]
                else:
                    pool = [f for f in pool if "start" not in f]
                if pool:
                    fact, restated = _pick(pool)
                    items.append(_item(field_key, fiscal_label(end), fact, tag, restated, cik_int))
                    found_any = True
                    break
        if not found_any and field_key not in ("operating_income", "non_controlling_interest", "purchase_of_intangibles"):
            gaps.append(f"XBRL companyfacts has no {field_key} fact ({' / '.join(tags[:2])})")
    return items, gaps


def extract_quarterly_line_items(facts: dict[str, Any], quarters: int = 5) -> list[XbrlLineItem]:
    """Three-month facts from 10-Q filings only (Q1–Q3); Q4 is never back-computed."""
    cik_int = int(facts.get("cik", 0))
    by_end: dict[str, dict[str, Any]] = {}
    for f in _facts_for(facts, XBRL_TAGS["revenue"][0]) + _facts_for(facts, XBRL_TAGS["revenue"][1]):
        d = _days(f)
        if f.get("form") == "10-Q" and d is not None and 80 <= d <= 100 and f.get("fp", "").startswith("Q"):
            by_end.setdefault(f["end"], f)
    ends = sorted(by_end)[-quarters:]
    out: list[XbrlLineItem] = []
    for end in ends:
        label = f"{by_end[end]['fp']}FY{by_end[end].get('fy', date.fromisoformat(end).year)}"
        for field_key in QUARTERLY_FIELDS:
            for tag in XBRL_TAGS[field_key]:
                pool = [f for f in _facts_for(facts, tag) if f["end"] == end and 80 <= (_days(f) or 0) <= 100]
                if pool:
                    fact, restated = _pick(pool)
                    out.append(_item(field_key, label, fact, tag, restated, cik_int))
                    break
    return out
