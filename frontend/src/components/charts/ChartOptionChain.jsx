import { useEffect, useId, useLayoutEffect, useRef } from "react";
import { RefreshCw, X } from "lucide-react";
import useChartOptionChain from "./useChartOptionChain";
import { finiteValue, priceText, timeText } from "./chartPresentation";
import "./chartOptionChain.css";

const numberText = (value, decimals = 0) => finiteValue(value) ? Number(value).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : "—";
const timestamp = (value) => finiteValue(value) && Number(value) > 0 && Number(value) <= 8640000000000000 ? `${timeText(Number(value) / 1000)} IST` : "unavailable";
const selectable = (leg) => Boolean(leg?.instrument?.instrument_key);

function QuoteCells({ leg, side, strike, itm, onChoose }) {
  const source = (field) => leg?.field_sources?.[field] || "upstox_rest_v2";
  const title = `${side} ${strike} · ${leg?.quote_status || "freshness unknown"} · LTP: ${source("ltp")} · ${leg?.quote_time_ms ? `quote ${timestamp(leg.quote_time_ms)}` : "exchange quote time unavailable"}`;
  const values = [numberText(leg?.oi_change), numberText(leg?.oi), numberText(leg?.volume), numberText(leg?.iv, 2)];
  const descriptions = [`Change to vendor previous OI; reference period unspecified; OI source: ${source("oi")}`, `OI source: ${source("oi")}`, `Volume source: ${source("volume")}`, "Vendor IV as received from REST; units unspecified"];
  const metrics = values.map((value, index) => <td key={index} className={itm ? "is-itm" : ""} title={descriptions[index]}>{value}</td>);
  const price = <td key="price" className={`chart-option-price ${itm ? "is-itm" : ""}`} title={title}>
    <button type="button" className="chart-option-ltp" disabled={!selectable(leg)} aria-label={`Load ${strike} ${side} chart at price`} onClick={() => onChoose(leg)}>{numberText(leg?.ltp, 2)}</button>
    <button type="button" className="chart-option-chart" disabled={!selectable(leg)} aria-label={`Load ${strike} ${side} chart`} onClick={() => onChoose(leg)}>Chart</button>
    {leg && <small>{leg.quote_status ? String(leg.quote_status).replace(/_/g, " ") : leg.quote_time_ms ? "Quote time available" : "Time unknown"}</small>}
  </td>;
  return side === "CE" ? <>{metrics}{price}</> : <>{price}{[...metrics].reverse()}</>;
}

export default function ChartOptionChain({ instrument, onSelect, onClose }) {
  const state = useChartOptionChain(instrument);
  const { data, expiry, expiries, loading, error, warnings, empty, status, streamHealthy } = state;
  const dialog = useRef(null), closeButton = useRef(null), titleId = useId(), descriptionId = useId();
  const scrollContainer = useRef(null);
  const rows = data?.rows || [];
  const windowIdentity = JSON.stringify([instrument?.instrument_key, expiry, state.center ?? null, rows[0]?.strike, rows[rows.length - 1]?.strike]);
  useLayoutEffect(() => {
    const scroller = scrollContainer.current;
    if (!scroller) return;
    const atm = scroller.querySelector("tr.is-atm");
    if (!atm) { scroller.scrollTop = 0; return; }
    const rowBounds = atm.getBoundingClientRect(), viewportBounds = scroller.getBoundingClientRect();
    scroller.scrollTop = Math.max(0, scroller.scrollTop + rowBounds.top - viewportBounds.top + rowBounds.height / 2 - scroller.clientHeight / 2);
    // Quote updates preserve the user's scroll; only a new strike window centers.
  }, [windowIdentity]);
  useEffect(() => {
    const previous = document.activeElement;
    closeButton.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  const onKeyDown = (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    if (event.key !== "Tab") return;
    const controls = [...dialog.current.querySelectorAll("button:not(:disabled), select:not(:disabled), [tabindex='0']")];
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  const choose = (leg) => { if (selectable(leg)) { onSelect(leg.instrument); onClose(); } };
  return <div className="chart-option-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} className="chart-option-chain" onKeyDown={onKeyDown}>
      <header className="chart-option-header"><div><small>OPTION CHAIN</small><h2 id={titleId}>{data?.underlying?.symbol || instrument?.symbol || "Options"}</h2></div>
        <button ref={closeButton} type="button" className="chart-option-close" aria-label="Close option chain" onClick={onClose}><X size={18} /></button></header>
      <div className="chart-option-toolbar">
        <label>Expiry <select aria-label="Option expiry" value={expiry} disabled={loading || !expiries.length} onChange={(event) => state.setExpiry(event.target.value)}>{!expiries.length && <option value="">Unavailable</option>}{expiries.map((date) => <option key={date} value={date}>{date}</option>)}</select></label>
        <span title="Spot and ATM use the REST snapshot; exchange quote time is unavailable.">Spot (snapshot) <strong>{finiteValue(data?.spot) ? priceText(data.spot) : "—"}</strong></span><span>ATM <strong>{numberText(data?.atm_strike, 2)}</strong></span>
        <span className={`chart-option-status ${streamHealthy && status === "Live updates" ? "is-streaming" : ""}`} role="status">{loading ? "Loading…" : status}</span>
        <button type="button" aria-label="Refresh option chain" disabled={loading} onClick={state.refresh}><RefreshCw size={13} /> Refresh</button>
      </div>
      <div className="chart-option-window"><button type="button" aria-label="Lower strikes" disabled={loading || rows.length < 2} onClick={() => state.moveWindow(-1)}>← Lower strikes</button>
        <button type="button" aria-label="Back to ATM" disabled={loading || !expiry} onClick={state.backToAtm}>Back to ATM</button>
        <button type="button" aria-label="Higher strikes" disabled={loading || rows.length < 2} onClick={() => state.moveWindow(1)}>Higher strikes →</button>
        {data && <span>{rows.length} shown{finiteValue(data.total_strikes) ? ` of ${numberText(data.total_strikes)} strikes` : " strikes"} · Received {timestamp(data.captured_at_ms)}</span>}
      </div>
      {error && <p className="chart-option-error" role="alert">{error}</p>}
      {warnings.length > 0 && <p className="chart-option-warning" role="status">{warnings.join(" · ")}</p>}
      {loading && <p className="chart-option-empty" role="status">Loading option chain…</p>}
      {empty && <p className="chart-option-empty">No listed option expiries for this instrument.</p>}
      {!loading && data && rows.length === 0 && <p className="chart-option-empty">No contracts in this strike window. Try Back to ATM or another expiry.</p>}
      {rows.length > 0 && <div ref={scrollContainer} className="chart-option-scroll" tabIndex={0} role="region" aria-label="Option chain table">
        <table><caption className="chart-option-sr-only">Calls and puts by strike. Price and Chart buttons load the contract chart.</caption><thead>
          <tr className="chart-option-sides"><th colSpan={5} scope="colgroup">CALLS · CE</th><th scope="col">Strike</th><th colSpan={5} scope="colgroup">PUTS · PE</th></tr>
          <tr>{["ΔOI", "OI", "Volume", "IV (vendor)", "LTP", "Strike", "LTP", "IV (vendor)", "Volume", "OI", "ΔOI"].map((label, index) => <th key={index} scope="col" title={label === "ΔOI" ? "Change to vendor previous OI; reference period unspecified" : undefined}>{label}</th>)}</tr>
        </thead><tbody>{rows.map((row) => <tr key={row.strike} className={row.atm ? "is-atm" : ""}>
          <QuoteCells leg={row.ce} side="CE" strike={row.strike} itm={finiteValue(data.spot) && Number(row.strike) < Number(data.spot)} onChoose={choose} />
          <th scope="row" className="chart-option-strike">{numberText(row.strike, Number(row.strike) % 1 ? 2 : 0)}{row.atm && <small>ATM</small>}</th>
          <QuoteCells leg={row.pe} side="PE" strike={row.strike} itm={finiteValue(data.spot) && Number(row.strike) > Number(data.spot)} onChoose={choose} />
        </tr>)}</tbody></table>
      </div>}
      <footer id={descriptionId}>Select an option price or Chart to open its chart. ΔOI: change to vendor previous OI; period unspecified. IV (vendor): raw value, units unspecified. Received time is not exchange quote time; REST exchange quote time unavailable. Quote updates do not refresh vendor IV.</footer>
    </section>
  </div>;
}
