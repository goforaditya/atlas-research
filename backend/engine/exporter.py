"""Clean-report export (Section 8.1, endpoint 7): GitHub-flavoured Markdown and a WeasyPrint PDF.

Both formats share one layout and one footnote registry: every [[token]] in the prose becomes a numbered
footnote naming the document, page and URL. Figures are the backend's formatted strings, never recomputed.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from jinja2 import Environment

from backend.engine.verification_gate import peer_slug
from backend.schemas.common import EvidenceCitation
from backend.schemas.state import ResearchRunState

TOKEN = re.compile(r"\s*\[\[([a-z0-9_]+)\]\]")

CURRENCY_LABEL = {"INR_CR": "₹ crore", "USD_M": "$ million"}
DERIVED_LABEL = {"ebit": "EBIT", "ebitda": "EBITDA", "interest_expense": "Interest expense", "gross_debt": "Gross debt",
                 "capex": "Capex"}


@dataclass
class Footnotes:
    """Assigns footnote numbers in order of first citation and resolves each token to its source."""

    state: ResearchRunState
    numbers: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def cite(self, token: str) -> int:
        if token not in self.numbers:
            self.numbers[token] = len(self.notes) + 1
            self.notes.append(self.describe(token))
        return self.numbers[token]

    def describe(self, token: str) -> str:
        ev, prefix = self.resolve(token)
        if ev is None:
            return prefix or f"Internal reference {token}"
        page = f", p.{ev.page_number}" if ev.page_number else ""
        url = f", {ev.source_url}" if ev.source_url else ""
        lead = f"{prefix}: " if prefix else ""
        return f"{lead}{ev.document_title}{page} ({ev.source_tier.value}){url}"

    def resolve(self, token: str) -> tuple[Optional[EvidenceCitation], str]:
        s = self.state
        reg = s.evidence_registry
        for m in [*s.metrics_ledger, *s.quarterly_metrics]:
            if m.metric_id == token:
                ev = reg.get(m.inputs[0].evidence_id) if m.inputs else None
                return ev, f"{m.metric_label} = {m.formula_substituted} = {m.formatted_value}, computed in code"
        for li in [*s.line_items, *s.quarterly_items]:
            if li.item_id == token:
                return li.evidence, f"{li.reported_label} {li.fiscal_period}"
        if token in reg:
            return reg[token], ""
        for n in s.news_events:
            if n.event_id == token:
                return n.evidence, n.headline
        for p in (s.sentiment.items if s.sentiment else []):
            if p.post_id == token:
                return p.evidence, f"{p.platform} post"
        for c in s.contradictions:
            if c.contradiction_id == token:
                return c.source_b_evidence, c.topic
        for f in s.red_flags:
            if f.rule_id == token:
                return None, f"Red-flag rule {f.rule_name}: {f.explanation}"
        for r in s.peer_comps:
            if token.startswith(f"peer_{peer_slug(r)}_"):
                column = token[len(f"peer_{peer_slug(r)}_"):]
                cell = r.formatted.get(column, "")
                if r.is_median_row:
                    return None, f"Peer median {column} {cell}, computed in code from the peer rows"
                ev_id = peer_evidence_id(r.evidence_ids, column)
                return reg.get(ev_id) if ev_id else None, f"{r.company_name} {column} {cell}"
        return None, ""


PEER_COLUMN_FIELD = {"rev_growth_pct": "revenue", "ebitda_margin_pct": "revenue", "roe_pct": "pat", "pe_multiple": "pat"}


def peer_evidence_id(evidence_ids: list[str], column: str) -> Optional[str]:
    """The latest evidence for the column's main input (ids are listed oldest period first), else the first id."""
    field_name = PEER_COLUMN_FIELD.get(column, "")
    matches = [e for e in evidence_ids if f"_{field_name}_" in e]
    return matches[-1] if matches else (evidence_ids[0] if evidence_ids else None)


def prose_md(text: Optional[str], fn: Footnotes) -> str:
    if not text:
        return ""
    return TOKEN.sub(lambda m: f"[^{fn.cite(m.group(1))}]", text)


def prose_html(text: Optional[str], fn: Footnotes) -> str:
    if not text:
        return ""
    out, last = [], 0
    for m in TOKEN.finditer(text):
        out.append(html.escape(text[last:m.start()]))
        out.append(f'<sup class="fn">{fn.cite(m.group(1))}</sup>')
        last = m.end()
    out.append(html.escape(text[last:]))
    return "".join(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "report"


def export_filename(state: ResearchRunState, replay_id: Optional[str], fmt: str, today: Optional[date] = None) -> str:
    base = replay_id or _slug(state.entity.legal_name if state.entity else state.company_query)
    return f"atlas_{base}_{(today or date.today()).strftime('%Y%m%d')}.{fmt}"


# ----------------------------------------------------------------- shared layout model

@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]


def statement_table(state: ResearchRunState) -> Table:
    periods = list(dict.fromkeys(li.fiscal_period for li in state.line_items))
    rows: dict[str, dict[str, str]] = {}
    labels: dict[str, str] = {}
    for li in state.line_items:
        key = f"{li.field_key}:{li.segment_name or ''}"
        labels.setdefault(key, f"Segment: {li.segment_name}" if li.segment_name else
                          (f"{DERIVED_LABEL.get(li.field_key, li.reported_label)} (derived)" if li.is_derived else li.reported_label))
        rows.setdefault(key, {})[li.fiscal_period] = li.formatted_value
    return Table(["Line item", *periods], [[labels[k], *(cells.get(p, "—") for p in periods)] for k, cells in rows.items()])


def ledger_table(state: ResearchRunState) -> Table:
    periods = [p for p in state.fiscal_years if any(m.fiscal_period == p for m in state.metrics_ledger)]
    rows: dict[str, dict[str, str]] = {}
    labels: dict[str, str] = {}
    for m in state.metrics_ledger:
        labels.setdefault(m.metric_key, re.sub(rf"\s*{re.escape(m.fiscal_period)}$", "", m.metric_label))
        rows.setdefault(m.metric_key, {})[m.fiscal_period] = m.formatted_value
    return Table(["Metric", *periods], [[labels[k], *(cells.get(p, "—") for p in periods)] for k, cells in rows.items()])


def peer_table(state: ResearchRunState) -> Table:
    cols = [("rev_growth_pct", "Revenue growth"), ("ebitda_margin_pct", "EBITDA margin"), ("roe_pct", "ROE"), ("pe_multiple", "P/E")]
    rows = []
    for r in state.peer_comps:
        name = r.company_name + (" (target)" if r.is_target else "")
        rows.append([name, *(r.formatted.get(k, "—") for k, _ in cols)])
    return Table(["Company", *(label for _, label in cols)], rows)


def red_flag_table(state: ResearchRunState) -> Table:
    return Table(["Rule", "Severity", "Observed", "Status"],
                 [[f.rule_name, f.severity, f.observed_value_formatted or "—", f.status.upper()] for f in state.red_flags])


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def md_table(t: Table) -> str:
    head = "| " + " | ".join(_md_cell(h) for h in t.header) + " |"
    sep = "| " + " | ".join(["---"] + ["---:"] * (len(t.header) - 1)) + " |"
    body = ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in t.rows]
    return "\n".join([head, sep, *body])


# ----------------------------------------------------------------- Markdown

def build_markdown(state: ResearchRunState, *, as_of: Optional[date] = None, synthetic: bool = False,
                   unconfirmed: bool = False) -> str:
    fn = Footnotes(state)
    p: Callable[[Optional[str]], str] = lambda t: prose_md(t, fn)  # noqa: E731
    e, s = state.entity, state.summary
    out: list[str] = []
    title = e.legal_name if e else state.company_query
    out.append(f"# {title}")
    meta = [f"{e.exchange}: {e.ticker}" if e and e.ticker else None, e.sector if e else None,
            f"As of {(as_of or date.today()).isoformat()}", f"Run {state.run_id}",
            f"Figures in {CURRENCY_LABEL[e.currency.value]}" if e else None]
    out.append(" · ".join(m for m in meta if m))
    if synthetic:
        out.append("\n> **Synthetic sample.** This company is made up to demonstrate the pipeline. None of its figures are real.")
    if unconfirmed:
        out.append("\n> **Unconfirmed seed figures.** These numbers come from the build specification's seed table (reviewer memory, press and IPO-portal summaries) and have not yet been checked against the filings. Do not rely on them.")

    if s:
        out += ["", "## Summary", "", f"**{s.verdict_headline}**", "", p(s.verdict_prose), "",
                f"Claims: {s.fact_count} fact, {s.inference_count} inference, {s.hypothesis_count} hypothesis.", "",
                "### Business", "", s.business_overview, "", p(s.revenue_mix_summary)]
        if s.quarterly_direction_note:
            out += ["", f"Latest quarter: {p(s.quarterly_direction_note)}"]
        out += ["", "### Management", "", s.management_summary]
        if s.governance_watch_note:
            out += ["", f"**Governance watch:** {s.governance_watch_note}"]
        out += ["", "### Opportunities", "", *(f"- {p(o)}" for o in s.opportunities),
                "", "### Risks", "", *(f"- {p(r)}" for r in s.risks)]

    out += ["", "## Financials", ""]
    fired = [f for f in state.red_flags if f.fired]
    if fired:
        out += ["### Red flags fired", "", *(f"- **{f.rule_name}** ({f.severity}): {f.explanation}" for f in fired), ""]
    if state.red_flags:
        out += ["### All rules evaluated", "", md_table(red_flag_table(state)), ""]
    if state.line_items:
        out += ["### Statements", "", md_table(statement_table(state)), ""]
    if state.metrics_ledger:
        out += ["### Calculation ledger", "", "Every ratio is computed in Python Decimal from the cited line items.", "",
                md_table(ledger_table(state)), ""]

    if state.news_events:
        out += ["## News", ""]
        for n in state.news_events:
            tag = "corroborated by filing" if n.corroborated_by_filing else "press only"
            out.append(f"- **{n.event_date} · {n.category}: {n.headline}** ({n.polarity.value}, {tag}). {p(n.summary)}")
        out.append("")

    if state.sentiment:
        st = state.sentiment
        out += ["## Sentiment", "",
                f"{st.total_posts_fetched} posts fetched, {st.bots_and_duplicates_removed} bots and duplicates removed in code, "
                f"{st.posts_classified} classified: {st.positive_pct}% positive, {st.neutral_pct}% neutral, "
                f"{st.negative_pct}% negative (all classified posts; only signal posts are listed).", ""]
        out += [f"- **{a.audience}**: {a.dominant_tone}. {a.trend_description}" for a in st.audience_breakdown]
        out.append("")
        for it in st.items:
            if it.verdict == "signal":
                out.append(f"- {it.platform} ({it.audience}, {it.polarity.value}): “{it.quote_text}”[^{fn.cite(it.post_id)}]")
        out.append("")

    if state.peer_comps:
        out += ["## Market", "", md_table(peer_table(state)), ""]
        if state.market_positioning_prose:
            out += [p(state.market_positioning_prose), ""]
        if state.market_advantages:
            out += ["**Advantages**", "", *(f"- {p(a)}" for a in state.market_advantages), ""]
        if state.market_disadvantages:
            out += ["**Disadvantages**", "", *(f"- {p(a)}" for a in state.market_disadvantages), ""]

    if state.insights:
        out += ["## Insights", ""]
        for ins in state.insights:
            out += [f"### [{ins.classification.value.upper()}] {ins.title}", "", p(ins.body), ""]
        if s and s.analyst_follow_up_notes:
            out += ["**Analyst follow-up**", "", *(f"- {p(n)}" for n in s.analyst_follow_up_notes), ""]

    out += ["## Method", "", "Source tiers: T1 audited filings and regulators (1.00), T2 exchange disclosures (0.85), "
            "T3 tier-1 press (0.65), T4 social and reviews (0.30). The language model never computes a figure.", ""]
    for c in state.contradictions:
        out.append(f"- **{c.topic}**: A ({c.source_a_tier.value}) “{p(c.source_a_claim)}” vs B ({c.source_b_tier.value}) "
                   f"“{p(c.source_b_claim)}”. {c.resolution_rationale}")
    if state.contradictions:
        out.append("")
    if state.verification:
        v = state.verification
        out += [f"Verification gate: {v.total_numeric_tokens_scanned} figures scanned, {v.verified_against_ledger} matched the "
                f"ledger, {v.unverified_tokens_blocked} blocked, {len(v.claims_downgraded_to_hypothesis)} claims downgraded.", ""]

    if state.stated_gaps:
        out += ["## Stated gaps", "", *(f"- {g}" for g in state.stated_gaps), ""]

    if fn.notes:
        out += ["---", "", *(f"[^{i}]: {note}" for i, note in enumerate(fn.notes, start=1))]
    return "\n".join(out).rstrip() + "\n"


# ----------------------------------------------------------------- PDF

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ title }}</title>
<style>
@page { size: A4; margin: 18mm 16mm 20mm; @bottom-right { content: counter(page) " / " counter(pages); font: 9px 'IBM Plex Sans', sans-serif; color: #5B6578; } }
body { font: 10.5px/1.5 'IBM Plex Sans', 'DejaVu Sans', sans-serif; color: #14213D; font-variant-numeric: tabular-nums; }
h1, h2, h3 { font-family: Newsreader, Georgia, 'DejaVu Serif', serif; font-weight: 600; margin: 0 0 6px; }
h1 { font-size: 22px; } h2 { font-size: 16px; margin-top: 18px; border-bottom: 1px solid #D8DEE8; padding-bottom: 3px; }
h3 { font-size: 12.5px; margin-top: 10px; }
.meta { color: #5B6578; margin-bottom: 10px; }
.banner { background: #FCF3DF; color: #B7791F; padding: 6px 10px; border-radius: 6px; }
.rf { border-left: 3px solid #C2352B; background: #FDECEB; padding: 5px 9px; margin: 4px 0; }
.badge { display: inline-block; padding: 0 6px; border-radius: 8px; font-size: 9px; font-weight: 600; }
.fact { background: #E3F5EE; color: #12805C; } .inference { background: #E6ECFF; color: #2D4ED8; } .hypothesis { background: #FCF3DF; color: #B7791F; }
table { border-collapse: collapse; width: 100%; font-size: 9px; margin: 4px 0 8px; page-break-inside: auto; }
th, td { border-bottom: 1px solid #D8DEE8; padding: 2px 4px; text-align: right; }
th:first-child, td:first-child { text-align: left; } th { color: #5B6578; font-weight: 500; }
sup.fn { color: #2D4ED8; font-size: 7.5px; }
ol.notes { font-size: 8.5px; color: #5B6578; padding-left: 16px; }
</style></head><body>
<h1>{{ title }}</h1>
<div class="meta">{{ meta }}</div>
{% if synthetic %}<p class="banner"><b>Synthetic sample.</b> This company is made up to demonstrate the pipeline. None of its figures are real.</p>{% endif %}
{% if unconfirmed %}<p class="banner"><b>Unconfirmed seed figures.</b> These numbers come from the build specification's seed table and have not yet been checked against the filings. Do not rely on them.</p>{% endif %}
{% if s %}
<h2>Summary</h2>
<h3>{{ s.verdict_headline }}</h3>
<p>{{ verdict|safe }}</p>
<p><span class="badge fact">{{ s.fact_count }} fact</span> <span class="badge inference">{{ s.inference_count }} inference</span> <span class="badge hypothesis">{{ s.hypothesis_count }} hypothesis</span></p>
<h3>Business</h3><p>{{ s.business_overview }}</p><p>{{ mix|safe }}</p>
{% if quarterly %}<p>Latest quarter: {{ quarterly|safe }}</p>{% endif %}
<h3>Management</h3><p>{{ s.management_summary }}</p>
{% if s.governance_watch_note %}<p class="banner">Governance watch: {{ s.governance_watch_note }}</p>{% endif %}
<h3>Opportunities</h3><ul>{% for o in opportunities %}<li>{{ o|safe }}</li>{% endfor %}</ul>
<h3>Risks</h3><ul>{% for r in risks %}<li>{{ r|safe }}</li>{% endfor %}</ul>
{% endif %}
<h2>Financials</h2>
{% for f in fired %}<div class="rf"><b>{{ f.rule_name }}</b> ({{ f.severity }}): {{ f.explanation }}</div>{% endfor %}
{% for name, t in tables %}<h3>{{ name }}</h3>
<table><thead><tr>{% for h in t.header %}<th>{{ h }}</th>{% endfor %}</tr></thead>
<tbody>{% for row in t.rows %}<tr>{% for c in row %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>{% endfor %}
{% if news %}<h2>News</h2><ul>{% for n in news %}<li>{{ n|safe }}</li>{% endfor %}</ul>{% endif %}
{% if sentiment %}<h2>Sentiment</h2><p>{{ sentiment }}</p><ul>{% for x in signal %}<li>{{ x|safe }}</li>{% endfor %}</ul>{% endif %}
{% if peers %}<h2>Market</h2>
<table><thead><tr>{% for h in peers.header %}<th>{{ h }}</th>{% endfor %}</tr></thead>
<tbody>{% for row in peers.rows %}<tr>{% for c in row %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
{% if positioning %}<p>{{ positioning|safe }}</p>{% endif %}
{% if advantages %}<h3>Advantages</h3><ul>{% for a in advantages %}<li>{{ a|safe }}</li>{% endfor %}</ul>{% endif %}
{% if disadvantages %}<h3>Disadvantages</h3><ul>{% for a in disadvantages %}<li>{{ a|safe }}</li>{% endfor %}</ul>{% endif %}
{% endif %}
{% if insights %}<h2>Insights</h2>{% for i in insights %}<h3><span class="badge {{ i.cls }}">{{ i.cls|upper }}</span> {{ i.title }}</h3><p>{{ i.body|safe }}</p>{% endfor %}
{% if follow_up %}<h3>Analyst follow-up</h3><ul>{% for n in follow_up %}<li>{{ n|safe }}</li>{% endfor %}</ul>{% endif %}{% endif %}
<h2>Method</h2>
<p>Source tiers: T1 audited filings and regulators (1.00), T2 exchange disclosures (0.85), T3 tier-1 press (0.65), T4 social and reviews (0.30). The language model never computes a figure.</p>
{% for c in contradictions %}<p><b>{{ c.topic }}</b>: {{ c.text|safe }}</p>{% endfor %}
{% if verification %}<p>{{ verification }}</p>{% endif %}
{% if gaps %}<h2>Stated gaps</h2><ul>{% for g in gaps %}<li>{{ g }}</li>{% endfor %}</ul>{% endif %}
{% if notes %}<h2>Sources</h2><ol class="notes">{% for n in notes %}<li>{{ n }}</li>{% endfor %}</ol>{% endif %}
</body></html>"""

_env = Environment(autoescape=True)


def build_html(state: ResearchRunState, *, as_of: Optional[date] = None, synthetic: bool = False,
               unconfirmed: bool = False) -> str:
    fn = Footnotes(state)
    p: Callable[[Optional[str]], str] = lambda t: prose_html(t, fn)  # noqa: E731
    e, s = state.entity, state.summary
    meta = [f"{e.exchange}: {e.ticker}" if e and e.ticker else None, e.sector if e else None,
            f"As of {(as_of or date.today()).isoformat()}", f"Run {state.run_id}",
            f"Figures in {CURRENCY_LABEL[e.currency.value]}" if e else None]
    tables = []
    if state.red_flags:
        tables.append(("All rules evaluated", red_flag_table(state)))
    if state.line_items:
        tables.append(("Statements", statement_table(state)))
    if state.metrics_ledger:
        tables.append(("Calculation ledger", ledger_table(state)))
    st = state.sentiment
    ctx = dict(
        title=e.legal_name if e else state.company_query, meta=" · ".join(m for m in meta if m), synthetic=synthetic, unconfirmed=unconfirmed, s=s,
        verdict=p(s.verdict_prose) if s else "", mix=p(s.revenue_mix_summary) if s else "",
        quarterly=p(s.quarterly_direction_note) if s and s.quarterly_direction_note else "",
        opportunities=[p(o) for o in s.opportunities] if s else [], risks=[p(r) for r in s.risks] if s else [],
        fired=[f for f in state.red_flags if f.fired], tables=tables,
        news=[f"<b>{html.escape(n.event_date)} · {html.escape(n.category)}: {html.escape(n.headline)}</b> "
              f"({n.polarity.value}, {'corroborated by filing' if n.corroborated_by_filing else 'press only'}). {p(n.summary)}"
              for n in state.news_events],
        sentiment=(f"{st.total_posts_fetched} posts fetched, {st.bots_and_duplicates_removed} bots and duplicates removed, "
                   f"{st.posts_classified} classified: {st.positive_pct}% positive, {st.neutral_pct}% neutral, "
                   f"{st.negative_pct}% negative (all classified posts; only signal posts are listed).") if st else "",
        signal=[f"{html.escape(i.platform)} ({html.escape(i.audience)}): “{html.escape(i.quote_text)}”"
                f'<sup class="fn">{fn.cite(i.post_id)}</sup>' for i in (st.items if st else []) if i.verdict == "signal"],
        peers=peer_table(state) if state.peer_comps else None,
        positioning=p(state.market_positioning_prose), advantages=[p(a) for a in state.market_advantages],
        disadvantages=[p(a) for a in state.market_disadvantages],
        insights=[dict(cls=i.classification.value, title=i.title, body=p(i.body)) for i in state.insights],
        follow_up=[p(n) for n in s.analyst_follow_up_notes] if s else [],
        contradictions=[dict(topic=c.topic, text=f"A ({c.source_a_tier.value}) “{p(c.source_a_claim)}” vs B ({c.source_b_tier.value}) "
                             f"“{p(c.source_b_claim)}”. {html.escape(c.resolution_rationale)}") for c in state.contradictions],
        verification=(f"Verification gate: {state.verification.total_numeric_tokens_scanned} figures scanned, "
                      f"{state.verification.verified_against_ledger} matched the ledger, "
                      f"{state.verification.unverified_tokens_blocked} blocked.") if state.verification else "",
        gaps=state.stated_gaps,
    )
    ctx["notes"] = fn.notes  # read last: rendering the prose above registered every footnote
    return _env.from_string(_TEMPLATE).render(**ctx)


def render_pdf(state: ResearchRunState, *, as_of: Optional[date] = None, synthetic: bool = False,
               unconfirmed: bool = False) -> bytes:
    from weasyprint import HTML  # imported lazily: needs system Pango libraries

    return HTML(string=build_html(state, as_of=as_of, synthetic=synthetic, unconfirmed=unconfirmed)).write_pdf()
