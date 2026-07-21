import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Zap } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/apiError";
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
  const [safetyLoaded, setSafetyLoaded] = useState(false);
  const [safetyError, setSafetyError] = useState(false);
  // Evidence is advisory after explicit user consent.  We still load the exact
  // failed checks so the irreversible decision is informed and auditable.
  const [armAdvisories, setArmAdvisories] = useState([]);
  const [forwardValidation, setForwardValidation] = useState(null);
  const [validationLoaded, setValidationLoaded] = useState(false);
  const [acceptUnvalidated, setAcceptUnvalidated] = useState(false);

  // Caps form fields
  const [lots, setLots] = useState("1");
  const [maxDay, setMaxDay] = useState("10");
  const [maxConcurrent, setMaxConcurrent] = useState("2");
  const [dailyLossCap, setDailyLossCap] = useState("4000");
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
    setSafetyLoaded(false);
    setSafetyError(false);
    api.getSafetyConfig()
      .then((d) => {
        if (cancelled) return;
        setSafetyConfig(d);
        setSafetyLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setSafetyConfig(null);
        setSafetyError(true);
        setSafetyLoaded(true);
      });
    return () => { cancelled = true; };
  }, [formOpen]);

  // Load live-arm advisories when the panel opens. Purely informational — on
  // failure we just show none (arm_advisories is never required to proceed).
  useEffect(() => {
    if (!formOpen || !dep?.id) return;
    let cancelled = false;
    setArmAdvisories([]);
    setForwardValidation(null);
    setValidationLoaded(false);
    api.deploymentMetrics(dep.id)
      .then((d) => {
        if (cancelled) return;
        setArmAdvisories(d?.arm_advisories || []);
        setForwardValidation(d?.forward_validation || null);
        setValidationLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setArmAdvisories([]);
        setForwardValidation({
          promotion_allowed: false,
          phase: "unavailable",
          failed_checks: ["forward_validation_unavailable"],
        });
        setValidationLoaded(true);
      });
    return () => { cancelled = true; };
  }, [formOpen, dep?.id]);

  // Focus the confirm input when the dialog reaches the confirm step.
  useEffect(() => {
    if (formOpen && confirmOpen && confirmInputRef.current) {
      confirmInputRef.current.focus();
    }
  }, [formOpen, confirmOpen]);

  const maxLots = safetyConfig?.max_lots_per_order ?? null;
  const maxOpen = safetyConfig?.max_open_positions ?? null;
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

  const dailyLossError = dailyLossCapNum == null || !Number.isFinite(dailyLossCapNum) || dailyLossCapNum <= 0
    ? "A positive daily loss cap is required for live execution"
    : null;
  const concurrentError = maxOpen != null && maxConcurrentNum > maxOpen
    ? `Account ceiling is ${maxOpen} open position${maxOpen === 1 ? "" : "s"}`
    : null;
  const promotionReady = Boolean(forwardValidation?.promotion_allowed);
  const unvalidated = validationLoaded && !promotionReady;
  const canProceedToConfirm = validationLoaded && safetyLoaded && !safetyError
    && !lotsError && !concurrentError && !dailyLossError
    && lotsNum >= 1 && maxDayNum >= 1 && maxConcurrentNum >= 1;

  const openForm = () => {
    setConfirmText("");
    setDryRunWarning(false);
    setAcceptUnvalidated(false);
    setSafetyConfig(null);
    setSafetyLoaded(false);
    setSafetyError(false);
    setConfirmOpen(false);   // always open on the caps step
    setFormOpen(true);
  };

  const handleFormSubmit = (e) => {
    e.preventDefault();
    if (!canProceedToConfirm) return;
    setConfirmText("");
    // Advance to the confirm STEP inside the SAME dialog. The caps form and the
    // typed-ENABLE confirm are two VIEWS of ONE Radix Dialog, never two stacked
    // modals — stacking made the second dialog's dismissable layer swallow the
    // submit's own click and close instantly (the "Continue does nothing" bug,
    // C5 in the 2026-07-21 release audit).
    setConfirmOpen(true);
  };

  // "Back" returns to the caps view WITHOUT closing the dialog (entered values
  // are preserved because it is the same open dialog, just a different step).
  const closeConfirmBackToForm = () => {
    setConfirmOpen(false);
    setConfirmText("");
  };

  const handleArm = async () => {
    if (confirmText !== "ENABLE" || (unvalidated && !acceptUnvalidated)) return;
    setBusy(true);
    setDryRunWarning(false);
    try {
      const body = {
        lots: lotsNum,
        max_lots_per_day: maxDayNum,
        max_concurrent: maxConcurrentNum,
        confirm: true,
        accept_unvalidated_live: Boolean(unvalidated && acceptUnvalidated),
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
      const detail = e?.response?.data?.detail;
      if (detail?.code === "explicit_unvalidated_live_consent_required") {
        setForwardValidation(detail.forward_validation || {
          promotion_allowed: false,
          phase: "unavailable",
          failed_checks: ["forward_validation_unavailable"],
        });
        setValidationLoaded(true);
        setAcceptUnvalidated(false);
        toast.error("Forward evidence changed. Review the failed checks and explicitly approve unvalidated live trading to continue.");
        return;
      }
      toast.error(`Enable failed: ${getApiErrorMessage(e)}`);
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
      <Dialog open={formOpen} onOpenChange={(o) => { if (!busy) { setFormOpen(o); if (!o) { setConfirmOpen(false); setConfirmText(""); } } }}>
        <DialogContent className={`max-w-sm bg-bg-1 ${confirmOpen ? "border-danger/60" : "border-line"}`}>
          {!confirmOpen && (
          <>
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

          {validationLoaded && (
            <div className={`rounded-md border px-3 py-2 text-xs ${promotionReady
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
              : "border-danger/50 bg-danger/10 text-danger"}`}
              data-testid="live-forward-validation-status">
              {promotionReady ? (
                <span><strong>Forward validated.</strong> The evidence policy permits capital promotion.</span>
              ) : (
                <div className="space-y-1">
                  <div><strong>Unvalidated live candidate.</strong> Evidence warnings do not prevent an explicit user-authorized deployment.</div>
                  <div className="font-mono text-[10px] break-words">
                    Failed: {(forwardValidation?.failed_checks || ["evidence unavailable"]).join(", ")}
                  </div>
                </div>
              )}
            </div>
          )}

          {safetyError && (
            <div className="rounded-md border border-danger/50 bg-danger/10 px-3 py-2 text-xs text-danger">
              Account safety ceilings could not be loaded. Live activation is disabled until this check succeeds.
            </div>
          )}

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
                {maxOpen != null && (
                  <span className="ml-2 text-warning">ceiling: {maxOpen}</span>
                )}
              </label>
              <Input
                type="number"
                min={1}
                max={maxOpen ?? undefined}
                value={maxConcurrent}
                onChange={(e) => setMaxConcurrent(e.target.value)}
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-max-concurrent"
                required
              />
              {concurrentError && <p className="text-[10px] text-danger mt-1">{concurrentError}</p>}
            </div>

            {/* Daily loss cap — capital gate, always required */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-dimmer mb-1 block">
                Daily loss cap ₹ <span className="text-warning">(required)</span>
              </label>
              <Input
                type="number"
                min={1}
                // step="any" — NOT step={100}. With min={1}, step={100} makes the
                // ONLY natively-valid values 1,101,201,…,4001, so the default 4000
                // (and every round rupee amount a user types) fails HTML5 step
                // validation. That silently blocks the <form> submit — the submit
                // event never fires, handleFormSubmit never runs, and "Continue"
                // does nothing even though the button looks enabled (C5, 2026-07-21).
                step="any"
                value={dailyLossCap}
                onChange={(e) => setDailyLossCap(e.target.value)}
                placeholder="e.g. 4000"
                className="bg-bg-2 border-line h-8 text-xs"
                data-testid="live-caps-loss"
                required
              />
              {dailyLossError && <p className="text-[10px] text-danger mt-1">{dailyLossError}</p>}
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
                  min={0.1}
                  step="any"
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
                  min={0.1}
                  step="any"
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
          </>
          )}

          {/* ── Step 2: danger typed-confirm (same dialog, second view) ──────── */}
          {confirmOpen && (
          <>
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
            <div className="rounded-md border border-line bg-bg-2 px-3 py-2 text-[11px] space-y-1">
              <div>Strategy: <span className="text-foreground">{dep.strategy_id || "—"}</span></div>
              <div>Instrument: <span className="text-foreground">{dep.instrument || "—"}</span></div>
              <div>Option selection: <span className="text-foreground">{(dep.option_policy?.moneyness || []).join("/").toUpperCase() || "ATM"} · DTE {(dep.option_policy?.dte_filter || []).join(",") || "all"}</span></div>
            </div>
            {unvalidated && (
              <label className="flex items-start gap-2 rounded-md border border-danger/60 bg-danger/10 px-3 py-2 text-[11px] text-danger" data-testid="accept-unvalidated-live">
                <input
                  type="checkbox"
                  checked={acceptUnvalidated}
                  onChange={(e) => setAcceptUnvalidated(e.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0"
                />
                <span>
                  <strong>Yes, I explicitly approve unvalidated real-money trading.</strong>{" "}
                  I understand the failed checks ({(forwardValidation?.failed_checks || ["evidence unavailable"]).join(", ")}) and accept the loss risk.
                </span>
              </label>
            )}
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
              onClick={closeConfirmBackToForm}
              disabled={busy}
              className="h-8 text-xs flex-1"
            >
              Back
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={confirmText !== "ENABLE" || busy || (unvalidated && !acceptUnvalidated)}
              onClick={handleArm}
              className="h-8 text-xs flex-1 bg-danger text-white hover:bg-danger/80 disabled:opacity-40"
              data-testid="deploy-to-live-arm-submit"
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Zap className="w-3.5 h-3.5 mr-1" />}
              {busy ? "Enabling…" : "ENABLE — Go Live"}
            </Button>
          </div>
          </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
