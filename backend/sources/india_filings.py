"""India Tier-1/2 fetcher: BSE first, NSE only as a secondary source (Section 7.2).

The BSE and NSE JSON endpoints are public but undocumented. Field names below follow the responses these
endpoints return today; the parsers accept the known spellings and skip rows they cannot read rather than
guessing. NSE refuses most non-browser clients with HTTP 403, which is logged as `fallback`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from backend.schemas.common import SourceTier
from backend.sources import FetchTrace, SourceBlocked, SourceError
from backend.sources.rate_limiter import DomainLimiter, get_with_retry

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_SCRIPS_URL = f"{BSE_API}/ListofScripData/w"
BSE_ANNOUNCEMENTS_URL = f"{BSE_API}/AnnSubCategoryGetData/w"
BSE_ANNUAL_REPORTS_URL = f"{BSE_API}/AnnualReport_New/w"
BSE_ATTACH_URL = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{name}"
BSE_AR_URL = "https://www.bseindia.com/bseplus/AnnualReport/{code}/{name}"
NSE_ANNUAL_REPORTS_URL = "https://www.nseindia.com/api/annual-reports"
NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"
BSE_HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com", "Accept": "application/json"}

# Announcement categories that matter for the report, matched on the subject/headline (case-insensitive).
CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("results", re.compile(r"financial results|outcome of board meeting.*result", re.I)),
    ("investor_presentation", re.compile(r"investor presentation|earnings (call|presentation)|analyst", re.I)),
    ("management_change", re.compile(r"resign|appointment|cessation|change in (management|kmp|directors?)|key managerial", re.I)),
    ("acquisition", re.compile(r"acqui|amalgamation|merger|scheme of arrangement", re.I)),
    ("fundraise", re.compile(r"\bqip\b|qualified institutions? placement|preferential|allotment|rights issue", re.I)),
    ("regulatory", re.compile(r"\brbi\b|\bsebi\b|penalty|show cause|order|licen[cs]e|authori[sz]ation", re.I)),
]


@dataclass(frozen=True)
class ScripMatch:
    scrip_code: str
    symbol: str
    name: str
    isin: str
    industry: str


@dataclass(frozen=True)
class FilingDoc:
    source: str  # "BSE" | "NSE"
    title: str
    filed_on: str  # ISO date
    url: str
    category: str
    tier: SourceTier
    fiscal_year: Optional[str] = None  # "FY25" for annual reports


def _first(row: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def categorise(subject: str) -> str:
    for name, pat in CATEGORY_PATTERNS:
        if pat.search(subject or ""):
            return name
    return "other"


def fy_label_from_range(text: str) -> Optional[str]:
    """'2024-2025', '2024-25' or '2025' -> 'FY25' (Indian fiscal year ending March of the later year)."""
    m = re.search(r"(20\d{2})\s*[-–/]\s*(20)?(\d{2})", text or "")
    if m:
        return f"FY{m.group(3)}"
    m = re.search(r"\b(20\d{2})\b", text or "")
    return f"FY{m.group(1)[2:]}" if m else None


def _iso(text: Any) -> str:
    if not text:
        return ""
    s = str(text)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10]


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    s = re.sub(r"\b(ltd|limited|pvt|private|the|inc)\b", " ", s)
    return " ".join(s.split())


class IndiaFilingsClient:
    def __init__(self, client: httpx.AsyncClient, limiter: DomainLimiter):
        self.client, self.limiter = client, limiter

    async def _bse_json(self, url: str, params: dict[str, Any]) -> Any:
        resp = await get_with_retry(self.client, url, self.limiter, params=params, headers=BSE_HEADERS)
        return resp.json()

    async def search_scrips(self, query: str, limit: int = 8) -> list[ScripMatch]:
        """search_indian_exchanges: BSE active equity scrip master, matched on symbol or issuer name."""
        rows = await self._bse_json(BSE_SCRIPS_URL, {"Group": "", "Scripcode": "", "industry": "", "segment": "Equity",
                                                    "status": "Active"})
        q, nq = query.strip().upper(), _norm(query)
        out: list[ScripMatch] = []
        for r in rows if isinstance(rows, list) else rows.get("Table", []):
            m = ScripMatch(str(_first(r, "SCRIP_CD", "scrip_cd") or ""), str(_first(r, "scrip_id", "SCRIP_ID") or ""),
                           str(_first(r, "Issuer_Name", "Scrip_Name", "SCRIP_NAME") or ""),
                           str(_first(r, "ISIN_NUMBER", "ISIN") or ""), str(_first(r, "INDUSTRY", "Industry") or ""))
            if not m.scrip_code:
                continue
            if m.symbol.upper() == q or (nq and (nq in _norm(m.name) or _norm(m.name) in nq)):
                out.append(m)
        out.sort(key=lambda m: (m.symbol.upper() != q, _norm(m.name) != nq, len(m.name)))
        return out[:limit]

    async def bse_announcements(self, scrip_code: str, days: int = 365, today: Optional[date] = None) -> list[FilingDoc]:
        end = today or date.today()
        start = end - timedelta(days=days)
        data = await self._bse_json(BSE_ANNOUNCEMENTS_URL, {
            "pageno": 1, "strCat": -1, "strPrevDate": start.strftime("%Y%m%d"), "strScrip": scrip_code,
            "strSearch": "P", "strToDate": end.strftime("%Y%m%d"), "strType": "C", "subcategory": -1,
        })
        docs = []
        for r in data.get("Table", []) if isinstance(data, dict) else []:
            subject = str(_first(r, "NEWSSUB", "HEADLINE") or "")
            attach = _first(r, "ATTACHMENTNAME")
            if not subject:
                continue
            docs.append(FilingDoc("BSE", subject, _iso(_first(r, "NEWS_DT", "DT_TM")),
                                  BSE_ATTACH_URL.format(name=attach) if attach else "", categorise(subject), SourceTier.T2))
        return docs

    async def bse_annual_reports(self, scrip_code: str) -> list[FilingDoc]:
        data = await self._bse_json(BSE_ANNUAL_REPORTS_URL, {"scripcode": scrip_code})
        docs = []
        for r in data.get("Table", []) if isinstance(data, dict) else []:
            year = str(_first(r, "Year", "YEAR", "year") or "")
            name = _first(r, "PDFDownload", "FILE_NAME", "file_name", "PDFNAME")
            if not name:
                continue
            url = name if str(name).startswith("http") else BSE_AR_URL.format(code=scrip_code, name=name)
            docs.append(FilingDoc("BSE", f"Annual Report {year}".strip(), _iso(_first(r, "DissemDate", "Date")), url,
                                  "annual_report", SourceTier.T1, fy_label_from_range(year)))
        return sorted(docs, key=lambda d: d.fiscal_year or "")

    async def nse_annual_reports(self, symbol: str) -> list[FilingDoc]:
        resp = await get_with_retry(self.client, NSE_ANNUAL_REPORTS_URL, self.limiter,
                                    params={"index": "equities", "symbol": symbol}, blocked_statuses=(401, 403))
        rows = resp.json().get("data", [])
        return [FilingDoc("NSE", f"Annual Report {r.get('fromYr')}-{r.get('toYr')}", "", r["fileName"], "annual_report",
                          SourceTier.T1, fy_label_from_range(f"{r.get('fromYr')}-{r.get('toYr')}"))
                for r in rows if r.get("fileName")]

    async def nse_announcements(self, symbol: str) -> list[FilingDoc]:
        resp = await get_with_retry(self.client, NSE_ANNOUNCEMENTS_URL, self.limiter,
                                    params={"index": "equities", "symbol": symbol}, blocked_statuses=(401, 403))
        rows = resp.json()
        return [FilingDoc("NSE", r.get("desc") or r.get("attchmntText", ""), _iso(r.get("an_dt")), r.get("attchmntFile", ""),
                          categorise(f"{r.get('desc', '')} {r.get('attchmntText', '')}"), SourceTier.T2)
                for r in (rows if isinstance(rows, list) else [])]

    async def fetch_filings(self, scrip_code: str, symbol: Optional[str], years: int = 5,
                            today: Optional[date] = None) -> tuple[list[FilingDoc], list[FetchTrace], list[str]]:
        """fetch_india_bse_nse_filings: BSE annual reports + announcements; NSE only when BSE has no annual report."""
        traces: list[FetchTrace] = []
        gaps: list[str] = []
        docs: list[FilingDoc] = []

        t = FetchTrace("bse_annual_reports", {"scrip_code": scrip_code})
        try:
            ars = await self.bse_annual_reports(scrip_code)
            docs += ars[-years:]
            traces.append(t.finish("ok", [d.title for d in ars[-years:]]))
        except SourceError as exc:
            ars = []
            traces.append(t.finish("error", str(exc)))

        if not ars and symbol:
            t = FetchTrace("nse_annual_reports", {"symbol": symbol})
            try:
                nse = await self.nse_annual_reports(symbol)
                docs += nse[-years:]
                traces.append(t.finish("ok", [d.title for d in nse[-years:]]))
            except SourceBlocked as exc:
                traces.append(t.finish("fallback", f"NSE refused the request ({exc}); expected for non-browser clients"))
            except SourceError as exc:
                traces.append(t.finish("error", str(exc)))

        t = FetchTrace("bse_announcements", {"scrip_code": scrip_code, "days": 365})
        try:
            ann = await self.bse_announcements(scrip_code, today=today)
            docs += ann
            traces.append(t.finish("ok", f"{len(ann)} announcements"))
        except SourceError as exc:
            traces.append(t.finish("error", str(exc)))
            gaps.append("BSE corporate announcements unavailable due to upstream error")

        n_annual = sum(1 for d in docs if d.category == "annual_report")
        if n_annual < 3:
            gaps.append(f"Only {n_annual} annual report(s) found on BSE/NSE; fewer than 3 audited years are available")
        return docs, traces, gaps
