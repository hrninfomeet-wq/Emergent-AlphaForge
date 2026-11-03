import React, { useEffect, useRef, useState } from "react";
import { displayPrice, displayQuantity, protectionLabel, istTime, FIELD_LABELS, rowIsPosition, selectableRows } from "../../lib/chartTradingModel";
import "./chartTrading.css";

function Summary({ summary }) {
  return <dl className="ct-summary">
    <dt>Instrument</dt><dd>{summary.symbol || summary.instrument_key || summary.instrument || "—"}</dd>
    <dt>Mode / side</dt><dd>{String(summary.mode || "").toUpperCase()} / {summary.side || "BUY"}</dd>
    <dt>Quantity</dt><dd>{displayQuantity(summary.quantity)}</dd><dt>Order type</dt><dd>{summary.order_type || "—"}</dd>
    <dt>Limit / trigger</dt><dd>{displayPrice(summary.limit_price)} / {displayPrice(summary.trigger_price)}</dd>
    <dt>Stop loss / target</dt><dd>{displayPrice(summary.stop_price)} / {displayPrice(summary.target_price)}</dd>
  </dl>;
}
function ConfirmDialog({ pending, mode, trading }) {
  const panel = useRef(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const closeRef = useRef(() => trading.setPending(null));
  closeRef.current = () => trading.setPending(null);
  useEffect(() => {
    const previous = document.activeElement;
    const node = panel.current;
    const focusable = () => [...node.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')];
    focusable()[0]?.focus();
    const keydown = event => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { event.preventDefault(); node.focus(); return; }
      const index = items.indexOf(document.activeElement);
      if (event.shiftKey && index <= 0) { event.preventDefault(); items[items.length - 1].focus(); }
      else if (!event.shiftKey && (index === -1 || index === items.length - 1)) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); if (previous?.isConnected) previous.focus(); };
  }, []);
  const isEnable = pending.kind === "enableInstant";
  const changeSummary = pending.kind === "change" ? { ...pending.row, mode: pending.mode, quantity: pending.row.quantity || pending.row.ordered_quantity, [pending.field]: pending.price } : null;
  const closing = pending.kind === "change" && pending.field === "cancel" && rowIsPosition(pending.row);
  if (closing) {
    const tick = trading.metadata?.contract?.tick_size;
    changeSummary.side = "SELL (close)";
    changeSummary.order_type = "LIMIT (engine-priced exit)";
    changeSummary.trigger_price = null;
    changeSummary.limit_price = tick > 0 && pending.row.mark_price > 0
      ? Number((Math.max(tick, Math.floor(pending.row.mark_price * .995 / tick) * tick)).toFixed(8)) : null;
  }
  const title = isEnable ? "Enable Instant Order" : pending.kind === "entry" ? "Confirm BUY order" : pending.field === "cancel" ? rowIsPosition(pending.row) ? "Confirm close position" : "Confirm cancel order" : `Confirm ${FIELD_LABELS[pending.field]} change`;
  return <div className="ct-dialog-backdrop"><div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="ct-dialog-title" className="ct-dialog"><h3 id="ct-dialog-title">{title}</h3>
    <Summary summary={pending.kind === "change" ? changeSummary : pending.preview.summary} />
    {pending.kind === "change" && <p>{FIELD_LABELS[pending.field]}: {displayPrice(pending.row[pending.field])} → {pending.field === "cancel" ? rowIsPosition(pending.row) ? "close position request" : "cancel entry request" : displayPrice(pending.price)}</p>}
    {closing && <p>The displayed limit is an estimate. The existing exit engine computes and reconciles its sell limit from the fresh premium; the final fill may differ.</p>}
    {isEnable ? <><p>Enabling Instant Order does not place this preview. Future BUY submissions for this instrument and mode will be sent after server preview without another confirmation. Changes and cancellations always require confirmation.</p>{mode === "live" && <><p className="ct-error">LIVE Instant Order can place real-money orders immediately. Losses can exceed your intended stop, and software protection can fail.</p><label><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} /> I understand LIVE orders will be sent without another confirmation.</label></>}</> : <p>{pending.kind === "entry" ? pending.preview.summary.warning : "A request does not confirm a fill or close. Check the refreshed status."}</p>}
    <button type="button" onClick={() => trading.setPending(null)}>Back</button><button type="button" onClick={() => trading.confirm(acknowledged)} disabled={trading.busy || (pending.kind === "change" && trading.stale) || (isEnable && mode === "live" && !acknowledged)}>{isEnable ? "Enable Instant Order" : `Confirm ${mode.toUpperCase()}`}</button>
  </div></div>;
}
function EditRow({ row, trading }) {
  const [field, setField] = useState("stop_price"), [price, setPrice] = useState("");
  const isPosition = rowIsPosition(row);
  const disabled = trading.stale || trading.busy || row.pending_change || row.cancel_requested || !row.chart_owned;
  return <tr><td>{row.symbol}<small>{row.status} · {row.mode.toUpperCase()}</small></td><td>{displayQuantity(isPosition ? row.quantity : row.ordered_quantity)}</td>
    <td>{row.order_type}<small>Limit {displayPrice(row.limit_price)} · Trigger {displayPrice(row.trigger_price)}</small></td>
    <td>{displayPrice(row.entry_price)} / {displayPrice(row.mark_price)}<small>{row.mark_fresh && !trading.stale ? "Fresh mark at snapshot" : "Stale / unknown mark"}</small></td>
    <td>{displayPrice(row.pnl)}</td><td>{displayPrice(row.stop_price)} / {displayPrice(row.target_price)}<small>{protectionLabel(row.protection)}</small></td>
    <td>{row.error && <small>{row.error}</small>}{!row.chart_owned ? "Managed outside chart" : <div className="ct-row-actions">
      <label><span className="ct-sr-only">Change field for {row.symbol}</span><select value={field} onChange={e => setField(e.target.value)}>
        <option value="stop_price">Stop loss</option><option value="target_price">Target</option>
        {!isPosition && row.order_type !== "MARKET" && <option value="limit_price">Limit</option>}
        {!isPosition && row.order_type === "STOP_LIMIT" && <option value="trigger_price">Trigger</option>}
      </select></label><label><span className="ct-sr-only">New price for {row.symbol}</span><input type="number" min="0.01" step="any" value={price} onChange={e => setPrice(e.target.value)} /></label>
      <button type="button" disabled={disabled || (row.mode === "live" && !trading.metadata?.live_available) || !price || Number(price) <= 0} onClick={() => trading.requestChange(row, field, Number(price))}>Review change</button>
      <button type="button" disabled={trading.stale || trading.busy || row.cancel_requested || !row.chart_owned} onClick={() => trading.requestChange(row, "cancel")}>{isPosition ? "Review close" : "Review cancel"}</button>
    </div>}</td></tr>;
}
export default function ChartTrading({ instrument, lastPrice, trading }) {
  const [tab, setTab] = useState("orders");
  const { mode, form, metadata, pending } = trading;
  const set = (name, value) => trading.setForm(previous => ({ ...previous, [name]: value }));
  const rows = selectableRows(trading.rows, mode).filter(row => row.instrument_key === instrument?.instrument_key && (tab === "positions" ? rowIsPosition(row) : !rowIsPosition(row)));
  const types = mode === "live" ? metadata?.live_order_types : metadata?.paper_order_types;
  const valid = Number(form.lots) > 0 && Number.isInteger(Number(form.lots)) && Number(form.stop_price) > 0 &&
    (form.order_type === "MARKET" || Number(form.limit_price) > 0) && (form.order_type !== "STOP_LIMIT" || Number(form.trigger_price) > 0) && (form.target_price === "" || Number(form.target_price) > 0);
  return <section className="ct-panel" aria-label="Chart trading">
    <header><h2>Chart trading</h2><strong className={mode === "live" ? "ct-live" : "ct-paper"}>{mode.toUpperCase()}</strong></header>
    <div className="ct-mode"><button type="button" aria-pressed={mode === "paper"} disabled={trading.busy} onClick={() => trading.setMode("paper")}>Paper</button><button type="button" aria-pressed={mode === "live"} disabled={trading.busy} onClick={() => trading.setMode("live")}>{metadata?.live_available ? "LIVE" : "View LIVE"}</button>{!metadata?.live_available && <small>Live entry / modification unavailable: {metadata?.live_reason || "Waiting for capability check"}</small>}</div>
    <p>Long option BUY only. SELL closes an owned position. {protectionLabel(mode === "paper" ? "software_only" : metadata?.protection)}.</p>
    {metadata && !metadata.supported && <p role="status">{metadata.reason}</p>}
    {trading.error && <p className="ct-error" role="alert">{trading.error}</p>}
    <div className="ct-mode"><strong>Instant Order: {trading.instant ? "ON" : "OFF"}</strong>{trading.instant ? <button type="button" onClick={trading.disableInstant}>Turn Instant off</button> : <button type="button" disabled={!valid || !metadata?.supported || trading.loading || trading.busy || (mode === "live" && !metadata.live_available)} onClick={() => trading.prepare(true)}>Review Instant Order</button>}<small>Resets when instrument or mode changes or this page reloads.</small></div>
    <form className="ct-ticket" onSubmit={e => { e.preventDefault(); trading.prepare(); }}>
      <div><strong>{instrument?.symbol || instrument?.instrument_key || "Select instrument"}</strong><small>BUY · Last {displayPrice(lastPrice)}</small></div>
      <label>Lots<input type="number" min="1" max={metadata?.max_lots} step="1" value={form.lots} onChange={e => set("lots", e.target.value)} /></label>
      <label>Order type<select value={form.order_type} onChange={e => set("order_type", e.target.value)}>{(types || ["LIMIT"]).map(type => <option key={type} value={type}>{type}</option>)}</select></label>
      {form.order_type !== "MARKET" && <label>Limit price<input type="number" min="0.01" step="any" value={form.limit_price} onChange={e => set("limit_price", e.target.value)} /></label>}
      {form.order_type === "STOP_LIMIT" && <label>Trigger price<input type="number" min="0.01" step="any" value={form.trigger_price} onChange={e => set("trigger_price", e.target.value)} /></label>}
      <label>Stop loss<input type="number" min="0.01" step="any" value={form.stop_price} onChange={e => set("stop_price", e.target.value)} required /></label>
      <label>Target (optional)<input type="number" min="0.01" step="any" value={form.target_price} onChange={e => set("target_price", e.target.value)} /></label>
      <button type="submit" disabled={!valid || !metadata?.supported || trading.loading || trading.busy || (mode === "live" && !metadata.live_available)}>{trading.instant ? `Send ${mode.toUpperCase()} BUY` : "Review BUY"}</button>
    </form>
    <div className="ct-blotter"><header><h3>Orders and positions</h3><button type="button" onClick={trading.refresh}>Refresh</button><span role="status">{trading.stale ? "Snapshot stale / unavailable" : `Snapshot ${trading.asOf ? istTime(trading.asOf) : "time unknown"}`}</span></header>
      <div role="tablist" aria-label="Trading blotter"><button role="tab" aria-selected={tab === "orders"} onClick={() => setTab("orders")}>Open orders</button><button role="tab" aria-selected={tab === "positions"} onClick={() => setTab("positions")}>Positions</button></div>
      <div className="ct-table"><table><thead><tr><th>Instrument / status</th><th>Qty</th><th>Order</th><th>Entry / mark</th><th>P&amp;L</th><th>Stop / target</th><th>Actions</th></tr></thead><tbody>{rows.map(row => <EditRow key={row.id} row={row} trading={trading} />)}</tbody></table></div>
      {!rows.length && <p>{trading.stale ? "Snapshot unavailable; account state is not confirmed." : "No matching active records in this mode and instrument."}</p>}
    </div>
    {pending && pending.key === instrument?.instrument_key && pending.mode === mode && <ConfirmDialog key={`${pending.kind}:${pending.generation}`} pending={pending} mode={mode} trading={trading} />}
  </section>;
}
