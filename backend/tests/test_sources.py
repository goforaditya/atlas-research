"""Fetcher tests against canned HTTP responses (respx); no live network.

The payloads below are small hand-made responses in each API's response shape, not recordings of real
companies. They exercise parsing, filtering, retries and fallbacks; they say nothing about any real filing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pymupdf
import pytest
import respx

from backend.sources import SourceBlocked, SourceConfigError, SourceError, verify_document_authenticity
from backend.sources.india_filings import (
    BSE_ANNOUNCEMENTS_URL,
    BSE_ANNUAL_REPORTS_URL,
    BSE_SCRIPS_URL,
    NSE_ANNUAL_REPORTS_URL,
    IndiaFilingsClient,
    categorise,
    fy_label_from_range,
)
from backend.sources.news_fetcher import (
    GDELT_DOC,
    NewsFetcher,
    deduplicate_articles,
    parse_gdelt,
    parse_google_news_rss,
)
from backend.sources.pdf_extractor import extract_raw_table, locate_statement_pages, render_page_png
from backend.sources.rate_limiter import CircuitBreaker, DomainLimiter, TokenBucket, get_with_retry
from backend.sources.sec_edgar import (
    TICKERS_URL,
    SecEdgarClient,
    extract_annual_line_items,
    extract_quarterly_line_items,
    recent_filings,
)
from backend.sources.sentiment_fetcher import (
    HN_SEARCH_URL,
    REDDIT_SEARCH_URL,
    REDDIT_TOKEN_URL,
    Post,
    SentimentFetcher,
    filter_bots_and_spam,
)

UA = "AtlasResearchBot/1.0 (compliance@atlasresearch.demo)"


async def _no_sleep(_s: float) -> None:
    return None


@pytest.fixture
def limiter() -> DomainLimiter:
    return DomainLimiter(sleep=_no_sleep, rate_override=10_000)


# ---------------------------------------------------------------- rate limiter and retries

async def test_token_bucket_waits_for_refill():
    now = [0.0]
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)
        now[0] += s

    b = TokenBucket(rate=2.0, clock=lambda: now[0], sleep=sleep)
    b.updated = 0.0
    await b.acquire()
    await b.acquire()
    assert waits == [pytest.approx(0.5)]


def test_circuit_breaker_opens_and_half_opens():
    now = [0.0]
    cb = CircuitBreaker(threshold=2, cooldown=10, clock=lambda: now[0])
    cb.record(False)
    assert not cb.is_open
    cb.record(False)
    assert cb.is_open
    now[0] = 11
    assert not cb.is_open


@respx.mock
async def test_get_with_retry_backs_off_on_429_then_succeeds(limiter):
    route = respx.get("https://data.sec.gov/x").mock(side_effect=[httpx.Response(429), httpx.Response(503),
                                                                  httpx.Response(200, json={"ok": True})])
    async with httpx.AsyncClient() as c:
        resp = await get_with_retry(c, "https://data.sec.gov/x", limiter)
    assert resp.json() == {"ok": True}
    assert route.call_count == 3


@respx.mock
async def test_get_with_retry_gives_up_after_three_backoffs(limiter):
    route = respx.get("https://data.sec.gov/y").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as c:
        with pytest.raises(SourceError):
            await get_with_retry(c, "https://data.sec.gov/y", limiter)
    assert route.call_count == 4


@respx.mock
async def test_blocked_status_raises_without_retry(limiter):
    route = respx.get("https://www.nseindia.com/api/z").mock(return_value=httpx.Response(403))
    async with httpx.AsyncClient() as c:
        with pytest.raises(SourceBlocked):
            await get_with_retry(c, "https://www.nseindia.com/api/z", limiter, blocked_statuses=(403,))
    assert route.call_count == 1


def test_document_whitelist():
    assert verify_document_authenticity("https://www.sec.gov/Archives/x.htm", b"abc").tier.value == "T1"
    assert verify_document_authenticity("https://www.bseindia.com/xml-data/a.pdf").accepted
    assert not verify_document_authenticity("https://www.moneycontrol.com/financials/x").accepted
    assert not verify_document_authenticity("https://example.com/report.pdf").accepted
    ir = verify_document_authenticity("https://ir.paytm.com/ar.pdf", b"x", ir_domains=("paytm.com",))
    assert ir.accepted and ir.tier.value == "T2" and len(ir.sha256) == 64


# ---------------------------------------------------------------- SEC EDGAR

def _fact(val, end, start=None, form="10-K", filed="2026-02-20", fp="FY", fy=2025, accn="0001-26-000001"):
    f = {"val": val, "end": end, "form": form, "filed": filed, "fp": fp, "fy": fy, "accn": accn}
    if start:
        f["start"] = start
    return f


FACTS = {
    "cik": 1512673,
    "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _fact(20_000_000_000, "2024-12-31", "2024-01-01", filed="2025-02-20", fy=2024),
            _fact(24_121_000_000, "2025-12-31", "2025-01-01"),
            _fact(6_000_000_000, "2025-09-30", "2025-07-01", form="10-Q", fp="Q3", filed="2025-11-05"),
            _fact(5_900_000_000, "2025-06-30", "2025-04-01", form="10-Q", fp="Q2", filed="2025-08-05"),
            _fact(17_000_000_000, "2025-09-30", "2025-01-01", form="10-Q", fp="Q3", filed="2025-11-05"),
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            _fact(1_000_000_000, "2024-12-31", "2024-01-01", filed="2025-02-20", fy=2024),
            _fact(1_100_000_000, "2024-12-31", "2024-01-01", filed="2026-02-20", fy=2025),  # restated comparative
            _fact(1_500_000_000, "2025-12-31", "2025-01-01"),
        ]}},
        "Assets": {"units": {"USD": [_fact(36_000_000_000, "2025-12-31"), _fact(34_000_000_000, "2024-12-31", filed="2025-02-20")]}},
    }},
}


async def test_sec_requires_contact_user_agent(limiter):
    async with httpx.AsyncClient() as c:
        with pytest.raises(SourceConfigError):
            SecEdgarClient(c, limiter, "AtlasResearchBot/1.0")


@respx.mock
async def test_sec_ticker_search(limiter):
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={
        "0": {"cik_str": 1512673, "ticker": "XYZ", "title": "Block, Inc."},
        "1": {"cik_str": 999, "ticker": "BLOK", "title": "Blockchain Holdings Corp"},
    }))
    async with httpx.AsyncClient() as c:
        sec = SecEdgarClient(c, limiter, UA)
        by_ticker = await sec.search_tickers("XYZ")
        by_name = await sec.search_tickers("Block Inc")
    assert by_ticker[0].cik == "0001512673"
    assert by_name[0].title == "Block, Inc."
    assert respx.calls.last.request.headers["User-Agent"] == UA


def test_sec_recent_filings_builds_archive_urls():
    subs = {"cik": "1512673", "filings": {"recent": {
        "form": ["10-K", "4", "8-K"], "accessionNumber": ["0001-26-000001", "x", "0001-26-000009"],
        "primaryDocument": ["xyz-10k.htm", "f4.xml", "xyz-8k.htm"], "filingDate": ["2026-02-20", "", "2026-03-01"],
        "reportDate": ["2025-12-31", "", "2026-02-27"], "items": ["", "", "5.02"]}}}
    rows = recent_filings(subs)
    assert [r["form"] for r in rows] == ["10-K", "8-K"]
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1512673/000126000001/xyz-10k.htm"
    assert rows[1]["items"] == "5.02"


def test_sec_annual_line_items_units_restatements_and_gaps():
    items, gaps = extract_annual_line_items(FACTS)
    rev = {i.fiscal_period: i for i in items if i.field_key == "revenue"}
    assert set(rev) == {"FY2024", "FY2025"}
    assert rev["FY2025"].value == Decimal("24121")  # USD -> USD_M by the unit normaliser
    assert rev["FY2025"].raw_value == "24121000000"
    pat24 = next(i for i in items if i.field_key == "pat" and i.fiscal_period == "FY2024")
    assert pat24.value == Decimal("1100") and pat24.is_restated  # latest filing wins and is flagged
    assets = {i.fiscal_period: i.value for i in items if i.field_key == "total_assets"}
    assert assets == {"FY2024": Decimal("34000"), "FY2025": Decimal("36000")}
    assert any("cfo" in g for g in gaps)
    assert not any("operating_income" in g for g in gaps)  # optional for most filers


def test_sec_quarterly_uses_three_month_10q_facts_only():
    q = extract_quarterly_line_items(FACTS)
    assert [(i.fiscal_period, i.value) for i in q if i.field_key == "revenue"] == [
        ("Q2FY2025", Decimal("5900")), ("Q3FY2025", Decimal("6000"))]


# ---------------------------------------------------------------- India filings (BSE first, NSE fallback)

@respx.mock
async def test_bse_scrip_search_and_announcements(limiter):
    respx.get(BSE_SCRIPS_URL).mock(return_value=httpx.Response(200, json=[
        {"SCRIP_CD": "543396", "scrip_id": "PAYTM", "Issuer_Name": "One 97 Communications Ltd", "ISIN_NUMBER": "INE982J01020",
         "INDUSTRY": "Fintech"},
        {"SCRIP_CD": "500001", "scrip_id": "ABC", "Issuer_Name": "Another Ltd", "ISIN_NUMBER": "", "INDUSTRY": ""},
    ]))
    respx.get(BSE_ANNOUNCEMENTS_URL).mock(return_value=httpx.Response(200, json={"Table": [
        {"NEWSSUB": "Resignation of Chief Financial Officer", "NEWS_DT": "2026-03-04T18:22:10", "ATTACHMENTNAME": "a1.pdf"},
        {"NEWSSUB": "Investor Presentation for Q4 FY26", "NEWS_DT": "2026-05-20T10:00:00", "ATTACHMENTNAME": "a2.pdf"},
        {"NEWSSUB": "", "NEWS_DT": "2026-05-21T10:00:00"},
    ]}))
    async with httpx.AsyncClient() as c:
        india = IndiaFilingsClient(c, limiter)
        hits = await india.search_scrips("PAYTM")
        anns = await india.bse_announcements("543396")
    assert hits[0].scrip_code == "543396"
    assert [a.category for a in anns] == ["management_change", "investor_presentation"]
    assert anns[0].filed_on == "2026-03-04"
    assert anns[0].url.endswith("/AttachLive/a1.pdf")
    assert respx.calls[0].request.headers["Referer"] == "https://www.bseindia.com/"


@respx.mock
async def test_nse_403_is_a_logged_fallback(limiter):
    respx.get(BSE_ANNUAL_REPORTS_URL).mock(return_value=httpx.Response(200, json={"Table": []}))
    respx.get(NSE_ANNUAL_REPORTS_URL).mock(return_value=httpx.Response(403))
    respx.get(BSE_ANNOUNCEMENTS_URL).mock(return_value=httpx.Response(200, json={"Table": []}))
    async with httpx.AsyncClient() as c:
        docs, traces, gaps = await IndiaFilingsClient(c, limiter).fetch_filings("543396", "PAYTM")
    assert docs == []
    assert [(t.tool_name, t.status) for t in traces] == [
        ("bse_annual_reports", "ok"), ("nse_annual_reports", "fallback"), ("bse_announcements", "ok")]
    assert any("fewer than 3 audited years" in g for g in gaps)


@respx.mock
async def test_bse_annual_reports_labels(limiter):
    respx.get(BSE_ANNUAL_REPORTS_URL).mock(return_value=httpx.Response(200, json={"Table": [
        {"Year": "2025-2026", "PDFDownload": "f26.pdf"}, {"Year": "2024-2025", "PDFDownload": "https://www.bseindia.com/x/f25.pdf"},
        {"Year": "2023-2024"},
    ]}))
    async with httpx.AsyncClient() as c:
        ars = await IndiaFilingsClient(c, limiter).bse_annual_reports("543396")
    assert [(d.fiscal_year, d.tier.value) for d in ars] == [("FY25", "T1"), ("FY26", "T1")]
    assert ars[1].url == "https://www.bseindia.com/bseplus/AnnualReport/543396/f26.pdf"


def test_fy_labels_and_categories():
    assert fy_label_from_range("2024-25") == "FY25"
    assert fy_label_from_range("2025-2026") == "FY26"
    assert categorise("Allotment of equity shares under QIP") == "fundraise"
    assert categorise("RBI imposes monetary penalty") == "regulatory"
    assert categorise("Trading window closure") == "other"


# ---------------------------------------------------------------- PDF statement pages and grids

@pytest.fixture
def statement_pdf(tmp_path):
    doc = pymupdf.open()
    toc = doc.new_page()
    toc.insert_text((72, 72), "Contents\nConsolidated Balance Sheet ..... 2\nStandalone Balance Sheet ..... 3")
    cons = doc.new_page()
    lines = [
        ("Consolidated Balance Sheet as at 31 March 2026", None, None),
        ("(Rs. in crore)", None, None),
        ("Particulars", "31-Mar-26", "31-Mar-25"),
        ("Trade receivables", "880.00", "610.00"),
        ("Cash and cash equivalents", "430.00", "(12.50)"),
        ("Total assets", "3,454.00", "2,657.00"),
        ("Other financial assets", "-", "4.00"),
    ]
    y = 72
    for label, a, b in lines:
        cons.insert_text((60, y), label, fontsize=9)
        if a:
            cons.insert_text((380 - pymupdf.get_text_length(a, fontsize=9), y), a, fontsize=9)
            cons.insert_text((480 - pymupdf.get_text_length(b, fontsize=9), y), b, fontsize=9)
        y += 16
    sa = doc.new_page()
    sa.insert_text((60, 72), "Standalone Balance Sheet as at 31 March 2026\n(₹ in crore)\nTotal assets 1,000.00 900.00")
    path = tmp_path / "ar.pdf"
    doc.save(path)
    return path


def test_locate_prefers_consolidated_over_contents_and_standalone(statement_pdf):
    pages = locate_statement_pages(statement_pdf)
    assert pages.best["balance_sheet"].page == 2
    assert pages.best["cash_flow"] is None


def test_extract_raw_table_grid_units_and_bboxes(statement_pdf):
    t = extract_raw_table(statement_pdf, 2)
    assert t.unit_header == "(Rs. in crore)"
    grid = {row[0]: row[1:] for row in t.grid}
    assert grid["Trade receivables"] == ["880.00", "610.00"]
    assert grid["Cash and cash equivalents"] == ["430.00", "(12.50)"]
    assert grid["Total assets"] == ["3,454.00", "2,657.00"]
    assert grid["Other financial assets"] == ["-", "4.00"]
    cell = next(r for r in t.rows if r[0].text == "Total assets")[1]
    x0, y0, x1, y1 = cell.bbox
    assert 0 < x0 < x1 <= t.page_size[0] and 0 < y0 < y1 <= t.page_size[1]


def test_render_page_png(statement_pdf, tmp_path):
    out = render_page_png(statement_pdf, 2, tmp_path / "p2.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- news

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Paytm parent posts loss as receivables climb - Mint</title><link>https://news.google.com/a</link>
<pubDate>Tue, 20 May 2026 08:00:00 GMT</pubDate><source url="https://www.livemint.com">Mint</source></item>
<item><title>Paytm stock tips for today - RandomBlog</title><link>https://news.google.com/b</link>
<pubDate>Tue, 20 May 2026 09:00:00 GMT</pubDate><source url="https://randomblog.example">RandomBlog</source></item>
<item><title>Old Paytm story - Reuters</title><link>https://news.google.com/c</link>
<pubDate>Mon, 01 Jan 2024 09:00:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""


def test_google_news_rss_keeps_tier1_publishers_and_strips_suffix():
    arts = parse_google_news_rss(RSS, "IN")
    assert [(a.title, a.publisher) for a in arts] == [
        ("Paytm parent posts loss as receivables climb", "Mint"), ("Old Paytm story", "Reuters")]


def test_gdelt_maps_domains():
    arts = parse_gdelt({"articles": [
        {"title": "Block shares fall", "domain": "www.reuters.com", "url": "u1", "seendate": "20260301T101500Z"},
        {"title": "Block shares fall", "domain": "unknown.example", "url": "u2", "seendate": "20260301T101500Z"},
    ]}, "US")
    assert [(a.publisher, a.published.year) for a in arts] == [("Reuters", 2026)]


def test_deduplicate_clusters_syndicated_headlines():
    from backend.sources.news_fetcher import Article

    a = [Article("1", "RBI imposes penalty of 5.39 crore on Paytm Payments Bank", "Mint", "", None, "gn"),
         Article("2", "RBI imposes penalty of 5.39 crore on Paytm Payments Bank: report", "Reuters", "", None, "gn"),
         Article("3", "Paytm Q4 results: revenue rises", "Mint", "", None, "gn")]
    groups = deduplicate_articles(a)
    assert sorted(len(g) for g in groups) == [1, 2]


@respx.mock
async def test_news_fetch_drops_old_articles_and_reports_gaps(limiter):
    respx.get(url__startswith="https://news.google.com/rss/search").mock(return_value=httpx.Response(200, text=RSS))
    respx.get(GDELT_DOC).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as c:
        arts, traces, gaps = await NewsFetcher(c, limiter).fetch("Paytm", "IN", now=datetime(2026, 10, 1, tzinfo=UTC))
    assert [a.publisher for a in arts] == ["Mint"]
    assert [t.status for t in traces] == ["ok", "error"]
    assert gaps == ["gdelt doc api unavailable due to upstream error"]


# ---------------------------------------------------------------- sentiment sources and the bot filter

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _post(i, platform="Reddit", author=None, text="Settlements have been delayed for three days again this week", **kw):
    return Post(f"p{i}", platform, author or f"user{i}", text, kw.pop("created_at", NOW - timedelta(days=5)), **kw)


def test_filter_drops_old_new_accounts_low_karma_and_short_reviews():
    posts = [
        _post(1, karma=500, account_created_at=NOW - timedelta(days=900)),
        _post(2, created_at=NOW - timedelta(days=400), text="Old complaint about onboarding paperwork from last year"),
        _post(3, karma=500, account_created_at=NOW - timedelta(days=10), text="Brand new account praising the stock"),
        _post(4, karma=3, text="Low karma account posting about the soundbox"),
        _post(5, "Hacker News", karma=4, text="Their API docs are actually quite good for merchants"),
        _post(6, "Play Store", text="worst app", rating=1),
    ]
    r = filter_bots_and_spam(posts, NOW)
    assert [p.post_id for p in r.kept] == ["p1"]
    assert r.removed == 5
    assert r.reasons["new or low-karma Reddit account"] == 2


def test_filter_drops_copy_paste_campaign_from_four_accounts():
    spam = "Huge news buy now before the takeover is announced next week by insiders"
    posts = [_post(i, text=spam, karma=100) for i in range(4)] + [_post(9, karma=100)]
    r = filter_bots_and_spam(posts, NOW)
    assert [p.post_id for p in r.kept] == ["p9"]
    assert r.reasons["duplicate text across accounts"] == 4


def test_filter_drops_play_store_bursts_only_above_twenty():
    base = NOW - timedelta(days=2)
    burst = [_post(i, "Play Store", text="Five stars great app really love it a lot", rating=5,
                   created_at=base + timedelta(minutes=i)) for i in range(21)]
    calm = [_post(100 + i, "Play Store", text=f"Review {i}: settlement reached my account {i} hours late", rating=4,
                  created_at=base + timedelta(minutes=i)) for i in range(20)]
    r = filter_bots_and_spam(burst + calm, NOW)
    assert r.reasons["review burst"] + r.reasons["duplicate text across accounts"] == 21
    assert sorted(p.post_id for p in r.kept) == sorted(p.post_id for p in calm)


@respx.mock
async def test_reddit_without_credentials_is_a_stated_gap(limiter):
    respx.get(HN_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": []}))

    def play_search(*a, **k):
        return []

    async with httpx.AsyncClient() as c:
        f = SentimentFetcher(c, limiter, play_search=play_search, play_reviews=lambda *a, **k: [])
        posts, traces, gaps = await f.fetch("Paytm", "One 97 Communications", "IN")
    assert posts == []
    assert traces[0].tool_name == "reddit_search" and traces[0].status == "fallback"
    assert gaps[0].startswith("Reddit skipped")


@respx.mock
async def test_reddit_oauth_search_and_author_metadata(limiter):
    respx.post(REDDIT_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(REDDIT_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": {"children": [
        {"data": {"id": "abc", "author": "merchant_raj", "title": "Payouts late", "selftext": "T+3 on weekends",
                  "created_utc": (NOW - timedelta(days=3)).timestamp(), "permalink": "/r/india/comments/abc"}},
        {"data": {"id": "del", "author": "[deleted]", "title": "x", "created_utc": NOW.timestamp()}},
    ]}}))
    respx.get("https://oauth.reddit.com/user/merchant_raj/about").mock(return_value=httpx.Response(200, json={
        "data": {"total_karma": 820, "created_utc": (NOW - timedelta(days=2000)).timestamp()}}))
    async with httpx.AsyncClient() as c:
        posts = await SentimentFetcher(c, limiter, reddit_client_id="id", reddit_client_secret="s").reddit("Paytm")
    assert len(posts) == 1
    p = posts[0]
    assert (p.post_id, p.text, p.karma) == ("rd_abc", "Payouts late T+3 on weekends", 820)
    assert respx.calls[1].request.headers["Authorization"] == "bearer tok"


@respx.mock
async def test_hn_algolia_with_karma_lookup(limiter):
    respx.get(HN_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": [
        {"objectID": "1", "author": "pg_fan", "comment_text": "<p>Square&#x27;s seller tools are solid</p>",
         "created_at_i": int((NOW - timedelta(days=1)).timestamp())}]}))
    respx.get("https://hn.algolia.com/api/v1/users/pg_fan").mock(return_value=httpx.Response(200, json={"karma": 12}))
    async with httpx.AsyncClient() as c:
        posts = await SentimentFetcher(c, limiter).hacker_news("Block Inc")
    assert posts[0].karma == 12 and "seller tools" in posts[0].text
    assert filter_bots_and_spam(posts, NOW).removed == 1  # karma under 15


async def test_play_store_matches_developer_and_converts_reviews(limiter):
    def play_search(q, **kw):
        return [{"appId": "net.one97.paytm", "developer": "One97 Communications Limited"},
                {"appId": "com.fake.paytm", "developer": "Fake Apps Inc"}]

    def play_reviews(app_id, **kw):
        assert app_id == "net.one97.paytm" and kw["count"] == 200
        return ([{"reviewId": "r1", "userName": "A", "content": "Settlement delayed again after the outage last week",
                  "score": 1, "at": datetime(2026, 9, 20, 10, 0)}], None)

    async with httpx.AsyncClient() as c:
        posts = await SentimentFetcher(c, limiter, play_search=play_search, play_reviews=play_reviews).play_store(
            "Paytm", "One97 Communications Ltd")
    assert [(p.post_id, p.rating, p.created_at.tzinfo is not None) for p in posts] == [("ps_r1", 1, True)]
