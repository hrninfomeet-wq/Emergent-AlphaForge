import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import {
  backtestSourceConfig, executionFormPatch, executionFrictionPayload,
  executionLevelsPayload, executionWindowPayload, executionSizingSummary,
  loadBacktestSource,
} from "../../frontend/src/lib/backtestDeploySource.js";
import { buildRiskPayload } from "../../frontend/src/lib/deployPayload.js";

const costs = {
  enabled: true, brokerage_per_order: 20, spread_pct_of_premium: 1.5,
  spread_min_pts: 0.2, stt_sell_rate: 0.001, exchange_txn_rate: 0.000325,
  sebi_rate: 0.000001, gst_rate: 0.18, stamp_buy_rate: 0.00003,
};
const slip = {
  atm_pts: 0.7, otm1_pts: 1.1, itm1_pts: 0.9, otm2_plus_pts: 2.1,
  itm2_plus_pts: 1.9, expiry_tail_multiplier: 3, expiry_tail_start: "14:45",
};
const sizing = { enabled: true, mode: "fixed_lots", fixed_lots: 7, capital: 300000 };
function ordinary() {
  return {
    id: "ordinary", instrument: "NIFTY", strategy_id: "fixture",
    params_applied: { threshold: 2 },
    config: { mode: "Balanced", params: {}, trade_window_start: "09:35", trade_window_end: "14:25",
      option_backtest: { enabled: true, moneyness: "atm", lots: 5, dte_filter: [2, 3, 4, 5],
        exit_mode: "spot_exit", cost_config: { enabled: false } } },
    option_backtest: { enabled: true, moneyness: "otm1", exit_mode: "option_levels",
      option_exit_config: { target_pts: 25, stop_pct: 20, applied: true },
      cost_config: costs, slippage_config: slip, sizing_config: sizing,
      request: { lots: 5, dte_filter: [2, 3, 4, 5],
        exit_controls: { enabled: true, unit: "pct", breakeven: { trigger: 0.2, lock: 0 },
          trailing: { activation: 0.3, distance: 0.1 } },
        daily_caps: { loss: 5000, target: null, max_trades: 3 } } },
  };
}
function premium() {
  const r = ordinary(); r.id = "premium";
  Object.assign(r.option_backtest, {
    dispatch: "premium_trigger_config", exit_mode: "premium_trigger_config",
    moneyness: "itm1", slippage_config: null,
    engine_params: { reference_time: "09:31", entry_cutoff: "14:40", leg_mode: "both", lazy_enabled: true },
    premium_trigger_config: { reference_time: "09:31", moneyness: "itm1", lots: 7 },
  });
  r.option_backtest.option_exit_config = { target_pct: 60, stop_pct: 20 };
  return r;
}

test("ordinary result transfers effective policy rather than its request echo", () => {
  const r = ordinary(); const ex = backtestSourceConfig(r).execution;
  const f = executionFormPatch(ex);
  assert.equal(f.friction_costs_enabled, true);
  assert.deepEqual(f.dte_filter, [2, 3, 4, 5]);
  assert.equal(f.default_lots, 7);
  assert.equal(f.option_moneyness, "otm1");
  assert.deepEqual(executionFrictionPayload(f).costs, costs);
  assert.deepEqual(executionFrictionPayload(f).slippage, slip);
  assert.deepEqual(executionWindowPayload(f), { trade_window_start: "09:35", trade_window_end: "14:25" });
  assert.deepEqual(executionLevelsPayload(f), {
    auto_paper_target_pts: 25, auto_paper_target_pct: null,
    auto_paper_stop_pts: null, auto_paper_stop_pct: 20,
  });
  assert.equal(f.risk_use_breakeven, true); assert.equal(f.risk_use_trailing, true);
  assert.equal(f.risk_use_daily_caps, true); assert.equal(f.breakeven_lock, 0);
  assert.equal(f.daily_cap_loss, 5000); assert.equal(f.daily_cap_max_trades, 3);
  assert.deepEqual(r.option_backtest.cost_config, costs, "normalizing must not mutate the source");
});

test("premium fixed lots, resolved costs and no point slippage remain truthful", () => {
  const ex = backtestSourceConfig(premium()).execution;
  const f = executionFormPatch(ex);
  assert.equal(executionSizingSummary(ex), "fixed 7 lot(s)");
  assert.equal(f.default_lots, 7); assert.equal(f.option_moneyness, "itm1");
  assert.deepEqual(f.dte_filter, [], "premium engine ignores the pairing request DTE");
  assert.equal(f.risk_use_daily_caps, false, "ignored pairing caps must not become a new overlay");
  assert.deepEqual(executionFrictionPayload(f).costs, costs);
  const s = executionFrictionPayload(f).slippage;
  for (const k of ["atm_pts", "otm1_pts", "itm1_pts", "otm2_plus_pts", "itm2_plus_pts"]) assert.equal(s[k], 0);
  assert.equal(s.expiry_tail_multiplier, 1, "premium engine does not widen spread on expiry tails");
  assert.deepEqual(executionWindowPayload(f), { trade_window_start: "09:31", trade_window_end: "14:40" });
  assert.equal(f.auto_paper_stop_pct, 20); assert.equal(f.auto_paper_target_pct, 60);
});

test("unbounded DTE stays unbounded; scalar DTE zero is preserved", () => {
  const r = ordinary(); r.option_backtest.request.dte_filter = null;
  assert.deepEqual(executionFormPatch(backtestSourceConfig(r).execution).dte_filter, []);
  r.option_backtest.request.dte_filter = 0;
  assert.deepEqual(executionFormPatch(backtestSourceConfig(r).execution).dte_filter, [0]);
  r.option_backtest.request.dte_filter = ["DTE0", "dte2", "2"];
  assert.deepEqual(executionFormPatch(backtestSourceConfig(r).execution).dte_filter, [0, 2]);
  r.option_backtest.request.dte_filter = " ALL ";
  assert.deepEqual(executionFormPatch(backtestSourceConfig(r).execution).dte_filter, []);
});

test("spot exits clear stale levels and optional overlay flags on source change", () => {
  const r = ordinary(); r.option_backtest.exit_mode = "spot_exit";
  r.option_backtest.request.exit_controls = null; r.option_backtest.request.daily_caps = null;
  const f = executionFormPatch(backtestSourceConfig(r).execution);
  assert.equal(f.auto_paper_target_pts, ""); assert.equal(f.auto_paper_stop_pct, "");
  assert.equal(f.risk_use_breakeven, false); assert.equal(f.risk_use_daily_caps, false);
  assert.equal(f.exit_controls_enabled, false);
});

test("disabled costs override the enabled request without losing saved rates", () => {
  const r = ordinary(); r.option_backtest.cost_config = { ...costs, enabled: false };
  r.config.option_backtest.cost_config = costs;
  const f = executionFormPatch(backtestSourceConfig(r).execution);
  assert.equal(f.friction_costs_enabled, false);
  assert.deepEqual(executionFrictionPayload(f).costs, { ...costs, enabled: false });
});

test("risk sizing is described by its actual mode", () => {
  assert.match(executionSizingSummary({ sizing_config: { enabled: true, mode: "premium_at_risk", capital: 200000, risk_per_trade_pct: 2, max_lots: 4 } }), /premium-at-risk.*2%.*200000.*4/);
  assert.equal(executionSizingSummary({ lots: 5, sizing_config: { enabled: false } }), "fixed 5 lot(s)");
});

test("full detail is required, malformed or unsupported policies are not defaulted", () => {
  assert.throws(() => backtestSourceConfig({ id: "thin", config: { mode: "Balanced" } }), /full|detail/i);
  const r = ordinary(); delete r.option_backtest;
  assert.throws(() => backtestSourceConfig(r), /resolved|effective/i);
  const bad = ordinary(); bad.option_backtest.moneyness = "itm2";
  assert.throws(() => backtestSourceConfig(bad), /moneyness|supported/i);
  const badTime = ordinary(); badTime.config.trade_window_end = "15:30";
  assert.throws(() => backtestSourceConfig(badTime), /entry window/);
  const badCost = ordinary(); badCost.option_backtest.cost_config = "broken";
  assert.throws(() => backtestSourceConfig(badCost), /cost\/slippage/);
});

test("spot-only detail remains an explicit policy-free source", () => {
  const r = ordinary(); r.config.option_backtest.enabled = false; r.option_backtest = { enabled: false };
  assert.equal(backtestSourceConfig(r).execution, null);
});

// Execute the request object the actual component constructs, without invoking
// create(), an API client, React, or any deployment/order operation.
const lab = fs.readFileSync(new URL("../../frontend/src/pages/LiveSignals.jsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const payloadSource = lab.slice(lab.indexOf("      const payload = {"), lab.indexOf("      const res = await api.createDeployment(payload);"));
const outgoing = new Function("form", "DTE_VALUES", "buildRiskPayload", "executionFrictionPayload", "executionLevelsPayload", "executionWindowPayload", payloadSource + "; return payload;");
const payload = f => outgoing({ source_type: "backtest_run", source_id: "ordinary", mode: "paper", auto_paper: true,
  pretrade_profile: "Balanced", source_params: {}, allow_overnight: false,
  max_consecutive_losses: "", daily_loss_cutoff_pct: "", max_open_paper_trades: "", capital_basis: "fixed", ...f },
  [0, 1, 2, 3, 4, 5, 6], buildRiskPayload, executionFrictionPayload, executionLevelsPayload, executionWindowPayload);
test("actual wizard request retains every resolved cost and risk field", () => {
  const ex = backtestSourceConfig(ordinary()).execution;
  const p = payload(executionFormPatch(ex));
  assert.deepEqual(p.friction.costs, costs); assert.deepEqual(p.friction.slippage, slip);
  assert.deepEqual(p.dte_filter, [2, 3, 4, 5]); assert.equal(p.default_lots, 7);
  assert.deepEqual(p.risk.exit_controls, ordinary().option_backtest.request.exit_controls);
  assert.deepEqual(p.risk.daily_caps, ordinary().option_backtest.request.daily_caps);
  assert.equal(p.risk.trade_window_start, "09:35"); assert.equal(p.risk.trade_window_end, "14:25");
  assert.equal(p.auto_paper_target_pts, 25); assert.equal(p.auto_paper_stop_pct, 20);
});
test("actual wizard request keeps unbounded DTE and premium fill policy", () => {
  const p = payload(executionFormPatch(backtestSourceConfig(premium()).execution));
  assert.deepEqual(p.dte_filter, []); assert.equal(p.friction.slippage.atm_pts, 0);
  assert.equal(p.friction.slippage.expiry_tail_multiplier, 1);
  assert.equal(p.auto_paper_target_pct, 60); assert.equal(p.auto_paper_stop_pct, 20);
});
test("ordinary disabled costs and spot exits do not leak another source's settings", () => {
  const r = ordinary(); r.option_backtest.cost_config = { enabled: false };
  r.option_backtest.exit_mode = "spot_exit"; r.option_backtest.request.daily_caps = null;
  r.option_backtest.request.exit_controls = null;
  const old = executionFormPatch(backtestSourceConfig(premium()).execution);
  const p = payload({ ...old, ...executionFormPatch(backtestSourceConfig(r).execution) });
  assert.equal(p.friction.costs.enabled, false); assert.equal(p.auto_paper_target_pct, null);
  assert.equal(p.auto_paper_stop_pct, null); assert.equal(p.risk.exit_controls, undefined);
});
test("actual wizard progression blocks unavailable or not-yet-applied source detail", () => {
  const expr = lab.slice(lab.indexOf("  const canNext1 = ") + "  const canNext1 = ".length, lab.indexOf("  const deployBlocked = ")).trim().replace(/;$/, "");
  const ready = new Function("form", "runSourceReady", "sourceKey", "libraryParamsValid", `return (${expr});`);
  const f = { source_type: "backtest_run", source_id: "ordinary", execution_source_key: "backtest_run:ordinary" };
  assert.equal(ready(f, false, "backtest_run:ordinary", true), false);
  assert.equal(ready({ ...f, execution_source_key: "backtest_run:premium" }, true, "backtest_run:ordinary", true), false);
  assert.equal(ready(f, true, "backtest_run:ordinary", true), true);
});

test("actual prefill effect applies a normalized run and ignores later preset list refreshes", () => {
  const text = lab.slice(lab.indexOf("  const prefillRef = useRef(null);"), lab.indexOf("  const create = async () =>"));
  const effect = new Function("useRef", "useEffect", "form", "selectedSourceConfig", "sourceKey", "setForm", "executionFormPatch", text);
  const ref = { current: null };
  let form = { source_type: "backtest_run", source_id: "ordinary", friction_costs_enabled: false };
  const invoke = (config, key) => effect(() => ref, fn => fn(), form, config, key, fn => { form = fn(form); }, executionFormPatch);
  invoke(backtestSourceConfig(ordinary()), "backtest_run:ordinary");
  assert.equal(form.friction_costs_enabled, true); assert.equal(form.execution_source_key, "backtest_run:ordinary");
  form = { ...form, source_type: "preset", source_id: "preset-A" };
  invoke({ execution: { lots: 3 } }, "preset:preset-A");
  form = { ...form, default_lots: 9 };
  invoke({ execution: { lots: 3 } }, "preset:preset-A");
  assert.equal(form.default_lots, 9, "periodic list refresh must not reset operator edits");
  form = { ...form, source_id: "preset-B" };
  invoke({ execution: { lots: 4 } }, "preset:preset-A");
  assert.equal(form.default_lots, 9, "stale effect must not mutate the new selection");
});

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));
test("thin list selection fetches full detail before it is ready", async () => {
  const d = deferred(), seen = [], requested = [];
  loadBacktestSource("ordinary", id => { requested.push(id); return d.promise; }, s => seen.push(s));
  await Promise.resolve();
  assert.deepEqual(requested, ["ordinary"]); assert.equal(seen.at(-1).status, "loading");
  d.resolve(ordinary()); await flush();
  assert.equal(seen.at(-1).status, "ready"); assert.equal(seen.at(-1).config.execution.lots, 7);
});
test("read failure never returns default or ready execution", async () => {
  const seen = [];
  loadBacktestSource("ordinary", async () => { throw new Error("detail unavailable"); }, s => seen.push(s));
  await flush(); assert.equal(seen.at(-1).status, "error"); assert.equal(seen.at(-1).config, null);
  assert.match(seen.at(-1).error, /detail unavailable/);
});
test("cancelled old success or failure cannot replace a newer selection", async () => {
  for (const fail of [false, true]) {
    const old = deferred(), seen = [];
    const cancel = loadBacktestSource("ordinary", () => old.promise, s => seen.push(s));
    cancel(); loadBacktestSource("premium", async () => premium(), s => seen.push(s));
    await flush(); const ready = seen.at(-1);
    if (fail) old.reject(new Error("late failure")); else old.resolve(ordinary());
    await flush(); assert.equal(seen.at(-1), ready); assert.equal(ready.id, "premium");
  }
});
test("wrong-ID response and malformed detail fail rather than applying another run", async () => {
  const seen = [];
  loadBacktestSource("ordinary", async () => premium(), s => seen.push(s));
  await flush(); assert.equal(seen.at(-1).status, "error");
  loadBacktestSource("ordinary", async () => ({ id: "ordinary", config: { mode: "Balanced" } }), s => seen.push(s));
  await flush(); assert.equal(seen.at(-1).status, "error");
});
