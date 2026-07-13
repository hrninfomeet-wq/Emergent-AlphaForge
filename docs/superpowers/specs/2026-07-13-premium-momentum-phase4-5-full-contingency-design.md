# Premium-momentum Phases 4+5 — full contingency ("Lazy Legs") design

Status: **design only, nothing implemented yet.** Amends/extends
`2026-07-10-premium-momentum-contingency-strategy-design.md` (the original spec) now that
Track A (backtest/cost/tuner) and Track B (live/paper execution) have shipped the **single-leg**
version. This doc re-scopes Phases 4 and 5 against a concrete AlgoTest-style blueprint the user
supplied a second time ("Configurable Contingency Breakout (NF CE PE EXP2 Base)") plus the
feasibility report it produced when run through the app's AI authoring wizard, and against a real
bug the user hit trying to optimize the shipped strategy through the general Optimizer page.

## 1. What's already built (do not re-build)

`premium_momentum` (id `premium_momentum`, `backend/app/premium_momentum*.py`,
`backend/app/strategies/plugins/premium_momentum.py`, `deployment_evaluator.py`'s dedicated
branch, `live_sl_monitor.py`'s `stepped_xy` mode) is a **single-leg, first-to-trigger** strategy:
at one reference time, lock ONE moneyness's CE+PE strikes, snapshot both premiums, and enter the
FIRST side whose premium crosses the momentum threshold — the other side is never traded that
session. Exits: stop/target + stepped X-Y trail. It rides the standard deploy/arm/guard rails with
no new gate. Full detail: `docs/STRATEGY_DEPLOYMENTS.md` → "`premium_momentum`: a lock-driven
deployment variant", `docs/DEVELOPER_GUIDE.md` §E "Premium-momentum: a strategy driven by a locked
strike + premium trigger, not spot".

**Two real gaps found this session, both explained by what's NOT built yet (Phases 4/5), not by
new bugs:**

1. **Feasibility checker blind spot.** `backend/app/ai/capability.py`'s `classify_rule` /
   `allowed_columns` model only knows how to map a rule onto spot-derived 1-minute OHLCV columns
   (the generic `evaluate()` pattern). It has **zero concept** of premium-native triggers,
   time-locked strikes, or lazy-leg contingency — so pasting the AlgoTest blueprint into the AI
   authoring wizard returns a blanket REJECT on every single rule line, including ones the app
   already handles at the deployment layer (EOD close, entry-time gates). This is a real,
   user-facing bug born of Phase 4 never having happened: the capability system was never taught
   that `premium_momentum`-shaped rules are buildable via config.
2. **Optimizer produces zero paired trades on `premium_momentum`.** Its plugin `evaluate()` is
   *intentionally* inert (a stub — the real logic lives only in the evaluator's dedicated branch
   and the separate `/premium-momentum` page/route). Running it through the **general** Optimizer
   or Backtest Lab page calls that stub, which never returns a direction → zero spot signals →
   nothing for option re-rank to pair → the "Option re-rank produced no paired results" message.
   Not a data-coverage problem — the strategy is simply siloed to its own bespoke page today.

## 2. The target blueprint (verbatim requirements, for traceability)

See the user-supplied "Configurable Contingency Breakout (NF CE PE EXP2 Base)" blueprint. Key
structural difference from what's shipped: **both CE and PE primary legs are primed
simultaneously** at the global entry time (not first-to-trigger-wins — both could independently
enter), each with its own lot size / target / SL / stepped trail, and **each primary leg's SL hit
arms a dormant opposite-side "lazy leg"** that takes a **fresh premium snapshot at the moment of
activation** (not the original entry-time snapshot) and runs its own independent
momentum/target/SL/trail. Plus session-level overlays: max positions per leg per day, a re-entry
cutoff time, and a global target/SL that closes everything.

This confirms the original spec's own scoping was correct: **"simultaneous two-leg tracking is
Phase 5"** (`2026-07-10-...-design.md` line ~170) — today's single-leg first-to-trigger is a
deliberate simplification, not a faithful implementation of this blueprint. Nothing here was lost
or mis-scoped; Phase 5 was always going to be required for this exact strategy shape.

## 3. Phase 4 — config graduation (re-scoped)

Goal (unchanged from the original spec, sharpened by the two bugs above): **one engine rule, no
per-strategy code**, usable through the *standard* Optimizer / Backtest Lab / Deployment UI — not
a bespoke page.

Concretely:

1. **Declarative config block** (attached to a deployment's `option_policy`, or a new
   `premium_trigger` block alongside it) covering the parameters **actually shipped today**:
   `reference_time`, `moneyness`, `side`, `momentum_pct`/`momentum_pts`, `stop_pct`, `target_pct`,
   `late_lock_cutoff` (plugin params — `strategies/plugins/premium_momentum.py`), the backtest's
   `trail_x`/`trail_y` (`premium_momentum_backtest.py`) and `lots`, and live's `stepped_xy` trail
   mode sourced from `deployment.risk.exit_controls` (not a plugin param today). **Note**:
   `entry_window`/`exit_window`/a bare `cutoff`, and the blueprint's max-positions-per-day /
   re-entry-cutoff / global target-SL are NOT shipped anywhere yet — they map to Phase 5's
   session-overlay work (§4.3) or to existing deployment-layer mechanisms (time-of-day blocks,
   `live_deploy_governor.py`'s caps), not to new strategy params. Don't go looking for
   already-built knobs that don't exist. This is a *lift*, not a rewrite — `premium_momentum.py`'s
   pure helpers (`lock_reference_strike`, `momentum_triggered`, `walk_premium_momentum`,
   `stepped_trail_stop`) already implement the rule; Phase 4 is about making the **evaluator
   dispatch on config presence**, not on `strategy_id == "premium_momentum"`, so any strategy
   could opt in.
2. **Wire it into the general path.** `backtest.run_backtest` / the Optimizer / Backtest Lab pages
   need a way to run a premium-trigger config through the SAME sim, cost model, and honest tuner
   that `premium_momentum_backtest.py`/`_tuner.py` already provide — likely by having the generic
   backtest dispatcher recognize the config block and delegate to the existing option-native sim,
   rather than trying to force premium logic through the spot-first two-stage engine. **Do not
   lose the honest-tuning discipline** (costs mandatory, chronological train/OOS, overfit flag —
   `docs/DEVELOPER_GUIDE.md` §G) when generalizing it.
3. **Teach the capability/feasibility system.** Extend `backend/app/ai/capability.py` (and
   whatever in `compiler.py`/`spec_schema.py` builds `allowed_columns`) to recognize a rule that
   matches the premium-trigger shape (entry-time snapshot, premium %/pts momentum, premium
   stop/target, stepped trail, EOD/time-window gates) and return a **mapped ACCEPT** pointing at
   the config block above, instead of a blanket per-rule REJECT. This directly fixes the bug the
   user hit. **Watch for a token collision**: `capability.py`'s existing token map (line ~193)
   maps the bare word `"premium"` to the ICT `premium_discount` zone concept (an unrelated
   structural-feature classification, R5 `BUILDABLE_WITH_FEATURE`) — depending on how the AI
   mapper tokenizes a rule, an *option-premium* rule can currently be misclassified as an ICT
   premium/discount rule rather than falling through to the R9 blanket reject. The new
   premium-trigger detection must disambiguate "option premium" from "ICT premium/discount zone"
   or it will get silently shadowed by the existing R5 path instead of firing. The session-level
   gates (entry time, exit time, max-positions-per-day) should map to the **existing**
   deployment-layer mechanisms (time-of-day blocks, EOD square-off,
   `max_lots_per_day`/`max_concurrent`/daily caps in `live_deploy_governor.py` /
   `deployment_kill_switch.py`) rather than new strategy-internal code — call this out explicitly
   in the capability report so the user understands *why* they map there and not into the
   strategy's own logic.
4. **UI**: a config-block builder in the deploy/authoring flow (reference-bar time, moneyness,
   direction, momentum unit, SL/TGT units, stepped X-Y fields, lots, caps) — the AlgoTest-mapper
   output already gestures at this shape; Phase 4 makes it a first-class form instead of only
   reachable via the bespoke `/premium-momentum` page.
5. **Regression safety.** The existing single-leg `premium_momentum` deployment behavior must
   remain byte-identical when a deployment's config collapses to today's shape — this is a
   generalization, not a rewrite of proven logic. Cover with a parity test (same fixture, same
   trades, before/after the dispatch-on-config change).

## 4. Phase 5 — two-leg + lazy-leg contingency (re-scoped)

Goal (unchanged from original spec, now grounded in the concrete blueprint): a genuine multi-leg
state machine, hard-gated on Phase 4 landing first (the contingency should be expressible in the
same declarative config, not more bespoke code).

1. **Simultaneous primary legs.** Both CE and PE armed independently at the reference time, each
   with its own lot size / target / SL / stepped trail (the blueprint's Leg 1 / Leg 2). Either,
   both, or neither may enter — this is a real behavior change from today's first-to-trigger
   exclusivity, so it must be an explicit config choice (`mode: "first_to_trigger"` keeps today's
   behavior; `mode: "both_legs"` is the new one) — **do not silently change existing deployments'
   behavior.**
2. **Lazy-leg contingency.** On a primary leg's SL hit, arm the dormant opposite-side leg: take a
   **fresh premium snapshot at the activation bar** (1-minute-bar-close granularity in backtest and
   the live evaluator — the blueprint's "exact millisecond" is explicitly out of reach, same
   limitation the original spec already accepted for the primary leg, now doubled for the lazy
   leg), select its own strike (own moneyness criteria), and run its own independent
   momentum/target/SL/stepped-trail — all via the SAME pure helpers as the primary leg, called a
   second time with a new reference point. This is a new position, not a modification of the
   closed one; both must coexist in the backtest ledger and the live guard/broker without
   colliding (two independent `premium_locks`-style records, two independent guard entries).
3. **Session overlays.** Max positions per leg per day (re-arm counter), re-entry cutoff time (no
   new entries OR lazy-leg activations after this time), and a global target/SL that closes ALL
   open legs for the deployment when hit (a new evaluator-level check, not per-leg).
4. **Backtest + live parity** via the same shared-pure-helper discipline Track A/B established —
   the backtest sim gets a second call to the walk/trigger helpers for the lazy leg; the live
   evaluator/guard gets a second lock record and a second guard registration, keyed so recovery
   can tell primary and lazy legs apart (extend `premium_locks`' schema, don't invent a parallel
   store).
5. **Hard gate (from the original spec, still correct):** only build this after Phase 4 lands and
   after the single-leg edge-search (Track A's tuner) or a two-leg backtest shows the contingency
   is *measurably* better than the plain single-leg version, not merely different. Building an
   elaborate multi-leg machine for a pattern that has no edge is exactly the mistake the honest
   Track-A tuner was built to prevent.

## 5. Non-negotiable invariants for whoever builds this (Phase 4 or 5)

- **No new arming gate.** Rides the existing per-deployment ARM + `LIVE_AUTOPLACE_ARMED` /
  `LIVE_GUARD_ARMED` + caps chain, exactly like the single-leg version. This has already had to be
  explicitly enforced once — see `docs/HANDOFF.md` §4 and `docs/DEVELOPER_GUIDE.md` §I.
- **Millisecond precision remains out of reach.** 1-minute bars in backtest; live is tick-driven
  but the evaluator's own poll loop runs on a ~2-3s new-bar-gated cadence (`runtime.py`), not
  tick-perfect. State this plainly in any UI/docs for the lazy leg — don't imply
  millisecond-accurate activation just because live ticks are involved.
- **TDD + host/container test split + the honest-tuning pattern** (§G in DEVELOPER_GUIDE.md) must
  be followed, not shortcut, given this touches the live evaluator and guard.
- **A subagent/workflow panel that returns 0 completed agents is not a passed check** — see
  DEVELOPER_GUIDE.md §H "Subagent / workflow orchestration". If building this via an agentic
  pipeline, treat account/session-limit failures as unverified work, not a clean pass.
