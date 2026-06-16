import { useEffect, useRef } from "react";
import { createChart, ColorType, AreaSeries, LineSeries, BaselineSeries } from "lightweight-charts";
import { useMaximize, MaximizeButton } from "@/components/MaximizeButton";

// Vertical axis-title strip — text reads bottom-to-top ("text up" orientation).
function AxisTitle({ text, color }) {
  return (
    <div className="flex items-center justify-center px-0.5" style={{ width: 18 }}>
      <span
        className="text-[10px] uppercase tracking-wider whitespace-nowrap"
        style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", color: color || "var(--color-dim)" }}
      >
        {text}
      </span>
    </div>
  );
}

function addSeries(chart, kind, opts) {
  if (kind === "area") return chart.addSeries(AreaSeries, opts);
  if (kind === "baseline") return chart.addSeries(BaselineSeries, opts);
  return chart.addSeries(LineSeries, opts);
}

/**
 * A single-pane chart with an independently-scaled LEFT and RIGHT series, each
 * with a named vertical axis title. Used to render the two backtest charts
 * (cumulative P&L vs trade value; account value vs drawdown) as separate cards.
 *
 * left / right: { data:[{time,value}], kind:"area"|"line"|"baseline", color, label }
 */
export function DualAxisChart({ title, left, right, currency = true, height = 300, testid }) {
  const containerRef = useRef(null);
  const { panelRef, maximized, toggleMaximize, fullHeight } = useMaximize(height);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#11161D" },
        textColor: "#AAB4C5",
        fontFamily: "IBM Plex Mono, monospace",
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

    const leftOpts = left.kind === "area"
      ? { priceScaleId: "left", lineColor: left.color, topColor: `${left.color}55`, bottomColor: `${left.color}05`, lineWidth: 2, priceFormat: fmt }
      : left.kind === "baseline"
        ? { priceScaleId: "left", baseValue: { type: "price", price: 0 }, topLineColor: "#2ED47A", topFillColor1: "#2ED47A22", topFillColor2: "#2ED47A05", bottomLineColor: "#FF5D5D", bottomFillColor1: "#FF5D5D33", bottomFillColor2: "#FF5D5D05", priceFormat: fmt }
        : { priceScaleId: "left", color: left.color, lineWidth: 2, priceFormat: fmt };
    const ls = addSeries(chart, left.kind, leftOpts);
    if (left.data?.length) ls.setData(left.data);

    const rightOpts = right.kind === "area"
      ? { priceScaleId: "right", lineColor: right.color, topColor: `${right.color}55`, bottomColor: `${right.color}05`, lineWidth: 2, priceFormat: fmt }
      : right.kind === "baseline"
        ? { priceScaleId: "right", baseValue: { type: "price", price: 0 }, topLineColor: "#2ED47A", topFillColor1: "#2ED47A22", topFillColor2: "#2ED47A05", bottomLineColor: "#FF5D5D", bottomFillColor1: "#FF5D5D33", bottomFillColor2: "#FF5D5D05", priceFormat: fmt }
        : { priceScaleId: "right", color: right.color, lineWidth: 1, priceFormat: fmt };
    const rs = addSeries(chart, right.kind, rightOpts);
    if (right.data?.length) rs.setData(right.data);

    chart.timeScale().fitContent();
    return () => {
      chart.remove();
    };
    // Depend on STABLE values (data refs are memoized upstream, kind/color are
    // constant per chart), NOT the freshly-built left/right objects — otherwise
    // the chart is disposed + recreated on every parent render, which races the
    // autoSize ResizeObserver ("Object is disposed"). autoSize handles resizing,
    // so no manual ResizeObserver is needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left.data, right.data, left.kind, right.kind, left.color, right.color, currency]);

  const unit = currency ? "₹" : "pts";
  const chartHeight = maximized ? fullHeight : height;
  return (
    <div ref={panelRef} className="rounded-lg border border-line bg-bg-1 overflow-auto" data-testid={testid}>
      <div className="px-3 py-2 border-b border-line flex items-center gap-3 text-[11px] flex-wrap">
        <span className="font-semibold uppercase tracking-wider text-dim">{title}</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 inline-block" style={{ background: left.color }} /> {left.label} (left)</span>
        <span className="inline-flex items-center gap-1 text-dimmer"><span className="w-3 h-0.5 inline-block" style={{ background: right.color }} /> {right.label} (right)</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-dimmer">{unit} · scroll to zoom</span>
          <MaximizeButton maximized={maximized} onToggle={toggleMaximize} label="chart" testid={testid ? `${testid}-maximize` : "dual-axis-maximize"} />
        </div>
      </div>
      <div className="flex">
        <AxisTitle text={`${left.label} (${unit})`} color={left.color} />
        <div ref={containerRef} className="flex-1 min-w-0" style={{ height: `${chartHeight}px` }} />
        <AxisTitle text={`${right.label}${right.kind === "baseline" ? ` (${unit})` : ""}`} color={right.color} />
      </div>
    </div>
  );
}
