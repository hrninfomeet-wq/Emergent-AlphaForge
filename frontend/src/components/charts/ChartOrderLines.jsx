import { useEffect, useRef, useState } from "react";
import { priceText } from "./chartPresentation";
import "./chartOrderLines.css";

export function orderLevels(rows) {
  return rows.filter(row => row.active && row.chart_owned).flatMap(row => {
    const prefix = row.mode === "live" ? "LIVE" : "Paper";
    const levels = row.quantity > 0
      ? [{ field: "entry_price", price: row.entry_price, name: `Avg ×${row.quantity}`, locked: true },
        { field: "stop_price", price: row.stop_price, name: "SL" },
        { field: "target_price", price: row.target_price, name: "Target" }]
      : [{ field: "limit_price", price: row.limit_price, name: "Buy limit" },
        { field: "trigger_price", price: row.trigger_price, name: "Buy trigger" }];
    return levels.filter(level => typeof level.price === "number" && Number.isFinite(level.price)).map(level => ({
      ...level, id: `${row.id}:${level.field}`, row, label: `${prefix} ${level.name}`,
      locked: level.locked || row.pending_change || row.cancel_requested,
    }));
  });
}

/** Ephemeral order handles, separate from durable user drawings. A drag only
 * proposes a price. The ticket owns confirmation and the server owns the order. */
export default function ChartOrderLines({ chart, series, rows, tickSize = .05, onRequestChange, disabled = false }) {
  const [coordinates, setCoordinates] = useState({});
  const [drag, setDrag] = useState(null);
  const levels = orderLevels(rows);
  const current = useRef(levels); current.current = levels;
  const dragRef = useRef(null);
  const box = useRef(null);
  useEffect(() => {
    const active = dragRef.current;
    if (active && (disabled || !current.current.some(level => level.id === active.id && !level.locked))) {
      dragRef.current = null; setDrag(null);
      chart?.applyOptions({ handleScroll: true, handleScale: true });
    }
  }, [rows, disabled, chart]);
  useEffect(() => {
    if (!chart || !series) return;
    let last = "";
    const update = () => {
      const next = {};
      current.current.forEach(level => { next[level.id] = series.priceToCoordinate(level.price); });
      const signature = JSON.stringify(next);
      if (signature !== last) { last = signature; setCoordinates(next); }
    };
    update();
    const timer = setInterval(update, 100);
    return () => clearInterval(timer);
  }, [chart, series]);
  useEffect(() => () => { if (dragRef.current) chart?.applyOptions({ handleScroll: true, handleScale: true }); }, [chart]);
  const proposedPrice = event => {
    const rect = box.current?.getBoundingClientRect();
    const y = rect && event.clientY - rect.top;
    const height = series?.getPane?.().getHeight();
    if (y == null || y < 0 || (height != null && y > height)) return null;
    const raw = series?.coordinateToPrice(y);
    if (!Number.isFinite(raw) || raw <= 0) return null;
    return Number((Math.round(raw / tickSize) * tickSize).toFixed(8));
  };
  const release = (event, canceled = false) => {
    const active = dragRef.current;
    dragRef.current = null; setDrag(null);
    if (!active) return;
    chart?.applyOptions({ handleScroll: true, handleScale: true });
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const price = canceled ? null : proposedPrice(event);
    // A concurrent fill/cancel while dragging invalidates the gesture.
    const still = current.current.find(level => level.id === active.id);
    if (!disabled && still && !still.locked && price !== null && price !== still.price) onRequestChange(still.row, still.field, price);
  };
  return <div ref={box} className="chart-order-handles" aria-label="Chart order lines">
    {levels.map(level => {
      const y = drag?.id === level.id ? drag.y : coordinates[level.id];
      if (y == null || y < 0 || y > (series?.getPane?.().getHeight() ?? Infinity)) return null;
      return <div className={`chart-order-handle ${level.field} ${level.row.mode}`} key={level.id} style={{ top: y }}>
        <button type="button" className="chart-order-price" aria-label={`${level.label} ${priceText(level.price)}`}
          title={level.locked ? "Entry average is read only; pending changes must settle" : "Drag to propose a new price, then confirm"}
          disabled={disabled || level.locked}
          onPointerDown={event => {
            if (event.button !== 0 || level.locked || disabled) return;
            event.preventDefault(); event.stopPropagation();
            event.currentTarget.setPointerCapture?.(event.pointerId);
            dragRef.current = level; setDrag({ id: level.id, y });
            chart?.applyOptions({ handleScroll: false, handleScale: false });
          }}
          onPointerMove={event => {
            if (dragRef.current?.id !== level.id) return;
            const rect = box.current.getBoundingClientRect();
            setDrag({ id: level.id, y: Math.max(0, event.clientY - rect.top) });
          }} onPointerUp={event => release(event)} onPointerCancel={event => release(event, true)}
          onKeyDown={event => {
            if (event.key === "ArrowUp" || event.key === "ArrowDown") {
              event.preventDefault();
              onRequestChange(level.row, level.field, Number((level.price + (event.key === "ArrowUp" ? tickSize : -tickSize)).toFixed(8)));
            }
          }}>{level.label} · {priceText(level.price)}{level.field === "target_price" ? " · software" : level.field === "stop_price" ? /broker/i.test(level.row.protection || "") ? " · broker status in blotter" : " · software" : ""}</button>
        {!level.locked && (level.field === "limit_price" || level.field === "target_price") && <button type="button" className="chart-order-remove"
          aria-label={level.field === "limit_price" ? "Cancel chart order" : "Remove chart target"} disabled={disabled}
          onClick={() => onRequestChange(level.row, level.field === "limit_price" ? "cancel" : "target_price", null)}>×</button>}
      </div>;
    })}
  </div>;
}
