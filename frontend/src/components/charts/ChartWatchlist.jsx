import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import InstrumentSearch from "./InstrumentSearch";
import useChartWatchlist, { watchlistQuote } from "./useChartWatchlist";
import "./chartWatchlist.css";

function LabelEditor({ label, name, onSave, onCancel, disabled }) {
  const [value, setValue] = useState(label);
  return <form className="chart-watchlist-edit" onSubmit={async (event) => { event.preventDefault(); if (await onSave(value)) onCancel(); }}>
    <input autoFocus aria-label={name} value={value} maxLength={80} onChange={(event) => setValue(event.target.value)} disabled={disabled}
      onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onCancel(); } }} />
    <button type="submit" disabled={disabled}>Save</button><button type="button" disabled={disabled} onClick={onCancel}>Cancel</button>
  </form>;
}

export default function ChartWatchlist({ activeInstrument, onSelect, onOptionChain }) {
  const state = useChartWatchlist();
  const { watchlist, loading, saving, error, quotes, warnings, streamHealthy, now } = state;
  const [adding, setAdding] = useState(false), [editingName, setEditingName] = useState(false), [editingRow, setEditingRow] = useState(null);
  const addButton = useRef(null), dialog = useRef(null), dialogId = useId();
  const disabled = loading || saving;
  const savedKeys = useMemo(() => new Set(watchlist?.items.map(item => item.instrument.instrument_key)), [watchlist]);
  const full = savedKeys.size >= 200;
  useEffect(() => {
    if (!adding) return undefined;
    const returnFocus = addButton.current;
    dialog.current?.querySelector("input")?.focus();
    return () => returnFocus?.focus();
  }, [adding]);
  const trapDialog = (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (!saving) setAdding(false); }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.current.querySelectorAll("input:not(:disabled), button:not(:disabled), [tabindex='0']")];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  return <aside className="chart-watchlist" aria-label="Chart watchlist">
    <header className="chart-watchlist-header">
      <div><small>WATCHLIST</small><strong title={watchlist?.name}>{watchlist?.name || "My watchlist"}</strong></div>
      <button type="button" aria-label="Rename watchlist" title="Rename watchlist" disabled={!watchlist || disabled} onClick={() => setEditingName(true)}><Pencil size={13} /></button>
      <button type="button" aria-label="Reload saved watchlist" title="Reload saved watchlist" disabled={disabled} onClick={() => state.refresh()}><RefreshCw size={13} /></button>
    </header>
    {editingName && watchlist && <LabelEditor key={`name-${watchlist.revision}`} label={watchlist.name} name="Watchlist name" onSave={state.rename} onCancel={() => setEditingName(false)} disabled={disabled} />}
    <div className="chart-watchlist-controls"><span>{watchlist?.items.length ?? 0} / 200</span>
      <button type="button" aria-label="Add symbol" ref={addButton} disabled={!watchlist || disabled || full} onClick={() => setAdding(true)}><Plus size={13} /> Add symbol</button>
    </div>
    <div className="chart-watchlist-message" role="status" aria-live="polite">{saving ? "Saving watchlist…" : loading ? "Loading saved watchlist…" : error ? "Saved state requires review" : watchlist ? "Saved on server" : "Saved list unavailable"}</div>
    {error && <div className="chart-watchlist-error" role="alert">{error}</div>}
    {warnings.length > 0 && <div className="chart-watchlist-warning" role="status">{warnings.join(" · ")}</div>}
    <div className="chart-watchlist-columns" aria-hidden="true"><span>Instrument</span><span>LTP / Day %</span></div>
    <ol className="chart-watchlist-items" aria-label="Saved instruments">
      {watchlist?.items.map((item, index) => {
        const instrument = item.instrument, title = item.label || instrument.symbol || instrument.name || instrument.instrument_key;
        const quote = watchlistQuote(quotes[instrument.instrument_key], streamHealthy, now);
        const quoteWarnings = quotes[instrument.instrument_key]?.warnings;
        const warningText = Array.isArray(quoteWarnings) ? quoteWarnings.filter((message) => typeof message === "string").join(" · ") : "";
        const active = activeInstrument?.instrument_key === instrument.instrument_key;
        return <li key={item.id} className={active ? "is-active" : ""}>
          <button type="button" className="chart-watchlist-select" aria-label={`Load ${title} chart`} aria-pressed={active} onClick={() => onSelect(instrument)}>
            <span className="chart-watchlist-symbol"><strong title={title}>{title}</strong><small>{instrument.exchange} · {instrument.symbol}</small></span>
            <span className="chart-watchlist-price"><strong>{quote.priceText}</strong><small className={quote.percent === null ? "" : quote.percent >= 0 ? "chart-positive" : "chart-negative"}>{quote.percent === null ? "Day % —" : `${quote.percent > 0 ? "+" : ""}${quote.percent.toFixed(2)}%`}</small></span>
          </button>
          <div className="chart-watchlist-quote-meta"><span title={warningText || undefined} className={`chart-watchlist-feed ${quote.fresh ? "is-fresh" : "is-stale"}`}>{quote.status}</span><span title={quote.timestamp}>{quote.timestamp}</span></div>
          {warningText && <div className="chart-watchlist-item-warning" title={warningText}>{warningText}</div>}
          {editingRow === item.id ? <LabelEditor key={`${item.id}-${watchlist.revision}`} label={item.label || ""} name={`Display label for ${instrument.symbol}`} onSave={(label) => state.relabel(item.id, label)} onCancel={() => setEditingRow(null)} disabled={disabled} /> :
            <div className="chart-watchlist-row-actions">
              <button type="button" disabled={disabled || index === 0} aria-label={`Move ${title} up`} title="Move up" onClick={() => state.move(item.id, -1)}><ArrowUp size={12} /></button>
              <button type="button" disabled={disabled || index === watchlist.items.length - 1} aria-label={`Move ${title} down`} title="Move down" onClick={() => state.move(item.id, 1)}><ArrowDown size={12} /></button>
              <button type="button" disabled={disabled} aria-label={`Rename ${title}`} title="Edit display label" onClick={() => setEditingRow(item.id)}><Pencil size={12} /></button>
              {onOptionChain && <button type="button" disabled={disabled} onClick={() => onOptionChain(instrument)} aria-label={`Open ${title} option chain`}>Chain</button>}
              <button type="button" disabled={disabled} className="chart-watchlist-remove" aria-label={`Remove ${title}`} title="Remove symbol" onClick={() => state.remove(item.id)}><Trash2 size={12} /></button>
            </div>}
        </li>;
      })}
    </ol>
    {!loading && watchlist?.items.length === 0 && <p className="chart-watchlist-empty">Add an instrument to follow its price and open its chart.</p>}
    <p className="chart-watchlist-footnote">Day % uses previous close. Stale prices retain their quote time.</p>
    {adding && <div className="chart-watchlist-modal-backdrop" onClick={(event) => { if (event.target === event.currentTarget && !saving) setAdding(false); }}>
      <div ref={dialog} className="chart-watchlist-modal" role="dialog" aria-modal="true" aria-labelledby={dialogId} onKeyDown={trapDialog}>
        <div className="chart-watchlist-modal-title"><h3 id={dialogId}>Add to {watchlist?.name}</h3><button type="button" disabled={saving} aria-label="Close add symbol" onClick={() => setAdding(false)}><X size={17} /></button></div>
        <InstrumentSearch label="Search instruments to add to watchlist" savedKeys={savedKeys} addDisabled={disabled || full} onSelect={state.add} />
        {full && <p role="status">Watchlist is full. Remove an instrument before adding another.</p>}
        {error && <div className="chart-watchlist-error" role="alert">{error}</div>}
        {saving && <p role="status">Saving instrument…</p>}
      </div>
    </div>}
  </aside>;
}
