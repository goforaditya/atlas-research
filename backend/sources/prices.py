"""fetch_close_price: last exchange close, tagged T2 with its date (Stage 9).

US closes come from Finnhub `quote` (needs FINNHUB_API_KEY). Indian closes come from the BSE equity bhavcopy in
the UDiFF CSV format BSE has published since July 2024 (`FinInstrmId` = scrip code, `ClsPric` = close).
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from backend.sources import SourceError
from backend.sources.rate_limiter import DomainLimiter, get_with_retry

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
BSE_BHAVCOPY_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"


def parse_bhavcopy(text: str, scrip_code: str) -> Optional[Decimal]:
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("FinInstrmId") or "").strip() == str(scrip_code):
            try:
                return Decimal(row["ClsPric"].strip())
            except (InvalidOperation, KeyError, AttributeError):
                return None
    return None


class PriceFetcher:
    def __init__(self, client: httpx.AsyncClient, limiter: DomainLimiter, finnhub_api_key: str = ""):
        self.client, self.limiter, self.finnhub_key = client, limiter, finnhub_api_key

    async def finnhub_close(self, ticker: str) -> Optional[tuple[Decimal, str]]:
        if not self.finnhub_key:
            return None
        resp = await get_with_retry(self.client, FINNHUB_QUOTE_URL, self.limiter,
                                    params={"symbol": ticker, "token": self.finnhub_key})
        data = resp.json()
        if not data.get("c") or not data.get("t"):
            return None
        return Decimal(str(data["c"])), datetime.fromtimestamp(int(data["t"]), UTC).date().isoformat()

    async def bse_close(self, scrip_code: str, today: Optional[date] = None, lookback_days: int = 7) -> Optional[tuple[Decimal, str]]:
        day = today or datetime.now(UTC).date()
        for i in range(1, lookback_days + 1):
            d = day - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            try:
                resp = await get_with_retry(self.client, BSE_BHAVCOPY_URL.format(ymd=d.strftime("%Y%m%d")), self.limiter,
                                            headers={"Referer": "https://www.bseindia.com/"})
            except SourceError:
                continue  # holiday or not yet published
            price = parse_bhavcopy(resp.text, scrip_code)
            if price is not None:
                return price, d.isoformat()
        return None

    async def __call__(self, *, ticker: Optional[str] = None, jurisdiction: str = "US", scrip_code: Optional[str] = None,
                       **_: object) -> Optional[tuple[Decimal, str]]:
        if jurisdiction == "IN":
            return await self.bse_close(scrip_code) if scrip_code else None
        return await self.finnhub_close(ticker.split(":")[-1].strip()) if ticker else None
