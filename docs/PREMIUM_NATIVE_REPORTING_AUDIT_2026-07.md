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

## Round 2 — audit of the user's 2026-07-29 23:03 run (`1c80bd04`)

Run: `algotest_option_buy_nifty · NIFTY · 2026-07-29 23:03:50`, window
**2026-01-01 09:15 → 2026-07-06 15:30 IST**, 124 sessions.

Result: 124 trades, 49.19% WR, net **₹83,738.69** on ₹200,000 (+41.87%),
max DD **−21.48%** (−₹68,110), Sharpe 1.288. Exits 38 target / 51 stop / 35 EOD.
`lots: 2` — **the pin holds; no repeat of lots=100.**

### K. THE DISCLAIMER IS A PLUMBING GAP, NOT A CAPABILITY GAP — corrects my
### earlier statement to the user that this needed "real work in the premium engine"
`premium_momentum_backtest.ENGINE_PARAM_KEYS` (34 keys) already includes EVERY
field in the disclaimer, and the sim really implements them:
`leg_mode=="both"` (:486), lazy arming on STOP (:514-522, with `lazy_armed` /
`lazy_entered` / `lazy_blocked_cutoff` coverage counters), `entry_cutoff` (:421),
`exit_time` (:426), percent trails, session P&L caps, VIX gate.

The ONLY reason they are dropped: `dispatch_full_backtest` filters `merged_params`
through `PremiumTriggerConfig` (14 fields) before calling a sim that accepts 34.
Additionally `runtime.py:1200` calls `_load_window(...)` WITHOUT `lazy_enabled=`,
so the preload never widens — and `_load_window` ALREADY takes that arg and
already applies `preload_scope`. The bespoke `/premium-momentum` page passes it;
the Backtest Lab does not.

**Fix is small**: (1) forward `lazy_enabled` to `_load_window`; (2) pass
`ENGINE_PARAM_KEYS`-filtered extras alongside `cfg.to_backtest_params()`;
(3) handle the sim's `ValueError("lazy_enabled requires lazy_momentum_pct|pts")`.

### L. NO TRAIL RAN AT ALL — the warning's own wording is wrong — HIGH
Config used carries `trail_x: null, trail_y: null` (the config has only the POINTS
pair; the user configured the PERCENT pair, which is dropped). Empirically
confirmed: `option_trail_exits: 0`. But my warning says the engine "runs the
single-leg momentum/stop/target/**trail** config only". False for this user.

### M. "Treat them as a lower bound on behaviour" is UNJUSTIFIED — my text — HIGH
Adding a second primary leg plus a lazy re-entry adds EXPOSURE. It can easily be
worse, not better. The honest claim is "a different, simpler strategy — not a
bound in either direction."

### N. Walk-forward reports 0 trades in all 3 folds and says NO DIVERGENCE — HIGH
`walkforward.is_vs_oos` = `{avg_is_win_rate: 0, avg_oos_win_rate: 0,
divergence_warning: **false**, fold_count: 3}`, `stitched_oos_trade_count: 0`,
every fold `trade_count: 0` IS and OOS. It runs the SPOT path (inert stub) so it
measures nothing — and then renders as reassurance. `significance` at least
degrades honestly (`badge: INSUFFICIENT, note: "0 trades"`); walk-forward does
not. Same bug class as the KPI grid, but worse: silence would be safer than a
green "no divergence".

### O. The run is 100% IN-SAMPLE — HIGH (methodology, not code)
Optimization job window == backtest window, byte-identical
(`1767239100000 → 1783332000000`). +41.87% IS the optimizer's training score.
`survival_summary: null`, `robustness: null` — no robustness evidence at all.

### P. 3 of 5 searched dimensions are still inert
Post-fix space searched `momentum_pct`, `stop_pct`, `lazy_enabled`,
`lazy_momentum_pct`, `lazy_stop_pct`. The last three are DROPPED by the
dispatcher, so the optimizer's `lazy_enabled: false` verdict is noise. Audit
finding [4] (search space must intersect what the dispatcher consumes) is
confirmed by real data and still UNFIXED.

### Q. Objective rewards over-trading
`net_pnl_inr` is monotonic in trade COUNT the same way it was in `lots`.
`momentum_pct` was driven to **6**, near the 5 floor → 124 trades in 124 sessions
(enters essentially every day). Prefer Sharpe / profit factor / return-over-maxDD.

### R. Brokerage not modelled
`cost_config.brokerage_per_order: 0`, 124 trades x 2 legs = 248 orders. At ₹20/order
that is ~₹4,960 ≈ 5.9% of the reported profit. Spread (1%) and STT are modelled.

## Round 3 — fixes 1-3 + two user-reported issues (2026-07-29, later)

User confirmed brokerage ₹0 is correct for their account and STT/other charges
are normal => **finding R is CLOSED, not a defect.**

### Fix 9 — dispatch forwards the FULL engine surface (retires the disclaimer)
`build_engine_params(cfg, merged_params)` in `premium_trigger_dispatch.py`: core
config (validated, authoritative for what it owns) + every other
`ENGINE_PARAM_KEYS` key present in merged_params. `PremiumTriggerConfig` was NOT
widened — it is what the byte-identical parity test protects; the extras travel
around it. Envelope now also carries `engine_params` (the exact dict that drove
the sim) for traceability.
`runtime.py` now passes `lazy_enabled=` to `_load_window`, so `preload_scope`
widens to the full moneyness band + BOTH sides. Without that a lazy leg finds no
opposite-side candles and every activation degrades to `lazy_excluded_no_data` —
a silent zero. An unusable lazy block (no `lazy_momentum_pct|pts`) is pre-empted
and REPORTED rather than raising or silently running single-leg.
**Result for the user's config: `dropped_params == []` — the 12-item banner is
gone, and all 19 params reach the engine (leg_mode='both', lazy_enabled=True,
entry_cutoff, exit_time, trail_x_pct/trail_y_pct all confirmed present).**
`tests/test_premium_dispatch_full_engine_surface.py` — 13 tests.

### Fix 10 — walk-forward stops reporting a pass it did not earn
`premium_walk_forward(option_trades, n_folds, train_pct)` in `walkforward.py`
partitions the OPTION trades by IST session into the same fold geometry. Faithful,
not a new method: the spot version never re-optimizes either, and premium sessions
are independent (strikes lock once at `reference_time`), so no signal straddles a
boundary. `_unmeasured()` returns `measured: False` + a human note and
`divergence_warning: None` — never `False`. `research.py` routes premium runs
here, and also computes `significance` from the OPTION metrics (it was badging
every premium run INSUFFICIENT/"0 trades"). Frontend renders an amber
"Not validated out of sample — absence of evidence, not a pass" panel.
`tests/test_premium_walkforward_honesty.py` — 12 tests.

### Fix 11 — search space restricted to what the engine consumes
`restrict_space_to_engine_params(space, premium_native=)` pins any dimension
outside `ENGINE_PARAM_KEYS`. Tracks the engine, not a denylist, so widening the
engine re-enables dimensions automatically. Wired at both call sites.

### Fix 12 — the Option Execution form's Lots now wins (user-reported)
Their run `... 23:12:05`: form `lots: 5`, plugin default `lots: 2`, **2 traded**
(Qty 130 = 2 x 65). `runtime.py` resolved `req.params.get("lots") or config.lots`,
i.e. strategy-param-first, making the form field inert for any strategy declaring
`lots` — which every AI-authored premium strategy now does. New
`resolve_premium_lots(strategy_lots=, form_lots=)`: **form wins**, strategy is the
fallback for a request with no option-execution block, non-positive/malformed
falls back rather than trading zero quantity. Consistency argument: for every
ORDINARY strategy that field is already the sole sizing control.
Safe because sizing is pinned/non-optimizable, so this cannot reintroduce
lots=100. `tests/test_premium_lots_precedence.py` — 7 tests.

### Also user-reported: "enabling lazy leg changes nothing"
Same root cause as the disclaimer; closed by Fix 9. Confirmed empirically before
the fix: their two runs (23:03:50 lazy off-effective, 23:12:05 lazy on) produced
byte-identical net P&L ₹83,738.69.

### Test-suite changes that were INVERTIONS, not regressions
`test_premium_dropped_params_are_surfaced.py` asserted the fields WERE dropped;
rewritten to assert they are not, while keeping the unusable-lazy case firing.
`test_premium_native_backtestlab_surfacing.py` pinned the old lots expression.

Suite **4058 passed / 0 failed**.

## Round 4 — the train/holdout study (user-approved 2026-07-29)

### Two pre-flight defects, both found BEFORE launching (commit `1835c8a`)
* **S. Optimizer preload never widened for the lazy leg — HIGH.**
  `optimizer.py` called `_load_window(..., sides=["CE","PE"])` with no
  `lazy_enabled=`, so the moneyness band stayed narrow. Since `lazy_enabled` is a
  searched bool, every lazy-ON trial would have degraded to
  `lazy_excluded_no_data` and scored identically to lazy-OFF — the search would
  have "discovered" the lazy leg does not help without ever measuring it.
  `premium_preload_needs_lazy(strategy)` reads the SCHEMA (the optimizer
  overrides defaults) and widens unless lazy is pinned off. Missed earlier
  because this call site already hardcodes both sides, which looked complete.
* **T. Premium trials scored on GROSS P&L — HIGH (audit finding J, now fixed).**
  `merged_params` is an allow-list on `parameter_schema`; `cost_config` is not a
  schema param, so it was never present and costs were off. The Backtest Lab
  injects it, so the optimizer ranked gross and the winner was then verified net.
  Job `option_config.cost_config` is now threaded in.

### Study design
Window split of the previously all-in-sample range:
* TRAIN   2026-01-01 09:15 → 2026-04-30 15:30 (`1767239100000 → 1777543200000`)
* HOLDOUT 2026-05-01 09:15 → 2026-07-06 15:30 (`1777607100000 → 1783332000000`)

Objective **`sharpe`** (scale-invariant and NOT monotonic in trade count, unlike
`net_pnl_inr` which drove `momentum_pct` to its floor). bayesian, n_trials 200
(ceiling; early-stop on), `min_trades: 20`, `opt_workers: 1`, lots 2, costs on
(brokerage ₹0 + 1% spread, per the account owner).

Train job `99e8d19c-eede-4290-9eeb-49db760e94b6`.

### Observations during the run
* **The lazy fix is live**: trials differing only in `lazy_enabled` now yield
  different trade counts (124/125/128). Pre-fix they were byte-identical.
* **Sharpe selects a different regime**: converged on `momentum_pct: 49`
  (selective, 70 trades) vs `net_pnl_inr`'s `momentum_pct: 6` (124 trades, one
  per session). The over-trading bias was objective-driven, as suspected.
* Search early-stopped at 110/200 trials (converged).
* ⚠️ A lazy-ON vs lazy-OFF average-Sharpe gap appeared (1.60 vs 0.70) but is
  CONFOUNDED — TPE allocates trials toward what already scores well (36 vs 14
  samples). Not a causal read; would need lazy pinned across two separate runs.

### RESULT — the strategy FAILS the holdout

Winner (train, `sharpe` = 4.49, converged at 110/200 trials):
`momentum_pct: 49, stop_pct: 30, lazy_enabled: true, lazy_momentum_pct: 8,
lazy_stop_pct: 26` (target_pct/trails/lots pinned).

| | TRAIN Jan 1–Apr 30 | HOLDOUT May 1–Jul 6 |
|---|---|---|
| trades | 70 | 33 |
| win rate | 48.57% | 42.42% |
| net P&L | **+₹121,654.61** | **−₹3,299.89** |
| return | **+60.83%** | **−1.65%** |
| max DD | −9.49% | −14.41% |
| Sharpe (daily) | **4.49** | **−0.27** |
| exits target:stop | 19:31 | 7:17 |

Data coverage is NOT the explanation: 80 train sessions / 44 holdout sessions,
trade frequency consistent (0.88 vs 0.75 per session).

**Verdict: no demonstrated edge.** Not degradation — disappearance. This is the
SECOND independent holdout failure for this strategy family (see
`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`, 2026-07-15).

Notes that matter more than the numbers:
* **Sharpe 4.49 over 50 trading days was itself the tell.** A daily Sharpe that
  high for a directional option-buying strategy is a window artifact, not an edge.
  Treat any train Sharpe > ~2.5 here as a red flag rather than a result.
* **The train run's OWN walk-forward already warned and was ignored by the
  threshold**: IS 53.00% → OOS 43.56%, a 9.44-point decay — just under the
  hardcoded 10-point `divergence_warning` cut. The boolean hid a real signal.
  **TODO: report the delta, not just a boolean, and reconsider 10 points.**
* **On the holdout the new walk-forward fired correctly** (IS 68.81% vs OOS
  17.78%, `divergence: True`). The instrument built in Round 3 earned its keep —
  the old one measured 0 trades and always said "no divergence".
* `dropped_params: NONE` and `engine got leg_mode='both' lazy=True cutoff='15:09'
  exit='15:13' trail_x_pct=5` on BOTH runs — the full configured strategy really ran.

### U. `option_trail_exits` is structurally always 0 here — corrects my own earlier claim
The premium leg walker's exit vocabulary is only `STOP` / `TARGET` / `EOD`
(`premium_momentum.py:176,184,187`) plus `DAY_STOP`
(`premium_momentum_backtest.py:207`). A trail exit is reported as `STOP`, so
`option_trail_exits` (which counts `OPTION_TRAIL_STOP`) can never be non-zero for
a premium-native run. I previously cited `option_trail_exits: 0` as evidence that
"no trail ran" — the conclusion happened to be true then (the percent pair was
being dropped and the points pair was null) but the INSTRUMENT was invalid.
The percent trail IS implemented (`stepped_trail_stop_pct`, XOR-resolved by
`_resolve_trail`) and did run in this study.
**Also unfixed: `DAY_STOP` is absent from `_EXIT_REASON_MAP`, so a session P&L cap
exit is silently bucketed as `OPTION_SIGNAL_EXIT`.** No caps were configured here,
so it did not fire — but it is a live mislabel.

## STILL OPEN (methodology — needs the user)
- The 2026-07-29 runs are 100% in-sample (optimize window == backtest window).
- Objective `net_pnl_inr` is monotonic in trade COUNT; `momentum_pct` was driven
  to 6 against a floor of 5 => 124 trades in 124 sessions. Prefer Sharpe /
  profit factor / return-over-maxDD.
- Re-validate: optimize on Jan-Apr, judge on May-Jul untouched.

## Known-pre-existing failures (NOT caused by this work)
`tests/test_premium_momentum_route.py` x2 — `ServerSelectionTimeoutError` on
localhost:27017. Verified identical on a stashed baseline. This is the documented
Windows localhost/IPv6-vs-Docker-IPv4 trap, not a regression.
