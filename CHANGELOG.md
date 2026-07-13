# Changelog

All notable changes to AlphaForge Trading Lab.

## [0.53.0] — Phase 4 groundwork: AI feasibility accepts premium-native rules + Gemini token-cutoff fix (2026-07-13, Emergent handoff session)

**The Strategy Library AI wizard no longer blanket-rejects the AlgoTest
"Configurable Contingency Breakout" blueprint.** The user-facing symptom was
that every rule of a premium-native blueprint (option-premium momentum trigger,
locked strike, stepped premium trail, session gates, position size) came back
`Can't map this to anything derivable from 1m OHLCV — R9 REJECT`, even though
the shipped `premium_momentum` code family already implements exactly those
rules and the deployment layer already handles the session gates + position
sizing. Root cause was `backend/app/ai/capability.py`'s R1-R9 model having zero
vocabulary for premium-native rules — Phase 4 (per the
`2026-07-13-premium-momentum-phase4-5-full-contingency-design.md` spec) had
never happened.

**What landed this session** (host-safe / pure, TDD; motor/route work
deliberately deferred to a follow-up session because it needs the container
+ a live warehouse fixture):

1. **New `PREMIUM_TRIGGER_CONCEPTS` classifier branch** (`app/ai/capability.py`) —
   fires BEFORE R5 so `option_premium_trigger` never collides with the existing
   ICT `premium_discount` mapping. Verdict: `BUILDABLE_NOW` with a mapped
   `feature="premium_trigger_config"` pointing at the shipped code
   (`app/premium_momentum*.py`, `app/live/live_sl_monitor.py` stepped_xy).
   Backed by 14 new host-safe tests in `tests/test_capability_premium_trigger.py`.
2. **New `SESSION_GATE_CONCEPTS` classifier branch** — entry/exit time windows,
   EOD square-off, re-entry cutoff, day caps, global TP/SL, AND position size
   all map to `feature="deployment_layer"` with a message pointing users at the
   *existing* mechanism (`app/live_deploy_governor.py`,
   `app/deployment_kill_switch.py`, `app/live/auto_square.py`) instead of
   pretending they belong in a strategy's `evaluate()`.
3. **Honest scoping for `PHASE5_FUTURE_CONCEPTS`** (`lazy_leg_contingency` +
   aliases) — verdict is `BUILDABLE_WITH_FEATURE` + `live_feasible=False` with
   an explicit "Phase 5 future work, design committed but not shipped" message,
   pointing at `docs/superpowers/specs/2026-07-13-...phase4-5*.md`. NOT an
   `INFEASIBLE` (that would mis-frame it as impossible) and NOT a false BUILD
   (that would promise behavior the code can't back).
4. **`capability_summary()` advertises the premium-trigger tier** so the LLM
   authoring prompt teaches the model the correct concept names AND the
   Strategy Library UI can render a discoverable "Premium-trigger strategies"
   section. Shipped features vs. Phase-5 future work explicitly separated.
5. **LLM prompt updated** (`app/ai/authoring_agent.py::_ruleset_system_prompt`)
   — the new concept vocabulary is enumerated for the model, WITH an explicit
   disambiguation warning about the `premium_discount` (ICT zone) vs.
   `option_premium_trigger` (option-price rule) token collision that would
   otherwise silently mis-route.
6. **Gemini truncation fix** (`app/ai/_gemini.py`, `app/ai/_anthropic.py`,
   `app/ai/llm_client.py`, `app/ai/py_author.py`): user was hitting
   `"AI (gemini-2.5-pro) response was cut off at the 8000-token limit"` on
   every AI Generate / Check Feasibility because `py_author.py` hard-capped at
   8000 and `_gemini.DEFAULT_MAX_TOKENS` was 8192 — gemini-2.5-pro's thinking
   tokens draw from the same budget and consumed most of it. Bumped
   `_gemini.DEFAULT_MAX_TOKENS` 8192 → 32768, `_anthropic.DEFAULT_MAX_TOKENS`
   8192 → 16384, `llm_client.complete_structured` wrapper default to match
   the highest per-backend default, and removed `py_author.py`'s explicit
   `max_tokens=8000` cap (it now inherits the wrapper default). google-genai
   /Anthropic bill on emitted tokens, not the ceiling, so this doesn't spike
   cost for small outputs. New host-safe test file:
   `tests/test_gemini_token_budget.py` (8 tests) pins the invariants.
7. **`docs/LOCAL_SETUP.md`** rewritten with a proper "sync from GitHub and
   run" TL;DR, an AI wizard setup section (Phase 4 sanity check), and
   troubleshooting entries for AI-provider config + the historic token-cutoff.
8. **`backend/.env.example`** expanded to a full template — AI keys grouped
   with links to obtain them, live-trading flags called out as OFFLINE-FIRST
   with explicit `LIVE_AUTOPLACE_ARMED=0` / `LIVE_GUARD_ARMED=0` defaults.

**End-to-end proof (with a live Gemini API call using the user's key):**

Before Phase 4: 13/13 blueprint rules → `INFEASIBLE` → aggregate `REJECT`.

After Phase 4: 13/13 rules mapped honestly:
- 11 `BUILDABLE_NOW` (7 → `premium_trigger_config`, 4 → `deployment_layer`)
- 2 `BUILDABLE_WITH_FEATURE` (`lazy_leg_contingency`, Phase-5 future work,
  honestly-scoped as backtest-only until shipped)
- Aggregate: **`ADVISE` — "Buildable with caveats — backtest-only feature(s):
  lazy_leg_contingency."** ✅ matches the spec's "ACCEPT or honestly-scoped
  partial mapping, not a blanket reject" end state.

**Test count**: 3,304 host-safe tests pass (up from ~3,290 pre-session).
1 pre-existing test (`test_premium_momentum_route::test_route_runs_a_real_
warehouse_backtest`) still fails on the host because it needs a seeded Mongo
warehouse — verified unrelated to this session's changes by re-running on the
baseline commit before staging any edits.

**Deferred to a follow-up session** (do NOT treat these as done):

- **Phase 4 engine dispatch**: `backtest.run_backtest` / the general
  Optimizer + Backtest Lab pages still can't dispatch a premium-trigger
  deployment through the same sim + honest tuner used by the bespoke
  `/premium-momentum` page — running the shipped `premium_momentum` plugin
  through the general Optimizer still produces "Option re-rank produced no
  paired results" (the second bug §1.2 in the Phase 4-5 spec, still open).
  The declarative config block + the capability classifier speak the same
  vocabulary now, but the *runtime* still branches on `strategy_id ==
  "premium_momentum"`. Config-block-based dispatch is the next task.
- **Phase 5 lazy-leg contingency** (two-leg state machine, session overlays)
  — full spec in `docs/superpowers/specs/2026-07-13-...phase4-5*.md`.
- **Broader AlgoTest-style multi-leg / greeks-based strategy builder** — user
  request; requires Phase 4 engine dispatch to land first (so the same
  declarative config can express both single- and multi-leg shapes).
- **Optimizer / Backtest / Live / Paper page UX review** — user flagged low
  confidence in what these pages do. Deferred; will need a working-session
  walkthrough to know what to change.

Non-negotiables preserved: no new arming gate, offline-first defaults intact,
`app/live/executor.py` still the sole entry chokepoint (this session touched
zero live-execution modules), byte-identical backward compatibility for the
existing single-leg `premium_momentum` deployment (nothing in
`premium_momentum*.py` or `deployment_evaluator.py` was touched).

## [0.52.0] — Premium-momentum strategy: live & paper execution (Track B) (2026-07-12)

**New deployable strategy family**: `premium_momentum` rides the standard deployment/arm/guard
rails with a dedicated evaluator branch instead of the generic `strategy.evaluate()` — the first
strategy in the app driven by a **time-locked strike + premium-native trigger** rather than a spot
indicator. At a configurable reference time the evaluator locks the CE/PE strike from spot,
captures each side's real premium from fresh WS ticks (persisted in a new `premium_locks`
collection, unique per `(deployment_id, session_date)`), and the first side whose premium rises
past the momentum threshold journals a signal through the normal audit pipeline. Exits ride a new
`stepped_xy` guard trail mode (AlgoTest-style discrete X-Y ratchet) alongside the existing
stop/target machinery. **No new arming gate**: the strategy is live-armable through the exact same
chain as every other deployment (per-deployment ARM + `LIVE_AUTOPLACE_ARMED`/`LIVE_GUARD_ARMED` +
caps) — a deliberate design choice, not an oversight. Locked strikes are pinned into every option
subscription-stream rebuild (auto-follow + manual restart), and recovery re-hydrates entered
positions from the persisted lock state with a watched-set guard against double-registering a
position across a restart. 250 tests (201 container + 49 host). Branch
`feat/premium-momentum-track-b`, 9-task TDD plan, merged to local main only (not pushed). **Not yet
validated in real market hours** — needs a live weekday session.

## [0.51.0] — Premium-momentum strategy: backtest, cost model & honest tuner (Track A) (2026-07-10)

**New option-native backtest engine** (`premium_momentum.py` + `premium_momentum_backtest.py`):
simulates a time-locked-strike, premium-momentum-triggered strategy with gap-honest intrabar
stop/target fills and a stepped X-Y trailing ratchet, built as a self-contained sim (not the
two-stage spot-then-option engine) to avoid re-resolving a drifting strike. A red-team pass found
and fixed a HIGH-severity look-ahead bug (the trailing high-water mark was updated before the same
bar's low was tested against it) plus a session-scoping bug that silently excluded 92/127 real
sessions (dated-vs-plain instrument-key mismatch, the same root-cause class as the warehouse's
expired-contract key split). **Cost model**: `apply_costs_to_trade` reuses the existing
`option_costs` rupee model verbatim. **Honest tuner** (`premium_momentum_tuner.py`): costs are
mandatory to tune (raises if disabled), selection happens on a chronological TRAIN split only,
results always report OOS, and an overfit flag fires when train-best diverges from OOS — validated
on its first real run (best-by-train +408 pts → −418 pts OOS, correctly flagged as overfit).
**Honest finding**: the AlgoTest blueprint's default parameters have **no edge** on 2026-H1 NIFTY
(18 of 19 sensitivity-sweep cells negative after costs) — the capability was built anyway, per an
explicit decision to separate "can the app run this kind of strategy" from "does this specific
config make money." New `/premium-momentum` research page (setup form, coverage-first results, Tune
panel).

## [0.50.3] — Live-safety: close two pre-existing double-sell windows (2026-07-11/12)

Two races the audit's adversarial review had flagged but left open: (1) **lost-ack blind retry** —
a `place_order` call that raised (network/timeout) but may have actually landed at the broker was
previously retried blind; all three exit executors (`auto_square`, `reprice_exit_leg`,
`panic_squareoff_verified`) now resolve `remarks==client_order_id` against the order book first,
adopting a landed order instead of re-posting, and fail closed (no retry) if the book can't be
read. (2) **panic cancel-confirm barrier** — the kill-switch's flatten pass now confirms every
targeted cancel is terminal (one re-fetch) and re-sizes/skips legs against the fresh position book
before placing, instead of racing a possibly-still-working cancel.

## [0.50.2] — Live-safety: reconcile audit backlog onto the Layer-1/2 guard (2026-07-11)

The four live-safety audit items (#1 broker-truth, #3 kill-switch stop-all, #5 recovery-that-reruns,
#6 guard fail-open) were written against an older guard design; a parallel session had since landed
a confirmed-flat finalize + widening re-price guard (Layer 1/2) on main, so a mechanical
cherry-pick would have regressed it. This branch reconciles all four onto the current guard, then
fixes every CONFIRMED finding from a fresh 3-lens adversarial review: the recovery latch now only
records success on a truly *complete* run (client present, no step raised, book readable) —
previously an incomplete recovery could latch green over an unguarded position; kill-switch claims
gained per-claim TTLs and a post-cancel re-confirm; the EOD square now bypasses a `square_stopped`
guard entry (a no-OCO manual position must still get its 15:00 backstop).

## [0.50.1] — Live-safety audit backlog: items #5–#10 (2026-07-08/09)

Closes out the remaining half of the July audit backlog (see `docs/audit-report-2026-07.md`): **#5
recovery that re-runs** (boot-time recovery was token-gated and boot-only — never ran if OAuth
completed after boot; now re-triggered on the OAuth callback and retried by the supervisor loop,
with a per-token latch); **#6 guard fail-open + gate-split** (a guard that exhausted its retry
budget now re-registers the position instead of silently dropping it; arm-state exposes an
`exit_gap` warning when auto-place is armed but the guard isn't); **#7 optimizer survival-gate
honesty** (the gate can no longer run gross of option costs; a phantom ₹200k capital default is now
a real UI input; the reported "OOS" no longer double-counts in-sample folds); **#8 optimizer
robustness trio** (a cross-job trial-cache poisoning bug in the parallel-worker fallback could leak
another job's cached Series into a new run's results — fixed with a per-call fresh cache); **#9
deployment parity** (the optimizer's trade window now matches what a live deployment can actually
take, instead of rewarding entries in the last 10 minutes before the deployment block); **#10
authored→deployed pipeline** (a `max_tokens` default was silently re-introducing the Gemini
truncation bug the authoring-UX fix had already solved once). Also fixed in this window: the
strategy-pipeline endpoint's paper-deployment count used an uppercase `"PAPER"` match against the
lowercase-stored `mode` field, undercounting every paper deployment on the Strategy Library pipeline
view.

## [0.50.0] — Trading correctness: paper capital gate, verified kill switch, historical range ingestion, deletable strategies (2026-07-07)

Four money-handling correctness/safety features (branch `feat/trading-correctness`,
one commit each; full host suite 3,032 passed).

- **Honest paper capital constraint** (`app/paper_capital.py`): a paper deployment now
  behaves like a demat account of configurable size. Entry-time gate in
  `auto_paper_trade_for_signal` (post-premium-resolution): a trade's premium outlay must
  fit available capital or the signal is **skipped and journaled** (`paper_trade_skip`
  on the signal, visible in the Signals Ledger with the full arithmetic). Per-deployment
  `risk.capital {amount, basis}` from the deploy wizard/caps editor is the primary gate
  (the scope `required_capital` is measured at, so the invariant is provable), plus an
  opt-in account-wide ceiling on `paper_account` (sum of open premium across ALL
  deployments). Basis `fixed` = never compounds, losses debit
  (`amount + min(0, realized in current IST day/week/month/year, total)` — provably
  bounds required_capital in every analytics bucket); `cumulative` = amount + total
  realized P&L. Missing config = legacy unconstrained; both layers default off. The
  wizard's paper step also gains an explicit lots-per-signal override (wires to the
  existing `lots_override`).
- **Kill-switch hardening** (`panic_squareoff_verified` + `KillSwitchPanel`): flattening
  is exchange-aware marketable LIMIT only (per-leg exch/prd/tick from the position row;
  LMT is the only price type this account/API has), priced through the fresh GetQuotes
  touch clamped inside the uc/lc circuit band, freeze-qty sliced; unfilled exits
  re-price through a bounded widening band (1→2→4%) via cancel+re-place (never
  ModifyOrder), re-reading fillshares after each cancel so a race fill can't over-sell;
  broker REJECTs end the leg immediately and surface the reason. Per-leg outcome report
  (placed/FILLED/UNFILLED-WORKING/REJECTED + reason) + final position-book re-check
  (`all_flat`/`residual`) reach the UI; the button now lives in an always-visible panel
  whenever open positions/working orders exist (typed-KILL confirm), not only inside a
  test session. `TERMINAL` gains the broker's "REJECT"/"CANCELLED" spellings.
- **Historical range ingestion** (Data Warehouse page): `/data-hygiene/catch-up` gains
  `from_date`/`to_date`/`confirm` — range mode plans first, ALWAYS (server-side 400
  without `confirm=true` after a dry run), then runs a sequential upsert-only chain:
  spot → expired-contract backfill (with a 35-day expiry lookahead past `to_date` —
  each day's band needs its NEXT expiry) → band-exact option fill over the range.
  Explicit ranges reach back to the verified-calendar floor 2024-01-22 with honest
  warnings (pre-baseline broker depth, expired-options API dependence). New panel:
  dry-run-gated Ingest + "Verify range" that diffs per-day counts/integrity hashes
  against the plan-time snapshot. **Live-verified end-to-end**: 2024-11-25/26 NIFTY
  ingested (750 spot + 10,500 option candles, 0 failures) with five 2026-06 control-day
  hashes byte-identical — the warehouse now starts 2024-11-25.
- **Deletable strategies**: the 11 non-confluence built-ins moved to
  `app/strategies/plugins/` as pure renames (origin flips to `custom` → retire AND
  delete); `confluence_scalper` stays the one permanent built-in (delete still 403s).
  Byte-identity proven by running the real backtest path for all 12 strategies over
  3,413 real candles before/after the move (identical trades/schemas/meta; source-sha
  pinning unaffected). The origin flip's safety gap is closed:
  `GET /strategies/{id}/references` reports the orphan blast radius (presets, backtest
  runs, optimizer jobs — no re-link path exists) and `DELETE /strategies/{id}` refuses
  with those counts until called with `?confirm=true`; the Strategy Library confirm
  dialog itemizes them.

## [0.49.3] — Intra-session gap indicator warm-up reset (2026-07-05)

**Silent correctness fix.** `warehouse.load_candles_df` returns raw rows with no
reindex to a complete minute grid, and `run_backtest` iterates positionally, so a
mid-session warehouse hole (a partial-day gap, or a half-day/closure boundary that
leaves a session partially present) made the post-gap bar positionally adjacent to
the pre-gap bar. The whole-frame rolling/EWM indicators (ATR/EMA/RSI/ADX/MACD/chop/
atr_avg/fvg/swing/velocity/variance_ratio/squeeze/supertrend/candle-geometry) then
computed **across the time discontinuity** with no NaN and no warning — contaminating
signals on bars adjacent to a gap. Whole missing *days* were already caught by the
day-level audit; intra-session partial gaps were not.

Fix: a per-bar `gap_before` flag (a >1-minute jump **within** the same IST session;
overnight/cross-date boundaries are intentionally not flagged, preserving the
existing overnight-carry contract) drives a `_reset_on_gap` wrapper that recomputes
each whole-frame indicator per gap-bounded segment, so post-gap bars re-warm from
NaN instead of smearing. A no-gap fast-path makes gap-free windows **byte-identical**
to the prior computation (proven by the indicator-equivalence + new parity tests),
and the fix is mirrored in `indicator_groups.py` so the optimizer/WFO memoized path
stays equivalent. Per-session indicators (VWAP/CPR/NR7/ORB/ToD) and per-row `regime`
are unchanged. `run_backtest` is untouched — post-gap NaN warm-up is the same
condition strategies already tolerate at frame start, so no trade fires on smeared
indicators. Paper/live enrichment inherits the fix: after a real-time data hiccup,
signals self-suppress until re-warmed. (`indicators.py`, `indicator_groups.py`,
`tests/test_gap_reset.py`.) Ref: `OPTIMIZER_VERDICT_2026-07.md` edge case #2 (that
research doc lives on `feat/exit-tiers-and-audit`; flip its edge-case-#2 row to FIXED
when that branch is integrated). Spec + plan under `docs/superpowers/`.

## [0.49.2] — STT rate updated to the current 0.1% (2026-07-04)

`option_costs.DEFAULT_STT_SELL_RATE` 0.0625% → **0.1%** (Finance Act 2024, effective
2024-10-01) — matches `live_friction_profile.STT_OPTIONS_SELL`. Affects every NEW
cost-enabled backtest and all paper-close charge calculations. Saved presets/runs that
serialized a full `cost_config` keep their stored rate for reproducibility; only configs
without an explicit `stt_sell_rate` pick up the new default. Exchange-txn rate (0.035%)
already matched the current NSE schedule and is unchanged.

## [0.49.1] — Required capital, drawer chart, charges everywhere, live trade history (2026-07-04)

Follow-ups from the 0.49.0 review, all four user-requested.

- **Required Capital** (per-deployment drawer): the minimum demat balance the account needed at the
  period's start to fund every trade in it — concurrent premium plus losses already banked (a loss
  taken before the next entry raises it above peak deployed). Sits beside Max Deployed.
- **Drawer chart**: two-axis line chart per deployment — Cumulative P&L (left) and Account value
  (right; starting capital, editable on the account card, + the deployment's own P&L) versus each
  trade chronologically; Daily/Weekly/Monthly/Yearly tabs, click a period row to chart it.
- **Charges everywhere**: paper closes now ALWAYS compute statutory charges (zero-brokerage
  Flattrade schedule: STT, exchange txn, GST, SEBI, stamp) — `realized_pnl` semantics unchanged
  (gross when friction off), new `net_realized_pnl` stored per trade. Paper blotter gains a
  Charges column with full breakdown tooltip + detail-drawer rows; per-strategy tables total them;
  the Backtest trades list gains a sortable Charges ₹ column. (STT default stays at the
  pre-Oct-2024 0.0625% — bumping to the current 0.1% would shift cost-model backtests; separate decision.)
- **Live trade history**: `GET /api/live-broker/trade-history` (paginated close-loop journal) + a
  Trade History table on the Live page stats card — entry/exit stamps, contract, lots, prices,
  realized P&L, exit reason; missing values render as "—", never fabricated. Manual broker-side
  trades stay in the Flattrade tradebook (not journaled by the app).
- Host suite **2942 passed** (2 pins updated to the new charges contract, +6 new tests).

## [0.49.0] — Backtest UX + optimizer honesty fix + Paper↔Live parity + live trade stats (2026-07-04)

Four user-requested slices across Backtest, Optimizer, Paper, and Live pages.

- **Backtest UX.** `NumberSliderInput` params are finally typeable (clamp moved from
  per-keystroke to commit on blur/Enter — fixes Backtest params, Optimizer trial budget,
  PreTradeChecklist); "Pair signals with option candles" defaults **ON**; new sortable
  **Opt P&L%** column ((Opt Exit − Opt Entry)/Opt Entry) beside Opt Exit; preflight button
  row no longer clips in the 320px aside.
- **Preflight ingest converges now.** The "Ingest missing & recheck" chain (a) syncs option-contract
  metadata first when contracts are missing — current + expired-in-window backfill — so
  `missing_contract` gaps actually fill; (b) ingests the configured moneyness band (OTM2/3, ITM2
  setups previously fetched contracts the backtest never used); (c) the panel polls the run and
  **auto re-checks** coverage on completion; (d) chain errors surface in the panel.
- **Optimizer honesty fix (behavior change, deliberate).** "Optimize indicator periods" had been a
  silent no-op since birth: `merged_params()` dropped the injected catalog params (rsi_length,
  macd_*, adx_length, …) in every path — including the optimizer's own trials — so displayed
  "optimized" periods were never applied. `merged_params` now accepts the shared indicator keys
  (single source: `indicator_groups.SHARED_INDICATOR_PARAM_KEYS`, drift-guarded against
  optimizer.py's literal). Re-running an old indicator-optimized preset now yields HONEST numbers.
  The Backtest form shows a read-only "Carried from preset: …" line for non-schema params.
- **Paper page.** Blotter "Strategy / Contract" split into two columns (18-col layout); Open P&L
  KPI moved left of Live MTM; **per-deployment drill-down**: Daily/Weekly/Monthly/Yearly stats
  (trades, net/min/max P&L, min/max deployment-isolated capital, max drawdown, peak deployed
  capital via sweep-line) — `deployment_period_stats` + `GET /api/paper/deployment-stats`.
- **Paper↔Live parity.** Overall controls (basket SL / target / trailing) now run for PAPER via a
  new `paper` scope on the same store + the pure live evaluator, evaluated from the
  LiveExitMonitor cycle (supervisor-reconciled; fail-closed on stale marks). Per-deployment caps
  editor (Live-deploy parity): lots/signal override (beats the pinned sizing replay),
  max concurrent, daily loss cap / max trades-day — `PUT /api/deployments/{id}/paper-caps`,
  enforced at entry in `paper_auto`. OCO backstop stays live-only (real resting broker orders).
- **Live page.** New **Live Trade Statistics** card: period P&L, win rate, profit factor and
  per-strategy breakdown over the journaled `live_trades` close-loop history
  (`GET /api/live-broker/trade-stats`, reusing the paper aggregators).
- Tests: +25 (pandas-drift fix, seam regression, period-stats math, parity enforcement, wiring
  pins) — host suite **2936 passed / 0 failed**.

## [0.48.3] — Deep cleanup: dead code, unused deps, disk bloat (2026-07-03)

Project-wide audit (multi-agent sweep, every deletion adversarially verified against dynamic
imports / test string-pins / Docker / docs) followed by removal. **No behavior changed** — the app
serves the same pages, routes, and features from slimmer sources and images.

- **Disk: ~1.44 GB → ~0.52 GB.** Deleted regenerable artifacts: host `frontend/node_modules`
  (~980 MB — Docker builds install their own; run `yarn install` in `frontend/` only if you want a
  host dev server), stale `frontend/build`, all `__pycache__`/`.pytest_cache` (~14 MB), an orphaned
  `.claude/worktrees` husk. The remaining ~514 MB is `.venv`, which the host test suite needs.
- **Dead code removed** (verified unreferenced twice, independently): 34 of 46 never-imported shadcn
  `components/ui/*` files (+ `hooks/use-toast.js`, `charts/MiniChart.jsx`), and
  `backend/app/models.py` (superseded by `app/schemas.py`). Kept deliberately: `MultiPaneChart.jsx`
  (earmarked for a deferred slice), `exit_controls_level.py` (parity-test pinned),
  `live/live_friction_profile.py` (awaiting live-readback work).
- **Dependencies slimmed, none upgraded.** Backend: 14 never-imported pins dropped (the unused auth
  stack `pyjwt`/`python-jose`/`passlib`/`bcrypt`/`email-validator`, Emergent leftovers
  `boto3`/`requests-oauthlib`/`jq`/`typer`, plus `python-multipart` and the lint set
  `black`/`isort`/`flake8`/`mypy`); `scikit-learn` was flagged too but **kept** — adversarial
  verification found optuna's param-importance imports it lazily at `optimizer.py:346`. Frontend:
  38 packages dropped (18 unused `@radix-ui/*`, the react-hook-form/zod chain, `framer-motion`,
  `cra-template`, the config-less ESLint-9 flat set, and `@emergentbase/visual-edits` — an external
  tarball fetched from `assets.emergent.sh` on every image build); `dotenv` is now declared
  (craco requires it; it previously resolved only via hoisting).
- **Emergent-platform leftovers removed:** root `.gitconfig` (inert agent identity), `.emergent/`,
  empty `test_reports/`, stock CRA `frontend/README.md`.
- **Fixed:** the two `test_structural_features.py` failures on the host (pandas 2 vs 3
  None-vs-NaN drift in object columns) — suite now fully green in both environments; stale doc
  pointers (`plan.md`, `start.bat`, ltm recall file) repointed to the canonical read order.
- **Untouched by design:** all warehouse/paper/live data (Mongo volume `mongo_data`),
  `Strategy_note_self/`, `docs/Resources/flattrade-pi-api/` (+ source PDF), `memory/`,
  the unmerged `feat/adaptive-strategies` XRS branch, and both stashes. `.kiro/` + `ltm/`
  (Kiro-IDE-era memory tooling, idle since 2026-06-12) left in place pending a user decision.

## [0.48.2] — Documentation refresh + repo tidy-up (2026-07-01)

Consolidated and refreshed the documentation so a new engineer/agent can onboard from a single guide,
and removed historical cruft. **No application code changed.**

- **Docs refreshed** (each drafted grounded in current code, then fact-checked against source):
  `README.md`, `docs/HANDOFF.md` (now a concise START-HERE with a where-to-go-deep index),
  `docs/ARCHITECTURE.md` (added the live-execution L0–L3 gate chain + the data-warehouse completeness
  model), `docs/API_REFERENCE.md` (all ~171 routes incl. the live-deploy set), and
  `docs/STRATEGY_DEPLOYMENTS.md` (dropped the retired approval-flow framing; documents armed
  auto-place + OCO/GTT backstop + `live_position_guard`).
- **New:** `docs/DEVELOPER_GUIDE.md` — the single consolidated onboarding guide: run/build/test
  workflow (the four-tier test pyramid), the **live-trading safety model**, the data-warehouse model,
  India trading rules, the research→deploy flow, and gotchas/known-issues.
- **Removed** (all for already-merged features; preserved in git history): the 65 one-off design
  specs + implementation plans under `docs/superpowers/`, the one-off `docs/experiments/` audit, and
  the stale unreferenced `design_guidelines.md`. Also cleared ~27 MB of untracked local build
  artifacts (the Flattrade `_build/` PDF-extraction dir, `__pycache__/`, `.pytest_cache/`).

## [0.48.1] — Backtest: stop silently dropping newest option candles at the load cap (2026-06-30)

Fixes a **silent data-loss** bug in the paired-option backtest. `options_1m` candles were loaded
oldest-first under a 1,000,000-row cap, so a long multi-strike range dropped the **newest** candles
and the most-recent trades paired 0% with no warning. The cap is raised to 4,000,000
(`OPTION_CANDLE_LOAD_CAP`, matching `optimizer`/`wfo`), a cap-hit now logs a warning and surfaces
`candles_capped` in the backtest response, and the Backtest Lab **Option Execution** card banners it
so the result is never silently incomplete. (Merged from `fix/option-candle-load-cap`, originally
authored 2026-06-16 off an older `main`.)

## [0.48.x] — Strategy → Deploy to Live: armed auto-place within caps (2026-06-25, BUILD COMPLETE; live readback pending)

Lets a **deployed backtest strategy** route its **continuous live signals** through the existing
Live order choke-point + software guard to place **real Flattrade orders** under hard per-deployment
+ account caps — instead of only auto-paper-trading. Built brainstorm → spec → plan → subagent-driven
TDD (per-task spec+quality review + a final holistic review). **2370 host tests pass; FE build clean.**
On branch `feat/strategy-deploy-to-live` (NOT merged/pushed). Spec/plan in
`docs/superpowers/{specs,plans}/2026-06-25-strategy-deploy-to-live*.md`; agent memory
`strategy-deploy-to-live-2026`.

- **User-authorized rule change:** the prior "user does every Place click" rule is replaced (the
  user's explicit decision) by **per-deployment ARM** — the human arms a deployment with caps and the
  backend then auto-places within that envelope. The assistant still never personally transmits.
- **Authorization** (`mode.is_deployment_live_allowed` + `deployment.risk.live`): per-deployment arm
  (armed / armed_until / lots / max_lots_per_day / max_concurrent / daily_loss_cap), **daily
  auto-disarm** at 15:00 IST / token expiry. Routes `POST /deployments/{id}/live/{arm|disarm|stop}`
  (typed-confirm arm; stop flattens this deployment's live positions + disarms), `GET …/live/status`,
  and `/deployments/stop-all` extended to disarm+flatten live. Account ceiling `max_lots_per_order`
  (default 20) added to the safety-config.
- **Entry** (`executor.place_deployed_order`, a sibling of `place_live_test_order` sharing the single
  `client.place_order` via the extracted `_transmit_and_arm`): gate chain = long-only (BUY) →
  per-deployment arm → fresh dry-run (`lots=capped`, `fat_finger_cap=account ceiling`) → full-size
  margin → all-verdicts → `qty==capped×lot` ceiling re-check → `can_trade` → RateThrottle (real
  transmits only) → **offline-first transmit boundary** (dry-run-logs unless `LIVE_AUTOPLACE_ARMED=1`)
  → arm-or-abort. Per-deployment lots are the USER's value (not strategy sizing), clamped to the
  ceiling.
- **Continuous routing** (`auto_live.py` + the `evaluate_active_deployments` tee): an armed deployment
  routes its CONFIRMED signal to `auto_live_trade_for_signal` (a clone of `paper_auto`) and
  **suppresses paper** (replace; the SAME atomic `paper_trade_claim` → one trade per signal). Entry
  uses a **FRESH** option-premium tick only (refuses a stale candle; never spot). `live_deploy_governor`
  enforces max_concurrent / max_lots_per_day / daily_loss_cap (a loss breach pauses ONLY that
  deployment). New `live_trades` collection.
- **Full exit parity** (`live_position_guard` extended): the guard now also reads the live spot tick
  and enforces **spot-mirror + time-stop** exits (matching the paper marker) plus a **15:00 IST EOD
  square** for deployed (MIS) positions, on top of premium SL/TP/trailing. Manual LIVE_TEST positions
  are untouched (keep their 10-min cap). **A deployed position is never unprotected:** a 50% premium
  catastrophe stop is always seeded when the strategy configures no premium stop, and guard
  registration is mandatory (failure → executor abort-protect: square + halt).
- **Frontend** (`components/live/DeployToLivePanel.jsx` + `LiveDeploymentStrip.jsx` + `LiveBanner`):
  caps form clamped to the account ceiling → danger typed-confirm ("type ARM") → arm; a Live
  Deployments strip (armed_until countdown, today's orders/lots/₹, open positions, Disarm/Stop, master
  Stop-all-live); banner shows the armed count + a DRY-RUN warning when `LIVE_AUTOPLACE_ARMED` is unset.
- **Two independent offline-first env kills:** `LIVE_AUTOPLACE_ARMED` (entries) + `LIVE_GUARD_ARMED`
  (automatic squares). With both unset nothing transmits. Manual square / kill / live-stop are
  user-initiated exits and transmit directly (like the manual ticket).
- **Pending (plan Task 16, user-gated):** Docker dry-run verification + a **user-supervised 1-lot live
  readback**. Known low-severity follow-ups: no explicit short-strategy arm guard (moot — signals are
  single-leg CE/PE BUY only); `disarmed_reason` not proactively written for eod/token-expiry (the
  predicate fails closed).

## [0.47.x] — Live Trading (Flattrade) real execution + GTT/OCO backstop (2026-06-22 → 06-25)

A new **Live Trading page** that places *real* orders via **Flattrade** (Noren/PiConnect OMS;
Upstox stays data-only), built safest-first with adversarial audits. `main == origin/main`
(pushed); this work plus the Paper-page redesign and Strategy-Library/AI-authoring are unified
on one `origin/main`. Full state + file map: `docs/HANDOFF.md` §2 (2026-06-25 block) and agent
memory `live-execution-build-2026`.

- **Execution path** (`backend/app/live/`): thin async `FlattradeClient` (form-encoded
  `jData=…&jKey=…`, IPv4-forced for the static-IP whitelist), `order_builder` exchange-aware
  choke-point (tick-rounded, freeze-qty split, LMT/SL-LMT only), and the L1–L3 gate chain
  (`mode` single-shot LIVE_TEST, `margin` fail-closed, `safety`/`idempotency`, `auto_square`
  ≤600s cap, `executor` 7-gate arm-or-abort, `reconcile`, `kill_switch`). Frontend
  `frontend/src/components/live/*` + `pages/LiveTrading.jsx` (`/live-trading`). **Hard invariants
  in code:** entry only in LIVE_TEST single-shot, ≤1 lot (3 layers), long-only default,
  arm-or-abort (no orphan), exit-parity reuses `execution_policy`. **The assistant never
  transmits or squares a real order — the user does the Place click.**
- **Live-validated** (SENSEX CE/PE 1-lot, squared clean). Fixes from live tests: **tick-rounding**
  (Decimal-round every prc/trgprc to the scrip `ti`) and a **phantom-timer** (broker returns an
  order# then async-REJECTS → `/test-session` reads the order book and resolves rejected entries).
- **Software exit guard** (`live_position_guard.py`, started in `server.py` lifespan) **replaces
  resting SL-LMT backstops** — a resting SL on a short option margin-rejects (~₹1.8L naked-short
  SPAN an option-buyer lacks, *proven live*). The guard reads the **broker position book ~1.5s**,
  evaluates stop/target/trailing + overall-basket (`overall_controls.py`) in software, and squares
  via cancel-all-then-close. Offline-first: dry-run unless **`LIVE_GUARD_ARMED=1`** (armed);
  configurable guard stop (default 50%). Execution-mode switch + approval queue retired for
  one-click direct place.
- **GTT/OCO backstop (`gtt.py` + `FlattradeClient.{place,cancel}_gtt/oco`, `gtt_book`)** — the NRML
  "PC-died" net (resting GTT/OCO blocks no margin → immune to the naked-short trap). Schema = the
  vision-verified PiConnect catalog (`docs/Resources/flattrade-pi-api/`, 58 endpoints +
  `catalog.json`, decoded from the 93-page PDF with a reusable geometric extractor). **Real-money
  `ai_t` values CONFIRMED by reading the user's own placed orders back from the live broker:**
  single GTT below-trigger = **`LTP_B_O`** in the **flat** request form (catalog #16; the wrapped
  `oivariable`/`place_order_params` shape is only what `GetPendingGTTOrder` *returns*); OCO =
  **`LMT_BOS_O`** with `oivariable` `x↔leg1`/`y↔leg2` (positional) and direction inferred from
  trigger-vs-LTP (catalog #21 + fired-leg `remarks` "Ltp X is below/above Y"). Routes: GET
  `/live-broker/gtt` lists the resting book (single GTTs *and* OCOs), POST builds + transmits on
  explicit `transmit=true`, DELETE cancels via `?kind=`. `GttBook` panel surfaces the recorded
  `ai_t` for read-back confirmation. Only `LTP_A_O` (single above-only target) stays inferred.
- **Other:** configurable basket overall SL/target/trailing/re-entry (`overall_controls.py` +
  `overall_settings_store.py`, per-deployment + broker-level scopes); `PayoffChart` option payoff;
  hero metric strip + reconcile panel; `BacktestRunJournal` gains sortable columns.

## [0.46.x — WIP] — Scenario-adaptive framework groundwork (2026-06-18)

On branch `feat/scenario-adaptive-framework` (off the unmerged stack; spec + proof-first
plan committed first). Groundwork for routing option-buying strategies on a market
**scenario** (opening-range regime). Committed so far:

- **Causal `orb_width` indicator group** (commits 2422609, ebcc04b): a keyed
  group (`("or_minutes",)`) producing `orb_width_pct_partial` (known only from the
  `or_minutes` cutoff bar onward — no look-ahead) and `orb_width_pct_prior` (the prior
  completed session's settled width, shifted across sessions; the **first** session of a
  window is NaN — guards the `order[-1]` negative-index leak). Golden byte-parity with the
  monolithic reference held across the param sweep.
- **Pure market-scenario classifier** (`feat(scenario)` ddcb16e): `app/scenario_classifier.py`.
- **Optimizer cache-key fix** (`fix(optimizer)` 1c7af80): `or_minutes` added to
  `INDICATOR_PARAM_KEYS`. Without it, two trials that differ **only** in `or_minutes` collide
  on one `enriched_cache` key (`_indicator_key`) and the second silently reuses a frame whose
  `orb_width` columns reflect the wrong `or_minutes` — the "optimizes against frozen
  indicators" bug class. Hardened beyond the one param with an **import-time guard**: every
  memoized `indicator_groups.GROUPS` param must appear in `INDICATOR_PARAM_KEYS` or import
  raises, so future keyed groups can't silently drift. `or_minutes` is deliberately **not** in
  `INDICATOR_PARAM_CATALOG` (not auto-swept unless that product decision is made). Regression
  test: `test_optimizer_indicator_keys.py::test_or_minutes_registered_in_keys_tuple`
  (text-scan, so host tests never import `optuna`).

### Problem found & fixed this session — pandas 3.0 µs-resolution epoch trap

The project `.venv` is now on **pandas 3.0.3 / numpy 2.4.6**, where
`pd.date_range(freq="1min")` builds a **microsecond**-resolution index (`datetime64[us]`).
`tests/_adaptive_testutil.py:make_ohlc` computed `ts = idx.asi8 // 1_000_000` assuming
nanoseconds, so on a `us` index it produced epoch-**seconds** (`1736135100`) instead of ms
(`1736135100000`). Downstream `pd.to_datetime(ts, unit="ms")` mapped every bar to ~1970 and
collapsed all three fixture sessions into one — failing the two `orb_width` causality tests
with `assert 1 >= 3` ("fixture must have >=3 sessions"). **Fix** (commit `3f0ff71`): pin the
resolution first — `ts = idx.as_unit("ms").asi8` — correct whether pandas builds the index at
ns (≤2.x) or us (≥3.0). Verified:
`pytest tests/test_optimizer_indicator_keys.py tests/test_indicator_equivalence.py -q` →
**9 passed**; all 8 fixture-using test files → **35 passed**. Production
`app/yfinance_source.py` audited and **safe** (it pins `astype("datetime64[ns, UTC]")` before
`// 10**6`). Gotcha recorded in agent memory `pandas3-resolution-epoch-trap`.

> Branch has since advanced past this groundwork (per-scenario `exit_plan` dispatcher, Signal/
> Trade scenario plumbing, scenario-classifier coverage); those land in a later CHANGELOG entry.

- **Paper-deployment sizing replay** (`feat/paper-sizing-replay`): paper deployments now
  replay the source run's sizing policy instead of a fixed `default_lots`: the run's
  `sizing_config` + `lots` are pinned (immutable) on `deployment.risk.sizing` at create, and
  `build_auto_trade` recomputes lots per signal via `size_position()` (premium-at-risk →
  lots scale with capital/risk%/the option stop; `lot_size` from the live contract so
  SENSEX/BANKNIFTY adapt). Legacy deployments and pre-capture presets fall back to
  `default_lots`, shown explicitly in the deploy wizard's read-only sizing summary.

## [0.45.x] — Trustworthy validation loop (Piece 3) (2026-06-17)

724 host tests pass (was 708; +16). On branch `feat/integrated-validation-loop` (merged
local-only into `feat/backtest-exit-controls`). Makes the existing `deployment_quality`
trust verdict **correct** and **omnipresent** — fragile / account-ruining configs are now
flagged at every surface (backtest results, optimizer promotion, deploy), **advisory and
never blocking**. Built spec → plan → TDD, hardened by **three adversarial multi-agent
audit passes** (44 findings incl. 6 blockers — e.g. Fix-A would 400→spot-only on overlay
survivors; the deploy gate read the wrong number; `_save_best_as_backtest` had a 2nd
caller in `wfo.py`). Origin: a survival-gated hunt kept producing *fragile* survivors
(OOS-positive but full-window option-₹ negative), and the manual cross-check that caught
them was not in the pipeline.

- **Fix-A (optimizer):** `_save_best_as_backtest` now replays the **promoted** config
  (params + chosen exit-controls) through `_run_paired_option_backtest(validate=False,
  auto_fetch=False)` and stores the full-window option result on the saved best run; finalize
  attaches `best_quality` + `best_option_pnl_value`. `validate=False` is load-bearing — the
  grid-derived `spot_exit`+`exit_controls` overlay would otherwise 400 and silently fall
  back to spot-only. Conditional doc key ⇒ spot mode byte-identical.
- **Fix-B (`app/deployment_quality.py`):** three self-contained option-₹ checks read from
  `source_doc.option_backtest` — `option_full_window_negative` (with a "fragile: OOS-positive
  but full-window-negative" escalation), `ruin_floor_breach` (equity ≤ floor / negative /
  DD≥100%), `coverage_attrition` (over the cap-excluded addressable denominator). A dedup
  suppresses the legacy evidence-driven `option_oos_negative` when a self-contained result is
  present; 2 new tunable thresholds (`ruin_floor`, `min_coverage_ratio`). 12 TDD tests.
- **Fix-C:** `GET /backtest/runs/{id}` attaches `quality` (compute-on-read; retroactive); a
  reusable `TrustScorecard` (green/amber) renders on the backtest results page + the optimizer
  promotion panel.
- **Fix-D (`deployments._gather_deployment_evidence`):** the deploy gate now reads the
  **promoted survivor's** full-window net (`job.best_option_pnl_value`, + a projection fix) —
  not `rerank.ranked[0]` (the base-config, non-survivor candidate).
- **Runtime:** added `validate: bool = True` to `_run_paired_option_backtest` (default keeps
  existing callers identical; the optimizer passes `False` for trusted internal replays).
- **Finding (recorded in memory `option-buying-edge-hunt-2026`):** across confluence_scalper /
  SEB / ORF × ATM/ITM × multiple 2025-26 NIFTY windows, the survival-gated optimizer produced
  **no deployable survivor** — every candidate was fragile (OOS+ / full-window−). ITM made SEB
  *worse*, proving the bottleneck is **directional signal quality**, not theta/moneyness/sizing.
  The gate correctly refused to promote a money-loser. No strategy was deployed.

## [0.44.x] — Backtest-page Exit/Risk panel (UI exposure of Piece 2) (2026-06-16)

On branch `feat/backtest-exit-controls`. Exposes the Piece-2 exit/risk overlay (premium
trailing-stop, breakeven, per-day loss/target/max-trades caps) as an **optional, off-by-default**
panel in the Backtest Lab, so a strategy can be backtested with controlled exits and the effect
seen trade-by-trade — threaded through all six config/save/prefill paths (defaults, panel,
`buildPayload`, `buildExecutionFromConfig`/`FromRun`, apply-preset + clone-run prefills). Off ⇒
byte-identical (gated emission; never an empty `{}`). Hardened by a 14-finding spec audit. Two
integration fixes shipped with it: **(a)** `PerformanceOverview` read exit-control attribution
from the spot metrics (`result.metrics`) instead of the option metrics
(`result.option_backtest.metrics`) — the block had been silently dead since Piece-2 Task 12;
**(b)** the async `POST /backtest/start` path now validates the overlay at submit (clean 400)
instead of failing the run in the worker (`.model_dump()` the pydantic sub-models; gate on
`option_backtest.enabled`).

## [0.43.x] — Adaptive regime strategies (SEB / ARS / ORF / GAP / XRS) (2026-06-16)

On branch `feat/adaptive-strategies`. Five regime-adaptive intraday option-**buying** strategies
for **NIFTY + SENSEX** on a "measured-edge" framework (Movement × Direction × Speed; edge-decay
exits; ATR/σ/percentile-relative thresholds for SENSEX-portability): **SEB** (squeeze→expansion),
**ARS** (variance-ratio soft-blend regime switch, flagship), **ORF** (opening-range fade/break),
**GAP** (gap-fade), **XRS** (NIFTY↔SENSEX cross-index lead/lag). Shipped MODULAR: drop-in single
`.py` files in `builtin/` (auto-discovered, plugins-compatible) over a shared causal-indicator
toolkit — new pure indicators (velocity/accel, variance_ratio, Bollinger/Keltner/squeeze,
supertrend, price-based VWAP σ-bands, NR7) + `cpr.py` (CPR + day-type) + `vol_seasonality.py`
(causal intraday time-gate) wired into `precompute_all_indicators`; `AdaptiveStrategyBase`
(time-gate + mode-aware speed-confirm + ATR exits). XRS uses an "extra columns only"
`attach_companion_rs` so `run_backtest` stays untouched. Built spec → 4-plan decomposition →
subagent-driven TDD; docker-smoked on real NIFTY+SENSEX frames (scale-free confirmed). All 5 live
in `/api/strategies`. (Plan 4 — edge-gate WF ₹-expectancy + edge-proportional sizing — deferred.)

## [0.42.x] — Exit/Risk Controls (Piece 2) — Commit 1: enforce + evaluate (2026-06-16)

654 host tests pass (was 612; +42). On branch `feat/exit-risk-controls` (stacked on
`feat/live-tick-paper-realism`). Premium-axis trailing-stop + breakeven + soft per-day
loss/target/max-trades caps as ONE execution overlay — enforced in the backtest sim,
scored by the survival gate, and enforced live. **Off by default ⇒ byte-identical.**
Built spec → plan → TDD task-by-task, each with per-task spec + code-quality subagent
review. Hardened by a 40-finding spec audit + a 25-finding plan audit (caught the
max_trades off-by-one, the sim↔live count convention, the real `runtime.py`/`wfo.py`
sim call sites, the overlay-conduit-into-survival gap).

- **New pure `app/exit_controls.py`** (host-testable, no motor/optuna):
  `effective_premium_stop` (ratcheted long-option stop = max(base, breakeven, trailing);
  pct/pts units), `daily_governor_decision` (sticky cumulative-realized extremum +
  already-admitted entry count), `validate_exit_risk_config` (per-path costs +
  option-exec + range guards), `stop_fill_price` (gap-open-clamp), exit/skip reason
  constants.
- **Sim** (`option_backtest.simulate_paired_option_trades`, one additive default-None
  `exit_controls`/`daily_caps` kwarg pair): trail/breakeven in `_walk_option_exit` —
  **look-ahead safe** (running-max advanced AFTER each bar's exit check, so the peak
  bar can't self-stop) + gap-fill at the bar open; **entry-session daily governor** in
  the pairing loop (`SKIPPED_DAILY_CAP` non-PAIRED status, `skipped_by_cap` coverage);
  control-attribution metrics.
- **Survival/optimizer**: cap-skips excluded from the `MIN_COVERAGE` denominator (a
  deliberate halt can't trip `low_coverage`); the overlay is forwarded into BOTH
  finalist sims (`_survival_eval_oos` + `_option_rerank`) so survival scores the
  overlay-applied ₹ curve; `wfo` + `preset_execution` carry it; the saved best
  persists it.
- **Live**: per-tick trail/breakeven ratchet in `mark_open_deployment_trades` (raise
  `risk.stop_price` from the PRIOR running-max → decider parity with the sim → single
  status-conditional write); soft entry-session daily governor
  (`deployment_kill_switch.check_soft_daily_governor` — counts OPEN+CLOSED entries
  today, sticky closed_at-order realized extremum, auto-resets next session, blocks
  entries only, distinct from the existing hard kill-switches).
- **Config + validation**: `ExitControlsReq`/`DailyCapsReq` + reason/metric constants
  in `schemas.py` (contract-pinned); 400s on the backtest (`runtime.py`), optimizer
  (`optimize_start`), and deployment-create paths with per-path cost flags; `SKIPPED`
  rows segregated out of the public `trades` response into `skipped_trades`. UI:
  deploy-wizard Exit/Risk panel (writes `risk.exit_controls`/`daily_caps`; ₹ caps
  gated behind costs) + backtest attribution display (`PerformanceOverview`).
- **Parity** claimed at the DECIDER level (shared pure functions return identical
  results for identical inputs); bar-vs-tick fill differences are documented not
  eliminated (pre-existing for the fixed stop, amplified by trailing; the open-clamp
  keeps the sim no rosier than live).
- Spec [docs/superpowers/specs/2026-06-15-exit-risk-controls-design.md], plan
  [docs/superpowers/plans/2026-06-15-exit-risk-controls.md].
- **Deferred to Commit 2:** the bounded finalist-grid SEARCH over exit configs
  (`search_exit_controls` flag, schema already present); the "₹-impact vs the same
  finalist without the overlay" attribution estimate.
- **Pending the user:** running-stack verification (docker rebuild; force a live
  trail-stop + a daily-loss halt; confirm auto-resume next session).

### Commit 2: auto-search (2026-06-16)

657 host tests pass. On branch `feat/exit-risk-controls`. When `search_exit_controls`
is on (plus Survivability ON + option re-rank), the optimizer sweeps a BOUNDED,
deterministic grid of exit configs per SURVIVING finalist and keeps the
best-surviving one (`chosen_exit_controls`) — the survival gate is the overfit
guard; the search runs only on survivors so it can never promote a disqualified
candidate.

- **New pure `exit_control_grid`** in `app/exit_controls.py` (host-testable, no
  motor/optuna): fixed-order Cartesian product of trail-distance × breakeven-trigger
  candidates, capped at `max_grid=12` via a deterministic uniform stride — no RNG;
  spec via `option_config.exit_control_search`. Default grid: `trail_distance
  [0.20, 0.35]`, `breakeven_trigger [0.0, 0.30]` (fractions of premium, unit=pct).
- **Optimizer wiring** (`optimizer.py`): `search_exit_controls` flag threads through
  the option re-rank path; per-finalist grid sweep on survivors only; result carries
  `chosen_exit_controls`. **Dormant by default** (verified by `py_compile` + the
  running stack).
- **Optimizer UI** (`Optimizer.jsx`): exit-control search toggle (gated on
  Survivability + option re-rank) + optional custom grid-bounds inputs (fractions)
  + per-finalist "auto-tuned exit" display; hint/placeholder text corrected to show
  the real fraction defaults (0.20, 0.35 / 0.0, 0.30) not the previous wrong
  whole-percentage claims.
- **Pending the user:** run an optimization with Survivability + `search_exit_controls`
  on; confirm survivors carry an auto-tuned `chosen_exit_controls` config.

## [0.41.x] — Live tick-driven paper-trading realism (2026-06-15)

612 backend tests pass (+ a live-engine suite). On branch `feat/live-tick-paper-realism`.
Brainstormed + researched (how Streak/retail platforms work) → spec → plan → built TDD
task-by-task with per-task spec + code-quality review. Goal: make deployed-strategy
paper trading reflect *live* execution so the Signal Journal + Paper page are
trustworthy forward-test evidence (before any future broker API). **No broker orders.**

Decision (researched): entries stay **candle-close** (matching the 1-minute backtest, no
repainting — retail platforms signal on OHLC close, not LTP); ticks drive **fills and
exits**, not entry evaluation.

- **`LiveExitMonitor` (new)** — a ~1.5s loop that marks every OPEN paper trade to the
  live premium and auto-closes on stop/target/spot-mirror/time-stop, replacing the old
  once-per-minute mark. A stop breached at 10:32:15 now closes at ~10:32:16 at the real
  premium instead of ~10:33:10 at a drifted price. `/api/live-exit-monitor/status`.
- **Entries ~2–3s after close** — the evaluator now fires on each *new closed* 1-min bar
  (poll-for-new-bar) instead of a fixed `minute+10s`; the roller flushes the bar ~1s
  after close, so signals are prompt and never evaluated on the forming bucket. The
  15:00 IST square-off was moved to run every cycle (time-based safety).
- **Union subscription** — the option-stream auto-follow now subscribes
  `{ATM band} ∪ {every open-trade contract}`, so a position whose strike drifts out of
  the band stays markable (its stop can't blow past un-monitored).
- **Tick-level time-stop + exit-friction parity** — `time_stop_minutes` is enforced at
  the live premium (and `risk_hints` is now threaded onto the trade so it actually fires
  live); every close routes through `close_trade → close_economics`, so exit
  slippage/spread/charges match the backtest on all paths.
- **Live Paper page** — `GET /paper/open-positions` computes unrealized P&L from the
  latest tick at request time; the page polls it every ~2s (live per-trade P&L, premium,
  distance-to-stop/target, stale flag, Open MTM) while the full journal/stats stay at 30s.
- **Verified in the running stack:** exit monitor cycling ~1.5s; **25 trades closed
  today (15 target_hit / 10 stop_hit), all `exit_price_source=live_tick`**; the
  open-positions feed serves live P&L. (Also fixed earlier on this branch's base: the
  `option_no_data` false-block and the optimizer resume 404 — unrelated pre-existing bugs.)
- **Deferred:** tick-native intra-bar entries (a different product needing a tick
  backtest); a forward-vs-backtest parity scorecard; broker-API live execution.

## [0.40.x] — Survivable optimization (capital-aware, risk-constrained) (2026-06-15)

600 backend tests pass (was 575; +25). Piece 1 of the Optimizer↔Backtest track,
adversarially designed (a self-review + an 8-agent audit that caught 4 blockers),
then built TDD task-by-task with per-task spec + code-quality review. On a feature
branch (`feat/survivable-optimization`); **default-off → byte-identical to today**.

The optimizer maximized a **spot-index-points** objective, but ruin happens on the
**₹-capital option-equity curve** — so a strategy could win the metric and still
bankrupt the account (the user's −₹49k run). Survival mode fixes that.

- **New `app/survival.py`** (pure, host-tested): `calmar` (floored denominator),
  `monte_carlo_risk_of_ruin` (seeded **per-day** bootstrap — preserves loss-streak
  clustering — with a fail-closed upper-CI), and `survival_verdict` — the gate that
  runs guards then, in priority order, an **absolute ₹ equity floor (primary)**, a
  **magnitude drawdown-% cap**, and **risk-of-ruin**. (The DD compare is on
  `abs(max_dd_pct)`: a naive `dd <= cap` passes every blown account, since the value
  is negative; a NaN dd is also rejected.)
- **Per-fold OOS evaluation**: the gate runs on each walk-forward **out-of-sample**
  slice (not the in-sample window the search already mined — the key anti-overfit
  fix), with RoR on the stitched OOS series. Survivors must also be **profitable**
  (`total_return_pct > 0`) to be promoted.
- **Wired into the optimizer's option re-rank** (`_survival_eval_oos`, reusing the
  single option-candle load): each top-K finalist is gated; **profitable survivors**
  are ranked by a **configurable objective (Calmar default / Total ₹)** — both now
  fenced by the survival constraints. **Zero survivors → `done_no_survivor`** +
  `survival_summary{reason_counts, suggestions}`; it never promotes a disqualified
  candidate. The same DTE filter as the re-rank is applied so survival evaluates the
  exact contract set the finalist was ranked on.
- **Validation (`/optimize/start`, 400s)**: survival mode requires `option_rerank` +
  option execution + `costs_enabled` (else RoR/Calmar run on gross P&L) + sane
  `ruin_floor`/caps. New `SurvivalConfigReq` schema.
- **Optimizer UI**: a Survivability setup panel (toggle + floor/DD%/RoR + objective,
  shown only in option_rerank mode), per-finalist **Survived/Disqualified** badges, a
  **return-vs-drawdown scatter** (inline SVG), and an honest **no-survivor** banner.
- **Verified end-to-end** in the running stack: the validation 400s fire; a real
  survival optimization ran (validation gate → option re-rank → survival stage →
  `done_no_survivor`); the absolute floor correctly disqualified 4 finalists whose
  **OOS** equity went to −₹336k…−₹811k though they looked fine in-sample — the
  overfit-exposure working as designed. (Frontend pending the user's visual check.)
- **Deferred**: Approach B (capital-aware *trials*) if survivor density proves thin;
  pieces 2 (exit/risk controls) + 3 (integrated loop) as their own specs. See
  `docs/superpowers/specs/2026-06-14-survivable-optimization-design.md` (in git history).

## [0.39.x] — Backtests run as a background job (async, non-blocking) (2026-06-14)

575 backend tests pass (was 571; +4 incl. the job-contract test). Phase 1 of the
"run backtests efficiently like the Optimizer" track — the **core of the #3 future
target** is now done; pause/resume + multi-core remain explicitly deferred.

- **`POST /backtest/start` (fire-and-poll)** — the synchronous `POST /backtest/run`
  ran `run_backtest` + `walk_forward` **inline on the event loop**, freezing every
  other request for the whole run (and a 60s client abort + retry could double-run).
  `/backtest/start` now inserts the run doc up front with `status:"running"` (so a
  crash/failure leaves a visible record), launches `run_backtest_job` via
  `asyncio.create_task`, and returns `{run_id, status}` instantly. The worker runs
  indicators + `run_backtest` + optional `walk_forward` inside **one
  `asyncio.to_thread` hop** (off the loop), then `$set`s the full result +
  `status:"done"`; on error it `$set`s `status:"failed"` + `error`. Legacy
  `POST /backtest/run` is kept unchanged for scripts/curl.
- **Frontend polls instead of holding a request open** — `BacktestLab.runBacktest`
  calls `api.startBacktest` then polls the existing `GET /backtest/runs/{id}` every
  2s until terminal (done/failed); the eased bar stays as a "working" indicator. This
  removes the **`timeout of 60000ms exceeded` / duplicate-on-retry** class entirely.
  Preset re-run and `?run=` replicate flow through the same path. Verified in the
  running stack: a browser run hits `/backtest/start` → 2 polls → result (`1841
  trades`), no legacy call, **no console errors**; API lifecycle confirmed end-to-end
  (instant `queued` → `running` → `done`, full metrics + walk-forward).
- **Deferred by design** (HANDOFF §14): real per-step progress + cancel (Phase 2);
  `JobsProvider` + restart-reconcile of orphaned `running` docs (Phase 3); true
  multi-core via `ProcessPoolExecutor` (Phase 4). Pause/resume + fold-level
  crash-resume were assessed and **dropped** — a single backtest is one monolithic
  loop, not discrete optimizer trials, so they don't transfer.

## [0.38.x] — Saved Presets page, auto run-names, save-as-preset, long request timeouts (2026-06-14)

571 backend tests pass. A batch of UX + workflow improvements (all frontend except
the preset source tag + the timeout review). See HANDOFF §14 for the open #3 target.

- **Saved Presets page** (`/presets`, below Optimizer): every preset grouped into
  *From Optimizer* / *From Backtest Lab*, with rich cards (strategy · instrument,
  optimizer provenance, an option-execution summary or amber "spot-only", a green
  "Deployed" badge cross-referenced from live deployments, expandable params), header
  stats, search + filters (source / instrument / deployable / deployed) + sort, and
  per-preset Deploy / Open-in-Lab / Rename / Duplicate / Delete. **Advanced extras:**
  a per-preset **validation badge** from `/deployments/readiness` (honest-WFO + option-
  rupee OOS → Validated / Partly / Weak / Unvalidated), **multi-select + bulk delete**,
  and **compare two** (params side-by-side, diffs highlighted, + execution). Source is
  inferred + now explicitly tagged (`config.source` on both save paths).
- **`Save as preset` on a loaded backtest result** — saves the run's exact params +
  option execution (from the run doc), and loading a run now also restores its matching
  **pretrade profile** so `?run=` → Run replicates exactly (verified: every metric
  identical incl. option net ₹312,841.78).
- **Auto run-names** in Backtest Lab + Optimizer setup: a descriptive + timestamp
  default (`strategy · instrument [· objective] · YYYY-MM-DD HH:MM:SS`), edit-aware,
  regenerated after each run — so a forgotten default never saves duplicates.
- **Long request timeouts (#1)**: heavy synchronous endpoints (backtest run, warehouse
  sync, data-hygiene scans, audits) get a **per-request 10-min** timeout (`LONG_TIMEOUT_MS`,
  build-time `REACT_APP_API_TIMEOUT_LONG`); the global stays 60s. Fixes
  `"timeout of 60000ms exceeded"` on large-date-range backtests — which was the **axios
  client** timeout, not a backend one (the backend has no request timeout and still
  saves the run after a client abort). **#3 future target** (HANDOFF §14): convert
  `/backtest/run` to the optimizer's fire-and-poll job pattern.

## [0.37.x] — Live-realism & gate-rigor hardening (6 slices) (2026-06-13)

566 backend tests pass (was 533; +33). A multi-agent review of the whole app
(features, code quality, day-trader fit) found the engine is honest about WHEN to
exit and WHETHER data exists, but not yet about AT WHAT NET PRICE the live path
fills, nor about out-of-sample edge at the deploy gate. Six slices, each its own
commit, each with the user-choice principle (tune a parameter / override / analyze
the output) rather than silent behavior changes:

- **Live-friction parity** (`4bc7df3`): the live paper path booked GROSS fills
  while the backtest is net-of-friction, so forward P&L overstated the backtest.
  New `app/live_friction.py` is the single source of the entry/exit fill math
  (`fill_premium`); `option_backtest` now calls it too (sim↔live can't diverge on
  fill price). `paper_trading.close_trade` applies exit slippage + round-trip
  charges and records gross + net + friction_cost when the trade carries a
  friction block; `paper_auto.build_auto_trade` slips the ENTRY before levels are
  set. Per-deployment, tunable: `DeploymentCreateReq.friction` → `risk.friction`;
  deploy wizard control (default ON, prefilled from the preset's backtest cost
  policy). Absent the block → unchanged gross behavior.
- **Deploy-gate rigor** (`754ebb2`): the gate read only in-sample spot stability.
  `deployment_quality` now also consumes out-of-sample evidence via
  `_gather_deployment_evidence` (shared with `/readiness`): a **selection-bias /
  deflated-Sharpe** check (Bailey & López de Prado expected-max over n_trials;
  Acklam inverse-normal, no scipy) and an **option-rupee-OOS** check (negative /
  missing). Acknowledgeable (never-blocking) warnings; every threshold tunable via
  `QualityThresholds` (+ `/deployments/quality` query params). `evidence=None`
  reproduces the historical gate exactly.
- **Live cockpit** (`24347db`): `nse_calendar.market_status` (holiday-aware
  open/closed); `/deployments/overview` returns it + per-deployment
  `last_evaluated_ts`. Live Signals header shows a market badge + ticking IST
  clock; cards show last-evaluated. The Optimizer's running job now persists
  across navigation (re-attaches on mount, mirroring `JobsProvider`).
- **Live exit edges** (`e914de4`): square-off is holiday-aware (`is_trading_day`,
  was weekday-only → fired on holidays); the entry-price fallback no longer books
  a silent fake-zero (flagged `exit_price_source=entry_fallback`/stale); a 120s
  staleness bound stops the marker/square-off booking a minutes-old tick as a fill
  (`exit_price_source` / `exit_price_stale` stamped + flow to the journal).
- **Manual mark/close safety** (`6d4b8d3`): `/paper/trades/{id}/mark` & `/close`
  gained an OPEN-status guard + conditional `replace_one({id, status:OPEN})` (no
  longer clobbers an auto-close) and a `premium_sanity_error` check that flags a
  fat-fingered spot level (overridable via `override_sanity`; UI prompts + 409
  refresh).
- **Chart + re-rank fidelity** (`50a0062`): the backtest chart no longer draws
  fictitious spot SL/target lines on option-level-exit runs (shows real premium
  Entry/Tgt/SL/Exit in the focus strip instead); the option re-rank gained an
  opt-in `rerank_diversity` shortlist (pure `app/rerank_select.py`) so an
  option-best-but-spot-mediocre config can surface (default off = unchanged).

One review claim was REFUTED on verification and NOT acted on: the warehouse
heatmap already renders band-truth coverage (the old density-heuristic endpoint
is dead code with zero frontend call sites) — no change made.

Follow-ups completed in the same session: the **paper journal** now surfaces the
slice-1/4 data — "P&L ₹ (net)" with a gross/friction sub-line + a stale-fill
"est" badge from `exit_price_source`/`exit_price_stale`. **Verified in the running
stack** (`docker compose up -d --build` + browser smoke): all six changes render
with no console errors — cockpit market badge/clock/last-evaluated, the deploy
wizard's friction control (default ON, prefilled) + selection-bias readiness line
+ gate ack warnings, the journal net column, the chart's premium focus strip on an
option_levels run (`premium exit · Entry ₹ · Tgt ₹`), and the Optimizer's
diversity-shortlist toggle. API spot-checks confirm `market_status`,
`last_evaluated_ts`, `n_trials`, and `deflated_sharpe`/`option_oos_net`.

## [0.36.x] — Backtest chart: full-screen (maximize) button (2026-06-13)

533 backend tests pass. A maximize button (top-right of the chart header, Maximize2/Minimize2 icon) toggles the price chart to full screen via the browser **Fullscreen API**; Esc exits. The chart container resizes (`window.innerHeight − 180`) and lightweight-charts' autoSize redraws to fill.

- **Bug fixed in the same change**: the component had `const window = useMemo(...)` (the trade time-window) which **shadowed the global `window`** — so the full-screen handler's `window.addEventListener` / `window.innerHeight` hit that object and threw `addEventListener is not a function`, blanking the page. Renamed the local to `tradeWindow`. (Chose the Fullscreen API over a `fixed inset-0` React overlay specifically to avoid restructuring the live chart's DOM subtree.)

## [0.35.x] — Backtest chart: trade-number labels on markers (2026-06-13)

533 backend tests pass. Small follow-up to the backtest price chart.

- Entry/exit markers now carry the **trade number** (e.g. `#3 CE` on the entry arrow, `#3` on the exit dot) so a marker maps directly to the dropdown / trade-list row.
- **Density-gated**: the `#N` text shows only when legible — when a trade is focused or ≤50 markers are in view; at the dense full-overview (hundreds of trades) markers fall back to plain ▲/▼ CE/PE arrows + ● dots so labels don't become an unreadable wall. (Deliberately did NOT put verbose per-marker labels like "Entry @… / 26000 PE 30 DEC 25" on the chart — that detail lives in the focus strip + dropdown; on-marker it would bury the price action.)

## [0.34.x] — Backtest price chart: trades on chart, timeframes, go-to (2026-06-13)

533 backend tests pass. Replaces the basic candle pane with a professional, dedicated `BacktestChart` and moves it out of Advanced analytics to sit directly below the Trade-quality view.

- **Instrument-titled chart** (NIFTY 50 / BANKNIFTY / SENSEX) with **timeframe buttons 1m/5m/15m/1h/1d** (server-resampled OHLC, scoped to the backtest window; 1m lazily windows to keep long runs light).
- **Trades drawn on the chart**: ▲/▼ entry markers (CE/PE) and ● exit markers; selecting a trade draws **Entry / Target / Stop / Exit** price lines (SL/target reconstructed in index points from the run's `spot_target_pts` / `spot_stop_pts`).
- **Trade navigator** (dropdown + prev/next) jumps to any trade's 1m candles and shows an entry/exit/target/stop/P&L detail strip — the fast way to inspect a row from the trade list on the chart.
- **Go-to date/time locator** (TradingView-style) centers any IST minute; crosshair **OHLC legend** and IST axis.
- The superseded `MultiPaneChart` was removed from the results (account value + drawdown already live in the Performance section).
- Fix: `DualAxisChart` recreated its chart every render (volatile effect deps) which raced lightweight-charts' autoSize observer ("Object is disposed"); deps now key off stable data refs.

## [0.33.x] — Backtest charts split + named axes + account-value range (2026-06-13)

532 backend tests pass. Acts on the user's review of 0.32.x.

- **Two separate charts** (the dual-pane chart was split): `DualAxisChart` rendered twice — **"Cumulative P&L vs trade value"** (left: Cumulative P&L, right: Trade value) and **"Account value & drawdown"** (left: Account value, right: Drawdown). `EquityUnderlyingChart` removed.
- **Named vertical axis titles** (text-up orientation, `writing-mode: vertical-rl` + 180° rotate) on the left and right of each chart, so each axis says what it plots.
- **Lowest / highest account-value cards** in the trade-quality block (min/max of the capital-growth curve). On the live run these read **₹−49,130 (lowest) / ₹1,109,497 (highest)** — surfacing that the account briefly went negative before recovering, a risk the +₹582,939 headline hides.

## [0.32.x] — Backtest results: chart corrected to per-trade buy value + monthly P&L + trade columns (2026-06-13)

530 backend tests pass (4 new/updated pins). Follow-up to 0.31.x acting on the user's review of the first cut. Frontend-only, still all client-side.

- **Chart corrected to the user's definition.** The right-axis line is now the **per-trade net buy value** (entry premium × qty + round-trip charges — capital deployed per trade), not the index spot level. Top pane: **Cumulative P&L (left)** + **Trade buy value (right)**; bottom pane: **Account value (capital growth)** + **drawdown**, clubbed as requested. (`tradeBuyValue`/`tradeSellValue` in `backtestMetrics.js`; chart rebuilt in `EquityUnderlyingChart`.)
- **Monthly P&L calendar** (`MonthlyPnlCalendar`): year × month grid of net P&L (₹ when option execution ran, else points), green/red shaded, with per-year totals — the consistency-at-a-glance view.
- **Trades table** gains three columns (option runs): **Lots (Qty)** e.g. `10 (750)`, **Buy ₹**, **Sell ₹**, where Sell − Buy = the trade's net P&L (charges loaded on the buy, consistent with the chart).
- **"recovered" clarified**: the longest-drawdown stat now reads "recovered to new high" / "not yet recovered" with an explanatory tooltip ("the longest stretch the account stayed below a previous peak before …"). The live run honestly shows 49 days, *not yet recovered* — i.e. it never made a new high after its deepest stretch by the test end.

## [0.31.x] — Backtest results: decision-first Performance overview (2026-06-13)

526 backend tests pass (4 new contract pins). A redesign of the backtest results to match popular options-backtesting platforms (algotest/quantman) on *presentation* while keeping our deeper research rigor. Frontend-only — all of it is derived client-side from data the run already returns; no engine/endpoint change.

- **`PerformanceOverview`** at the top of results (`components/backtest/`): a **rupee-first hero** (Net ₹, Return on capital %, Ending equity from the configured capital, Max DD ₹/%, **Profit ÷ max DD**, annualized Sharpe) when a starting capital is set, else a points hero.
- **Account value & underlying chart** (`EquityUnderlyingChart`, lightweight-charts): the requested algotest-style view — account value (₹, capital-growth curve, e.g. ₹200,000 → ₹782,939) on the right axis with the **underlying (NIFTY) value as context** on the left axis, plus a dedicated **drawdown** pane below. The underlying is context only — explicitly **not** a buy-and-hold benchmark (declined by the user).
- **Trade-quality block** (`lib/backtestMetrics.js`, pure): avg win / avg loss, payoff ratio, expectancy ₹/trade, largest win/loss, max win/loss streak, longest-drawdown duration + recovered flag, trading days, avg trades/day.
- **Honesty guard**: CAGR/Calmar are suppressed under a ~1-year window (annualizing a few months produced absurd 1900% vanity numbers); the span-independent **Profit ÷ max DD** is the headline reward/risk ratio instead (the live run honestly reads 1.12× — barely above its worst drawdown).
- **Declutter**: the deep research cards (data-trust, option pairing, context breakdown, MAE/MFE, Monte Carlo, walk-forward, signal funnel, old multi-pane chart) move into a collapsible **Advanced analytics** section so the decision view stays scannable.

## [0.30.x] — Warehouse review follow-ups: partial-day repair, VIX in sync, planner relabel (2026-06-13)

522 backend tests pass (9 new). Fixes from the Data Warehouse Q&A review. **Investigation correction first:** the "broker-empty" strikes flagged in the review (NIFTY 25000 PE, SENSEX 81000/82000/83000, etc.) were re-verified against Upstox's own expired-contract master — each maps to exactly one authoritative token that returns zero candles, with **no duplicate/alternative** anywhere in the 61,700-contract store. They are genuinely unavailable at Upstox (late-listed strikes Upstox never archived), so the W1 broker-empty ledger was correct; the earlier "wrong-key, re-fetchable" hypothesis was withdrawn (a neighbor-token probe had returned an adjacent strike's data). No contract-key "fix" was applied because there was nothing to fix.

- **F2 — under-captured spot day repair (the real Q4 amber cause).** A trading day captured only partially (PC off mid-session → only the live roller's morning stored, e.g. 2026-06-12 at 255/375) sat at the last-stored-date high-water mark, so incremental catch-up declared "up to date" and never re-pulled the full session. `compute_catch_up_plan` now also detects closed trading days whose stored bar count is materially below the **calendar-expected** session length (`incomplete_spot_days`; Muhurat/short sessions and weekend stray ticks are never flagged), pulls the catch-up window back to repair them (bounded by `SPOT_REPAIR_LOOKBACK_DAYS=21` to avoid churn on genuine broker-short days), and the chain re-fetches the full session + re-bands the widened day. Verified live: 2026-06-12 went 255→375 for all three indices and its option band filled (NIFTY 20 / BANKNIFTY 30 / SENSEX 28 pairs), heatmap now green, overall **verified**.
- **F3 — India VIX folded into sync.** VIX previously refreshed only on startup/connect/run-now. It now also runs on the **daily 18:00 loop** (`daily_autoupdate_loop` gained a `pre_run_fn` side-task = `_topup_vix`) and on **"Sync now"** (`/warehouse/sync` tops up VIX on every real sync and reports it). UI caption notes the auto-refresh.
- **F5 — manual Option Planner relabeled** (`OptionPlannerPanel`): an amber note makes explicit it is moneyness-relative selection with "any candle = covered", **not** the daily ATM-band completeness that Sync/auto-update maintain — use it only for research pulls the band doesn't cover.

## [0.29.x] — Data Warehouse Overhaul: honest status, one-button sync, band-truth heatmap (2026-06-13)

513 backend tests pass. Full implementation of the four approved workstreams from the Data Warehouse page review (commits `51a7fd2`, `d15ec40`, + this one):

- **W1 — the perpetual "warning" is fixed, honestly.** The nightly catch-up's option stage was still the close-sampled moneyness preview (pre-band philosophy); it is now `build_band_fetch_plan` over the FULL rolling window, so automation self-heals wick-edge gaps and stops re-fetching stored candles (observed: 24,330 fetched / 0 added in one boot run). New **broker-empty ledger** (`option_known_empty`): pairs a clean fetch proves the broker has no candles for are recorded once, excluded from missing counts/actions, and reported as `broker_empty_pairs` — verified live: NIFTY/BANKNIFTY/SENSEX **100% band / verified / 0 actions** with 102 pairs ledgered. A **grace rule** protects the latest closed session (Upstox publishes F&O history with a lag — a same-night sync saw Friday's whole band empty and would have mis-ledgered it; those 76 pairs were purged and stay actionable until published). `POST /api/warehouse/sync` = catch-up + band sweep for spot-current instruments.
- **W2 — status-first page.** The hygiene plan persists (`data_hygiene_latest`, `GET /api/data-hygiene/latest`) and renders ON LOAD: per-index band chips + broker-empty footnote + checked-at + action count — no forced 5–15s check. "Update to latest" → **"Sync now"**; stale fixed-scope caption replaced by the live rolling window; collapsible "How this page works"; IST date defaults; VIX baseline served by the backend.
- **W3 — heatmaps tell the truth.** The option heatmap's old metric (`candles / 375×stored contracts`) was self-referential — green days could miss entire wick strikes. Cells are now per-day **band coverage** (`per_day` from `band_completeness`, served from the persisted plan; the heavy `/options/coverage` load is gone from the page). Both heatmaps get 8-weeks/3-months/All range selectors instead of ~270 unbounded columns.
- **W4 — structure.** Option planner + expired-contract backfill demoted into a collapsed **Advanced tools** section (banner: routine maintenance is automatic); destructive clears moved into a **danger zone** requiring typed instrument-name confirmation; runs table gets human source labels + a status filter; `DataWarehouse.jsx` split 1,909 → 526 lines + 8 panel components under `components/warehouse/`; contract tests read `tests/contract_corpus.warehouse_page_text()`.

## [0.28.x] — Quality Hardening, Slice C: server.py split into routers/schemas/runtime (2026-06-13)

503 backend tests pass. The 4,271-line `backend/server.py` monolith is now a 203-line app factory; every route, model, and helper moved **byte-for-byte** (no frontend changes):

- **`app/schemas.py`** — all 24 Pydantic request models. **`app/runtime.py`** — shared singletons (`upstox_stream_manager`, `live_candle_roller`), constants, and the 43 route helpers. **`app/routers/{research,warehouse,journals,deployments,broker}.py`** — 103 routes (22/38/9/16/16); each router file declares its own `api = APIRouter()` so decorators and bodies needed zero edits. `server.py` keeps the app factory, root/health, startup/shutdown + scheduler wiring, and mounts the group routers. Import DAG: server → routers → runtime → app business modules (no cycles, nothing imports server).
- **Zero-behavior-change proof, not eyeballs**: the OpenAPI schema dumped from the running container before vs after the split is **byte-identical** (sorted-JSON compare); the route table is set-identical (107 routes); a first-match probe replayed 111 (URL, method) pairs through both apps' compiled route regexes in original vs new registration order — same winning route every time (no literal-vs-`{param}` shadowing introduced). Full suite + rebuilt container + 18-endpoint curl smoke + browser sweep of all 7 pages (no console errors).
- **Contract tests** updated in the same commit: new `tests/contract_corpus.py` (`backend_api_text()` = server.py + schemas + runtime + routers concatenated); the 17 test files that string-asserted on server.py text now assert on the corpus, and the bootstrap `py_compile` check covers all 9 split files. Tests still never import server.py.
- The quality-hardening spec (Slices A/B/C) is now **fully delivered**, closing out the 2026-06-12 architecture review.

## [0.27.x] — Execution Policy: One Source of Exit Truth + Sim↔Live Parity Tests (2026-06-13)

503 backend tests pass (11 new parity invariants). The last big item from the accepted architecture review.

- **`app/execution_policy.py` (new)** — THE place exit semantics live: `resolve_premium_levels` (pts-over-pct, target above / stop below, configurable floor — sim 0.0, live ₹0.05), `tick_exit_reason` (a live tick is a degenerate bar routed through the backtest's own `intrabar_exit`), `spot_mirror_levels` (byte-for-byte the spot engine's CE/PE formulas), `spot_mirror_exit_reason`.
- **Delegations (behavior-preserving)**: `option_backtest._resolve_option_levels`, `paper_auto.compute_auto_risk_levels` / `compute_spot_exit_levels` / `spot_exit_reason`, and `paper_trading.risk_exit_reason` now all resolve through the shared policy.
- **Real parity bug fixed by the extraction**: both live tick deciders (`risk_exit_reason`, `spot_exit_reason`) checked the TARGET first while the entire sim stack is pessimistic STOP-FIRST — in degenerate configurations (stop ≥ target) live would book the lucky fill the backtest refuses. Live now routes through `intrabar_exit`, so sim and live cannot drift; `tests/test_execution_policy.py` replays identical inputs through both paths (levels, tick-vs-bar decisions, both directions, floors/rounding documented) and pins stop-first forever.
- Kiro quality-hardening **Slice C (server.py split) is now UNLOCKED** — prompt added to `.kiro/specs/quality-hardening/spec.md`.

## [0.26.x] — Quality Hardening, Slice B: Research analytics the data already supports (2026-06-12)

492 backend tests pass (5 new contract pins); frontend builds clean (no new eslint warnings). Frontend-only, client-side math, no backend changes — five separately-committed, separately browser-verified analytics surfaces over data the existing endpoints already return. Each commit rebuilt the frontend container and was confirmed served on `http://localhost:3000`.

- **MAE / MFE distribution card** (`BacktestLab.jsx`, `mae-mfe-card`): two histograms (MFE favorable, MAE adverse) with medians + max and a one-line hint that median MAE is the level below which a tighter stop would have cut winners. Uses the paired option-leg excursions (`option_mfe_pts`/`option_mae_pts`, premium points) when option execution ran, else the spot-leg `mfe_pts`/`mae_pts`.
- **Monte Carlo card** (`BacktestLab.jsx`, `monte-carlo-card`): bootstrap-resamples the run's per-trade P&L (draw N trades with replacement, 1,000 runs, input capped at 1,000 trades) and reports P5/P50/P95 max drawdown, P5/P50/P95 ending P&L, and **P(net<0)**. Bootstrap-with-replacement is deliberate — a plain order shuffle leaves the sum invariant, so ending P&L (and thus P(net<0)) would be degenerate. `option_pnl_value` (net ₹) when paired, else `pnl_pts`.
- **Run comparison view** (`RunComparison.jsx` + `BacktestRunJournal.jsx`, `run-comparison-panel`): select exactly two saved runs → parameters diff (differing keys highlighted), headline metric table, and overlaid equity curves normalized to trade index so different-length runs line up. Fetches both runs in full via the existing `GET /api/backtest/runs/{id}`.
- **Volatility audit panel** (`DataWarehouse.jsx`, `volatility-audit-panel`): read-only panel in Verify & Audit calling the existing `POST /api/volatility/audit` for an instrument + IST date range + spike threshold — total bars, spike-bar count, spike share %, max ratio, and a top-10 spike-bars table. Verified end-to-end against the live endpoint (NIFTY May–Jun 2026: 10,179 bars, 104 spikes, 1.02%). New `api.volatilityAudit`.
- **risk_hints in the Signals Ledger detail row** (`SignalJournal.jsx`, `ledger-risk-hints`): renders the captured `risk_hints` (spot target/stop pts, premium target/stop %, time stop minutes) next to the entry triggers; only non-null hints are shown.
- Contract tests: new `tests/test_quality_hardening_slice_b.py` pins all five surfaces (string-asserts on frontend source + the volatility route in server.py; no server import, no motor).

## [0.25.x] — Fix: hygiene option fetch under-requested the completeness band (2026-06-12)

487 backend tests pass (2 new). Bug found while verifying Slice A in the browser: after running "Fill gaps", the Data Hygiene panel stayed **degraded** (~91–93% band coverage) and re-running did nothing — the fetch jobs reported `status: ok` but added **0 candles**.

- **Root cause (contradicts the 0.23.x "honest residual" claim)**: the band-completeness check demands every strike in `round(day_low)−1 … round(day_high)+1` (the day's intraday extremes + 1 pad), but the hygiene fetch re-derived a SEPARATE per-day ATM ± moneyness (`atm+otm1+itm1`) selection via `_build_option_warehouse_preview`. The two disagree at the edges, so intraday-wick / band-edge strikes were judged "missing" forever yet **never requested**. Verified against the broker: NIFTY 25200 CE on 2025-09-15 (day high 25138.45 → rounds to 25150 → +1 pad demands 25200) had **375 candles available at Upstox** but was never fetched. The ~7–9% residual was NOT broker-unavailability — it was fetchable data the fetch path skipped.
- **Fix**: the hygiene option fetch is now driven by the SAME completeness band it is judged against. New `data_hygiene.build_band_fetch_plan` recomputes `completeness.missing_band_pairs` and resolves each missing `(day, expiry, side, strike)` to its stored contract, building a fetch plan that requests EXACTLY the missing pairs (pure, tested grouping helper `fetch_items_from_missing_pairs`; unresolved strikes are surfaced, not dropped). `_hygiene_submit_option_candles` uses it instead of the moneyness preview; the run doc now records `missing_pairs` + `unresolved_contracts`.
- **Verified end-to-end on the live warehouse**: one hygiene run added NIFTY +68,368 / BANKNIFTY +98,946 / SENSEX +114,726 candles (the old path added 0). Band coverage **92.9% → 99.24% (NIFTY), 91.43% → 98.86% (BANKNIFTY), 91.46% → 99.04% (SENSEX)**. The small remaining residual (22/41/39 pairs) equals the `empty` fetch tasks — strikes the broker genuinely has no data for, now honestly shown as amber "warning" instead of red "degraded".

## [0.24.x] — Quality Hardening, Slice A: Warehouse Truth in the UI + Retention (2026-06-12)

485 backend tests pass (3 new contract pins); frontend builds clean (no new eslint warnings). Frontend-only — surfaces the daily ATM-band completeness truth from 0.23.x and closes the remaining UI review items. All four Slice-A items verified against the live stack (plan/auto-update/stream/token endpoint shapes confirmed field-by-field).

- **DataHygienePanel band-coverage fields** (`frontend/src/components/DataHygienePanel.jsx`): the per-instrument option block now reads the band diff instead of the retired per-expiry heuristic — daily ATM-band **coverage %** with status color, **missing strike-day count**, a compact **missing-by-month** line, and an **expandable missing-sample** list (`date · expiry · strike · side`, capped 50 by the backend). The existing check/fill flow is untouched.
- **Dashboard warehouse-health banner** (`frontend/src/components/WarehouseHealthBanner.jsx`, mounted on `Dashboard.jsx`): one "can I trust today's data?" strip — last auto-update result + time, per-index band coverage, live-stream running/stale (tick freshness within 3m), and the OAuth token countdown. Green only when everything is verified/running, amber otherwise. The band-coverage plan costs ~5s, so it is **lazy behind a Check button** (never run on mount) and cached for the browser session.
- **Auto-update history** (`DataHygienePanel`): a collapsible list of the last ~10 runs from `GET /api/warehouse/auto-update/status` `history[]` (status · trigger · jobs submitted · finished-at · error).
- **Opt-in retention** (`frontend/src/pages/SignalJournal.jsx`): a "auto-purge AUDITED older than N days" input in the cleanup bar, persisted in `localStorage`. Applied client-side on page load via `POST /api/signals/purge` `{older_than_days, states: ["AUDITED"]}` at most once per IST day (timestamp guard). Empty = off (default off); confirm-free because the user opted in by setting N.
- Contract tests pinned in the same commit: band fields + history (`test_option_coverage.py`), the warehouse-health banner (`test_bootstrap_contract.py`), and the retention surface (`test_signal_paper_lifecycle.py`).

## [0.23.x] — Warehouse Truth: Daily ATM-Band Completeness + Rolling 9-Month Scope (2026-06-12)

484 backend tests pass (26 new). Root-cause fix from the architecture/data audit (user-confirmed): the warehouse reported "verified" while backtests hit `MISSING_ENTRY_CANDLE` — hygiene judged option coverage per-day/per-expiry ("any candle that day") while spot sweeps several strikes intraday, so strikes that were ATM for part of a session were never fetched and never flagged. Verified on real data: NIFTY 2026-05-20 (spot 23397→23691) was missing 23550CE entirely while hygiene said verified, 0 actions.

- **`app/completeness.py` (new)** — the ONE definition of option completeness: a day is complete when every strike the day's spot low–high touched (nearest-step rounding identical to the fetch path, ±1 pad) has candles for both legs at the day's resolved expiry. Pure + unit-tested, including the real May-2026 case.
- **Hygiene rewired to the band diff** (`data_hygiene.py`): one candles_1m aggregation now feeds both spot coverage and per-day low/high; one options_1m aggregation yields exact stored (day, expiry, side, strike) pairs; the plan reports `planned_pairs` / `missing_pairs` / band `coverage_pct` / `missing_by_month` + a bounded sample, and emits the option action whenever ANY pair is missing. The submit path was already exact (planner preview at sample=1 → per-date fetch tasks) — it just never fired because the old heuristic said "verified". Plan runtime ~4.8s for 3 indices over 9 months.
- **First honest audit of the real warehouse**: 83.5–84.4% band coverage, 1,701 missing strike-days across NIFTY/BANKNIFTY/SENSEX over the rolling window — backfill submitted via the normal hygiene execute (3 background jobs).
- **Rolling 9-month scope** (user decision): default audit/fetch window is now `today − 9 months` (floored at the 2024-11-27 baseline; older data kept, no longer audited by default). `DEFAULT_MONEYNESS` for hygiene is now `atm+otm1+itm1` so fetches cover the band's ±1 pad.
- **No-fallback strike selection pinned by regression test**: the audit confirmed `select_contract_for_signal` exact-matches the target strike or returns None (no silent far-strike substitution is possible); a test now guards that property forever. (The reported "trade #109 paired 400 pts away" was a journal-reading mix-up — verified: 0 of 124 trades paired beyond ±100 pts.)
- Quick wins from the review: re-rank/WFO option-candle loads log a warning when the 4M-row cap is hit (was a silent truncation risk); optimizer enriched-frame cache cap lowered 64→16 (memory headroom on long indicator-period searches); preset rename now also updates referencing deployments' `source_id` (readiness/quality lookups no longer orphan).
- **Root cause #2, found while verifying the backfill** (`upstox_client.py`): contracts synced while ACTIVE keep `source="current_option_contract"` forever, and endpoint routing keyed off that provenance flag — so after expiry the normal V3 endpoint was still called and Upstox rejected with `UDAPI100011`, silently leaving every once-synced-live weekly (Feb–Jun 2026) unfillable. `_is_expired_instrument_key` now routes by the contract's actual `expiry_date` (< today IST), and `_expired_endpoint_key` synthesizes the `SEGMENT|TOKEN|DD-MM-YYYY` key the expired endpoint requires when only a 2-part key is stored (persisted candles keep the original key). 7 routing tests (motor stubbed — the module transitively imports db).
- **429 backoff** in `_authenticated_get`: three patient retries (2s/5s/10s) before failing — parallel backfill jobs after startup catch-up could burst past the broker's rate budget and turn whole contract-days into recorded failures.
- **Root cause #3 — duplicate contract identity / split candle keys**: the expired-contract backfill stores dated 3-part instrument keys (`NSE_FO|72171|26-05-2026`) while the current-contract sync stores the plain 2-part key for the SAME contract (702 duplicated NIFTY identities). Candles fragmented across both forms, so every exact-key consumer (backtest pairing, preview per-date counts, re-rank/WFO loads) could miss data that existed. Fix: `instruments.canonical_instrument_key` (2-part broker form) is now THE candle key — canonicalized at persist (`persist_option_candles_bulk`), in pairing's candle grouping + lookup (`option_backtest`), in re-rank/WFO candle queries (both forms during transition), in the backtest-run loader and option preflight, and in the preview counters. One-time migration: **6,937 dated keys → canonical, 6.15M docs renamed, 115,333 double-stored minutes deduplicated.** Regression tests: dated-contract↔canonical-candle bridging + split-key merge in `test_option_backtest.py`.
- **Acceptance verified end-to-end**: `Optimized · confluence 10` re-run after the fixes — **124/124 trades paired, 0 missing** (was 121/124 before, and would have been 16/124 with the key split left unfixed — caught by re-running the acceptance test mid-change). Band coverage after backfills: ~91–93% per index; the residual is genuinely unavailable at the broker (band-edge strikes that never traded), now honestly visible instead of hidden behind "verified".

## [0.22.x] — Auto-subscribe ATM±3 option universe in market hours (2026-06-12)

458 backend tests pass (1 new). Follow-up to Slice 5 item 4:

- The market-hours evaluator loop now keeps a baseline ATM±3 option universe subscribed on the read-only Upstox stream automatically (calls `_auto_follow_option_stream(min_radius=3)` each minute while connected). The live option-chain snapshot on `/live` — and paper-trade marks — now always have fresh premiums during market hours with **no manual stream restart and no active deployment required**.
- `_auto_follow_option_stream` gained a `min_radius` floor and is now **idempotent**: it skips the disruptive WS restart when the current subscription already covers the desired keys, and re-centers automatically only when the ATM band drifts out of coverage.

## [0.21.x] — Forward Surfaces Overhaul, Slice 5: Polish (2026-06-12)

457 backend tests pass (4 new); frontend builds clean (no new eslint warnings). Four small, separately-committed items closing out the forward-surfaces spec:

- **P&L calendar heat-grid** on the Paper Trading page (`paper-pnl-calendar`): a GitHub-style per-day realized-₹ grid (weekday rows × week columns, cells colored green/red by realized P&L, intensity by magnitude), computed client-side from the closed trades already fetched for the summary strip; honors the active filter, capped to the most recent 16 weeks, collapsible.
- **Data-realism preflight line** in deploy wizard step 1 (`preflight-summary`): fetches `GET /api/deployments/preflight` for the chosen preset's instrument and shows spot coverage, upcoming option expiries, active-vs-expired contracts, Upstox token state, and structural breaks with verified/warning/degraded dots. Informational; never blocks. Restores the surface dropped in the Slice-2 rebuild.
- **Drift re-pin**: new route `POST /api/deployments/{id}/repin-source` (recompute the plugin's source SHA, update `strategy_source_sha`, clear all `drift_*` fields, append a `repin_history` audit entry, resume only if it was auto-paused for `strategy_source_drift`) backed by pure helper `build_repin_update` (4 unit tests). UI: a "Re-pin & resume" button on the deployment card's pause banner (drift-paused only) + `api.repinDeploymentSource`.
- **ATM±3 option-chain snapshot** on the Deployments page (`option-chain-panel`): scaffolds the nearest-expiry ATM-centered strike band from the existing option-universe route and fills CE/PE LTPs from the read-only WS stream (`/upstox/stream/ticks/latest`); CE LTP | strike | PE LTP per deployed instrument with the ATM row highlighted, spot + expiry header, 30s auto-refresh, collapsible. No new backend route.

## [0.20.x] — Forward Surfaces Overhaul, Slice 4: Paper Trading Journal (2026-06-12)

453 backend tests pass; frontend builds clean (no new eslint warnings).

- **`/paper` rebuilt as the Paper Trading Journal** (`frontend/src/pages/PaperTrading.jsx`), a strategy-named trading journal on the upgraded `GET /api/paper/trades`. Columns: deployment / strategy, contract (`trading_symbol`), CE/PE, lots × lot size, entry time + price, exit time + price, exit reason, holding time, P&L in ₹ and as % of entry premium, status. Entries/exits are option premium (₹), never spot.
- **Day-wise grouping** with per-day subtotal rows (realized + open MTM), and a **summary strip** (today realized, open MTM, open count, win rate, profit factor) above a small cumulative-realized **equity sparkline** — computed client-side over the filtered set (capped at 500 trades).
- **Server-side filter / sort / paginate / CSV**: deployment (preselected from `?deployment=`), instrument, status, IST date range; clickable sort on whitelisted columns (entry time, entry price, P&L, exit time); skip/limit pagination with total; CSV honoring filters; 30s auto-refresh.
- **One-click close flows** replace the old type-a-price requirement: "Close @ market" uses the trade's `last_price` (prompts for a premium only when `last_price` is null), a confirmed "Close all open" closes every open trade at its last mark (skipping unmarked ones), and a small manual-premium field remains as an off-hours fallback.
- **Purge toolkit** (via `POST /api/paper/trades/purge`, all confirmed) for CLOSED trades only — row-select, older-than-N-days, per-deployment. OPEN trades are never deletable.
- Contract test `test_frontend_exposes_live_and_paper_operational_views` pins the rebuilt page's testids (old `paper-trading-journal`/`paper-trade-table`/`mark-paper-trade`/`close-paper-trade`/`risk-badge` preserved).

## [0.19.x] — Save Backtest Setup as Preset + Preset Rename (2026-06-12)

453 backend tests pass; frontend builds clean (no new eslint warnings).

- **Save setup as preset (Backtest Lab):** a new "Save setup as preset" button (`backtest-save-preset`) writes the current setup — strategy + modified params + the full Option Execution / exit policy — as a named preset. The `execution` block (moneyness, DTE, exit_mode, premium target/stop %/pts, lots, costs) is built to match `execution_from_option_config`, so the preset re-applies in the Lab and **prefills the deploy wizard, deploying as-is**. (Previously presets could only be created by the Optimizer's "Apply as preset".) Overwrite is confirmed.
- **Rename preset:** new backend route `POST /api/presets/{name}/rename?new_name=` (preserves config + execution; 404 if missing, 409 on name collision, 400 on empty) + `api.renamePreset`. A rename (pencil) button (`preset-rename-*`) was added to the Optimizer's Saved Presets panel next to deploy/delete.

## [0.18.x] — Forward Surfaces Overhaul, Slice 3: Signals Ledger (2026-06-12)

453 backend tests pass. Frontend builds clean (no new eslint warnings).

- **`/journal` rebuilt as the Signals Ledger** (`frontend/src/pages/SignalJournal.jsx`), the trade-recommendation record built on `GET /api/signals/enriched`. One row per deployment signal joined with its paper trade: IST time, deployment, strategy, instrument, CE/PE, contract (strike+side) + expiry, spot at entry, entry premium, expandable entry-trigger `reasons`, exit time/premium/reason, P&L in ₹ and premium points, score, state, blockers and `paper_trade_error`.
- **Server-side filter / sort / paginate / CSV**: deployment (preselected from `?deployment=` URL param, the command-center deep-link), instrument, state, clean/blocked, IST date range; clickable sort on the whitelisted columns (time, instrument, score, state); skip/limit pagination with total; CSV export honoring the current filters (`format=csv`); 45s auto-refresh.
- **Deletion toolkit** (via `POST /api/signals/purge`, all confirmed): row-checkbox "Delete selected", "Delete older than N days", and per-deployment purge. Paper trades are never touched by this route; OPEN trades are unaffected.
- Contract tests updated in the same commit: `test_signal_paper_lifecycle.py::test_frontend_exposes_live_and_paper_operational_views` now pins the ledger page's testids; `test_option_coverage.py::test_backtest_run_journal_moved_to_backtest_lab` updated for the rebuilt page (still asserts it is not the backtest-run table).

## [0.17.x] — Forward Surfaces Overhaul, Slices 1–2 (2026-06-12)

453 backend tests pass. Slices 3–5 (Signals ledger, Paper journal, polish) are spec'd for the next agent in `.kiro/specs/forward-surfaces-overhaul/`.

- **Independent deployments** (user decision): removed the highest-score-wins concurrency rule — every ACTIVE deployment journals and trades its own signals, enabling honest multi-strategy A/B. Exposure control = per-deployment `max_open_paper_trades`.
- **Approval flow fully retired**: deleted `POST /signals` (manual research), `/signals/{id}/transition`, `/approve`, `/skip`, `/mark-blocked`, `/signals/{id}/paper` and their request models + api.js methods. Modes are now `signal_only` | `paper` (legacy `shadow`/`recommendation` map to signal_only on create; old stored docs render as signal-only). `manual_approval_required` is stamped False on new deployments.
- **Signals ledger API** (`GET /api/signals/enriched`): signal⟷trade join — entry premium, exit premium/reason, P&L ₹ + premium points, trigger `reasons`, blockers, `paper_trade_error` — with server-side filters (deployment/strategy/instrument/state/clean/date-range), whitelisted sort, pagination, CSV export.
- **Paper trades API upgraded**: same filter/sort/pagination/CSV treatment + `deployment_name` on every row; `events` excluded from lists.
- **Deletion toolkit**: `POST /api/signals/purge` and `POST /api/paper/trades/purge` (ids / per-deployment / older-than-X; OPEN trades never deletable), and `POST /api/deployments/{id}/archive?purge=1` (undeploy + purge journals, keeping OPEN trades for the marker/square-off).
- **Deployments overview API** (`GET /api/deployments/overview`): per-deployment today (clean/blocked signals, open trades, open MTM, realized) + lifetime (closed trades, realized ₹, win rate) + account totals in one call.
- **Option stream auto-follow**: on deployment create/resume, the live option subscription re-derives its strike radius from ACTIVE paper deployments' moneyness policies (`radius_for_deployments`, +1 drift headroom, clamp 1–5) and restarts the read-only stream — best-effort, never blocks the deployment.
- **`/live` rebuilt as the Deployments command center**: per-strategy cards (mode chip, status + auto-pause reason, today's signals/open MTM/realized, lifetime ₹ and win rate, links to its signals/trades) with Pause/Resume/Evaluate and **Undeploy** (archive ± purge); header totals (today MTM, open trades, signals today) with 30s auto-refresh; a 3-step deploy wizard (preset + readiness/quality evidence → execution prefilled from the preset's policy → kill switches & ack). Pending Approval panel and the manual research-signal console are gone.
- Contract tests rewritten to pin the new surface and assert retired routes stay gone; +3 universe-radius tests. Verified against the live market session (overview/enriched/CSV endpoints + the new page and wizard in Chrome).

## [0.16.x] — Pipeline Alignment: Preset Execution Policy, Readiness, Option-Aware WFO (2026-06-12)

449 backend tests pass. Implements the accepted alignment recommendations (1, 2, 4, 5); recommendation 3 (retiring the legacy spot evaluation) deferred by user decision.

- **Presets carry their execution policy** (`app/preset_execution.py`): apply-as-preset now stores `config.execution` (moneyness, DTE filter, exit mode, premium levels, lots, costs) derived from the job's `option_config`, plus `source_job_kind`. Backtest Lab re-applies the policy on preset load (option pairing auto-enabled under the same terms); the deployment form prefills option policy + auto-paper premium fallbacks from it (pts/pct mapped, guarded so the 15s refresh never clobbers manual edits). Old presets keep working (no execution block → unchanged behavior).
- **Deployment readiness** (`GET /api/deployments/readiness?source_type=&source_id=`): informational evidence card in the deployment form next to the quality gate — latest completed honest-WFO for the strategy (efficiency, OOS-positive windows, params-match, option-OOS ₹ when present) and latest option-rupee evidence (exact-params re-rank job preferred, else option-paired backtest run). Missing evidence shows as the next step to run, making the canonical pipeline (WFO → option rupee → deploy) visible at the decision point.
- **Deploy deep-link**: each preset row in the Optimizer gets a Deploy button → `/live?preset=NAME` preselects it as the deployment source.
- **Option-aware walk-forward (WFO v2)** (`WfoStartReq.option_aware` + `option_config`; default ON in the UI form): after stitching, the OOS trades are paired ONCE with real option candles (same engine + windowed data loading as the re-rank) and the results panel shows an "Option OOS (₹)" block — net rupee, win rate, charges, pairing %, per-window rupee chips, and rupee consistency. Pairing failures or data gaps degrade to an honest error/low-coverage note; the spot stitch is never affected. Window re-optimization itself stays on spot (per-window option evaluation remains future work).
- **Optimizer DTE filter is a multi-select** (ALL / 0–6 chips, was a single-token dropdown capped at DTE3); legacy saved setups and job clones coerce automatically. The option sub-panel is shared by re-rank and WFO rupee check (top-K hidden for WFO).
- New tests: 4 WFO option-OOS pure helpers (window bucketing, summary shaping) + 5 preset-execution derivation; 449 total.

## [0.15.x] — Backtest Lab / Optimizer Alignment Fixes (2026-06-12)

440 backend tests pass. Course-alignment pass over the research surfaces (user review of 2026-06-12).

- **Multi-select DTE filter** (Backtest Lab): the DTE dropdown is now a chip multi-select (ALL / 0–6), e.g. tick 0+1+2 for the 0–2 DTE buying window. Backend `normalize_dte_filter` accepts a single token or a list everywhere it's used (backtest run, option preflight, optimizer re-rank); old saved runs with `"dte2"`-style tokens still clone correctly.
- **ATM default moneyness** (Backtest Lab UI + `OptionBacktestReq`): was OTM1, which contradicted the warehouse's auto-maintained ATM-only scope, the Optimizer default, and the deployment default.
- **Premium exits now replicable live**: deployments accept `auto_paper_target_pts`/`auto_paper_stop_pts` (₹ of premium) alongside the existing `_pct` fallbacks — points take precedence over percent, the same rule as the backtest's `option_levels` mode. The Live Signals form gets a ₹-points/percent unit toggle; `compute_auto_risk_levels` resolves hint-pct → dep-pts → dep-pct per leg (stop still floors at ₹0.05).
- **Optimizer re-rank premium exits in points**: the option sub-panel gains the same Points/Percent toggle as the Backtest Lab (the backend already accepted `option_target_pts`/`option_stop_pts`; the UI never sent them).
- **"Walk-forward" naming split**: the Backtest Lab toggle/panel is now "Walk-forward split check (same params, IS vs OOS)" with a tooltip pointing to the Optimizer's honest re-optimizing WFO — two different things no longer share one name.
- **Lots input clarity**: the Option Execution "Lots" input is disabled (with a note) while Capital & position sizing is on, since the sizing panel controls the lot count in that case — previously it was silently ignored.
- **Sizing estimate visibility**: premium-at-risk sizing without a premium stop (e.g. spot-mirror exit mode) shows an amber note that per-trade rupee risk uses the Assumed stop % (an estimate, not an exact bound).
- **Live-parity note** in the premium SL/target panel: backtest exit settings do not travel with presets; the deployment fallback fields are the live equivalent.
- **Unit audit (no fixes needed)**: verified pnl pts→₹ via quantity (lots × contract lot_size), slippage points + half-spread per side, sizing ₹ vs ₹ budget, `net_pnl_inr` = cost-adjusted points × lot size, WFO efficiency = pts/day ÷ pts/day, marker routes premium levels to the option tick and spot-mirror levels to the index tick, paper P&L = (price − entry) × quantity. No unit mismatches found.
- 8 new tests (3 multi-DTE, 5 premium-pts fallback).

## [0.14.x] — Auto Paper Trading on Signals + Low-Sample Forward Metrics (2026-06-11)

432 backend tests pass.

- **Auto paper trading** (`backend/app/paper_auto.py`): paper-mode deployments with `risk.auto_paper` (new `DeploymentCreateReq` fields, default ON for new deployments) open a paper trade for every clean CONFIRMED signal automatically — no manual approval — so signal outcomes are auditable. Hook runs in `evaluate_active_deployments` AFTER the concurrency rule.
- **Entry price = real option premium**: live WS tick → `options_1m` candle ≤5 min old → refuse and journal `paper_trade_error` on the signal. Fixes a pre-existing bug where approval-created trades opened at the SPOT index level while marks/square-off use option premium, corrupting P&L. The manual approve route now uses the same resolution and never duplicates a trade that auto_paper already created.
- **Strategy-defined exits**: the evaluator captures each signal's `risk_hints` (target_pct/stop_pct/spot pts/time stop); auto trades compute stop/target from those hints first, deployment-level `auto_paper_target_pct`/`auto_paper_stop_pct` second (LONG-premium semantics, stop floored at ₹0.05).
- **Per-minute live marker** (`mark_open_deployment_trades`): every minute during market hours, OPEN paper trades are marked to the latest option tick and auto-closed on stop/target (existing `mark_trade_to_market` machinery); the linked signal transitions to EXITED. Without this, stop/target levels only ever fired on manual marks. Tickless trades are left untouched (no stale-price closes).
- **Spot-mirror exits** (post-review fix): the builtin strategies define exits as SPOT INDEX POINTS, not premium-% — exactly what the backtest's `spot_exit` mode simulates. Auto trades now carry `spot_exit` levels (direction-aware: CE target above entry spot, PE below) and the marker closes the option at its current premium when the UNDERLYING hits the level (`spot_target_hit`/`spot_stop_hit`). Without this, trades from every current strategy would have had no intraday exit at all.
- **Race hardening** (post-review fixes): an atomic claim on the signal (`claim_signal_for_paper_trade`) guarantees the evaluator hook and the manual approve route can never both open a trade for one signal; the approve route resolves premium and claims BEFORE any state transition (premium unavailable → 409 and the signal stays CONFIRMED/re-approvable instead of dangling ACTIVE); the marker's writes are conditional on `status=OPEN` so a concurrent manual close is never clobbered; the legacy `risk.stop_price`/`target_price` fallback in the approve route was removed (spot-level units vs premium entry → instant bogus stop_hit).
- Kill switches govern auto trades unchanged (paper mode only; `max_open_paper_trades` blocks the signal so no trade opens).
- **Low-sample forward metrics**: Strategy Library now requests `include_ineligible=1` and shows deployments with <10 complete sessions under an amber "low sample" badge (with n/10 sessions + trade count) instead of hiding them — per user decision, since the PC rarely runs full market sessions.
- UI: auto-paper controls in the Live Signals deployment form (paper mode only: toggle + fallback target/stop % of premium).
- 32 new tests (28 unit in `tests/test_paper_auto.py` + 4 evaluator integration).

## [0.13.x] — Honest Walk-Forward Optimization (2026-06-10)

400 backend tests pass. The single optimizer's result is in-sample by definition; this release adds the honest mode.

- **Walk-forward optimization** (`backend/app/wfo.py`, `POST /api/optimize/wfo`): chronological train/test windows in TRADING days present in the data (rolling or anchored, holiday-aware by construction); per-window Optuna TPE re-optimization on the train slice only; each window's best evaluated on its UNSEEN test slice; all OOS trades stitched into one out-of-sample equity curve — the number to believe.
- Analyses: **walk-forward efficiency** (OOS pnl/day ÷ IS pnl/day; ≥0.7 strong, <0.4 likely overfit), **OOS consistency** (share of OOS-positive windows), **param stability** (rel_spread of each chosen param across windows — wandering params are fitted to noise).
- Final deployable params come from the most recent train window and are saved as `best_params` plus a full `best_backtest_run_id`, so Save-as-Preset / View-Best-in-Lab / deployment flows work unchanged.
- Leak-safety: indicators are computed once on the full frame and sliced per window — verified causal (trailing windows only) in `app/indicators.py`, which also gives test windows realistic warmup history like live evaluation.
- Jobs persist in `optimization_jobs` with `kind="wfo"`: cancel at trial boundaries, pause/resume at window granularity, startup orphan-marking covered.
- UI: "Run type" selector (Single | Walk-forward) in the Optimizer page, window config block, WFO results panel (stitched-OOS headline + equity sparkline, color-coded WF efficiency, per-window table, param-stability bars), WFO tag in Job History.
- 22 unit tests (`tests/test_wfo.py`). Live smoke on real NIFTY data correctly exposed an overfit quick-run: WF efficiency −1.06, 0/3 windows OOS-positive.
- WFO v1 evaluates on spot; for option realism run the final preset through option re-rank or an option backtest afterwards.

## [0.12.x] — Optimizer Overhaul + Options-Buying Upgrades (2026-06-09)

378 backend tests pass. Local stack healthy. Backend changes require a container rebuild.

### Auto-Optimizer
- **Two-stage option re-rank** (`evaluation_mode: "spot" | "option_rerank"`, `rerank_top_k`, `option_config`): Stage 1 fast spot search; Stage 2 re-ranks the top-K candidates by REAL paired-option net rupee P&L. Option contracts + candles loaded once and simulated in-memory (`_option_rerank`); `simulate_paired_option_trades` now pre-groups candles by `instrument_key`. The legacy spot-only path is untouched for A/B. Live A/B showed spot-profitable params can be net-rupee LOSERS on options.
- **Pause / Resume / crash-resume**: `POST /api/optimize/jobs/{id}/pause` + `/resume`. Compact trial log + best-so-far flushed to the job doc; resume rehydrates and re-seeds the Optuna study (`_flush_trial_log`, `_rebuild_study`, `resume_optimization`). Startup reconcile now marks orphaned jobs `interrupted` (resumable), not failed. New statuses: `paused`, `interrupted`.
- **Optional guard rails** (single UI toggle, default ON): `min_trades` significance floor (default 10) + optional CE/PE `min_direction_share`. OFF = pure objective maximization (one-sided allowed).
- **Indicator-period search** (`optimize_indicator_periods`): RSI/MACD/ATR/EMA/ADX/CHOP/swing become tunable; enriched frames cached per indicator-period combo (fixes indicators being frozen at defaults).
- **net_pnl_inr** objective (net points × latest contract lot size).
- Trial budget raised to 5000 in the UI; heavy work moved to `asyncio.to_thread` so the API stays responsive; cancel skips heavy analysis for a fast Stop.
- UI: pre-trade profile selector (previously a dead backend↔frontend link), clone-config-to-setup from Job History, preset **delete** button, save-as-preset for paused/interrupted/failed, "no usable result" hint, setup config persisted to `localStorage`, removed the dead Mode selector.
- Spec authored at `.kiro/specs/optimizer-enhancements/` (requirements → design → tasks).

### Backtest engine / data
- Backtest hot loop converted from per-row `df.iloc[i]` to pre-materialized dict records → ~8.8x faster row access (behavior identical; all strategies verified dict-safe).
- `indicators.detect_fvg` vectorized (was a GIL-holding O(n) Python loop that could stall the event loop on full-history runs).
- **Pre-run option preflight** (`POST /api/backtest/option-preflight?ingest_missing=`): would-pair coverage report + optional background ingest of missing option data; "Option Data Preflight" panel in Backtest Lab.
- Option pairing correctness: windowed contract query (`length=None` + expiry window) fixed near-zero pairing; expiry-mode selector; hardened against silent oldest-contract fallback. BANKNIFTY option-data gaps filled; coverage spans 2024-11-27 → present for all three indices.

### Trading logic (shared decision engine — backtest = paper = live)
- `dte.py` DTE filter; `option_costs.py` rupee cost model (brokerage + statutory + %-of-premium spread); `portfolio.py` premium-at-risk sizing + rupee equity; `market_context.py`/`vix.py`/`context_signals.py` regime/time/DTE/VIX tagging + S/R/round-level/divergence signals; `exit_engine.py` shared `intrabar_exit` for spot and option engines; `strategies/builtin/explosive_reversal.py` score-based detector. India VIX ingested as `INDIAVIX` from 2025-12-29.

## [0.11.x] — Per-Deployment Kill Switches + Forward Metrics + Live Option Universe (2026-06-01)

- Per-deployment kill switches (`max_consecutive_losses` → PAUSE, `daily_loss_cutoff_pct` → PAUSE, `max_open_paper_trades` → BLOCK) in `backend/app/deployment_kill_switch.py`, wired into the evaluator (paper deployments only).
- Forward metrics aggregation (`backend/app/forward_metrics.py`): session-gated (≥70% of 10:00-15:00 IST) deployment metrics; Strategy Library shows them after ≥10 complete sessions. Routes `GET /api/deployments/metrics`, `/deployments/{id}/metrics`.
- Live ATM option universe preview/restart for the read-only Upstox stream (`live_option_universe.py`, `GET /api/upstox/stream/options/universe`, `POST /api/upstox/stream/options/restart`).
- Warehouse chart trust UI (explicit OHLC overlay, IST axis, session markers, local chart theme).

## [0.10.x] — Data Warehouse Hardening (2026-05-31)

A focused pass to make the warehouse fast, trustworthy, self-maintaining, and inspectable. 272 backend tests pass.

### Performance
- New `backend/app/option_coverage_cache.py` + `option_coverage_cache` collection. `/api/options/coverage` served from a precomputed per-underlying summary (~8s → ~200ms) with a single-flight lock to prevent a startup stampede. Cache warmed on boot, refreshed after option-fetch jobs and after clearing option data.
- Data Warehouse page renders on the fast calls and loads the option heatmap independently.
- `compute_hygiene_plan` optimized from a 120s+ timeout to ~6s by replacing the `options_1m`→`option_contracts` `$lookup` join with a group on the embedded `underlying`/`expiry_date` fields, and aggregating spot coverage server-side.

### Correctness
- `warehouse.audit_integrity` is now holiday-aware (uses `nse_calendar.trading_days_in_range`); previously NSE holidays were counted as missing days. `summary.calendar_assumption == "nse_trading_calendar"`.

### Features
- **Data Hygiene UI** (`DataHygienePanel`): Check warehouse (plan) + Fill gaps (dependency-ordered execute), routed through the global job tracker. Data Warehouse page regrouped into Connection / Data Hygiene / Index Data / Option Data / Verify & Audit / Diagnostics sections.
- **Automatic warehouse catch-up** (`backend/app/warehouse_autoupdate.py`): runs on startup, on Upstox OAuth-connect, and daily at 18:00 IST; gated on Upstox connected; status + toggle UI; routes `GET/POST /api/warehouse/auto-update/{status,toggle,run}`.
- **Point-in-time lookup** (`backend/app/warehouse_lookup.py`, `GET /api/warehouse/lookup`): spot + derived ATM + nearest expiry + ATM CE/PE candles for a date/time, warehouse-only.
- **Candlestick chart** (`backend/app/warehouse_ohlc.py`, `GET /api/warehouse/ohlc/{instrument}`): server-side resample to 1m/5m/15m/1h/1d + intraday gap detection. `WarehouseChart` with OHLC crosshair legend, date/time locator (validate + snap + mark), gap banner.
- **NSE holiday-calendar modal** (`HolidayCalendarDialog`, `GET /api/calendar/holidays`).
- **Global background-job tracker** (`frontend/src/lib/jobs.jsx` `JobsProvider`): ingest/fetch/hygiene progress survives navigation (run IDs persisted to `localStorage`); active-jobs indicator in the top bar.
- **OAuth token-expiry countdown** in the global top bar (color-escalating) and the Upstox panel.

### UI cleanup
- Removed the "Made with Emergent" badge, `emergent-main.js` loader, and PostHog session-recording telemetry from `index.html`.
- Removed the obsolete yfinance ingest panel (kept read-only coverage cards).
- Backtest Run Journal moved into the Backtest Lab; Signal Journal repurposed as the deployment signal audit trail.
- Removed the redundant Raw Option Universe Audit panel (clear-options action relocated to Data Trust Audit; `/api/options/audit` route kept for programmatic use).

## [0.9.x] — Phase 4b Slices (Forward Testing Stack)

### Slice 9 — Deployment quality warnings + acknowledgment checkbox
- New module `backend/app/deployment_quality.py` with 5 checks: missing walk-forward, walk-forward IS/OOS divergence (OOS < IS × 0.7 OR explicit divergence flag), low trade count (< 30), weak Sharpe (< 0.5), large drawdown ratio (|max_dd|/total_pnl > 0.15)
- New route `GET /api/deployments/quality?source_type=...&source_id=...`
- `DeploymentCreateReq.acknowledged_warnings` required when warnings present (HTTP 400 `acknowledgment_required` otherwise)
- Quality snapshot stored on deployment as `quality_at_creation` plus `acknowledged_warnings` flag for audit
- Frontend: `QualityBadge` with severity-colored warning list and inline ack checkbox; Create button disabled until ack ticked when needed
- 15 new tests (223 total)

### Slice 8 — Strategy source SHA pinning + drift detection
- New module `backend/app/strategy_source_hash.py` — SHA-256 of plugin .py file, truncated to 16 hex
- Pin `strategy_source_sha` on deployment creation; evaluator compares pinned vs current on every tick
- On mismatch, auto-pause with `drift_reason="strategy_source_drift"` and full audit (pinned/current/timestamp)
- Pre-slice-8 deployments without a pinned SHA continue to operate (legacy compat)
- 14 new tests

### Slice 11 — Idempotency hardening (out-of-order)
- Unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` with `partialFilterExpression {deployment_id: {$exists: true, $type: "string"}}` so manual research signals are unaffected
- Evaluator catches Mongo duplicate-key (E11000) errors as `outcome="skipped"`, `reason="already_journaled"` and advances `last_evaluated_ts`
- Index added to `ensure_indexes()` and created live on running DB

### Slice 7 — Slippage model + post-hoc volatility detector
- New module `backend/app/slippage.py` with `SlippageConfig` (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x)
- Wired into `simulate_paired_option_trades`; per-trade audit fields `raw_*`, `entry_slippage_pts`, `slippage_bucket`, `expiry_tail_applied`
- Override per backtest via `OptionBacktestReq.slippage_config`
- New module `backend/app/volatility.py` with `VolatilityConfig` (spike_threshold=2.5, realized_window=5, baseline=11250 bars). `annotate_volatility()` adds 4 columns
- New route `POST /api/volatility/audit`
- 30 new tests

### Slice 6.5 — Live tick → 1m OHLC roller
- New module `backend/app/live_candle_roller.py` subscribes to `UpstoxMarketStreamManager` broadcast and aggregates per-(instrument, minute) OHLC buckets
- Flushes on minute rollover via `persist_index_candles_bulk` to `candles_1m`
- Stale-bucket flush on 5s timeouts
- Subscribe-before-task-start to avoid producer/consumer race
- New routes `GET /api/live-candles/status`, `POST /api/live-candles/start`, `POST /api/live-candles/stop`
- Auto-starts on backend boot after WS auto-start; auto-flushes on shutdown
- 8 new tests
- Closes a real gap discovered 2026-05-29: Upstox historical endpoint returns empty for the same trading day

### Slice 6 — Data Hygiene workflow + NSE holiday calendar
- New module `backend/app/data_hygiene.py` computes diff vs desired warehouse (default 2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE, sample=1m)
- Returns prioritized actions per instrument (spot, contracts, option_candles) with ETA hints
- New module `backend/app/nse_calendar.py` with hand-curated NSE holidays for 2024–2026 plus `SPECIAL_SATURDAY_SESSIONS` (2025-02-01, 2026-02-01) and `SHIFTED_EXPIRY_DAYS` (e.g., 2026-01-14 SENSEX shift)
- New routes `POST /api/data-hygiene/{plan,execute}`, `GET /api/data-hygiene/status`
- Wired into hygiene plan so spot coverage no longer false-flags holidays
- 17 new tests

### Slice 5 — Pre-flight data realism panel + active-expiry contract picker fix
- New module `backend/app/deployment_preflight.py` checks spot coverage last 30 days, upcoming option expiries, active vs expired contracts, Upstox token state
- Per-instrument structural break notes (NIFTY weekly day rotation, BANKNIFTY weekly discontinued Nov 2024, SENSEX BSE Friday)
- New route `GET /api/deployments/preflight?instrument=...`
- Tightened `_resolve_option_contract` to filter `expiry_date >= today` with new blocker `option_contract_no_active_expiry`
- Frontend: `PreflightBadge` collapsible above Create button
- 8 new tests

### Slice 4 — Paper trade auto-creation on signal approval
- Approve route auto-creates a paper trade when signal carries `deployment_id` AND `deployment.mode == "paper"`
- Trade uses `lot_size` from option contract (Upstox-supplied), `lots` from `deployment.risk.default_lots` (default 1)
- Stamps `deployment_id` and `source="paper_auto_on_approval"`
- Failure to create trade does NOT roll back approval — records `paper_trade_error`
- Frontend: mode badge on pending signal card, "Approve + Paper" button label when applicable
- New form fields: DTE filter input, default lots, allow-overnight checkbox

### Slice 3 — Auto-square-off at 15:00 IST + expiry-day cutoff + dte_filter
- New module `backend/app/paper_squareoff.py` background loop
- Closes all OPEN paper trades once per market day at 15:00 IST. Skips trades whose deployment has `risk.allow_overnight=true`
- Exit price priority: WS tick → last_price → entry_price (zero-PnL fallback)
- Idempotent
- Expiry-day cutoff: blocks new signals on the deployment instrument's expiry day at 15:00 IST (looked up from `option_contracts.expiry_date`, never weekday-hardcoded)
- New deployment fields: `option_policy.dte_filter` (default `[0,1,2,3,4,5,6]`), `risk.allow_overnight` (default false)
- Audit trail extended with `bar_ts`, `decision_ts`, `next_expiry_iso`
- 14 new tests

### Slice 2 — Approval UI (Approve / Skip / Mark Blocked)
- New routes `POST /api/signals/{id}/approve` (CONFIRMED → TRIGGERED → ACTIVE with audit), `/skip` (CONFIRMED → SKIPPED → AUDITED), `/mark-blocked` (any non-AUDITED → AUDITED + blockers)
- Frontend: `PendingApprovalPanel` above existing console showing only CONFIRMED deployment-generated signals with three buttons + optional note input
- Auto-refresh signals list every 15s
- Evaluate-now button on each ACTIVE deployment card
- 6 new tests

### Slice 1 — 1m_close deployment evaluator
- New module `backend/app/deployment_evaluator.py`
- Pulls last N candles, runs strategy.evaluate() on closed bar, applies pretrade filter, picks ATM/OTM1/ITM1 contract step-aware from option_contracts
- Journals clean (CONFIRMED) or blocked (AUDITED with blockers) signals
- Time-of-day windows: blocks 09:15–09:25 and 14:50–15:30 IST
- `option_no_data` flag when contract has no candle in last 5 min
- Idempotency via `last_evaluated_ts`
- Concurrency rule: keep highest-score per `(instrument, candle_ts)`
- Background scheduler in `server.py` wakes 10s after each minute boundary during NSE market hours
- New routes `POST /api/deployments/{id}/evaluate-on-close`, `POST /api/deployments/evaluate-active`
- 13 new tests

## [0.8.0] — Phase 4 Foundation
- Upstox OAuth + encrypted token storage
- Upstox V3 read-only WebSocket market-data stream with sanitized tick persistence
- Upstox 1m index historical ingest with automatic chunk guidance and background jobs
- Option contract sync, expired contract backfill, option candle fetch with OI preservation
- Option Data Planner with preview-first workflow, ATM-only default, indexed lookup
- Option Coverage Heatmap and Raw Option Universe Audit
- Persistent market header (NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY) with WS-first fallback
- Theme: System / Black / White via CSS variables
- Offline signal lifecycle (`WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`)
- Manual paper trading journal with stop/target auto-close
- Strategy Deployment management foundation (CRUD only — evaluator added in Slice 1)

## [0.7.1] — Local Bootstrap Repair + Status Reconciliation
- Fixed backend syntax issue blocking local startup
- Added missing backend/frontend env examples and frontend `yarn.lock`
- Removed unavailable Emergent-only backend dependency and added runtime imports required locally
- Verified Docker Desktop + Compose stack on Windows (`mongo`, `backend`, `frontend` healthy)
- Removed obsolete Compose `version` key
- Updated dashboard/sidebar phase status to reflect Phase 4a scaffold and verified local deployment
- Rewrote handoff/setup notes for local PC development instead of hosted Emergent-only operation

## [0.7.0] — Phase 7: Local Deployment Package
- Added `docker-compose.yml` with mongo/backend/frontend services + persistent volume
- Added `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage build → nginx)
- Added `frontend/nginx.conf` with gzip + SPA fallback + static caching
- Added `start.sh` (Mac/Linux) + `start.bat` (Windows) launchers
- Added `.env.example` templates for backend + frontend
- Added `docs/LOCAL_SETUP.md` step-by-step guide (Docker + Native)
- Added comprehensive documentation: `README.md`, `docs/ARCHITECTURE.md`, `docs/HANDOFF.md`, `docs/STRATEGY_PLUGINS.md`, `docs/API_REFERENCE.md`

## [0.6.5] — Phase 3.5: User-feedback Fixes
- Fixed: progress bar not filling during optimizer run (added `bg-info` Tailwind utility)
- Added: optimizer auto-saves best params as a full `backtest_run` (with trades + equity + walk-forward) linked via `optimization_job_id`
- Added: "View Best in Lab" button → navigates to `/backtest?run=<id>`
- Added: 3 export buttons on Optimizer (Config JSON, Result JSON, Alts CSV)
- Added: "Saved Presets" panel in Optimizer left sidebar
- Added: "Load preset (optimized params)" dropdown in Backtest Lab
- Added: URL deep-link `/backtest?preset=<name>` auto-applies preset
- Added: Stop button on running optimization (`POST /api/optimize/jobs/{id}/cancel`)
- Added: graceful cancellation — worker checks `cancelled` flag every 5 trials
- Added: CANCELLED status badge

## [0.6.0] — Phase 3: Auto-Optimizer
- New `/api/optimize/*` routes (start, list, get, delete, apply-as-preset)
- Optuna TPE (Bayesian), Grid Search (sampled), CMA-ES (Genetic) samplers
- 6 objectives: risk_adjusted (default), sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd
- Walk-forward integrated; pre-compute indicators ONCE per job for 100× speedup
- Robustness scoring (±10/20% perturbation, % staying within 85% of best)
- Parameter importance + 2D heatmap of top-2 important params
- Top-N alternatives ranking
- Optimizer page with progress polling, best-so-far card, status badges, full result cards, job history

## [0.5.0] — Phase 2.5: BacktestLab Polish
- NumberSliderInput: combined slider + typeable number box
- Date window picker on backtest config
- Save with name + reload via "Load past run" dropdown
- Export Config JSON / Full Result JSON / Trades CSV
- Signal Journal: filter, bulk-select, bulk-delete, click-row-to-load via `/backtest?run=<id>` deep-link

## [0.4.0] — Phase 2: V1 Full Lab
- 6 built-in strategies: Confluence Scalper, VWAP Pullback Scalp, ORB, SMC Liquidity Sweep+FVG, Fibonacci Pullback, VWAP Mean Reversion
- Custom strategy plugin auto-discovery (drop `.py` file → restart)
- Data Warehouse v2 with per-day SHA-256 integrity hashes + coverage heatmap UI
- Multi-pane TradingView Lightweight Charts v5 (price + equity + drawdown synced)
- Pre-Trade Checklist: 3 profiles, 10+ configurable filters, anti-over-filter safeguard
- Statistical significance badge (Wilson 95% CI)
- Regime detector (ADX + Choppiness + ATR expansion)
- Signal funnel telemetry per backtest

## [0.1.0] — Phase 1: POC
- Single-file E2E proof
- yfinance ingestion → MongoDB persistence
- Vectorized indicators (EMA, RSI, MACD, ATR, VWAP, ADX, Choppiness)
- Confluence Scalper port
- Vectorized backtest with realistic Indian intraday cost model
- Walk-forward IS vs OOS validation
- Equity curve + drawdown series
- Statistical significance evaluation
