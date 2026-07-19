import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Zap } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * DeployToLivePanel — "Enable live execution" action for a single deployment.
 *
 * Opens a caps form (lots/signal, max_lots_per_day, max_concurrent,
 * daily_loss_cap) validated against the account ceiling from getSafetyConfig().
 * The form's submit opens a DANGER typed-confirm dialog: the user must type
 * ENABLE exactly before the enable call goes through.
 *
 * There is no per-session arm ceremony: authorization is simply
 * deployment.mode === "live". Once enabled, the deployment trades on its own
 * strategy logic across sessions until explicitly disabled — this panel (and
 * its caps form) is the only place those caps and the catastrophe band are
 * ever set, so the backend refuses to go live without them.
 *
 * After enabling:
 *   - If autoplace_armed === false: a prominent warning is shown (entries are
 *     still dry-run pending LIVE_AUTOPLACE_ARMED=1 — the auto-exit guard
 *     always transmits regardless).
 *   - On success: onArmed() callback fires so the parent can refresh.
 *
 * Props:
 *   dep        – deployment object { id, name, strategy_id, … }
 *   onArmed    – called after a successful enable (parent refreshes the strip)
 */
export default function DeployToLivePanel({ dep, onArmed }) {
  // ── Phase 1: caps form ─────────────────────────────────────────────────────
  const [formOpen, setFormOpen] = useState(false);
  const [safetyConfig, setSafetyConfig] = useState(null);
  // Non-blocking live-arm advisories (S19/B8) — e.g. thin forward record, or (for
  // premium_momentum multi-leg deployments) the edge-hunt verdict. Advisory only:
  // a fetch failure degrades silently, never blocks the arm flow.
  const [armAdvisories, setArmAdvisories] = useState([]);

  // Caps form fields
  const [lots, setLots] = useState("1");
  const [maxDay, setMaxDay] = useState("10");
  const [maxConcurrent, setMaxConcurrent] = useState("2");
  const [dailyLossCap, setDailyLossCap] = useState("");
  // PC-down OCO backstop (catastrophe band) — optional; blank → backend default.
  const [catStopPct, setCatStopPct] = useState("");
  const [catTargetPct, setCatTargetPct] = useState("");

  // ── Phase 2: danger typed-confirm ─────────────────────────────────────────
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [dryRunWarning, setDryRunWarning] = useState(false);
  const confirmInputRef = useRef(null);

  // Load safety config when the form opens so we can show the ceiling.
  useEffect(() => {
    if (!formOpen) return;
    let cancelled = false;
    api.getSafetyConfig()
      .then((d) => { if (!cancelled) setSafetyConfig(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [formOpen]);

  // Load live-arm advisories when the panel opens. Purely informational — on
  // failure we just show none (arm_advisories is never required to proceed).
  useEffect(() => {
    if (!formOpen || !dep?.id) return;
    let cancelled = false;
    setArmAdvisories([]);
    api.deploymentMetrics(dep.id)
      .then((d) => { if (!cancelled) setArmAdvisories(d?.arm_advisories || []); })
      .catch(() => { if (!cancelled) setArmAdvisories([]); });
    return () => { cancelled = true; };
  }, [formOpen, dep?.id]);

  // Focus the confirm input when the danger dialog opens.
  useEffect(() => {
    if (confirmOpen && confirmInputRef.current) {
      confirmInputRef.current.focus();
    }
  }, [confirmOpen]);

  const maxLots = safetyConfig?.max_lots_per_order ?? null;
  const lotsNum = Math.max(1, parseInt(lots, 10) || 1);
  const maxDayNum = Math.max(1, parseInt(maxDay, 10) || 1);
  const maxConcurrentNum = Math.max(1, parseInt(maxConcurrent, 10) || 1);
  const dailyLossCapNum = dailyLossCap.trim() !== "" ? parseFloat(dailyLossCap) : null;
  // Catastrophe-band inputs: only forward a finite number; blank/NaN → omit (backend default).
  const catStopPctNum = catStopPct.trim() !== "" && Number.isFinite(parseFloat(catStopPct))
    ? parseFloat(catStopPct) : null;
  const catTargetPctNum = catTargetPct.trim() !== "" && Number.isFinite(parseFloat(catTargetPct))
    ? parseFloat(catTargetPct) : null;

  const lotsError = maxLots != null && lotsNum > maxLots
    ? `Account ceiling is ${maxLots} lot${maxLots === 1 ? "" : "s"}/order`
    : null;

  const canProceedToConfirm = !lotsError && lotsNum >= 1 && maxDayNum >= 1 && maxConcurrentNum >= 1;

  const openForm = () => {
    setConfirmText("");
    setDryRunWarning(false);
    setFormOpen(true);
  };

  const handleFormSubmit = (e) => {
    e.preventDefault();
    if (!canProceedToConfirm) return;
    setConfirmText("");
    setConfirmOpen(true);
  };

  const handleArm = async () => {
    if (confirmText !== "ENABLE") return;
    setBusy(true);
    setDryRunWarning(false);
    try {
      const body = {
        lots: lotsNum,
        max_lots_per_day: maxDayNum,
        max_concurrent: maxConcurrentNum,
        confirm: true,
        ...(dailyLossCapNum != null ? { daily_loss_cap: dailyLossCapNum } : {}),
        // PC-down OCO backstop — only sent when the operator entered a value;
        // a blank field omits the key so the backend default band applies.
        ...(catStopPctNum != null ? { catastrophe_stop_pct: catStopPctNum } : {}),
        ...(catTargetPctNum != null ? { catastrophe_target_pct: catTargetPctNum } : {}),
      };
      const res = await api.enableDeploymentLive(dep.id, body);
      setConfirmOpen(false);
      setFormOpen(false);
      if (res?.autoplace_armed === false) {
        setDryRunWarning(true);
      } else {
        toast.success(`"${dep.name || dep.id}" — live execution enabled`);
      }
      onArmed?.();
    } catch (e) {
      toast.error(`Enable failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const depLabel = dep.name || dep.id?.slice(0, 8) || "deployment";

  return (
    <>
      {/* Dry-run warning — shown after arm when autoplace_armed=false */}
      {dryRunWarning && (
        <div className="mt-1 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            Backend dry-run only — set <code className="font-mono text-warning">LIVE_AUTOPLACE_ARMED=1</code> to transmit real orders.
          </span>
        </div>
      )}

      {/* Trigger button */}
      <Button
        variant="outline"
        size="sm"
        onClick={openForm}
        className="h-7 text-xs border-danger/40 text-danger hover:text-danger/80 hover:bg-danger/10"
        data-testid="deploy-to-live-open"
      >
        <Zap className="w-3 h-3 mr-1" />
        Enable Live Execution
      </Button>

      {/* ── Caps form dialog ──────────────────────────────────────────────── */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-sm bg-bg-1 border-line">
          <DialogHeader>
            <DialogTitle className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Zap className="w-4 h-4 text-danger" />
              Enable Live Execution · {depLabel}
            </DialogTitle>
          </DialogHeader>

          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-warning flex items-start gap-2">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              Live orders transmit <strong>real money</strong> trades to Flattrade. Set conservative caps — these cannot be changed while live (disable and re-enable to change them).
            </span>
          </div>

          <form onSubmit={handleFormSubmit} className="space-y-3">
            {/* Lots per signal */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                Lots / signal
                {maxLots != null && (
                  <span className="ml-2 text-warning">ceiling: {maxLots}</span>
                )}
              </label>
              <Input
                type="number"
                min={1}
                max={maxLots ?? undefined}
                value={lots}
                onChange={(e) => setLots(e.target.value)}
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-lots"
                required
              />
              {lotsError && (
                <p className="text-[10px] text-danger mt-1">{lotsError}</p>
              )}
            </div>

            {/* Max lots/day */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                Max lots / day
              </label>
              <Input
                type="number"
                min={1}
                value={maxDay}
                onChange={(e) => setMaxDay(e.target.value)}
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-max-day"
                required
              />
            </div>

            {/* Max concurrent */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                Max concurrent positions
              </label>
              <Input
                type="number"
                min={1}
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(e.target.value)}
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-max-concurrent"
                required
              />
            </div>

            {/* Daily loss cap (optional) */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                Daily loss cap ₹ <span className="text-dimmer/60">(optional — leave blank to disable)</span>
              </label>
              <Input
                type="number"
                min={0}
                step={100}
                value={dailyLossCap}
                onChange={(e) => setDailyLossCap(e.target.value)}
                placeholder="e.g. 5000"
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-loss"
              />
            </div>

            {/* PC-down OCO backstop (catastrophe band) — optional overrides */}
            <div className="rounded-md border border-line bg-bg-2/40 px-3 py-2 space-y-3">
              <p className="text-[10px] uppercase tracking-wider text-dimmer">
                PC-down OCO backstop{" "}
                <span className="text-dimmer/60 normal-case tracking-normal">
                  — resting broker OCO if the PC/guard is down (optional)
                </span>
              </p>
              {/* Catastrophe stop % */}
              <div>
                <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                  Catastrophe stop % <span className="text-dimmer/60">(blank → default)</span>
                </label>
                <Input
                  type="number"
                  min={0}
                  step={0.5}
                  value={catStopPct}
                  onChange={(e) => setCatStopPct(e.target.value)}
                  placeholder="default ~50"
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="live-caps-cat-stop"
                />
              </div>
              {/* Catastrophe target % */}
              <div>
                <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                  Catastrophe target % <span className="text-dimmer/60">(blank → default)</span>
                </label>
                <Input
                  type="number"
                  min={0}
                  step={0.5}
                  value={catTargetPct}
                  onChange={(e) => setCatTargetPct(e.target.value)}
                  placeholder="default ~135"
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="live-caps-cat-target"
                />
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setFormOpen(false)}
                className="h-8 text-xs flex-1"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={!canProceedToConfirm}
                className="h-8 text-xs flex-1 bg-danger/20 border border-danger/40 text-danger hover:bg-danger/30"
              >
                Continue →
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* ── Danger typed-confirm dialog ───────────────────────────────────── */}
      <Dialog open={confirmOpen} onOpenChange={(o) => { if (!busy) { setConfirmOpen(o); setConfirmText(""); } }}>
        <DialogContent className="max-w-sm bg-bg-1 border-danger/60">
          <DialogHeader>
            <DialogTitle className="text-sm font-semibold text-danger flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              Enable Live Execution
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-3 text-xs text-dim">
            <p>
              You are about to go live on <strong className="text-foreground">{depLabel}</strong> — it will
              trade <strong className="text-foreground">real money</strong> on its own strategy logic from
              now on, across sessions, via Flattrade, until you disable it.
            </p>
            <div className="rounded-md border border-line bg-bg-2 px-3 py-2 font-mono text-[11px] space-y-1">
              <div>Lots/signal: <span className="text-foreground">{lotsNum}</span></div>
              <div>Max lots/day: <span className="text-foreground">{maxDayNum}</span></div>
              <div>Max concurrent: <span className="text-foreground">{maxConcurrentNum}</span></div>
              {dailyLossCapNum != null && (
                <div>Daily loss cap: <span className="text-foreground">₹{dailyLossCapNum.toLocaleString()}</span></div>
              )}
              {catStopPctNum != null && (
                <div>Catastrophe stop: <span className="text-foreground">{catStopPctNum}%</span></div>
              )}
              {catTargetPctNum != null && (
                <div>Catastrophe target: <span className="text-foreground">{catTargetPctNum}%</span></div>
              )}
            </div>
            <p className="text-dimmer">
              Deployed entries go live as NRML with a resting OCO backstop; the catastrophe band is auto-widened to stay clear of the software guard stop.
            </p>
            <p className="text-dimmer">
              Type <strong className="text-danger font-mono">ENABLE</strong> below to go live for{" "}
              <em>{depLabel}</em>.
            </p>
            <Input
              ref={confirmInputRef}
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="Type ENABLE to confirm"
              className="bg-bg-2 border-danger/40 h-9 text-sm font-mono tracking-widest"
              data-testid="deploy-to-live-confirm-input"
              disabled={busy}
              onKeyDown={(e) => { if (e.key === "Enter" && confirmText === "ENABLE") handleArm(); }}
            />
          </div>

          {/* Non-blocking arm advisories (S19/B8) — thin/negative forward record,
              or (premium_momentum multi-leg) the edge-hunt verdict. Advisory only. */}
          {armAdvisories.length > 0 && (
            <div className="space-y-1.5" data-testid="arm-advisories">
              {armAdvisories.map((adv) => (
                <div
                  key={adv.id}
                  className={`flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-[11px] ${
                    adv.severity === "danger"
                      ? "border-danger/40 bg-danger/10 text-danger"
                      : "border-amber-500/40 bg-amber-500/10 text-warning"
                  }`}
                  data-testid={`arm-advisory-${adv.id}`}
                >
                  <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />
                  <span>{adv.message}</span>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => { setConfirmOpen(false); setConfirmText(""); }}
              disabled={busy}
              className="h-8 text-xs flex-1"
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={confirmText !== "ENABLE" || busy}
              onClick={handleArm}
              className="h-8 text-xs flex-1 bg-danger text-white hover:bg-danger/80 disabled:opacity-40"
              data-testid="deploy-to-live-arm-submit"
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Zap className="w-3.5 h-3.5 mr-1" />}
              {busy ? "Enabling…" : "ENABLE — Go Live"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
