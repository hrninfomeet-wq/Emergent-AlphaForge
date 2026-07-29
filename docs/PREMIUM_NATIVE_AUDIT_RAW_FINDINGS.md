# Audit findings — RAW agent output (UNVERIFIED)

Workflow `wf_50d18619-409`. 5 audit agents completed; **ALL 6 adversarial
verifiers died on the account spend limit**, so every finding below is an
UNVERIFIED agent claim. Each must be independently checked against source
before it is acted on. Do not cite these to the user as facts.

Counts by dimension:

  - dropped-config-fields: 12
  - kpi-and-trade-list-surfacing: 10
  - optimizer-premium-path: 13
  - param-space-hygiene: 10
  - sizing-and-cost-precedence: 7

## [1] HIGH — Grid search hard-crashes the whole job on any param with no declared min/max (KeyError), while Bayesian silently invents [0,100] for the same schema

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:261`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
        t = info["type"]
        if t == "int":
            lo, hi = int(info["min"]), int(info["max"])
            step = max(1, (hi - lo) // 6)
            vals = list(range(lo, hi + 1, step))
        elif t == "float":
            lo, hi = float(info["min"]), float(info["max"])
```

**Failure scenario:**
`_suggest` (optimizer.py:242,245) reads bounds defensively — `info.get("min", 0)` / `info.get("max", 100)` — but `_grid_combinations` reads them with `info["min"]` / `info["max"]`. The two disagree about what "no bounds" means. I ran the real schema of `backend/app/strategies/plugins/algotest_option_buy_nifty.py` through both code paths: `_suggest` yields `suggest_int(0, 100)` for all 11 unbounded ints; `_grid_combinations` raises `KeyError: 'min'`. Grid is a first-class user choice (`frontend/src/pages/Optimizer.jsx:65` offers "Grid Search"; `backend/app/routers/research.py:497` accepts `method="grid"`). The raise happens at optimizer.py:1298, OUTSIDE the per-combo `try` added at optimizer.py:1309, so it escapes to the outer handler at optimizer.py:1770 and the job is persisted as `status:"failed", error:"'min'"`. The user sees a one-character error message with no indication of which param or that bounds are the problem — and if they re-run with Bayesian it "works", silently searching lots in [0,100].

**Suggested fix:**
Make `_grid_combinations` use the same `.get()` defaults as `_suggest`, and better: have `_build_param_space` refuse (or loudly warn on) an int/float entry with no min/max instead of letting two call sites improvise different answers.

## [2] HIGH — A float param with no declared max is searched over [0.0, 1.0] — a 100x scale error for percent knobs, decided only by whether the LLM wrote `20` or `20.0`

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:245`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
        elif t == "float":
            lo, hi = float(info.get("min", 0.0)), float(info.get("max", 1.0))
            out[name] = trial.suggest_float(name, lo, hi)
```

**Failure scenario:**
`backend/app/ai/compiler.py:412` types a premium_trigger config value from its Python literal via `_schema_type_for` (compiler.py:330-338): `20` -> "int", `20.0` -> "float". Neither branch emits bounds. So a spec whose `stop_pct` is `20.0` compiles to `{"type": 'float', "default": 20.0}` and every trial gets `suggest_float("stop_pct", 0.0, 1.0)` — a 0-1% premium stop, i.e. an instant stop-out on essentially every trade. `PremiumTriggerConfig.stop_pct` is `ge=0.0, le=100.0` (premium_trigger_config.py:85), so the value validates and the run completes and reports metrics normally. Identically, `momentum_pct: 15.0` would be searched in [0,1] -> entry fires on any 0-1% move. The user's plugin happened to get ints (searched 0-100); the same authoring flow with a decimal point produces a silently 100x-wrong search range with no error anywhere.

**Suggested fix:**
Do not fabricate bounds. In `_build_param_space`, skip (and record as unsearchable) any int/float entry lacking both `min` and `max`, and make `validate_spec` require `min`/`max` for every int/float `ParamSpec`.

## [3] HIGH — The AI compiler emits every premium_trigger config key as an unbounded, non-`fixed` schema entry, so sizing/execution/risk knobs (`lots`) become optimizer dimensions

- dim: `param-space-hygiene`
- site: `backend/app/ai/compiler.py:411`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
        _pt_entries = "".join(
            f'{_nl_indent}{k!r}: {{"type": {_schema_type_for(v)!r}, "default": {v!r}}},'
            for k, v in spec.premium_trigger.items() if v is not None
        )
```

**Failure scenario:**
The emitted dict literal carries only `type` and `default` — never `min`/`max`, never `"fixed"`. `_build_param_space` (optimizer.py:202-215) admits any int/float/bool entry unconditionally, and there is NO denylist/allowlist of sizing or policy params anywhere in the repo (I grepped `optimizer.py`, `wfo.py`, `research.py`, `schemas.py`, and the whole tree for `optimizable|non_optimizable|search_space|SIZING|denylist|allowlist` — the only exclusion mechanism that exists is a per-param `"fixed"` key the compiler never writes). Result for the shipped `algotest_option_buy_nifty`: `lots` -> `suggest_int("lots", 0, 100)`. Because `PremiumTriggerConfig.lots` is `ge=1, le=100` (premium_trigger_config.py:102), the optimizer's accidental ceiling coincides exactly with the config's max, so a strictly-monotonic objective pins lots=100. Worse, lots=0 IS inside the suggested range and fails `ge=1`, so `extract_premium_trigger_config` returns `invalid:` (premium_trigger_dispatch.py:115-117), `dispatch_full_backtest` returns None, and `_evaluate_premium_trigger` (optimizer.py:366-367) reports `_premium_zero_metrics()` — an out-of-range sizing value is scored as an honest 'this config takes no trades' result, indistinguishable from a genuinely signal-free parameterization. Compare `premium_momentum.py:70,77,85-92`, which hand-writes `"fixed"` on exactly these policy knobs; the generated plugin gets none of that protection.

**Suggested fix:**
In `_param_schema_literal`/the premium_trigger emitter, write `"fixed": <value>` for non-alpha keys (`lots`, `late_lock_cutoff`, `entry_cutoff`, `exit_time`, `session_max_loss_rupees`, `session_max_profit_rupees`, `vix_min`, `vix_max`) and require explicit `min`/`max` for the rest — mirroring what `premium_momentum` does by hand.

## [4] HIGH — 8 of the 12 searched dimensions for a premium-native authored strategy are dropped by the evaluator before the sim ever sees them

- dim: `param-space-hygiene`
- site: `backend/app/premium_trigger_dispatch.py:110`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
_CONFIG_FIELDS = tuple(PremiumTriggerConfig.model_fields)
...
    src = dict(params or {})
    present = {k: src[k] for k in _CONFIG_FIELDS if src.get(k) is not None}
```

**Failure scenario:**
`_build_param_space` builds the space from `strategy.parameter_schema`; `dispatch_full_backtest` builds the actual run config from `_CONFIG_FIELDS` only. I intersected the two for `algotest_option_buy_nifty`: searched = {lots, momentum_pct, stop_pct, target_pct, trail_x_pct, trail_y_pct, lazy_enabled, lazy_momentum_pct, lazy_stop_pct, lazy_target_pct, lazy_trail_x_pct, lazy_trail_y_pct}; consumed by dispatch = {lots, momentum_pct, stop_pct, target_pct} only. The other EIGHT are filtered out at premium_trigger_dispatch.py:110 and can never change the objective. Consequences: TPE spends ~67% of the trial budget on pure noise; `_param_importance` (optimizer.py:444) reports importances for knobs that provably do nothing; and `_heatmap` (optimizer.py:455-461) picks the top-2 by that importance, so it can render a completely flat grid of identical objective values as if it were a real parameter-interaction surface. This is exactly the class of no-op search that `research.py:528-533` already 400s for exit controls ('the grid would burn with no effect') — no equivalent check exists for the parameter space.

**Suggested fix:**
For a premium-native strategy, intersect the search space with the keys the dispatcher actually consumes (`PremiumTriggerConfig.model_fields`) and either drop the rest or reject the run with a named list, the same way `search_exit_controls` is rejected in research.py:528.

## [5] HIGH — KPI grid reads the SPOT `result.metrics`, which is a zero-filled stub for premium-native runs

- dim: `kpi-and-trade-list-surfacing`
- site: `frontend/src/pages/BacktestLab.jsx:1838`
- category: wrong-numbers

**Evidence (agent-quoted, unverified):**
```
BacktestLab.jsx:1838  `const m = result.metrics || {};`
BacktestLab.jsx:1933-1938
```
<MetricCard label="Trades" value={fmtInt(m.trade_count)} testid="result-trades" />
<MetricCard label="Win Rate" value={fmtPct(m.win_rate)} testid="result-winrate" />
<MetricCard label="Profit Factor" value={fmtNum(m.profit_factor, 2)} testid="result-pf" />
<MetricCard label="Net P&L (pts)" value={fmtPnL(m.total_pnl_pts)} ... />
<MetricCard label="Max DD (pts)" value={fmtPnL(m.max_dd_pts)} ... />
<MetricCard label="Sharpe" value={fmtNum(m.sharpe, 2)} ... />
```
Backend source of that field — routers/research.py:231,259-260:
`metrics = res["metrics"]` ... `"metrics": metrics,` / `"trades": res["trades"],`  (res = run_backtest on the SPOT path, whose evaluate() is the inert stub)
backtest.py:261-268 `_empty_metrics()` → `{"trade_count": 0, ..., "win_rate": 0.0, "profit_factor": None, ..., "max_dd_pts": 0.0, "sharpe": None, ..., "total_pnl_pts": 0.0, ...}`
Contrast — the two account cards are the ONLY ones fed from the option envelope, BacktestLab.jsx:1845-1846 `const acctRange = useMemo(() => { const s = buildPerformanceSeries(result);` and lib/backtestMetrics.js:48-57 `const ob = result?.option_backtest; ... if (curve.length && portfolio?.starting_capital != null) {`
```

**Failure scenario:**
Run `algotest_option_buy_nifty` in the Backtest Lab. `dispatch_full_backtest` fills only `option_backtest.{metrics,trades,portfolio}`; the top-level `metrics` stays `_empty_metrics()`. The six KPI cards therefore render Trades `0`, Win Rate `0.00%`, Profit Factor `–` (fmt.js:11 returns "–" for null), Net P&L `+0.00`, Max DD `+0.00`, Sharpe `–` — while "Highest Acct Value" renders ₹ millions from `option_backtest.portfolio.curve`. This exact asymmetry is symptom 1.

**Suggested fix:**
Minimal, one-place fix in ResultsView: derive the KPI source from the premium envelope when `result.option_backtest?.dispatch === "premium_trigger_config"`. e.g. `const ob = result.option_backtest; const premium = ob?.dispatch === "premium_trigger_config"; const m = premium ? {trade_count: ob.metrics.paired_trade_count, win_rate: ob.metrics.win_rate, profit_factor: <computed>, total_pnl_pts: ob.metrics.total_option_pnl_pts, max_dd_pts: ob.portfolio.max_drawdown_value, sharpe: ob.portfolio.sharpe_daily} : (result.metrics || {})`. The profit-factor computation already exists server-side and can be lifted verbatim from optimizer.py:371-380 (gross_win/gross_loss over PAIRED `option_pnl_value`); better still, add `profit_factor` to option_backtest.py::_compute_metrics (line 362) so every consumer gets it. Relabel the two 'pts' cards to ₹ in premium mode since the option metrics are rupee/premium-points, not index points.

## [6] HIGH — Trades pane is fed the empty SPOT trade list, so premium-native runs render "No trades were taken in this run."

- dim: `kpi-and-trade-list-surfacing`
- site: `frontend/src/pages/BacktestLab.jsx:2006`
- category: dropped-data

**Evidence (agent-quoted, unverified):**
```
BacktestLab.jsx:2006  `<TradesTable trades={result.trades || []} optionBacktest={result.option_backtest} />`
BacktestLab.jsx:2780-2784
```
  if (!trades.length) {
    return (
      <Panel title="Trades" testid="trades-panel">
        <div className="text-xs text-dimmer">No trades were taken in this run.</div>
```
The option legs are only ever joined ONTO spot rows — BacktestLab.jsx:2676-2687 `for (const ot of (optionBacktest?.trades || [])) { if (ot?.index_trade_id != null) map[ot.index_trade_id] = ot; }` ... `(trades || []).map((t, i) => { const opt = optionByTradeId[i] || null;` — with zero spot rows the map is never iterated, so every `opt_*` column is unreachable.
```

**Failure scenario:**
A premium-native run that took 300 option trades shows an empty Trades panel with the literal text "No trades were taken in this run." — the user cannot see entry/exit time, strike, side or P&L for any of them, even though `result.option_backtest.trades` carries all 300 fully-shaped PAIRED rows. This is symptom 2.

**Suggested fix:**
In ResultsView, when `result.option_backtest?.dispatch === "premium_trigger_config"`, synthesize spot-shaped rows from the option legs and pass them in: `trades = ob.trades.map((t,i) => ({direction: t.direction, entry_ts: t.option_entry_ts, exit_ts: t.option_exit_ts, entry_price: t.entry_option_price, exit_price: t.exit_option_price, exit_reason: t.option_exit_reason, pnl_pts: t.option_pnl_pts, pnl_pct: ..., index_trade_id: t.index_trade_id}))`. Because the adapter already stamps `index_trade_id: i` (premium_trigger_dispatch.py:219), the existing `optionByTradeId[i]` join then lights up every Opt Leg / Lots / Buy₹ / Sell₹ / Charges / Opt P&L column with zero further changes.

## [7] HIGH — Trades.csv export downloads a file containing the literal string "(empty)" for premium-native runs

- dim: `kpi-and-trade-list-surfacing`
- site: `frontend/src/lib/exports.js:75`
- category: dropped-data

**Evidence (agent-quoted, unverified):**
```
exports.js:73-76
```
export const exportTradesCsv = (result) => {
  const stamp = (result?.name || "run") + "_" + (result?.id?.slice(0, 8) || "");
  exportCsv(result?.trades || [], `alphaforge_trades_${safeName(stamp)}.csv`);
};
```
exports.js:28-31 `if (!rows || rows.length === 0) { triggerDownload(new Blob(["(empty)"], { type: "text/csv" }), filename); return; }`
Wired to the always-visible button at BacktestLab.jsx:1895 `onClick={() => exportTradesCsv(result)}`.
```

**Failure scenario:**
The user, unable to see trades in the UI, clicks "Trades.csv" as the escape hatch and receives a 7-byte file reading `(empty)` — with no error and no hint that the real trades live under `option_backtest.trades`. The last route to per-trade P&L is closed.

**Suggested fix:**
`exportTradesCsv` should prefer the option envelope when it is the authoritative trade list: `const ob = result?.option_backtest; const rows = ob?.dispatch === "premium_trigger_config" ? (ob.trades || []) : (result?.trades || []);`. Same one-line guard fixes exportBacktestResult's usefulness for premium runs.

## [8] HIGH — Statistical-significance badge is computed from the empty spot metrics, so every premium-native run is badged red "WEAK / CI [0–0%]"

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/routers/research.py:249`
- category: wrong-verdict

**Evidence (agent-quoted, unverified):**
```
research.py:249  `sig = stat_significance(metrics["trade_count"], metrics["win_rate"], metrics.get("profit_factor"))`  (metrics = the spot `res["metrics"]` from line 231)
backtest.py:329-331
```
def stat_significance(n: int, win_rate_pct: float, profit_factor: Optional[float]) -> Dict[str, Any]:
    if n == 0:
        return {"badge": "INSUFFICIENT", "ci95_win_rate": [0, 0], "note": "0 trades"}
```
Rendered at BacktestLab.jsx:1860 `<SignificanceBadge significance={result.significance} />` → SignificanceBadge.jsx:11 `INSUFFICIENT:{ cls: "bg-rose-950 ...", icon: XCircle, label: "WEAK" }`.
Same zeros propagate to the run journal — BacktestRunJournal.jsx:215-219 `{fmtInt(r.metrics?.trade_count)}` / `{fmtPct(r.metrics?.win_rate)}` / `{fmtNum(r.metrics?.profit_factor, 2)}` / `{fmtPnL(r.metrics?.total_pnl_pts)}`.
```

**Failure scenario:**
A premium-native strategy with 400 trades and a 60% win rate is stamped with a red "WEAK, CI [0–0%]" badge at the top of its own result, and appears in the Backtest Run Journal as `0 | 0.00% | – | +0.00` — indistinguishable from a strategy that genuinely never traded. Two runs of the same premium strategy with wildly different edge are literally identical in the journal, so the user cannot rank or select between them.

**Suggested fix:**
In research.py (both `backtest_run` line 249 and `run_backtest_job` line 332), when `option_result` came back with `dispatch == "premium_trigger_config"`, compute significance from the option envelope: `sig = stat_significance(option_result["metrics"]["paired_trade_count"], option_result["metrics"]["win_rate"], <profit factor>)`. Cleanest variant: mirror the option metrics into the top-level `metrics` for premium-native runs (one dict build in research.py) — that simultaneously fixes the KPI cards, the journal, the significance badge and the trust scorecard below.

## [9] HIGH — Trust scorecard always reports "Trade count not available" and silently skips the overfitting (deflated-Sharpe) check for premium-native runs

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/deployment_quality.py:216`
- category: safety-gate-bypass

**Evidence (agent-quoted, unverified):**
```
deployment_quality.py:209,216 `metrics = _metrics(source_doc)` / `trade_count = int(_safe_float(metrics.get("trade_count")))` — `_metrics` (line 176-182) reads only `source_doc["metrics"]`, i.e. the zero-filled spot dict; it never looks at `option_backtest.metrics`.
deployment_quality.py:263-269
```
    elif trade_count == 0:
        warnings.append({
            "id": "missing_trade_count",
            "severity": SEVERITY_WARNING,
            "label": "Trade count not available",
            "detail": "Source backtest does not report a trade count. Cannot assess sample-size reliability.",
```
deployment_quality.py:315 `if n_trials and sharpe_val is not None and trade_count > 0:` — the selection-bias / deflated-Sharpe check. For premium-native runs `sharpe_val` is None (spot sharpe) AND `trade_count` is 0, so it can never run.
deployment_quality.py:284-286 `max_dd = abs(_safe_float(metrics.get("max_dd_pts")))` / `total_pnl = ...` / `if total_pnl > 0 and max_dd > 0:` — the large-drawdown check is likewise dead (both are 0.0).
Attached on every result read at research.py:423 `doc["quality"] = evaluate_source_quality(doc, evidence=evidence)` and rendered at BacktestLab.jsx:1956 `<TrustScorecard quality={result?.quality} />`.
```

**Failure scenario:**
An optimizer-selected premium-native preset (the exact scenario that produced lots=100 out of a 100-trial sweep) is never flagged for selection bias: `n_trials` is present but `trade_count == 0` and `sharpe is None`, so the deflated-Sharpe warning is skipped. Three of the five quality gates (low/zero trade count, weak Sharpe, large drawdown) are inert, and the one that does fire says the opposite of the truth — 'trade count not available' on a 400-trade run. The user gets a green-looking trust panel on the most overfit result in the app.

**Suggested fix:**
Widen `_metrics` (deployment_quality.py:176) to fall back to the option envelope: when `source_doc["option_backtest"]["dispatch"] == "premium_trigger_config"`, map `paired_trade_count→trade_count`, `portfolio.sharpe_daily→sharpe`, `portfolio.max_drawdown_value→max_dd_pts`, `metrics.total_option_pnl_pts→total_pnl_pts`. It is a pure function with `om = source_doc.get("option_backtest")` already in scope at line 211.

## [10] HIGH — The Backtest Lab premium path never forwards the form's starting capital — every premium-native account curve is hardcoded to ₹200,000

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/runtime.py:1209`
- category: dropped-config

**Evidence (agent-quoted, unverified):**
```
runtime.py:1209-1213 — note the absent `capital=`:
```
        pm_result = dispatch_full_backtest(
            strategy_id=req.strategy_id, merged_params=pm_params,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=underlying,
        )
```
premium_trigger_dispatch.py:281 `    capital: float = 200_000.0,` → line 319 `portfolio = build_rupee_equity_curve(paired_trades, capital=capital)`.
The ordinary path DOES honor it — option_backtest.py:836 `"portfolio": build_rupee_equity_curve(paired_trades, capital=sizing_cfg.capital),` fed from runtime.py:1433 `sizing_config=config.sizing_config`.
The optimizer's own premium branch proves the omission is an oversight — optimizer.py:683,696 `capital = float((option_cfg.get("sizing_config") or {}).get("capital", 200_000) or 200_000)` ... `instrument=instrument, capital=capital,`.
The form does send it — BacktestLab.jsx:576 `capital: Number(config.option_capital || 200000),`.
```

**Failure scenario:**
User sets Capital = ₹10,00,000 in the option form and runs a premium-native strategy. `build_rupee_equity_curve` starts at ₹2,00,000 instead, so: 'Highest/Lowest Acct Value' (the ONE card that populates), 'Capital', 'Ending equity', 'Return on capital' and 'Max DD %' are all wrong — Return-on-capital is inflated 5×, and `total_return_pct`/`max_drawdown_pct` are the numbers the survival/quality gates key on. The Option Execution card then prints "Capital ₹200,000" (BacktestLab.jsx:2126) as if the user had asked for it.

**Suggested fix:**
Pass it through at runtime.py:1209, exactly as optimizer.py:683 does: `capital=float(((config.sizing_config or {}).get("capital")) or 200_000)`, and add `"capital": <that value>` to the synthetic sizing_config dispatch returns (premium_trigger_dispatch.py:348-352) so the card and any downstream deployment pin report the real number.

## [11] HIGH — Premium path ignores the whole sizing_config — including the `max_lots` cap that would have stopped lots=100

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/premium_trigger_dispatch.py:348`
- category: dropped-config

**Evidence (agent-quoted, unverified):**
```
premium_trigger_dispatch.py:348-352 fabricates a sizing block from the strategy param instead of reading the user's:
```
        "sizing_config": {
            "mode": "fixed_lots",
            "fixed_lots": int(cfg.lots),
            "enabled": True,
        },
```
The ordinary path routes every trade through the cap — portfolio.py:93-95:
```
    if not cfg.enabled or cfg.mode == "fixed_lots":
        lots = max(1, int(cfg.fixed_lots or 1))
        lots = min(lots, max(1, cfg.max_lots))
```
with `max_lots: int = 10` (portfolio.py:43) and `mode: str = "premium_at_risk"` (portfolio.py:39) coming from the form (BacktestLab.jsx:579 `max_lots: Math.max(1, Number(config.option_max_lots || 10))`).
`_adapt_premium_trades_to_paired` applies no cap at all — premium_trigger_dispatch.py:200 `quantity = int(lot_size) * int(lots)`.
The optimizer could reach exactly 100 because `PremiumTriggerConfig` bounds it there: premium_trigger_config.py:102 `lots: int = Field(default=1, ge=1, le=100)` vs optimizer.py:242 `lo, hi = int(info.get("min", 0)), int(info.get("max", 100))`.
```

**Failure scenario:**
User selects premium_at_risk sizing, 1% risk/trade, max 10 lots. A premium-native run ignores every one of those: it sizes 100 fixed lots on every trade, un-capped, and then renders 'Account (rupee) — fixed lots' (BacktestLab.jsx:2124) as though fixed-lots were the user's choice. On the ordinary path the identical config would have clamped to 10 lots — a 10× P&L difference driven purely by which execution family the strategy belongs to.

**Suggested fix:**
Accept the caller's sizing_config in `dispatch_full_backtest` and apply it: at minimum clamp with the user's cap — `lots = min(int(cfg.lots), max(1, SizingConfig.from_dict(sizing_config).max_lots))` before `_adapt_premium_trades_to_paired`, and echo the REAL sizing_config (mode/capital/max_lots) in the returned envelope rather than the synthetic one. Separately, bound `lots` in the authored plugin's parameter_schema (`{'type':'int','default':2,'min':1,'max':10}`) so the optimizer cannot search it to 100 in the first place.

## [12] HIGH — A deployment created from a premium-native run trades a DIFFERENT lot count than the backtest that justified it (100 simulated → 10 traded, pin says 5)

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/strategy_deployments.py:122`
- category: backtest-live-divergence

**Evidence (agent-quoted, unverified):**
```
strategy_deployments.py:121-124,138 pins the synthetic block:
```
    if st == "backtest_run":
        ob = source_doc.get("option_backtest") or {}
        sizing_config = ob.get("sizing_config")
        lots = (ob.get("request") or {}).get("lots")
```
... `"sizing_config": SizingConfig.from_dict(sizing_config).to_dict(), "lots": max(1, lots_n),`
The pinned dict has NO `max_lots` key (premium_trigger_dispatch.py:348-352), so `SizingConfig.from_dict` (portfolio.py:60-65 only sets keys that are present) leaves the class default `max_lots: int = 10` (portfolio.py:43).
At trade time paper_auto.py:420-427 `cfg = SizingConfig.from_dict(sizing_config)` → `if cfg.enabled: sized = size_position(...)` → portfolio.py:94-95 clamps `min(100, 10) = 10`.
Meanwhile the pin's `lots` comes from a different source entirely: runtime.py:1216 `pm_result["request"] = config.model_dump()` — i.e. the option FORM's lots (the user's 5).
```

**Failure scenario:**
The user deploys the premium-native run whose headline P&L was simulated at 100 lots (`cfg.lots` from the optimized strategy params). The deployment pin records `lots: 5` (the form value) and `fixed_lots: 100` (the strategy param) — two different numbers — and paper/live actually sizes 10 lots because the missing `max_lots` key defaults to 10. Three different lot counts across simulate / pin / execute, none of them the 5 the user chose. The deployed P&L will be ~1/10th of the backtest's, with no warning anywhere.

**Suggested fix:**
Make the premium envelope's sizing_config a complete, honest replay record: include `capital`, `max_lots` (>= the lots actually simulated) and the `lots` the sim really used, and make runtime.py write `pm_result["request"]` with that same resolved lots value instead of the raw form config. Add an assertion/test that `deployment_sizing_from_source(...)` round-trips to the SAME lot count `_adapt_premium_trades_to_paired` used — the parity this pin exists to guarantee.

## [13] HIGH — 11 of the authored plugin's 19 declared params (leg_mode, entry_cutoff, exit_time, trail_x_pct/trail_y_pct, all six lazy_*) are silently dropped from the backtest — but honored live

- dim: `kpi-and-trade-list-surfacing`
- site: `backend/app/premium_trigger_dispatch.py:309`
- category: dropped-config

**Evidence (agent-quoted, unverified):**
```
premium_trigger_dispatch.py:309-312 passes ONLY the narrow config's translation:
```
    pm_result = run_premium_momentum_backtest(
        spot_df=spot_df, option_candles=option_candles, contracts=contracts,
        instrument=instrument, params=cfg.to_backtest_params(),
    )
```
`to_backtest_params` can emit only 14 keys (premium_trigger_config.py:166-187), and `extract_premium_trigger_config` filters the incoming dict to those same fields first (premium_trigger_dispatch.py:110 `present = {k: src[k] for k in _CONFIG_FIELDS if src.get(k) is not None}`).
The sim reads the dropped ones — premium_momentum_backtest.py:319-337:
```
    trail_x_pct = params.get("trail_x_pct")
    trail_y_pct = params.get("trail_y_pct")
    leg_mode = str(params.get("leg_mode") or "first_to_trigger").lower()
    lazy_enabled = bool(params.get("lazy_enabled") or False)
    ... lazy_momentum_pct / lazy_stop_pct / lazy_target_pct / lazy_trail_x_pct / lazy_trail_y_pct / lazy_moneyness
    entry_cutoff = normalize_hhmm(params.get("entry_cutoff"))
    exit_time = normalize_hhmm(params.get("exit_time"))
```
The plugin declares every one of them — algotest_option_buy_nifty.py:21,25-26,28-36 (`leg_mode: 'both'`, `trail_x_pct: 5`, `entry_cutoff: '15:09'`, `exit_time: '15:13'`, `lazy_enabled: True`, ...).
The LIVE path does NOT drop them — strategy_deployments.py:339-340 `if source == "premium_trigger" and cfg is not None: params.update(cfg.model_dump(exclude_none=True))` (merge OVER params, per its own docstring at 325-329: "replacing would silently erase every 5B setting").
```

**Failure scenario:**
`NF_CE_PE_EXP2_Base` is described as "momentum breakout with contingent lazy legs triggered upon primary leg stop-loss hits" and ships `lazy_enabled: True`, `leg_mode: 'both'`, an X-Y trail and a 15:13 exit. The Backtest Lab simulates NONE of that: no lazy leg is ever armed, leg_mode falls back to 'first_to_trigger', no trail, no entry cutoff, no timed exit. The reported P&L belongs to a strategy the user never authored — and when that same run is deployed, deployment_evaluator.py:513-514 feeds the FULL param set to the live engine, so live trades rules the backtest never validated. Backtest and live are different strategies.

**Suggested fix:**
Pass the caller's full param dict through, using the config only as an overlay — mirror strategy_deployments.effective_premium_params exactly: `sim_params = {**merged_params_filtered_to_engine_keys, **cfg.to_backtest_params()}`. `premium_trigger_allowed_keys()` / `ENGINE_PARAM_KEYS` (premium_trigger_dispatch.py:143-145) already enumerate the legal engine surface, so the filter is one set-intersection. Until that lands, the premium-native banner (BacktestLab.jsx:1973-1978) must name the dropped knobs instead of claiming the config is honored.

## [14] HIGH — Optimizer's premium-native path silently drops option_config.cost_config — every rank and every survival verdict is scored on GROSS P&L while the UI says "net"

- dim: `sizing-and-cost-precedence`
- site: `backend/app/optimizer.py:361`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
    result = dispatch_full_backtest(
        strategy_id=strategy.id, merged_params=merged_params,
        spot_df=spot_df, option_candles=option_candles, contracts=contracts,
        instrument=instrument)

# and the ordinary branch, for contrast, optimizer.py:901:
    cost_config = option_cfg.get("cost_config")

# and _option_rerank_premium_trigger, optimizer.py:861:
        pm_result = await asyncio.to_thread(
            dispatch_full_backtest, strategy_id=strategy.id, merged_params=merged,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=instrument,
        )
```

**Failure scenario:**
Optimizer page, evaluation_mode=option_rerank, "Apply rupee costs" ON (brokerage 20/order, spread 1%) — or Survival ON, which the route FORCES costs on for (routers/research.py:516 `option_costs_on = bool((oc.get("cost_config") or {}).get("enabled"))`, raising 400 otherwise). All three premium-native call sites (`_evaluate_premium_trigger` :361, `_option_rerank_premium_trigger` :861, `_survival_eval_oos_premium_trigger` :693) pass only `merged_params`, which is `strategy.merged_params()` — a strict allow-list over `parameter_schema` (strategies/base.py:98-109). `cost_config` is not a schema key for `premium_momentum` or `algotest_option_buy_nifty`, so `extract_premium_trigger_config` never sees it and `cfg.cost_config` is always None, i.e. `CostConfig.from_dict(None)` -> disabled -> net == gross (premium_momentum_backtest.py:366). At 100 lots x 65 lot size x ~2 sides x hundreds of sessions the dropped charges are lakhs of rupees. The ranked table then prints that gross figure under the caption "scored on modelled net rupee P&L" (Optimizer.jsx:2217), and the survival gate — whose entire stated purpose at research.py:512-515 is "else survival can pass a GROSS option curve (no spread/brokerage/STT) as deployable" — passes exactly that gross curve.

**Suggested fix:**
Thread the run's option execution terms into the premium path the same way the ordinary path does: pass `option_cfg` down to `_evaluate_premium_trigger` / `_option_rerank_premium_trigger` / `_survival_eval_oos_premium_trigger` and inject `cost_config` (and `lots`, see the separate finding) into `merged_params` before `dispatch_full_backtest`, e.g. `merged = {**merged, **({'cost_config': option_cfg['cost_config']} if option_cfg.get('cost_config') else {})}`. Alternatively make `dispatch_full_backtest` take explicit `cost_config`/`lots` overrides so no caller can forget them.

## [15] HIGH — Deployed size != backtested size: the premium sizing_config omits max_lots, so live/paper silently clamps to 10 lots while the backtest ran unclamped

- dim: `sizing-and-cost-precedence`
- site: `backend/app/premium_trigger_dispatch.py:348`
- category: backtest-live-parity

**Evidence (agent-quoted, unverified):**
```
        "sizing_config": {
            "mode": "fixed_lots",
            "fixed_lots": int(cfg.lots),
            "enabled": True,
        },

# portfolio.py:44 (default filled in by SizingConfig.from_dict for the missing key):
    max_lots: int = 10                     # hard cap on sized lots

# portfolio.py:93-95 (what live actually computes):
    if not cfg.enabled or cfg.mode == "fixed_lots":
        lots = max(1, int(cfg.fixed_lots or 1))
        lots = min(lots, max(1, cfg.max_lots))
```

**Failure scenario:**
Backtest a premium-native strategy at lots=100 (the optimizer's unbounded search reaches exactly that; `PremiumTriggerConfig.lots` allows `le=100`). `_adapt_premium_trades_to_paired` (premium_trigger_dispatch.py:200) sizes every trade at `quantity = lot_size * 100` with NO max_lots clamp, so the reported P&L / equity curve is a 100-lot result. Deploy that run: `deployment_sizing_from_source` pins `SizingConfig.from_dict({...no max_lots...}).to_dict()` -> `max_lots: 10`. At signal time `resolve_deployment_lots` (paper_auto.py:421-427) calls `size_position`, hits the fixed_lots branch above and returns `min(100, 10) = 10`. The user trades one tenth of the position the backtest justified — and there is no warning anywhere. The ordinary path does not have this gap: option_backtest.py:754-760 runs the SAME `size_position` inside the backtest, so its backtested lots are already clamped and match live.

**Suggested fix:**
Either route the premium adapter's lot count through `size_position` (so the backtest is clamped identically to live), or emit the full canonical policy — `SizingConfig(mode='fixed_lots', fixed_lots=int(cfg.lots), max_lots=max(10, int(cfg.lots)), enabled=True).to_dict()` — so the pin round-trips the size the backtest actually used. Add a test asserting `resolve_deployment_lots(pin) == trades[0]['lots']` for a premium-native run.

## [16] HIGH — The premium-native banner's "its lots ... are honored" is false whenever the strategy declares a `lots` param — the form's Lots is silently overridden

- dim: `sizing-and-cost-precedence`
- site: `backend/app/runtime.py:1190`
- category: ui-claim-false

**Evidence (agent-quoted, unverified):**
```
        pm_params["lots"] = int(req.params.get("lots") or config.lots or 1)

# BacktestLab.jsx:1978 (the claim):
            params; the option-pairing form's moneyness is not used, while its lots &amp; cost model are honored.

# strategies/plugins/algotest_option_buy_nifty.py:27 (why req.params always carries lots):
        'lots': {"type": 'int', "default": 2},
```

**Failure scenario:**
The frontend seeds `config.params` from every key in `parameter_schema` (BacktestLab.jsx:518-522) and posts it verbatim as `params` (BacktestLab.jsx:537). For `algotest_option_buy_nifty`, `req.params['lots']` is therefore ALWAYS present and truthy (2 by default, 100 after loading an optimized preset), so the `or` chain at runtime.py:1190 never falls through to `config.lots` — the Option Execution form's Lots field is a dead control. User sets Lots=5 in the form, the run executes at 100, and the banner two panes above tells them the form value was honored. The same sentence IS true for the shipped `premium_momentum` (its schema has no `lots` key), which is exactly why the falsehood went unnoticed. Note also that `lots=0` — reachable because `_suggest` defaults an unbounded int to [0,100] — is falsy here, so a 0-lot optimizer trial silently re-sizes to the form's lots in the Lab while `PremiumTriggerConfig(lots=0)` fails `ge=1` in the optimizer, i.e. the same params produce different sizes on the two paths.

**Suggested fix:**
Pick one owner of `lots` for premium-native runs and make the UI say so. Simplest honest fix: make the form authoritative (`pm_params['lots'] = int(config.lots or req.params.get('lots') or 1)`) and reword the banner; or keep the strategy param authoritative, disable the form's Lots input for premium-native strategies, and change the banner to "lots come from the strategy's `lots` param; the form's lots is not used". Use `is not None` rather than `or` so 0 fails loudly instead of silently re-sizing.

## [17] HIGH — The user's capital never reaches the premium-native sim — Account (rupee), Return %, Max DD % and Highest/Lowest Acct Value are all computed off a hardcoded Rs 200,000

- dim: `sizing-and-cost-precedence`
- site: `backend/app/runtime.py:1209`
- category: wrong-number

**Evidence (agent-quoted, unverified):**
```
        pm_result = dispatch_full_backtest(
            strategy_id=req.strategy_id, merged_params=pm_params,
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=underlying,
        )

# premium_trigger_dispatch.py:281 (the default that is therefore always used):
    capital: float = 200_000.0,

# premium_trigger_dispatch.py:319:
    portfolio = build_rupee_equity_curve(paired_trades, capital=capital)

# option_backtest.py:836 (the ordinary path, for contrast):
        "portfolio": build_rupee_equity_curve(paired_trades, capital=sizing_cfg.capital),
```

**Failure scenario:**
User turns on "Capital & position sizing", sets Capital = Rs 10,00,000 (BacktestLab.jsx:576 posts it as `option_backtest.sizing_config.capital`). `_run_paired_option_backtest`'s premium branch reads `config.lots` and `config.cost_config` but never `config.sizing_config`, and calls `dispatch_full_backtest` with no `capital=` — so the equity curve starts at Rs 200,000. `portfolio.total_return_pct` and `max_drawdown_pct` are then 5x overstated (portfolio.py:174 `net_pnl / capital * 100`, :158 `dd_value / peak * 100`), the Option Execution card prints "Capital Rs 200,000" (BacktestLab.jsx:2126) contradicting the form, and `buildPerformanceSeries` takes `capital = portfolio.starting_capital` (backtestMetrics.js:56) so the Lowest/Highest Acct Value cards and the whole account chart are drawn on the wrong base. With 100 lots the curve also runs deeply "impossible" against a 2-lakh account without any ruin flag. The optimizer has the same omission asymmetrically: `_survival_eval_oos_premium_trigger` DOES pass capital (optimizer.py:683/696) while `_evaluate_premium_trigger` (:361) and `_option_rerank_premium_trigger` (:861) do not, so Stage-1/Stage-2 and the survival gate score the same candidate on two different capital bases.

**Suggested fix:**
In runtime.py's premium branch pass `capital=float((config.sizing_config or {}).get('capital', 200_000) or 200_000)` into `dispatch_full_backtest`, mirroring optimizer.py:683; do the same at optimizer.py:361 and :861. Consider making `capital` a required keyword on `dispatch_full_backtest` so a caller cannot silently inherit 200k.

## [18] HIGH — dispatch drops every authored premium param outside PremiumTriggerConfig — the trail, both-leg mode, entry cutoff, exit time and all lazy legs never run, though the sim supports them

- dim: `sizing-and-cost-precedence`
- site: `backend/app/premium_trigger_dispatch.py:110`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
    present = {k: src[k] for k in _CONFIG_FIELDS if src.get(k) is not None}

# premium_trigger_config.py:178-182 — only these exit knobs are forwarded:
        for k in ("stop_pct", "stop_pts", "target_pct", "target_pts",
                  "trail_x", "trail_y"):
            v = getattr(self, k)
            if v is not None:
                params[k] = v

# premium_momentum_backtest.py:100 and :108 — what the sim actually accepts:
    "trail_x", "trail_y", "trail_x_pct", "trail_y_pct",
    "lazy_trail_x", "lazy_trail_y", "lazy_trail_x_pct", "lazy_trail_y_pct",
```

**Failure scenario:**
`algotest_option_buy_nifty` declares `trail_x_pct=5, trail_y_pct=5, leg_mode='both', entry_cutoff='15:09', exit_time='15:13', lazy_enabled=True, lazy_momentum_pct=10, lazy_stop_pct=10, lazy_target_pct=50, lazy_trail_x_pct=5, lazy_trail_y_pct=5` (plugin lines 21-36). `_CONFIG_FIELDS` is `PremiumTriggerConfig.model_fields`, which contains `trail_x`/`trail_y` but not the `_pct` variants and none of the 5B keys — so all eleven are filtered out at :110 and `to_backtest_params()` emits none of them. `run_premium_momentum_backtest` reads every one of them (:319-337) and falls back to its own defaults: no stepped trail (`_resolve_trail(None,None,None,None) -> None`), `leg_mode` defaults to `'first_to_trigger'` instead of `'both'` (:323) so only ONE leg per session trades instead of two, no entry cutoff, no exit_time square, no lazy reversal leg. The result the user is shown is a completely different strategy from the one they authored — roughly half the exposure, unprotected by the trail — and nothing reports the drop (the `"absent"` vs `"invalid"` distinction at :112-117 only fires when the config is unparseable, never when extra knobs are discarded). The frontend banner reinforces the illusion by saying "Strike selection follows the ... strategy params".

**Suggested fix:**
Stop making the narrow `PremiumTriggerConfig` the filter for what reaches the sim. Forward the full `ENGINE_PARAM_KEYS` intersection (the union already computed by `premium_trigger_allowed_keys()`) as passthrough params alongside `cfg.to_backtest_params()`, or widen `PremiumTriggerConfig` to the shipped surface. At minimum, compute `dropped = set(merged_params) & set(ENGINE_PARAM_KEYS) - set(cfg.to_backtest_params())` and return it on the envelope so the UI can render "these declared params were not applied" instead of silently running a different strategy.

## [19] HIGH — Backtest dispatch silently drops the ENTIRE lazy-leg contingency — the authored strategy's headline feature is never simulated

- dim: `dropped-config-fields`
- site: `backend/app/premium_trigger_dispatch.py:110`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
_CONFIG_FIELDS = tuple(PremiumTriggerConfig.model_fields)   # line 80
...
    src = dict(params or {})
    present = {k: src[k] for k in _CONFIG_FIELDS if src.get(k) is not None}
    if not present:
        return None, "absent"
```

**Failure scenario:**
The plugin `algotest_option_buy_nifty.py` (lines 30-36) declares lazy_enabled=True, lazy_moneyness='itm1', lazy_momentum_pct=10, lazy_stop_pct=10, lazy_target_pct=50, lazy_trail_x_pct=5, lazy_trail_y_pct=5. `PremiumTriggerConfig` has NONE of these seven fields, so the dict-comprehension at line 110 discards all seven before the model is constructed. `to_backtest_params()` therefore never emits `lazy_enabled`, and `premium_momentum_backtest.py:324` evaluates `bool(params.get("lazy_enabled") or False)` -> False, so the whole `if lazy_enabled:` block at line 514 is skipped. The plugin's own description string is 'Intraday momentum breakout strategy with contingent lazy legs triggered upon primary leg stop-loss hits' — the reversal legs that ARE the strategy produce zero trades in the backtest, and coverage counters lazy_armed/lazy_entered are all 0. The user sees P&L, win rate and trade count for a single-leg strategy they never configured, with no indication that half the ruleset was discarded.

**Suggested fix:**
Stop routing the execution config through the narrow `PremiumTriggerConfig` allow-list. Either (a) pass `merged_params` filtered by `premium_momentum_backtest.ENGINE_PARAM_KEYS` straight to `run_premium_momentum_backtest` (keeping `PremiumTriggerConfig` only for its cross-field validators on the core subset), or (b) carry the non-core keys alongside: `params = {**{k: v for k, v in merged_params.items() if k in ENGINE_PARAM_KEYS}, **cfg.to_backtest_params()}`. At minimum, return the dropped key names in the result envelope so the UI can refuse to show numbers.

## [20] HIGH — `leg_mode` is dropped, so a 'both legs' strategy is silently backtested as single-leg first_to_trigger

- dim: `dropped-config-fields`
- site: `backend/app/premium_momentum_backtest.py:323`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
    leg_mode = str(params.get("leg_mode") or "first_to_trigger").lower()
...
        if leg_mode == "both":
            chosen = entered_candidates
        else:
            chosen = []
            if entered_candidates:
                chosen = [min(entered_candidates, key=lambda c: c[3]["entry_ts"])]
```

**Failure scenario:**
The plugin declares `'leg_mode': {"type": 'str', "default": 'both'}` (algotest_option_buy_nifty.py:21). `PremiumTriggerConfig` has no `leg_mode` field, so premium_trigger_dispatch.py:110 drops it and the sim falls back to 'first_to_trigger'. On any session where both the CE and the PE premium cross the 15% momentum trigger, only the EARLIEST entry is kept (line 491) and the other leg is discarded entirely. The user configured up to 2 trades/session and gets at most 1 — trade count, net P&L, win rate and max drawdown are all computed over roughly half the intended trade population. The drop is direction-biased too: whichever side moves first always wins, so the retained sample systematically over-represents fast-moving sessions.

**Suggested fix:**
Include `leg_mode` in whatever param dict reaches `run_premium_momentum_backtest` (see the dispatch fix). Until then, `dispatch_full_backtest` must REFUSE (return None with a surfaced reason) when `merged_params` contains a key in `ENGINE_PARAM_KEYS` that `PremiumTriggerConfig` cannot carry, rather than running a differently-configured strategy.

## [21] HIGH — `entry_cutoff` and `exit_time` are dropped — the backtest enters after the configured cutoff and holds past the configured square-off

- dim: `dropped-config-fields`
- site: `backend/app/premium_momentum_backtest.py:336`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
    entry_cutoff = normalize_hhmm(params.get("entry_cutoff"))
    exit_time = normalize_hhmm(params.get("exit_time"))
...
        cutoff_ts: Optional[int] = None
        if entry_cutoff:
            cutoff_rows = sdf[sdf["ist_time"] >= str(entry_cutoff)]
            if not cutoff_rows.empty:
                cutoff_ts = int(cutoff_rows.iloc[0]["ts"])
        exit_ts_bound = session_end_ts
        if exit_time:
            exit_rows = sdf[sdf["ist_time"] >= str(exit_time)]
            if not exit_rows.empty:
                exit_ts_bound = min(session_end_ts, int(exit_rows.iloc[0]["ts"]))
```

**Failure scenario:**
The plugin declares entry_cutoff='15:09' and exit_time='15:13' (algotest_option_buy_nifty.py:28-29). Neither exists on `PremiumTriggerConfig`, so both are dropped at premium_trigger_dispatch.py:110 and arrive as None. Result: `cutoff_ts` stays None, so `walk_premium_momentum` is called with `entry_cutoff_ts=None` (line 481) and a momentum entry at 15:25 is accepted; and `exit_ts_bound` falls back to `session_end_ts`, so every unstopped position is held ~16 extra minutes to the 15:29 EOD bar instead of squaring at 15:13. On expiry days that window is where 0-DTE premium decays hardest, so both the trade population and the per-trade exit price are wrong — in the user's favour or against it depending on the day, but never the strategy they configured.

**Suggested fix:**
Same dispatch fix — `entry_cutoff`/`exit_time` are already in `ENGINE_PARAM_KEYS` (premium_momentum_backtest.py:102) and fully implemented by the sim; only the config filter blocks them. They must not be silently defaulted to None when the strategy declared a value.

## [22] HIGH — `trail_x_pct`/`trail_y_pct` are dropped, so the configured stepped trailing stop never runs (config carries only the POINTS pair)

- dim: `dropped-config-fields`
- site: `backend/app/premium_trigger_config.py:178`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
        for k in ("stop_pct", "stop_pts", "target_pct", "target_pts",
                  "trail_x", "trail_y"):
            v = getattr(self, k)
            if v is not None:
                params[k] = v
```

**Failure scenario:**
The plugin declares trail_x_pct=5 and trail_y_pct=5 (algotest_option_buy_nifty.py:25-26) — the PERCENT-of-entry ratchet. `PremiumTriggerConfig` only models `trail_x`/`trail_y`, the absolute POINTS pair (lines 91-99), so the two `_pct` keys are dropped at premium_trigger_dispatch.py:110 and `to_backtest_params()` emits neither pair. In the sim, `trail_x/trail_y/trail_x_pct/trail_y_pct` are all None (lines 317-320) and `_resolve_trail` returns None (line 163) — its docstring even notes 'A PARTIAL pair ... silently produces no trail'. `walk_premium_momentum` runs with `trail=None`: no ratchet, so every winner runs to the 50% target or the 20% hard stop instead of being protected. Profit factor, win rate and average win are all computed for a strategy with no trailing stop. Worse, the two pairs use different UNITS, so nothing in the pipeline could even coerce one into the other — the silence is total.

**Suggested fix:**
Add `trail_x_pct`/`trail_y_pct` to whatever param dict reaches the sim (they are in `ENGINE_PARAM_KEYS` already and `_resolve_trail` handles the XOR). Do NOT map `trail_x_pct` onto `trail_x` — the units differ and `_resolve_trail` raises on both pairs being set.

## [23] HIGH — Backtest and LIVE deployment run different strategies: the deployment path deliberately preserves the 12 fields the backtest silently discards

- dim: `dropped-config-fields`
- site: `backend/app/strategy_deployments.py:322`
- category: backtest-live-parity

**Evidence (agent-quoted, unverified):**
```
def effective_premium_params(deployment):
    """The params a premium-native deployment should actually run.

    The deployment's ``premium_trigger`` block is MERGED OVER ``params``, never
    substituted for them: the session engine reads ``leg_mode``, the five
    ``lazy_*`` fields, ``entry_cutoff``, ``exit_time``, the session P&L caps and
    ``vix_min``/``vix_max`` from params, and none of those exist on
    ``PremiumTriggerConfig`` — replacing would silently erase every 5B setting."""
    params = dict((deployment or {}).get("params") or {})
    cfg, source = resolve_deployment_premium_trigger(deployment)
    if source == "premium_trigger" and cfg is not None:
        params.update(cfg.model_dump(exclude_none=True))
    return params
```

**Failure scenario:**
The live/paper session engine reads exactly the fields the backtest drops — `premium_momentum_live.py:100` (`params.get("leg_mode")`), `:150`/`:170`/`:233` (`params.get("lazy_enabled")`), `:236` (`entry_cutoff`), and the module docstring at `:20-29` documents both. `effective_premium_params` was written specifically so a deployment does NOT lose them. The backtest path took the opposite decision. So the user backtests a single-leg, no-trail, hold-to-EOD strategy, sees its P&L, and deploys — and the deployment trades a two-leg strategy with lazy reversals, a 5%/5% ratchet, a 15:09 entry cutoff and a 15:13 square-off. The backtest numbers that authorised the deployment describe none of its live behaviour, and no reconciliation surface flags the divergence.

**Suggested fix:**
Make the backtest consume `effective_premium_params`-shaped params (raw params filtered by `ENGINE_PARAM_KEYS`) so the two paths read the identical dict, and add a parity test asserting that for a given deployment doc the keys reaching `run_premium_momentum_backtest` equal the keys reaching the live session engine.

## [24] HIGH — The optimizer scores EVERY premium-native trial with option costs disabled, ignoring both the job's costs flag and the option form's cost_config

- dim: `dropped-config-fields`
- site: `backend/app/optimizer.py:361`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
    result = dispatch_full_backtest(
        strategy_id=strategy.id, merged_params=merged_params,
        spot_df=spot_df, option_candles=option_candles, contracts=contracts,
        instrument=instrument)
```

**Failure scenario:**
`_evaluate_premium_trigger` (Stage 1), `_option_rerank_premium_trigger` (:861-865) and `_survival_eval_oos_premium_trigger` (:693-697) all call `dispatch_full_backtest` with `merged_params` only — none of them passes `costs` or `option_cfg["cost_config"]`. `dispatch_full_backtest` reads costs solely from `cfg.cost_config` (premium_trigger_dispatch.py:311), which comes from the strategy params; `algotest_option_buy_nifty.py` declares no `cost_config`, so `CostConfig.from_dict(None)` returns `cls()` with `enabled: bool = False` (option_costs.py:61) and net_pnl == gross_pnl. The ordinary-strategy paths do the opposite — `_survival_eval_oos` passes `cost_config=option_cfg.get("cost_config")` (:787) and `_option_rerank` passes it at :1008. Consequence: the optimizer ranks and promotes premium-native candidates on ZERO-COST P&L, and the survival gate (a real money gate) evaluates equity floor / drawdown / RoR with brokerage, STT, GST and bid-ask spread all set to zero. Then `_save_best_as_backtest` (:601) replays the winner through `_run_paired_option_backtest`, which DOES apply the form's cost model (runtime.py:1191-1194) — so the saved 'Optimized ·' run reports materially lower P&L than the trial score that selected it, with no explanation.

**Suggested fix:**
Thread the caller's cost model into the premium dispatch: add a `cost_config` (and `costs_enabled`) parameter to `dispatch_full_backtest` that overrides `cfg.cost_config` when supplied, and pass `option_cfg.get("cost_config")` from all three optimizer premium call sites, exactly as the ordinary paths already do.

## [25] HIGH — Optimizer's saved "best" backtest_run is produced by the inert spot stub — zero metrics, zero trades, zero equity curve for every premium-native run

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:576`
- category: missing-premium-branch

**Evidence (agent-quoted, unverified):**
```
res = await asyncio.to_thread(run_backtest, df_enriched, strategy, merged, instrument=instrument, costs_enabled=costs_enabled, pretrade_filters=pretrade)
        metrics = res["metrics"]
        ...
            "metrics": metrics,
            "trades": res["trades"],
            "equity_curve": res["equity_curve"],
```

**Failure scenario:**
`_save_best_as_backtest` is the ONLY premium-relevant site in optimizer.py with no `is_premium_trigger_strategy` guard (the six guarded sites are 737, 893, 1159, 1197, 1264, 1284). For `algotest_option_buy_nifty` whose `evaluate()` returns `Signal(direction="NONE")` (algotest_option_buy_nifty.py:44), `run_backtest` yields trades=[] and zero metrics, and those are persisted as the run's top-level `metrics`/`trades`/`equity_curve` (optimizer.py:624-626). The Backtest Lab then renders `m = result.metrics` into the Trades / Win Rate / Profit Factor / Net P&L / Max DD / Sharpe cards (BacktestLab.jsx:1933-1938) and `result.trades` into the Trades pane (BacktestLab.jsx:2006) -> all blank/zero, while "Highest Acct Value" (BacktestLab.jsx:1946) is derived from `option_backtest.portfolio` -> a large number. This is exactly reported symptoms 1 and 2. Worse: `option_config` is passed only when `evaluation_mode == "option_rerank"` (optimizer.py:1707), so in the DEFAULT "spot" mode the saved run has no `option_backtest` at all and is completely empty. `walk_forward` at line 580 also burns minutes producing a meaningless zero-trade WFO.

**Suggested fix:**
Add the seventh guard: when `is_premium_trigger_strategy(strategy)`, build the run via `dispatch_full_backtest` (reusing the Stage-1 preload) and hoist its `metrics`/`trades`/`portfolio` into the doc's top-level `metrics`/`trades`/`equity_curve` (or persist a `dispatch: premium_trigger_config` marker the Lab honours), and skip `walk_forward`.

## [26] HIGH — dispatch drops 8 of the 12 optimized dimensions: leg_mode, entry_cutoff, exit_time, trail_*_pct and every lazy_* knob never reach the engine

- dim: `optimizer-premium-path`
- site: `backend/app/premium_trigger_config.py:178`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
        for k in ("stop_pct", "stop_pts", "target_pct", "target_pts",
                  "trail_x", "trail_y"):
            v = getattr(self, k)
            if v is not None:
                params[k] = v
```

**Failure scenario:**
`_CONFIG_FIELDS = tuple(PremiumTriggerConfig.model_fields)` (premium_trigger_dispatch.py:80) filters merged_params down to 14 keys, and `to_backtest_params()` emits only those. The sim declares 34 keys it actually reads (premium_momentum_backtest.py:96-109), including `leg_mode`, `entry_cutoff`, `exit_time`, `trail_x_pct`, `trail_y_pct` and the whole `lazy_*` family. For NF_CE_PE_EXP2_Base this silently converts the declared config into a different strategy: `leg_mode='both'` -> engine default `first_to_trigger` (premium_momentum_backtest.py:323 and 486, i.e. at most ONE leg per session instead of two), `lazy_enabled=True` -> False (line 324) even though the plugin's own description is "contingent lazy legs triggered upon primary leg stop-loss hits", `entry_cutoff='15:09'`/`exit_time='15:13'` -> None (lines 336-337), `trail_x_pct/trail_y_pct=5` -> no trail (lines 317-320). Because `_build_param_space` (optimizer.py:202-215) admits every int/float/bool, the search space is 12-dimensional but only `momentum_pct`, `stop_pct`, `target_pct` and `lots` can move the objective: 8 dimensions are pure noise, so the TPE surrogate, `parameter_importance` (optimizer.py:1499) and every reported importance figure are fit on parameters that provably cannot change any metric.

**Suggested fix:**
Either widen `PremiumTriggerConfig`/`to_backtest_params()` to the full `ENGINE_PARAM_KEYS` surface, or make `extract_premium_trigger_config` refuse (return `invalid:`) when a param the engine reads is present but unrepresentable — silent narrowing is the failure mode. Independently, exclude keys not in the dispatchable surface from `_build_param_space` for premium strategies.

## [27] HIGH — Optimizer scores every premium trial with the cost model OFF while the run it saves applies the form's cost model

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:1200`
- category: config-divergence

**Evidence (agent-quoted, unverified):**
```
                return _evaluate_premium_trigger(
                    strategy, strategy.merged_params(params), pm_spot_df,
                    pm_option_candles, pm_contracts, instrument,
                    objective, lot_size, min_trades, min_direction_share)
```

**Failure scenario:**
`merged_params` is a strict allow-list keyed on `parameter_schema` (strategies/base.py:98-109) and the authored plugin declares no `cost_config`, so `cfg.cost_config` is None — and per premium_trigger_config.py:112-117 "When None, costs are disabled and net_pnl == gross_pnl". Every Stage-1 trial (optimizer.py:1200), every Stage-2 re-rank sim (optimizer.py:862) and every survival fold (optimizer.py:694) therefore scores GROSS premium P&L, ignoring `option_config.cost_config` which the Optimizer UI always sends when option costs or survivability are on (Optimizer.jsx:426-430). The Backtest Lab path does the opposite: it re-applies `config.cost_config` after the merge (runtime.py:1191-1194), and `_save_best_as_backtest` passes `option_cfg` straight through (optimizer.py:1705). Net result in one job document: `best_value` / `rerank.ranked[*].option_pnl_value` are pre-cost while `finished["best_option_pnl_value"]` (optimizer.py:1756) is post-cost — two different rupee P&Ls for the identical params, and the winner was selected on the pre-cost one.

**Suggested fix:**
In the premium branches, mirror runtime.py:1190-1194: after `strategy.merged_params(params)`, re-apply `option_cfg.get('cost_config')` (and `option_cfg['lots']` if the form is meant to govern size) before calling `dispatch_full_backtest`.

## [28] HIGH — Stage-2 premium re-rank ranks on option_pnl_value, which is exactly linear in the unbounded `lots` param — a position-size maximizer that overrides Stage 1

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:880`
- category: objective-degenerate

**Evidence (agent-quoted, unverified):**
```
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)
```

**Failure scenario:**
`option_pnl_value` is `total_option_pnl_value` (optimizer.py:873), the sum of per-trade `option_pnl_value` whose quantity is `int(lot_size) * int(lots)` (premium_trigger_dispatch.py:200) — exactly linear in `lots`. For an ORDINARY strategy `lots` is a constant read from the form (`lots = int(option_cfg.get("lots") or 1)`, optimizer.py:897) so this ranking is size-neutral; for a premium-native strategy `lots` is a searched param, so among any two profitable configs the one with more lots always wins. The same monotone key governs the survivor sort (optimizer.py:1600) and the final promotion (optimizer.py:1642 `"value": best["option_pnl_value"]`). This means bounding the Stage-1 objective alone cannot stop lots -> 100: whichever candidate carries the largest lots and is profitable is promoted as `best_params`, which is what the user saw.

**Suggested fix:**
Normalise the re-rank key to a size-invariant quantity (per-lot rupee P&L, or return-on-capital at a fixed lot count), and/or exclude `lots` from the premium search space entirely and take it from `option_config.lots` like every other strategy does.

## [29] HIGH — net_pnl_inr multiplies option premium points by lot_size and silently discards `lots`; risk_adjusted divides a Sharpe by a RUPEE drawdown

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:173`
- category: unit-mismatch

**Evidence (agent-quoted, unverified):**
```
        return float(metrics.get("total_pnl_pts", 0) or 0) * float(lot_size)
```

**Failure scenario:**
For a premium run `_evaluate_premium_trigger` maps `"total_pnl_pts": float(m.get("total_option_pnl_pts", 0))` (optimizer.py:394) — per-UNIT premium points — so `net_pnl_inr` = points x lot_size is the ONE-LOT rupee P&L and ignores `lots` completely: with the winning lots=100 the objective reports 1/100th of the trade list's own `total_option_pnl_value`, i.e. the KPI the user later sees. The same block sets `"max_dd_pts": abs(float(port.get("max_drawdown_value", 0)))` (optimizer.py:396-398) — a RUPEE magnitude — which the default `risk_adjusted` objective feeds into `sharpe / max(1.0, dd / 100.0)` (optimizer.py:179-181). A spot run's index-point drawdown makes that denominator 1-5; a premium run's rupee drawdown of ~50,000 makes it 500. Since `sharpe_daily` is scale-invariant (portfolio.py:177-183 divides daily P&L by capital, so mean and std scale together) while `max_drawdown_value` scales linearly with `lots`, the DEFAULT objective reduces to roughly `sharpe / lots`: Stage 1 actively pushes lots toward 1 while Stage 2 pushes it to 100, and the two stages optimise opposite directions of the same parameter.

**Suggested fix:**
For premium metrics, set `total_pnl_pts` from `total_option_pnl_value / lot_size` (or add a `net_pnl_inr` that reads `total_option_pnl_value` directly) and express `max_dd_pts` as a drawdown PERCENT (or divide the rupee DD by lot_size x lots) so `risk_adjusted` stays on the scale it was designed for.

## [30] HIGH — Deployment sizing pinned from an optimizer-saved premium run carries two contradictory lot counts, and max_lots silently clamps 100 lots to 10 live

- dim: `optimizer-premium-path`
- site: `backend/app/premium_trigger_dispatch.py:348`
- category: round-trip-mismatch

**Evidence (agent-quoted, unverified):**
```
        "sizing_config": {
            "mode": "fixed_lots",
            "fixed_lots": int(cfg.lots),
            "enabled": True,
        },
```

**Failure scenario:**
`dispatch_full_backtest` writes `sizing_config.fixed_lots = cfg.lots` (=100, the OPTIMIZED param) while the same envelope's `request` is the option FORM (`pm_result["request"] = config.model_dump()`, runtime.py:1216) carrying the user's lots=5. `deployment_sizing_from_source` then reads them from those two different places — `sizing_config = ob.get("sizing_config")` and `lots = (ob.get("request") or {}).get("lots")` (strategy_deployments.py:122-124) — pinning `{"sizing_config": {"mode":"fixed_lots","fixed_lots":100,"max_lots":10,"enabled":True}, "lots": 5}`. At execution time `paper_auto.py:420-432` sees `cfg.enabled` True, so it takes the `size_position` branch and IGNORES `pin["lots"]`; `size_position` then applies `lots = min(lots, max(1, cfg.max_lots))` (portfolio.py:94-95) with the defaulted `max_lots=10` -> the deployment trades 10 lots against a backtest that reported 100-lot P&L, a 10x overstatement, while the user's own 5 is discarded by both.

**Suggested fix:**
Emit `max_lots` explicitly (>= fixed_lots) in the dispatch's sizing_config, and make `_adapt_premium_trades_to_paired` route its lot count through `size_position` so the backtest and the pinned deployment cannot disagree. Also co-write `request.lots` from the same `cfg.lots` the sizing block uses.

## [31] MEDIUM — `_robustness_score` counts perturbations of inert params as passes, inflating the reported robustness score (50 pp for shipped `premium_momentum`)

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:431`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
            metrics, _ = evaluate_fn(test_params)
            val = obj(metrics)
            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5
            n_total += 1
            if ok:
                n_ok += 1
```

**Failure scenario:**
The shipped `premium_momentum` schema leaves exactly four dims in the search space: `momentum_pct`, `stop_pct`, `lazy_momentum_pct`, `lazy_stop_pct` (everything else is `"fixed"` or `str`-typed). The two `lazy_*` dims are inert twice over: they are not `PremiumTriggerConfig` fields so premium_trigger_dispatch.py:110 drops them, and `lazy_enabled` is pinned `"fixed": False` at premium_momentum.py:70 anyway — the file's own test even calls them 'harmless dead weight in the general Optimizer' (tests/test_premium_momentum_plugin.py:91-95). They are not harmless here: perturbing them returns byte-identical metrics, so `val == base_val`, so for any profitable best trial (`base_val > 0`) `val >= base_val * 0.85` is trivially true and all 8 of their 16 perturbations count as `ok`. The score is `n_ok / n_total * 100`, so half the denominator is guaranteed-pass filler: a strategy whose two REAL params are both fragile still reports 50, and one where the real params are half-robust reports 75 -> `RobustnessCard` (frontend/src/pages/Optimizer.jsx:1987-1988) prints 'ROBUST' in green. For the authored plugin the ratio would be 32 of 48 guaranteed passes (67 pp).

**Suggested fix:**
Skip params the evaluator does not consume, and treat a perturbation whose metrics are byte-identical to the base as 'no information' (excluded from n_total) rather than as a pass.

## [32] MEDIUM — `_robustness_score` and `_heatmap` both KeyError on unbounded params, so the two headline quality checks silently disappear from the results page

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:424`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
            t_v = base_v * (1 + pct)
            t_v = max(float(info["min"]), min(float(info["max"]), t_v))
```

**Failure scenario:**
Same unguarded `info["min"]`/`info["max"]` reads appear at optimizer.py:424 (robustness clamp) and optimizer.py:463 (`np.linspace(info_a["min"], info_a["max"], grid_n)`). `evaluation_mode` defaults to "spot" (schemas.py:196), so for `algotest_option_buy_nifty` both are invoked at optimizer.py:1685 and 1689 and both raise `KeyError: 'min'` on the first param. The surrounding handlers only `log.warning(f"heatmap failed: {e}")` / `log.warning(f"robustness failed: {e}")` (optimizer.py:1686-1691), leaving `heatmap=None, robustness=None` in the persisted job. The UI then renders the bare string 'Robustness not computed' (frontend/src/pages/Optimizer.jsx:1984) and hides the heatmap — with no explanation, on precisely the run whose lots=100 numbers most need a sanity check. A user reasonably reads a missing robustness card as 'not applicable', not 'the analysis crashed'.

**Suggested fix:**
Resolve effective bounds once in `_build_param_space` (so every downstream consumer sees the same numbers), and when an analysis is skipped, persist a `heatmap_skipped_reason` / `robustness_skipped_reason` the UI can show instead of a silent blank.

## [33] MEDIUM — A schema-level `"fixed"` silently voids any min/max the user types in the Search Bounds panel

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:236`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```

**Failure scenario:**
`_build_param_space` copies the schema entry wholesale (`info = dict(defn)`, optimizer.py:207) and then layers the user's overrides on top — but it only replaces `fixed` when the OVERRIDE supplies one (optimizer.py:209-210); a schema `"fixed"` survives, and `_suggest` returns it before it ever looks at min/max. Meanwhile the frontend's bounds editor lists EVERY int/float param (`numericParams`, frontend/src/pages/Optimizer.jsx:323-327) — including `premium_momentum`'s `momentum_pts`, `target_pct`, `session_max_loss_rupees`, `session_max_profit_rupees`, `vix_min`, `vix_max`, all of which carry `"fixed": None` — and sends only `{min, max}` (Optimizer.jsx:1343,1347-1348). So a user who opens 'Parameter Search Bounds (advanced)' and sets `target_pct` to 50-300 gets `target_pct = None` in all 200 trials, with no warning and no way to tell from the results. The panel's own copy ('Leave blank to use the strategy's default bounds', Optimizer.jsx:1339) compounds it: the placeholder is `String(def.min ?? "")`, so an unbounded param shows an empty box while the backend quietly uses [0,100] or [0,1].

**Suggested fix:**
Filter `numericParams` to exclude entries carrying `"fixed"` (or render them as a disabled 'pinned to X' row), and populate the placeholders from the job's effective `param_space` rather than from the raw schema so implicit defaults are visible.

## [34] MEDIUM — `_rebuild_study` silently drops unbounded params on resume, so resuming an authored-plugin job discards the entire search history

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:546`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
        try:
            if t == "int":
                dists[name] = optuna.distributions.IntDistribution(int(info["min"]), int(info["max"]))
            elif t == "float":
                dists[name] = optuna.distributions.FloatDistribution(float(info["min"]), float(info["max"]))
            elif t == "bool":
                dists[name] = optuna.distributions.CategoricalDistribution([True, False])
        except Exception:
            continue
```

**Failure scenario:**
The same unguarded `info["min"]` KeyError fires here, but is swallowed by the blanket `except Exception: continue`, so the param simply gets no distribution. For `algotest_option_buy_nifty` that removes 11 of 12 dims, leaving only the bool `lazy_enabled`. The re-seeded trials at optimizer.py:553-563 then carry a single boolean each, so TPE resumes with essentially zero knowledge of the 200 trials already paid for — while the job doc still reports `n_trials_completed` and the log line at optimizer.py:1247 says 'Resuming optimization ... from trial N'. The user pays for the trials twice and is told the resume worked.

**Suggested fix:**
Resolve effective bounds once in `_build_param_space` so `_rebuild_study` cannot see a bound-less entry, and replace the blanket `except Exception: continue` with a logged, job-visible warning naming the dropped param.

## [35] MEDIUM — `param_overrides` is completely unvalidated: an inverted user range makes every trial fail while the job still reports status "done"

- dim: `param-space-hygiene`
- site: `backend/app/schemas.py:185`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
    param_overrides: Dict[str, Any] = Field(default_factory=dict)
```

**Failure scenario:**
`optimize_start` (backend/app/routers/research.py:495-540) validates `method`, `n_trials`, `evaluation_mode`, `rerank_top_k`, survival config and exit controls — but never touches `param_overrides`, and `_build_param_space` copies `ov["min"]`/`ov["max"]` in verbatim (optimizer.py:211-214) with no `min <= max` check. A user who types min=50, max=10 into the bounds panel produces `trial.suggest_int(name, 50, 10)`; the resulting error is swallowed by `catch=(Exception,)` at optimizer.py:1354, every trial is marked FAIL, `study.best_value` raises and is caught at optimizer.py:1361-1363, and the job finishes at optimizer.py:1717 with `final_status="done"`, `best_params={}`, `best_value=None`. The same inverted-range shape arises with no user error at all: `ParamSpec.min` and `ParamSpec.max` are independently Optional (backend/app/ai/spec_schema.py:29-30) and `_param_schema_literal` emits each only when set (compiler.py:305-308), so a spec declaring `min: 5.0` and no max compiles to a float searched over `lo=5.0, hi=1.0`.

**Suggested fix:**
Validate in `optimize_start` that each override has `min <= max`, that overridden names exist in the strategy's schema, and that the resulting effective range for every searched param is non-empty; 400 instead of running 200 doomed trials. Additionally, make the optimizer finish as "failed" (not "done") when zero trials completed successfully.

## [36] MEDIUM — `_suggest`'s default lower bound of 0 proposes values that are invalid or degenerate for the params it is applied to

- dim: `param-space-hygiene`
- site: `backend/app/optimizer.py:242`
- category: optimizer-param-space-hygiene

**Evidence (agent-quoted, unverified):**
```
        if t == "int":
            lo, hi = int(info.get("min", 0)), int(info.get("max", 100))
            out[name] = trial.suggest_int(name, lo, hi)
```

**Failure scenario:**
Zero is a legal suggestion for every unbounded int. Concretely, for `algotest_option_buy_nifty`: `lots=0` violates `PremiumTriggerConfig.lots` `ge=1` (premium_trigger_config.py:102) and is reported as a zero-trade result (optimizer.py:366-367) rather than as an invalid proposal; `momentum_pct=0` is legal (`ge=0.0`) and means 'enter as soon as premium is at or above the reference', firing on every armed session — paired with a lots value near the accidental 100 ceiling, that is precisely the 'huge Highest Acct Value with no visible trades' shape the user reported; `stop_pct=0` means an instant stop. The same 0 floor is applied to `cooldown_bars`, which `compiler.py:391` also emits with no bounds (`{"type": "int", "default": N}`). The 100 upper default is equally arbitrary: `PremiumTriggerConfig.target_pct` legitimately allows up to 1000 (premium_trigger_config.py:87), so an unbounded `target_pct` is silently truncated to a tenth of its valid range.

**Suggested fix:**
Remove the invented defaults entirely (see finding on `_build_param_space`); if a fallback must remain, derive it from the consuming model's own `ge`/`le` (e.g. via `PremiumTriggerConfig.model_fields[name]` constraints) rather than from a hard-coded [0,100]/[0,1].

## [37] MEDIUM — The only surface that does render premium trades caps at 50 rows and shows no timestamp, strike or date

- dim: `kpi-and-trade-list-surfacing`
- site: `frontend/src/pages/BacktestLab.jsx:2225`
- category: dropped-data

**Evidence (agent-quoted, unverified):**
```
BacktestLab.jsx:2225 `{trades.slice(0, 50).map((t, idx) => (` — no pagination, sort, filter or CSV, unlike TradesTable.
Header columns, BacktestLab.jsx:2211-2221: `#`, `Option`, `Dir`, `Entry`, `Exit`, `Target`, `Stop`, `Lots`, `Exit Reason`, `P&L`, `Status` — no entry/exit time and no strike column.
The 'Option' cell falls back to the raw key because the adapter blanks the symbol — BacktestLab.jsx:2228 `{t.trading_symbol || t.instrument_key || "-"}` vs premium_trigger_dispatch.py:232 `"trading_symbol": "",`.
`Target`/`Stop` are always "—" for premium runs: premium_trigger_dispatch.py:245-246 `"option_target_level": None, "option_stop_level": None,`.
The data IS present and unused: premium_trigger_dispatch.py:236,238-239 `"strike": strike,` / `"option_entry_ts": entry_ts, "option_exit_ts": exit_ts,`.
```

**Failure scenario:**
Even after finding the Option Execution card, a user whose premium-native run took 300 trades can see at most 50 of them, in run order, with no date or time on any row and the contract shown as an opaque instrument key rather than '24500 CE'. They still cannot answer 'which trades were taken and when'. Trades 51-300 are unreachable through the entire UI.

**Suggested fix:**
This card should not be the trade list at all — point TradesTable at the option legs (see the finding on BacktestLab.jsx:2006), which already has sort, filters, resizable columns and no row cap. If the card keeps its inline table, at minimum drop the `.slice(0, 50)`, add Date/Entry-time/Exit-time columns from `option_entry_ts`/`option_exit_ts` via `tsToTime`, and render `{t.strike} {t.direction}` instead of the instrument key (or populate `trading_symbol` in the adapter at premium_trigger_dispatch.py:232).

## [38] MEDIUM — Premium-native runs ignore sizing_config entirely, yet the UI disables the Lots input and tells the user "the sizing panel sets the lots then" — nothing sets them

- dim: `sizing-and-cost-precedence`
- site: `frontend/src/pages/BacktestLab.jsx:1049`
- category: ui-claim-false

**Evidence (agent-quoted, unverified):**
```
                  disabled={config.option_sizing_enabled}
                  title={config.option_sizing_enabled
                    ? "Ignored while Capital & position sizing is on — the sizing panel below controls the lot count."
                    : "Lots per trade (lot size always comes from the contract)."}

# premium_trigger_dispatch.py:315 — the only sizing input the premium path reads:
    paired_trades = _adapt_premium_trades_to_paired(
        pm_result.get("trades", []), instrument=instrument, lots=int(cfg.lots), lot_size=lot_size,
    )
```

**Failure scenario:**
User enables "Capital & position sizing" with mode=premium_at_risk, 1% risk/trade, Max lots=10 — the classic way to keep a run realistic. The Lots input greys out and says the sizing panel now controls the lot count. For a premium-native strategy nothing in `dispatch_full_backtest` looks at `sizing_config`: no `size_position` call, no risk budget, no `max_lots` cap. Every trade is sized at the flat `cfg.lots` (100), risk_per_unit/risk_amount/risk_exceeded are hardcoded None/False in the adapter (premium_trigger_dispatch.py:255-257), so the per-trade risk-budget warning triangle in the Lots column (BacktestLab.jsx:2235) can never fire. The user believes a 1%-risk-capped run produced the result; it did not. The card header even renders "Account (rupee) — fixed lots" (BacktestLab.jsx:2124) while the sizing panel above still shows premium_at_risk selected.

**Suggested fix:**
Either implement premium-at-risk sizing in `_adapt_premium_trades_to_paired` (call `portfolio.size_position` per trade with the run's SizingConfig, exactly as option_backtest.py:754 does), or — if fixed-lots is a deliberate limitation of the premium engine — surface it: keep the Lots input enabled for premium-native strategies and add an explicit "position sizing is not applied to premium-native runs; size comes from the strategy's `lots` param" note next to the sizing switch.

## [39] MEDIUM — Persisted premium run doc is self-contradictory on lots, and both the deployment pin and "Save as preset" copy the FORM value instead of the one that traded

- dim: `sizing-and-cost-precedence`
- site: `backend/app/strategy_deployments.py:124`
- category: wrong-number

**Evidence (agent-quoted, unverified):**
```
        ob = source_doc.get("option_backtest") or {}
        sizing_config = ob.get("sizing_config")
        lots = (ob.get("request") or {}).get("lots")

# runtime.py:1216 — `request` is the FORM's OptionBacktestReq, not what ran:
            pm_result["request"] = config.model_dump()

# BacktestLab.jsx:423 — preset built from the run doc reads the same form value:
      lots: Math.max(1, Number(ob.lots || 1)),
```

**Failure scenario:**
A premium-native run executed at lots=100 persists three disagreeing numbers in one document: `option_backtest.trades[*].lots = 100`, `option_backtest.sizing_config.fixed_lots = 100`, and `option_backtest.request.lots = 5` (the form). `deployment_sizing_from_source` stores the form's 5 as the pin's `lots` field — the value `resolve_deployment_lots` falls back to whenever `sizing_config` is absent or disabled (paper_auto.py:433/438), i.e. a pin that is wrong by 20x sits one branch away from being used. Worse for presets: "Save THIS result's params + option execution as a preset" runs `buildExecutionFromRun`, which copies `ob.lots` (5) and never copies `sizing_config` at all — so `deployment_sizing_from_source`'s preset branch finds no `sizing_config`, returns None (strategy_deployments.py:131-132), and the deployment silently falls all the way back to `risk.default_lots`. The 100-lot backtest the user is deploying on the strength of becomes a 1-lot (or default_lots) deployment with no message.

**Suggested fix:**
Make the premium branch write back what actually ran: after `dispatch_full_backtest`, set `pm_result['request'] = {**config.model_dump(), 'lots': int(cfg.lots), 'moneyness': cfg.moneyness}` so the persisted request block cannot disagree with `sizing_config`/`trades`. Have `buildExecutionFromRun` carry `ob.sizing_config` into the preset's execution block (the backend's `execution_from_option_config` already does, preset_execution.py:63-66) and source its `lots` from `ob.sizing_config.fixed_lots` when present.

## [40] MEDIUM — `late_lock_cutoff` is a dead knob in the backtest: the config accepts it and emits it, the sim never reads it, live does

- dim: `dropped-config-fields`
- site: `backend/app/premium_trigger_config.py:183`
- category: dead-knob

**Evidence (agent-quoted, unverified):**
```
        if self.late_lock_cutoff:
            params["late_lock_cutoff"] = self.late_lock_cutoff
```

**Failure scenario:**
`late_lock_cutoff` is a first-class `PremiumTriggerConfig` field (line 105), is HH:MM-validated (line 120), is emitted into the sim's params dict at line 184, is advertised to the AI author via `premium_trigger_allowed_keys()` (premium_trigger_dispatch.py:145) and is declared by the shipped plugin with default '10:15' (plugins/premium_momentum.py:57). But `premium_momentum_backtest.py` never calls `params.get("late_lock_cutoff")` — it is absent from `ENGINE_PARAM_KEYS` (:96-109), and `tests/test_engine_params_are_declared.py:56-61` proves that set is exactly what the sim reads. Only `premium_momentum_live.py:224` reads it. So a user who sets 'no strike lock after 10:15' gets sessions locked at any time in the backtest (inflating the traded sample with late, low-quality sessions) and correctly gated in live — and the field's own docstring promises 'leaving it flat that day'.

**Suggested fix:**
Either implement the cutoff in `run_premium_momentum_backtest` (skip the session when the reference bar resolves at/after `late_lock_cutoff`, counted in `exclude_reasons`) and add it to `ENGINE_PARAM_KEYS`, or delete the field from `PremiumTriggerConfig`/`to_backtest_params` and expose the sim's `entry_cutoff` instead. Do not leave it emitted-but-unread.

## [41] MEDIUM — AI-compiled premium plugins hand the optimizer 8 inert search dimensions — it tunes and reports 'best' values for params the backtest discards

- dim: `dropped-config-fields`
- site: `backend/app/ai/compiler.py:411`
- category: optimizer-integrity

**Evidence (agent-quoted, unverified):**
```
        _pt_entries = "".join(
            f'{_nl_indent}{k!r}: {{"type": {_schema_type_for(v)!r}, "default": {v!r}}},'
            for k, v in spec.premium_trigger.items() if v is not None
        )
```

**Failure scenario:**
The compiler emits only `type` and `default` — never `min`/`max` and never `"fixed"`. The shipped `premium_momentum` plugin deliberately pins its multi-leg knobs with `"fixed": None` / `"fixed": False` to keep them OUT of the search space (plugins/premium_momentum.py:70-92), but an authored plugin gets no such protection. `_build_param_space` (optimizer.py:202-215) admits every int/float/bool, so for `algotest_option_buy_nifty` the space contains 12 dimensions of which only momentum_pct, stop_pct, target_pct and lots actually reach the sim. The other 8 — trail_x_pct, trail_y_pct, lazy_enabled, lazy_momentum_pct, lazy_stop_pct, lazy_target_pct, lazy_trail_x_pct, lazy_trail_y_pct — are dropped at premium_trigger_dispatch.py:110 and cannot change the objective. Two-thirds of the search dimensions are pure noise: Optuna's TPE wastes its budget modelling them, duplicate-objective trials look like a converged plateau, and the persisted `best_params` / preset / deployment records a confidently-reported 'optimal' lazy_stop_pct=37 that never influenced a single rupee.

**Suggested fix:**
Have the compiler emit `"fixed": <default>` for any premium_trigger key that `dispatch_full_backtest` cannot deliver to the sim (or, better, fix the drop and emit the spec's declared min/max). Independently, `_build_param_space` should exclude any key not in the strategy's effective execution surface for premium-native strategies.

## [42] MEDIUM — The drop is completely silent: the dispatcher warns only on an INVALID config, never on discarded fields

- dim: `dropped-config-fields`
- site: `backend/app/premium_trigger_dispatch.py:303`
- category: silent-failure

**Evidence (agent-quoted, unverified):**
```
    cfg, reason = extract_premium_trigger_config(merged_params)
    if cfg is None:
        if reason and reason != "absent":
            log.warning("dispatch refused for %s: %s", strategy_id, reason)
        return None
```

**Failure scenario:**
The only diagnostic in the whole path fires when the config fails to VALIDATE. A config that validates while 12 declared fields were thrown away produces no log line, no field in the returned envelope, and no UI signal. The envelope's `premium_trigger_config` key (line 359) is `cfg.model_dump()` — i.e. only the 14 fields that survived — so even an operator inspecting the persisted `backtest_runs` document sees a config that looks complete and consistent. There is no way, from the result alone, to discover that lazy legs / leg_mode / the trail / the cutoffs were dropped. This is what turns four wrong-number bugs into an undetectable one.

**Suggested fix:**
Have `extract_premium_trigger_config` return the discarded keys as a third element and have `dispatch_full_backtest` put them in the envelope (e.g. `"dropped_params": [...]`), log them at WARNING, and render them as a red banner in the Backtest Lab premium-native block. Better still, refuse the run when any dropped key is in `ENGINE_PARAM_KEYS` — running a different strategy is worse than running none.

## [43] MEDIUM — The authoring wizard tells the user the dropped fields are 'what the engine will actually trade'

- dim: `dropped-config-fields`
- site: `frontend/src/components/strategy/AuthoringWizard.jsx:885`
- category: misleading-ui

**Evidence (agent-quoted, unverified):**
```
            <div className="text-[10.5px] text-dimmer">
              Review these values before installing — they are what the engine will
              actually trade. Exits live here, not in the Exits section below.
            </div>
```

**Failure scenario:**
The panel above this text renders EVERY key of the `premium_trigger` block (lines 872-883, `Object.entries(premiumTrigger)`), which by design includes the widened surface — `compiler.py:116` validates specs against `premium_trigger_allowed_keys()`, the union that deliberately admits leg_mode, the lazy_* knobs, entry_cutoff and exit_time so an authored lazy-leg strategy stops coming back as 'couldn't map'. The user therefore reads 'lazy enabled: true', 'exit time: 15:13', 'leg mode: both' next to an explicit promise that these are what the engine trades — then backtests and gets a single-leg, no-trail, hold-to-EOD run. The authoring surface was widened without widening the execution surface, and the copy asserts the two are the same.

**Suggested fix:**
Derive the panel's badge state from the execution surface, not the authoring surface: mark each field 'backtest + live', 'live only', or 'not simulated' based on what `dispatch_full_backtest` actually forwards, and soften the copy until the dispatch drop is fixed.

## [44] MEDIUM — The anti-drift test triangle is missing its third leg — nothing asserts that an expressible param actually reaches the sim

- dim: `dropped-config-fields`
- site: `tests/test_engine_params_are_declared.py:59`
- category: test-coverage

**Evidence (agent-quoted, unverified):**
```
def test_the_declaration_does_not_invent_params():
    """The reverse: declaring something the sim ignores would advertise a knob
    that silently does nothing — the dead-knob class."""
    phantom = sorted(set(ENGINE_PARAM_KEYS) - _keys_the_engine_reads())
    assert not phantom, (
        f"ENGINE_PARAM_KEYS declares {phantom}, which the sim never reads")
```

**Failure scenario:**
This file guards two of the three edges: ENGINE_PARAM_KEYS ⊇ what the sim reads, and the authoring surface ⊇ ENGINE_PARAM_KEYS (:64-75). The third edge — DISPATCH delivers what authoring allows — has no test, which is exactly why 12 authorable fields can be dropped at premium_trigger_dispatch.py:110 with a fully green suite. The same file's own docstring articulates the lesson it then fails to apply: 'deriving the authoring surface from a declaration only helps if the declaration is complete.' `test_spec_premium_full_surface.py` likewise only checks expressibility, never delivery. The dead-knob class this file was written to prevent is currently shipping at 12x scale.

**Suggested fix:**
Add a test that builds a params dict containing every key in `premium_trigger_allowed_keys()`, runs it through the dispatch path, and asserts the params dict handed to `run_premium_momentum_backtest` retains every key that is in `ENGINE_PARAM_KEYS` (monkeypatch the sim to capture its `params` argument). It fails today with a 12-name list — which is the point.

## [45] MEDIUM — Grid Search crashes the entire job for any strategy whose parameter_schema omits min/max (every AI-authored plugin)

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:261`
- category: crash

**Evidence (agent-quoted, unverified):**
```
        if t == "int":
            lo, hi = int(info["min"]), int(info["max"])
```

**Failure scenario:**
`_build_param_space` copies the schema verbatim (`info = dict(defn)`, optimizer.py:207) and never injects bounds; only `_suggest` invents them (`lo, hi = int(info.get("min", 0)), int(info.get("max", 100))`, optimizer.py:242). `algotest_option_buy_nifty.parameter_schema` declares e.g. `'momentum_pct': {"type": 'int', "default": 15}` with no min/max, so `space['momentum_pct']` has no `"min"` key. `_grid_combinations` is called unguarded at optimizer.py:1298 inside `run_optimization`'s outer try -> `KeyError: 'min'` -> the job is marked `status: "failed", error: "'min'"` (optimizer.py:1770-1772) with zero diagnostic value. Grid Search is a first-class UI choice (Optimizer.jsx:65).

**Suggested fix:**
Normalise bounds once inside `_build_param_space` (apply the same `[0,100]` / `[0.0,1.0]` fallbacks `_suggest` uses, or better: reject a schema with no bounds with an actionable job error) so `_grid_combinations`, `_heatmap`, `_robustness_score` and `_rebuild_study` all see a complete space.

## [46] MEDIUM — Robustness score and heatmap silently come back null for any bounds-less schema

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:424`
- category: silent-analysis-loss

**Evidence (agent-quoted, unverified):**
```
            t_v = max(float(info["min"]), min(float(info["max"]), t_v))
```

**Failure scenario:**
Same missing-bounds root as the grid crash, but here the KeyError is swallowed: `_robustness_score` raises on the first perturbation and is caught by `except Exception as e: log.warning(f"robustness failed: {e}")` (optimizer.py:1690-1691); `_heatmap`'s `np.linspace(info_a["min"], info_a["max"], grid_n)` (optimizer.py:463) is caught at 1686-1687. Both analyses are then persisted as `null` (optimizer.py:1730-1731). For an AI-authored strategy the user therefore always gets an empty Robustness/Heatmap panel with no explanation, and the only signal that the optimizer's own overfitting check never ran is a backend log line.

**Suggested fix:**
Fix the bounds normalisation in `_build_param_space`; until then surface the failure on the job document (e.g. `analysis_warnings: ["robustness skipped: param 'momentum_pct' has no declared bounds"]`) instead of a bare null.

## [47] MEDIUM — Resuming a paused job on a bounds-less schema seeds Optuna with parameterless trials, silently discarding the entire search history

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:546`
- category: silent-search-loss

**Evidence (agent-quoted, unverified):**
```
            if t == "int":
                dists[name] = optuna.distributions.IntDistribution(int(info["min"]), int(info["max"]))
            ...
        except Exception:
            continue
```

**Failure scenario:**
With no `"min"`/`"max"` in `space` every distribution construction raises and is skipped, leaving `dists == {}`. The seeding loop then computes `params = {k: p[k] for k in dists if k in p}` = `{}` and its guard `if len(params) != len(dists): continue` passes vacuously (0 == 0), so `study.add_trial(create_trial(params={}, distributions={}, value=v))` (optimizer.py:556-561) inserts N valueful-but-parameterless trials. After a pause/resume of a premium-native (or any AI-authored) optimization the TPE sampler has zero observations for any parameter and degenerates to random search for the rest of the run, while appearing to have resumed; `study.best_params` can also return `{}`, which at optimizer.py:1699 (`if best_so_far["params"]:`) would skip saving the best run entirely.

**Suggested fix:**
Make the guard meaningful — skip seeding (and log) when `dists` is empty or smaller than the non-fixed space — and fix the bounds normalisation so `dists` is complete.

## [48] MEDIUM — Premium Stage-2 re-rank ignores the analyze-time budget and never emits progress, so the job hangs in "analyzing" and reports analyze_budget_hit=False

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:893`
- category: dropped-governance

**Evidence (agent-quoted, unverified):**
```
    if is_premium_trigger_strategy(strategy):
        return await _option_rerank_premium_trigger(candidates, get_enriched, strategy, instrument)
```

**Failure scenario:**
The premium branch drops the three governance arguments its caller supplies (`analyze_t0`, `analyze_budget_sec`, `progress_cb` — optimizer.py:887, 1539-1540). `_option_rerank_premium_trigger`'s candidate loop (optimizer.py:860-879) has no `over_budget` check and no `progress_cb` call, and it returns a hardcoded budget flag: `return ranked, contracts, option_candles, False` (optimizer.py:881). With `rerank_top_k` up to 500 (Optimizer.jsx:522) and each candidate a full-window premium sim, a premium job runs the re-rank to completion regardless of the user's `analyze_budget_sec`, the UI's `rerank_progress`/ETA stays frozen at the initial `{"stage": "option_rerank", "candidates": N}` written at optimizer.py:1534, and the finished job reports `analyze_budget_hit: false` even after blowing through the budget.

**Suggested fix:**
Thread `analyze_t0`/`analyze_budget_sec`/`progress_cb` into `_option_rerank_premium_trigger` and mirror `_option_rerank`'s per-candidate `ewma` + `over_budget` + `progress_cb` block (optimizer.py:1025-1033), returning the real `budget_hit`.

## [49] MEDIUM — "Auto-tune exit controls" is a guaranteed no-op for premium strategies while still re-running survivors x grid full OOS simulations

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:1586`
- category: silently-dropped-config

**Evidence (agent-quoted, unverified):**
```
                                v = await _survival_eval_oos(
                                    strategy, df_enr, merged, rerank_contracts, rerank_candles,
                                    instrument, costs, pretrade, {**option_cfg, "exit_controls": gc}, survival,
```

**Failure scenario:**
`_survival_eval_oos` forwards premium strategies to `_survival_eval_oos_premium_trigger` (optimizer.py:737-741), whose signature has no exit_controls and which reads only `option_cfg["sizing_config"]["capital"]` (optimizer.py:683) before calling `dispatch_full_backtest` — a path that has no exit-controls concept at all. Every grid entry `gc` therefore produces a bit-identical verdict, so `better = (v.get("calmar") or -1e9) > (r["survival"].get("calmar") or -1e9)` (optimizer.py:1591) is always False and `chosen_exit_controls` is never set — while the loop still pays `len(survivors) x len(grid)` complete multi-fold premium re-simulations. The Optimizer UI advertises this as "adopts whichever still survives... saved as chosen_exit_controls" (Optimizer.jsx:1171).

**Suggested fix:**
Skip the exit-control grid entirely for `is_premium_trigger_strategy(strategy)` and surface a job warning explaining it is not applicable to premium-native execution (the premium engine's exits are the config's stop/target/trail), or implement exit_controls in the premium survival path.

## [50] MEDIUM — The documented O6 live-effective entry window is never applied to premium trials or premium survival folds

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:1071`
- category: invariant-violation

**Evidence (agent-quoted, unverified):**
```
        # O6: live-effective entry window (IST). Threaded into EVERY optimizer
        # backtest (trials, survival folds, parallel workers) so selection + the
        # survival gate agree and never reward 14:50-15:00 entries live can't take.
```

**Failure scenario:**
`trade_window_start`/`trade_window_end` (defaulted by the UI to 09:25/14:50, Optimizer.jsx:518-519) are threaded into `_evaluate` and the non-premium `_survival_eval_oos` only. `_evaluate_premium_trigger` (optimizer.py:335-339) and `_survival_eval_oos_premium_trigger` (optimizer.py:669-672) take no trade-window arguments and never pass one to `dispatch_full_backtest`; the sim's own equivalent knob, `entry_cutoff`, is dropped by the config narrowing (premium_momentum_backtest.py:336 vs. the 14-field `_CONFIG_FIELDS`). Premium candidates are therefore selected on full-session entries the live path would refuse, and the stated "selection + the survival gate agree" invariant is false for exactly the strategy family the optimizer now supports.

**Suggested fix:**
Map `trade_window_end` onto the premium engine's `entry_cutoff` (and `exit_time`) when dispatching from the optimizer, or make the comment/UI state that the window is not enforced for premium-native runs.

## [51] LOW — Stale in-code justification: runtime.py claims the single-leg restriction is 'by design', which is no longer true once config-presence routing admitted authored strategies

- dim: `dropped-config-fields`
- site: `backend/app/runtime.py:1196`
- category: stale-invariant

**Evidence (agent-quoted, unverified):**
```
        # NOTE: no lazy_enabled here — the general Backtest Lab path stays
        # single-leg by design until Phase 5B (PremiumTriggerConfig deliberately
        # has no lazy fields; dispatch would drop them anyway). The bespoke
        # /premium-momentum page is the two-leg/lazy surface.
```

**Failure scenario:**
This comment encodes the assumption that anyone wanting lazy legs uses the bespoke `/premium-momentum` page, which passes raw `params` straight through (premium_momentum_routes.py:196-199) and therefore honours all 34 ENGINE_PARAM_KEYS. That assumption broke when `dispatch_full_backtest` moved to config-presence routing: an AI-authored premium-native strategy has NO bespoke page — the Backtest Lab and the Optimizer are its only surfaces, and both go through the lossy dispatch. The same strategy backtested via the bespoke route and via the Backtest Lab now returns different trades, different P&L and different coverage from identical params, and this comment is the reason a reader would conclude that is intended. It also means the shipped `premium_momentum` plugin's own 5B settings vanish whenever it is run from the Backtest Lab or optimized.

**Suggested fix:**
Delete or rewrite the comment as part of the dispatch fix, and add a test asserting that `/premium-momentum/backtest` and `_run_paired_option_backtest` produce identical trade lists for the same instrument/window/params.

## [52] LOW — lots=0 (and other out-of-range suggestions) fail PremiumTriggerConfig validation and are silently recorded as zero-trade trials

- dim: `optimizer-premium-path`
- site: `backend/app/optimizer.py:242`
- category: silent-trial-loss

**Evidence (agent-quoted, unverified):**
```
            lo, hi = int(info.get("min", 0)), int(info.get("max", 100))
            out[name] = trial.suggest_int(name, lo, hi)
```

**Failure scenario:**
`PremiumTriggerConfig.lots` is `Field(default=1, ge=1, le=100)` (premium_trigger_config.py:102) but `_suggest` samples the bounds-less `lots` from [0, 100]. On lots=0 `extract_premium_trigger_config` raises, returns `(None, "invalid:...")` with only a `log.warning` (premium_trigger_dispatch.py:115-117), `dispatch_full_backtest` returns None, and `_evaluate_premium_trigger` falls to `return _premium_zero_metrics(), merged_params` (optimizer.py:365-367) -> `_DISQUALIFY`. In the persisted trial log that is indistinguishable from a genuinely unprofitable configuration, so the user cannot tell that ~1% of the budget (and any other trial whose sampled value violates a config constraint) was spent on structurally invalid configs rather than on the strategy.

**Suggested fix:**
Record the refusal reason on the trial (e.g. `trial_history[...]["error"] = reason`) and clamp the premium search space to the config model's own field constraints (`ge`/`le` from `PremiumTriggerConfig.model_fields`) when building the space.
