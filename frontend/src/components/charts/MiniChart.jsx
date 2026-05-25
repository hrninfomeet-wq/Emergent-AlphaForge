import { useEffect, useRef } from "react";
import { createChart, ColorType, CandlestickSeries, AreaSeries, LineSeries } from "lightweight-charts";

/**
 * Reusable TradingView Lightweight Charts wrapper.
 * Props:
 *   data: array of {time, open, high, low, close} for candles OR {time, value} for line/area
 *   type: 'candles' | 'area' | 'line'
 *   color: stroke color
 *   height: pixel height
 */
export function MiniChart({ data = [], type = "line", color = "#5AA9FF", height = 220, testid = "mini-chart" }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#11161D" },
        textColor: "#AAB4C5",
        fontFamily: "IBM Plex Mono, monospace",
      },
      localization: {
        locale: "en-US",
        timeFormatter: (ts) => new Date(ts * 1000).toISOString().slice(11, 16),
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
        tickMarkFormatter: (ts) => new Date(ts * 1000).toISOString().slice(5, 16).replace("T", " "),
      },
      height,
      autoSize: true,
    });
    chartRef.current = chart;

    let series;
    if (type === "candles") {
      series = chart.addSeries(CandlestickSeries, {
        upColor: "#2ED47A",
        downColor: "#FF5D5D",
        wickUpColor: "#2ED47A",
        wickDownColor: "#FF5D5D",
        borderVisible: false,
      });
    } else if (type === "area") {
      series = chart.addSeries(AreaSeries, {
        lineColor: color,
        topColor: `${color}55`,
        bottomColor: `${color}05`,
        lineWidth: 2,
      });
    } else {
      series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
      });
    }
    seriesRef.current = series;
    if (data && data.length) series.setData(data);

    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
    // eslint-disable-next-line
  }, []);

  useEffect(() => {
    if (seriesRef.current && data) seriesRef.current.setData(data);
  }, [data]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: `${height}px` }}
      data-testid={testid}
    />
  );
}
