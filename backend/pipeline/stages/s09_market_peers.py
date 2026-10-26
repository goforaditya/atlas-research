"""Stage 9: market and peers. Peer metrics come from the same engines; medians are computed in Decimal."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from backend.engine.decimal_math import median, safe_div
from backend.engine.derived_fields import compute_derived_fields
from backend.engine.formatters import MISSING, format_multiple, format_pct
from backend.engine.metrics_calculator import compute_all_metrics
from backend.schemas.financials import LineItem
from backend.schemas.intelligence import PeerCompRow

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
