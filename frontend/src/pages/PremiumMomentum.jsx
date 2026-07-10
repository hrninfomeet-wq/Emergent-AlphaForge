import { useState } from "react";
import { apiClient, LONG_TIMEOUT_MS } from "@/lib/api";
import { toast } from "sonner";
import { Play, Loader2, AlertTriangle } from "lucide-react";

/**
 * PremiumMomentum — the AlgoTest-style premium-momentum option-buying backtest
 * (Phase 1+1.1+2 of the Contingency Breakout blueprint).
 *
 * Per session: at the reference time the chosen-moneyness CE/PE strikes are
 * LOCKED from spot; the first side whose OPTION PREMIUM rises by the momentum
 * threshold enters; exits on premium stop (gap-honest intra-bar fills) /
 * target / stepped X-Y trail / EOD. Coverage-gated: any session whose locked
 * strike lacks warehouse candles is EXCLUDED and counted — never mis-filled.
 *
 * HONESTY NOTES (also rendered in the UI): 1-minute bars (entries on bar-close
 * cross, an approximation of tick engines); NO cost model yet (brokerage/
 * spread/STT would make real results worse); premium points per 1 unit, not
 * rupees x lot.
 */

const inputCls =
  "text-xs px-2 py-1.5 rounded-md bg-bg-2 border border-line text-foreground focus:outline-none focus:ring-1 focus:ring-info w-full";
const labelCls = "text-[10px] uppercase tracking-wider text-dimmer mb-1 block";

const DEFAULTS = {
  instrument: "NIFTY",
  start_date: "2026-01-01",
  end_date: "2026-07-09",
  reference_time: "09:31",
  moneyness: "itm1",
  side: "first_to_trigger",
  momentum_unit: "pct",
  momentum_value: "15",
  stop_unit: "pct",
  stop_value: "20",
  target_unit: "pct",
  target_value: "",
  trail_x: "",
  trail_y: "",
  // Cost model (engine schedule): ON by default — a gross backtest flatters.
  costs_enabled: true,
  spread_pct: "1.0",
  brokerage: "0",
  lots: "1",
  // Tuner grids (comma lists). Tuning REQUIRES costs on (backend 400s otherwise).
  tune_momentum: "10,15,20,25",
  tune_stop: "10,20,30",
  tune_target: "none,30,50",
  tune_trail_x: "",
  tune_trail_y: "",
  train_frac: "0.7",
};

function parseGridList(s, { allowNone = false } = {}) {
  const out = [];
  for (const part of String(s || "").split(",")) {
    const v = part.trim();
    if (!v) continue;
    if (allowNone && /^(none|null|-)$/i.test(v)) { out.push(null); continue; }
    const n = Number(v);
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

function dateToMs(d, endOfDay) {
  if (!d) return null;
  // IST (UTC+5:30) midnight / end-of-day for the chosen calendar date.
  const t = new Date(`${d}T${endOfDay ? "23:59:00" : "00:00:00"}+05:30`);
  return t.getTime();
}

function num(v) {
  const n = Number(v);
  return String(v).trim() !== "" && Number.isFinite(n) ? n : null;
}

export default function PremiumMomentum() {
  const [cfg, setCfg] = useState(DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [tuneBusy, setTuneBusy] = useState(false);
  const [tuneResult, setTuneResult] = useState(null);
  const [tuneError, setTuneError] = useState(null);

  const set = (k) => (e) => setCfg((c) => ({ ...c, [k]: e.target.value }));

  function baseParams() {
    const params = {
      reference_time: cfg.reference_time || "09:31",
      moneyness: cfg.moneyness,
      side: cfg.side,
      lots: Math.max(1, Number(cfg.lots) || 1),
      cost_config: cfg.costs_enabled
        ? {
            enabled: true,
            spread_pct_of_premium: Number(cfg.spread_pct) || 0,
            brokerage_per_order: Number(cfg.brokerage) || 0,
          }
        : { enabled: false },
    };
    return params;
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const params = baseParams();
      const mom = num(cfg.momentum_value);
      if (mom != null) params[cfg.momentum_unit === "pts" ? "momentum_pts" : "momentum_pct"] = mom;
      const stop = num(cfg.stop_value);
      if (stop != null) params[cfg.stop_unit === "pts" ? "stop_pts" : "stop_pct"] = stop;
      const tgt = num(cfg.target_value);
      if (tgt != null) params[cfg.target_unit === "pts" ? "target_pts" : "target_pct"] = tgt;
      const tx = num(cfg.trail_x);
      const ty = num(cfg.trail_y);
      if (tx != null && ty != null) {
        params.trail_x = tx;
        params.trail_y = ty;
      }
      const body = {
        instrument: cfg.instrument,
        start_ts: dateToMs(cfg.start_date, false),
        end_ts: dateToMs(cfg.end_date, true),
        params,
      };
      const res = await apiClient.post("/premium-momentum/backtest", body, { timeout: LONG_TIMEOUT_MS });
      setResult(res.data);
      const n = (res.data.trades || []).length;
      toast.success(`Backtest done — ${n} trade${n === 1 ? "" : "s"}`);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "Backtest failed");
    } finally {
      setBusy(false);
    }
  }

  async function runTune() {
    setTuneBusy(true);
    setTuneError(null);
    try {
      const grid = {};
      const gm = parseGridList(cfg.tune_momentum);
      if (gm.length) grid.momentum_pct = gm;
      const gs = parseGridList(cfg.tune_stop);
      if (gs.length) grid.stop_pct = gs;
      const gt = parseGridList(cfg.tune_target, { allowNone: true });
      if (gt.length) grid.target_pct = gt;
      const gx = parseGridList(cfg.tune_trail_x);
      if (gx.length) grid.trail_x = gx;
      const gy = parseGridList(cfg.tune_trail_y);
      if (gy.length) grid.trail_y = gy;
      const body = {
        instrument: cfg.instrument,
        start_ts: dateToMs(cfg.start_date, false),
        end_ts: dateToMs(cfg.end_date, true),
        base_params: baseParams(),
        grid,
        train_frac: Math.min(0.9, Math.max(0.5, Number(cfg.train_frac) || 0.7)),
      };
      const res = await apiClient.post("/premium-momentum/tune", body, { timeout: LONG_TIMEOUT_MS });
      setTuneResult(res.data);
      toast.success(`Tune done — ${res.data.n_configs} configs`);
    } catch (e) {
      setTuneError(e?.response?.data?.detail || e?.message || "Tune failed");
    } finally {
      setTuneBusy(false);
    }
  }

  const trades = result?.trades || [];
  const cov = result?.coverage;
  const pnl = trades.map((t) => t.premium_pnl);
  const wins = pnl.filter((p) => p > 0);
  const total = pnl.reduce((a, b) => a + b, 0);
  const byMonth = {};
  for (const t of trades) {
    const m = t.session_date.slice(0, 7);
    byMonth[m] = byMonth[m] || { n: 0, sum: 0 };
    byMonth[m].n += 1;
    byMonth[m].sum += t.premium_pnl;
  }

  return (
    <div className="p-4 space-y-4" data-testid="premium-momentum-page">
      <div className="flex flex-col lg:flex-row gap-4">
        {/* Setup */}
        <div className="lg:w-80 shrink-0 rounded-lg border border-line bg-bg-1 p-3 space-y-3" data-testid="pm-setup">
          <div className="text-[10px] uppercase tracking-wider text-dim">Premium-Momentum Setup</div>

          <div>
            <label className={labelCls}>Instrument</label>
            <select value={cfg.instrument} onChange={set("instrument")} className={inputCls} data-testid="pm-instrument">
              {["NIFTY", "BANKNIFTY", "SENSEX"].map((i) => <option key={i}>{i}</option>)}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className={labelCls}>Start</label>
              <input type="date" value={cfg.start_date} onChange={set("start_date")} className={inputCls} data-testid="pm-start" />
            </div>
            <div>
              <label className={labelCls}>End</label>
              <input type="date" value={cfg.end_date} onChange={set("end_date")} className={inputCls} data-testid="pm-end" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className={labelCls}>Reference time (IST)</label>
              <input type="time" value={cfg.reference_time} onChange={set("reference_time")} className={inputCls} data-testid="pm-ref-time" />
            </div>
            <div>
              <label className={labelCls}>Moneyness</label>
              <select value={cfg.moneyness} onChange={set("moneyness")} className={inputCls} data-testid="pm-moneyness">
                {["atm", "itm1", "itm2", "otm1", "otm2"].map((m) => <option key={m}>{m}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className={labelCls}>Side</label>
            <select value={cfg.side} onChange={set("side")} className={inputCls} data-testid="pm-side">
              <option value="first_to_trigger">CE + PE — first to trigger</option>
              <option value="ce">CE only</option>
              <option value="pe">PE only</option>
            </select>
          </div>

          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="col-span-2">
              <label className={labelCls}>Momentum entry (premium rise)</label>
              <input value={cfg.momentum_value} onChange={set("momentum_value")} className={inputCls} placeholder="e.g. 15" data-testid="pm-momentum" />
            </div>
            <select value={cfg.momentum_unit} onChange={set("momentum_unit")} className={inputCls} data-testid="pm-momentum-unit">
              <option value="pct">%</option>
              <option value="pts">pts</option>
            </select>
          </div>

          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="col-span-2">
              <label className={labelCls}>Stop loss (on premium)</label>
              <input value={cfg.stop_value} onChange={set("stop_value")} className={inputCls} placeholder="e.g. 20" data-testid="pm-stop" />
            </div>
            <select value={cfg.stop_unit} onChange={set("stop_unit")} className={inputCls} data-testid="pm-stop-unit">
              <option value="pct">%</option>
              <option value="pts">pts</option>
            </select>
          </div>

          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="col-span-2">
              <label className={labelCls}>Target (optional)</label>
              <input value={cfg.target_value} onChange={set("target_value")} className={inputCls} placeholder="blank = none" data-testid="pm-target" />
            </div>
            <select value={cfg.target_unit} onChange={set("target_unit")} className={inputCls} data-testid="pm-target-unit">
              <option value="pct">%</option>
              <option value="pts">pts</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className={labelCls}>Trail X (pts, optional)</label>
              <input value={cfg.trail_x} onChange={set("trail_x")} className={inputCls} placeholder="every X up" data-testid="pm-trail-x" />
            </div>
            <div>
              <label className={labelCls}>Trail Y (pts)</label>
              <input value={cfg.trail_y} onChange={set("trail_y")} className={inputCls} placeholder="raise SL by Y" data-testid="pm-trail-y" />
            </div>
          </div>

          <div className="border-t border-line pt-2 space-y-2">
            <label className="flex items-center gap-2 text-[11px] text-dim">
              <input
                type="checkbox"
                checked={cfg.costs_enabled}
                onChange={(e) => setCfg((c) => ({ ...c, costs_enabled: e.target.checked }))}
                className="h-3 w-3 rounded border-line"
                data-testid="pm-costs-enabled"
              />
              Apply cost model (spread + statutory charges)
            </label>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className={labelCls}>Spread %/side</label>
                <input value={cfg.spread_pct} onChange={set("spread_pct")} className={inputCls} data-testid="pm-spread" />
              </div>
              <div>
                <label className={labelCls}>Brokerage ₹/leg</label>
                <input value={cfg.brokerage} onChange={set("brokerage")} className={inputCls} data-testid="pm-brokerage" />
              </div>
              <div>
                <label className={labelCls}>Lots</label>
                <input value={cfg.lots} onChange={set("lots")} className={inputCls} data-testid="pm-lots" />
              </div>
            </div>
          </div>

          <button
            onClick={run}
            disabled={busy}
            className="w-full inline-flex items-center justify-center gap-2 text-xs font-semibold px-3 py-2 rounded-md bg-info/15 border border-info/50 text-foreground disabled:opacity-50"
            data-testid="pm-run-btn"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {busy ? "Running…" : "Run backtest"}
          </button>

          <div className="text-[10px] text-dimmer leading-relaxed border-t border-line pt-2">
            <b className="text-dim">Honesty notes:</b> 1-minute bars (bar-close entries — a tick engine
            would differ). The cost model (engine schedule: spread/side + STT 0.1% sell + exchange/SEBI/
            stamp/GST) is <b className="text-dim">ON by default</b> — turning it off flatters results.
            Stops fill gap-honestly (intra-bar low touch, min(stop, open)). Sessions without warehouse
            coverage are excluded and counted — never silently mis-filled. Tuning REQUIRES costs on and
            selects on TRAIN only; the test column is out-of-sample.
          </div>
        </div>

        {/* Results */}
        <div className="flex-1 min-w-0 space-y-3">
          {error && (
            <div className="rounded-md border border-rose-900 bg-rose-950/50 p-2 text-[11px] text-rose-200 flex items-start gap-2" data-testid="pm-error">
              <AlertTriangle className="w-4 h-4 shrink-0 text-rose-300" />
              <span className="whitespace-pre-wrap">{String(error)}</span>
            </div>
          )}

          {!result && !error && (
            <div className="rounded-lg border border-line bg-bg-1 p-8 text-center text-sm text-dimmer">
              Configure the premium-momentum rules and run a backtest. The coverage report will show
              exactly how many sessions the result actually rests on.
            </div>
          )}

          {cov && (
            <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="pm-coverage">
              <div className="text-[10px] uppercase tracking-wider text-dim mb-2">Coverage (sample honesty)</div>
              <div className="flex flex-wrap gap-2 text-[11px] font-mono">
                <span className="px-2 py-0.5 rounded bg-bg-2 border border-line">sessions {cov.sessions_total}</span>
                <span className="px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/40 text-emerald-300">traded {cov.sessions_traded}</span>
                <span className={`px-2 py-0.5 rounded border ${cov.sessions_excluded > 0 ? "bg-amber-500/10 border-amber-500/40 text-warning" : "bg-bg-2 border-line"}`}>
                  excluded {cov.sessions_excluded}
                </span>
                <span className="px-2 py-0.5 rounded bg-bg-2 border border-line">no-signal {cov.sessions_no_signal}</span>
                {Object.entries(cov.exclude_reasons || {}).map(([k, v]) => (
                  <span key={k} className="px-2 py-0.5 rounded bg-bg-2 border border-line text-dimmer">{k}: {v}</span>
                ))}
              </div>
              {cov.sessions_excluded > 0 && (
                <div className="text-[10px] text-warning mt-1.5">
                  Excluded sessions shrink the sample — the P&L below rests only on the traded sessions.
                </div>
              )}
            </div>
          )}

          {trades.length > 0 && (
            <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="pm-stats">
              <div className="text-[10px] uppercase tracking-wider text-dim mb-2">Result (premium points / unit — pre-cost)</div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                <div>
                  <div className={`text-lg font-mono font-bold ${total >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{total.toFixed(1)}</div>
                  <div className="text-[10px] text-dimmer">total pts ({trades.length} trades)</div>
                </div>
                <div>
                  <div className={`text-lg font-mono font-bold ${total >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{(total / trades.length).toFixed(2)}</div>
                  <div className="text-[10px] text-dimmer">avg pts / trade</div>
                </div>
                <div>
                  <div className="text-lg font-mono font-bold">{((wins.length / trades.length) * 100).toFixed(1)}%</div>
                  <div className="text-[10px] text-dimmer">win rate</div>
                </div>
                <div>
                  <div className="text-lg font-mono font-bold">
                    {wins.length ? (wins.reduce((a, b) => a + b, 0) / wins.length).toFixed(1) : "—"} / {trades.length - wins.length
                      ? (pnl.filter((p) => p <= 0).reduce((a, b) => a + b, 0) / (trades.length - wins.length)).toFixed(1)
                      : "—"}
                  </div>
                  <div className="text-[10px] text-dimmer">avg win / avg loss</div>
                </div>
              </div>
              {result?.summary && (
                <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-mono" data-testid="pm-net-summary">
                  <span className={`px-2 py-0.5 rounded border ${result.summary.net_pnl_pts >= 0 ? "border-emerald-500/40 text-emerald-300" : "border-rose-500/40 text-rose-300"}`}>
                    NET {result.summary.net_pnl_pts.toFixed(1)} pts
                  </span>
                  <span className={`px-2 py-0.5 rounded border ${result.summary.net_pnl_rupees >= 0 ? "border-emerald-500/40 text-emerald-300" : "border-rose-500/40 text-rose-300"}`}>
                    NET ₹{result.summary.net_pnl_rupees.toLocaleString("en-IN")}
                  </span>
                  <span className="px-2 py-0.5 rounded border border-line text-dimmer">
                    charges ₹{result.summary.charges_rupees.toLocaleString("en-IN")}
                  </span>
                  <span className="px-2 py-0.5 rounded border border-line text-dimmer">
                    {result.summary.lots} lot(s) × {result.summary.lot_size}
                    {result.summary.costs_enabled ? "" : " — COSTS OFF (gross)"}
                  </span>
                </div>
              )}
              <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-mono">
                {Object.entries(byMonth).sort().map(([m, v]) => (
                  <span key={m} className={`px-2 py-0.5 rounded border ${v.sum >= 0 ? "border-emerald-500/40 text-emerald-300" : "border-rose-500/40 text-rose-300"}`}>
                    {m}: {v.sum.toFixed(0)} ({v.n})
                  </span>
                ))}
              </div>
            </div>
          )}

          {trades.length > 0 && (
            <div className="rounded-lg border border-line bg-bg-1 overflow-hidden" data-testid="pm-trades">
              <div className="text-[10px] uppercase tracking-wider text-dim p-3 pb-0">Trades</div>
              <div className="overflow-x-auto max-h-[420px] overflow-y-auto p-3">
                <table className="w-full text-[11px] font-mono">
                  <thead className="text-dimmer text-left">
                    <tr>
                      <th className="pr-3 py-1">session</th>
                      <th className="pr-3">side</th>
                      <th className="pr-3">strike</th>
                      <th className="pr-3">expiry</th>
                      <th className="pr-3 text-right">ref</th>
                      <th className="pr-3 text-right">entry</th>
                      <th className="pr-3 text-right">exit</th>
                      <th className="pr-3">reason</th>
                      <th className="pr-3 text-right">pnl pts</th>
                      <th className="text-right">bars</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-t border-line/50">
                        <td className="pr-3 py-1">{t.session_date}</td>
                        <td className="pr-3">{t.side}</td>
                        <td className="pr-3">{t.strike}</td>
                        <td className="pr-3 text-dimmer">{t.expiry_date || "—"}</td>
                        <td className="pr-3 text-right">{t.ref_premium}</td>
                        <td className="pr-3 text-right">{t.entry_premium}</td>
                        <td className="pr-3 text-right">{t.exit_premium}</td>
                        <td className="pr-3">{t.exit_reason}</td>
                        <td className={`pr-3 text-right ${t.premium_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{t.premium_pnl.toFixed(1)}</td>
                        <td className="text-right">{t.bars_held}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {/* Tuner — honest grid search (train/test split; selects on TRAIN only) */}
          <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 space-y-2" data-testid="pm-tune-panel">
            <div className="text-[10px] uppercase tracking-wider text-purple-400">
              Tune (honest grid — costs required, selects on train, reports out-of-sample)
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
              <div>
                <label className={labelCls}>Momentum % list</label>
                <input value={cfg.tune_momentum} onChange={set("tune_momentum")} className={inputCls} data-testid="pm-tune-momentum" />
              </div>
              <div>
                <label className={labelCls}>Stop % list</label>
                <input value={cfg.tune_stop} onChange={set("tune_stop")} className={inputCls} data-testid="pm-tune-stop" />
              </div>
              <div>
                <label className={labelCls}>Target % list ("none" ok)</label>
                <input value={cfg.tune_target} onChange={set("tune_target")} className={inputCls} data-testid="pm-tune-target" />
              </div>
              <div>
                <label className={labelCls}>Trail X pts list</label>
                <input value={cfg.tune_trail_x} onChange={set("tune_trail_x")} className={inputCls} data-testid="pm-tune-trail-x" />
              </div>
              <div>
                <label className={labelCls}>Train fraction</label>
                <input value={cfg.train_frac} onChange={set("train_frac")} className={inputCls} data-testid="pm-train-frac" />
              </div>
            </div>
            <button
              onClick={runTune}
              disabled={tuneBusy || !cfg.costs_enabled}
              title={cfg.costs_enabled ? "" : "Tuning requires the cost model ON"}
              className="inline-flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-md bg-purple-500/15 border border-purple-500/50 text-foreground disabled:opacity-50"
              data-testid="pm-tune-btn"
            >
              {tuneBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              {tuneBusy ? "Tuning…" : "Run tune"}
            </button>
            {!cfg.costs_enabled && (
              <div className="text-[10px] text-warning">Tuning requires the cost model ON — gross tuning finds edges smaller than the friction it ignores.</div>
            )}
            {tuneError && (
              <div className="rounded-md border border-rose-900 bg-rose-950/50 p-2 text-[11px] text-rose-200 whitespace-pre-wrap" data-testid="pm-tune-error">
                {String(tuneError)}
              </div>
            )}
            {tuneResult && (
              <div className="space-y-2" data-testid="pm-tune-results">
                <div className="text-[11px] text-dimmer font-mono">
                  split: train {tuneResult.split.train_sessions} sessions ({tuneResult.split.train_range?.join(" → ")}) ·
                  test {tuneResult.split.test_sessions} ({tuneResult.split.test_range?.join(" → ")}) · {tuneResult.n_configs} configs
                </div>
                <div className="overflow-x-auto max-h-[340px] overflow-y-auto">
                  <table className="w-full text-[11px] font-mono">
                    <thead className="text-dimmer text-left">
                      <tr>
                        <th className="pr-3 py-1">#</th>
                        <th className="pr-3">params</th>
                        <th className="pr-3 text-right">train n</th>
                        <th className="pr-3 text-right">train net</th>
                        <th className="pr-3 text-right">test n</th>
                        <th className="pr-3 text-right">test net (OOS)</th>
                        <th>verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tuneResult.configs.map((c, i) => (
                        <tr key={i} className="border-t border-line/50">
                          <td className="pr-3 py-1">{i + 1}</td>
                          <td className="pr-3">{Object.entries(c.params).map(([k, v]) => `${k}=${v == null ? "none" : v}`).join(" ")}</td>
                          <td className="pr-3 text-right">{c.train.trades}</td>
                          <td className={`pr-3 text-right ${c.train.net_pnl_pts >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{c.train.net_pnl_pts.toFixed(1)}</td>
                          <td className="pr-3 text-right">{c.test.trades}</td>
                          <td className={`pr-3 text-right ${c.test.net_pnl_pts >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{c.test.net_pnl_pts.toFixed(1)}</td>
                          <td>
                            {c.overfit_warning
                              ? <span className="text-warning">⚠ overfit</span>
                              : c.train.net_pnl_pts > 0 && c.test.net_pnl_pts > 0
                                ? <span className="text-emerald-300">✓ holds OOS</span>
                                : <span className="text-dimmer">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
