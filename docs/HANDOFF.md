# Handoff

Updated: 2026-06-12 (quality-hardening Slice B — research analytics)

This is the entry point for the next AI agent or developer. Read it before editing code. The repository and tests are the source of truth — not any prior chat.

## Recent Work — Auto-subscribe ATM±3 option universe in market hours (2026-06-12)

See CHANGELOG 0.22.x. Follow-up to Slice 5 item 4: the option-chain snapshot needed live ticks, which previously only flowed after a deploy/resume restarted the option stream. Now the market-hours evaluator loop calls `_auto_follow_option_stream(min_radius=OPTION_CHAIN_BASELINE_RADIUS=3)` each minute, so the universe stays subscribed automatically whenever Upstox is connected — no manual restart, no deployment required. `_auto_follow_option_stream` is now idempotent (skips the WS restart when coverage already includes the desired keys; re-centers when the ATM band drifts). 458 tests pass.

## Recent Work — Forward Surfaces Overhaul, Slice 5: Polish (2026-06-12)

See CHANGELOG 0.21.x. The final slice — four small, separately-committed polish items closing out `.kiro/specs/forward-surfaces-overhaul/` (the whole spec is now DONE):
1. **P&L calendar heat-grid** on `/paper` (`paper-pnl-calendar`) — per-day realized-₹ GitHub-style grid, client-side from the closed trades already fetched, capped 16 weeks, collapsible.
2. **Data-realism preflight line** in deploy wizard step 1 (`preflight-summary`) on `GET /api/deployments/preflight` for the preset's instrument — coverage / expiries / contracts / token / structural breaks, informational. Restores the surface dropped in Slice 2.
3. **Drift re-pin** — `POST /api/deployments/{id}/repin-source` (pure helper `build_repin_update` in `app/strategy_source_hash.py`, 4 unit tests: recompute SHA, clear `drift_*`, append `repin_history`, resume only if drift-paused) + a "Re-pin & resume" button on the card's pause banner + `api.repinDeploymentSource`.
4. **ATM±3 option-chain snapshot** on `/live` (`option-chain-panel`) — strike band from the option-universe route, CE/PE LTPs from `/upstox/stream/ticks/latest`, ATM highlighted, 30s refresh. No new backend route.

**457 backend tests pass; frontend builds clean.** The forward-surfaces-overhaul spec is fully delivered (Slices 1–5). The next agent should pick the next item from `plan.md` / the optimizer-enhancements spec, or take direction from the user.

## Recent Work — Forward Surfaces Overhaul, Slice 4: Paper Trading Journal (2026-06-12)

See CHANGELOG 0.20.x. `frontend/src/pages/PaperTrading.jsx` (route `/paper`) was rebuilt from the old mark/close table into the **Paper Trading Journal** described in `forward-surfaces-overhaul` R4. Pure UI over the already-shipped upgraded `GET /api/paper/trades` (verified field-by-field against `server.py`):
- Columns: deployment/strategy, contract (`trading_symbol`), CE/PE, lots × lot size, entry time+price, exit time+price, exit reason, holding time, P&L ₹ + % of entry premium, status. **Day-wise grouping** with per-day subtotals; **summary strip** (today realized, open MTM, open count, win rate, profit factor) + a cumulative-realized **equity sparkline** (computed client-side over the filtered set, capped 500).
- Server-side filter (deployment via `?deployment=`, instrument, status, IST date range), whitelisted sort, skip/limit pagination + total, CSV (`format=csv`), 30s auto-refresh. A second lightweight fetch (no status filter, no pagination) drives the summary/sparkline so they reflect the whole filtered set, not just the page.
- **Close flows** replace the manual type-a-price requirement: one-click "Close @ market" (uses `last_price`; prompt fallback only when null), confirmed "Close all open" (skips trades with no mark), and a small manual-premium field as an off-hours fallback. All closes post option PREMIUM, never spot.
- **Purge** (CLOSED only) via `POST /api/paper/trades/purge`: row-select / older-than-N / per-deployment, all confirm-gated. OPEN trades are never selectable/deletable.
- Contract test updated in the same commit (`test_signal_paper_lifecycle.py::test_frontend_exposes_live_and_paper_operational_views`) — old testids preserved, new surface pinned. No backend changes. **Slice 5 (polish) is the only remaining slice** in `.kiro/specs/forward-surfaces-overhaul/`.

## Recent Work — Save Backtest Setup as Preset + Rename (2026-06-12)

See CHANGELOG 0.19.x. Two small user-requested preset features:
- **Backtest Lab "Save setup as preset"** (`backtest-save-preset`): captures strategy + current params + the Option Execution/exit policy into a preset's `config.execution` (same shape as `preset_execution.execution_from_option_config`), so a hand-tuned Lab setup is deployable as-is (the deploy wizard already prefills from `execution`). Closes the long-noted gap that backtest exit config didn't travel into deployments — now it does when you save the setup as a preset.
- **Preset rename**: `POST /api/presets/{name}/rename?new_name=` (config-preserving, 409 on collision) + `api.renamePreset` + a pencil button in the Optimizer Saved Presets panel.
- No new collections; `savePreset` was already in api.js (previously unused by any page). 453 tests pass, frontend clean, both containers rebuilt.

## NEXT AGENT — START HERE (Opus 4.8 in Kiro)

Your work is fully spec'd: **`.kiro/specs/forward-surfaces-overhaul/`**
(requirements → design → tasks). **All five slices are now DONE** (Signals
ledger `/journal`, Paper trading journal `/paper`, and the Slice-5 polish:
P&L calendar, deploy-wizard preflight line, drift re-pin, ATM±3 chain). The
spec is fully delivered — pick the next item from `plan.md` or the
optimizer-enhancements spec, or take direction from the user. `design.md`
carries the endpoint contracts, frontend conventions, testing rules, and the
trading-domain rules that are non-negotiable
(premium-never-spot, lot size from contract metadata, OPEN trades never
deletable, IST everywhere, never push without the user's approval). The backend
for those slices is already built and tested — the slices are UI + contract-test
work. Anything trading-critical beyond the spec (evaluator, optimizer, WFO,
paper_auto) goes back to the senior agent.

## Recent Work — Warehouse Truth: ATM-Band Completeness (2026-06-12, night)

See CHANGELOG 0.23.x. The root cause of "verified warehouse, failing backtests":
hygiene's option check was per-day/per-expiry presence while consumers need the
day's full strike BAND. Implementation notes for the next agent:

- `app/completeness.py` is now the single completeness definition (band math
  uses `options_universe.round_to_step` on BOTH bounds — nearest rounding, not
  floor/ceil, deliberately matching the per-minute ATM fetch path; a floor/ceil
  band would demand strikes the fetcher never selects = permanently missing).
- `data_hygiene.compute_hygiene_plan`: `_spot_day_rows` (one candles_1m agg →
  date/count/low/high) + `_option_pairs_by_day` (one options_1m agg → exact
  stored pairs) + `band_completeness`. Option action fires whenever
  `missing_pairs > 0`; `_hygiene_submit_option_candles` was already exact and
  unchanged. Default scope start = `default_scope_start()` = rolling 9 months
  (floor 2024-11-27). Hygiene DEFAULT_MONEYNESS = atm+otm1+itm1 (band pad).
- Audit corrections vs the earlier review notes: the optimizer enriched cache
  was ALREADY capped (now 64→16); `select_contract_for_signal` has NO nearest-
  strike fallback (exact match or None) — pinned by a regression test in
  test_options_universe.py. The "#109 wrong strike" report was a journal
  misread; real defect was missing band fetches only.
- First real audit: ~83-84% band coverage, 1,701 missing strike-days across the
  3 indices (9-month window); backfill ran via normal hygiene execute.
- **Root cause #2 (found verifying the backfill):** endpoint routing for
  option-candle fetches keyed off `contract.source` provenance, so contracts
  synced while ACTIVE were still sent to the normal V3 endpoint after expiry →
  `UDAPI100011` → permanent holes for every once-synced-live weekly.
  `upstox_client._is_expired_instrument_key` now routes by `expiry_date <
  today(IST)`, and `_expired_endpoint_key` synthesizes the
  `SEGMENT|TOKEN|DD-MM-YYYY` expired-endpoint key for 2-part keys (stored
  candles keep the original key). Plus a 3-step 429 backoff in
  `_authenticated_get`. Tests: `tests/test_upstox_expired_routing.py` (stubs
  motor — upstox_client transitively imports app.db).
- **Root cause #3: split candle keys.** Expired-backfill contracts carry dated
  3-part keys; current-sync stores 2-part for the same identity. Candles are
  now ALWAYS stored canonical (`instruments.canonical_instrument_key`) and all
  candle lookups canonicalize (pairing group+lookup, backtest-run loader,
  preflight, preview counters, re-rank/WFO queries use both forms). One-time
  migration done: 6,937 keys, 6.15M docs, 115k duplicate minutes removed. If
  you add ANY new options_1m query keyed by instrument_key, canonicalize it.
- **Acceptance:** confluence-10 re-run = 124/124 paired, 0 missing. Band
  residual (~7–9%) is broker-unavailable band-edge strikes — honest, visible.
  **[CORRECTED 2026-06-12, CHANGELOG 0.25.x]** This was WRONG: most of that
  ~7–9% was fetchable data the hygiene fetch never requested (the fetch used a
  per-day ATM±moneyness selection that did not cover the padded spot-range
  band). After driving the fetch from `completeness.missing_band_pairs`, real
  coverage is ~99% and the true residual is only the genuinely broker-empty
  strikes (~1%).
- Frontend DataHygienePanel still displays the OLD summary fields — surfacing
  `missing_pairs`/`coverage_pct`/`missing_by_month` is Kiro quality-hardening
  Slice A (`.kiro/specs/quality-hardening/`).

## Status In One Line

Latest (2026-06-13, night): **Data Warehouse overhaul (W1–W4, CHANGELOG 0.29.x)** — band-exact nightly catch-up + broker-empty ledger with latest-session grace (`option_known_empty`; hygiene reaches VERIFIED honestly), `POST /api/warehouse/sync`, instant status hero from the persisted plan (`/api/data-hygiene/latest`), band-truth option heatmap (`per_day`), Advanced-tools collapse + typed-confirmation danger zone, page split into `components/warehouse/*` (tests: `contract_corpus.warehouse_page_text()`). NOTE: Friday 2026-06-12's option bars were not yet published by Upstox at review time — the panel shows amber with ~76 actionable pairs until the next sync/auto-update lands them; that is correct behavior, not a bug.

Latest (2026-06-13, later): **Slice C landed (senior agent)** — server.py is a 203-line app factory; routes/models/helpers moved byte-for-byte to `app/routers/*`, `app/schemas.py`, `app/runtime.py` (CHANGELOG 0.28.x; OpenAPI byte-identical + 111-probe route-match proof). Contract tests now assert on `tests/contract_corpus.backend_api_text()` — when you pin a route string, it can live in any router file. The quality-hardening spec is FULLY DELIVERED; next agent takes direction from the user.

Earlier (2026-06-13): **execution-policy extraction** — `app/execution_policy.py` is the single source of exit semantics (premium levels, spot-mirror levels, tick decisions as degenerate bars through `intrabar_exit`); sim↔live parity is now a TESTED invariant (`tests/test_execution_policy.py`, 11 golden tests), and a real divergence was fixed (live deciders were target-first; now pessimistic stop-first like the sim). 503 tests pass. Kiro Slice C (server split) UNLOCKED in `.kiro/specs/quality-hardening/spec.md`. Earlier: quality-hardening Slices A+B verified (band coverage ~99% after Kiro's correct fetch-from-band fix); warehouse-truth fixes (CHANGELOG 0.23.x).

Latest (2026-06-12): **quality-hardening Slice B delivered** — five client-side research-analytics surfaces (CHANGELOG 0.26.x), each a separate commit and each browser-verified on the live stack: MAE/MFE distribution card + Monte Carlo card (bootstrap, P(net<0)) in the Backtest Lab results, a two-run comparison view (params diff / metric table / overlaid equity) in the Backtest Run Journal, a read-only volatility-audit panel on the Data Warehouse page (on the existing `POST /api/volatility/audit`), and `risk_hints` in the Signals Ledger detail row. Frontend-only, no backend changes; new contract test `tests/test_quality_hardening_slice_b.py` pins all five. **492 pytest tests pass (5 new); frontend builds clean (no new eslint warnings); frontend container rebuilt per item.** Slice C (server.py split) is GATED — do not start until the senior agent's execution-policy extraction lands. Preceded by the hygiene band-fetch fix (below).

## Prior Status In One Line — "Fill gaps" used to leave the panel permanently "degraded" (added 0 candles) because the fetch re-derived a per-day ATM±moneyness selection that didn't cover the padded spot-range band; intraday-wick/band-edge strikes were demanded forever but never fetched (the broker HAD the data — the 0.23.x "honest residual" claim was wrong for this class). Fetch is now driven by the same band via `data_hygiene.build_band_fetch_plan` → `completeness.missing_band_pairs`. Verified live: one run added 68k/99k/115k candles; band coverage 92.9%→99.24% (NIFTY), 91.4%→98.9% (BANKNIFTY), 91.5%→99.0% (SENSEX); residual now equals genuinely broker-empty strikes (amber "warning"). **487 pytest tests pass; both containers rebuilt and healthy.** Preceded by quality-hardening Slice A (warehouse truth in the UI + retention — see below).

## Status (Slice A)

## Recent Work — Forward Surfaces Overhaul, Slice 3: Signals Ledger (2026-06-12)

See CHANGELOG 0.18.x. `frontend/src/pages/SignalJournal.jsx` (route `/journal`) was rebuilt from the old deployment-signal audit table into the **Signals Ledger** described in `forward-surfaces-overhaul` R3. It is pure UI over the already-shipped `GET /api/signals/enriched` (verified field-by-field against `server.py`): server-side filters (deployment via `?deployment=` URL param, instrument, state, clean/blocked, IST date range), whitelisted server-side sort (time/instrument/score/state via the `confidence` field for score), skip/limit pagination with total, CSV via `window.open` on the endpoint with `format=csv`, 45s auto-refresh, an expandable per-row detail showing entry-trigger `reasons`, and the deletion toolkit on `POST /api/signals/purge` (row-select ids / older-than-N-days / per-deployment, all `window.confirm`-gated). Two contract tests updated in the same commit. No backend changes. Slice 4 (rebuild `PaperTrading.jsx` on the upgraded `/paper/trades`) is next.


## Recent Work — Forward Surfaces Overhaul, Slices 1–2 (2026-06-12)

See CHANGELOG 0.17.x for the feature list. Implementation notes:

- `deployment_evaluator.evaluate_active_deployments`: `_apply_concurrency_rule` deleted; the auto-paper hook still re-reads signal state before trading (guards concurrent mutations). `concurrency_lower_score` no longer occurs on new signals (old journaled ones keep it).
- Mode model: `strategy_deployments.ALLOWED_MODES = {signal_only, paper}` with `LEGACY_MODE_MAP` (shadow/recommendation → signal_only) applied at create; stored legacy docs untouched — anything ≠ `paper` is treated as signal-only everywhere. `manual_approval_required` now stamped False.
- Retired from `server.py`: POST /signals, /signals/{id}/transition|approve|skip|mark-blocked|paper + SignalCreateReq/SignalTransitionReq/SignalApprovalReq/PaperDeployReq + api.js methods. Contract tests now assert these STAY gone (`test_signal_paper_lifecycle.py`).
- New routes live next to their kin: `/signals/enriched` + `/signals/purge` before the deployments list; `/deployments/overview` before `/deployments/{id}` (route order matters); trades purge after the upgraded list. `_ist_day_bounds_ms_full` + `_csv_response` are shared helpers.
- Option-stream auto-follow: `live_option_universe.radius_for_deployments` (pure, tested) + `server._auto_follow_option_stream()` called on deployment create/resume — best-effort, returns `{restarted, reason|radius}` in the response under `option_stream`, never raises.
- `/live` page: full rewrite (`LiveSignals.jsx`) — overview-driven cards, wizard (preset → execution → risk) with readiness + quality ack inside, undeploy with optional purge (double confirm). Deep-link `/live?preset=NAME` opens the wizard preselected. PreflightBadge UI was dropped from the wizard (route still exists) — restoring it is Slice 5 item 2.

## Recent Work — Pipeline Alignment (2026-06-12, second pass)

See CHANGELOG 0.16.x. Key implementation notes for the next agent:

- `app/preset_execution.py execution_from_option_config` derives `preset.config.execution` in `apply_opt_as_preset` (works for single re-rank and option-aware WFO jobs; spot-only jobs store no block). Backtest Lab `applyPreset` maps it back onto the option form; LiveSignals prefills option policy + auto-paper fallbacks from it (`prefillAppliedRef` guards against the 15s preset refresh clobbering edits).
- `GET /api/deployments/readiness` (registered before `/deployments/{id}`) returns `{source, wfo, option_evidence}`; evidence matching prefers exact `best_params == preset.params`. UI: `ReadinessBadge` in `LiveSignals.jsx` between Preflight and Quality badges. `/live?preset=NAME` deep-link preselects the source (Rocket button per preset row in the Optimizer).
- WFO v2: `WfoStartReq.option_aware/option_config` → `wfo.py _pair_oos_with_options` (re-rank-style windowed contract/candle loading over the stitched OOS trades, simulated once) → `option_oos_summary` (pure; per-window bucketing by `signal_entry_ts` IST date against window test ranges). Wrapped in try/except — pairing failure writes `option_oos.error`, never fails the job. Resume-safe (runs in finalize, after windows complete).
- Optimizer.jsx: one shared `buildOptionConfig()` feeds both the re-rank payload and the WFO payload; DTE chips state is an int array (legacy localStorage/job tokens coerced via `parseDteFilter` in `loadSetup`/clone).

## Recent Work — Backtest Lab / Optimizer Alignment Fixes (2026-06-12)

From the user's systematic review of the Backtest Lab and Optimizer (course-correction toward the core objective). See CHANGELOG 0.15.x for the full list. Key points for the next agent:

- `app/dte.py normalize_dte_filter` now returns an Optional **frozenset** (was Optional[int]) and accepts lists — all comparisons changed from `== target` to `in target` (server run + preflight paths, optimizer re-rank). `OptionBacktestReq.dte_filter` takes a token or a list; the Backtest Lab UI is a chip multi-select (empty = all).
- `OptionBacktestReq.moneyness` and the Backtest Lab UI default to **ATM** (was OTM1 — contradicted the ATM-only warehouse auto-maintenance scope).
- `compute_auto_risk_levels` precedence per leg: strategy hint pct → deployment `auto_paper_*_pts` (₹ of premium, NEW) → deployment `auto_paper_*_pct`. `DeploymentCreateReq` gained the pts fields; the Live Signals form has a ₹-points/percent toggle (UI-only `auto_paper_unit` key is stripped from the payload).
- Optimizer option re-rank UI gained the Points/Percent toggle; the backend `_option_rerank` already read `option_target_pts`/`option_stop_pts`.
- The Backtest Lab's old IS/OOS check is labeled "Walk-forward split check (same params, IS vs OOS)" everywhere to stop name-collision with the optimizer's honest WFO.
- Unit audit across the money chain (pnl pts→₹, slippage/spread per side, sizing budgets, `net_pnl_inr`, WFO efficiency, marker tick routing, paper P&L quantity math): **no mismatches found** — documented in CHANGELOG.
- Remaining known gap (deliberately NOT fixed yet, pending the user's alignment discussion): backtest exit config does not travel with presets into deployments; the deployment fallback fields are the manual bridge. The optimizer's DTE sub-panel select is still single-token (backend already accepts lists).

## Recent Work — Auto Paper Trading on Signals + Low-Sample Metrics (2026-06-11)

User requirement: a deployment generating live signals should also paper-trade each confirmed signal automatically (default 1 lot) so the signal's outcome is auditable; and the 10-complete-session gate should not hide forward evidence.

- `backend/app/paper_auto.py` — the new module. `resolve_option_entry_price` (live WS tick → options_1m candle ≤5 min old → None; NEVER spot), `compute_auto_risk_levels` (strategy risk hints win over deployment `auto_paper_target_pct`/`auto_paper_stop_pct`; LONG-premium semantics; stop floors at 0.05), `auto_paper_trade_for_signal` (creates the trade, advances the signal CONFIRMED→TRIGGERED→ACTIVE, links `paper_trade_id`; refusal journals `paper_trade_error` and leaves the signal approvable), `mark_open_deployment_trades` (minute marker: marks OPEN trades to live ticks, auto-closes on stop/target via the existing `mark_trade_to_market`, transitions signals to EXITED; tickless trades untouched).
- Evaluator hook runs in `evaluate_active_deployments` **after** `_apply_concurrency_rule` (a trade must never open for a signal that gets demoted moments later) and re-reads the signal state first. The evaluator also now captures the strategy's `risk_hints` (target_pct/stop_pct/spot pts/time stop) on every signal doc.
- `evaluate_active_deployments(db, latest_tick_lookup=...)` — the server loop passes the live tick map and calls the marker each minute during market hours.
- **Entry-price bug fixed in the approve route**: the old flow filled the option paper trade at `signal.entry_price` (the SPOT index close, e.g. ~23,900) while the trade's instrument and all later marks are option premium (~150) — corrupting every P&L. Both paths now resolve premium; no premium → no trade + journaled reason. Approve also skips trade creation when the signal already carries `paper_trade_id` (no duplicates with auto_paper).
- `DeploymentCreateReq`: `auto_paper` (default true), `auto_paper_target_pct`, `auto_paper_stop_pct` merged into `deployment.risk`. Pre-existing deployments lack the flag → behavior unchanged. Kill switches govern auto trades automatically (paper mode; `max_open_paper_trades` blocks the signal → no trade).
- UI: Live Signals deployment form gets the auto-paper block (visible in paper mode); Strategy Library fetches `include_ineligible=1` and shows an amber "LOW SAMPLE n/10 sessions" badge instead of hiding metrics (only deployments with ≥1 closed trade are listed).
- **Spot-mirror exits**: builtin strategies define exits as SPOT POINTS (`risk_hints.spot_target_pts`/`spot_stop_pts`) — the live equivalent of the backtest's `spot_exit` mode. Auto trades carry `trade.spot_exit` (direction-aware levels); the marker watches the live spot tick and closes the option at its current premium on `spot_target_hit`/`spot_stop_hit`. Premium-% levels (strategy `target_pct`/`stop_pct` — no builtin sets these — or deployment `auto_paper_*_pct`) apply on top via `trade.risk`.
- **Concurrency hardening** (from the adversarial review): atomic signal claim (`paper_trade_claim`) shared by the evaluator hook and approve route — single trade per signal guaranteed; approve resolves premium + claims BEFORE transitioning (premium unavailable → HTTP 409, signal stays CONFIRMED); marker replaces are conditional on `status=OPEN` (concurrent manual close wins); legacy `risk.stop_price`/`target_price` fallback removed from approve (spot-level units would instantly stop out a premium entry). A stale claim with no trade (crash in the claim→insert window) blocks later auto-trades for that signal — visible on the signal doc for audit.
- 28 unit tests (`tests/test_paper_auto.py`) + 4 evaluator integration tests; 432 total pass. Live verification limited to off-market checks (form → deployment doc, routes) — the first real market session will exercise the full path; watch backend logs for `auto-paper` lines.

## Recent Work — Honest Walk-Forward Optimization (2026-06-10)

The single optimizer maximizes its objective over the full window, so its result is in-sample by definition (the post-hoc walk_forward() check measures param stability, not selection bias). The new WFO mode does the honest version:

- `backend/app/wfo.py` — window splitter over TRADING days present in the data (rolling / anchored, holiday-aware by construction), per-window Optuna TPE re-optimization on the train slice only, OOS evaluation of each window's best on its unseen test slice, stitched OOS equity + metrics (same formulas as `compute_metrics`), **walk-forward efficiency** (OOS pnl/day ÷ IS pnl/day; ≥0.7 strong, <0.4 overfit), **OOS consistency** (share of OOS-positive windows), **param stability** (rel_spread of each chosen param across windows). Final deployable params = best of the most recent train window; saved as `best_params` + a full `best_backtest_run_id` so Save-as-Preset / View-in-Lab / deployment flows work unchanged.
- Indicators are computed once on the full frame and sliced per window — safe because every indicator in `app/indicators.py` is causal (trailing windows only), and it gives test windows realistic warmup history exactly like live.
- Jobs live in `optimization_jobs` with `kind="wfo"`; cancel at trial boundaries; **pause/resume at window granularity** (completed windows persist in `wfo_windows`; a half-finished window re-runs). Startup orphan-marking covers WFO jobs automatically.
- Routes: `POST /api/optimize/wfo` (window config: `train_days=60`, `test_days=20`, `step_days=None→test_days`, `wf_mode=rolling|anchored`, `n_trials_per_window=40`, `max_windows=12` — oldest windows dropped so deployable params come from the newest data). Resume route branches on job kind.
- UI (`Optimizer.jsx`): "Run type" selector (Single | Walk-forward), window config block, WFO results panel — stitched-OOS headline badges + equity sparkline, WF-efficiency color coding, per-window table with per-window params, param-stability bars. Job history tags WFO runs.
- 22 unit tests in `tests/test_wfo.py` (window math, stitch metrics incl. max-DD, efficiency guards, stability).
- WFO evaluates on spot only (v1). For option realism, run the final preset through an option re-rank optimization or an option backtest afterwards.

## Read Order For A New Agent

1. This file (`docs/HANDOFF.md`)
2. `plan.md` (product roadmap) + `docs/PROJECT_OVERVIEW.md`
3. `docs/ARCHITECTURE.md`
4. `docs/API_REFERENCE.md`
5. `.kiro/specs/optimizer-enhancements/` (a worked example of the spec workflow used here)
6. `ltm/runtime/active-context.json` + `ltm/runtime/last-recall.md` (long-term memory of recent sessions — richest record of the latest work)
7. The relevant code + tests for the next task.

## Recent Work — Optimizer Overhaul + Options-Buying Upgrades (2026-06-09)

The most recent and richest work. The user's goal is a disciplined 0/1/2-DTE index-options **buying** system; the guiding principle is ONE shared decision engine where backtest, paper, and live agree, hard rules apply only to live real-money, and paper/forward/signal stay unrestricted but tagged.

**Trading-logic slices (backend `app/`):**
- `dte.py` — DTE filter (0..6 trading days before nearest expiry), metadata-driven.
- `option_costs.py` — rupee cost model: brokerage + STT/exchange/SEBI/GST/stamp + **bid-ask spread as a % of premium** (the silent killer on cheap OTM/0DTE).
- `portfolio.py` — premium-at-risk position sizing + rupee equity curve. Lot SIZE always from `option_contracts.lot_size`; user picks lot COUNT or % risk.
- `market_context.py` + `vix.py` — regime / time-of-day / DTE / India-VIX buckets; India VIX ingested as `INDIAVIX` in `candles_1m` (AUX key `NSE_INDEX|India VIX`), baseline from 2025-12-29.
- `context_signals.py` + `strategies/builtin/explosive_reversal.py` — S/R, round levels (NIFTY 50/100; BANKNIFTY & SENSEX 500s), RSI/MACD divergence; score-based (additive), NOT hard gates.
- `exit_engine.py` — single shared `intrabar_exit(high,low,stop,target,is_long,stop_first)` used by both spot and option engines (deduped 3 exit decisions).

**Option pairing fixes (server.py / option_backtest.py):** windowed contract query (`length=None` + expiry-window filter) fixed near-zero pairing; expiry-mode selector (auto nearest-per-trade vs fixed); hardened against silent oldest-contract fallback. `simulate_paired_option_trades` now pre-groups candles by `instrument_key` once (was O(trades×candles) per-trade scan).

**Pre-run option preflight:** `POST /api/backtest/option-preflight?ingest_missing=0|1` — reports would-pair coverage % and missing contracts/candles before a backtest; with `ingest_missing=1` + Upstox connected, submits a background option-warehouse fetch. UI: "Option Data Preflight" panel in Backtest Lab.

**Auto-Optimizer overhaul (`app/optimizer.py`, `Optimizer.jsx`) — see the optimizer section below for detail:**
- Spec authored at `.kiro/specs/optimizer-enhancements/` (requirements → design → tasks).
- Optional **guard rails** (single toggle, default ON): `min_trades` significance floor (default 10) + optional CE/PE `min_direction_share`. OFF = pure objective maximization (one-sided/all-PE allowed — profitability is the objective, not balance).
- **Indicator-period search** (`optimize_indicator_periods`): RSI/MACD/ATR/EMA/ADX/CHOP/swing lengths become tunable; indicators are recomputed (cached) per indicator-period combo — fixed a bug where they were frozen at defaults.
- **net_pnl_inr** objective (net points × latest lot size).
- **Two-stage option re-rank** (`evaluation_mode: "spot" | "option_rerank"`): Stage 1 fast spot search; Stage 2 re-ranks top-K (default 50, ≤500) by REAL paired-option net rupee (option candles loaded once, simulated in-memory). Exposes that spot-profitable params can LOSE on options. Old "spot" path is fully intact for A/B.
- **Speed:** backtest hot loop converted from `df.iloc[i]` to pre-materialized dict records → ~8.8x faster row access; `indicators.detect_fvg` vectorized (was a GIL-holding O(n) Python loop). Heavy work runs in `asyncio.to_thread` so the API stays responsive.
- **Pause / Resume / crash-resume:** `POST /optimize/jobs/{id}/pause` + `/resume`. Progress (compact trial log + best-so-far) is flushed to the job doc; resume rehydrates and re-seeds the Optuna study. On restart, orphaned jobs are marked **`interrupted`** (resumable), not failed. Save-as-preset works for paused/interrupted/failed via best-so-far fallback.
- UI niceties: pre-trade profile selector (was a dead backend↔frontend link), clone-config-to-setup from Job History, "no usable result" hint, preset delete button, setup config persisted to `localStorage`.

**Why parallelism was removed from the plan:** evaluated and permanently dropped. For an options-buying app, raw trial speed is a non-bottleneck and "more trials" raises overfitting risk. The actual per-bar bottleneck was solved non-parallelly (dict records ~8.8x; vectorized FVG). Process parallelism fights the pause/resume design, degrades Bayesian TPE's sample efficiency, and adds Windows `spawn` complexity. If specific loop speedups are ever needed, use algorithmic approaches: split signal-gen from trade-sim, memoize duplicate params, multi-fidelity pruning, numba JIT.



## Recent Work — Per-Deployment Kill Switches (2026-06-01)

Phase 4b Slice 12 completed (paper deployments only):

- Added `backend/app/deployment_kill_switch.py` — pure decision helpers (`trailing_consecutive_losses`, `daily_realized_summary`, `evaluate_kill_switches`) + an async wrapper `check_deployment_kill_switches` that loads the deployment's paper trades.
- Three switches, configured under `deployment.risk`:
  - `max_consecutive_losses` → **PAUSE** (hard circuit-breaker, like drift) when the trailing run of losing closed paper trades reaches the limit.
  - `daily_loss_cutoff_pct` → **PAUSE** when today's net realized paper P&L as a % of capital deployed today drops to/below the (negative) cutoff.
  - `max_open_paper_trades` → **BLOCK** (soft) new signals while this many paper trades are OPEN; self-clears as trades close; does not pause.
- Wired into `deployment_evaluator.evaluate_deployment_on_close`: the pause check runs right after the drift check (auto-pauses with `kill_switch_reason`/`kill_switch_inputs` stamped on the deployment); the block reason is added to the bar's signal blockers.
- New `DeploymentCreateReq` fields (`max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`) merged into `risk`. Live Signals form exposes them; the deployment card shows the pause reason.
- Only paper deployments are governed. 16 unit + 2 evaluator-integration + 1 contract test.

Live-session slice completed while the Upstox stream was active:

- Added `backend/app/live_option_universe.py`, which builds a narrow read-only option subscription set from current live spot ticks, stored `option_contracts`, nearest `expiry_date >= today`, and an ATM-centered strike band.
- Added `GET /api/upstox/stream/options/universe` to preview the current option keys before mutating the stream.
- Added `POST /api/upstox/stream/options/restart` to restart the read-only Upstox V3 stream with the normal market-header instruments plus the live option universe. This is necessary because Upstox subscriptions are captured at WebSocket connect time.
- Default live option universe is NIFTY, BANKNIFTY, SENSEX with `radius=1` (ATM, one strike below, one strike above; CE+PE = 6 contracts per index). Keep this small until forward/paper behavior is trusted.
- Live verification on 2026-06-01: stream restarted with 29 instruments (11 header + 18 option keys); option ticks arrived; live candle roller remained healthy and continued aggregating only spot index candles.
- If the universe preview shows `missing_contracts`, run the existing current contract sync route first: `POST /api/upstox/options/contracts/{instrument}/sync`.

## Recent Work — Forward Metrics Aggregation (2026-06-01)

Phase 4b Slice 10 completed:

- Added `backend/app/forward_metrics.py` to compute deployment-level paper metrics from closed `paper_trades`.
- Session completeness is measured from `candles_1m` for the deployment instrument in the 10:00-15:00 IST window. A session is complete at `>=70%` coverage, i.e. at least 210 of 300 expected minutes.
- Headline metrics include only closed paper trades whose entry session was complete. Trades from incomplete/missing sessions are counted under `excluded_incomplete_session_trade_count` for audit.
- New routes: `GET /api/deployments/metrics?include_ineligible=1` and `GET /api/deployments/{deployment_id}/metrics`.
- Strategy Library now fetches visible deployment metrics and shows them inside the relevant strategy card only when `complete_session_count >= 10`.
- Live DB smoke on 2026-06-01: 7 deployments are collecting metrics, 0 visible yet because none has 10 complete sessions.

## Recent Work — Warehouse Chart Trust UI (2026-06-01)

Focused pass on the Data Warehouse candlestick panel:

- The top-left chart overlay now shows explicit Open / High / Low / Close values with chart-theme-safe colors. It no longer relies on the app page theme, which made O/H/L hard to read on a dark chart.
- Added small icon-only chart theme controls: System, Dark, Light. This is local to the chart and does not change the whole app theme.
- Every timeframe now requests the full stored warehouse range. The earlier short-range behavior was a frontend `LOOKBACK_DAYS` optimization (`1m=3d`, `5m=7d`, `15m=21d`, `1h=90d`), not missing warehouse data.
- Chart time labels are formatted in IST through the Lightweight Charts tick formatter, with a footer reminder that the regular session is 09:15-15:30 IST.
- Added session-open markers so intraday multi-session views show where a new Indian market session begins.
- 1h resampling is anchored to 09:15 IST, not 09:00. Gap detection skips the current in-progress trading session until after 15:30 IST.
- Fixed a stale async request race: the slow default full-history `1d` load could finish after a quick `1m`/`5m` switch and overwrite the chart while the toolbar showed the newer timeframe. `loadSeqRef` now ignores older responses.

## Recent Work — Data Warehouse Hardening (2026-05-31)

A full pass to make the warehouse trustworthy and fast. All slices committed and pushed to `main`:

- **Perf:** option coverage served from `option_coverage_cache` (8s → ~200ms); the page renders on fast calls and loads the heatmap independently. (`190ba45`)
- **Quick wins:** removed the "Made with Emergent" badge + loader script + PostHog telemetry; removed the obsolete yfinance ingest panel; added the NSE holiday-calendar modal. (`23b07f9`)
- **Correctness:** Data Trust Audit is now holiday-aware (was counting NSE holidays as missing days). (`76fb99c`)
- **Persistent jobs:** `JobsProvider` above the router tracks ingest/fetch/hygiene jobs and persists run IDs to `localStorage`, so progress bars survive navigation; global active-jobs indicator in the top bar. (`6242b08`)
- **Data Hygiene UI:** the plan/execute/status workflow is surfaced as the hero panel; page regrouped into Connection / Data Hygiene / Index Data / Option Data / Verify & Audit / Diagnostics. The hygiene plan was optimized from a 120s+ timeout to ~6s by dropping a `$lookup` join. (`8f9c695`)
- **Auto-update:** warehouse catches up to yesterday's close on startup, on OAuth-connect, and daily at 18:00 IST; status + toggle in the UI. (`70e5b4a`)
- **Point lookup:** spot + ATM CE/PE for any date/time, read from the warehouse only, to cross-check against a broker terminal. (`d8bb4b5`)
- **Candlestick chart:** per-index chart (1m/5m/15m/1h/1d) with an OHLC crosshair legend, a date/time locator (validates + snaps to bucket + marks the bar), and a gap banner. The backend now filters chart candles/gaps to calendar-approved 09:15-15:30 IST regular sessions so weekend/holiday/off-session rows do not create false candles or gap warnings. (`7b16457`, `882092d`)
- **UI follow-ups:** Backtest Run Journal moved into Backtest Lab; Signal Journal repurposed as the deployment signal audit trail; OAuth token-expiry countdown in the top bar. (`2fcb9d0`)
- **Cleanup:** removed the redundant Raw Option Universe Audit panel (kept the clear-options action in Data Trust Audit; the `/options/audit` route stays for programmatic use). (`882092d`)

## Working Local Stack

| Service | Where | Notes |
|---|---|---|
| Frontend | `http://localhost:3000` | React + nginx, dark/light theme |
| Backend | `http://localhost:8001` | FastAPI, all routes under `/api` |
| MongoDB | container `alphaforge_mongo` | Persistent named volume `mongo_data` (NOT in the project folder / OneDrive) |
| Upstox | OAuth flow, REST historical, V3 WebSocket stream | OAuth expires daily; re-connect drives the auto-update |

The stack is launched with `docker compose up -d --build`. Use `start.bat` (Windows) or `start.sh` (Mac/Linux).

## What Is Working End-To-End

Data:

- Index 1-minute candles for NIFTY, BANKNIFTY, SENSEX in `candles_1m`, audited per day in `integrity_hashes`. Holiday-aware audit via `nse_calendar`.
- ATM CE/PE option candles in `options_1m` (NIFTY ~1.46M / BANKNIFTY ~1.69M / SENSEX ~2.21M; OI populated).
- Option contract metadata in `option_contracts` (strike, side, expiry_date, instrument_key, lot_size).
- NSE holiday calendar with budget-Saturday and shifted-expiry exceptions in `backend/app/nse_calendar.py`; surfaced as a holiday-calendar modal.
- Live tick → 1m OHLC roller closes the same-day historical gap (`backend/app/live_candle_roller.py`) and now drops non-trading-day/off-session ticks before they can create warehouse candles.
- Data Hygiene workflow (UI + backend) audits the warehouse against a default scope (2024-11-27 → today, ATM only) and submits dependency-ordered fetches; ~6s plan.
- Automatic warehouse catch-up (`backend/app/warehouse_autoupdate.py`) on startup, OAuth-connect, and daily 18:00 IST.
- Option coverage served from a precomputed cache (`option_coverage_cache`) for fast page loads.
- Point-in-time lookup (spot + ATM CE/PE at a date/time) and a per-index candlestick chart with gap detection, explicit OHLC, IST axis labels, session-open markers, and local chart theme controls, all warehouse-only.

Research:

- 6 built-in strategies plus a custom plugin loader (`backend/app/strategies/builtin/*.py`, `plugins/*.py`); includes `explosive_reversal` (score-based context detector).
- Backtest with realistic costs, walk-forward IS/OOS, statistical significance, regime detection. Hot loop is dict-records based (~8.8x faster than per-row `df.iloc`); `intrabar_exit` is shared between spot and option engines.
- Rupee cost model (`option_costs.py`: brokerage + statutory charges + %-of-premium spread), premium-at-risk sizing (`portfolio.py`), DTE filter (`dte.py`), market-context + India-VIX tagging (`market_context.py`, `vix.py`).
- Pre-run option-data preflight (`POST /api/backtest/option-preflight`) reports would-pair coverage and optionally ingests missing option data.
- **Auto-Optimizer (upgraded):** Bayesian / Grid / CMA-ES with robustness, importance, heatmap, top-N. Optional guard rails (min_trades + CE/PE share), indicator-period search, `net_pnl_inr` objective, an **option-aware two-stage re-rank** (`evaluation_mode=option_rerank`), and **pause / resume / crash-resume** with persisted progress. The legacy spot-only path is preserved for A/B.
- Slippage model wired into paired option backtests (`backend/app/slippage.py`).
- Post-hoc volatility detector (`backend/app/volatility.py`) replaces the rejected event calendar.

Forward testing:

- Strategy Deployments persisted in `strategy_deployments`, created only from saved presets or backtest runs.
- 1-minute close evaluator (`backend/app/deployment_evaluator.py`) running on a background scheduler during NSE market hours.
- Paper deployments with `risk.auto_paper` (default ON for new deployments) auto-trade every clean CONFIRMED signal at real option premium; a per-minute marker fires premium and spot-mirror exits intraday.
- Remaining CONFIRMED signals (shadow/recommendation, auto-paper off, auto-trade refusals) show in the Pending Approval panel; user clicks Approve / Skip / Mark Blocked.
- Approve creates a paper trade when `deployment.mode == "paper"`, entry at resolved option premium, lot size pulled from the option contract; never duplicates an auto-created trade.
- Forward metrics aggregate win-rate, average P&L, and profit factor from closed paper trades, gated by complete 10:00-15:00 IST sessions; low-sample results surface in Strategy Library under an amber badge.
- Auto square-off at 15:00 IST every market day (override per deployment with `risk.allow_overnight=true`).
- Expiry-day cutoff at 15:00 IST blocks new signals on the deployment instrument's expiry day.
- Strategy source SHA is pinned on every new deployment; the evaluator auto-pauses on drift.
- Pre-flight data realism panel and deployment quality warnings with required acknowledgment surface known issues at deployment creation.
- Idempotency hardened with the partial unique index `signals(deployment_id, candle_ts)`.

Live data:

- Upstox V3 read-only market-data WebSocket stream auto-starts on backend boot.
- Market header prefers fresh ticks and falls back to REST quotes when ticks are stale or absent.
- During market hours, the stream can be restarted with a small current ATM option universe through `/api/upstox/stream/options/restart`; this feeds live option LTPs into the in-memory latest-tick map for paper/recommendation marking.

## What Is Not Done

Warehouse: complete for v1 (this session). Optional warehouse extras not yet built: option price sanity check (intrinsic floor / impossible-jump flagging), a `mongodump` backup button. OI is populated but a dedicated staleness check is not built.

Product roadmap (from `plan.md`):

- Phase 4b forward-testing stack is **complete** (incl. Slice 12 per-deployment kill switches).
- **Optimizer follow-ups:** honest walk-forward is DONE (2026-06-10). Remaining: after A/B-validating the option re-rank advantage, retire the legacy spot-only path; then a **risk engine** (live-only hard rules: position sizing, daily loss cutoff, regime gating — paper/forward stay tagged-not-blocked). Possible WFO v2: option-aware OOS evaluation per window.
- ~~User-requested (2026-06-10): auto paper trading + low-sample metrics~~ — **DONE 2026-06-11** (see the top "Recent Work" section). Remaining follow-up: verify the auto-trade path in the first live market session.
- Deferred optimizer items: full per-trial option-aware evaluation, loop speedups (split signal-gen from trade-sim, memoization, multi-fidelity pruners, numba JIT — user granted dependency-install permission).
- Data: Flattrade/Fyers historical-option API as a fallback source to fill residual ~5-8% option-data gaps Upstox lacks (TradingView is NOT viable for option premium).
- Phase 5 profitability boosters (Kaplan–Meier survival, meta-model, Kelly sizing, Telegram alerts) deferred until ≥6 months of forward signal history exists.
- Phase 6 swing/positional extension is not started.
- No automatic broker order placement. The manual approval gate is intentional and must remain.

## Project Conventions (Important)

These were locked by the user during development. Do not change them without asking.

- DTE filter default: `[0, 1, 2, 3, 4, 5, 6]` on every deployment (full week + 2 days).
- Auto square-off at 15:00 IST every market day. `risk.allow_overnight=true` opts out per deployment.
- Slippage defaults: ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x multiplier.
- Time-of-day blocks on signal generation: 09:15–09:25 IST (first 10 min) and 14:50–15:30 IST (last 30 min).
- Expiry-day cutoff: 15:00 IST on the deployment instrument's expiry day, looked up from `option_contracts.expiry_date`. Never weekday-hardcoded.
- Data hygiene scope: 2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE only, sample=1m. Do not extend back to Jan 2024.
- Lot size: always read from `option_contracts.lot_size` (Upstox-supplied), never hardcoded.
- No event calendar. Reliable scheduled-event timestamps are unavailable. The post-hoc volatility detector replaces this.
- Session completeness: a forward session counts as "complete" only if data covered ≥70% of 10:00–15:00 IST.
- Walk-forward acceptance: the app warns but does not block. The user makes a conscious choice via the acknowledgment checkbox.
- All routes under `/api`. CORS open in dev.
- Never commit `.env`, access tokens, broker credentials, or `memory/test_credentials.md`.

## Operational Lessons (Discovered During Development)

These are the gotchas that bit us. Read this section before doing related work.

### Upstox

- Upstox returns `400 Invalid date range` on 30-day chunks crossing a Feb→Mar boundary. Use `chunk_days=7` for spot ingest. The chunker already uses 7.
- Upstox historical endpoint returns **empty for the same trading day**. Without the live tick → 1m roller the evaluator is stuck on yesterday's last bar. The roller closes this gap.
- `GLOBAL_INDICATOR|USDINR` is rejected by the REST quote endpoint (HTTP 400) but works on the WebSocket. Market header gracefully falls back per-tile.
- Expired option candles must be requested through `/v2/expired-instruments/historical-candle/{expired_key}/1minute/{to}/{from}`. Sending expired keys to the normal V3 endpoint returns `UDAPI1021`.
- The WS stream's subscribed instrument set is captured at connect time. Changing the subscription list in code does not auto-update an already-running stream — you must stop and restart.
- The live option stream preview requires current `option_contracts`. If BANKNIFTY/SENSEX/NIFTY show `missing_contracts`, sync current contracts first. As of this work, BANKNIFTY current expiry resolved to the next available monthly contract because weekly BANKNIFTY options are discontinued.

### Index expiry calendar

- NIFTY weekly expiry day rotated: Thu (until 2024-08) → Wed (2024-09 to 2025-03) → Tue (2025-04+).
- BANKNIFTY weekly options were discontinued in November 2024. Only monthly expiries are available since.
- SENSEX is a weekly Friday expiry on BSE. It can shift to Wednesday when Thursday is a holiday — example: 2026-01-15 BMC/Maharashtra civic elections shifted SENSEX expiry to 2026-01-14. The `SHIFTED_EXPIRY_DAYS` set in `backend/app/nse_calendar.py` records these.
- 2025-02-01 and 2026-02-01 are Budget Saturday trading sessions. Both are listed in `SPECIAL_SATURDAY_SESSIONS`.
- 2025-10-21 Diwali Muhurat trading captured 60 candles (limited evening session). The audit recognizes this.
- 4 days in the warehouse have off-by-1 candle counts (374 candles total) caused by single-minute Upstox glitches. Treated as complete.

### Contract picker

- Always filter `option_contracts.expiry_date >= today` when resolving an ATM/OTM/ITM contract for a live signal. The 2026-05-28 bug where a Nov-2024 expired contract was selected was caused by missing this filter. Blocker name: `option_contract_no_active_expiry`.

### Strategy source drift

- `strategy_source_sha` is pinned on every new deployment. If the plugin .py file changes and you want the deployment to keep running, you must create a new deployment or explicitly re-pin (no UI for that yet).
- Pre-slice-8 deployments without a pinned SHA continue to operate. Drift detection is opt-in by deployment-creation timing.

### Idempotency

- The unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` is in `backend/app/db.ensure_indexes()`. The partial filter `{deployment_id: {$exists: true, $type: "string"}}` keeps manual research signals out of the constraint.
- The evaluator catches `E11000` duplicate-key errors and treats them as `outcome="skipped"`, `reason="already_journaled"`, then advances `last_evaluated_ts` to avoid retry loops.

### Quality gates

- The `acknowledged_warnings=true` flag is required at deployment creation when `deployment_quality.evaluate(...)` returns warnings. The 400 error code is `acknowledgment_required`. Pre-existing deployments are unaffected.
- Quality is evaluated against the source preset or backtest run. Five checks: missing walk-forward, walk-forward divergence (OOS < IS × 0.7 or explicit divergence flag), trade count < 30, Sharpe < 0.5, |max_dd|/total_pnl > 0.15.

### Performance (warehouse page) — learned 2026-05-31

- `options_1m` has 5M+ docs. Any aggregation over it on a page-load path is too slow. Option coverage is precomputed into `option_coverage_cache`; the data-hygiene plan groups on the embedded `underlying`/`expiry_date` fields (the `(underlying, expiry_date, strike, side, ts)` index supports it) instead of a `$lookup` join. If you add a new read-path aggregation, cache it or window it.
- `options_1m` candles already carry `underlying` and `expiry_date` (set at fetch time in `option_warehouse_jobs.persist_option_candles_bulk`), so you rarely need to join to `option_contracts`.
- The candlestick chart windows intraday timeframes (1m=3d, 5m=7d, 15m=21d, 1h=90d, 1d=full) so requests stay ~100ms. Full-history 5m is ~3s.

### Frontend background jobs — learned 2026-05-31

- Long-running job polling must live in `frontend/src/lib/jobs.jsx` (`JobsProvider`, mounted above the router in `App.js`), not in page-local state, or progress is lost on navigation. Active run IDs are persisted to `localStorage` (`alphaforge.activeJobs`, `alphaforge.activeHygiene`) and resumed on mount.
- The provider tracks single jobs (`upstox_ingest`, `option_fetch`) and the data-hygiene batch separately; pages subscribe to completion via `onJobComplete(kind, fn)`.

### Git on this repo

- `core.autocrlf=true`, so `git push`/`commit` print CRLF warnings — harmless. Splitting mixed-file commits by hunk requires `git apply --cached --recount --ignore-whitespace`.

## Architecture Snapshot

Backend modules of note:

- `backend/server.py` — FastAPI routes and orchestration.
- `backend/app/db.py` — Mongo client, `ensure_indexes()`, JSON-safe serialization.
- `backend/app/deployment_evaluator.py` — 1m_close forward evaluator + scheduler logic.
- `backend/app/forward_metrics.py` — session-gated forward paper metrics for deployments.
- `backend/app/deployment_preflight.py` — pre-flight data realism check.
- `backend/app/deployment_quality.py` — quality warnings (5 checks).
- `backend/app/data_hygiene.py` — warehouse fill plan + execute (index-friendly aggregations, ~6s).
- `backend/app/warehouse_autoupdate.py` — automatic catch-up (startup / OAuth / daily 18:00 IST).
- `backend/app/warehouse_lookup.py` — point-in-time spot + ATM CE/PE lookup.
- `backend/app/warehouse_ohlc.py` — OHLC resampling + intraday gap detection, filtered to calendar-approved regular sessions.
- `backend/app/option_coverage_cache.py` — precomputed option-coverage cache (fast page loads).
- `backend/app/nse_calendar.py` — holiday list, Budget Saturdays, shifted expiry days, labeled year calendar.
- `backend/app/live_candle_roller.py` — tick → 1m OHLC for same-day intraday; guards against non-trading-day/off-session warehouse writes.
- `backend/app/paper_squareoff.py` — 15:00 IST auto square-off loop.
- `backend/app/slippage.py` + `volatility.py` — execution realism.
- `backend/app/strategy_source_hash.py` — drift detection.
- `backend/app/option_data_planner.py` + `option_warehouse_jobs.py` — option fetch flow.
- `backend/app/upstox_client.py` + `upstox_stream.py` — broker REST + WebSocket.
- `backend/app/live_option_universe.py` — live ATM option universe preview/restart support for read-only option ticks.

Frontend of note:

- `frontend/src/lib/jobs.jsx` — global `JobsProvider` (background-job tracker, survives navigation).
- `frontend/src/components/Layout.jsx` — sidebar, top bar, active-jobs indicator, token-expiry countdown.
- `frontend/src/components/DataHygienePanel.jsx`, `WarehouseLookup.jsx`, `WarehouseChart.jsx`, `HolidayCalendarDialog.jsx`, `BacktestRunJournal.jsx`.
- `frontend/src/pages/DataWarehouse.jsx` — sectioned warehouse console (hygiene, index, options, verify, diagnostics).
- `frontend/src/pages/LiveSignals.jsx` — Pending Approval panel + Strategy Deployment form.
- `frontend/src/pages/SignalJournal.jsx` — deployment signal audit trail.
- `frontend/src/pages/BacktestLab.jsx`, `Optimizer.jsx`, `PaperTrading.jsx`.

Mongo collections in active use:

- `candles_1m`, `options_1m`, `option_contracts`, `integrity_hashes`, `warehouse_runs`, `option_coverage_cache`
- `backtest_runs`, `optimization_jobs`, `presets`, `pretrade_profiles`
- `strategy_deployments`, `signals` (with the unique partial index above), `paper_trades`
- `ticks`, `upstox_tokens`

See `docs/ARCHITECTURE.md` for the full module map.

## Verification Checklist

```bash
python -m pytest tests -q     # 440 pass as of 2026-06-12
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

UI smoke checks:

- Theme selector switches System / Black / White cleanly.
- Data Warehouse: Data Hygiene "Check warehouse" returns per-instrument status; coverage heatmaps render fast; candlestick chart loads, O/H/L/C overlay is readable, axis is IST, chart theme icons switch, date/time locator marks a bar, and holiday-calendar modal opens.
- Top bar shows the OAuth token-expiry countdown.
- Live Signals page shows the deployment list and the Pending Approval panel.
- Strategy Library loads without console errors; deployments with closed paper trades show a Forward block — full metrics at ≥10 complete sessions, an amber "low sample" badge below that.
- Creating a deployment with quality warnings is blocked until the ack checkbox is ticked.
- Live Signals deployment form shows the auto-paper block in paper mode and the kill-switch fields.

Service health:

- `GET /api/health` returns `{db: "ok"}`.
- `GET /api/upstox/status` shows connected when OAuth is current.
- `GET /api/live-candles/status` shows the roller running during market hours.
- `GET /api/warehouse/auto-update/status` shows the last catch-up run.

## Recommendations For The Next Agent

- Read the relevant slice section in `plan.md` before starting. Each slice has done/not-done markers.
- Add tests next to the module you change. The `tests/` directory is the truth — `pytest -q` must pass before you commit.
- Keep changes small and verifiable. The user prefers small slices over big rewrites.
- Use the LTM workflow (`.kiro/steering/ltm-operations.md`) if asked to resume or recall.
- If a problem repeats, look at the operational-lessons section above before retrying.
- Push directly to `main` on `hrninfomeet-wq/Emergent-AlphaForge`. Use clean multi-line commit messages with bullet points.
- Use `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` for new local `FERNET_KEY` values.
- Never echo broker secrets or token values. Reference them by env-var name only.
