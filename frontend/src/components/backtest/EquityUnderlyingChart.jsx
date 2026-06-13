import { useEffect, useRef } from "react";
import { createChart, ColorType, AreaSeries, LineSeries, BaselineSeries } from "lightweight-charts";

/**
 * Account value (or cumulative P&L) over time with the underlying (NIFTY/…)
 * value overlaid on a second axis — the algotest-style "Cumulative P&L vs
 * Underlying" view — plus a dedicated drawdown pane below.
 *
 * This is NOT a buy-and-hold benchmark: the underlying line is context only
 * (where the index was while the strategy's account moved).
 *
 * Props:
 *   equity:     [{time, value}]  account value in ₹ (or cumulative points)
 *   underlying: [{time, value}]  index level at each trade
 *   drawdown:   [{time, value}]  underwater curve (₹ or points, <= 0)
 *   currency:   bool             ₹ vs points (affects axis precision)
 *   height:     number
 */
export function EquityUnderlyingChart({ equity = [], underlying = [], drawdown = [], currency = true, height = 420 }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#11161D" },
        textColor: "#AAB4C5",
        fontFamily: "IBM Plex Mono, monospace",
        panes: { separatorColor: "#263041", separatorHoverColor: "#314055" },
      },
      grid: { vertLines: { color: "#1B2330" }, horzLines: { color: "#1B2330" } },
      crosshair: { mode: 1 },
      leftPriceScale: { visible: true, borderColor: "#263041" },
      rightPriceScale: { visible: true, borderColor: "#263041" },
      timeScale: {
        borderColor: "#263041",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (ts) => new Date(ts * 1000).toISOString().slice(5, 16).replace("T", " "),
      },
      height,
      autoSize: true,
    });

    const precision = currency ? 0 : 2;
    const minMove = currency ? 1 : 0.01;

    // Account value / cumulative P&L on the RIGHT axis (the hero series).
    const equitySeries = chart.addSeries(
      AreaSeries,
      {
        priceScaleId: "right",
        lineColor: "#5AA9FF",
        topColor: "#5AA9FF55",
        bottomColor: "#5AA9FF05",
        lineWidth: 2,
        priceFormat: { type: "price", precision, minMove },
      },
      0,
    );
    if (equity.length) equitySeries.setData(equity);

    // Underlying index value on the LEFT axis — context only.
    const underlyingSeries = chart.addSeries(
      LineSeries,
      {
        priceScaleId: "left",
        color: "#7C8597",
        lineWidth: 1,
        lineStyle: 0,
        priceFormat: { type: "price", precision: 0, minMove: 1 },
        crosshairMarkerVisible: false,
      },
      0,
    );
    if (underlying.length) underlyingSeries.setData(underlying);

    // Drawdown pane (underwater).
    const ddSeries = chart.addSeries(
      BaselineSeries,
      {
        baseValue: { type: "price", price: 0 },
        topLineColor: "#2ED47A",
        topFillColor1: "#2ED47A22",
        topFillColor2: "#2ED47A05",
        bottomLineColor: "#FF5D5D",
        bottomFillColor1: "#FF5D5D33",
        bottomFillColor2: "#FF5D5D05",
        priceFormat: { type: "price", precision, minMove },
      },
      1,
    );
    if (drawdown.length) ddSeries.setData(drawdown);

    try {
      const panes = chart.panes();
      if (panes[0]) panes[0].setHeight(Math.round(height * 0.68));
      if (panes[1]) panes[1].setHeight(Math.round(height * 0.32));
    } catch (e) { /* panes API best-effort */ }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [equity, underlying, drawdown, currency]);

  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden" data-testid="equity-underlying-chart">
      <div className="px-3 py-2 border-b border-line flex items-center gap-3 text-[11px]">
        <span className="font-semibold uppercase tracking-wider text-dim">Account value &amp; underlying</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 bg-[#5AA9FF] inline-block" /> {currency ? "Account (₹)" : "Equity (pts)"}</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 bg-[#7C8597] inline-block" /> Underlying</span>
        <span className="ml-auto text-dimmer">drawdown below</span>
      </div>
      <div ref={containerRef} style={{ width: "100%", height: `${height}px` }} />
    </div>
  );
}
