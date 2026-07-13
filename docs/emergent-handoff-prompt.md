# Handoff prompt for continuing development on Emergent.sh

Copy everything below the line into the new agent session. It is written to be self-contained —
the receiving agent has none of the context from the repo's prior AI sessions, only what's in the
repo itself plus this prompt.

---

## Context

You're continuing development on **AlphaForge Trading Lab**, a local-first research +
forward-test app for Indian index options (NIFTY / BANKNIFTY / SENSEX). Stack: React
(CRA+craco) frontend, FastAPI backend, MongoDB (motor), Docker Compose. **Before writing any
code, read in this order:**

1. `docs/HANDOFF.md` — the shortest useful orientation, read it first.
2. `docs/DEVELOPER_GUIDE.md` — the consolidated onboarding: run/build/test, the live-trading
   safety model (read §E twice, it says so itself), data-warehouse model, India rules,
   research→deploy flow, and a "Tips, tricks & gotchas" section you should not skip.
3. `docs/ARCHITECTURE.md` — module map, data flow, Mongo collections, the live gate chain.
4. `docs/STRATEGY_DEPLOYMENTS.md` — the deployment model, including a section specifically on
   `premium_momentum` (the strategy family you're extending).
5. `docs/superpowers/specs/2026-07-10-premium-momentum-contingency-strategy-design.md` — the
   ORIGINAL design for this strategy family (why it's phased the way it is).
6. `docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md` —
   **the actual spec for the work you're picking up.** It re-scopes Phases 4 and 5 against a
   concrete blueprint (below) and documents two real bugs the current build has. Read it in full
   before planning anything.

The `CHANGELOG.md` (top entries) tells you what shipped most recently and when.

## What's already built — Phases 0–3 (do not re-implement)

`premium_momentum` is a working, deployed, single-leg strategy: at a configurable reference time
it locks one moneyness's CE+PE strikes from spot, snapshots both premiums from live ticks, and
enters the FIRST side whose premium rises past a momentum threshold (points or %). Exits via
premium stop/target plus a stepped X-Y trailing ratchet. It runs backtest (option-native sim +
a cost-model + an honest chronological-train/OOS tuner), paper, and live — through the app's
**standard** deployment/arm/guard rails, with **no strategy-specific arming gate** (a deliberate,
explicit design decision — do not add one). Relevant modules: `backend/app/premium_momentum*.py`,
`backend/app/premium_lock_store.py`, `backend/app/premium_pin.py`,
`backend/app/strategies/plugins/premium_momentum.py`, the dedicated branch in
`backend/app/deployment_evaluator.py`, the `stepped_xy` mode in `backend/app/live/live_sl_monitor.py`.

**Caveat carried forward from CHANGELOG 0.52.0, still true**: the live path has **not yet been
validated in a real market-hours session** (needs a live weekday run). Treat the shipped live
behavior as code-reviewed and test-covered, not as a market-proven baseline, when you build Phase
4's backward-compatibility parity tests or touch the guard for Phase 5.

**Its default blueprint parameters have no measured edge** on 2026-H1 NIFTY (verified by the
honest tuner — see the CHANGELOG 0.51.0 entry). That's expected and fine: this is a capability
build, not (yet) a validated money-maker. Don't chase "making the default profitable" — that's a
separate, later exercise once the full contingency shape below exists to test.

## Two real bugs you'll be fixing as part of this work (already root-caused, not open questions)

1. **The AI authoring "Check feasibility" panel rejects every rule of a premium-native blueprint**
   (see the attached blueprint + its feasibility report below). Root cause:
   `backend/app/ai/capability.py`'s `classify_rule`/`allowed_columns` model only understands rules
   expressible as spot-derived 1-minute OHLCV columns — it has zero concept of premium triggers,
   locked strikes, or lazy-leg contingency. This is a real, user-facing gap, not a re-confirmation
   that the strategy is impossible (most of it is already built). Fixing this is IN SCOPE for
   Phase 4 (see the spec).
2. **Running the shipped `premium_momentum` plugin through the general Optimizer page produces
   the literal string "Option re-rank produced no paired results."** (Backtest Lab hits the same
   root cause but surfaces it differently — as zero signals/trades, not that exact string.) Root
   cause: the plugin's `evaluate()` is deliberately a stub (the real logic lives only in the
   dedicated evaluator branch and its own `/premium-momentum` page) — the generic pages call the
   stub, get zero spot signals, and have nothing to pair. Fixing this — making the strategy
   runnable through the *standard* Optimizer/Backtest Lab UI — is the core of Phase 4.

## Your objective: Phase 4 + Phase 5

Read `docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md` in
full — it has the concrete scope, file pointers, and safety invariants. Short version:

- **Phase 4**: graduate the single-leg rule from bespoke per-strategy code into a **declarative
  config block** usable by the standard Optimizer/Backtest Lab/Deployment UI (one engine rule, no
  per-strategy code), and teach the capability/feasibility system to recognize this rule shape
  instead of blanket-rejecting it. Must be **byte-identical/backward-compatible** with the shipped
  single-leg behavior for existing deployments.
- **Phase 5**: extend to the FULL blueprint below — simultaneous CE+PE primary legs (not just
  first-to-trigger), and a **lazy-leg contingency**: when a primary leg hits its stop loss, arm
  the dormant opposite-side leg with a fresh premium snapshot and its own independent
  momentum/target/SL/trail. Plus session-level overlays (max positions/day, re-entry cutoff,
  global target/SL closing everything). **Hard-gated** on Phase 4 landing first and on a backtest
  showing the contingency is measurably better than the single-leg version, not just different.

**End state to confirm before you call this done**: pasting the blueprint below into the AI
authoring wizard's "Check feasibility" should return an ACCEPT (or an honestly-scoped partial
mapping, not a blanket reject), the resulting strategy should be tunable through the **same**
Optimizer/Backtest Lab pages as any other strategy, and it should be deployable to paper and live
through the standard chain — exactly like the single-leg version already is, just for the full
two-leg contingency shape.

## The target blueprint (build against this exactly)

```
# Strategy Blueprint: Configurable Contingency Breakout (NF CE PE EXP2 Base)

## 1. Strategy Overview
This is a conditional, delta-neutral-biased intraday momentum breakout strategy. It simultaneously
tracks a Call (CE) and Put (PE) option. Instead of entering immediately, it waits for a
user-defined momentum spike to trigger a position.

The core feature is its Contingency System ("Lazy Legs"). If a primary position is stopped out,
the strategy flips its bias and automatically activates an idle leg on the opposite side to catch
the market reversal. All parameters must be exposed as configurable variables in the Alpha Forge
UI or configuration file.

## 2. Global Configurable Parameters
- Instrument (default: NIFTY)
- Underlying Tracking: Spot/Cash
- Strategy Type: Intraday Same Day
- Entry Time (e.g. 09:31:00)
- Exit Time (e.g. 15:13:00)
- Re-entry Cutoff Time (e.g. 15:09:00)
- Max Positions Per Leg (default: 1)
- Global Target Profit (points/percentage/none)
- Global Stop Loss (points/percentage/none)

## 3. Primary Legs Configuration
At Global Entry Time, scan the options chain based on Strike Selection criteria.

### Leg 1: Primary Call (CE)
- Expiry: Weekly (default)
- Strike Selection: ITM1 (default)
- Position Size: 2 lots (default)
- Momentum Trigger: BUY when premium rises by a configurable % or points from the Global Entry
  Time snapshot
- Target Profit / Stop Loss: configurable (points/%/none), e.g. 20% SL
- Trailing SL: trail up by X for every Y increase in premium (e.g. 5%/5%), tracking the highest
  premium since execution
- On SL Hit: activate Lazy Leg 1

### Leg 2: Primary Put (PE)
- Same shape as Leg 1, mirrored. On SL Hit: activate Lazy Leg 2.

## 4. Contingency "Lazy Legs" Configuration
Dormant until their primary leg hits SL — no reference price or momentum tracking until then.

### Lazy Leg 1 (only if Primary CE hits SL)
- Option Type: PE (flips to the opposite side of the failed leg)
- Strike Selection: configurable (default ITM1)
- Position Size: configurable (default 2 lots)
- Momentum Trigger: takes a NEW premium snapshot at the moment of activation; BUY when premium
  rises by a configurable %/points from THAT snapshot (not the original entry-time snapshot)
- Target / SL / Trailing SL: same shape as the primary legs, independently configurable

### Lazy Leg 2 (only if Primary PE hits SL)
- Mirrors Lazy Leg 1 on the opposite side (flips to CE).

## 5. Execution Flow
1. At Global Entry Time, map required strikes and begin calculating momentum targets dynamically.
2. Place pending logic at the momentum targets (not necessarily resting broker orders — evaluate
   in software like the shipped single-leg version does).
3. Trailing calculation tracks the Highest High of premium since execution; percentage-based
   trailing must not be approximated with fixed points.
4. On a primary leg's SL_HIT, initialize the corresponding Lazy Leg, capture a new snapshot, and
   arm its momentum target.
```

## Feasibility report the current build produced against this blueprint (for reference — read but
## don't treat every REJECT line as gospel; several are explained by the blind spot above, not a
## real capability gap)

Every rule came back `Can't map this to anything derivable from 1m OHLCV` — including session/gate
rules (entry-time gate, EOD close, re-entry cutoff, max-positions-per-day) that the app already
handles at the deployment layer, not inside a strategy's `evaluate()`. Treat these specifically as
evidence the capability classifier needs a premium-trigger-aware code path (Phase 4), not as proof
those gates are unbuildable — they're built already, just invisible to today's classifier.

## Non-negotiable ground rules (violating any of these is treated as a serious regression)

- **`app/live/executor.py` is the sole ENTRY chokepoint** — no other module may call
  `client.place_order` for a new entry, ever. It is NOT the sole caller of `place_order` overall:
  legitimate EXIT paths call it directly and always will — `auto_square.py` (guard/kill-switch
  squares), `kill_switch.py`'s `panic_squareoff`, and `close_loop.py`. Route any new lazy-leg
  **entry** through the executor; route any new lazy-leg **exit** through the existing
  guard/auto_square/execution_policy machinery, not through the executor and not through a new
  bespoke path.
- **No new arming gate.** This strategy family rides the existing per-deployment ARM +
  `LIVE_AUTOPLACE_ARMED`/`LIVE_GUARD_ARMED` + caps chain. An earlier design draft had a
  strategy-specific validation gate and it was explicitly removed from the spec on request before
  any code was written — don't re-propose one.
- **Offline-first stays offline-first.** Both env kills default OFF; unset means dry-run,
  always.
- **TDD discipline.** Write the failing test first for any new behavior (host tests are pure/no
  motor; container tests touch motor/routes — see DEVELOPER_GUIDE.md §B for the exact split and
  commands).
- **Honest-tuning pattern**, if you touch the tuner: costs mandatory to enable tuning,
  chronological train/OOS split, overfit flag — do not let a "looks good on the whole window"
  result ship without an OOS check.
- **Never commit `.env`, tokens, or broker credentials.**
- **Push only when explicitly asked** — this repo's owner treats pushing as an explicit,
  per-changeset decision, not an automatic one, once you're iterating locally on your own fork/
  branch structure on this platform (adapt to whatever this platform's actual push/commit norms
  are, but don't silently auto-push to a shared branch without confirmation).
- **A subagent/review panel that returns 0 completed results is not a passed check.** If you're
  using any multi-agent review step and it dies without completing, say so explicitly rather than
  reporting a clean pass.

## Process recommendation

This repo's prior work on this exact feature (Phases 0–3) followed a spec → plan → TDD-execute →
adversarial-review discipline (see the `docs/superpowers/specs/` and `docs/superpowers/plans/`
directories for the pattern — read the Track B plan,
`docs/superpowers/plans/2026-07-10-premium-momentum-track-b-execution.md`, as a concrete example
of the level of detail expected: file:line-anchored task breakdowns, explicit test-first
descriptions, and a mandatory adversarial-review checkpoint before considering a phase done).
Follow the same shape for Phase 4 and Phase 5: write a plan doc before code, execute it in
discrete reviewable tasks, and adversarially review the diff against the safety invariants above
before calling either phase complete.
