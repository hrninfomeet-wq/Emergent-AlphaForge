# Round-8 FINAL — all 8 agents completed

Workflow `wf_85678c6e-c17` (resumed as `w5bmdnw90`): **8/8 agents, 0 errors**.

## Disputed claims (agents disagreed — resolve by hand)


## result-persistence-display (never audited before)

### HIGH — Run journal renders every premium-native run as 0 trades / 0.00% WR next to a green SIGNIFICANT badge

- site: `frontend/src/components/BacktestRunJournal.jsx:215`
- verifier verdict: CONFIRMED

**Evidence:**
```
The journal row reads ONLY the spot metrics envelope:

BacktestRunJournal.jsx:215-220
```
<td className="p-2 font-mono text-right">{fmtInt(r.metrics?.trade_count)}</td>
<td className="p-2 font-mono text-right">{fmtPct(r.metrics?.win_rate)}</td>
<td className="p-2 font-mono text-right">{fmtNum(r.metrics?.profit_factor, 2)}</td>
<td className={`p-2 font-mono text-right ${colorPnL(r.metrics?.total_pnl_pts)}`}>{fmtPnL(r.metrics?.total_pnl_pts)}</td>
<td className="p-2 font-mono text-right text-danger">{fmtPnL(r.metrics?.max_dd_pts)}</td>
<td className="p-2"><SignificanceBadge significance={r.significance} /></td>
```

For a premium-native strategy `result.metrics` is a zero stub by construction — `algotest_option_buy_nifty.py:49` returns `Signal(direction="NONE", ...)`, `backtest.py:193` skips it (`if sig.direction not in ("CE", "PE"): continue`), so `compute_metrics([])` returns `backtest.py:262-268` `{"trade_count": 0, "win_rate": 0.0, "profit_factor": None, "max_dd_pts": 0.0, "total_pnl_pts": 0.0, ...}`.

But `significance` on the SAME row is computed from the OPTION metrics — `research.py:227-228`:
```
sig = stat_significance(int(om.get("paired_trade_count", 0) or 0),
                        float(om.get("win_rate", 0.0) or 0.0), pf)
```
The detail view already solves this (`backtestMetrics.js:80-110` `resultKpis` reads `option_backtest.metrics` for premium runs; the comment at BacktestLab.jsx:1940-1946 documents exactly this class of bug) — the journal and the "Load past run" dropdown were never given the same treatment. BacktestLab.jsx:853 has the identical defect: `WR {fmtPct(r.metrics?.win_rate)}`.

The data needed is present in the list payload: `research.py:467` projects out only `trades`, `equity_curve`, `walkforward` — `option_backtest.metrics` IS returned.
```

**Scenario:** User runs `algotest_option_buy_nifty` over 253 sessions; the Backtest Lab result pane shows Trades 253, Win Rate 46%, Net P&L ₹+1.2L. They scroll to the Backtest Run Journal to compare it with earlier runs and the same run's row reads: Trades 0, WinRate 0.00%, PF –, Net Pts +0.00, MaxDD +0.00 — beside a green SIGNIFICANT badge whose CI was computed from those 253 option trades. Every premium run in the history table looks like a dud, and sorting the journal by Trades/PF/Net Pts ranks every premium run last regardless of actual performance.

**Fix:** Extract the family-aware KPI selection out of `frontend/src/lib/backtestMetrics.js` (`isPremiumNative` + `resultKpis`) and use it for the journal rows and the "Load past run" label, exactly as ResultsView does. Both already receive `option_backtest` from GET /backtest/runs. Add the unit tag (₹ vs pts) to the column header or the cell so a rupee figure is never rendered under "Net Pts".

### HIGH — Trust scorecard reads the spot metrics stub, so premium-native runs get a false 'Trade count not available' and silently skip three real checks

- site: `backend/app/deployment_quality.py:216`
- verifier verdict: CONFIRMED

**Evidence:**
```
`evaluate_source_quality` resolves trade count from the SPOT envelope only:

deployment_quality.py:176-183
```
def _metrics(source_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the metrics dict whether the source is a preset or a backtest_run."""
    if isinstance(source_doc.get("metrics"), dict):
        return dict(source_doc["metrics"])
```
deployment_quality.py:214-216
```
sharpe = metrics.get("sharpe")
sharpe_val = _safe_float(sharpe) if sharpe is not None else None
trade_count = int(_safe_float(metrics.get("trade_count")))
```
It already holds the option envelope one line later (`deployment_quality.py:211` `om = source_doc.get("option_backtest")`) and uses it for the coverage/ruin checks — but never for `trade_count` or `sharpe`.

Consequences for a premium-native run (`metrics.trade_count == 0`, `metrics.sharpe is None`, `metrics.total_pnl_pts == 0.0`):
1. deployment_quality.py:263-270 fires `"Trade count not available" — "Source backtest does not report a trade count. Cannot assess sample-size reliability."`
2. deployment_quality.py:273 `if sharpe_val is not None` — weak_sharpe check never runs.
3. deployment_quality.py:286 `if total_pnl > 0 and max_dd > 0` — large_drawdown check never runs.
4. deployment_quality.py:315 `if n_trials and sharpe_val is not None and trade_count > 0` — the selection-bias / deflated-Sharpe check never runs.

This is displayed: `research.py:481-485` attaches `doc["quality"] = evaluate_source_quality(doc, ...)` on every GET /backtest/runs/{id}, and BacktestLab.jsx:1981 renders `<TrustScorecard quality={result?.quality} />` (TrustScorecard.jsx:19-20 turns the panel amber and lists each warning). The same function gates deployment at deployments.py:419-432.
```

**Scenario:** A premium-native run with 253 paired option trades and portfolio.sharpe_daily = -0.4 displays a KPI grid reading "Trades 253 / Sharpe -0.40" directly above an amber Trust panel saying "Trade count not available — Source backtest does not report a trade count." The weak-Sharpe warning that should have fired on -0.40 is silently skipped because it reads `metrics.sharpe` (None), and if the run came from an optimizer sweep the selection-bias/deflated-Sharpe check is skipped too. The user deploys a strategy whose three most important trust checks never executed, having acknowledged only a warning that is factually wrong.

**Fix:** In `_metrics` / `evaluate_source_quality`, route by family the same way `research.py:211` does (`(option_result or {}).get("dispatch") == "premium_trigger_config"`): for a premium-native source take `trade_count` from `option_backtest.metrics.paired_trade_count`, `sharpe` from `option_backtest.portfolio.sharpe_daily`, and the drawdown/return pair from `portfolio.max_drawdown_value` / `portfolio.net_pnl_value`. Keep the spot reads for ordinary runs so existing verdicts are unchanged.

### HIGH — A backtest interrupted by a restart stays status:'running' forever — no reconcile, no status column, and it renders as a blank results page

- site: `backend/app/routers/research.py:411`
- verifier verdict: CONFIRMED

**Evidence:**
```
`/backtest/start` inserts `status: "running"` up front (research.py:450-459) and launches an in-process fire-and-forget task (research.py:460 `asyncio.create_task(run_backtest_job(run_id, req))`). The worker only converts `Exception` to a failed status:

research.py:411-417
```
except Exception as exc:  # mark the doc failed so the client sees a result, not a hang
    log.exception("backtest job %s failed: %s", run_id, exc)
    try:
        await db.backtest_runs.update_one({"id": run_id}, {"$set": {
            "status": "failed", "error": str(exc)[:500]}})
```
`asyncio.CancelledError` is a BaseException, not an Exception — a process shutdown/restart never reaches this handler.

The startup reconcile exists but covers only optimization jobs, with a comment that describes this exact mechanism:

server.py:77-92
```
# Reconcile orphaned optimization jobs. Optimization workers are in-process
# fire-and-forget asyncio tasks, so any job left "queued"/"running"/
# "analyzing" in the DB belongs to a previous process (e.g. a container
# rebuild). Mark them "interrupted" ...
reconciled = await db.optimization_jobs.update_many(
    {"status": {"$in": ["queued", "running", "analyzing"]}},
```
There is no equivalent `db.backtest_runs.update_many` anywhere (verified: the only writers of `backtest_runs` are research.py:326/341/347/395/414/450 and optimizer.py:860).

Downstream, nothing surfaces the state. The client gives up silently — BacktestLab.jsx:730-733:
```
if (!doc || doc.status === "running") {
  toast.info("Backtest still running — it'll appear in “Load past run” when done.");
  return;
}
```
The journal has no status column at all (header list, BacktestRunJournal.jsx:170-181), and clicking the row calls `loadPastRun` which sets the doc as the result with no status check (BacktestLab.jsx:754-755), so BacktestLab.jsx:1558 renders a full ResultsView off `result.metrics || {}` (line 1844).
```

**Scenario:** User starts a 3-year backtest and the container restarts (or they close the app) 10 minutes in. The doc is left `{status: "running", progress: 0}` with no metrics and no error, permanently. The journal shows a normal-looking row with `–` in every metric column — indistinguishable from a finished zero-trade run. Clicking it renders the complete Backtest Lab results page with every KPI card blank, no error banner, and a live "Deploy" button. The user re-runs, assuming the strategy produced nothing.

**Fix:** Add a `backtest_runs` arm to the startup reconcile in `server.py` alongside the optimization-jobs one: `update_many({"status": "running"}, {"$set": {"status": "failed", "error": "Interrupted by a server restart."}})`. Separately, add a Status column to BacktestRunJournal and make `loadPastRun`/ResultsView show an explicit failure/incomplete banner (and hide Deploy / Save-as-preset) when `status !== "done"` and `metrics` is absent.

### MEDIUM — GET /backtest/runs strips the small spot trade array but returns the larger option_backtest.trades for every row

- site: `backend/app/routers/research.py:467`
- verifier verdict: CONFIRMED

**Evidence:**
```
research.py:464-469
```
@api.get("/backtest/runs")
async def list_backtest_runs(limit: int = Query(50, le=200)):
    db = get_db()
    cur = db.backtest_runs.find({}, {"_id": 0, "trades": 0, "equity_curve": 0, "walkforward": 0}).sort("created_at", -1).limit(limit)
```
The projection removes `trades` (a spot Trade dict — 17 keys after `_clean_trade_dict`, backtest.py:249-258) but leaves `option_backtest` untouched. That object carries, per run:
 * `option_backtest.trades` — one row per spot trade, PAIRED or MISSING (option_backtest.py:714/722/743/753/848), each with ~45 keys including a nested `charges` dict and a nested `context` dict (option_backtest.py:848-883);
 * `option_backtest.skipped_trades` (runtime.py:1537);
 * `option_backtest.equity_curve` and `option_backtest.portfolio.curve` — one entry per paired trade each (option_backtest.py:452-458, portfolio.py:165-171);
 * `option_backtest.context_breakdown` and the full `option_backtest.data` block.

So the array the projection deletes is strictly smaller than the one it keeps. The journal asks for the maximum: BacktestRunJournal.jsx:32 `const d = await api.listBacktestRuns(200);` against `limit: int = Query(50, le=200)`.

Relatedly, there is no cap or truncation on the persisted trade arrays in either writer (research.py:309/398 write `res["trades"]` and research.py:323/406 write `option_result` whole), and therefore no truncation-disclosure field exists on the run document at all.
```

**Scenario:** A user with 60 saved option backtests, several of them multi-thousand-trade scalper runs, opens the Backtest Lab. The journal issues `GET /api/backtest/runs?limit=200` and the response carries every option leg of every run — tens of MB — to render a table that reads only `metrics`, `significance`, `config.mode` and `created_at`. The page stalls on load and the trimming the projection was written to provide never happens. In the same vein, a run large enough to exceed the 16 MB BSON limit is lost entirely at `update_one` (research.py:395) and surfaces only as `status: failed` with a driver message, because nothing caps or truncates the arrays first.

**Fix:** Change the list projection to an INCLUSION projection of exactly what the journal renders — `{"_id": 0, "id": 1, "created_at": 1, "name": 1, "instrument": 1, "strategy_id": 1, "status": 1, "config.mode": 1, "metrics": 1, "significance": 1, "option_backtest.metrics": 1, "option_backtest.dispatch": 1}` — which also hands the journal the premium metrics it needs for finding #1. Apply the same to `journals.py:208`. Separately, cap the persisted trade arrays and write an explicit `trades_truncated` / `trades_total` pair when the cap bites.

### MEDIUM — Preset saved from a displayed run drops sizing_config, trade window and spread_min_pts, so the deployment sizes differently from the run that justified it

- site: `frontend/src/pages/BacktestLab.jsx:422`
- verifier verdict: CONFIRMED

**Evidence:**
```
`buildExecutionFromRun` claims parity with the backend builder (BacktestLab.jsx:417-421: "Same shape as buildExecutionFromConfig / the backend's execution_from_option_config") but emits only moneyness / dte_filter / exit_mode / lots / premium levels / a 2-field cost_config / exit_controls / daily_caps (BacktestLab.jsx:425-468). The backend's canonical builder emits three more things:

preset_execution.py:52-55
```
for key in ("trade_window_start", "trade_window_end"):
    val = option_cfg.get(key)
    if val:
        execution[key] = str(val)
```
preset_execution.py:64-66
```
_min_pts = _num(cost_cfg.get("spread_min_pts"))
if _min_pts is not None:
    execution["cost_config"]["spread_min_pts"] = _min_pts
```
preset_execution.py:76-79
```
sizing_config = (option_cfg or {}).get("sizing_config")
if isinstance(sizing_config, dict):
    from app.portfolio import SizingConfig
    execution["sizing_config"] = SizingConfig.from_dict(sizing_config).to_dict()
```
All three ARE in the run doc the UI is looking at: buildPayload sends `sizing_config` (BacktestLab.jsx:579-587), `cost_config.spread_min_pts` (line 577) and `trade_window_start/end` (lines 552-553), and `/backtest/start` persists the whole request as `config` (research.py:454).

The consumer is real: strategy_deployments.py:125-132
```
elif st == "preset":
    ex = (source_doc.get("config") or {}).get("execution") or {}
    sizing_config = ex.get("sizing_config")
    lots = ex.get("lots")
...
if not isinstance(sizing_config, dict):
    return None
```
and the docstring at strategy_deployments.py:110-111 states the consequence: "Returns ... None when the source carries no sizing config (→ live falls back to default_lots)." The restore side already expects the window (`applyPreset`, BacktestLab.jsx:287-288 `...(ex.trade_window_start ? { trade_window_start: ex.trade_window_start } : {})`) — the save side never writes it. `saveAsPreset`'s `buildExecutionFromConfig` (BacktestLab.jsx:321-374) has the identical three omissions.
```

**Scenario:** User runs a backtest with premium_at_risk sizing (capital ₹5,00,000, risk 1%, max_lots 10) and a 09:25–14:50 window; the results page shows Account Value, Return % and Max DD % all computed off that sizing. They click "Save as preset" on that result and deploy the preset. `deployment_sizing_from_source` returns None because `execution.sizing_config` is absent, so the deployment trades `default_lots` instead of the risk-sized lot count, and re-applying the preset in the Lab restores the form default 15:00 window instead of 14:50 — silently producing a different net result on replay (the preset_execution comment measures 5.4% on one winner).

**Fix:** Have both JS builders emit the three missing keys from the same source of truth: `trade_window_start/end` from `run.config` (or `config.trade_window_*` for the form builder), `cost_config.spread_min_pts`, and the full `sizing_config` object. Better, add a backend endpoint that runs `execution_from_option_config` on the stored run and returns the execution block, so there is one implementation instead of three. Also teach `applyPreset` to restore `spread_min_pts` and `sizing_config`.

### MEDIUM — Premium-native runs persist a hardcoded candles_capped:false that was never checked, while their loader silently truncates oldest-first

- site: `backend/app/runtime.py:1311`
- verifier verdict: CONFIRMED

**Evidence:**
```
The premium dispatch stamps the flag as a literal:

runtime.py:1307-1313
```
pm_result["data"] = {
    "expiry_date": config.expiry_date, "expiry_mode": "premium_trigger_config",
    "resolved_expiries": [], "trades_without_expiry": 0,
    "contracts_loaded": len(contracts), "instrument_keys_needed": 0,
    "candles_loaded": int(len(option_candles)), "candles_capped": False,
    "source": "premium_trigger_dispatch", "auto_fetch": False, "dte_filter": None,
}
```
But the loader that produced `option_candles` truncates without reporting it — premium_momentum_routes.py:156-161:
```
option_rows = await db.options_1m.find(
    {"instrument_key": {"$in": canon_keys},
     "ts": {"$gte": int(start_ts), "$lte": int(end_ts)}},
    {"_id": 0},
).sort("ts", 1).to_list(length=OPTION_CANDLE_LOAD_CAP)
return spot_df, pd.DataFrame(option_rows), contracts
```
`OPTION_CANDLE_LOAD_CAP = 4_000_000` (runtime.py:77) and the sort is ascending, so a capped load drops the NEWEST candles. The ordinary paired path guards exactly this and says so — runtime.py:1443-1452:
```
if len(candle_rows) >= OPTION_CANDLE_LOAD_CAP:
    # Oldest-first sort means a capped load drops the NEWEST candles, so
    # the most recent trades silently fail to pair. Never let this pass
    # unnoticed: warn and surface candles_capped in the response.
    candles_capped = True
```
The UI's warning is keyed on that flag — BacktestLab.jsx:2152 `{data.candles_capped && (...)}` — so it can never fire for a premium run. The lazy-leg preload widens to 5 moneynesses x 2 sides (premium_momentum_backtest.py:112-124 `preload_scope`), which multiplies the row count that reaches the cap.
```

**Scenario:** A multi-year premium-momentum backtest with the lazy reversal leg enabled loads 4,000,000 option candles and is truncated. Because the sort is oldest-first, the most recent months have no premium series, so those sessions are excluded as `no_premium_series` and the run reports a smaller sample over an older, unrepresentative period. The persisted document asserts `candles_capped: false`, the Option Execution card shows no cap warning, and nothing in the UI distinguishes this from a genuine data gap.

**Fix:** Return a capped flag from `_load_window` (`len(option_rows) >= OPTION_CANDLE_LOAD_CAP`) alongside the frame, log the same warning the ordinary path logs, and set `pm_result["data"]["candles_capped"]` from it at runtime.py:1311 instead of hardcoding False. Consider also switching the truncating load to a descending sort or a per-key bound so the newest data is never the part that is dropped.

### MEDIUM — A failed or still-running backtest run can be selected as a deployment source; nothing anywhere checks status

- site: `backend/app/runtime.py:1906`
- verifier verdict: CONFIRMED

**Evidence:**
```
runtime.py:1905-1906
```
elif source_type == "backtest_run":
    doc = await db.backtest_runs.find_one({"id": source_id}, {"_id": 0, "trades": 0, "equity_curve": 0})
```
No `status` predicate. The validation that follows (runtime.py:1949-1971) checks only that the strategy is registered and the params/instrument/timeframe validate — all of which are present from the moment `/backtest/start` inserts the doc (research.py:450-459 writes `config`, `instrument`, `strategy_id` before any compute runs).

`build_deployment_doc` then tolerates the empty result silently — strategy_deployments.py:199:
```
metrics = source_doc.get("metrics") if isinstance(source_doc.get("metrics"), dict) else {}
```
and strategy_deployments.py:230 `sizing_pin = deployment_sizing_from_source(...)` returns None because `option_backtest` was never written.

The quality gate (deployments.py:419-432) raises only `acknowledgment_required`, and its warnings for such a doc are the generic `missing_walk_forward` (deployment_quality.py:220-227) and `missing_trade_count` (deployment_quality.py:263-270) — neither says the source run FAILED or never finished, because no code path reads `status`. Contrast the sibling evidence query at deployments.py:308-310, which does filter optimization jobs by `"status": "done"`.

The path is reachable from the UI: the journal lists runs regardless of status (research.py:467 has no filter), `loadPastRun` sets any doc as the result (BacktestLab.jsx:754-755), and ResultsView renders a live Deploy button on `result?.id` (BacktestLab.jsx:1926-1936).
```

**Scenario:** A backtest fails partway (Upstox gap-fill error, or a restart leaves it 'running'). The user opens the journal, clicks the row — which shows no status and blank metrics — sees a results page with a Deploy button, and deploys. The deployment is created against a run that produced no trades, no walk-forward and no option result; sizing falls back to default_lots, `metrics_snapshot` is empty, and the only warnings shown are the generic 'no walk-forward' / 'trade count not available' pair, which look like ordinary advisory noise rather than 'this backtest never completed'.

**Fix:** In `_load_deployment_source`, reject a `backtest_run` whose `status` exists and is not `"done"` with a 409 naming the actual state (`"Backtest run <id> is <status> — deploy only from a completed run"`). Runs written by `/backtest/run` and by the optimizer carry no `status` key at all, so gate on `doc.get("status") not in (None, "done")` to stay backward compatible.

### MEDIUM — Monte Carlo card silently resamples only the first 1000 trades and reports the truncated count as the sample size

- site: `frontend/src/pages/BacktestLab.jsx:2550`
- verifier verdict: CONFIRMED

**Evidence:**
```
BacktestLab.jsx:2548-2552
```
const source = optionEnabled ? (optionBacktest?.trades || []) : (trades || []);
const pnlKey = optionEnabled ? "option_pnl_value" : "pnl_pts";
const pnl = source.map((t) => Number(t[pnlKey])).filter((v) => Number.isFinite(v)).slice(0, 1000);
const N = pnl.length;
```
`option_backtest.trades` is in chronological order, so `.slice(0, 1000)` keeps the OLDEST 1000 trades — not a random sample. `N` is then the truncated length, and the card presents it as the full sample without any truncation notice:

BacktestLab.jsx:2594-2596
```
{fmtInt(sims.runs)} bootstrap runs over {fmtInt(sims.N)} trades (drawn with replacement, {unit}). Shows how
path-luck could reshape drawdown and the final result given this strategy's per-trade P&L.
```
and the card's own advice is keyed on the understated statistic — BacktestLab.jsx:2627: `size for the P95 drawdown of ${fmtV(sims.ddP95)}`. Expected maximum drawdown grows with path length, so bootstrapping 1000-trade paths for a 4300-trade strategy systematically understates ddP95 and P(net<0).
```

**Scenario:** A confluence run produces 4,300 paired option trades. The Monte Carlo panel says "1,000 bootstrap runs over 1,000 trades" and quotes a P95 drawdown of ₹85,000, telling the user to size for it. The real 4,300-trade path distribution has a materially deeper P95. The user sizes capital against a number derived from a quarter of their own trades, and the panel gives no indication that any truncation occurred — the trades table below it shows all 4,300.

**Fix:** Either remove the cap (1000 runs x N trades is cheap at these sizes) or, if it must stay, sample uniformly at random rather than taking the head, and disclose it: render `over {fmtInt(sims.N)} of {fmtInt(total)} trades (sampled)` whenever `total > sims.N`. Also filter to `status === "PAIRED"` explicitly rather than relying on `Number.isFinite` to drop the MISSING_* rows.

### LOW — data_coverage is written only by POST /backtest/run; the async endpoint the UI actually calls discards it

- site: `backend/app/routers/research.py:354`
- verifier verdict: CONFIRMED

**Evidence:**
```
Field-by-field, `data_coverage` is the only payload key the sync writer produces that the async writer does not (async-only keys are `status`, `progress`, `finished_at`).

Sync — research.py:260 keeps the coverage and research.py:313-317 persists it with an explicit promise:
```
df, data_coverage = await attach_required_data(df, strategy.required_data)
...
# Empty for every strategy that declares no `required_data`. When
# non-empty it reports per-column coverage over THIS window, so a result
# can never quietly rest on a column that was absent for part of it.
"data_coverage": data_coverage,
```
Async — research.py:352-354 throws it away:
```
# Warehouse-backed columns join on the RAW frame while we are still on the
# event loop — the _compute closure below is sync and cannot await.
df, _ = await attach_required_data(df, strategy.required_data)
```
and the `$set` at research.py:395-410 has no `data_coverage` key. The UI calls the async endpoint (api.js:174-175 `startBacktest: (payload) => apiClient.post("/backtest/start", payload)`, used at BacktestLab.jsx:717).

The only other record of partial coverage is a log line, not a durable field — warehouse.py:391-401 `# Degrade LOUDLY ... log.warning("data column %r covers only %.2f%% of this window ...")`. Currently latent: no shipped strategy overrides `required_data` (default `[]` at strategies/base.py:93), so today the value is always `{}`.

Secondary response-shape divergence: the sync endpoint returns `serialize_doc(result_doc)` (research.py:327) with no `quality` key, while the async path's read endpoint attaches one (research.py:481-485).
```

**Scenario:** The first strategy to declare `required_data: ["vix"]` is backtested from the Backtest Lab over a window where India VIX covers only 60% of bars. `attach_required_data` logs the warning to the server console and returns coverage, which the async worker drops on the floor. The saved run document has no `data_coverage`, so nothing in the UI or in any later audit of that run can show that the VIX-gated rule was inert on 40% of the window — the exact failure mode warehouse.py:391-393 says this mechanism exists to prevent.

**Fix:** In `run_backtest_job`, capture the coverage (`df, data_coverage = await attach_required_data(...)`) and include `"data_coverage": data_coverage` in the `$set` at research.py:395. Better, factor the shared body of `backtest_run` and `run_backtest_job` into one result-document builder the way `resolve_wf_and_significance` already did for the walk-forward routing — the two handlers have now drifted twice for the same reason.


## All claim verdicts

### [4] CONFIRMED — HIGH — A single raising grid combo still kills the whole job — O14's error record poisons the Top-N sort

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1472-1478 (grid branch) — I read it verbatim:
```
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
```
The analyze stage then sorts that same list with no None filter — backend/app/optimizer.py:1686:
```
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
(I re-read 1585-1690: nothing between the loop and this line scrubs or replaces the None record.) I ran the exact expression in this repo's python: `sorted([{'objective_value':1.23},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)` -> `TypeError: '<' not supported between instances of 'float' and 'NoneType'`.
The TypeError is caught only by the whole-function handler, backend/app/optimizer.py:1998-2000:
```
    except Exception as e:
        log.exception(f"optimization {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e), "finished_at": ...})
```
so every good trial, best_params, the saved best backtest and all analyses are discarded. The poison persists across Resume: `_compact_trial` keeps the null score (optimizer.py:635 `"objective_value": t.get("objective_value"),`), resume rehydrates it (optimizer.py:1424-1425 `trial_history = list(rdoc.get("trial_log") or [])`), and `resume_optimization` explicitly allows `failed` (optimizer.py:2041 `if doc.get("status") not in ("paused", "interrupted", "failed"): return False`) — so Resume re-hits the identical sort. Note `_rebuild_study` DOES defend against None (optimizer.py:681-682 `v = float(v) if v is not None else _DISQUALIFY`), proving the None-score hazard was known at one site and missed at the sort. The only O14 test is source-grep only — tests/test_optimizer_robustness_contract.py:17-23 asserts the strings "disqualified, continuing" and '"metrics": None' are present; it never exercises the sort. Findings [9] and [12] are the same defect restated.
```

**Minimal fix:** At backend/app/optimizer.py:1686 make the sort total-order safe: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)` (errored combos carry no score and are already recorded separately). Same guard needed on the grid importance fallback at optimizer.py:1701 (`vals = [... for t in trial_history if name in t["params"]]` feeds Nones into np.mean — currently masked by a bare `except Exception: pass`, which silently drops importance). Add a behavioural test that drives the grid branch with one raising combo and asserts a terminal non-failed status.

### [5] REFUTED — HIGH — Zero-survivor refusal is defeated: apply-as-preset falls back to the stale spot best that the survival gate rejected

- severity if real: NONE

**Evidence:**
```
Two independent reasons, both read directly.
(1) The endpoint does NOT accept the zero-survivor status. backend/app/routers/research.py:705:
```
    if job.get("status") not in ("done", "cancelled", "paused", "interrupted", "failed"):
        raise HTTPException(400, "Job has no finished result yet")
```
"done_no_survivor" is not in that tuple, and the zero-survivor branch sets exactly that status — backend/app/optimizer.py:1878-1879:
```
        if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0:
            final_status = "done_no_survivor"
```
So the request 400s before reaching the fallback at research.py:707. The audit's own parenthetical ("status check at research.py:705 allows `done`") misreads a tuple membership test as a prefix match.
(2) The claim's load-bearing premise — "that terminal patch (optimizer.py:1853-1883) contains **no `best_so_far` key**" — is false in current source. The finished patch re-persists it, backend/app/optimizer.py:1900-1905:
```
            "best_so_far": {
                "value": best_so_far["value"] if best_so_far["value"] > -1e8 else None,
                "params": best_so_far["params"],
                "metrics": best_so_far["metrics"],
                "trial_num": best_so_far.get("trial_num"),
            },
```
and in the zero-survivor branch that local is already emptied (optimizer.py:1794 `best_so_far = {"value": -1e9, "params": {}, "metrics": {}, "trial_num": -1}`), so the stored `best_so_far.params` is `{}` — the fallback `(job.get("best_so_far") or {}).get("params")` yields `{}`, which is falsy, so research.py:708-709 raises "Job has no best parameters to save". This re-persist is item (a) of the ALREADY FIXED commit 588208b.
The frontend claim also fails for the same reason: frontend/src/pages/Optimizer.jsx:1436 `const hasBest = (bsf.params && Object.keys(bsf.params).length > 0) || ...` is false for `params: {}`, so the Save-as-Preset button at Optimizer.jsx:1492-1497 does not render for a zero-survivor job.
```

**Minimal fix:** None required. If belt-and-braces is wanted, tighten the fallback at backend/app/routers/research.py:707 to `job.get("best_params") if job.get("best_params") is not None else ...` so a present-but-empty best can never fall through — but no reachable path today produces the claimed silent save.

### [6] CONFIRMED — HIGH — Resume silently skips up to 49 trials it then counts as completed (grid: 49 combos never evaluated)

- severity if real: HIGH

**Evidence:**
```
Resume trusts the high-cadence counter over the low-cadence evidence — backend/app/optimizer.py:1424-1425:
```
            trial_history = list(rdoc.get("trial_log") or [])
            completed = int(rdoc.get("n_trials_completed") or len(trial_history))
```
The two are written 10x apart in every trial loop. Grid, optimizer.py:1525-1531:
```
                if completed % 5 == 0:
                    await _update_job(job_id, {"n_trials_completed": completed, "best_so_far": {...}})
                if completed % 50 == 0:
                    await _flush_trial_log(job_id, trial_history, best_so_far, completed)
```
Sequential bayesian, optimizer.py:1572-1578 (`if completed % 5 == 0 or completed == n_trials:` vs `if completed % 50 == 0:`) and parallel, optimizer.py:1593-1599 (`if (completed // 5) > (prior // 5) ...` vs `if (completed // 50) > (prior // 50):`) are identical in shape. `_flush_trial_log` writes both keys together (optimizer.py:643-651 includes `"n_trials_completed": completed`), so only the %5-only writes create the gap — up to 49.
A user pause is consistent because `_maybe_pause` flushes first (optimizer.py:1483 `await _flush_trial_log(job_id, trial_history, best_so_far, completed)`), but a server restart is not: backend/server.py:84-92 flips any `queued`/`running`/`analyzing` job to `interrupted` (a resumable state per optimizer.py:2041) purely by DB update, with no flush:
```
        reconciled = await db.optimization_jobs.update_many(
            {"status": {"$in": ["queued", "running", "analyzing"]}},
            {"$set": {"status": "interrupted", "paused": False, ...}},
        )
```
The resumed loops then skip by the inflated counter, not by the history: grid `for params in combos[completed:]` (optimizer.py:1493), sequential `for i in range(completed, n_trials)` (optimizer.py:1541), parallel `while completed < n_trials:` (optimizer.py:1584). So with `n_trials_completed: 95` and 50 persisted records, combos 50-94 are never evaluated, yet the job reports `n_trials_completed: 200` and status `done` (optimizer.py:1883-1886). The truncated pool also feeds Stage-2 finalist selection and top_n via `sorted_trials` (optimizer.py:1686), and `_rebuild_study` re-seeds the sampler from only the 50 surviving records (optimizer.py:675-685), so the resumed job's reported best is drawn from a smaller search than the ceiling it claims.
```

**Minimal fix:** Never let the counter run ahead of the evidence. Smallest change: at backend/app/optimizer.py:1425 use `completed = min(int(rdoc.get("n_trials_completed") or 0), len(trial_history))` (falling back to `len(trial_history)`), so resume restarts from the last flushed record. Better: flush the trial log on the same 5-trial cadence (change each `% 50` / `// 50` guard at optimizer.py:1530, 1577, 1598 to the 5-trial one) so the two can never diverge, and/or write `n_trials_completed` only from inside `_flush_trial_log`. Add a test that persists `n_trials_completed=95` with a 50-record `trial_log` and asserts the resumed grid loop starts at combo 50.

### [9] CONFIRMED — HIGH — Grid search: one raising combo is recorded with objective_value=None, then the analyze stage sorts on it and the whole job dies with TypeError — defeating the explicit "must NOT crash the whole job" handler

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1469-1477 (grid loop) records a None objective:
                try:
                    metrics, merged = await asyncio.to_thread(evaluate, params)
                    val = obj(metrics)
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue

The analyze stage then sorts that same list with NO filter and NO try, at backend/app/optimizer.py:1652:
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)

Reproduced in this repo's Python:
  sorted([{'objective_value':1.5},{'objective_value':None}], key=lambda t: t['objective_value'], reverse=True)
  -> TypeError: '<' not supported between instances of 'float' and 'NoneType'

The only enclosing handler is the job-level one, backend/app/optimizer.py:1957-1959:
    except Exception as e:
        log.exception(f"optimization {job_id} crashed")
        await _update_job(job_id, {"status": "failed", "error": str(e), "finished_at": datetime.now(timezone.utc).isoformat()})
so every successful trial's results (best_params, importance, heatmap, robustness, the saved run) are discarded.

The second crash site is real too — backend/app/rerank_select.py:39:
        if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:
The key EXISTS with value None, so .get returns None, and `None <= -1e9` raises (verified: TypeError: '<=' not supported between instances of 'NoneType' and 'float').

Unrecoverable-on-resume is also confirmed: backend/app/optimizer.py:631-637 `_compact_trial` keeps the poison record verbatim — `"objective_value": t.get("objective_value"),` — so the resumed run (which skips the already-counted combo via `combos[completed:]`, optimizer.py:1458) rebuilds a trial_history that still contains None and dies at :1652 again.
```

**Minimal fix:** At backend/app/optimizer.py:1652 filter the failed records before sorting: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)`. Also coerce None in the fallback-importance loop (optimizer.py:1673-1682, which reads t["objective_value"] into np.mean — inside a try, so it only silently loses importance) and make rerank_select.py:39 None-safe: `v = t.get("objective_value"); if v is None or v <= DISQUALIFY: continue`.

### [10] CONFIRMED — HIGH — min_trades guards the SPOT trade count only; option-rerank promotes any candidate with a single PAIRED trade, so a statistically empty config becomes the reported best

- severity if real: HIGH

**Evidence:**
```
The guard reads whatever `trade_count` Stage-1 metrics carry — backend/app/optimizer.py:145-151:
    tc = int(metrics.get("trade_count", 0) or 0)
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample

Promotion in option_rerank mode (survival OFF, the default) requires only ONE paired trade — backend/app/optimizer.py:1801-1812:
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
                    "params": best["params"],

and the ordering that produces ranked[0] only uses the boolean, backend/app/optimizer.py:1183:
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)

`_option_rerank` records the count but filters on nothing — backend/app/optimizer.py:1161-1171:
            "option_pnl_value": float(m.get("total_option_pnl_value", 0.0) or 0.0),
            ...
            "paired_trade_count": int(m.get("paired_trade_count", 0) or 0),

That promoted config is then saved and published unconditionally (optimizer.py:1861 `if best_so_far["params"]:` -> `_save_best_as_backtest`, optimizer.py:1888 `"best_params": best_so_far["params"],`).

The codebase documents the gap itself, backend/app/survival.py:21-23:
# A tail statistic (ruin probability) needs more than the spot min_trades=10
# guard (which counts SPOT trades); this counts PAIRED rupee trades.
MIN_TRADES_FOR_RUIN = 100
and that gate is off by default — backend/app/survival.py:36 `    enabled: bool = False`.

One correction to the auditor's scope: for PREMIUM-NATIVE strategies the guard IS paired-aware, because backend/app/optimizer.py:506 remaps the metric — `"trade_count": int(m.get("paired_trade_count", 0) or 0),`. The defect is real for the ORDINARY path only (run_backtest spot metrics via optimizer.py:431-432).
```

**Minimal fix:** In the option_rerank block, filter `ranked` by the paired count before both promotion paths: after the `_option_rerank` call, `_lowpair = [r for r in ranked if r["paired_trade_count"] < min_trades]` and use `ranked_ok = [r for r in ranked if r["paired_trade_count"] >= min_trades]` for the survivor sort (optimizer.py:1707-1735) and for the `elif ranked and ranked[0]["paired_trade_count"] > 0:` promotion at optimizer.py:1801 (keep the full `ranked` for the displayed table). Add `"dropped_low_pairing": len(_lowpair)` to `rerank_info` (optimizer.py:1818-1827) so a low-pairing sweep is visible instead of silently promoted.

### [11] CONFIRMED — HIGH — When every trial fails the guard rails the optimizer still promotes, saves and lets the user apply a disqualified config — and the dedicated "no usable result" banner never fires

- severity if real: HIGH

**Evidence:**
```
The sentinel is -1e9 (backend/app/rerank_select.py:13 `DISQUALIFY = -1e9`, imported as _DISQUALIFY at optimizer.py:57) and it beats the -inf seed at backend/app/optimizer.py:1432:
            best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}

All three trial loops overwrite it with the disqualified candidate — grid at optimizer.py:1480:
                if val > best_so_far["value"]:
                    best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}
sequential bayesian at optimizer.py:1524 and the parallel path at optimizer.py:1580, both:
                if study_best_val is not None and study_best_val > best_so_far["value"]:
(-1e9 > -inf is True, and study.best_params is non-empty because the trials COMPLETED with value -1e9 rather than failing.)

Non-empty params then trigger the save, backend/app/optimizer.py:1861-1866:
        if best_so_far["params"]:
            best_merged = strategy.merged_params(best_so_far["params"])
            df_best = get_enriched(best_merged)
            best_backtest_run_id = await _save_best_as_backtest(
and it is published as the answer while only the headline is blanked, optimizer.py:1888-1890:
            "best_params": best_so_far["params"],
            "best_value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,
            "best_metrics": best_so_far["metrics"],

The UI guard is keyed on params, not value — frontend/src/pages/Optimizer.jsx:1436-1437:
  const hasBest = (bsf.params && Object.keys(bsf.params).length > 0)
    || (isWfo && job.best_params && Object.keys(job.best_params).length > 0);
so the banner written for exactly this case is skipped, Optimizer.jsx:1559-1561:
      {(finished || cancelled) && !hasBest && (
        <div ... data-testid="opt-no-result">
          No trial produced a usable result — every candidate either took no trades or was disqualified by the guard rails.
and the trophy card renders instead (Optimizer.jsx:1568 `{bsf.params && Object.keys(bsf.params).length > 0 && (`) with real-looking bsf.metrics and a "—" objective (Optimizer.jsx:214 `const fmtBest = (v) => (v == null || v <= -1e8) ? "—" : Number(v).toFixed(3);`).

Apply-as-preset accepts it, backend/app/routers/research.py:707-709:
    best_params = job.get("best_params") or (job.get("best_so_far") or {}).get("params")
    if 
```

**Minimal fix:** Require the value to clear the sentinel before promoting: change optimizer.py:1480 to `if val > _DISQUALIFY and val > best_so_far["value"]:` and optimizer.py:1524 / 1580 to `if study_best_val is not None and study_best_val > _DISQUALIFY and study_best_val > best_so_far["value"]:`. best_so_far then stays `{"value": -inf, "params": {}}`, which already makes optimizer.py:1861 skip `_save_best_as_backtest`, publishes empty best_params, fires the Optimizer.jsx:1559 banner, and makes research.py:709 reject apply-as-preset — no frontend change needed.

### [12] CONFIRMED — MEDIUM — A single raising grid combo permanently fails the whole job at the analyze stage (None objective_value poisons the sort)

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1468-1478 (grid loop) records a None score instead of raising:
```
                # O14: a single raising combo must NOT crash the whole job (resume
                # then deterministically re-hits the same combo forever). Mirror the
                # bayesian study.optimize(catch=Exception): disqualify + continue.
                try:
                    metrics, merged = await asyncio.to_thread(evaluate, params)
                    val = obj(metrics)
                except Exception as exc:
                    log.warning("grid trial %d raised (%s) — disqualified, continuing",
                                completed, exc)
                    trial_history.append({"params": params, "metrics": None,
                                          "objective_value": None, "error": str(exc)[:200]})
                    completed += 1
                    continue
```
The analyze stage then sorts that list unguarded — backend/app/optimizer.py:1652:
```
        sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)
```
I ran the exact comparison in this repo's interpreter: `sorted([{'v':1.5},{'v':None}], key=lambda t:t['v'], reverse=True)` -> `TypeError: '<' not supported between instances of 'float' and 'NoneType'`. Nothing between 1478 and 1652 filters or coerces the None (verified by reading the whole span; the only coercion is inside `_rebuild_study`, backend/app/optimizer.py:681-682 `v = rec.get("objective_value"); v = float(v) if v is not None else _DISQUALIFY`, which mutates only the Optuna study, not `trial_history`). The TypeError escapes to the top-level handler, backend/app/optimizer.py:1957-1960: `except Exception as e: ... await _update_job(job_id, {"status": "failed", "error": str(e), ...})`. Resume re-hits it: `trial_history = list(rdoc.get("trial_log") or [])` (optimizer.py:1390) and `_compact_trial` preserves the key verbatim (optimizer.py:634 `"objective_value": t.get("objective_value")`), while `completed` is restored from the same doc so the grid loop is a no-op. Secondary confirmation: backend/app/rerank_select.py:39 `if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:` also raises on an explicit-None record (the default only applies when the key is absent).
```

**Minimal fix:** At backend/app/optimizer.py:1652 rank only scored trials: `sorted_trials = sorted((t for t in trial_history if t.get("objective_value") is not None), key=lambda t: t["objective_value"], reverse=True)`; also guard backend/app/rerank_select.py:39 with `v = t.get("objective_value"); if v is None or v <= DISQUALIFY: continue`, and skip None in the grid importance fallback (optimizer.py:1667). Add a grid test with one raising combo asserting the job reaches status done.

### [13] REFUTED — MEDIUM — `search_exit_controls` burns the analyze budget on a provably no-op grid whenever exit_mode is the default `spot_exit`

- severity if real: NONE

**Evidence:**
```
The claimed reachable state (a job running with `search_exit_controls=True` while `exit_mode` is `spot_exit`) is rejected at the only job-creation site. backend/app/routers/research.py:585-594, inside `@api.post("/optimize/start")` (declared at research.py:556):
```
    # O5: premium-based exit-control search is a NO-OP unless option execution uses
    # premium levels (exit_mode='option_levels'). Under the default spot exit the
    # grid burns with zero effect and silently adopts nothing — reject it loudly so
    # the user picks option-levels exit or turns the search off.
    if req.search_exit_controls and str(oc.get("exit_mode") or "").strip() != "option_levels":
        raise HTTPException(
            400,
            "search_exit_controls requires option_config.exit_mode='option_levels' — "
            "premium-based exit controls (trailing/target/stop on the option leg) are "
            "a no-op under spot exit, so the grid would burn with no effect.")
```
That HTTPException fires before `job_id = await optimizer_create_job(req.model_dump())` (research.py:603), and a full grep shows research.py:603 is the ONLY caller of `optimizer.create_job`, i.e. the only writer of the `config` payload that `payload.get("search_exit_controls")` (optimizer.py:1738) later reads; `resume_optimization` (optimizer.py:1993-2010) replays that already-validated stored config. So the grid at optimizer.py:1738-1757 cannot execute under `spot_exit`, and the described symptom (silent wasted survival evaluations, budget truncation, `chosen_exit_controls` never set) is unreachable. The audit's own suggested fix ("skip it with a job-level warning unless exit_mode == option_levels") is already implemented, more strictly, as a hard 400. Residual, NOT what was claimed: the UI checkbox at frontend/src/pages/Optimizer.jsx:1166 is gated only on `evaluation_mode === "option_rerank"` and survival-enabled, not on `option_exit_mode === "option_levels"`, so such a user gets a 400 on Start rather than a disabled control — a cosmetic UX gap, not wasted compute or a false verdict.
```

**Minimal fix:** None required for correctness. Optional UX: disable/annotate the "Auto-tune exit controls" checkbox in frontend/src/pages/Optimizer.jsx:1160-1178 unless `config.option_exit_mode === "option_levels"`, so the constraint shows before Start instead of as a 400.

### [14] CONFIRMED — MEDIUM — On a truncated survival stage, un-evaluated finalists are silently counted as non-survivors and `evaluated` over-reports

- severity if real: MEDIUM

**Evidence:**
```
The survival loop can break mid-list — backend/app/optimizer.py:1733-1737:
```
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
                survivors = [r for r in ranked if r.get("survival", {}).get("survived")
                             and (r["survival"].get("total_return_pct") or 0) > 0]
```
`r["survival"]` is written ONLY inside that loop (optimizer.py:1722 success, 1729 `"reason": "eval_error"`, 1755 exit-grid). I read the row constructor in `_option_rerank` (optimizer.py:1162-1171) and it has no `survival` key, so every finalist past the break has no key and is dropped from `survivors` indistinguishably from a real gate failure. The summaries then claim the full list: optimizer.py:1781 `survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),` and optimizer.py:1796 `"survivors": 0, "evaluated": len(ranked), "reason_counts": reasons,`, with the un-evaluated rows attributed to a fabricated reason at optimizer.py:1792 `rs = r.get("survival", {}).get("reason", "unknown")`. The same over-count reaches the UI via optimizer.py:1818 `analyzed_candidates = f"{len(ranked)}"`, rendered verbatim at frontend/src/pages/Optimizer.jsx:1539: "Analyzing budget hit — evaluated {job.analyzed_candidates ?? \"?\"} candidate(s). Raise the budget or lower Re-rank top-K for full coverage." And the terminal verdict is emitted regardless of truncation — optimizer.py:1877-1878: `if survival.enabled and survival_summary is not None and survival_summary.get("survivors") == 0: final_status = "done_no_survivor"`. Reachability is the shipped default: `analyze_budget_sec: int = 1800` and `rerank_top_k: int = 50` (backend/app/schemas.py:219, 197), and `_analyze_should_stop` (optimizer.py:1623-1637) also returns True on a user pause/cancel, not just budget.
```

**Minimal fix:** Track the loop position and report the truth: after the loop, tag the tail `for r in ranked[i+1:]: r.setdefault("survival", {"survived": False, "reason": "not_evaluated_budget"})`, set `"evaluated": i + 1` plus `"skipped_unevaluated": len(ranked) - (i + 1)` in both survival_summary dicts (optimizer.py:1781, 1796) and in `analyzed_candidates` (optimizer.py:1818), and suppress `done_no_survivor` at optimizer.py:1877 when the stage was truncated (use a distinct status such as `done_truncated`).

### [15] CONFIRMED — MEDIUM — Stage 2 promotes a winner on option rupees with no minimum-sample guard — one paired trade is enough

- severity if real: MEDIUM

**Evidence:**
```
Promotion with survival off tests only "nonzero paired trades" — backend/app/optimizer.py:1801-1806:
```
            elif ranked and ranked[0]["paired_trade_count"] > 0:
                best = ranked[0]
                best_so_far = {
                    "value": best["option_pnl_value"],
                    "params": best["params"],
```
and the ordering is pure rupees with only a zero/nonzero tiebreak — optimizer.py:1183 (also 1016 on the premium path): `ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)`. The `min_trades` guard is applied to the SPOT count only, inside `_objective_value` — optimizer.py:149-151:
```
    if tc == 0:
        return _DISQUALIFY  # no trades at all
    if min_trades and tc < min_trades:
        return _DISQUALIFY  # statistically meaningless sample
```
where `tc = int(metrics.get("trade_count", 0) or 0)` (optimizer.py:147). A grep for `min_trades` across backend/app/optimizer.py returns only lines 141/150/451/464/1213/1356/1372/1414 — the objective and its plumbing; it is never compared against `paired_trade_count` (whose only occurrences are 1169/1183/1774/1801/1811). No other gate substitutes: the 100-trade floor lives in the off-by-default survival path (backend/app/survival.py:21-23 `MIN_TRADES_FOR_RUIN = 100`), and `assess_option_research_integrity` (backend/app/option_data_integrity.py:27-120) checks identity/provenance blockers only — it computes no sample-size or coverage blocker. So the promoted `best_so_far`, `best_value`, the saved run and the preset can rest on a couple of paired trades; the UI only exposes it passively as `{paired}/{spot}` (frontend/src/pages/Optimizer.jsx:2245) with no flag.
```

**Minimal fix:** Reuse the job's `min_trades` on the option side: at backend/app/optimizer.py:1801 require `ranked[0]["paired_trade_count"] >= min_trades` (else leave the Stage-1 best / emit a `done_no_survivor`-style "insufficient paired sample" status), and mark under-sampled rows `r["insufficient_sample"] = True` in `_option_rerank` (optimizer.py:1162-1171) so the sort key sinks them and the re-rank table can flag them.

### [17] CONFIRMED — MEDIUM — Option re-rank runs to completion after Stop/Pause — neither of its two loops reads the control flags

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:1108-1114 — `_option_rerank`'s signature accepts NO stop signal: `*, analyze_t0: Optional[float] = None, analyze_budget_sec: int = 0, progress_cb=None, trade_window_start: ..., trade_window_end: ..., min_trades: int = 0,`.

Loop 1 (spot re-run per candidate), optimizer.py:1149-1157 — no control read at all:
```
    for cand in candidates:
        merged = strategy.merged_params(cand["params"])
        enr = get_enriched(merged)
        res = await asyncio.to_thread(
            run_backtest, enr, strategy, merged,
            instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade,
            **_tw,
        )
```
Loop 2 (per-candidate option sim), optimizer.py:1268-1271 — budget ONLY:
```
        if analyze_t0 is not None and over_budget(
                elapsed=time.monotonic() - analyze_t0, budget_sec=analyze_budget_sec):
            budget_hit = True
            break
```
And `over_budget` is a no-op when the budget is 0 — backend/app/analyze_budget.py:9 `return budget_sec > 0 and elapsed >= float(budget_sec)`; the default is `analyze_budget_sec = int(payload.get("analyze_budget_sec", 1800) or 0)` (optimizer.py:1716), so `0` means unlimited.

The stop signal that does read the flags is invoked at only two sites, both AFTER `_option_rerank` returns: optimizer.py:1841 `if await _analyze_should_stop():  # O13: budget OR cancel/pause` (survival loop) and optimizer.py:1849 (exit-control grid). Grep of every control read in the file confirms none is inside `_option_rerank`: `_job_control(job_id)` at optimizer.py:1559, 1607, 1650 (three trial loops) and 1733 (inside `_analyze_should_stop`); `_is_cancelled(job_id)` at 1707, 1950, 1984. Reachability: `rerank_top_k` is validated up to 500 — backend/app/routers/research.py:566-567 `if req.evaluation_mode == "option_rerank" and not (1 <= req.rerank_top_k <= 500): raise HTTPException(400, "rerank_top_k must be 1–500")`, and the call site passes only `analyze_t0=_an_t0, analyze_budget_sec=analyze_budget_sec, progress_cb=_an_progress, trade_window_*` (optimizer.py, the `_option_rerank(...)` await inside the `evaluation_mode == "option_rerank"` block).
```

**Minimal fix:** Add `should_stop=None` to `_option_rerank`'s keyword-only params, pass `should_stop=_analyze_should_stop` from the call site, and check `if should_stop is not None and await should_stop(): budget_hit = True; break` at the top of the spot-re-run loop (optimizer.py:1149) and next to the existing budget check in the sim loop (optimizer.py:1268). Candidates not yet simulated simply do not enter `ranked`, which the caller already tolerates.

### [18] CONFIRMED — MEDIUM — A truncated survival sweep reports every finalist as "evaluated" and every unevaluated one as a failure reason

- severity if real: HIGH

**Evidence:**
```
The survival loop breaks early — backend/app/optimizer.py:1840-1842:
```
                    await _an_progress("survival", i + 1, len(ranked), _per_item_surv)
                    if await _analyze_should_stop():  # O13: budget OR cancel/pause
                        break
```
The summary still reports the FULL finalist count — optimizer.py:1888 `survival_summary = {"survivors": len(survivors), "evaluated": len(ranked),` and optimizer.py:1902 `"survivors": 0, "evaluated": len(ranked), "reason_counts": reasons,`.

The zero-survivor branch invents a reason for finalists never touched — optimizer.py:1897-1900:
```
                    reasons: Dict[str, int] = {}
                    for r in ranked:
                        rs = r.get("survival", {}).get("reason", "unknown")
                        reasons[rs] = reasons.get(rs, 0) + 1
```
A finalist that never ran has no `"survival"` key, so it contributes `"unknown"`, and that string is rendered verbatim to the user — frontend/src/pages/Optimizer.jsx:2303-2306:
```
      {summary?.reason_counts && Object.keys(summary.reason_counts).length > 0 && (
        <div className="space-y-0.5 text-[11px] text-rose-300/80">
          {Object.entries(summary.reason_counts).map(([reason, n]) => (
            <div key={reason} className="font-mono">{reason}: {n}</div>
```
under `<div className="font-semibold text-rose-200">No strategy survived your constraints.</div>` (Optimizer.jsx:2302).

No truncation signal for the cancel/pause path — `_analyze_should_stop` sets the flag ONLY in the budget branch (optimizer.py:1729-1737): `if over_budget(...): analyze_budget_hit = True; return True` … `cf, pf = await _job_control(job_id)` … `return bool(cf or pf)` (no flag). The terminal status reads cancellation only: optimizer.py:1984 `cancelled_flag = await _is_cancelled(job_id)` then `final_status = "cancelled" if cancelled_flag and completed < n_trials else "done"`. And pause is an accepted analyze-stage action — backend/app/routers/research.py:652 `if doc.get("status") not in ("running", "queued", "analyzing"):`. The UI's only truncation banner is gated on the budget flag — Optimizer.jsx:1537 `{finished && job.analyze_budget_hit && (`.

So a Pause at finalist 12 of 150 yields `evaluated: 150`, `reason_counts: {"unknown": 138, …}`, status `done`/`done_no_survivor`, and no banner.
```

**Minimal fix:** In the survival loop keep a `surv_evaluated = i + 1` counter and write `"evaluated": surv_evaluated, "finalists": len(ranked)`; build `reasons` only over `[r for r in ranked if "survival" in r]` and add `"not_evaluated": len(ranked) - surv_evaluated`. Set a `analyze_stopped_by` string (`"budget"|"cancelled"|"paused"`) inside `_analyze_should_stop` for the cancel/pause return too (optimizer.py:1733-1737) and surface it beside the existing `analyze_budget_hit` banner at Optimizer.jsx:1537.

### [20] CONFIRMED — MEDIUM — WFO analyze stage reads no control flag at all — Stop/Pause is ignored through option-OOS pairing and the full walk-forward, and the job reports "done"

- severity if real: MEDIUM

**Evidence:**
```
backend/app/wfo.py — `_job_control(job_id)` appears at exactly two call sites, both inside the per-window trial loop: wfo.py:688 `cf, pf = await _job_control(job_id)` (sequential) and wfo.py:707 (parallel). After the window loop nothing is re-read:
```
        # ---- Final analysis over completed windows ----
        await _update_job(job_id, {"status": "analyzing"})
```
(wfo.py:790-791). What follows is unpoliced — wfo.py:803-811:
```
        if final_params and not cancelled:
            merged_final = strategy.merged_params(final_params)
            df_final = get_enriched(merged_final)
            best_backtest_run_id = await _save_best_as_backtest(
                job_id, payload, strategy, df_final, final_params,
                instrument, costs, pretrade, run_walkforward=True, option_config=None,
```
and wfo.py:819-824:
```
        if payload.get("option_aware") and (payload.get("option_config") or {}) and oos_sorted:
            try:
                from app.db import get_db
                opt_cfg = payload.get("option_config") or {}
                sim = await _pair_oos_with_options(get_db(), oos_sorted, instrument, opt_cfg)
```
The terminal status uses the loop-local flag only — wfo.py:829:
```
        final_status = "cancelled" if (cancelled and len(completed_windows) < len(windows)) else "done"
```
`cancelled` is assigned only at wfo.py:690 and wfo.py:709, both inside the window loop, so a Stop that lands during `analyzing` cannot influence it. Pause is offered for that status — backend/app/routers/research.py:652 `if doc.get("status") not in ("running", "queued", "analyzing"):`. (The audit's own quote of the wfo.py `finally` guard is accurate — wfo.py:783-788 `if use_parallel: shutdown_pool()` — and that guard is what claim 22 shows the optimizer lacks.)
```

**Minimal fix:** In wfo.py add after line 791: `cf, pf = await _job_control(job_id)` and set `cancelled = cancelled or cf`; guard the two expensive tails (`if final_params and not cancelled and not pf:` at 803, and `and not (cancelled or pf)` on the `option_aware` condition at 819, recording `option_oos = {"stopped": True}`); recompute `final_status` at 829 from the refreshed flag (`"cancelled" if cancelled else "done"`), and write `status: "paused"` instead of finishing when `pf`.

### [21] CONFIRMED — MEDIUM — An all-trials-failed job persists -Infinity into best_so_far.value, and FastAPI's allow_nan=False then 500s the whole job-history endpoint

- severity if real: HIGH

**Evidence:**
```
Seed: backend/app/optimizer.py:1531 `best_so_far = {"value": -float("inf"), "params": {}, "metrics": {}, "trial_num": -1}`.

Three progress writes round it with NO finite guard — optimizer.py:1593, 1640, 1695, all identical:
```
                        "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
```
The codebase already knows this is unsafe — optimizer.py:691 (`_flush_trial_log`) and the terminal patch both guard: `"value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None,`.

Reachable on the DEFAULT sequential-bayesian path: `await asyncio.to_thread(study.optimize, objective_fn, n_trials=1, catch=(Exception,))` marks a raising trial FAIL, then optimizer.py (sequential branch) does `try: study_best_val = study.best_value ... except Exception: study_best_val = None` and `if study_best_val is not None and study_best_val > best_so_far["value"]:` — so with every trial raising, `best_so_far["value"]` stays `-inf`, and the write fires because the condition is `if completed % 5 == 0 or completed == n_trials:` (optimizer.py:1637) — the `completed == n_trials` clause fires even for a 3-trial run.

Nothing sanitizes it downstream. backend/app/db.py:27-35 `serialize_doc` passes floats through untouched (`return doc`). The list endpoint keeps the field — backend/app/routers/research.py:610-613:
```
    cur = db.optimization_jobs.find(
        {},
        {"_id": 0, "param_space": 0, "top_n_alternatives": 0, "heatmap": 0, "robustness": 0, "rerank": 0, "trial_log": 0, "wfo": 0, "wfo_windows": 0, "wfo_oos_trades": 0},
    ).sort("created_at", -1).limit(limit)
```
(no `best_so_far: 0`). The app uses the default JSONResponse — backend/server.py:57 `app = FastAPI(title="AlphaForge Trading Lab API")` — and .venv/Lib/site-packages/starlette/responses.py:186-193 renders with `allow_nan=False`.

I reproduced the full chain in this repo's .venv (fastapi 0.110.1 / starlette 0.37.2): `round(float('-inf'),4)` -> `-inf`; `jsonable_encoder({'best_so_far': {'value': -inf}})` -> `{'best_so_far': {'value': -inf}}`; `JSONResponse(...).render(...)` -> `ValueError: Out of range float values are not JSON compliant: -inf`; and `bson.BSON.encode({'v': float('-inf')})` round-trips to `-inf`, so Mongo stores it happily.
```

**Minimal fix:** Apply the guard that already exists at optimizer.py:691 to all three progress writes (optimizer.py:1593, 1640, 1695): `"value": round(best_so_far["value"], 4) if best_so_far["value"] > -1e8 else None` — best done by extracting one `_best_so_far_doc(best_so_far)` helper used by every writer. Belt-and-braces: in backend/app/db.py:27 make `serialize_doc` map non-finite floats to `None` (`if isinstance(doc, float) and not math.isfinite(doc): return None`), so no future write can 500 an entire collection endpoint.

### [22] CONFIRMED — MEDIUM — A concurrent optimizer job's fork pool is torn down by an unrelated job's finally block, failing the running job

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1646 and 1700-1701 — the teardown is unconditional inside the parallel branch, even when `start_pool` handed this job nothing:
```
            pool = start_pool(raw_df, _workers)   # None -> concurrent parallel job active -> sequential in-process
            try:
                ...
            finally:
                shutdown_pool()
```
`start_pool` returns None when another job owns the pool — backend/app/parallel_eval.py:138-140:
```
    with _POOL_LOCK:
        if _POOL is not None:
            return None  # another parallel job owns the pool -> caller falls back to sequential
```
and `shutdown_pool` has no ownership check — parallel_eval.py:150-156:
```
def shutdown_pool() -> None:
    """Tear down the active pool (no-op if none). Call in the optimizer job's finally."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
            _POOL = None
```
wfo.py guards against exactly this and its comment claims the optimizer already does — incorrectly (wfo.py:783-788):
```
            # Only tear down a pool THIS job started. shutdown_pool() acts on the
            # module-global pool, so an unconditional call from a sequential job
            # (pool=None) would kill a CONCURRENT parallel job's pool — mirrors the
            # single-run optimizer which shuts down only inside its parallel branch.
            if use_parallel:
                shutdown_pool()
```
(wfo.py:656-657 sets `pool = start_pool(df, _workers) if _workers > 1 else None` / `use_parallel = pool is not None` — the optimizer has no equivalent.) The `finally` also fires on the pause early-return, which is inside the `try`: optimizer.py:1654-1655 `if pf and await _maybe_pause(): return`. Reachability is Linux-only (`fork_available()` — parallel_eval.py:30-31 `return "fork" in multiprocessing.get_all_start_methods()`, and `effective_workers` returns 1 without fork), i.e. the Docker container, with two jobs both requesting opt_workers>1; the victim's in-flight `pool.submit(...)` futures then raise BrokenProcessPool out of `asyncio.to_thread(parallel_backtest, ...)` and the job is marked failed by the outer handler.
```

**Minimal fix:** Mirror wfo.py exactly: after `pool = start_pool(raw_df, _workers)` (optimizer.py:1646) add `use_parallel = pool is not None`, and change the `finally` at 1700-1701 to `if use_parallel: shutdown_pool()`. Harden the primitive too: `def shutdown_pool(pool=None)` in parallel_eval.py:150 that no-ops when `pool is not None and _POOL is not pool`, so no caller can close a pool it did not create.

### [23] CONFIRMED — MEDIUM — A pinned (fixed) parameter override is dropped from best_params, so the saved best runs a different value than the trials did

- severity if real: MEDIUM

**Evidence:**
```
The mechanism is real end-to-end, and I read every link.

1. A caller-supplied pin is honoured into the space — backend/app/optimizer.py:396-399:
```
        ov = overrides.get(name, {})
        if "fixed" in ov:
            info["fixed"] = ov["fixed"]
```
(same again for the injected indicator params at optimizer.py:422-423).

2. `_suggest` runs the trial with that value but never tells Optuna — backend/app/optimizer.py:434-437:
```
def _suggest(trial: optuna.Trial, space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```
and `_rebuild_study` likewise registers no distribution for it — optimizer.py:747-749: `for name, info in space.items(): / if "fixed" in info: / continue`.

3. The sequential bayesian/genetic branch takes the best from Optuna alone — backend/app/optimizer.py:1604-1612 (HEAD:1553-1561):
```
                    study_best_val = study.best_value
                    study_best_params = dict(study.best_params)
                except Exception:
                    study_best_val = None
                    study_best_params = {}
                if study_best_val is not None and study_best_val > best_so_far["value"]:
                    best_so_far = {
                        "value": study_best_val, "params": study_best_params,
```
The parallel branch repeats it at optimizer.py:1661/1668 (HEAD:1610). The GRID branch by contrast keeps the full dict — optimizer.py:1573: `best_so_far = {"value": val, "params": dict(params), "metrics": metrics, "trial_num": completed}`.

4. I grepped every `"fixed"` site in optimizer.py (lines 284,314,315,342,346,398,399,404,409,413,422,423,436,437,455,456,624,665,748,1401) — there is NO post-hoc overlay of the pins back onto `best_so_far["params"]`.

5. The omission silently reverts to the schema default downstream — backend/app/strategies/base.py:98-109:
```
    def merged_params(self, override: Dict[str, Any] | None) -> Dict[str, Any]:
        ...
        out = self.default_params()
        if override:
            for k, v in override.items():
                if k in out or k in SHARED_INDICATOR_PARAM_KEYS:
                    out[k] = v
        return out
```
and both consumers use the truncated dict: `merged = strategy.merged_params(best_params)` (optimizer.py:782, inside `_sa
```

**Minimal fix:** Overlay the pins after reading Optuna, at both sites (optimizer.py:1605 and :1661): `study_best_params = {**{k: v["fixed"] for k, v in space.items() if "fixed" in v}, **dict(study.best_params)}`. Add a test asserting that with `param_overrides={"spot_target_pts": {"fixed": X}}`, X != default, the persisted `best_params["spot_target_pts"] == X` for bayesian, grid and genetic alike.

### [24] CONFIRMED — MEDIUM — objective="profit_factor": a config with ZERO losing trades scores 0.0 — the worst possible value — so the optimizer actively steers away from the strictly-profitable configs

- severity if real: HIGH

**Evidence:**
```
Confirmed at HEAD; an UNCOMMITTED concurrent edit in the working tree is already fixing exactly this.

At HEAD (commit ad850d3), `git show HEAD:backend/app/optimizer.py` line 166 inside `_objective_value`:
```
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
```

`profit_factor` is None precisely in the no-losing-trade case — backend/app/backtest.py:282-297:
```
    losses = pnls[pnls <= 0]
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(losses.sum()) if len(losses) > 0 else 0.0
    ...
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
```
All-winners ⇒ `losses` empty ⇒ `gross_loss == 0.0` ⇒ not `< 0` ⇒ `None` ⇒ scored 0.0. Since PF is a ratio of non-negative magnitudes every real PF is `>= 0.0`, so the flawless config sits at or below the floor of the ranking, beneath any money-losing PF of 0.5.

The premium branch in the SAME file already does it correctly — optimizer.py:531-536:
```
    if gross_loss > 0:
        profit_factor = round(gross_win / gross_loss, 3)
    elif gross_win > 0:
        profit_factor = 999.0  # only wins: large-but-finite (inf breaks JSON/ranking)
    else:
        profit_factor = None   # no wins and no losses -> undefined (scored 0.0)
```

It is a user-selectable objective — frontend/src/pages/Optimizer.jsx:72: `{ id: "profit_factor", name: "Maximize Profit Factor", desc: "Gross profit / |gross loss|" },`.

CAVEAT / current state: the on-disk working tree (uncommitted, ` M backend/app/optimizer.py`) now reads at optimizer.py:164-176:
```
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        if v is not None:
            return float(v)
        ...
        return 999.0 if int(metrics.get("wins", 0) or 0) > 0 else 0.0
```
That is the correct fix and it matches the premium sentinel, but it is NOT committed — so the finding is CONFIRMED against the repo's committed state and is mid-remediation by a concurrent session.
```

**Minimal fix:** Already being applied in the working tree: `return 999.0 if int(metrics.get("wins", 0) or 0) > 0 else 0.0` when `profit_factor is None`, mirroring optimizer.py:534. Commit it and add a test that a metrics dict with `profit_factor=None, wins=12, losses=0` outscores one with `profit_factor=0.5`.

### [25] CONFIRMED — MEDIUM — _robustness_score counts no-op perturbations as passes: integer rounding and bound clamping make ±10/20% shifts re-evaluate the identical config, inflating the ROBUST verdict

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:632-645 (HEAD:581-594) — no guard that the resolved value differs from the base:
```
        base_v = float(best_params[name])
        for pct in (-0.20, -0.10, 0.10, 0.20):
            t_v = base_v * (1 + pct)
            t_v = max(float(info["min"]), min(float(info["max"]), t_v))
            if info["type"] == "int":
                t_v = int(round(t_v))
            test_params = dict(best_params)
            test_params[name] = t_v
            metrics, _ = evaluate_fn(test_params)
            val = obj_fn(metrics)
            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5
            n_total += 1
            if ok:
                n_ok += 1
```
`n_total` is incremented unconditionally and `score = round((n_ok / n_total * 100) ...)` (optimizer.py:647).

I reproduced the numerics in this repo's Python against the REAL declared bounds. backend/app/strategies/builtin/confluence_scalper.py:20-21:
```
        "signal_threshold": {"type": "int", "min": 40, "max": 95, "default": 62},
        "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
```
Results: cooldown_bars base 1 -> [1, 1, 1, 1] (4/4 identical to base); base 2 -> [2, 2, 2, 2] (4/4); base 3 -> [2, 3, 3, 4] (2/4); base 5 -> [4, 4, 6, 6] (0/4). Clamping on a bound: signal_threshold base 95 -> [76, 86, 95, 95] (2/4 identical); float `spot_target_pts` (min 5, max 200) base 200 -> [160.0, 180.0, 200.0, 200.0] (2/4 identical).

A no-op perturbation re-runs the base config, so `val == base_val`, and for any `base_val >= 0` the test `val >= base_val * 0.85` passes by construction — free credit. It is published as a verdict — frontend/src/pages/Optimizer.jsx:1988: `const label = score >= 70 ? "ROBUST" : score >= 50 ? "MODERATE" : "FRAGILE";` and Jsx:1996 describes it as "% of ±10/20% param perturbations that stayed within 85% of best objective". The perturbation table also renders those rows as genuine evidence (`"param": name, "shift_pct": int(pct * 100), "value": t_v, ... "ok": bool(ok)` — optimizer.py:646-651).

One correction to the auditor: the free pass only applies when `base_val >= 0`. When the winning objective is negative the same no-op FAILS (see finding 26) — so the bias is inflation for positive objectives and deflation for negative ones, not universal inflation.
```

**Minimal fix:** After clamping/rounding at optimizer.py:637, `if t_v == base_v: continue` so the no-op is neither run nor counted; for `int` params use an absolute ±1/±2 step within bounds instead of a multiplicative shift. Record the skipped dimensions in the returned payload so the UI can say "n params could not be perturbed" instead of silently crediting them.

### [26] CONFIRMED — MEDIUM — _robustness_score's pass test inverts when the best objective is negative: `val >= base_val * 0.85` demands a 15% IMPROVEMENT, so neg_max_dd runs are labelled FRAGILE by construction

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:642 (HEAD:587), verbatim:
```
            ok = val >= base_val * 0.85 and metrics.get("trade_count", 0) >= 5
```
The intent is documented as a slack band — optimizer.py:561: `"""Perturb each numeric param by ±10% and ±20%; count fraction that stay 'profitable'.` and frontend/src/pages/Optimizer.jsx:1996: "% of ±10/20% param perturbations that stayed within 85% of best objective". Multiplying by 0.85 only loosens the bar for positive values. I checked the arithmetic: base 2.0 -> threshold 1.7 (looser); base -120 -> threshold -102.0 (TIGHTER — the perturbation must beat the base by 15%); base -0.4 -> threshold -0.34.

`neg_max_dd` is `<= 0` by construction — optimizer.py:186-187 (HEAD:176-177):
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
```
and it is user-selectable — frontend/src/pages/Optimizer.jsx:75: `{ id: "neg_max_dd", name: "Minimize Max Drawdown", desc: "Stable equity curve" },`. The same inversion hits any run whose winning `sharpe` or `risk_adjusted` is negative.

The path is live: `obj` is the guard-aware objective closure (optimizer.py:1458-1462 `def obj(metrics): return _objective_value(metrics, objective, lot_size=lot_size, min_trades=min_trades, ...)`) and is passed straight in — optimizer.py:1946: `robustness = await asyncio.to_thread(_robustness_score, evaluate, obj, best_so_far["params"], space)`. Since the best trial is by definition the minimum-drawdown config, essentially every perturbation fails, score collapses toward 0, and Optimizer.jsx:1988 paints "FRAGILE" on the most stable configuration the search found.

Additional hazard I verified but the auditor did not mention: `_DISQUALIFY` is a large negative float imported from rerank_select (optimizer.py:57), so a disqualified perturbation gets `val * 0.85` treatment too — the sign bug and the sentinel interact.
```

**Minimal fix:** Replace the signed multiplication at optimizer.py:642 with a magnitude-based band: `tol = abs(base_val) * 0.15; ok = val >= base_val - tol and metrics.get("trade_count", 0) >= 5`. Explicitly short-circuit the degenerate cases (`base_val == 0`, and `val <= _DISQUALIFY` -> always fail) rather than letting the arithmetic decide.

### [28] CONFIRMED — Parallel trial path attaches the PREVIOUS best's metrics to the NEW best params whenever the search space contains a pinned dimension, because study.best_params omits fixed params

- severity if real: HIGH

**Evidence:**
```
backend/app/optimizer.py:1679-1683 (parallel ask/tell branch, current working tree):
```
                    if study_best_val is not None and study_best_val > best_so_far["value"]:
                        best_metrics = next((t["metrics"] for t in reversed(trial_history)
                                             if t["params"] == study_best_params), best_so_far["metrics"])
                        best_so_far = {"value": study_best_val, "params": study_best_params,
                                       "metrics": best_metrics, "trial_num": completed - 1}
```
`trial_history` entries carry the FULL dict returned by `_suggest`, which injects pinned dims without calling `trial.suggest_*` — backend/app/optimizer.py:382-387:
```
def _suggest(trial: optuna.Trial, space: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = {}
    for name, info in space.items():
        if "fixed" in info:
            out[name] = info["fixed"]
            continue
```
I reproduced the mismatch against the installed optuna using the project's own `_suggest`: with space `{a:int[1,10], b:fixed 0.3}`, `study.best_params` = `{'a': 5}` while history params are `{'a': 5, 'b': 0.3}`, so `next(...)` returned the STALE fallback; with no pinned dim the same loop matched correctly. Pinning is produced by backend/app/optimizer.py:359-362 `elif t in ("int", "float") and not ("min" in info and "max" in info): ... info["fixed"] = info.get("default")`, and the AI compiler always emits a bare cooldown_bars — backend/app/ai/compiler.py:464 `f'        "cooldown_bars": {{"type": "int", "default": {spec.cooldown_bars!r}}},\n'`. The stale dict is what the job reports: backend/app/optimizer.py `"best_metrics": best_so_far["metrics"]` in the finished payload, and it is only overwritten inside the `evaluation_mode == "option_rerank"` branches, so a spot-only job ships it verbatim.
SCOPE CORRECTION to the original finding: the parallel branch is unreachable for premium-native strategies — backend/app/optimizer.py:1532-1533 `_workers = (effective_workers(opt_workers) if method == "bayesian" and not is_premium_trigger_strategy(strategy) else 1)`. I built the real param space for every shipped strategy: all ten ORDINARY plugins plus ConfluenceScalper have ZERO pinned dims, and only the two premium plugins (which are pinned sequential) have any. So the bug fires only on (a) an AI-authored ordinary strategy
```

**Minimal fix:** Capture the winning metrics at tell time inside the existing `for trial, params, (metrics, _m) in zip(...)` loop (as wfo.py already does) instead of re-deriving them from `study.best_params`, and merge the pinned dims back into `best_so_far["params"]` so the reported params match what the trial actually ran. A one-line stopgap: compare only on the searched subset, `if {k: t["params"][k] for k in study_best_params if k in t["params"]} == study_best_params`.

### [29] CONFIRMED — risk_adjusted/neg_max_dd mix units: max_dd_pts is index POINTS on the ordinary path and RUPEES on the premium path, and max(1.0, dd/100) zeroes the drawdown penalty for any ordinary run under 100 points

- severity if real: MEDIUM

**Evidence:**
```
backend/app/optimizer.py:186-191 (verbatim, current tree):
```
    if objective == "neg_max_dd":
        return -abs(float(metrics.get("max_dd_pts", 0) or 0))
    # risk_adjusted (default)
    sharpe = float(metrics.get("sharpe") or 0)
    dd = abs(float(metrics.get("max_dd_pts") or 1))
    return sharpe / max(1.0, dd / 100.0)
```
Ordinary path units are index points — backend/app/backtest.py:281,287-290,300:
```
    pnls = np.array([t.pnl_pts for t in trades])
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
        "max_dd_pts": round(max_dd, 2),
```
So for |max_dd| <= 100 points the divisor is exactly 1.0 and `risk_adjusted` is arithmetically identical to `sharpe` — the drawdown term contributes nothing, while frontend/src/pages/Optimizer.jsx:69 advertises `desc: "Sharpe / drawdown — balanced quality"` and backend/app/optimizer.py:1287 makes it the default (`objective = payload.get("objective", "risk_adjusted")`). The clamp is undocumented anywhere.
The premium substitution is also real — backend/app/optimizer.py:602-605:
```
        # NOT a true unit match: rupee max-drawdown substituted where the spot
        # formula expects index points (a rupee-native premium strategy has no
        # index-points drawdown concept) — an honest, documented proxy.
        "max_dd_pts": abs(float(port.get("max_drawdown_value", 0.0) or 0.0)),
```
PARTIAL PUSHBACK: that half is a deliberate, in-code-documented proxy, and both jobs are labelled only by `obj={job.objective}` (frontend/src/pages/Optimizer.jsx:1457) with no unit tag — so it is a cross-job comparability wart, not a hidden bug. The actionable defect is the `max(1.0, ...)` clamp, which silently turns the default objective into plain Sharpe.
```

**Minimal fix:** Drop the clamp: `return sharpe / max(1e-6, dd / scale)` so drawdown always contributes, and make the denominator scale-free (Calmar-style `total_pnl / |max_dd|`) rather than a hardcoded /100 points. Separately emit an `objective_units` field ("points" vs "rupees") on the job so best_value is never compared across the spot and premium paths.

### [30] CONFIRMED — Early stop is invisible: the ceiling n_trials is reported as the trial count and stamped into the saved run's overfit evidence

- severity if real: MEDIUM

**Evidence:**
```
The backend does record the distinction — backend/app/optimizer.py:1708-1710 verbatim:
```
        await _update_job(job_id, {"status": "analyzing", "n_trials_completed": completed,
                                   "early_stopped": early_stopped, "stopped_at_trial": completed,
                                   "trials_ceiling": n_trials})
```
`grep -rn "early_stopped|trials_ceiling|stopped_at_trial" frontend/src/` returns ZERO hits — I ran it. The UI renders only frontend/src/pages/Optimizer.jsx:1504 `{job.n_trials_completed || 0} / {job.n_trials_total || 0} trials` with :1425 `const pct = job.n_trials_total ? Math.round((job.n_trials_completed / job.n_trials_total) * 100) : 0;`, and the only explanatory lines nearby cover failed/cancelled/paused/interrupted (:1520-1531) — nothing for early_stopped.
The CEILING, not the actual count, reaches both artifacts: backend/app/optimizer.py:1971-1977 `best_backtest_run_id = await _save_best_as_backtest(... n_trials=n_trials, ...)` which stores it at :855 `**({"n_trials": int(n_trials)} if n_trials else {})`, and the trust evidence at :2061 `"n_trials": n_trials, "spot_option_correlation": spot_option_corr})`. backend/app/deployment_quality.py:328 turns that into the literal narrative `f"This result was the best of {n_trials} optimizer trials over {trade_count} trades. "` and :316 `dsr = deflated_sharpe(sharpe_val, n_trials, trade_count)`.
The trigger is real and default-ON: backend/app/optimizer.py:1289 `early_stop = bool(payload.get("early_stop", True))`, and backend/app/early_stop.py:42-43 gives `eff_warmup = min(200, max(30, 150//3)) = 50`, `eff_patience = min(200, max(20, 150//5)) = 30` for the 150-trial default.
```

**Minimal fix:** Pass `completed` (not `n_trials`) to `_save_best_as_backtest` and into the `evidence` dict, carrying the ceiling separately as `trials_ceiling`, so the deflated-Sharpe narrative quotes the number of draws that actually happened. In Optimizer.jsx, when `job.early_stopped` render a line naming `stopped_at_trial`/`trials_ceiling` and compute `pct` from `stopped_at_trial` so a converged run does not read as 33% complete.

### [31] CONFIRMED — objective="net_pnl_inr" ignores option_config.lots and converts SPOT index points at the option lot size, so the "Net P&L (₹)" it maximises is not the rupee P&L of the run being validated

- severity if real: LOW

**Evidence:**
```
backend/app/optimizer.py:179-183 verbatim:
```
    if objective == "net_pnl_inr":
        # Net rupee P&L = net points (already cost-adjusted when costs_enabled)
        # × lot size. This is an honest index-point→rupee conversion; it does
        # not model option premium decay (see option-aware mode, future slice).
        return float(metrics.get("total_pnl_pts", 0) or 0) * float(lot_size)
```
The scoring closure is built with `lot_size` only — backend/app/optimizer.py:1461-1465:
```
        def obj(metrics: Dict[str, Any]) -> float:
            return _objective_value(
                metrics, objective, lot_size=lot_size,
                min_trades=min_trades, min_direction_share=min_direction_share,
            )
```
and `lot_size` is the contract lot (`_DEFAULT_LOT_SIZE = {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 20}` at :122, overridden from the contracts collection at :1376-1384). The simulated size lives in a different dict that never reaches the objective: `lots = int(option_cfg.get("lots") or 1)` at :1124 inside `_option_rerank` and at :966 inside `_survival_eval_oos`. So Stage 1 ranks on one lot of index points while Stage 2 and the survival gate simulate `lots` lots.
PUSHBACK on one clause of the finding: `option_cfg["lots"]` is a run-level constant, not a search dimension (and `lots` is now pinned by NON_ALPHA_PARAM_NAMES), so multiplying every trial's score by it is a monotonic rescale — trial ORDERING is unaffected by the omission. What is wrong is the reported MAGNITUDE of the headline ₹ (frontend/src/pages/Optimizer.jsx:1589 `{fmtBest(job.best_value ?? bsf.value)}`), which is off by exactly the `lots` factor and only when the user sets lots > 1 (the default is 1, where there is no discrepancy at all). The spot-proxy nature is stated in the code comment above and in the UI's own description (Optimizer.jsx:70 `desc: "Net rupee P&L = net points × lot size (enable costs)"`), which literally describes the formula implemented.
```

**Minimal fix:** Pass the effective multiplier into the closure — `lot_size=lot_size * max(1, int((option_cfg or {}).get("lots") or 1))` at optimizer.py:1463 — so the ₹ figure matches the size actually simulated, and rename the UI option to "Net P&L (₹, spot-equivalent)" so it is not read as the paired-option net.
