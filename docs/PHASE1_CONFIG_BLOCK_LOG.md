# Phase 1 — config-block generalization: working log

> **CHECKPOINT FILE.** Updated and committed continuously so any session (or any
> agent) can resume with zero context loss. Newest findings appended per step.
> Companion: `docs/CAPABILITY_PHASE_PLAN_2026-07.md` §Phase 1 (the plan),
> `docs/AGENT_TODO.md` (the board).

**Started:** 2026-07-28 · **Status:** 🔄 STEP 0 — research fan-out running

---

## The problem, restated

`classify_rule` answers **BUILDABLE_NOW** for premium-trigger, session-gate,
position-sizing and expiry/instrument concepts — the app tells the user their
plain-words strategy is buildable. It then cannot build it:

| Layer | Reality | Evidence |
|---|---|---|
| `StrategySpec` | no field for any of it | `ai/spec_schema.py:42-56` |
| `compile_spec` | emits only `entry_ce`/`entry_pe`/`exits`/`gate_skip_regimes`/`cooldown_bars` | `ai/compiler.py:262-359` |
| Full-Python prompt | never mentions these mechanisms | `ai/py_author.py:19-56` |
| Backtest dispatch | `if strategy_id != "premium_momentum": return None` | `premium_trigger_dispatch.py:181` |

The dispatch file contradicts its own stated design directly below that line:
*"the whole point of Phase 4 dispatch is to route on CONFIG PRESENCE, not on
`strategy_id == 'premium_momentum'`."* The generalization was specified and never
finished. **This is a completion, not an invention.**

## Ordered plan (dependencies are real — mostly sequential)

| Step | What | Depends on | Status |
|---|---|---|---|
| 0 | Research fan-out (3 parallel read-only agents) | — | 🔄 RUNNING |
| 1 | Route dispatch on CONFIG PRESENCE, keep byte-identical parity for `premium_momentum` | 0 | ⬜ |
| 2 | `config_block` on `StrategySpec` + compiler support | 1 | ⬜ |
| 3 | Teach both generators the mechanism exists | 2 | ⬜ |
| 4 | Config-block builder in the authoring wizard UI | 2 | ⬜ |
| 5 | Wire the live/paper evaluator on the same schema | 1,2 | ⬜ |
| 6 | Couple the feasibility gate to the generator (gate is currently skippable) | 3 | ⬜ |

## Non-negotiable invariants for this phase

1. **Byte-identical parity for `premium_momentum`.** There is an existing parity
   test (`tests/test_premium_trigger_dispatch_parity.py`); it must stay green
   untouched. Any change that alters the shipped strategy's numbers is wrong.
2. **No safety control is weakened.** Config-driven strategies go through the
   SAME gates: caps, guard, kill switch, account caps, transmit fence.
3. **The compiler stays deterministic.** No `eval`/`exec`; every literal
   `repr()`'d; every column whitelisted (`ai/compiler.py:8-20`). A config block
   must be validated the same way, not passed through as opaque user data.
4. **Fail closed on an unbuildable config.** If a config block references a
   mechanism the engine cannot serve, refuse at authoring time with an
   explanation — never install a strategy that silently no-ops (the
   `vix_boost_threshold` dead-knob class of defect, now guarded by
   `tests/test_strategy_column_contract.py`).

## Findings log

### Step 0 — research fan-out (2026-07-28)
Three read-only agents dispatched in parallel:
- **A** — `PremiumTriggerConfig` surface: fields, `_CONFIG_FIELDS`,
  `to_backtest_params()`, what the parity test actually pins.
- **B** — capability taxonomy: exactly which BUILDABLE_NOW concepts map to which
  mechanism, so the config block covers the real promises rather than a guess.
- **C** — live/paper evaluator dispatch: where a config-driven strategy must hook
  in to actually deploy, not merely backtest.

_(results appended below as they land)_

### ⚠️ Step 0 finding — the 5F DATA-column work landed DURING Phase 0, and it changes this phase

`af59f50` → `a301cc0` landed directly on top of my `c3d6388` (Phase 0 complete),
i.e. while Phase 0 was being verified in the browser. Two of my earlier statements
are now **obsolete and must not be carried forward**:

| I said (earlier this session) | Actual state now |
|---|---|
| "`capability.py` still declares `has_vix_history: False` — the wizard may be refusing rules against data that exists" | **FIXED.** `capability.py` now sets `has_vix_history: True`, with a comment citing the 2026-07-27 backfill (412/413 NIFTY sessions) |
| "VIX is still not available as a per-bar signal — wire first, then unlock" | **DONE, correctly, in that order.** `backend/app/data_columns.py` joins warehouse-backed series onto the bar frame; a strategy opts in via `required_data=["vix"]` |

**This is a precedent, not just a fix.** `DataColumn` is exactly the shape the
`config_block` work should follow, and it already solved the hard problems:

- **Opt-in.** No declaration ⇒ the module never runs and the frame is
  byte-identical. Same guarantee `app.features` gives for structural features.
- **Causal by construction.** As-of join at-or-before the bar's own timestamp,
  bounded by `max_staleness_ms` — "not a convention callers must remember, it is
  the only join this module implements".
- **Honest about absence.** Missing ⇒ NaN, never a filled default, plus per-column
  coverage so a caller can say "present for 68% of your window" instead of letting
  a strategy silently score zero. The module's own docstring names the
  `vix_boost_threshold` dead knob as the failure mode it exists to prevent.
- **One staleness bound per quantity.** An adversarial pass caught a 4-day bound
  that would let the live session-start VIX gate PASS while the per-bar column read
  NaN — two different answers about one market state inside one deployment.
- Already threaded through the authoring path: `capability.classify_rule(...,
  required_data=())`, `compiler` validates against `DATA_COLUMN_REGISTRY` and
  refuses unknown columns.

**Consequences for Phase 1:**
1. VIX-conditioned authored rules are **already served** — drop that from scope.
2. `config_block` should mirror `required_data`'s contract: opt-in, registry-validated,
   refuse-unknown, byte-identical when absent. Invariant #4 in this doc is already
   the house pattern rather than something new to argue for.
3. Re-verify the promise inventory (agent B) against the CURRENT `capability.py`,
   not the version I read earlier in the session.

### Step 0 · Agent A — `PremiumTriggerConfig` surface (LANDED)

**The guard lives in TWO places, and `dispatch_backtest` is already generic.**

| Function | `strategy_id` guard? | Notes |
|---|---|---|
| `dispatch_backtest` (`premium_trigger_dispatch.py:235`) | **NONE — already strategy-agnostic** | This is what the parity test targets. **Do not modify it.** |
| `dispatch_full_backtest` (`:161`) | `if strategy_id != "premium_momentum": return None` at **`:181-182`** | builds the config itself from `merged_params` via `_CONFIG_FIELDS` |
| `runtime._run_paired_option_backtest` | **DUPLICATE guard** at **`runtime.py:1144`** | `if req.strategy_id == "premium_momentum" and ...` |

So "route on config presence" = change **two caller-side guards**, and leave
`dispatch_backtest` / `to_backtest_params` / `PremiumTriggerConfig` untouched —
precisely the three things the parity test protects.

**🐞 BLOCKING BUG FOUND — silent field loss (verified empirically, not just read).**
`StrategyBase.merged_params` is a strict allow-list keyed on the plugin's
`parameter_schema`. `premium_momentum`'s schema is missing **6 of the config's 14
fields**. Reproduced directly:

```
MISSING from plugin parameter_schema: ['cost_config','lots','stop_pts','target_pts','trail_x','trail_y']
SILENTLY DROPPED by merged_params:    ['cost_config','lots','stop_pts','target_pts','trail_x','trail_y']
```

`runtime.py:1149-1166` rescues only `lots` and `cost_config` (with a comment
acknowledging the allow-list). **`stop_pts`, `target_pts`, `trail_x`, `trail_y`
get no rescue** — configure a point-based stop/target or an X-Y trail and the
backtest silently runs WITHOUT it and reports the numbers as if it had. Same
class as the `vix_boost_threshold` dead knob and the `early_stop` no-op.

**This is a prerequisite, not a side quest:** the moment any authored strategy can
carry a config block, this drop hits every one of them. Fix it in Step 1.

**Also:** `_CONFIG_FIELDS` (`:73-77`) is a hand-maintained literal mirror of the
14 field names — no introspection. Derive it from `PremiumTriggerConfig.model_fields`
so it cannot drift.

**Parity test locks (must stay green, untouched):** byte-identical `trades` +
`coverage` + `summary` across 3 scenarios; dispatch only ADDS `dispatch` +
`premium_trigger_config` keys; validation still fails loudly (extra=forbid, both/
neither momentum, lone trail, bad HH:MM); `side`/`moneyness` case-folding;
config defaults still match the shipped plugin (`09:31`/`itm1`/`first_to_trigger`/`1`).

---

## ✅ STEP 1 COMPLETE — routing on config presence (suite 3839/0, parity green)

`extract_premium_trigger_config(params) -> (cfg, reason)` in
`premium_trigger_dispatch.py`. Three fixes in one change:

1. **Routes on config presence.** `dispatch_full_backtest`'s
   `strategy_id != "premium_momentum"` guard and `runtime.py`'s duplicate are both
   gone. `strategy_id` is retained for logging only. `dispatch_backtest`,
   `to_backtest_params` and `PremiumTriggerConfig` were NOT touched — they are what
   the parity test protects.
2. **Silent field loss fixed.** Extraction reads RAW params. `runtime` still fills
   plugin defaults first (a partial API request must behave like the filled UI
   panel) but then re-applies every config field from the raw request, so
   `stop_pts`/`target_pts`/`trail_x`/`trail_y` survive.
3. **Absent ≠ invalid.** A present-but-invalid config now returns
   `invalid:<detail>` and is REFUSED with a warning, instead of silently
   degrading to a different execution path that reports plausible numbers.

`_CONFIG_FIELDS` is now `tuple(PremiumTriggerConfig.model_fields)` — cannot drift.

### Step 0 · Agent C — the deployment hook map (LANDED)

**16 hook points gate on the literal string `strategy_id == "premium_momentum"`.**
Step 1 closed #12 (both halves). The rest, in priority order:

| # | Hook | Site | Domain |
|---|---|---|---|
| 1 | **Track B evaluator branch** | `deployment_evaluator.py:479` | **deployment — THE one that makes it trade** |
| 4 | session engine | `premium_momentum_live.py:204-450` | called only from #1 |
| 2,3,7,9 | day-stop / VIX gate / `square_at_ist` / exit-plan shaping | nested inside #1 | deployment |
| 5 | live guard-close finalize + lazy arm | `runtime.py:244` | live |
| 6 | paper exit-marker lazy arm | `paper_auto.py:739` | paper |
| 13 | coverage preflight | `runtime.py:1462` | backtest |
| 14 | optimizer survival/re-rank | `optimizer.py:731,887,1153,1191` | backtest |
| 10,11 | edge-verdict advisories | `routers/deployments.py:135`, `forward_metrics.py:528` | advisory only |

**Already generic — reusable with no new hooks:** both sinks
(`auto_live_trade_for_signal` / `auto_paper_trade_for_signal`), `exit_controls`,
the per-deployment kill switches, `risk_hints`/`blockers`/lifecycle, and
`square_at_ist` *enforcement* in the live guard (it is simply never populated for
a non-premium strategy).

### 🔴 Four risks agent C surfaced that change scope

1. **`capability.py` tells users to "configure on the deployment's
   `premium_trigger` block" — that field does not exist anywhere.** Confirmed
   absent from `strategy_deployments.py` and `routers/deployments.py`. The only
   way to actually trade this today is to set `strategy_id = "premium_momentum"`
   verbatim and put the knobs in ordinary `params`. This is the promise/reality
   gap at its sharpest, and it means **Step 2 must add a real
   `premium_trigger` block to the deployment doc**, not only to `StrategySpec`.
2. **Paper silently ignores `exit_time`/`square_at_ist`.** Populated into
   `risk_hints`, enforced only on the LIVE path (`live_position_guard`);
   `paper_auto` never reads it. A config promising an early square-off is honoured
   live and silently ignored on paper — a real parity gap that would carry over.
3. **Sizing replay is dropped for the premium-native path.**
   `dispatch_full_backtest` returns `"sizing_config": None`, so a deployment
   created from such a run silently falls back to `default_lots` instead of
   replaying the config's own `lots`.
4. **`build_deployment_doc` never validates `params` against the strategy's
   `parameter_schema`** — a malformed authored config passes creation cleanly and
   only no-ops at evaluation time.

### Revised step order (was: spec first)

Agent C's risk #1 inverts it: the deployment doc needs the config block before
the spec is worth emitting one, otherwise authored configs still cannot trade.

| Step | What | Status |
|---|---|---|
| 1 | dispatch routes on config presence | ✅ DONE |
| 1b | optimizer + coverage-preflight guards (#13, #14) — same class as Step 1 | ⬜ NEXT |
| 2 | `premium_trigger` block on the DEPLOYMENT doc + validation (risk #1, #4) | ⬜ |
| 3 | Track B evaluator routes on config presence (#1-#4,#7,#9) — safety-critical, own step | ⬜ |
| 4 | `config_block` on `StrategySpec` + compiler | ⬜ |
| 5 | teach both generators | ⬜ |
| 6 | wizard config-block builder UI | ⬜ |
| 7 | paper `square_at_ist` parity (risk #2) + sizing replay (risk #3) | ⬜ |
