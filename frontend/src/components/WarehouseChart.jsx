import { useCallback, useEffect, useRef, useState } from "react";
import { createChart, ColorType, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtInt, fmtNum } from "@/lib/fmt";
import { useTheme } from "@/lib/theme";
import { LineChart, AlertTriangle, Crosshair, Target, Monitor, Moon, Sun } from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"];

// How many days of history to request per timeframe (intraday TFs need less so
// the chart stays responsive; daily shows the whole warehouse).
const LOOKBACK_DAYS = { "1m": 3, "5m": 7, "15m": 21, "1h": 90, "1d": 0 };

// Bucket size in seconds for each timeframe (used to snap a locate-time to its bar).
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400 };

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const CHART_THEME_OPTIONS = [
  { key: "system", label: "Use system chart theme", icon: Monitor, testId: "chart-theme-system" },
  { key: "dark", label: "Use dark chart theme", icon: Moon, testId: "chart-theme-dark" },
  { key: "light", label: "Use light chart theme", icon: Sun, testId: "chart-theme-light" },
];

const CHART_PALETTES = {
  dark: {
    background: "#11161D",
    text: "#E6EDF7",
    muted: "#AAB4C5",
    grid: "#1B2330",
    border: "#263041",
    legendBg: "rgba(17, 22, 29, 0.94)",
    legendBorder: "#9FB2CC",
    legendText: "#F5F8FF",
    up: "#2ED47A",
    down: "#FF5D5D",
    session: "#5AA9FF",
  },
  light: {
    background: "#FFFFFF",
    text: "#1C2636",
    muted: "#4B5A70",
    grid: "#E4EAF2",
    border: "#C4D0DE",
    legendBg: "rgba(255, 255, 255, 0.96)",
    legendBorder: "#6E8CAF",
    legendText: "#122033",
    up: "#128D52",
    down: "#D64545",
    session: "#2563EB",
  },
};

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

function normalizeChartTime(time) {
  if (typeof time === "number") return time;
  if (typeof time === "string") {
    const parsed = Date.parse(time);
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : 0;
  }
  if (time?.year && time?.month && time?.day) {
    return Math.floor(Date.UTC(time.year, time.month - 1, time.day, 0, 0, 0) / 1000);
  }
  return Number(time) || 0;
}

function istParts(timeSec) {
  const istMs = timeSec * 1000 + IST_OFFSET_MS;
  const iso = new Date(istMs).toISOString();
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

// Format a unix-seconds bar time as an IST label appropriate to the timeframe.
function barLabel(timeSec, timeframe) {
  const parts = istParts(normalizeChartTime(timeSec));
  return timeframe === "1d" ? parts.date : `${parts.date} ${parts.time}`;
}

// Lightweight Charts calls tickMarkFormatter with UTC epoch seconds. Render the
// axis in IST so intraday warehouse candles line up with NSE/BSE sessions.
function axisLabel(time, timeframe) {
  const timeSec = normalizeChartTime(time);
  if (!timeSec) return "";
  const parts = istParts(timeSec);
  if (timeframe === "1d") return parts.date.slice(5);
  return parts.time === "09:15" ? parts.date.slice(5) : parts.time;
}

function chartOptionsFor(palette, timeframe) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: palette.background },
      textColor: palette.text,
      fontFamily: "IBM Plex Mono, monospace",
    },
    localization: {
      timeFormatter: (time) => `${barLabel(normalizeChartTime(time), timeframe)} IST`,
    },
    grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: palette.border },
    timeScale: {
      borderColor: palette.border,
      timeVisible: timeframe !== "1d",
      secondsVisible: false,
      fixLeftEdge: true,
      tickMarkFormatter: (time) => axisLabel(time, timeframe),
    },
    height: 460,
    autoSize: true,
  };
}

function candlestickOptionsFor(palette) {
  return {
    upColor: palette.up,
    downColor: palette.down,
    wickUpColor: palette.up,
    wickDownColor: palette.down,
    borderVisible: false,
  };
}

function buildSessionMarkers(bars, timeframe, palette) {
  if (timeframe === "1d") return [];
  const sessions = [];
  let previousDate = null;
  for (const bar of bars) {
    const parts = istParts(bar.time);
    if (parts.date !== previousDate) {
      sessions.push({ time: bar.time, date: parts.date });
      previousDate = parts.date;
    }
  }
  const textEvery = Math.max(1, Math.ceil(sessions.length / 24));
  return sessions.map((session, index) => ({
    time: session.time,
    position: "belowBar",
    color: palette.session,
    shape: "circle",
    text: index % textEvery === 0 ? `${session.date.slice(5)} 09:15 IST` : "09:15",
  }));
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
  const { effectiveTheme } = useTheme();
  const [instrument, setInstrument] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState("1d");
  const [chartTheme, setChartTheme] = useState("system");
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState({ bar_count: 0, gaps: [], gap_day_count: 0 });
  const [legend, setLegend] = useState(null);        // hovered/last bar OHLC
  const [locate, setLocate] = useState({ date: todayIso(), time: "09:15" });
  const [locateMsg, setLocateMsg] = useState(null);
  const resolvedChartTheme = chartTheme === "system" ? effectiveTheme : chartTheme;
  const palette = CHART_PALETTES[resolvedChartTheme === "light" ? "light" : "dark"];

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);
  const sessionMarkersRef = useRef([]);
  const locateMarkerRef = useRef(null);
  const paletteRef = useRef(palette);
  const timeframeRef = useRef(timeframe);
  const loadSeqRef = useRef(0);
  const barsRef = useRef([]);       // current bars [{time,open,high,low,close}]
  const lastBarRef = useRef(null);

  const applyMarkers = useCallback(() => {
    markersRef.current?.setMarkers([
      ...sessionMarkersRef.current,
      ...(locateMarkerRef.current ? [locateMarkerRef.current] : []),
    ]);
  }, []);

  // Create the chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, chartOptionsFor(paletteRef.current, timeframeRef.current));
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, candlestickOptionsFor(paletteRef.current));
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

  useEffect(() => {
    paletteRef.current = palette;
    timeframeRef.current = timeframe;
    chartRef.current?.applyOptions(chartOptionsFor(palette, timeframe));
    seriesRef.current?.applyOptions(candlestickOptionsFor(palette));
    sessionMarkersRef.current = buildSessionMarkers(barsRef.current, timeframe, palette);
    if (locateMarkerRef.current) {
      locateMarkerRef.current = { ...locateMarkerRef.current, color: palette.session };
    }
    applyMarkers();
  }, [applyMarkers, palette, timeframe]);

  const load = useCallback(async () => {
    const seq = loadSeqRef.current + 1;
    loadSeqRef.current = seq;
    const requestedInstrument = instrument;
    const requestedTimeframe = timeframe;
    setLoading(true);
    setLocateMsg(null);
    try {
      const res = await api.warehouseOhlc(requestedInstrument, { timeframe: requestedTimeframe, ...windowFor(requestedTimeframe), include_gaps: true });
      if (seq !== loadSeqRef.current) return;
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
      sessionMarkersRef.current = buildSessionMarkers(clean, requestedTimeframe, paletteRef.current);
      locateMarkerRef.current = null;
      applyMarkers();
      chartRef.current?.timeScale().fitContent();
      setLegend(lastBarRef.current);
      setMeta({ bar_count: res.bar_count || clean.length, gaps: res.gaps || [], gap_day_count: res.gap_day_count || 0 });
      if (clean.length === 0) toast.message(`No ${requestedInstrument} candles stored for this window`);
    } catch (e) {
      if (seq !== loadSeqRef.current) return;
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Chart load failed: ${msg}`);
    } finally {
      if (seq === loadSeqRef.current) setLoading(false);
    }
  }, [applyMarkers, instrument, timeframe]);

  useEffect(() => { load(); }, [load]);

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

    locateMarkerRef.current = {
      time: match.time,
      position: "aboveBar",
      color: paletteRef.current.session,
      shape: "arrowDown",
      text: `${locate.date}${timeframe !== "1d" ? " " + locate.time : ""}`,
    };
    applyMarkers();
    const rangeFrom = Math.max(firstSec, match.time - bucket * 20);
    const rangeTo = Math.min(lastSec, match.time + bucket * 20);
    if (rangeFrom < rangeTo) {
      chartRef.current?.timeScale().setVisibleRange({ from: rangeFrom, to: rangeTo });
    } else {
      chartRef.current?.timeScale().fitContent();
    }
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

        <div className="flex items-center gap-1">
          {CHART_THEME_OPTIONS.map(({ key, label, icon: Icon, testId }) => (
            <Button
              key={key}
              size="icon"
              variant="secondary"
              onClick={() => setChartTheme(key)}
              title={label}
              aria-label={label}
              className={`h-7 w-7 border ${chartTheme === key ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-2 text-dim"}`}
              data-testid={testId}
            >
              <Icon className="w-3.5 h-3.5" />
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
            <div
              className="absolute top-2 left-2 z-20 max-w-[calc(100%-1rem)] rounded-md border px-2.5 py-1.5 text-[12px] leading-5 font-mono pointer-events-none shadow-sm flex flex-wrap gap-x-2 gap-y-0.5"
              style={{ backgroundColor: palette.legendBg, borderColor: palette.legendBorder, color: palette.legendText }}
              data-testid="chart-ohlc-legend"
            >
              <span className="font-semibold" style={{ color: palette.legendText }} data-testid="chart-title">
                {instrument} · {timeframe} · {barLabel(lg.time, timeframe)} IST
              </span>
              <span data-testid="chart-ohlc-open"><span style={{ color: palette.muted }}>O</span> <span style={{ color: palette.legendText }}>{fmtNum(lg.open, 2)}</span></span>
              <span data-testid="chart-ohlc-high"><span style={{ color: palette.muted }}>H</span> <span style={{ color: palette.legendText }}>{fmtNum(lg.high, 2)}</span></span>
              <span data-testid="chart-ohlc-low"><span style={{ color: palette.muted }}>L</span> <span style={{ color: palette.legendText }}>{fmtNum(lg.low, 2)}</span></span>
              <span data-testid="chart-ohlc-close"><span style={{ color: palette.muted }}>C</span> <span style={{ color: lgUp ? palette.up : palette.down }}>{fmtNum(lg.close, 2)}</span></span>
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
          <span data-testid="chart-session-note"> · Axis: IST · regular session 09:15-15:30 · blue dots mark session opens</span>
        </div>
      </div>
    </div>
  );
}
