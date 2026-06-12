// Upstox OAuth + manual spot ingest (split from pages/DataWarehouse.jsx).
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Download, Link2, RefreshCw, Unplug } from "lucide-react";
import { fmtInt, fmtNum, fmtSigned } from "@/lib/fmt";
import TokenCountdown from "@/components/TokenCountdown";
import { INSTRUMENTS, quoteTimeDisplay } from "./shared";

export function TokenExpiryBadge({ status }) {
  return <TokenCountdown status={status} variant="badge" />;
}

export function UpstoxPanel({ status, busy, ingesting, form, setForm, ingestJob, onConnect, onDisconnect, onIngest, onQuote, quote, quoteLoading }) {
  const configured = status?.configured;
  const connected = status?.connected && !status?.expired;
  const statusClass = connected
    ? "bg-emerald-950 text-emerald-200 border-emerald-900"
    : configured
      ? "bg-amber-950 text-amber-200 border-amber-900"
      : "bg-rose-950 text-rose-200 border-rose-900";
  const statusText = connected ? "connected" : configured ? "ready for OAuth" : "needs credentials";

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="upstox-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Link2 className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Upstox Broker Data</div>
        <div className="ml-auto flex items-center gap-2">
          <TokenExpiryBadge status={status} />
          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${statusClass}`} data-testid="upstox-status-badge">
            {statusText}
          </span>
        </div>
      </div>
      <div className="p-3 grid grid-cols-1 xl:grid-cols-[1fr_2fr] gap-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="text-sm font-semibold">OAuth</div>
          <div className="text-[11px] text-dim mt-1">
            {connected
              ? `Token stored${status.expires_at ? ` · expires ${status.expires_at}` : ""}`
              : configured
                ? "Broker keys loaded. Connect after the Upstox redirect URL is registered."
                : "Add broker keys in backend/.env and restart backend."}
          </div>
          <div className="flex gap-2 mt-3">
            <Button
              size="sm"
              onClick={onConnect}
              disabled={busy || !configured}
              className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2 flex-1"
              data-testid="upstox-connect-button"
            >
              <Link2 className="w-3 h-3 mr-1" />
              {connected ? "Reconnect" : "Connect Upstox"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={onDisconnect}
              disabled={busy || !status?.connected}
              className="h-7 text-xs"
              data-testid="upstox-disconnect-button"
            >
              <Unplug className="w-3 h-3 mr-1" />
              Disconnect
            </Button>
          </div>
          <div className="mt-3 rounded-md border border-line bg-bg-1 p-2" data-testid="upstox-live-quote-card">
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Live market snapshot</div>
                <div className="mt-1 text-xs font-mono">
                  {quote?.underlying === form.instrument && quote?.last_price != null
                    ? `${quote.underlying} ${fmtNum(quote.last_price, 2)}`
                    : "No quote loaded"}
                </div>
              </div>
              <Button
                size="sm"
                variant="secondary"
                onClick={onQuote}
                disabled={quoteLoading || !connected}
                className="h-7 text-xs"
                data-testid="upstox-live-quote-button"
              >
                <RefreshCw className="w-3 h-3 mr-1" />
                {quoteLoading ? "Reading..." : "Quote"}
              </Button>
            </div>
            {quote?.underlying === form.instrument && (
              <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-dim">
                <div>
                  <span className="text-dimmer">Change</span>
                  <span className="ml-1 font-mono">{fmtSigned(quote.net_change, 2)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Time</span>
                  <span className="ml-1 font-mono">{quoteTimeDisplay(quote)}</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.instrument}
                onChange={(e) => set("instrument", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="upstox-instrument-select"
              >
                {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              From
              <Input
                type="date"
                value={form.from_date}
                onChange={(e) => set("from_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="upstox-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="upstox-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Chunk
              <Input
                type="number"
                min="1"
                max="30"
                placeholder="Auto"
                value={form.chunk_days}
                onChange={(e) => set("chunk_days", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="upstox-chunk-days"
              />
            </label>
            <Button
              size="sm"
              onClick={onIngest}
              disabled={ingesting || !connected}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="upstox-ingest-button"
            >
              <Download className="w-3 h-3 mr-1" />
              {ingesting ? "Fetching..." : "Ingest"}
            </Button>
          </div>
          <div className="mt-2 text-[11px] text-dim" data-testid="upstox-chunk-guidance">
            Auto is recommended for index candles. It uses conservative broker calls for one instrument; use 1-3 after failures, or 14-30 only when you want faster but heavier requests.
          </div>
          <div className="mt-2 rounded-md border border-line bg-bg-1 p-2 text-[11px] text-dim" data-testid="upstox-large-import-help">
            Routine updates are automatic (Sync / auto-update) — use this form only to seed history beyond the rolling 9-month scope.
            Large imports run in background: leave Chunk as Auto, click Ingest once, keep this page open for progress, then run Data Trust Audit for the same date range.
          </div>
          {ingestJob && (
            <div className="mt-2 rounded-md border border-line bg-bg-1 p-2" data-testid="upstox-ingest-progress">
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span className="font-mono uppercase text-dim">{ingestJob.status}</span>
                <span className="font-mono text-dimmer">{fmtNum(ingestJob.progress_pct || 0, 1)}%</span>
              </div>
              <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
                <div
                  className="h-full bg-info transition-all"
                  style={{ width: `${Math.min(100, Math.max(0, Number(ingestJob.progress_pct || 0)))}%` }}
                />
              </div>
              <div className="mt-2 grid grid-cols-2 lg:grid-cols-4 gap-2 text-[11px] text-dim">
                <div>
                  <span className="text-dimmer">Chunks</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.completed_chunks || 0)}/{fmtInt(ingestJob.total_chunks || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Fetched</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.total_fetched || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Added</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.candles_added || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Matched</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.matched_existing || 0)}</span>
                </div>
              </div>
              {Boolean(ingestJob.failed_chunks?.length) && (
                <div className="mt-2 text-[11px] text-danger">
                  Failed chunks: {fmtInt(ingestJob.failed_chunks.length)}. Lower Chunk to 1-3 and retry the same date range.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
