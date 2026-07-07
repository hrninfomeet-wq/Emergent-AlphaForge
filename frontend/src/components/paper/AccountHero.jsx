import { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { Pencil, Check, X } from "lucide-react";
import { fmtINR, fmtINRSigned, fmtPct } from "@/lib/fmt";

function Stat({ label, value, tone = null }) {
  const cls = tone == null ? "" : Number(tone) > 0 ? "text-success" : Number(tone) < 0 ? "text-danger" : "";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono tabular-nums mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}

export default function AccountHero({ analytics, startingCapital, capitalConfig, onSetCapital, busy }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(startingCapital ?? 200000));
  const [enforceDraft, setEnforceDraft] = useState(Boolean(capitalConfig?.enforce_capital));
  const [basisDraft, setBasisDraft] = useState(capitalConfig?.capital_basis || "fixed");
  if (!analytics) return null;
  const a = analytics;
  const curve = (a.equity_curve || []).map((p) => ({ day: p.day, equity: p.equity_value }));
  const save = () => {
    const v = Number(draft);
    if (!Number.isFinite(v) || v <= 0) return;
    onSetCapital(v, { enforce_capital: enforceDraft, capital_basis: basisDraft });
    setEditing(false);
  };
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="paper-account-hero">
      <div className="flex justify-between flex-wrap gap-3 items-start">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Account value (realized)</div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-mono tabular-nums">{fmtINR(a.account_value_realized)}</span>
            <span className={`text-sm font-mono ${a.total_return_pct >= 0 ? "text-success" : "text-danger"}`}>{fmtPct(a.total_return_pct, 2)}</span>
          </div>
          <div className="text-[11px] text-dimmer flex items-center gap-1">
            start {editing ? (
              <span className="inline-flex items-center gap-1.5 flex-wrap">
                <input value={draft} onChange={(e) => setDraft(e.target.value)} type="number"
                  className="h-6 w-24 bg-bg-2 border border-line rounded px-1 text-[11px]" data-testid="paper-capital-input" />
                <label className="inline-flex items-center gap-1 text-[10px] text-dimmer"
                  title="When on, a new paper trade (any deployment) must fit inside this capital — skipped and journaled otherwise.">
                  <input type="checkbox" checked={enforceDraft} onChange={(e) => setEnforceDraft(e.target.checked)}
                    className="h-3 w-3 rounded border-line" data-testid="paper-capital-enforce" />
                  account-wide entry ceiling
                </label>
                {enforceDraft && (
                  <select value={basisDraft} onChange={(e) => setBasisDraft(e.target.value)}
                    className="h-6 rounded border border-line bg-bg-2 px-1 text-[10px]" data-testid="paper-capital-basis">
                    <option value="fixed">fixed</option>
                    <option value="cumulative">cumulative</option>
                  </select>
                )}
                <button onClick={save} disabled={busy} className="text-success" title="Save"><Check className="w-3.5 h-3.5" /></button>
                <button onClick={() => setEditing(false)} className="text-dimmer" title="Cancel"><X className="w-3.5 h-3.5" /></button>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1">
                {fmtINR(a.starting_capital)}
                {capitalConfig?.enforce_capital && (
                  <span className="text-[10px] px-1 py-0.5 rounded border border-violet-500/40 text-violet-300"
                    title={`Account-wide entry ceiling ON (${capitalConfig?.capital_basis || "fixed"} basis): new paper trades across all deployments must fit inside the starting capital.`}
                    data-testid="paper-capital-enforced-badge">
                    ceiling {capitalConfig?.capital_basis || "fixed"}
                  </span>
                )}
                <button onClick={() => {
                  setDraft(String(a.starting_capital));
                  setEnforceDraft(Boolean(capitalConfig?.enforce_capital));
                  setBasisDraft(capitalConfig?.capital_basis || "fixed");
                  setEditing(true);
                }} className="text-dimmer hover:text-foreground" title="Edit starting capital" data-testid="paper-capital-edit"><Pencil className="w-3 h-3" /></button>
              </span>
            )}
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-2">
          <Stat label="Open P&L" value={fmtINRSigned(a.open_pnl)} tone={a.open_pnl} />
          <Stat label="Live MTM" value={fmtINR(a.account_value_mtm)} tone={a.account_value_mtm - a.starting_capital} />
          <Stat label="Deployed in market" value={fmtINR(a.deployed_capital)} />
          <Stat label="Max drawdown" value={fmtINR(a.max_drawdown_value)} tone={a.max_drawdown_value} />
        </div>
      </div>
      <div className="h-[150px] mt-2" data-testid="paper-equity-curve">
        {curve.length >= 2 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={curve} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-success)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--color-success)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "var(--text-3)" }} width={48}
                tickFormatter={(v) => `₹${Math.round(v / 1000)}k`} />
              <Tooltip formatter={(v) => fmtINR(v)} labelFormatter={(l) => l}
                contentStyle={{ background: "var(--bg-2)", border: "1px solid var(--border-1)", fontSize: 11 }} />
              <Area type="monotone" dataKey="equity" stroke="var(--color-success)" strokeWidth={1.5} fill="url(#eq)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-[11px] text-dimmer font-mono pt-6">No closed trades yet — equity curve appears as trades close.</div>
        )}
      </div>
    </div>
  );
}
