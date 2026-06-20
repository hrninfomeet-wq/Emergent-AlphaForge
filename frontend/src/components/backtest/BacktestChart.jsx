import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createChart, ColorType, CandlestickSeries, createSeriesMarkers } from "lightweight-charts";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtNum, fmtPnL } from "@/lib/fmt";
import { LineChart, Crosshair, ChevronLeft, ChevronRight, X, Maximize2, Minimize2 } from "lucide-react";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"];
const TF_SECONDS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400 };
const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const DAY_MS = 86400000;
const INSTR_LABEL = { NIFTY: "NIFTY 50", BANKNIFTY: "BANKNIFTY", SENSEX: "SENSEX" };

// --- IST time helpers (mirror WarehouseChart; kept local to avoid coupling) ---
function istParts(timeSec) {
  const iso = new Date(timeSec * 1000 + IST_OFFSET_MS).toISOString();
  return { date: iso.slice(0, 10), time: iso.slice(11, 16) };
}
function barLabel(timeSec, tf) {
  const p = istParts(timeSec);
  return tf === "1d" ? p.date : `${p.date} ${p.time}`;
}
function axisLabel(timeSec, tf) {
  if (!timeSec) return "";
  const p = istParts(timeSec);
  if (tf === "1d") return p.date.slice(5);
  return p.time === "09:15" ? p.date.slice(5) : p.time;
}
function istToMs(dateStr, timeStr) {
  if (!dateStr) return null;
  let hh = 9, mm = 15;
  if (timeStr) { const [a, b] = timeStr.split(":"); hh = Number(a); mm = Number(b || 0); }
  const [y, m, d] = dateStr.split("-").map(Number);
  return Date.UTC(y, m - 1, d, hh, mm, 0) - IST_OFFSET_MS;
}
const toSec = (ms) => Math.floor(Number(ms) / 1000);

function chartOptions(tf) {
  return {
    layout: { background: { type: ColorType.Solid, color: "#11161D" }, textColor: "#E6EDF7", fontFamily: "IBM Plex Mono, monospace" },
    localization: { timeFormatter: (t) => `${barLabel(t, tf)} IST` },
    grid: { vertLines: { color: "#1B2330" }, horzLines: { color: "#1B2330" } },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: "#263041" },
    timeScale: { borderColor: "#263041", timeVisible: tf !== "1d", secondsVisible: false, tickMarkFormatter: (t) => axisLabel(t, tf) },
    height: 460,
    autoSize: true,
  };
}

// Snap an epoch-sec to the loaded bar whose bucket contains it (so markers /
// the locator attach to a real bar at any timeframe).
function snapToBar(tsSec, barTimes, bucket) {
  if (!barTimes.length) return null;
  let match = null;
  for (const bt of barTimes) {
    if (tsSec >= bt && tsSec < bt + bucket) return bt;
    if (bt > tsSec) { match = match || bt; break; }
    match = bt;
  }
  return match;
}

/**
 * Backtest price chart: the instrument candles with the strategy's trades drawn
 * on top. Timeframe switch (1m–1d), entry/exit markers, focused-trade
 * entry/SL/target price lines, a date/time "go to" locator, and a trade
 * navigator that jumps to any trade's 1m candles. SL/target are reconstructed
 * in index points from the run's spot_target_pts / spot_stop_pts.
 */
export function BacktestChart({ result }) {
  const instrument = String(result?.instrument || "NIFTY").toUpperCase();
  const title = INSTR_LABEL[instrument] || instrument;
  const trades = useMemo(
    () => (result?.trades || []).filter((t) => t.entry_ts != null).sort((a, b) => Number(a.entry_ts) - Number(b.entry_ts)),
    [result],
  );
  const params = result?.params_applied || result?.config?.params || {};
  const tgtPts = Number(params.spot_target_pts ?? 30);
  const stpPts = Number(params.spot_stop_pts ?? 15);

  // When the run exited on the OPTION's own premium levels (exit_mode
  // "option_levels"), the spot target/stop points above are NOT the exit logic —
  // drawing them as index lines is fiction. Detect that mode and join each spot
  // trade to its paired option trade (by the shared spot entry ts) so we can show
  // the real premium Entry/Tgt/SL/Exit instead.
  const optionExitMode = String(result?.option_backtest?.exit_mode || "spot_exit");
  const optionLevelsRun = optionExitMode === "option_levels"
    && Boolean(result?.option_backtest?.option_exit_config?.applied);
  const optionByEntryTs = useMemo(() => {
    const m = new Map();
    for (const ot of (result?.option_backtest?.trades || [])) {
      if (ot.signal_entry_ts != null) m.set(Number(ot.signal_entry_ts), ot);
    }
    return m;
  }, [result]);
  const focusOption = (trade) =>
    optionLevelsRun && trade ? optionByEntryTs.get(Number(trade.entry_ts)) : null;

  // NOTE: must NOT be named `window` — that shadows the global and breaks
  // window.addEventListener / window.innerHeight (the full-screen handler).
  const tradeWindow = useMemo(() => {
    const e = trades.map((t) => Number(t.entry_ts)).filter(Boolean);
    const x = trades.map((t) => Number(t.exit_ts || t.entry_ts)).filter(Boolean);
    return { from: e.length ? Math.min(...e) : null, to: x.length ? Math.max(...x) : null };
  }, [trades]);

  const [timeframe, setTimeframe] = useState("1d");
  const [focusIdx, setFocusIdx] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [legend, setLegend] = useState(null);
  const [locate, setLocate] = useState({ date: "", time: "09:15" });
  const [locateMsg, setLocateMsg] = useState(null);
  const [maximized, setMaximized] = useState(false);
  const [chartHeight, setChartHeight] = useState(460);
  const panelRef = useRef(null);

  // Full screen via the browser Fullscreen API — this resizes the panel WITHOUT
  // restructuring React's tree (no fixed/flex re-render of the live chart, which
  // crashes lightweight-charts). autoSize picks up the container height change.
  // Esc is handled natively by the Fullscreen API.
  useEffect(() => {
    const sync = () => {
      const fs = document.fullscreenElement === panelRef.current;
      setMaximized(fs);
      setChartHeight(fs ? Math.max(360, window.innerHeight - 180) : 460);
    };
    document.addEventListener("fullscreenchange", sync);
    window.addEventListener("resize", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      window.removeEventListener("resize", sync);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleMaximize = () => {
    const el = panelRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.().catch(() => {});
  };

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const markersRef = useRef(null);
  const priceLinesRef = useRef([]);
  const barsRef = useRef([]);
  const tfRef = useRef(timeframe);

  // Window to fetch: focused trade -> ±2 days around it; 1m unfocused -> last 5
  // days (keeps 1m light over long runs); else the whole backtest window.
  const fetchRange = useMemo(() => {
    const f = focusIdx >= 0 ? trades[focusIdx] : null;
    if (f) {
      const e = Number(f.entry_ts); const x = Number(f.exit_ts || f.entry_ts);
      return { start_ts: e - 2 * DAY_MS, end_ts: x + 2 * DAY_MS };
    }
    if (timeframe === "1m" && tradeWindow.to) return { start_ts: tradeWindow.to - 5 * DAY_MS, end_ts: tradeWindow.to };
    return { start_ts: tradeWindow.from, end_ts: tradeWindow.to };
  }, [focusIdx, timeframe, trades, tradeWindow]);

  // Create chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, chartOptions(tfRef.current));
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#2ED47A", downColor: "#FF5D5D", wickUpColor: "#2ED47A", wickDownColor: "#FF5D5D", borderVisible: false,
    });
    markersRef.current = createSeriesMarkers(seriesRef.current, []);
    chart.subscribeCrosshairMove((param) => {
      const bar = param?.seriesData?.get(seriesRef.current);
      if (bar && param.time) setLegend({ time: param.time, ...bar });
    });
    const ro = new ResizeObserver(() => chart.applyOptions({}));
    ro.observe(containerRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, []);

  useEffect(() => {
    tfRef.current = timeframe;
    chartRef.current?.applyOptions(chartOptions(timeframe));
  }, [timeframe]);

  const drawMarkersAndLines = useCallback(() => {
    const bars = barsRef.current;
    if (!bars.length || !markersRef.current) return;
    const barTimes = bars.map((b) => b.time);
    const first = barTimes[0]; const last = barTimes[barTimes.length - 1];
    const bucket = TF_SECONDS[tfRef.current] || 60;

    // Clear old price lines.
    for (const pl of priceLinesRef.current) { try { seriesRef.current.removePriceLine(pl); } catch (e) { /* ignore */ } }
    priceLinesRef.current = [];

    // Keep each trade's number (matches the dropdown + trade list) so markers
    // can be labelled #N.
    const inRange = [];
    trades.forEach((t, i) => {
      const e = toSec(t.entry_ts);
      if (e >= first - bucket && e <= last + bucket) inRange.push({ t, n: i + 1 });
    });
    const entryOnly = inRange.length > 120 && focusIdx < 0;
    // Trade-number labels only when they stay legible — when a trade is focused
    // or few markers are in view. At the dense overview (hundreds of trades) the
    // numbers would be an unreadable wall, so fall back to plain arrows/dots.
    const labelMarkers = focusIdx >= 0 || inRange.length <= 50;

    const markers = [];
    for (const { t, n } of inRange) {
      const isCE = String(t.direction).toUpperCase() === "CE";
      const eBar = snapToBar(toSec(t.entry_ts), barTimes, bucket);
      if (eBar != null) {
        markers.push({
          time: eBar,
          position: isCE ? "belowBar" : "aboveBar",
          color: isCE ? "#2ED47A" : "#FF5D5D",
          shape: isCE ? "arrowUp" : "arrowDown",
          text: labelMarkers ? `#${n} ${t.direction}` : t.direction,
        });
      }
      if (!entryOnly && t.exit_ts != null) {
        const xBar = snapToBar(toSec(t.exit_ts), barTimes, bucket);
        if (xBar != null) {
          markers.push({
            time: xBar,
            position: "aboveBar",
            color: Number(t.pnl_pts) >= 0 ? "#2ED47A" : "#FF5D5D",
            shape: "circle",
            text: labelMarkers ? `#${n}` : undefined,
          });
        }
      }
    }
    // De-dupe by (time, position, shape, text) and sort — keep distinct #N
    // markers; lightweight-charts needs ascending time.
    const seen = new Set();
    const clean = markers
      .filter((m) => { const k = `${m.time}-${m.position}-${m.shape}-${m.text || ""}`; if (seen.has(k)) return false; seen.add(k); return true; })
      .sort((a, b) => a.time - b.time);
    markersRef.current.setMarkers(clean);

    // Focused trade: entry / target / stop price lines on the index.
    const f = focusIdx >= 0 ? trades[focusIdx] : null;
    if (f && seriesRef.current) {
      const isCE = String(f.direction).toUpperCase() === "CE";
      const entry = Number(f.entry_price);
      const mk = (price, color, title2) => seriesRef.current.createPriceLine({
        price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: title2,
      });
      priceLinesRef.current.push(mk(entry, "#9FB2CC", "Entry"));
      // Spot target/stop lines are the real exit logic ONLY for spot-mirror runs.
      // For premium-level runs they'd be fictitious on an index chart, so we draw
      // just Entry + the actual Exit (the premium levels show in the focus strip).
      if (!focusOption(f)) {
        const target = isCE ? entry + tgtPts : entry - tgtPts;
        const stop = isCE ? entry - stpPts : entry + stpPts;
        priceLinesRef.current.push(mk(target, "#2ED47A", "Target"));
        priceLinesRef.current.push(mk(stop, "#FF5D5D", "Stop"));
      }
      if (f.exit_price != null) priceLinesRef.current.push(mk(Number(f.exit_price), "#5AA9FF", "Exit"));
    }
    // focusOption is an inline fn closing over optionLevelsRun + optionByEntryTs,
    // both already listed below. Adding focusOption itself would change identity
    // every render → new `load` identity → the load() effect refetches in a loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades, focusIdx, tgtPts, stpPts, optionLevelsRun, optionByEntryTs]);

  const load = useCallback(async () => {
    if (!fetchRange.start_ts || !fetchRange.end_ts) return;
    setLoading(true);
    try {
      const res = await api.warehouseOhlc(instrument, {
        timeframe, start_ts: Math.floor(fetchRange.start_ts), end_ts: Math.ceil(fetchRange.end_ts), include_gaps: false,
      });
      const bars = (res.bars || []).map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
      const seen = new Set(); const clean = [];
      for (const b of bars) { if (!seen.has(b.time)) { seen.add(b.time); clean.push(b); } }
      clean.sort((a, b) => a.time - b.time);
      barsRef.current = clean;
      seriesRef.current?.setData(clean);
      drawMarkersAndLines();
      const f = focusIdx >= 0 ? trades[focusIdx] : null;
      if (f && clean.length) {
        const bucket = TF_SECONDS[timeframe] || 60;
        const from = toSec(f.entry_ts) - bucket * 30;
        const to = toSec(f.exit_ts || f.entry_ts) + bucket * 30;
        chartRef.current?.timeScale().setVisibleRange({ from: Math.max(clean[0].time, from), to: Math.min(clean[clean.length - 1].time, to) });
      } else {
        chartRef.current?.timeScale().fitContent();
      }
      setLegend(clean.length ? clean[clean.length - 1] : null);
      if (!clean.length) toast.message(`No ${title} candles stored for this window`);
    } catch (e) {
      toast.error(`Chart load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [instrument, timeframe, fetchRange, drawMarkersAndLines, focusIdx, trades, title]);

  useEffect(() => { load(); }, [load]);

  const focusTrade = (idx) => {
    if (idx < 0 || idx >= trades.length) return;
    setFocusIdx(idx);
    setTimeframe("1m");
  };
  const clearFocus = () => { setFocusIdx(-1); setTimeframe("1d"); };

  const runLocate = () => {
    const bars = barsRef.current;
    if (!bars.length) { setLocateMsg({ type: "err", text: "No bars loaded." }); return; }
    const ms = istToMs(locate.date, timeframe === "1d" ? "09:15" : locate.time);
    if (ms == null) { setLocateMsg({ type: "err", text: "Pick a date." }); return; }
    const target = Math.floor(ms / 1000);
    const bucket = TF_SECONDS[timeframe] || 60;
    const first = bars[0].time; const lastT = bars[bars.length - 1].time;
    if (target < first - bucket || target > lastT + bucket) {
      setLocateMsg({ type: "err", text: `Outside loaded window (${barLabel(first, timeframe)} – ${barLabel(lastT, timeframe)} IST). Tip: pick a trade or widen.` });
      return;
    }
    const match = snapToBar(target, bars.map((b) => b.time), bucket) ?? lastT;
    const from = Math.max(first, match - bucket * 30); const to = Math.min(lastT, match + bucket * 30);
    if (from < to) chartRef.current?.timeScale().setVisibleRange({ from, to });
    setLocateMsg({ type: "ok", text: `Jumped to ${barLabel(match, timeframe)} IST.` });
  };

  const lg = legend; const lgUp = lg && lg.close >= lg.open;
  const focus = focusIdx >= 0 ? trades[focusIdx] : null;

  return (
    <div
      ref={panelRef}
      className="rounded-lg border border-line bg-bg-1 overflow-auto"
      data-testid="backtest-chart-panel"
    >
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
        <LineChart className="w-4 h-4 text-info" />
        <div className="text-sm font-semibold text-foreground" data-testid="backtest-chart-title">{title}</div>
        <span className="text-[11px] text-dimmer">strategy trades on price</span>
        <div className="flex items-center gap-1 ml-auto">
          {TIMEFRAMES.map((tf) => (
            <Button
              key={tf}
              size="sm"
              variant="secondary"
              onClick={() => setTimeframe(tf)}
              className={`h-7 px-2 text-xs border ${tf === timeframe ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-2 text-dim"}`}
              data-testid={`backtest-chart-tf-${tf}`}
            >
              {tf}
            </Button>
          ))}
          <Button
            size="icon"
            variant="secondary"
            onClick={toggleMaximize}
            className="h-7 w-7 border border-line bg-bg-2 text-dim ml-1"
            title={maximized ? "Exit full screen (Esc)" : "Maximize chart (full screen)"}
            aria-label={maximized ? "Exit full screen" : "Maximize chart"}
            data-testid="backtest-chart-maximize"
          >
            {maximized ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </Button>
        </div>
      </div>

      {/* Trade navigator + go-to locator */}
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap text-[11px]">
        <span className="text-dim">Trade</span>
        <Button size="icon" variant="secondary" className="h-7 w-7" onClick={() => focusTrade(focusIdx <= 0 ? 0 : focusIdx - 1)} disabled={!trades.length} data-testid="backtest-chart-prev" title="Previous trade">
          <ChevronLeft className="w-3.5 h-3.5" />
        </Button>
        <select
          value={focusIdx}
          onChange={(e) => { const v = Number(e.target.value); v < 0 ? clearFocus() : focusTrade(v); }}
          className="h-7 rounded-md border border-line bg-bg-2 px-2 text-[11px] text-foreground max-w-[260px]"
          data-testid="backtest-chart-trade-select"
        >
          <option value={-1}>All trades (overview)</option>
          {trades.map((t, i) => (
            <option key={i} value={i}>
              #{i + 1} · {barLabel(toSec(t.entry_ts), "5m")} · {t.direction} · {fmtPnL(t.pnl_pts)}pts
            </option>
          ))}
        </select>
        <Button size="icon" variant="secondary" className="h-7 w-7" onClick={() => focusTrade(focusIdx < 0 ? 0 : Math.min(trades.length - 1, focusIdx + 1))} disabled={!trades.length} data-testid="backtest-chart-next" title="Next trade">
          <ChevronRight className="w-3.5 h-3.5" />
        </Button>
        {focus && (
          <Button size="sm" variant="secondary" className="h-7 text-xs" onClick={clearFocus} data-testid="backtest-chart-clear-focus">
            <X className="w-3 h-3 mr-1" /> Clear
          </Button>
        )}

        <span className="text-dim ml-2">Go to (IST)</span>
        <Input type="date" value={locate.date} onChange={(e) => setLocate((p) => ({ ...p, date: e.target.value }))} className="bg-bg-2 border-line h-7 w-36 text-xs" data-testid="backtest-chart-goto-date" />
        <Input type="time" value={locate.time} disabled={timeframe === "1d"} onChange={(e) => setLocate((p) => ({ ...p, time: e.target.value }))} className="bg-bg-2 border-line h-7 w-24 text-xs disabled:opacity-40" data-testid="backtest-chart-goto-time" />
        <Button size="sm" onClick={runLocate} className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="backtest-chart-goto-button">
          <Crosshair className="w-3 h-3 mr-1" /> Go
        </Button>
        {locateMsg && <span className={`font-mono ${locateMsg.type === "err" ? "text-rose-300" : "text-emerald-300"}`}>{locateMsg.text}</span>}
      </div>

      {focus && (() => {
        const fo = focusOption(focus);
        return (
          <div className="px-3 py-1.5 border-b border-line text-[11px] font-mono flex flex-wrap gap-x-3 gap-y-0.5" data-testid="backtest-chart-focus-detail">
            <span className="text-dim">#{focusIdx + 1}</span>
            <span className={focus.direction === "CE" ? "text-emerald-300" : "text-rose-300"}>{focus.direction}</span>
            {fo ? (
              <>
                <span className="text-sky-300">premium exit</span>
                <span><span className="text-dimmer">Entry ₹</span> {fmtNum(fo.entry_option_price, 2)}</span>
                <span><span className="text-dimmer">Exit ₹</span> {fo.exit_option_price != null ? fmtNum(fo.exit_option_price, 2) : "—"}</span>
                <span className="text-emerald-300"><span className="text-dimmer">Tgt ₹</span> {fo.option_target_level != null ? fmtNum(fo.option_target_level, 2) : "—"}</span>
                <span className="text-rose-300"><span className="text-dimmer">SL ₹</span> {fo.option_stop_level != null ? fmtNum(fo.option_stop_level, 2) : "—"}</span>
                <span className="text-dimmer" title="Index entry/exit on the chart; SL/Tgt are premium ₹, not index points">{fo.option_exit_reason || focus.exit_reason}</span>
              </>
            ) : (
              <>
                <span><span className="text-dimmer">Entry</span> {fmtNum(focus.entry_price, 2)}</span>
                <span><span className="text-dimmer">Exit</span> {focus.exit_price != null ? fmtNum(focus.exit_price, 2) : "—"}</span>
                <span className="text-emerald-300"><span className="text-dimmer">Tgt</span> {fmtNum(focus.direction === "CE" ? focus.entry_price + tgtPts : focus.entry_price - tgtPts, 2)}</span>
                <span className="text-rose-300"><span className="text-dimmer">SL</span> {fmtNum(focus.direction === "CE" ? focus.entry_price - stpPts : focus.entry_price + stpPts, 2)}</span>
                <span className="text-dimmer">{focus.exit_reason}</span>
              </>
            )}
            <span className={Number(focus.pnl_pts) >= 0 ? "text-emerald-300" : "text-rose-300"}>{fmtPnL(focus.pnl_pts)} pts</span>
          </div>
        );
      })()}

      <div className="p-3">
        <div className="relative">
          {lg && (
            <div
              className="absolute top-2 left-2 z-20 rounded-md border px-2.5 py-1.5 text-[12px] font-mono pointer-events-none flex flex-wrap gap-x-2 shadow-sm"
              style={{ backgroundColor: "rgba(17,22,29,0.96)", borderColor: "#9FB2CC", color: "#F5F8FF" }}
              data-testid="backtest-chart-legend"
            >
              <span className="font-semibold" style={{ color: "#F5F8FF" }}>{title} · {timeframe} · {barLabel(lg.time, timeframe)} IST</span>
              <span><span style={{ color: "#9FB2CC" }}>O</span> <span style={{ color: "#F5F8FF" }}>{fmtNum(lg.open, 2)}</span></span>
              <span><span style={{ color: "#9FB2CC" }}>H</span> <span style={{ color: "#F5F8FF" }}>{fmtNum(lg.high, 2)}</span></span>
              <span><span style={{ color: "#9FB2CC" }}>L</span> <span style={{ color: "#F5F8FF" }}>{fmtNum(lg.low, 2)}</span></span>
              <span><span style={{ color: "#9FB2CC" }}>C</span> <span style={{ color: lgUp ? "#2ED47A" : "#FF5D5D" }}>{fmtNum(lg.close, 2)}</span></span>
            </div>
          )}
          {loading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg-1/60 text-xs text-dim">Loading {title} {timeframe}…</div>}
          <div ref={containerRef} style={{ width: "100%", height: chartHeight }} data-testid="backtest-chart-canvas" />
        </div>
        <div className="mt-2 text-[10px] text-dimmer font-mono">
          {title} · {timeframe} · ▲/▼ entry (CE/PE) · ● exit · #n = trade number (shown when few in view) · pick a trade for entry/target/stop lines · Axis IST
          {timeframe === "1m" && focusIdx < 0 ? " · 1m shows the last 5 days — pick a trade to inspect older ones" : ""}
        </div>
      </div>
    </div>
  );
}
