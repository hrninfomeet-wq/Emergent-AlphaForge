import { useEffect, useRef } from "react";
import { createChart, ColorType, AreaSeries, LineSeries, BaselineSeries } from "lightweight-charts";

/**
 * Two synchronized panes:
 *   Top    — Cumulative P&L (left axis) + per-trade buy value (right axis).
 *            The algotest-style "cumulative P&L vs trade value" view. The
 *            right-axis line is the capital DEPLOYED per trade (entry premium ×
 *            qty + charges), per the user's definition — NOT a benchmark and
 *            NOT the index level.
 *   Bottom — Account value / capital growth (left axis) + drawdown (right axis),
 *            clubbed together as requested.
 *
 * Props: cumPnl[], buyValue[], accountValue[], drawdown[] ([{time,value}]),
 *        currency (₹ vs points), rightLabel, height.
 */
export function EquityUnderlyingChart({
  cumPnl = [], buyValue = [], accountValue = [], drawdown = [],
  currency = true, rightLabel = "Trade buy value", height = 460,
}) {
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
    const fmt = { type: "price", precision, minMove };

    // --- Pane 0: cumulative P&L (LEFT) + trade buy value (RIGHT) ---
    const cumSeries = chart.addSeries(
      AreaSeries,
      {
        priceScaleId: "left",
        lineColor: "#2ED47A",
        topColor: "#2ED47A55",
        bottomColor: "#2ED47A05",
        lineWidth: 2,
        priceFormat: fmt,
      },
      0,
    );
    if (cumPnl.length) cumSeries.setData(cumPnl);

    const buySeries = chart.addSeries(
      LineSeries,
      {
        priceScaleId: "right",
        color: "#5AA9FF",
        lineWidth: 1,
        priceFormat: fmt,
        crosshairMarkerVisible: true,
      },
      0,
    );
    if (buyValue.length) buySeries.setData(buyValue);

    // --- Pane 1: account value / capital growth (LEFT) + drawdown (RIGHT) ---
    const acctSeries = chart.addSeries(
      LineSeries,
      {
        priceScaleId: "left",
        color: "#C9A227",
        lineWidth: 2,
        priceFormat: fmt,
      },
      1,
    );
    if (accountValue.length) acctSeries.setData(accountValue);

    const ddSeries = chart.addSeries(
      BaselineSeries,
      {
        priceScaleId: "right",
        baseValue: { type: "price", price: 0 },
        topLineColor: "#2ED47A",
        topFillColor1: "#2ED47A22",
        topFillColor2: "#2ED47A05",
        bottomLineColor: "#FF5D5D",
        bottomFillColor1: "#FF5D5D33",
        bottomFillColor2: "#FF5D5D05",
        priceFormat: fmt,
      },
      1,
    );
    if (drawdown.length) ddSeries.setData(drawdown);

    try {
      const panes = chart.panes();
      if (panes[0]) panes[0].setHeight(Math.round(height * 0.6));
      if (panes[1]) panes[1].setHeight(Math.round(height * 0.4));
    } catch (e) { /* panes API best-effort */ }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cumPnl, buyValue, accountValue, drawdown, currency]);

  const unit = currency ? "₹" : "pts";
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden" data-testid="equity-underlying-chart">
      <div className="px-3 py-2 border-b border-line flex items-center gap-3 text-[11px] flex-wrap">
        <span className="font-semibold uppercase tracking-wider text-dim">Cumulative P&amp;L vs trade value</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 bg-[#2ED47A] inline-block" /> Cumulative P&amp;L ({unit}, left)</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 bg-[#5AA9FF] inline-block" /> {rightLabel} (right)</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 bg-[#C9A227] inline-block" /> Account value</span>
        <span className="ml-auto text-dimmer">drawdown shaded · scroll to zoom</span>
      </div>
      <div ref={containerRef} style={{ width: "100%", height: `${height}px` }} />
    </div>
  );
}
