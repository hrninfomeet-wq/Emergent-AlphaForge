# Premium-native backtest reporting — audit & fix log (2026-07-29)

Checkpoint file. Append as work lands; never rewrite history.

Trigger: user optimized the AI-authored `algotest_option_buy_nifty`
("NF_CE_PE_EXP2_Base"), backtested it, and saw

  * no Trades / Win rate / Profit factor / Net P&L in the KPI grid,
  * an empty Trades pane,
  * "Highest Acct Value" enormous,
  * OPTION EXECUTION showing lots = 100 while the form said 5.

## Hard evidence (from the user's own run, Mongo `optimization_jobs`)

Job `cf53e98f-6f7f-437c-a45e-8b03d2134692`, method=bayesian, objective=`net_pnl_inr`:

```
param_space:  "lots": {"type":"int","default":2}      <-- NO min/max
best_params:  lots: 100                                <-- exactly the [0,100] ceiling
best_value:   5,929,384.5     (₹59.3 lakh)
best_metrics: max_dd_pts: 1,515,878                    <-- "points" holding rupees
```

Also swept with no bounds: `momentum_pct` (->76), `stop_pct`, `trail_x_pct` (->36),
`trail_y_pct` (->78), `lazy_stop_pct` (->43), `lazy_trail_x_pct` (->76),
`lazy_trail_y_pct` (->97).

Also in the space: `ema_fast, ema_slow, rsi_length, macd_fast, macd_slow,
macd_signal, atr_length, adx_length, chop_length, swing_lookback` — **10 indicator
dimensions for a premium-native strategy whose `evaluate()` is inert and which
reads no indicator at all.** 10 of 22 search dimensions provably cannot change the
result.

## Confirmed root causes (orchestrator-verified, pre-workflow)

### A. Optimizer treats position size as an alpha parameter — HIGH
`optimizer.py::_suggest` defaults an int with no declared min/max to `[0, 100]`
(`optimizer.py:242`). `_build_param_space` admits every int/float/bool in the
plugin's `parameter_schema`. `lots` is in that schema. `net_pnl_inr` /
`total_pnl_pts` are monotonic in lots => the optimizer is a position-size
maximizer. It returned the ceiling, 100.
Secondary: `lo` defaults to **0**, so `lots=0` (and `period=0`) are reachable.

### B. KPI grid reads the wrong metrics object — HIGH
`BacktestLab.jsx:1933-1936` reads `const m = result.metrics` (the SPOT metrics,
empty for premium-native). `acctRange` uses `buildPerformanceSeries(result)`
(`lib/backtestMetrics.js:47`) which correctly falls back to
`result.option_backtest.portfolio` — hence account value populates while
Trades/WinRate/PF/NetP&L stay blank. That asymmetry IS the user's symptom.
Note `computeKeyMetrics` already does the adaptive thing correctly.

### C. Trades pane iterates spot trades — HIGH
`BacktestLab.jsx:2006` `<TradesTable trades={result.trades || []} .../>`;
`TradesTable` maps over `trades` and joins option legs by `index_trade_id`
(`:2686-2719`), then early-returns on `!trades.length` (`:2780`). Premium-native
runs have zero spot trades, so the option legs — which DO exist in
`option_backtest.trades` — can never render.

### D. Banner is factually wrong about lots & cost — HIGH
`BacktestLab.jsx:1978` claims "the option-pairing form's moneyness is not used,
while its **lots & cost model are honored**". But `runtime.py:1190`:
`pm_params["lots"] = int(req.params.get("lots") or config.lots or 1)` — the
STRATEGY param wins over the form. Since authored plugins now declare `lots` in
`parameter_schema`, the strategy panel silently overrides the form. Form=5,
strategy=100 => 100 used. Same precedence for `cost_config` (`:1191-1194`).

### E. Form capital never reaches the equity curve — MEDIUM
`runtime.py:1209` calls `dispatch_full_backtest(...)` with **no `capital=`**, so
it uses the signature default `200_000.0`. The user's `option_capital` is ignored,
so "Highest/Lowest Acct Value", drawdown % and return % are all computed off a
hardcoded base.

### F. Lazy-leg / two-leg config silently dropped in Backtest Lab — HIGH
`runtime.py:1196-1199` states the general path "stays single-leg by design …
PremiumTriggerConfig deliberately has no lazy fields; dispatch would drop them
anyway". The user's plugin declares `leg_mode='both'`, `lazy_enabled=True` and 6
`lazy_*` params, and the authoring wizard told them lazy legs are BUILDABLE_NOW
and live-feasible. The backtest therefore measures a DIFFERENT strategy than the
one configured, with no warning. `entry_cutoff` / `exit_time` are dropped too.
NB the optimizer still *searched* `lazy_*` (they're in the space) — tuning
parameters that provably do not affect the result.

## Workflow audit result
`wf_50d18619-409`: 5 audit agents completed, **all 6 adversarial verifiers died on
the account spend limit**. 52 UNVERIFIED findings dumped to
`docs/PREMIUM_NATIVE_AUDIT_RAW_FINDINGS.md`. They are agent claims, not facts —
each is being independently verified before it is acted on.

Verified by the orchestrator so far:
 * **G. `_grid_combinations` KeyError — HIGH.** `optimizer.py:261` reads
   `info["min"]`/`info["max"]` while `_suggest` reads `.get(min,0)/.get(max,100)`.
   Grid Search therefore died with `KeyError: 'min'` on any unbounded param — the
   user would have seen a one-character error. Two call sites improvising
   different answers to "what does no-bounds mean".
 * **H. float default range is `[0.0, 1.0]` — HIGH.** So `stop_pct: 20.0`
   (a float literal) would be searched over 0-1%, i.e. instant stop-out, decided
   purely by whether the LLM emitted `20` or `20.0`.
 * **I. compiler emits bare `{type, default}` — HIGH.** `compiler.py:411`. The
   shipped `premium_momentum` plugin never declares `lots` at all and hand-writes
   `"fixed"` on risk knobs + `min`/`max` on alpha knobs; authored plugins got none
   of that. Confirmed `PremiumTriggerConfig.lots` is `ge=1, le=100`, so the
   invented ceiling coincided exactly with the config max — 100 validated cleanly.
 * **J. optimizer scores premium trials with costs OFF.** `_evaluate_premium_trigger`
   (`optimizer.py:361`) calls `dispatch_full_backtest` without injecting the job's
   cost config, while the Backtest Lab path (`runtime.py:1191-4`) does inject it.
   So the optimizer ranks on GROSS and the user then backtests NET.

## Fixes landed

### 1. Optimizer no longer invents bounds or tunes size — DONE
`optimizer.py`: new `NON_ALPHA_PARAM_NAMES` (lots/max_lots/fixed_lots/quantity/
capital/session_max_*). `_build_param_space` now PINS (`fixed`) any numeric param
that is non-alpha, or that lacks BOTH `min` and `max`, instead of letting the two
downstream call sites improvise. Pinning reuses the existing `fixed` mechanism, so
the param still flows into `merged_params` (strategy runs with it) and
`_grid_combinations` stops raising KeyError for free. Bools exempt (exhaustive
domain). Explicit user min+max override remains an opt-in escape hatch.
`tests/test_optimizer_param_space_hygiene.py` — 14 tests.

**Regression measured across all 13 registered strategies: ZERO hand-written
strategies changed** (they all declare proper bounds). Only the AI-authored
plugin was affected — which is the defect.

### 2. Indicator periods not injected for premium-native — DONE
`resolve_indicator_period_search(requested, premium_native=)` in `optimizer.py`,
wired at `optimizer.py:1265` and `wfo.py:615`. The user's job searched 22 dims,
10 of them indicator periods for a strategy that reads no indicator.

### 3. Compiler emits curated bounds / pins — DONE
`compiler.premium_param_schema_entry(key, value)`: non-alpha -> `fixed`; else copy
`min`/`max` DERIVED from the shipped `premium_momentum` schema (anti-drift, not
hardcoded); else `fixed`. `tests/test_compiler_emits_optimizable_schema.py` — 9
tests, end-to-end through compile -> exec -> real `_build_param_space`.

### 4. User's installed plugin regenerated — DONE
`algotest_option_buy_nifty.py` schema rewritten with the new bounds/pins.
Search space: **22 dims (10 inert + leverage) -> 5 real bounded dims**
(momentum_pct 5-50, stop_pct 10-40, lazy_momentum_pct 5-50, lazy_stop_pct 10-40,
lazy_enabled).

### 5. KPI grid + Trades pane read the right envelope — DONE
`frontend/src/lib/backtestMetrics.js`: new pure `isPremiumNative(result)`,
`displayTrades(result)`, `resultKpis(result)`. The KPI grid now reads the option
envelope for premium runs and RELABELS to rupees (showing a rupee figure under the
old "Net P&L (pts)" heading would repeat the units lie found in `optimizer.py:398`,
where a rupee drawdown is stored as `max_dd_pts`). `profitFactor` is COMPUTED
because `option_backtest.metrics` has no such key — that card could never have
shown anything. The Trades table is fed `displayTrades`, which reshapes the
executed option trades into the existing row contract (so sorting, filters and
CSV export work unchanged) and preserves `index_trade_id` so the option columns
still join.

Verified BEHAVIOURALLY: `tests/test_premium_native_result_surfacing.py` executes
the real module through node (15 tests) rather than grepping the JSX. A grep
cannot tell a use from a definition — precisely how the `NameError` shipped to
this user a day earlier.

Also split the `"Lots (Qty)"` column, which rendered `quantity` (lots x lot size)
under a heading containing "Lots", into separate `Lots` and `Qty` columns.

### 6. Banner corrected — DONE
It claimed "the option-pairing form's ... lots & cost model are honored". False:
`runtime.py:1190` gives the STRATEGY param precedence. Now states the real
precedence and points at the params panel.

### 7. Capital passthrough — DONE
`dispatch_full_backtest(capital=)` changed to `Optional[float]` with the fallback
named once (`DEFAULT_BACKTEST_CAPITAL`), and `runtime.py` forwards the form's
`sizing_config.capital`. `tests/test_premium_capital_passthrough.py` — 3 tests.

### 8. Silently-dropped config is now SAID — DONE
`premium_dropped_params(params)` + `dropped_params` on the result envelope +
an amber warning panel. Scope is the KNOWN premium surface minus what
`PremiumTriggerConfig` carries, so registry bookkeeping can never be mistaken for
a dropped capability, and `None` values are excluded.
For the user's strategy this names: `leg_mode`, `lazy_enabled`, `lazy_moneyness`,
`lazy_momentum_pct`, `lazy_stop_pct`, `lazy_target_pct`, `lazy_trail_x_pct`,
`lazy_trail_y_pct`, `entry_cutoff`, `exit_time`, `trail_x_pct`, `trail_y_pct`.
`tests/test_premium_dropped_params_are_surfaced.py` — 9 tests.
**NOT fixed: the backtest still does not SIMULATE the lazy leg.** That is a
larger piece of work; disclosure is the honest interim.

## Status
- [x] Root causes A–F confirmed against source + the user's real run data
- [x] Workflow audit — completed w/ verifier loss; 52 raw findings checkpointed
- [x] Fix 1-4 (optimizer param-space hygiene + compiler + user's plugin)
- [x] Fix 5 KPI grid + Trades pane (the user's #1 question)
- [x] Fix 6-7 banner truthfulness + capital passthrough
- [x] Fix 8 dropped-config disclosure
- [ ] Rebuild containers + user re-verification
- [ ] Verify the remaining raw findings (esp. optimizer scoring premium trials
      with costs OFF while the Backtest Lab applies them — claim J)
- [ ] OPEN QUESTION for the user: should the Backtest Lab SIMULATE lazy legs?

## Known-pre-existing failures (NOT caused by this work)
`tests/test_premium_momentum_route.py` x2 — `ServerSelectionTimeoutError` on
localhost:27017. Verified identical on a stashed baseline. This is the documented
Windows localhost/IPv6-vs-Docker-IPv4 trap, not a regression.
