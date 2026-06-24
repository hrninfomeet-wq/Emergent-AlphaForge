import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  Loader2,
  RotateCcw,
  Save,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";

/**
 * OverallSettingsPanel — AlgoTest-parity "Overall Controls" for a running basket.
 *
 * These controls are evaluated on the BASKET AGGREGATE (not per leg):
 *   mtm            = Σ leg MTM in ₹
 *   basket_premium = Σ entry premium × qty
 *   premium_pct mode threshold = value/100 × basket_premium
 * Per-leg SL/target still run elsewhere; whichever (leg or overall) hits first
 * wins, and an overall hit squares off the WHOLE basket.
 *
 * Props:
 *   scope — "overall" | "broker_level"  (default "overall"). Passed straight to the
 *           api so one panel can drive either the strategy-wide or broker-wide config.
 *
 * Backend contract (added later; coded against these signatures):
 *   api.getOverallSettings(scope)          → Promise<config>
 *   api.putOverallSettings(scope, config)  → Promise<any>
 *
 * The single shared config object (ALL numbers; "unit"/"mode" pick ₹ vs %):
 * {
 *   sl:      { enabled, mode:"mtm"|"premium_pct", value },
 *   target:  { enabled, mode:"mtm"|"premium_pct", value },
 *   trailing:{ mode:"none"|"lock"|"lock_trail"|"overall_trail",
 *              unit:"mtm"|"premium_pct",
 *              lock_at, lock_floor, trail_per, trail_by, base_sl },
 *   reentry: { enabled, max(<=5), type:"asap"|"momentum", reverse, momentum_pct },
 * }
 */

// ── Default (fully-disabled) config — also the offline fallback ──────────────
function defaultConfig() {
  return {
    sl: { enabled: false, mode: "mtm", value: 0 },
    target: { enabled: false, mode: "mtm", value: 0 },
    trailing: {
      mode: "none",
      unit: "mtm",
      lock_at: 0,
      lock_floor: 0,
      trail_per: 0,
      trail_by: 0,
      base_sl: 0,
    },
    reentry: {
      enabled: false,
      max: 1,
      type: "asap",
      reverse: false,
      momentum_pct: 0,
    },
  };
}

// ── Safe numeric coercion — empty string / NaN → 0 ───────────────────────────
function num(v, fallback = 0) {
  if (v === "" || v === null || v === undefined) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function intClamp(v, lo, hi, fallback = lo) {
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

/**
 * Normalise an arbitrary server payload into the full config shape so the form
 * never reads `undefined` (defensive against a partial / older backend reply).
 */
function normaliseConfig(raw) {
  const d = defaultConfig();
  if (!raw || typeof raw !== "object") return d;
  const sl = raw.sl ?? {};
  const target = raw.target ?? {};
  const trailing = raw.trailing ?? {};
  const reentry = raw.reentry ?? {};
  return {
    sl: {
      enabled: !!sl.enabled,
      mode: sl.mode === "premium_pct" ? "premium_pct" : "mtm",
      value: num(sl.value),
    },
    target: {
      enabled: !!target.enabled,
      mode: target.mode === "premium_pct" ? "premium_pct" : "mtm",
      value: num(target.value),
    },
    trailing: {
      mode: ["lock", "lock_trail", "overall_trail"].includes(trailing.mode)
        ? trailing.mode
        : "none",
      unit: trailing.unit === "premium_pct" ? "premium_pct" : "mtm",
      lock_at: num(trailing.lock_at),
      lock_floor: num(trailing.lock_floor),
      trail_per: num(trailing.trail_per),
      trail_by: num(trailing.trail_by),
      base_sl: num(trailing.base_sl),
    },
    reentry: {
      enabled: !!reentry.enabled,
      max: intClamp(reentry.max, 1, 5, 1),
      type: reentry.type === "momentum" ? "momentum" : "asap",
      reverse: !!reentry.reverse,
      momentum_pct: num(reentry.momentum_pct),
    },
  };
}

const SCOPE_LABEL = { overall: "Strategy-wide", broker_level: "Broker-level" };

const inputCls =
  "bg-bg-2 border border-line rounded-md px-2 py-1.5 text-xs font-mono text-foreground placeholder:text-dimmer focus:outline-none focus:ring-1 focus:ring-info/50 disabled:opacity-50";
const labelCls = "text-[10px] uppercase tracking-wider text-dimmer font-semibold";

// ── Small presentational helpers ────────────────────────────────────────────
function SubSection({ title, badge, children }) {
  return (
    <div className="rounded-lg border border-line bg-bg-2/50 overflow-hidden">
      <div className="px-3 py-2 border-b border-line bg-bg-2/40 flex items-center gap-2">
        <span className="text-xs font-semibold text-foreground">{title}</span>
        {badge}
      </div>
      <div className="px-3 py-3 space-y-3">{children}</div>
    </div>
  );
}

function Toggle({ checked, disabled, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex items-center gap-2 text-xs font-mono disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${
        checked ? "text-emerald-300" : "text-dimmer"
      }`}
    >
      <span
        className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full border transition-colors ${
          checked
            ? "border-emerald-500/60 bg-emerald-500/25"
            : "border-line bg-bg-3"
        }`}
      >
        <span
          className={`inline-block h-3 w-3 rounded-full bg-foreground transition-transform ${
            checked ? "translate-x-3.5" : "translate-x-0.5"
          }`}
        />
      </span>
      {label && <span className="font-semibold">{label}</span>}
    </button>
  );
}

/** Two-option segmented control (e.g. ₹ MTM / % Premium). */
function Segmented({ value, options, disabled, onChange }) {
  return (
    <div className="flex gap-1">
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          disabled={disabled}
          onClick={() => onChange(o.v)}
          className={`flex-1 py-1.5 px-2 text-xs font-mono font-semibold rounded-md border transition-colors disabled:opacity-50 ${
            value === o.v
              ? "border-info/60 bg-info/15 text-info"
              : "border-line bg-bg-2 text-dimmer hover:bg-bg-3"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Labelled number input with safe parsing. */
function NumField({ label, value, onChange, disabled, step = "1", min, placeholder, suffix }) {
  return (
    <div className="flex flex-col gap-1">
      <label className={labelCls}>{label}</label>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          step={step}
          min={min}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={`min-w-0 flex-1 ${inputCls}`}
        />
        {suffix && (
          <span className="shrink-0 text-[10px] font-mono text-dimmer">{suffix}</span>
        )}
      </div>
    </div>
  );
}

const UNIT_OPTIONS = [
  { v: "mtm", label: "₹ MTM" },
  { v: "premium_pct", label: "% Premium" },
];

const TRAIL_MODES = [
  { v: "none", label: "None" },
  { v: "lock", label: "Lock" },
  { v: "lock_trail", label: "Lock & Trail" },
  { v: "overall_trail", label: "Overall Trail SL" },
];

const TRAIL_EXPLAINER = {
  none: "No trailing — only the fixed Overall SL / Target above apply.",
  lock:
    "Once basket MTM reaches Lock-at (Y), the floor locks at Lock-floor (X); exit if MTM falls back below the floor. The floor never drops.",
  lock_trail:
    "Once MTM reaches Lock-at (Y), the floor starts at Lock-floor (X) and ratchets up by Trail-by (B) for every Trail-per (A) of further profit; exit if MTM falls below the floor.",
  overall_trail:
    "Stop starts at −Base SL (S0); every Trail-per (A) of profit raises the stop by Trail-by (B). The stop only ratchets up; exit when MTM ≤ stop.",
};

export default function OverallSettingsPanel({ scope = "overall" }) {
  const scopeKey = scope === "broker_level" ? "broker_level" : "overall";

  const [config, setConfig] = useState(() => defaultConfig());
  const [loaded, setLoaded] = useState(() => defaultConfig()); // last-loaded snapshot (for Reset)
  const [loading, setLoading] = useState(true);
  const [loadNote, setLoadNote] = useState(null); // shown when GET fell back to defaults

  const [saveBusy, setSaveBusy] = useState(false);
  const [saveOk, setSaveOk] = useState(false);
  const [saveError, setSaveError] = useState(null);

  // ── Load on mount + whenever scope changes ────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadNote(null);
    setSaveOk(false);
    setSaveError(null);
    api
      .getOverallSettings(scopeKey)
      .then((res) => {
        if (cancelled) return;
        const norm = normaliseConfig(res);
        setConfig(norm);
        setLoaded(norm);
      })
      .catch((e) => {
        if (cancelled) return;
        // 404 / not wired yet / network → start from a fully-disabled default.
        const norm = defaultConfig();
        setConfig(norm);
        setLoaded(norm);
        setLoadNote(
          e?.response?.data?.detail ??
            e?.message ??
            "no saved settings — starting from defaults"
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeKey]);

  // ── Granular updaters (any edit clears a stale save confirmation) ─────────
  const markDirty = useCallback(() => {
    setSaveOk(false);
    setSaveError(null);
  }, []);

  const patchSection = useCallback(
    (section, patch) => {
      markDirty();
      setConfig((prev) => ({ ...prev, [section]: { ...prev[section], ...patch } }));
    },
    [markDirty]
  );

  const handleReset = useCallback(() => {
    setConfig(loaded);
    setSaveOk(false);
    setSaveError(null);
  }, [loaded]);

  // ── Build the exact wire config (all numbers coerced; ints clamped) ───────
  const buildConfig = useCallback(() => {
    const c = config;
    return {
      sl: {
        enabled: !!c.sl.enabled,
        mode: c.sl.mode === "premium_pct" ? "premium_pct" : "mtm",
        value: num(c.sl.value),
      },
      target: {
        enabled: !!c.target.enabled,
        mode: c.target.mode === "premium_pct" ? "premium_pct" : "mtm",
        value: num(c.target.value),
      },
      trailing: {
        mode: ["lock", "lock_trail", "overall_trail"].includes(c.trailing.mode)
          ? c.trailing.mode
          : "none",
        unit: c.trailing.unit === "premium_pct" ? "premium_pct" : "mtm",
        lock_at: num(c.trailing.lock_at),
        lock_floor: num(c.trailing.lock_floor),
        trail_per: num(c.trailing.trail_per),
        trail_by: num(c.trailing.trail_by),
        base_sl: num(c.trailing.base_sl),
      },
      reentry: {
        enabled: !!c.reentry.enabled,
        max: intClamp(c.reentry.max, 1, 5, 1),
        type: c.reentry.type === "momentum" ? "momentum" : "asap",
        reverse: !!c.reentry.reverse,
        momentum_pct: num(c.reentry.momentum_pct),
      },
    };
  }, [config]);

  const handleSave = useCallback(async () => {
    if (saveBusy) return;
    setSaveBusy(true);
    setSaveOk(false);
    setSaveError(null);
    const wire = buildConfig();
    try {
      await api.putOverallSettings(scopeKey, wire);
      setSaveOk(true);
      setLoaded(wire); // committed → this becomes the new Reset baseline
    } catch (e) {
      setSaveError(e?.response?.data?.detail ?? e?.message ?? "Save failed");
    } finally {
      setSaveBusy(false);
    }
  }, [saveBusy, buildConfig, scopeKey]);

  // ── Derived flags ─────────────────────────────────────────────────────────
  const sl = config.sl;
  const target = config.target;
  const trailing = config.trailing;
  const reentry = config.reentry;

  const trailMode = trailing.mode;
  const showLockParams = trailMode === "lock" || trailMode === "lock_trail";
  const showTrailParams = trailMode === "lock_trail" || trailMode === "overall_trail";
  const showBaseSl = trailMode === "overall_trail";

  // Re-entry is only meaningful when an Overall SL or Target can trigger an exit.
  const reentryEligible = sl.enabled || target.enabled;
  const reentryActive = reentry.enabled && reentryEligible;

  const unitSuffix = (unitMode) => (unitMode === "premium_pct" ? "%" : "₹");

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-dimmer font-mono py-6 justify-center">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading overall controls…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Scope + load note */}
      <div className="flex items-center gap-2 flex-wrap text-[10px] font-mono">
        <span className="text-dimmer uppercase tracking-wider font-semibold">
          Scope
        </span>
        <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-line bg-bg-3 text-foreground font-semibold">
          {SCOPE_LABEL[scopeKey] ?? scopeKey}
        </span>
        {loadNote && (
          <span className="inline-flex items-center gap-1 text-amber-400" title={loadNote}>
            <AlertTriangle className="w-3 h-3 shrink-0" />
            defaults
          </span>
        )}
      </div>

      {/* ── 1. Overall Stop Loss ─────────────────────────────────────────── */}
      <SubSection
        title="Overall Stop Loss"
        badge={
          sl.enabled ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full border border-danger/50 bg-danger/10 text-danger text-[10px] font-mono font-bold uppercase tracking-wider">
              On
            </span>
          ) : null
        }
      >
        <Toggle
          checked={sl.enabled}
          onChange={(v) => patchSection("sl", { enabled: v })}
          label="Enable overall stop loss"
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label className={labelCls}>Mode</label>
            <Segmented
              value={sl.mode}
              options={UNIT_OPTIONS}
              disabled={!sl.enabled}
              onChange={(v) => patchSection("sl", { mode: v })}
            />
          </div>
          <NumField
            label={sl.mode === "premium_pct" ? "Loss (% of premium)" : "Loss (₹ MTM)"}
            value={sl.value}
            onChange={(v) => patchSection("sl", { value: v })}
            disabled={!sl.enabled}
            step={sl.mode === "premium_pct" ? "0.5" : "100"}
            min="0"
            placeholder={sl.mode === "premium_pct" ? "e.g. 30" : "e.g. 5000"}
            suffix={unitSuffix(sl.mode)}
          />
        </div>
      </SubSection>

      {/* ── 2. Overall Target ────────────────────────────────────────────── */}
      <SubSection
        title="Overall Target"
        badge={
          target.enabled ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-300 text-[10px] font-mono font-bold uppercase tracking-wider">
              On
            </span>
          ) : null
        }
      >
        <Toggle
          checked={target.enabled}
          onChange={(v) => patchSection("target", { enabled: v })}
          label="Enable overall target"
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label className={labelCls}>Mode</label>
            <Segmented
              value={target.mode}
              options={UNIT_OPTIONS}
              disabled={!target.enabled}
              onChange={(v) => patchSection("target", { mode: v })}
            />
          </div>
          <NumField
            label={target.mode === "premium_pct" ? "Profit (% of premium)" : "Profit (₹ MTM)"}
            value={target.value}
            onChange={(v) => patchSection("target", { value: v })}
            disabled={!target.enabled}
            step={target.mode === "premium_pct" ? "0.5" : "100"}
            min="0"
            placeholder={target.mode === "premium_pct" ? "e.g. 50" : "e.g. 8000"}
            suffix={unitSuffix(target.mode)}
          />
        </div>
      </SubSection>

      {/* ── 3. Trailing ──────────────────────────────────────────────────── */}
      <SubSection
        title="Trailing"
        badge={
          trailMode !== "none" ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full border border-info/50 bg-info/10 text-info text-[10px] font-mono font-bold uppercase tracking-wider">
              {TRAIL_MODES.find((m) => m.v === trailMode)?.label ?? trailMode}
            </span>
          ) : null
        }
      >
        <div className="flex flex-col gap-1">
          <label className={labelCls}>Mode</label>
          <div className="flex gap-1 flex-wrap">
            {TRAIL_MODES.map((m) => (
              <button
                key={m.v}
                type="button"
                onClick={() => patchSection("trailing", { mode: m.v })}
                className={`flex-1 min-w-[72px] py-1.5 px-2 text-xs font-mono font-semibold rounded-md border transition-colors ${
                  trailMode === m.v
                    ? "border-info/60 bg-info/15 text-info"
                    : "border-line bg-bg-2 text-dimmer hover:bg-bg-3"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {/* Plain-English mechanic explainer */}
        <div className="text-[11px] font-mono text-dim leading-relaxed">
          {TRAIL_EXPLAINER[trailMode] ?? TRAIL_EXPLAINER.none}
        </div>

        {trailMode !== "none" && (
          <>
            {/* Unit toggle (₹ / %) */}
            <div className="flex flex-col gap-1">
              <label className={labelCls}>Unit</label>
              <Segmented
                value={trailing.unit}
                options={UNIT_OPTIONS}
                onChange={(v) => patchSection("trailing", { unit: v })}
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {showLockParams && (
                <NumField
                  label="Lock-at (Y) — profit to activate"
                  value={trailing.lock_at}
                  onChange={(v) => patchSection("trailing", { lock_at: v })}
                  step="100"
                  min="0"
                  placeholder="e.g. 4000"
                  suffix={unitSuffix(trailing.unit)}
                />
              )}
              {showLockParams && (
                <NumField
                  label="Lock-floor (X) — locked profit"
                  value={trailing.lock_floor}
                  onChange={(v) => patchSection("trailing", { lock_floor: v })}
                  step="100"
                  min="0"
                  placeholder="e.g. 2000"
                  suffix={unitSuffix(trailing.unit)}
                />
              )}
              {showBaseSl && (
                <NumField
                  label="Base SL (S0) — initial stop (loss)"
                  value={trailing.base_sl}
                  onChange={(v) => patchSection("trailing", { base_sl: v })}
                  step="100"
                  min="0"
                  placeholder="e.g. 3000"
                  suffix={unitSuffix(trailing.unit)}
                />
              )}
              {showTrailParams && (
                <NumField
                  label="Trail-per (A) — profit step"
                  value={trailing.trail_per}
                  onChange={(v) => patchSection("trailing", { trail_per: v })}
                  step="100"
                  min="0"
                  placeholder="e.g. 1000"
                  suffix={unitSuffix(trailing.unit)}
                />
              )}
              {showTrailParams && (
                <NumField
                  label="Trail-by (B) — floor rise per step"
                  value={trailing.trail_by}
                  onChange={(v) => patchSection("trailing", { trail_by: v })}
                  step="100"
                  min="0"
                  placeholder="e.g. 500"
                  suffix={unitSuffix(trailing.unit)}
                />
              )}
            </div>

            {/* Inverted lock config — floor at/above trigger would exit instantly */}
            {showLockParams &&
              num(trailing.lock_floor) >= num(trailing.lock_at) && (
                <div className="flex items-start gap-1.5 text-[11px] font-mono text-amber-400">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
                  <span>
                    Lock floor ({num(trailing.lock_floor)}) should be BELOW the
                    lock trigger ({num(trailing.lock_at)}) — as set it would exit
                    immediately.
                  </span>
                </div>
              )}
          </>
        )}
      </SubSection>

      {/* ── 4. Re-entry ──────────────────────────────────────────────────── */}
      <SubSection
        title="Re-entry"
        badge={
          reentryActive ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full border border-info/50 bg-info/10 text-info text-[10px] font-mono font-bold uppercase tracking-wider">
              On
            </span>
          ) : null
        }
      >
        <Toggle
          checked={reentry.enabled}
          onChange={(v) => patchSection("reentry", { enabled: v })}
          label="Enable re-entry after an overall exit"
        />
        <div className="flex items-center gap-1.5 text-[11px] font-mono text-dimmer">
          <AlertTriangle
            className={`w-3.5 h-3.5 shrink-0 ${
              reentry.enabled && !reentryEligible ? "text-amber-400" : "text-dimmer"
            }`}
          />
          Requires an Overall SL or Target to be enabled (it re-enters after that
          exit fires).
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <NumField
            label="Max re-entries (1–5)"
            value={reentry.max}
            onChange={(v) => patchSection("reentry", { max: intClamp(v, 1, 5, 1) })}
            disabled={!reentry.enabled}
            step="1"
            min="1"
            placeholder="1"
          />
          <div className="flex flex-col gap-1">
            <label className={labelCls}>Type</label>
            <Segmented
              value={reentry.type}
              options={[
                { v: "asap", label: "RE-ASAP" },
                { v: "momentum", label: "RE-MOMENTUM" },
              ]}
              disabled={!reentry.enabled}
              onChange={(v) => patchSection("reentry", { type: v })}
            />
          </div>
          {reentry.type === "momentum" && (
            <NumField
              label="Momentum % — move needed to re-enter"
              value={reentry.momentum_pct}
              onChange={(v) => patchSection("reentry", { momentum_pct: v })}
              disabled={!reentry.enabled}
              step="0.5"
              min="0"
              placeholder="e.g. 5"
              suffix="%"
            />
          )}
        </div>

        <Toggle
          checked={reentry.reverse}
          disabled={!reentry.enabled}
          onChange={(v) => patchSection("reentry", { reverse: v })}
          label="Reverse on re-entry (flip side)"
        />
      </SubSection>

      {/* ── Actions ──────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap pt-1">
        <button
          type="button"
          disabled={saveBusy}
          onClick={handleSave}
          className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-info/50 bg-info/10 text-info text-xs font-mono font-semibold hover:bg-info/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {saveBusy ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Save className="w-3.5 h-3.5" />
          )}
          {saveBusy ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          disabled={saveBusy}
          onClick={handleReset}
          title="Revert edits to the last-loaded / last-saved config"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Reset
        </button>

        {saveOk && (
          <span className="inline-flex items-center gap-1.5 text-xs font-mono text-emerald-300">
            <CheckCircle className="w-3.5 h-3.5 shrink-0" />
            Saved
          </span>
        )}
        {saveError && (
          <span className="inline-flex items-center gap-1.5 text-xs font-mono text-danger">
            <XCircle className="w-3.5 h-3.5 shrink-0" />
            {saveError}
          </span>
        )}
      </div>
    </div>
  );
}
