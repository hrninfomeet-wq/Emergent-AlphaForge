import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtInt, fmtNum } from "@/lib/fmt";
import { LineChart, AlertTriangle, Crosshair, Target } from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"];

// How many days of history to request per timeframe (intraday TFs need less so
// the chart stays responsive; daily shows the whole warehouse).
const LOOKBACK_DAYS = { "1m": 3, "5m": 7, "15m": 21, "1h": 90, "1d": 0 };

// Bucket size in seconds for each timeframe (used to snap a locate-time to its bar).
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400 };

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

function windowFor(timeframe) {
  const days = LOOKBACK_DAYS[timeframe] ?? 30;
  if (!days) return {}; // full history
  const end = Date.now();
  const start = end - days * 24 * 60 * 60 * 1000;
  return { start_ts: start, end_ts: end };
}

// IST date + time -> UTC epoch ms (minute-truncated).
function istToMs(dateStr, timeStr) {
  if (!dateStr) return null;
  let hh = 9, mm = 15;
  if (timeStr) {
    const parts = timeStr.split(":");
    hh = Number(parts[0]); mm = Number(parts[1] || 0);
  }
  const [y, m, d] = dateStr.split("-").map(Number);
  return Date.UTC(y, m - 1, d, hh, mm, 0) - IST_OFFSET_MS;
}

function todayIso() {
  return new Date(Date.now() + IST_OFFSET_MS).toISOString().slice(0, 10);
}

// Format a unix-seconds bar time as an IST label appropriate to the timeframe.
function barLabel(timeSec, timeframe) {
  const istMs = timeSec * 1000 + IST_OFFSET_MS;
  const iso = new Date(istMs).toISOString();
  return timeframe === "1d" ? iso.slice(0, 10) : `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

/**
 * Warehouse candlestick chart.
 *
 * Pick an index and timeframe (1m/5m/15m/1h/1d, default 1d) over stored 1m
 * candles resampled server-side. Features:
 *   - TradingView-style OHLC legend (top-left) that follows the crosshair.
 *   - A date/time locator that marks the matching bar with an arrow, validates
 *     the date against the loaded range, and snaps a finer time to its bar.
 *   - A gap banner for trading days missing 1m candles.
 */
export default function WarehouseChart() {
  const [instrument, setInstrument] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState("1d");
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState({ bar_count: 0, gaps: [], gap_day_count: 0 });
  const [legend, setLegend] = useState(null);        // hovered/last bar OHLC
  const [locate, setLocate] = useState({ date: todayIso(), time: "09:15" });
  const [locateMsg, setLocateMsg] = useState(null);

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);
  const barsRef = useRef([]);       // current bars [{time,open,high,low,close}]
  const lastBarRef = useRef(null);

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
      timeScale: { borderColor: "#263041", timeVisible: true, secondsVisible: false },
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
    markersRef.current = createSeriesMarkers(seriesRef.current, []);

    // OHLC legend follows the crosshair; falls back to the last bar on leave.
    chart.subscribeCrosshairMove((param) => {
      const bar = param?.seriesData?.get(seriesRef.current);
      if (bar && param.time) {
        setLegend({ time: param.time, ...bar });
      } else {
        setLegend(lastBarRef.current ? { time: lastBarRef.current.time, ...lastBarRef.current } : null);
      }
    });

    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, []);

  const load = async () => {
    setLoading(true);
    setLocateMsg(null);
    try {
      const res = await api.warehouseOhlc(instrument, { timeframe, ...windowFor(timeframe), include_gaps: true });
      const bars = (res.bars || []).map((b) => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      }));
      const seen = new Set();
      const clean = [];
      for (const b of bars) {
        if (!seen.has(b.time)) { seen.add(b.time); clean.push(b); }
      }
      barsRef.current = clean;
      lastBarRef.current = clean.length ? clean[clean.length - 1] : null;
      seriesRef.current?.setData(clean);
      markersRef.current?.setMarkers([]);
      chartRef.current?.timeScale().fitContent();
      setLegend(lastBarRef.current);
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

  // Locate a candle by IST date/time: validate, snap to the timeframe bucket,
  // mark it with an arrow, and center the view on it.
  const locateCandle = () => {
    const bars = barsRef.current;
    if (!bars.length) { setLocateMsg({ type: "err", text: "No bars loaded yet." }); return; }
    const ms = istToMs(locate.date, timeframe === "1d" ? "09:15" : locate.time);
    if (ms == null) { setLocateMsg({ type: "err", text: "Pick a valid date." }); return; }
    const targetSec = Math.floor(ms / 1000);

    const firstSec = bars[0].time;
    const lastSec = bars[bars.length - 1].time;
    const bucket = TF_SECONDS[timeframe] || 60;

    if (targetSec < firstSec) {
      setLocateMsg({ type: "err", text: `Selected time is before the loaded range (${barLabel(firstSec, timeframe)} IST). Switch to 1d for older data.` });
      return;
    }
    if (targetSec > lastSec + bucket) {
      setLocateMsg({ type: "err", text: `Selected time is beyond stored data (last bar ${barLabel(lastSec, timeframe)} IST).` });
      return;
    }

    // Snap to the bar whose [start, start+bucket) window contains the target;
    // this auto-resolves a finer time to the appropriate coarser candle.
    let match = null;
    for (const b of bars) {
      if (targetSec >= b.time && targetSec < b.time + bucket) { match = b; break; }
      if (b.time > targetSec) { match = match || b; break; }
    }
    if (!match) match = bars[bars.length - 1];

    markersRef.current?.setMarkers([{
      time: match.time,
      position: "aboveBar",
      color: "#5AA9FF",
      shape: "arrowDown",
      text: `${locate.date}${timeframe !== "1d" ? " " + locate.time : ""}`,
    }]);
    chartRef.current?.timeScale().setVisibleRange({
      from: match.time - bucket * 20,
      to: match.time + bucket * 20,
    });
    setLegend(match);
    const snapped = (Math.floor(match.time / 60) * 60) !== Math.floor(targetSec / 60);
    setLocateMsg({
      type: "ok",
      text: `Marked bar at ${barLabel(match.time, timeframe)} IST${snapped ? " (snapped to the " + timeframe + " bar containing your time)" : ""}.`,
    });
  };

  const lg = legend;
  const lgUp = lg && lg.close >= lg.open;

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

      {/* Locate toolbar */}
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap text-[11px]">
        <Target className="w-3.5 h-3.5 text-info" />
        <span className="text-dim">Locate (IST)</span>
        <Input
          type="date"
          value={locate.date}
          onChange={(e) => setLocate((p) => ({ ...p, date: e.target.value }))}
          className="bg-bg-2 border-line h-7 w-36 text-xs"
          data-testid="chart-locate-date"
        />
        <Input
          type="time"
          value={locate.time}
          disabled={timeframe === "1d"}
          onChange={(e) => setLocate((p) => ({ ...p, time: e.target.value }))}
          className="bg-bg-2 border-line h-7 w-28 text-xs disabled:opacity-40"
          data-testid="chart-locate-time"
        />
        <Button size="sm" onClick={locateCandle} className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="chart-locate-button">
          <Crosshair className="w-3 h-3 mr-1" /> Mark
        </Button>
        {locateMsg && (
          <span className={`font-mono ${locateMsg.type === "err" ? "text-rose-300" : "text-emerald-300"}`} data-testid="chart-locate-msg">
            {locateMsg.text}
          </span>
        )}
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
          {/* OHLC legend overlay (top-left) */}
          {lg && (
            <div className="absolute top-2 left-2 z-20 rounded-md border border-line bg-bg-1/90 px-2 py-1 text-[11px] font-mono pointer-events-none" data-testid="chart-ohlc-legend">
              <span className="text-dim">{instrument} · {timeframe} · {barLabel(lg.time, timeframe)}</span>
              <span className="ml-2">O <span className="text-foreground">{fmtNum(lg.open, 2)}</span></span>
              <span className="ml-1.5">H <span className="text-foreground">{fmtNum(lg.high, 2)}</span></span>
              <span className="ml-1.5">L <span className="text-foreground">{fmtNum(lg.low, 2)}</span></span>
              <span className="ml-1.5">C <span className={lgUp ? "text-emerald-400" : "text-red-400"}>{fmtNum(lg.close, 2)}</span></span>
            </div>
          )}
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
