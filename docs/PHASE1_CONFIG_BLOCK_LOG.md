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
