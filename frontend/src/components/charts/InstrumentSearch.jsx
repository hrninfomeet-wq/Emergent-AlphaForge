import { useEffect, useId, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { chartApi } from "../../lib/chartApi";
import { INDEX_SHORTCUTS, readable } from "./chartPresentation";

export default function InstrumentSearch({ onSelect, uniqueId, label = "Search NSE or BSE instruments", savedKeys, addDisabled = false }) {
  const generatedId = useId();
  const listId = `${uniqueId || generatedId}-instruments`;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(INDEX_SHORTCUTS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);
  const wrapper = useRef(null);
  const input = useRef(null);
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(async () => {
      try {
        const data = await chartApi.instruments(query.trim(), { signal: controller.signal });
        if (!disposed) { setItems(Array.isArray(data.items) ? data.items : []); setActive(0); }
      } catch {
        if (!disposed && !controller.signal.aborted) { setError("Instrument search unavailable. Saved indices are still available."); setItems(INDEX_SHORTCUTS); }
      } finally { if (!disposed) setLoading(false); }
    }, 220);
    return () => { disposed = true; clearTimeout(timer); controller.abort(); };
  }, [query, open]);
  useEffect(() => {
    const outside = (event) => { if (!wrapper.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const choose = async (item) => {
    if (savedKeys) {
      if (!addDisabled && !savedKeys.has(item.instrument_key) && await onSelect(item)) input.current?.focus();
      return;
    }
    onSelect(item);
    setQuery("");
    setOpen(false);
  };
  return <div className="chart-search" ref={wrapper}>
    <Search size={17} />
    <input ref={input} aria-label={label} placeholder="Search symbol, strike or expiry…" role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
      aria-activedescendant={open && items[active] ? `${listId}-${active}` : undefined} value={query} onFocus={() => setOpen(true)}
      onChange={(event) => { setQuery(event.target.value); setOpen(true); }} onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
        if (event.key === "ArrowDown") { event.preventDefault(); setOpen(true); setActive((index) => Math.min(index + 1, items.length - 1)); }
        if (event.key === "ArrowUp") { event.preventDefault(); setActive((index) => Math.max(index - 1, 0)); }
        if (event.key === "Enter" && open && !loading && items[active]) { event.preventDefault(); choose(items[active]); }
      }} />
    {query && <button type="button" aria-label="Clear instrument search" onClick={() => setQuery("")}><X size={14} /></button>}
    {open && <div className="chart-search-menu">
      <div className="chart-search-menu-title">NSE + BSE <span>Indices · Equities · Futures · Options</span></div>
      {loading && <div className="chart-search-message" role="status">Searching instruments…</div>}
      {error && <div className="chart-inline-warning" role="status">{error}</div>}
      <div id={listId} role="listbox" aria-label="Matching instruments">
        {!loading && items.map((item, index) => {
          const added = savedKeys?.has(item.instrument_key);
          const action = added ? `${item.symbol} already in watchlist` : `Add ${item.symbol} to watchlist`;
          return <button type="button" role="option" aria-selected={index === active} id={`${listId}-${index}`}
            key={`${item.instrument_key}:${item.expiry || ""}`} aria-label={savedKeys ? action : undefined}
            disabled={added || addDisabled} onClick={() => choose(item)} onMouseEnter={() => setActive(index)}>
            <span><strong>{item.symbol}</strong><small>{item.name}{item.expiry ? ` · ${item.expiry}` : ""}</small></span>
            <span className="chart-search-kind">{item.exchange}<small>{readable(item.kind)}</small></span>
            {savedKeys && <span className={`chart-watchlist-add-icon${added ? " is-added" : ""}`} aria-hidden="true">{added ? "✓" : "+"}</span>}
          </button>;
        })}
        {!loading && !items.length && <div className="chart-search-message">No matching instruments. Try a symbol or expiry.</div>}
      </div>
    </div>}
  </div>;
}
