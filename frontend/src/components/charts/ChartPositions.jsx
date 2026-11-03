import { ShieldCheck } from "lucide-react";
import { amountText, finiteValue, positionFresh, priceText, readable } from "./chartPresentation";

function PositionRow({ position, positionsAt, now }) {
  const fresh = positionFresh(position, positionsAt, now);
  const elapsed = positionsAt ? Math.max(0, now - positionsAt) : null;
  const levels = Array.isArray(position.protection_levels) ? position.protection_levels : [];
  return <tr>
    <td><span className={`chart-origin ${position.origin}`}>{position.origin === "broker" ? "BROKER" : "PAPER"}</span><strong>{position.symbol}</strong><small>{position.label || readable(position.protection_source)}</small></td>
    <td>{finiteValue(position.quantity) ? Number(position.quantity).toLocaleString("en-IN") : "Unknown"}</td>
    <td>{priceText(position.entry_price)}</td><td>{priceText(position.mark_price)}</td>
    <td className={finiteValue(position.pnl) ? position.pnl >= 0 ? "chart-positive" : "chart-negative" : ""}>{amountText(position.pnl)}<small>{position.pnl_basis || (position.origin === "broker" ? "Broker unrealised" : "Paper unrealised")}</small></td>
    <td>{levels.length ? levels.map((level) => <div key={level.id} title={level.source}>{level.kind === "stop" ? "SL" : "Target"} {priceText(level.price)}{finiteValue(level.quantity) ? ` × ${level.quantity}` : ""}<small>{level.label} · {readable(level.source)}</small></div>)
      : <>{priceText(position.stop_price)} / {priceText(position.target_price)}<small>{position.protection_source ? readable(position.protection_source) : "Protection unknown"}</small></>}</td>
    <td><span className={`chart-status ${fresh ? "success" : "warning"}`}>{fresh ? "Fresh" : "Stale / unknown"}</span>
      <small>{finiteValue(position.mark_age_ms) && elapsed !== null ? `Mark ${Math.floor((Number(position.mark_age_ms) + elapsed) / 1000)}s old` : "Mark age unknown"}</small>
      {position.origin === "broker" && <small>{finiteValue(position.broker_age_ms) && elapsed !== null ? `Broker ${Math.floor((Number(position.broker_age_ms) + elapsed) / 1000)}s old` : "Broker age unknown"}</small>}
    </td>
  </tr>;
}

export default function ChartPositions({ positions, positionsAt, now, warnings, error, origin, setOrigin, visible, setVisible }) {
  return <section className="chart-positions" aria-label="Read-only positions">
    <div className="chart-positions-header"><div className="chart-section-title"><ShieldCheck size={17} /><strong>Positions</strong><span className="chart-readonly">READ ONLY</span></div>
      <div className="chart-position-controls"><select aria-label="Position source filter" value={origin} onChange={(event) => setOrigin(event.target.value)}><option value="all">Broker + paper</option><option value="broker">Broker only</option><option value="paper">Paper only</option></select><label><input type="checkbox" checked={visible} onChange={(event) => setVisible(event.target.checked)} /> Show on chart</label></div>
    </div>
    {(error || warnings.length > 0) && <div className="chart-inline-warning" role="status">{[error, ...warnings].filter(Boolean).join(" · ")}</div>}
    {positions.length ? <div className="chart-position-table-wrap"><table className="chart-position-table"><thead><tr><th>Source / contract</th><th>Quantity</th><th>Average entry</th><th>Mark</th><th>Unrealised P&amp;L</th><th>Stop / target</th><th>Freshness</th></tr></thead><tbody>
      {positions.map((position) => <PositionRow key={`${position.origin}:${position.id}`} position={position} positionsAt={positionsAt} now={now} />)}
    </tbody></table></div> : <div className="chart-position-empty">{error || warnings.length ? "No matched positions in the available snapshot. Account state is not confirmed." : positionsAt ? "No matching positions for this exact instrument." : "Waiting for the position snapshot…"}</div>}
    <p>Only the selected contract is shown. Entry, stop and target lines are informational; protection is managed by the trading engine.</p>
  </section>;
}
