import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtNum, fmtInt } from "@/lib/fmt";
import { Search } from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function OHLC({ candle }) {
  if (!candle) return <span className="text-dimmer">—</span>;
  return (
    <span className="font-mono">
      O <span className="text-foreground">{fmtNum(candle.open, 2)}</span>{"  "}
      H <span className="text-foreground">{fmtNum(candle.high, 2)}</span>{"  "}
      L <span className="text-foreground">{fmtNum(candle.low, 2)}</span>{"  "}
      C <span className="text-foreground font-semibold">{fmtNum(candle.close, 2)}</span>
    </span>
  );
}

/**
 * Point-in-time warehouse lookup.
 *
 * Pick an index, date, and time (IST) and see what the LOCAL warehouse stored
 * for that minute: the spot candle, the derived ATM strike, the resolved
 * expiry, and the ATM CE/PE option candles. Lets you cross-check stored data
 * against a real broker terminal. Reads only the warehouse, never the broker.
 */
export default function WarehouseLookup() {
  const [form, setForm] = useState({ instrument: "NIFTY", date: todayIso(), time: "10:00" });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const set = (k, v) => setForm((p) => ({ ...p, [k]: v }));

  const run = async () => {
    if (!form.date) {
      toast.error("Pick a date");
      return;
    }
    setLoading(true);
    try {
      const res = await api.warehouseLookup(form.instrument, form.date, form.time);
      setResult(res);
      if (!res.spot) toast.warning("No spot candle stored at that minute");
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Lookup failed: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  const legCard = (side) => {
    const leg = result?.legs?.[side];
    const label = side === "CE" ? "ATM Call (CE)" : "ATM Put (PE)";
    return (
      <div className="rounded-md border border-line bg-bg-1 p-2.5" data-testid={`lookup-leg-${side.toLowerCase()}`}>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[11px] font-semibold">{label}</span>
          {leg?.strike != null && (
            <span className="text-[10px] font-mono text-dimmer">{fmtInt(leg.strike)} {leg.expiry}</span>
          )}
        </div>
        {leg?.available ? (
          <>
            <div className="text-[11px]"><OHLC candle={leg.candle} /></div>
            <div className="mt-1 text-[10px] text-dimmer font-mono">
              OI {fmtInt(leg.candle?.oi || 0)} · vol {fmtInt(leg.candle?.volume || 0)}
              {!leg.exact && <span className="text-warning"> · nearest bar</span>}
            </div>
          </>
        ) : (
          <div className="text-[11px] text-dimmer">{leg?.reason === "contract_metadata_missing" ? "No contract metadata for this strike/expiry" : "No stored candle at this minute"}</div>
        )}
      </div>
    );
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-lookup-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Search className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Spot &amp; ATM Option Lookup</div>
      </div>
      <div className="p-3 space-y-3">
        <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_0.8fr_auto] gap-2 items-end">
          <label className="text-[11px] text-dim">
            Instrument
            <select
              value={form.instrument}
              onChange={(e) => set("instrument", e.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
              data-testid="lookup-instrument"
            >
              {INSTRUMENTS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-dim">
            Date (IST)
            <Input type="date" value={form.date} onChange={(e) => set("date", e.target.value)} className="mt-1 bg-bg-1 border-line" data-testid="lookup-date" />
          </label>
          <label className="text-[11px] text-dim">
            Time (IST)
            <Input type="time" value={form.time} onChange={(e) => set("time", e.target.value)} className="mt-1 bg-bg-1 border-line" data-testid="lookup-time" />
          </label>
          <Button size="sm" onClick={run} disabled={loading} className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="lookup-search-button">
            <Search className="w-3 h-3 mr-1" />
            {loading ? "Looking…" : "Look up"}
          </Button>
        </div>
        <div className="text-[11px] text-dimmer">
          Reads stored warehouse data only (no broker call). Use it to cross-check a timestamped value against your trading terminal.
        </div>

        {result && (
          <div className="space-y-2" data-testid="lookup-result">
            <div className="rounded-md border border-line bg-bg-2 p-2.5">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[11px] font-semibold">{result.underlying} spot</span>
                <span className="text-[10px] font-mono text-dimmer">
                  {result.spot?.ist_time || result.target_ist}
                  {result.spot && !result.spot_exact && <span className="text-warning"> · nearest bar</span>}
                </span>
              </div>
              {result.spot ? (
                <div className="flex flex-wrap items-center gap-3 text-[11px]">
                  <OHLC candle={result.spot} />
                  {result.atm_strike != null && (
                    <span className="font-mono text-dimmer">ATM <span className="text-foreground font-semibold">{fmtInt(result.atm_strike)}</span></span>
                  )}
                </div>
              ) : (
                <div className="text-[11px] text-dimmer">No spot candle stored for this minute.</div>
              )}
            </div>

            {result.spot && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {legCard("CE")}
                {legCard("PE")}
              </div>
            )}

            {(result.notes || []).length > 0 && (
              <div className="rounded-md border border-amber-900 bg-amber-950/30 p-2 text-[11px] text-warning space-y-0.5">
                {result.notes.map((n, i) => <div key={i}>• {n}</div>)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
