import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Play, RefreshCw, Square, Wifi, WifiOff } from "lucide-react";
import { api, API } from "@/lib/api";

const PRIMARY_FALLBACK = [
  "NIFTY 50",
  "SENSEX",
  "BANKNIFTY",
  "GOLD FUT",
  "BTCUSD",
  "USDINR",
  "GIFT NIFTY",
  "MIDCPNIFTY",
];

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  const digits = Math.abs(number) >= 1000 ? 2 : 4;
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits >= 4 ? 2 : 2,
    maximumFractionDigits: digits,
  }).format(number);
}

function formatSigned(value, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${formatNumber(number)}${suffix}`;
}

function toneFor(item) {
  const change = Number(item?.change ?? 0);
  if (item?.status !== "ok") return "text-dimmer";
  if (change > 0) return "text-emerald-400";
  if (change < 0) return "text-red-400";
  return "text-foreground";
}

function updatedLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export default function MarketHeader() {
  const [snapshot, setSnapshot] = useState({ items: [] });
  const [streamStatus, setStreamStatus] = useState(null);
  const [streamBusy, setStreamBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [globalOpen, setGlobalOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let eventSource = null;
    let pollInterval = null;

    async function loadStreamStatus() {
      try {
        const stream = await api.upstoxStreamStatus();
        if (!cancelled) setStreamStatus(stream);
      } catch (err) {
        if (!cancelled) setStreamStatus(null);
      }
    }

    async function loadSnapshotOnce() {
      try {
        const data = await api.marketHeader();
        if (!cancelled) {
          setSnapshot(data || { items: [] });
          setError("");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err?.response?.data?.detail || err?.message || "Market header unavailable");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    function startPollingFallback() {
      if (pollInterval) return;
      pollInterval = window.setInterval(loadSnapshotOnce, 1000);
    }

    function stopPollingFallback() {
      if (pollInterval) {
        window.clearInterval(pollInterval);
        pollInterval = null;
      }
    }

    function startSSE() {
      if (typeof EventSource === "undefined") {
        startPollingFallback();
        return;
      }
      try {
        eventSource = new EventSource(`${API}/market/header/stream`);
        eventSource.addEventListener("snapshot", (evt) => {
          if (cancelled) return;
          try {
            const data = JSON.parse(evt.data);
            setSnapshot(data || { items: [] });
            setError("");
            setLoading(false);
            stopPollingFallback();
          } catch (parseErr) {
            // Ignore malformed events; SSE will keep flowing.
          }
        });
        eventSource.onerror = () => {
          // Browser auto-reconnects EventSource. While disconnected, fall back to polling.
          if (cancelled) return;
          startPollingFallback();
        };
      } catch (err) {
        startPollingFallback();
      }
    }

    // Kick off in parallel.
    loadSnapshotOnce();
    loadStreamStatus();
    startSSE();
    // Refresh stream status every 5s — it changes infrequently and is small.
    const statusTimer = window.setInterval(loadStreamStatus, 5000);

    return () => {
      cancelled = true;
      if (eventSource) eventSource.close();
      stopPollingFallback();
      window.clearInterval(statusTimer);
    };
  }, []);

  async function toggleStream() {
    setStreamBusy(true);
    try {
      const data = streamStatus?.running
        ? await api.stopUpstoxStream()
        : await api.startUpstoxStream({ mode: "full", persist_ticks: true });
      setStreamStatus(data);
      const nextSnapshot = await api.marketHeader();
      setSnapshot(nextSnapshot || { items: [] });
      setError("");
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Upstox stream unavailable");
    } finally {
      setStreamBusy(false);
    }
  }

  const { primary, global } = useMemo(() => {
    const items = Array.isArray(snapshot?.items) ? snapshot.items : [];
    return {
      primary: items.filter((item) => item.group === "primary"),
      global: items.filter((item) => item.group === "global"),
    };
  }, [snapshot]);

  const primaryItems = primary.length
    ? primary
    : PRIMARY_FALLBACK.map((label) => ({ key: label, label, group: "primary", status: "loading" }));
  const okCount = [...primary, ...global].filter((item) => item.status === "ok").length;
  const liveTickMode = snapshot?.source_mode === "live_ticks" || streamStatus?.running;
  const statusText = loading ? "loading" : error ? "offline" : liveTickMode ? "live ticks" : `${okCount}/${primary.length + global.length || primaryItems.length} quotes`;
  const StatusIcon = error ? WifiOff : Wifi;

  return (
    <section
      data-testid="market-header"
      className="border-b border-line bg-bg-1/95 px-3 py-2"
      aria-label="Market header"
    >
      <div className="flex items-center gap-2 text-[11px] text-dimmer">
        <StatusIcon className={`h-3.5 w-3.5 ${error ? "text-red-400" : "text-emerald-400"}`} />
        <span className="font-mono uppercase tracking-wide">{statusText}</span>
        <span className="hidden sm:inline">{liveTickMode ? "Upstox WebSocket" : "API fallback"}</span>
        <button
          type="button"
          onClick={toggleStream}
          disabled={streamBusy}
          className="ml-1 inline-flex h-6 items-center gap-1 rounded border border-line bg-bg-2 px-2 text-[10px] font-mono uppercase text-dim hover:text-foreground disabled:opacity-50"
          data-testid="market-header-stream-toggle"
          title={streamStatus?.running ? "Stop Upstox tick stream" : "Start Upstox tick stream"}
        >
          {streamStatus?.running ? <Square className="h-3 w-3" /> : <Play className="h-3 w-3" />}
          {streamStatus?.running ? "Stop" : "Stream"}
        </button>
        {snapshot?.updated_at && <span className="ml-auto font-mono">Updated {updatedLabel(snapshot.updated_at)}</span>}
        {loading && <RefreshCw className="h-3.5 w-3.5 animate-spin text-info" />}
      </div>

      <div
        data-testid="market-header-primary"
        className="mt-2 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(132px,1fr))]"
      >
        {primaryItems.map((item) => (
          <MarketTile key={item.key || item.label} item={item} compact={primaryItems.length > 6} />
        ))}
      </div>

      <button
        type="button"
        data-testid="market-header-global-toggle"
        onClick={() => setGlobalOpen((value) => !value)}
        className="mt-2 flex h-7 items-center gap-1.5 text-xs text-dim hover:text-foreground"
        aria-expanded={globalOpen}
      >
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${globalOpen ? "rotate-180" : ""}`} />
        <span>Global Markets {global.length || ""}</span>
      </button>

      {globalOpen && (
        <div
          data-testid="market-header-global"
          className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(128px,1fr))]"
        >
          {global.map((item) => (
            <MarketTile key={item.key || item.label} item={item} compact />
          ))}
        </div>
      )}
    </section>
  );
}

function MarketTile({ item, compact = false }) {
  const tone = toneFor(item);
  const source = item?.status === "error" ? "unavailable" : item?.source || item?.status || "";

  return (
    <article className="min-w-0 rounded-md border border-line bg-bg-2 px-2.5 py-2">
      <div className="truncate text-[12px] font-semibold text-foreground">{item.label || item.key}</div>
      <div className={`${compact ? "text-[15px]" : "text-[16px]"} mt-1 font-mono font-semibold ${tone}`}>
        {formatNumber(item.last_price)}
      </div>
      <div className={`mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] font-mono ${tone}`}>
        <span>{formatSigned(item.change)}</span>
        <span>{formatSigned(item.change_pct, "%")}</span>
      </div>
      <RangeBar item={item} />
      <div className="mt-1 truncate text-[10px] text-dimmer">{source}</div>
    </article>
  );
}

/**
 * Day low→high range bar with a marker for the current/last price.
 *
 * Renders only when the quote carries a valid day high/low spread. The marker
 * position is the last price interpolated between low and high; an up/down
 * caret reflects the change direction. Degrades silently (renders nothing)
 * when high/low are unavailable, e.g. a partial/legacy WS tick.
 */
function RangeBar({ item }) {
  const low = Number(item?.low);
  const high = Number(item?.high);
  const last = Number(item?.last_price);
  const ok =
    item?.status === "ok" &&
    Number.isFinite(low) &&
    Number.isFinite(high) &&
    Number.isFinite(last) &&
    high > low;
  if (!ok) return null;

  const pct = Math.max(0, Math.min(100, ((last - low) / (high - low)) * 100));
  const change = Number(item?.change ?? 0);
  const markerColor = change > 0 ? "bg-emerald-400" : change < 0 ? "bg-red-400" : "bg-foreground";
  const Caret = change < 0 ? ChevronDown : ChevronUp;
  const caretColor = change > 0 ? "text-emerald-400" : change < 0 ? "text-red-400" : "text-dimmer";

  return (
    <div className="mt-1.5" data-testid="market-tile-range" title={`Day range ${formatNumber(low)} – ${formatNumber(high)}`}>
      <div className="relative h-1.5 rounded-full bg-bg-3 overflow-hidden">
        {/* low→current fill */}
        <div
          className={`absolute inset-y-0 left-0 ${change < 0 ? "bg-red-500/40" : "bg-emerald-500/40"}`}
          style={{ width: `${pct}%` }}
        />
        {/* current-price marker */}
        <div
          className={`absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 -translate-x-1/2 ${markerColor}`}
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="mt-0.5 flex items-center justify-between text-[9px] font-mono text-dimmer leading-none">
        <span title="Day low">L {formatNumber(low)}</span>
        <Caret className={`h-3 w-3 ${caretColor}`} />
        <span title="Day high">H {formatNumber(high)}</span>
      </div>
    </div>
  );
}
