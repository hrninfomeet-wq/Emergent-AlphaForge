import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, CandlestickSeries } from "lightweight-charts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { fmtInt } from "@/lib/fmt";
import { LineChart, AlertTriangle } from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const TIMEFRAMES = ["5m", "15m", "1h", "1d"];

// How many days of history to request per timeframe (intraday TFs need less so
// the chart stays responsive; daily shows the whole warehouse).
const LOOKBACK_DAYS = { "5m": 7, "15m": 21, "1h": 90, "1d": 0 };

function windowFor(timeframe) {
  const days = LOOKBACK_DAYS[timeframe] ?? 30;
  if (!days) return {}; // full history
  const end = Date.now();
  const start = end - days * 24 * 60 * 60 * 1000;
  return { start_ts: start, end_ts: end };
}

/**
 * Warehouse candlestick chart.
 *
 * Pick an index and timeframe; renders stored 1m candles resampled server-side
 * to 5m/15m/1h/1d (default 1d). A gap banner surfaces trading days that have
 * fewer than 375 stored minutes, so missing data is visible, not hidden behind
 * a coverage percentage.
 */
export default function WarehouseChart() {
  const [instrument, setInstrument] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState("1d");
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState({ bar_count: 0, gaps: [], gap_day_count: 0 });

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  // Create the chart once.
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
      rightPriceScale: { borderColor: "#263041" },
      timeScale: {
        borderColor: "#263041",
        timeVisible: true,
        secondsVisible: false,
      },
      height: 460,
      autoSize: true,
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#2ED47A",
      downColor: "#FF5D5D",
      wickUpColor: "#2ED47A",
      wickDownColor: "#FF5D5D",
      borderVisible: false,
    });
    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.warehouseOhlc(instrument, { timeframe, ...windowFor(timeframe), include_gaps: true });
      const bars = (res.bars || []).map((b) => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      }));
      // lightweight-charts requires strictly ascending, unique times.
      const seen = new Set();
      const clean = [];
      for (const b of bars) {
        if (!seen.has(b.time)) { seen.add(b.time); clean.push(b); }
      }
      seriesRef.current?.setData(clean);
      chartRef.current?.timeScale().fitContent();
      setMeta({ bar_count: res.bar_count || clean.length, gaps: res.gaps || [], gap_day_count: res.gap_day_count || 0 });
      if (clean.length === 0) toast.message(`No ${instrument} candles stored for this window`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Chart load failed: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [instrument, timeframe]);

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-chart-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
        <LineChart className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Candlestick Chart</div>

        <div className="flex items-center gap-1 ml-2">
          {INSTRUMENTS.map((i) => (
            <Button
              key={i}
              size="sm"
              variant="secondary"
              onClick={() => setInstrument(i)}
              className={`h-7 px-2.5 text-xs border ${i === instrument ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-2 text-dim"}`}
              data-testid={`chart-instrument-${i.toLowerCase()}`}
            >
              {i}
            </Button>
          ))}
        </div>

        <div className="flex items-center gap-1 ml-auto">
          {TIMEFRAMES.map((tf) => (
            <Button
              key={tf}
              size="sm"
              variant="secondary"
              onClick={() => setTimeframe(tf)}
              className={`h-7 px-2 text-xs border ${tf === timeframe ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-2 text-dim"}`}
              data-testid={`chart-tf-${tf}`}
            >
              {tf}
            </Button>
          ))}
        </div>
      </div>

      <div className="p-3">
        {meta.gap_day_count > 0 && (
          <div className="mb-2 rounded-md border border-amber-900 bg-amber-950/30 p-2 text-[11px] text-amber-100 flex items-start gap-2" data-testid="chart-gap-banner">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <div>
              <span className="font-semibold">{meta.gap_day_count} day(s)</span> in this window have missing 1m candles (&lt; 375/session).
              <span className="text-dimmer"> e.g. </span>
              {meta.gaps.slice(0, 4).map((g, i) => (
                <span key={g.date} className="font-mono">
                  {i > 0 && ", "}
                  {g.date} ({g.missing_count} miss)
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="relative">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg-1/60 text-xs text-dim" data-testid="chart-loading">
              Loading {instrument} {timeframe}…
            </div>
          )}
          <div ref={containerRef} style={{ width: "100%", height: 460 }} data-testid="warehouse-chart-canvas" />
        </div>
        <div className="mt-2 text-[10px] text-dimmer font-mono">
          {instrument} · {timeframe} · {fmtInt(meta.bar_count)} bars · stored warehouse data
          {timeframe !== "1d" && LOOKBACK_DAYS[timeframe] ? ` · last ${LOOKBACK_DAYS[timeframe]}d` : " · full history"}
        </div>
      </div>
    </div>
  );
}
