import { useEffect, useRef } from "react";
import { createChart, ColorType, CandlestickSeries, AreaSeries, BaselineSeries } from "lightweight-charts";

/**
 * Multi-pane synchronized chart: price (candles) on top, equity (area) middle, drawdown (baseline) bottom.
 * Time scales are synchronized via lightweight-charts v5 panes API.
 *
 * Props:
 *   candles: [{time, open, high, low, close}]
 *   equity:  [{time, value}]
 *   drawdown:[{time, value}]
 *   markers: optional trade markers for the price pane
 */
export function MultiPaneChart({ candles = [], equity = [], drawdown = [], markers = [], height = 600 }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#11161D" },
        textColor: "#AAB4C5",
        fontFamily: "IBM Plex Mono, monospace",
        panes: { separatorColor: "#263041", separatorHoverColor: "#314055" },
      },
      localization: {
        locale: "en-US",
        timeFormatter: (ts) => {
          const d = new Date(ts * 1000);
          return d.toISOString().slice(11, 16);
        },
        dateFormat: "yyyy-MM-dd",
      },
      grid: {
        vertLines: { color: "#1B2330" },
        horzLines: { color: "#1B2330" },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: "#263041" },
      timeScale: {
        borderColor: "#263041",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (ts) => {
          const d = new Date(ts * 1000);
          return d.toISOString().slice(5, 16).replace("T", " ");
        },
      },
      height,
      autoSize: true,
    });
    chartRef.current = chart;

    // Price pane (pane index 0)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#2ED47A",
      downColor: "#FF5D5D",
      wickUpColor: "#2ED47A",
      wickDownColor: "#FF5D5D",
      borderVisible: false,
    });
    if (candles && candles.length) candleSeries.setData(candles);
    if (markers && markers.length && candleSeries.setMarkers) {
      try { candleSeries.setMarkers(markers); } catch (e) { /* setMarkers not available in v5 default */ }
    }

    // Equity pane (pane 1)
    const equitySeries = chart.addSeries(
      AreaSeries,
      {
        lineColor: "#5AA9FF",
        topColor: "#5AA9FF66",
        bottomColor: "#5AA9FF05",
        lineWidth: 2,
        priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      },
      1
    );
    if (equity && equity.length) equitySeries.setData(equity);

    // Drawdown pane (pane 2)
    const ddSeries = chart.addSeries(
      BaselineSeries,
      {
        baseValue: { type: "price", price: 0 },
        topLineColor: "#2ED47A",
        topFillColor1: "#2ED47A33",
        topFillColor2: "#2ED47A05",
        bottomLineColor: "#FF5D5D",
        bottomFillColor1: "#FF5D5D33",
        bottomFillColor2: "#FF5D5D05",
      },
      2
    );
    if (drawdown && drawdown.length) ddSeries.setData(drawdown);

    // Pane height ratios
    try {
      const panes = chart.panes();
      if (panes[0]) panes[0].setHeight(Math.round(height * 0.55));
      if (panes[1]) panes[1].setHeight(Math.round(height * 0.25));
      if (panes[2]) panes[2].setHeight(Math.round(height * 0.20));
    } catch (e) {}

    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
    // eslint-disable-next-line
  }, [candles, equity, drawdown]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: `${height}px` }}
      className="rounded-lg border border-line bg-bg-1 overflow-hidden"
      data-testid="multi-pane-chart"
    />
  );
}
