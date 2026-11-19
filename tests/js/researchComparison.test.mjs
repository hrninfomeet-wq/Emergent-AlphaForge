import assert from "node:assert/strict";
import { test } from "node:test";
import { compareResearch, loadResearchComparison } from "../../frontend/src/lib/researchComparison.js";

const clone = v => structuredClone(v);
function run(id = "source") {
  return { id, instrument: "NIFTY", strategy_id: "fixture", params_applied: { threshold: 2 },
    config: { timeframe: "1m", start_ts: 1000, end_ts: 2000, costs_enabled: true,
      pretrade_filters: {}, trade_window_start: "09:25", trade_window_end: "14:50",
      option_backtest: { enabled: true, lots: 1 } },
    metrics: { total_pnl_pts: 12, trade_count: 3 },
    option_backtest: { enabled: true, moneyness: "atm", exit_mode: "spot_exit",
      cost_config: { enabled: false }, slippage_config: { atm_pts: 0.5 },
      sizing_config: { enabled: true, mode: "fixed_lots", fixed_lots: 7 },
      portfolio: { starting_capital: 200000, net_pnl_value: 1200 },
      request: { lots: 1, dte_filter: [3, 2], expiry_mode: "auto", expiry_date: null,
        entry_max_age_sec: 180, exit_max_age_sec: 180, exit_controls: null, daily_caps: null } } };
}
function job() { return { id: "job", status: "done", best_backtest_run_id: "winner",
  best_params: { threshold: 3 }, param_space: { threshold: { type: "int", min: 1, max: 5 } } }; }
const source = record => ({ kind: "run", record });
const row = (c, key) => c.settings.find(r => r.key === key);

test("same recorded common settings remain separate from parameter changes and dataset identity", () => {
  const a = run(), b = run("winner"); b.params_applied.threshold = 3;
  const c = compareResearch(source(a), job(), b);
  assert.equal(c.settingsStatus, "same");
  assert.equal(c.dataset.status, "unknown", "matching dates do not fingerprint candles");
  assert.equal(c.parameters.find(p => p.key === "threshold").status, "different");
  assert.equal(row(c, "sizing").left.fixed_lots, 7, "use effective sizing, not the requested lot");
  assert.equal(c.performance.left.optionNetPnl, 1200);
  assert.equal(c.performance.left.spotNetPnl, 12);
});

test("cost, interval, DTE and sizing mismatches are explicit", () => {
  const a = run(), b = clone(a); b.config.end_ts = 3000;
  b.option_backtest.cost_config = { enabled: true, spread_pct_of_premium: 1 };
  b.option_backtest.sizing_config.fixed_lots = 100;
  b.option_backtest.request.dte_filter = [0];
  const c = compareResearch(source(a), job(), b);
  assert.equal(c.settingsStatus, "different");
  for (const key of ["interval", "costs", "sizing", "dte"]) assert.equal(row(c, key).status, "different", key);
});

test("missing policies, parameters and empty detail are unknown, never equal", () => {
  const a = run(); delete a.option_backtest.cost_config; delete a.option_backtest.request.dte_filter;
  delete a.params_applied;
  const c = compareResearch(source(a), job(), clone(a));
  assert.equal(row(c, "costs").status, "unknown"); assert.equal(row(c, "dte").status, "unknown");
  assert.equal(c.settingsStatus, "unknown");
  const empty = compareResearch(source({}), {}, null);
  assert.equal(empty.settingsStatus, "unknown");
  assert.equal(empty.dataset.status, "unknown");
});

test("premium-native metrics/window/DTE/slippage use the premium envelope", () => {
  const a = run(); Object.assign(a.option_backtest, { dispatch: "premium_trigger_config",
    exit_mode: "premium_trigger_config", slippage_config: null, cost_config: null,
    engine_params: { reference_time: "09:31", entry_cutoff: "14:40" },
    premium_trigger_config: { reference_time: "09:31", lots: 7 },
    option_exit_config: { target_pct: 50, stop_pct: 20 },
    metrics: { paired_trade_count: 4 }, portfolio: { starting_capital: 200000, net_pnl_value: 42 } });
  a.metrics = { total_pnl_pts: 0, trade_count: 0 };
  const c = compareResearch(source(a), job(), clone(a));
  assert.deepEqual(row(c, "window").left, ["09:31", "14:40"]);
  assert.equal(row(c, "dte").left, "Not applied by premium engine");
  assert.equal(row(c, "slippage").left, "No point slippage / expiry-tail widening");
  assert.equal(c.performance.left.spotNetPnl, null); assert.equal(c.performance.left.optionNetPnl, 42);
  assert.equal(c.performance.left.tradeCount, 4);
});

test("premium searched lots/windows are shown as parameter differences, not shared-setting mismatch", () => {
  const a = run(); a.option_backtest.dispatch = "premium_trigger_config";
  a.option_backtest.engine_params = { reference_time: "09:31", entry_cutoff: "14:40" };
  const b = clone(a); b.option_backtest.sizing_config.fixed_lots = 9;
  b.option_backtest.engine_params.reference_time = "09:35";
  const j = job(); j.param_space.lots = { min: 1, max: 10 }; j.param_space.reference_time = { fixed: "09:35" };
  const c = compareResearch(source(a), j, b);
  assert.equal(row(c, "sizing").role, "parameter"); assert.equal(row(c, "window").role, "parameter");
  assert.notEqual(c.settingsStatus, "different");
});

test("preset policies remain declared evidence, without invented effective results", () => {
  const p = { kind: "preset", record: { name: "Known", config: { instrument: "NIFTY", strategy_id: "fixture",
    params: { threshold: 2 }, execution: { lots: 7, cost_config: { enabled: false } } } } };
  const c = compareResearch(p, job(), run("winner"));
  assert.equal(row(c, "sizing").status, "unknown"); assert.equal(c.performance.left.optionNetPnl, null);
  assert.equal(c.parameters[0].left, 2); assert.equal(c.declaredPolicy.lots, 7);
});

test("incumbent seed is enqueued evidence and missing rerank rows cannot prove exclusion", () => {
  const p = { kind: "preset", record: { name: "Known", config: { params: { threshold: 2 } } } };
  const j = job(); j.incumbent_seeds = [{ source: "preset:Known", params: { threshold: 2 }, dropped: { stop: "out_of_bounds" } }];
  j.rerank = { evaluated: 70, ranked: [{ params: { threshold: 3 } }] };
  const c = compareResearch(p, j, run("winner"));
  assert.equal(c.inclusion.seed.status, "recorded"); assert.match(c.inclusion.seed.note, /enqueued.*not.*execution/i);
  assert.equal(c.inclusion.rerank.status, "unknown");
  j.rerank.ranked.push({ params: { threshold: 2 }, option_pnl_value: 123 });
  assert.equal(compareResearch(p, j, run("winner")).inclusion.rerank.status, "recorded");
});

test("cancelling selection suppresses stale success and failure, including a pending winner read", async () => {
  let resolve, reject; const events = [];
  const old = new Promise(r => { resolve = r; });
  const fetchers = { getBacktestRun: id => id === "source" ? Promise.resolve(run()) : old,
    getOptJob: () => Promise.resolve(job()) };
  const cancel = loadResearchComparison({ source: { kind: "run", id: "source" }, jobId: "job" }, fetchers, e => events.push(e));
  await new Promise(r => setImmediate(r)); cancel(); resolve(run("winner"));
  await new Promise(r => setImmediate(r)); assert.deepEqual(events.map(e => e.status), ["loading"]);
  const failed = new Promise((_, r) => { reject = r; }); fetchers.getOptJob = () => failed;
  const cancel2 = loadResearchComparison({ source: { kind: "run", id: "source" }, jobId: "job" }, fetchers, e => events.push(e));
  cancel2(); reject(new Error("stale")); await new Promise(r => setImmediate(r));
  assert.deepEqual(events.map(e => e.status), ["loading", "loading"]);
});

test("full job and linked winner load in sequence; wrong-id detail refuses comparison", async () => {
  const events = [], calls = [];
  const fetchers = { getBacktestRun: async id => { calls.push(id); return run(id); }, getOptJob: async () => job() };
  loadResearchComparison({ source: { kind: "run", id: "source" }, jobId: "job" }, fetchers, e => events.push(e));
  await new Promise(r => setImmediate(r));
  assert.deepEqual(calls, ["source", "winner"]); assert.equal(events.at(-1).status, "ready");
  fetchers.getOptJob = async () => ({ ...job(), id: "other" });
  loadResearchComparison({ source: { kind: "run", id: "source" }, jobId: "job" }, fetchers, e => events.push(e));
  await new Promise(r => setImmediate(r)); assert.equal(events.at(-1).status, "error");
});
