import type { PeerCompRow } from "../../types/atlas";
import { useRunStore } from "../../store/useRunStore";
import { Prose, peerSlug } from "../explain/EvidenceDrawer";
import { WaitingFor } from "../explain/TimelineScrubber";

const COLS: { key: keyof PeerCompRow & string; label: string }[] = [
  { key: "rev_growth_pct", label: "Revenue growth" },
  { key: "ebitda_margin_pct", label: "EBITDA margin" },
  { key: "roe_pct", label: "ROE" },
  { key: "pe_multiple", label: "P/E" },
];

export default function MarketTab() {
  const state = useRunStore((s) => s.projectedState);
  const openEvidence = useRunStore((s) => s.openEvidence);
  const rows = state.peer_comps;
  const priceDates = [...new Set(rows.map((r) => r.price_as_of).filter(Boolean))];

  return (
    <WaitingFor stageId="s09_market_peers" stateKey="peer_comps" ready={rows.length > 0}>
      <section className="card" aria-labelledby="peers-h">
        <h2 id="peers-h">Peer comparison</h2>
        <p className="sm mu mt-0">Peers go through the same extraction and metrics engine as the target. The median row is computed in code.</p>
        <div className="scroll-x">
          <table>
            <thead><tr><th>Company</th>{COLS.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker} className={r.is_target ? "bg-ac2" : r.is_median_row ? "font-medium" : ""}>
                  <td>
                    {r.company_name}
                    {!r.is_median_row && <span className="mu"> · {r.ticker}</span>}
                    {r.is_target && <span className="pill ml-2">target</span>}
                  </td>
                  {COLS.map((c) => (
                    <td key={c.key}>
                      <button type="button" className="num-link" onClick={() => openEvidence(`peer_${peerSlug(r)}_${c.key}`)}>
                        {r.formatted[c.key] ?? "—"}
                      </button>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {priceDates.length > 0 && <p className="sm mu mb-0">P/E uses closing prices as of {priceDates.join(", ")}.</p>}
      </section>
      {state.market_positioning_prose && (
        <section className="card"><h3>Positioning</h3><p className="my-1"><Prose text={state.market_positioning_prose} /></p></section>
      )}
      <div className="cgrid">
        <section className="card">
          <h3>Advantages</h3>
          <ul className="sm my-1 pl-5">{state.market_advantages.map((a, i) => <li key={i}><Prose text={a} /></li>)}</ul>
        </section>
        <section className="card">
          <h3>Disadvantages</h3>
          <ul className="sm my-1 pl-5">{state.market_disadvantages.map((a, i) => <li key={i}><Prose text={a} /></li>)}</ul>
        </section>
      </div>
    </WaitingFor>
  );
}
