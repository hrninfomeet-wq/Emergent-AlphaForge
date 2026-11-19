// Saved-run transfer is separate from the bounded journal list. Only a full run
// can distinguish what was requested from the policy the engines actually ran.
const own = (o, k) => Object.prototype.hasOwnProperty.call(o || {}, k);
const copy = (v) => v == null ? v : JSON.parse(JSON.stringify(v));
const object = (v) => v != null && typeof v === "object" && !Array.isArray(v);
const ZERO_SLIPPAGE = {
  atm_pts: 0, otm1_pts: 0, itm1_pts: 0, otm2_plus_pts: 0, itm2_plus_pts: 0,
  expiry_tail_multiplier: 1, expiry_tail_start: "15:00",
};

function dteValues(value) {
  if (value == null) return [];
  const values = Array.isArray(value) ? value : [value];
  const tokens = values.filter(v => typeof v !== "string" || !["", "all"].includes(v.trim().toLowerCase()))
    .map(v => typeof v === "string" ? v.trim().toLowerCase().replace(/^dte/, "") : v);
  if (tokens.some(v => v == null || typeof v === "boolean" || v === ""
      || !Number.isInteger(Number(v)) || Number(v) < 0)) {
    throw new Error("The saved DTE policy is invalid. Re-run this backtest before deploying it.");
  }
  return [...new Set(tokens.map(Number))].sort((a, b) => a - b);
}

/** Normalize the effective result, not config.option_backtest's request echo. */
export function backtestSourceConfig(run) {
  if (!object(run?.config) || !own(run.config, "option_backtest")) {
    throw new Error("Full backtest detail is required to load its execution policy.");
  }
  const cfg = run.config;
  const asked = cfg.option_backtest;
  const base = { ...copy(cfg), instrument: run.instrument || cfg.instrument,
    strategy_id: run.strategy_id || cfg.strategy_id, params: copy(run.params_applied || cfg.params || {}) };
  if (!asked?.enabled) return { ...base, execution: null };
  const ob = run.option_backtest;
  if (!object(ob) || !ob.enabled || !object(ob.sizing_config)) {
    throw new Error("This run has no resolved execution/sizing policy. Re-run it before deploying it.");
  }
  const premium = ob.dispatch === "premium_trigger_config";
  const request = object(ob.request) ? ob.request : asked;
  const moneyness = ob.moneyness ?? asked.moneyness;
  // Deployment option policy currently supports these three bands. Refuse an
  // unsupported saved band instead of displaying an empty select then using ATM.
  if (!["atm", "itm1", "otm1"].includes(moneyness)) {
    throw new Error(`Saved moneyness ${moneyness || "(missing)"} is not supported by this deployment wizard.`);
  }
  const sizing = copy(ob.sizing_config);
  const lots = sizing.enabled && sizing.mode === "fixed_lots"
    ? Number(sizing.fixed_lots) : Number(request.lots ?? asked.lots);
  if (!Number.isInteger(lots) || lots < 1) throw new Error("The resolved run has no valid lot count.");
  if (!own(ob, "cost_config") || (ob.cost_config != null && !object(ob.cost_config))
      || (!premium && !object(ob.slippage_config))) {
    throw new Error("This run has no complete effective cost/slippage policy. Re-run it before deploying it.");
  }
  const exits = ob.option_exit_config || {};
  const engine = ob.engine_params || ob.premium_trigger_config || {};
  const ex = {
    moneyness, lots, sizing_config: sizing,
    // The premium session engine loads sessions directly and does not apply the
    // pairing form's DTE filter. Do not turn an ignored request into a new gate.
    dte_filter: premium ? [] : dteValues(request.dte_filter),
    exit_mode: premium ? "premium_trigger_config" : (ob.exit_mode || request.exit_mode || "spot_exit"),
    cost_config: copy(ob.cost_config) || { enabled: false },
    // Premium-native apply_costs_to_trade models spread without point-slippage
    // or expiry-tail widening; a null result is meaningful, not a missing default.
    slippage_config: premium ? copy(ZERO_SLIPPAGE) : copy(ob.slippage_config),
    trade_window_start: premium ? (engine.reference_time || cfg.trade_window_start) : cfg.trade_window_start,
    trade_window_end: premium ? (engine.entry_cutoff || ob.live_entry_cutoff || cfg.trade_window_end) : cfg.trade_window_end,
    // Premium exits/caps live in the pinned strategy engine params. The generic
    // option-pairing overlay is not simulated on that path.
    exit_controls: premium ? null : copy(request.exit_controls),
    daily_caps: premium ? null : copy(request.daily_caps),
  };
  const time = v => typeof v === "string" && /^\d{2}:\d{2}$/.test(v)
    && Number(v.slice(0, 2)) < 24 && Number(v.slice(3)) < 60;
  if (!time(ex.trade_window_start) || !time(ex.trade_window_end)
      || ex.trade_window_start < "09:15" || ex.trade_window_end > "15:00"
      || ex.trade_window_start >= ex.trade_window_end) {
    throw new Error("The saved entry window cannot be carried into the deployment's 09:15–15:00 bounds. Re-run with a supported window.");
  }
  if (ex.exit_mode === "option_levels" || premium) {
    for (const key of ["target_pts", "stop_pts", "target_pct", "stop_pct"]) {
      ex[`option_${key}`] = own(exits, key) ? exits[key] : request[`option_${key}`];
    }
  }
  return { ...base, execution: ex };
}

/** A cancellable read. Cleanup suppresses both stale successes and stale errors. */
export function loadBacktestSource(id, fetchRun, publish) {
  let cancelled = false;
  publish({ id, status: "loading", config: null, error: null });
  Promise.resolve().then(() => fetchRun(id)).then(run => {
    if (cancelled) return;
    if (run?.id !== id) throw new Error("Backtest detail does not match the selected run.");
    const config = backtestSourceConfig(run);
    if (!cancelled) publish({ id, status: "ready", config, error: null });
  }).catch(error => {
    if (!cancelled) publish({ id, status: "error", config: null,
      error: error?.message || "Could not load the saved execution policy." });
  });
  return () => { cancelled = true; };
}

/** Complete patch: changing source must also clear the previous source's fields. */
export function executionFormPatch(ex) {
  const cost = ex?.cost_config || {};
  const slip = ex?.slippage_config || {};
  const overlay = ex?.exit_controls?.enabled ? ex.exit_controls : null;
  const caps = ex?.daily_caps;
  const be = overlay?.breakeven, trail = overlay?.trailing;
  const useCaps = !!caps && [caps.loss, caps.target, caps.max_trades].some(v => v != null);
  const levels = ex?.exit_mode === "option_levels" || ex?.exit_mode === "premium_trigger_config";
  const targetUnit = levels && ex.option_target_pts != null ? "pts" : "pct";
  const stopUnit = levels && ex.option_stop_pts != null ? "pts" : "pct";
  return {
    option_moneyness: ex?.moneyness || "atm", dte_filter: dteValues(ex?.dte_filter),
    default_lots: ex?.lots || 1,
    auto_paper_unit: targetUnit, auto_paper_target_unit: targetUnit, auto_paper_stop_unit: stopUnit,
    auto_paper_target_pts: levels ? ex.option_target_pts ?? "" : "",
    auto_paper_stop_pts: levels ? ex.option_stop_pts ?? "" : "",
    auto_paper_target_pct: levels ? ex.option_target_pct ?? "" : "",
    auto_paper_stop_pct: levels ? ex.option_stop_pct ?? "" : "",
    friction_enabled: true, friction_costs_enabled: Boolean(cost.enabled),
    friction_brokerage: cost.brokerage_per_order ?? 0,
    friction_spread_pct: cost.spread_pct_of_premium ?? 0,
    friction_spread_min_pts: cost.spread_min_pts ?? 0,
    friction_slip_atm: slip.atm_pts ?? 0.5,
    friction_slip_otm1: slip.otm1_pts ?? 1, friction_slip_itm1: slip.itm1_pts ?? slip.otm1_pts ?? 1,
    friction_slip_otm2: slip.otm2_plus_pts ?? 2, friction_slip_itm2: slip.itm2_plus_pts ?? slip.otm2_plus_pts ?? 2,
    source_cost_config: copy(cost), source_slippage_config: copy(slip),
    trade_window_start: ex?.trade_window_start || "", trade_window_end: ex?.trade_window_end || "",
    exit_controls_enabled: Boolean(be || trail || useCaps),
    risk_use_breakeven: Boolean(be), risk_use_trailing: Boolean(trail), risk_use_daily_caps: useCaps,
    exit_controls_unit: overlay?.unit || "pct",
    breakeven_trigger: be?.trigger ?? "", breakeven_lock: be?.lock ?? "",
    trailing_activation: trail?.activation ?? "", trailing_distance: trail?.distance ?? "",
    daily_cap_loss: caps?.loss ?? "", daily_cap_target: caps?.target ?? "",
    daily_cap_max_trades: caps?.max_trades ?? "",
    // These are explicit wizard overrides, not the source's sizing policy.
    lots_override: "", capital_amount: "", acknowledged_warnings: false,
  };
}

export function executionFrictionPayload(form) {
  return {
    enabled: Boolean(form.friction_enabled),
    slippage: {
      ...(form.source_slippage_config || {}),
      atm_pts: Number(form.friction_slip_atm) || 0,
      otm1_pts: Number(form.friction_slip_otm1) || 0,
      itm1_pts: Number(form.friction_slip_itm1 ?? form.friction_slip_otm1) || 0,
      otm2_plus_pts: Number(form.friction_slip_otm2) || 0,
      itm2_plus_pts: Number(form.friction_slip_itm2 ?? form.friction_slip_otm2) || 0,
    },
    costs: {
      ...(form.source_cost_config || {}),
      enabled: Boolean(form.friction_costs_enabled),
      brokerage_per_order: Number(form.friction_brokerage) || 0,
      spread_pct_of_premium: Number(form.friction_spread_pct) || 0,
      spread_min_pts: Number(form.friction_spread_min_pts) || 0,
    },
  };
}

export function executionLevelsPayload(form) {
  const out = {};
  for (const side of ["target", "stop"]) {
    const unit = form[`auto_paper_${side}_unit`] || form.auto_paper_unit;
    for (const u of ["pts", "pct"]) {
      const key = `auto_paper_${side}_${u}`;
      out[key] = unit === u && form[key] !== "" && form[key] != null ? Number(form[key]) : null;
    }
  }
  return out;
}

export function executionWindowPayload(form) {
  return Object.fromEntries(["trade_window_start", "trade_window_end"]
    .filter(k => form[k]).map(k => [k, form[k]]));
}

export function executionSizingSummary(ex, defaultLots = 1) {
  const s = ex?.sizing_config;
  if (s?.enabled && s.mode === "premium_at_risk") {
    return `premium-at-risk · ${s.risk_per_trade_pct}% · ₹${s.capital} · max ${s.max_lots}`;
  }
  return `fixed ${s?.enabled && s.mode === "fixed_lots" ? s.fixed_lots : ex?.lots || defaultLots} lot(s)`;
}
