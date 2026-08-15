/**
 * Deploy-wizard payload construction and gating — pure, so it can be driven from
 * node instead of inspected as JSX.
 *
 * ## The bug this exists to prevent
 *
 * The wizard built its request inline and sent `risk: enabled ? {…} : null`. The
 * comment above that line said "otherwise omitted", but `null` is not omission:
 * `DeploymentCreateReq.risk` is `Dict[str, Any] = Field(default_factory=dict)`
 * — NOT Optional — so Pydantic rejected the request with
 *
 *     risk: Input should be a valid dictionary
 *
 * which reached the user verbatim. The effect was that "Exit / Risk controls",
 * an optional panel, had to be ticked for ANY deployment to succeed: the
 * checkbox appeared mandatory purely to satisfy a malformed payload.
 *
 * Reproduced against the running backend:
 *     risk: null -> 422 {"type":"dict_type","loc":["body","risk"], …}
 *     risk: {}   -> passes validation and reaches real business logic
 *
 * The contract is now explicit and enforced by `buildRiskPayload`, which ALWAYS
 * returns an object. `friction` is genuinely `Optional[...] = None` on the
 * backend and so may still be null — the two fields differ, and the difference
 * is deliberate rather than incidental.
 */

/** Number, or null for a blank/unparseable field. */
function num(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Non-negative integer, or null. */
function intOrNull(v) {
  const n = num(v);
  return n === null ? null : Math.max(0, parseInt(String(v), 10) || 0);
}

/** True when at least one of the named form fields holds a value. */
function anySet(form, keys) {
  return keys.some((k) => form?.[k] !== "" && form?.[k] !== null && form?.[k] !== undefined);
}

export const RISK_SUBSECTIONS = ["breakeven", "trailing", "daily_caps"];

/**
 * The `risk` object for the request. ALWAYS an object — never null.
 *
 * An empty `{}` is the valid, explicit representation of "no optional exit or
 * risk controls configured". The backend's default for the field is exactly
 * that, so a disabled panel now sends precisely what the schema expects rather
 * than a value it rejects.
 */
export function buildRiskPayload(form) {
  const f = form || {};
  if (!f.exit_controls_enabled) return {};

  const useBreakeven = Boolean(f.risk_use_breakeven)
    && anySet(f, ["breakeven_trigger", "breakeven_lock"]);
  const useTrailing = Boolean(f.risk_use_trailing)
    && anySet(f, ["trailing_activation", "trailing_distance"]);
  const useDailyCaps = Boolean(f.risk_use_daily_caps)
    && anySet(f, ["daily_cap_loss", "daily_cap_target", "daily_cap_max_trades"]);

  const risk = {};

  // Only emit exit_controls when a sub-control actually carries a value.
  // `enabled: true` with every field null arms nothing (effective_premium_stop
  // requires a positive trigger or distance), so posting it would persist a
  // control that silently does nothing — the exact shape the live guard reports
  // as "no_trail".
  if (useBreakeven || useTrailing) {
    risk.exit_controls = {
      enabled: true,
      unit: f.exit_controls_unit || "pct",
      breakeven: useBreakeven ? {
        trigger: num(f.breakeven_trigger),
        lock: num(f.breakeven_lock),
      } : null,
      trailing: useTrailing ? {
        activation: num(f.trailing_activation),
        distance: num(f.trailing_distance),
      } : null,
    };
  }

  if (useDailyCaps) {
    risk.daily_caps = {
      loss: num(f.daily_cap_loss),
      target: num(f.daily_cap_target),
      max_trades: intOrNull(f.daily_cap_max_trades),
    };
  }

  return risk;
}

/** True when the risk payload configures nothing at all. */
export function riskIsEmpty(risk) {
  return !risk || Object.keys(risk).length === 0;
}

/**
 * Everything the operator must acknowledge before deploying, as ONE list.
 *
 * Consolidated on purpose. The wizard already had a quality-warning
 * acknowledgement; bolting a second checkbox on for "no exit controls" would
 * mean two ticks for one decision. A single acknowledgement covering whatever
 * happens to apply is both fewer clicks and harder to click through blindly,
 * because the list changes with the situation.
 */
export function deployAcknowledgements(form, quality) {
  const out = [];
  const q = quality || {};
  if (q.acknowledgment_required) {
    for (const w of (q.warnings || [])) {
      out.push({
        id: `quality:${w.id || w.code || out.length}`,
        kind: "quality",
        title: w.title || w.id || "Source quality warning",
        detail: w.message || w.detail || "",
      });
    }
    if (!(q.warnings || []).length) {
      out.push({
        id: "quality:unspecified",
        kind: "quality",
        title: "Source quality warnings",
        detail: "This source did not pass every quality check.",
      });
    }
  }
  if (riskIsEmpty(buildRiskPayload(form))) {
    out.push({
      id: "no_exit_controls",
      kind: "risk",
      title: "No optional exit or risk controls",
      detail: ("No breakeven lock, trailing stop or daily cap is configured. "
        + "Positions rely only on the strategy's own exits and the 15:00 IST "
        + "square-off."),
    });
  }
  return out;
}

/**
 * Field-level validation of the risk inputs, mirroring the backend's
 * `exit_controls.validate_exit_risk_config` so the user is told BEFORE the round
 * trip. Returns `{field: message}`; empty means valid.
 *
 * `unit: "pct"` is a FRACTION (`entry * (1 + trigger)`), so 0.25 means +25% and
 * a value >= 1 is almost always someone typing a percentage.
 */
export function validateRiskFields(form) {
  const f = form || {};
  const errors = {};
  if (!f.exit_controls_enabled) return errors;

  const pct = (f.exit_controls_unit || "pct") === "pct";
  const check = (key, value, { allowZero = false } = {}) => {
    if (value === "" || value === null || value === undefined) return;
    const n = Number(value);
    if (!Number.isFinite(n)) {
      errors[key] = "Must be a number.";
      return;
    }
    if (n < 0) {
      errors[key] = "Cannot be negative.";
      return;
    }
    if (!allowZero && n === 0) {
      errors[key] = "Must be greater than 0, or leave blank to disable.";
      return;
    }
    if (pct && n >= 1) {
      errors[key] = `Use a fraction: ${n}% is ${(n / 100).toFixed(2)}, not ${n}.`;
    }
  };

  if (f.risk_use_breakeven) {
    check("breakeven_trigger", f.breakeven_trigger);
    check("breakeven_lock", f.breakeven_lock, { allowZero: true });
    if (f.breakeven_trigger === "" && f.breakeven_lock !== "") {
      errors.breakeven_trigger = "A trigger is required to arm the breakeven lock.";
    }
  }
  if (f.risk_use_trailing) {
    check("trailing_activation", f.trailing_activation);
    check("trailing_distance", f.trailing_distance);
    if (f.trailing_distance === "" && f.trailing_activation !== "") {
      errors.trailing_distance = "A trail distance is required to arm the trailing stop.";
    }
  }
  if (f.risk_use_daily_caps) {
    for (const k of ["daily_cap_loss", "daily_cap_target"]) {
      const n = num(f[k]);
      if (n !== null && n <= 0) errors[k] = "Must be greater than 0, or leave blank.";
      // The backend refuses a rupee cap when costs are off, because the cap
      // would then act on GROSS P&L. Say so here rather than silently disabling
      // the input from a toggle two steps back.
      if (n !== null && !f.friction_costs_enabled) {
        errors[k] = "Enable costs on step 2 — a ₹ cap on gross P&L would mislead.";
      }
    }
    const mt = num(f.daily_cap_max_trades);
    if (mt !== null && (mt <= 0 || !Number.isInteger(mt))) {
      errors.daily_cap_max_trades = "Must be a whole number greater than 0.";
    }
  }
  return errors;
}

/** Why Deploy is disabled, or `[]` when it is ready. */
export function deployBlockers(form, quality, { sourceReady = true } = {}) {
  const out = [];
  if (!sourceReady) out.push("Choose a source on step 1.");
  const fieldErrors = validateRiskFields(form);
  const n = Object.keys(fieldErrors).length;
  if (n) out.push(`Fix ${n} exit/risk field${n === 1 ? "" : "s"}.`);
  if (deployAcknowledgements(form, quality).length && !form?.acknowledged_warnings) {
    out.push("Acknowledge the notes below to deploy.");
  }
  return out;
}
