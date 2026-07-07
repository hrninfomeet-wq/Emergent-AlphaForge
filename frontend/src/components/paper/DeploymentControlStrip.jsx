import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { fmtNum, colorPnL } from "@/lib/fmt";
import { Pause, Play, Square, OctagonX, Activity, SlidersHorizontal } from "lucide-react";
import { deploymentLiveness } from "@/lib/deploymentLiveness";
import { getApiErrorMessage } from "@/lib/apiError";

const inr = (v) => (v == null ? "—" : `₹${fmtNum(v, 0)}`);

// Per-deployment paper caps editor — Live-deploy parity (lots/signal override,
// max concurrent positions, daily loss cap ₹, max trades/day). Empty = no cap.
function CapsEditor({ dep, onSaved }) {
  const risk = dep.risk || {};
  const caps = risk.daily_caps || {};
  const capital = risk.capital || {};
  const [lots, setLots] = useState(risk.lots_override != null ? String(risk.lots_override) : "");
  const [maxConc, setMaxConc] = useState(risk.max_concurrent != null ? String(risk.max_concurrent) : "");
  const [maxLoss, setMaxLoss] = useState(caps.max_loss != null ? String(caps.max_loss) : "");
  const [maxTrades, setMaxTrades] = useState(caps.max_trades != null ? String(caps.max_trades) : "");
  const [capAmount, setCapAmount] = useState(capital.amount != null ? String(capital.amount) : "");
  const [capBasis, setCapBasis] = useState(capital.basis || "fixed");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const dailyCaps =
        maxLoss !== "" || maxTrades !== ""
          ? {
              enabled: true,
              ...(maxLoss !== "" ? { max_loss: Math.abs(parseFloat(maxLoss)) } : {}),
              ...(maxTrades !== "" ? { max_trades: parseInt(maxTrades, 10) } : {}),
            }
          : null;
      await api.putPaperCaps(dep.id, {
        lots_override: lots !== "" ? parseInt(lots, 10) : null,
        max_concurrent: maxConc !== "" ? parseInt(maxConc, 10) : null,
        daily_caps: dailyCaps,
        capital: capAmount !== "" ? { amount: Math.abs(parseFloat(capAmount)), basis: capBasis } : null,
      });
      toast.success("Paper caps saved");
      onSaved?.();
    } catch (e) {
      toast.error(`Caps save failed: ${getApiErrorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const Field = ({ label, value, onChange, placeholder }) => (
    <label className="flex flex-col gap-0.5 text-[10px] text-dimmer">
      {label}
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        inputMode="numeric"
        className="h-6 w-24 rounded border border-line bg-bg-2 px-1.5 text-[11px] font-mono text-foreground" />
    </label>
  );

  return (
    <div className="px-3 pb-2 flex items-end gap-3 flex-wrap bg-bg-0 border-t border-line/60 pt-2"
      data-testid="paper-caps-editor">
      <Field label="Lots / signal (override)" value={lots} onChange={setLots} placeholder="preset sizing" />
      <Field label="Max concurrent" value={maxConc} onChange={setMaxConc} placeholder="∞" />
      <Field label="Daily loss cap ₹" value={maxLoss} onChange={setMaxLoss} placeholder="off" />
      <Field label="Max trades / day" value={maxTrades} onChange={setMaxTrades} placeholder="∞" />
      <Field label="Capital ₹ (entry gate)" value={capAmount} onChange={setCapAmount} placeholder="unconstrained" />
      <label className="flex flex-col gap-0.5 text-[10px] text-dimmer">
        Capital basis
        <select value={capBasis} onChange={(e) => setCapBasis(e.target.value)} disabled={capAmount === ""}
          className="h-6 rounded border border-line bg-bg-2 px-1 text-[11px] text-foreground disabled:opacity-50"
          data-testid="paper-caps-capital-basis">
          <option value="fixed">fixed</option>
          <option value="cumulative">cumulative</option>
        </select>
      </label>
      <Button size="sm" variant="outline" disabled={saving} onClick={save}
        className="h-6 text-[11px]" data-testid="paper-caps-save">
        Save caps
      </Button>
      <span className="text-[10px] text-dimmer">
        Empty = no cap. Lots override replaces the preset's sizing replay. Capital skips (and journals)
        entries whose premium outlay doesn't fit.
      </span>
    </div>
  );
}

// One row of the Live Deployments strip: status dot + strategy/name, live open
// count + MTM, and the Pause/Resume + Stop controls.
function DeploymentControlRow({ dep, open, busy, feedHealth, onPause, onResume, onStop, onCapsSaved }) {
  const status = String(dep.status || "").toUpperCase();
  const isActive = status === "ACTIVE";
  const isPaused = status === "PAUSED";
  const live = deploymentLiveness(dep, feedHealth);
  const openCount = open?.openCount || 0;
  const openMtm = open?.openMtm || 0;
  const [capsOpen, setCapsOpen] = useState(false);
  const hasCaps = !!(dep.risk?.lots_override != null || dep.risk?.max_concurrent != null || dep.risk?.daily_caps || dep.risk?.capital);
  return (
    <div data-testid="paper-deploy-row-wrap">
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="paper-deploy-row">
      <span className={`w-2 h-2 rounded-full shrink-0 ${live.dot}`} title={live.tooltip} />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[200px]" title={dep.name}>{dep.name || dep.id?.slice(0, 8) || "—"}</div>
        <div className="text-[10px] text-dimmer truncate max-w-[200px]" title={dep.strategy_id}>{dep.strategy_id || "—"}</div>
      </div>
      <span className={`ml-2 text-[11px] uppercase tracking-wider ${live.text}`} title={live.tooltip}>{live.label}</span>
      <span className="ml-3 text-[11px] font-mono text-dim whitespace-nowrap">
        {openCount} open · MTM <span className={colorPnL(openMtm)}>{inr(openMtm)}</span>
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => setCapsOpen(!capsOpen)}
          className={`h-7 text-xs ${hasCaps ? "text-info" : "text-dim"} hover:text-foreground`}
          data-testid="paper-deploy-caps" title="Lots / concurrency / daily caps (Live-deploy parity)">
          <SlidersHorizontal className="w-3 h-3 mr-1" /> Caps
        </Button>
        {isActive && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onPause(dep)}
            className="h-7 text-xs text-warning" data-testid="paper-deploy-pause">
            <Pause className="w-3 h-3 mr-1" /> Pause
          </Button>
        )}
        {isPaused && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResume(dep)}
            className="h-7 text-xs text-emerald-300 hover:text-emerald-200" data-testid="paper-deploy-resume">
            <Play className="w-3 h-3 mr-1" /> Resume
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={busy} onClick={() => onStop(dep)}
          className="h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-deploy-stop">
          <Square className="w-3 h-3 mr-1" /> Stop
        </Button>
      </div>
    </div>
    {capsOpen && <CapsEditor dep={dep} onSaved={onCapsSaved} />}
    </div>
  );
}

// Live Deployments control strip: master Stop-all + per-deployment Pause/Resume/Stop.
// Presentational only — the page owns the doPause/doResume/doStop/doStopAll handlers.
export default function DeploymentControlStrip({ liveDeployments, perDeployOpen, busy, feedHealth, onPause, onResume, onStop, onStopAll, onCapsSaved }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-deploy-strip">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Activity className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Live Deployments</div>
        <span className="text-[11px] text-dimmer">pause / resume / stop · squares off open positions</span>
        <Button variant="outline" size="sm" disabled={busy || liveDeployments.length === 0} onClick={onStopAll}
          className="ml-auto h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-stop-all"
          title="Square off every open position and pause all active strategies">
          <OctagonX className="w-3.5 h-3.5 mr-1" /> Stop ALL paper trading
        </Button>
      </div>
      {liveDeployments.length === 0 ? (
        <div className="px-3 py-3 text-[11px] text-dimmer">No active deployments.</div>
      ) : (
        <div className="divide-y divide-line">
          {liveDeployments.map((dep) => (
            <DeploymentControlRow
              key={dep.id}
              dep={dep}
              open={perDeployOpen[dep.id]}
              busy={busy}
              feedHealth={feedHealth}
              onPause={onPause}
              onResume={onResume}
              onStop={onStop}
              onCapsSaved={onCapsSaved}
            />
          ))}
        </div>
      )}
    </div>
  );
}
