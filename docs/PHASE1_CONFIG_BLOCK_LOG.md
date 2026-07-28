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

---

## ✅ STEP 1c — the classifier's promises are now checkable (suite 3844/0)

### Step 0 · Agent B — promise inventory (LANDED), and it found FALSE promises

`classify_rule` messages are read by two audiences that both act on them: the
human in the wizard, and **the authoring LLM itself** (they are fed back as
grounding). A message naming a field that does not exist therefore induces the
LLM to emit a config referencing it, which dies against `extra="forbid"` with an
error the user cannot act on.

**Two shipped false, verified directly against the model:**

| Message said | Reality |
|---|---|
| "mapped to the premium_trigger_config's **`expiry`** field" | `PremiumTriggerConfig` has **no `expiry` field**. Real mechanism is the deployment's `dte_filter` (days-to-expiry ints). |
| "the premium_trigger_config's `side` field **(CE\|PE\|BOTH)**" | `side` is `Literal["ce","pe","first_to_trigger"]` — **no `BOTH`**. Running both legs is `leg_mode="both"`, a different field on a different schema. |

**Why they shipped:** ~51 tests touch `classify_rule` and **not one pins message
text against the schema it cites** — every assertion is verdict-only or a loose
`"premium" in msg.lower()` substring. The classifier was verified to be
internally consistent, never to be *truthful*.

**Fixed:** both messages now name mechanisms that exist, plus
`tests/test_capability_promises_are_real.py` — any field a message attributes to
`premium_trigger_config` or to the deployment must exist on that model, and any
advertised `side` value list must match the real `Literal`. That is the durable
fix; the wording corrections alone would have re-rotted.

### 🔴 Scope corrections from agent B (these change Step 4's schema)

1. **`PremiumTriggerConfig` is NOT the surface to generalize.** The shipped
   `premium_momentum` plugin's real parameter surface is much richer:
   `leg_mode`, `lazy_enabled`, `lazy_momentum_pct`, `lazy_stop_pct`,
   `lazy_target_pct`, `lazy_moneyness`, `entry_cutoff`, `exit_time`,
   `session_max_loss_rupees`, `session_max_profit_rupees`, `vix_min`, `vix_max`
   — **none of which are on `PremiumTriggerConfig`**. A config block modelled on
   the Session-2 subset would under-serve the promises by a wide margin.
2. **Session gates are largely UNSERVED as literally promised.** There is *no*
   configurable time-of-day entry/exit window for an ordinary deployment —
   only a fixed 15:00 IST new-entry cutoff and a fixed 15:00 EOD square. A user
   cannot express "enter only 09:30–10:00" today; the `entry_cutoff`/`exit_time`
   that do exist are `premium_momentum`-plugin params.
3. **Live has no profit-target kill.** Paper gets both target and loss via
   `daily_caps`; live's `daily_loss_cap` is loss-only. "session_target" is a
   half-truth for live.
4. **Group G is stale in the opposite direction** — lazy-leg/two-leg is
   advertised as "Phase 5 future work, not yet shipped", but Phase 5B shipped
   2026-07-17. `capability_summary()`'s "future" list needs the same treatment.
5. `moneyness_selection` routes to premium-trigger, but a *generic*
   `option_moneyness` already exists on `DeploymentCreateReq` for any deployment
   — the broader, working mechanism is invisible in the promise text.

---

## ✅ STEP 1b COMPLETE — the backtest domain stops asking for an id (suite 3851/0)

New predicate `is_premium_trigger_strategy(strategy)` in
`premium_trigger_dispatch.py`. Six sites converted:

| Site | Was |
|---|---|
| `optimizer` OOS survival | `getattr(strategy, "id", None) == "premium_momentum"` |
| `optimizer` Stage-2 re-rank | same |
| `optimizer` Stage-1 preload | `strategy.id == "premium_momentum"` |
| `optimizer` Stage-1 evaluate closure | same |
| `optimizer` worker pinning ×2 | `strategy.id != "premium_momentum"` |
| `runtime` coverage preflight | `req.strategy_id == "premium_momentum"` |

**`"premium_momentum"` literal count in `optimizer.py`: 6 → 0.**

All six meant the same thing — *this strategy's `evaluate()` is a stub, so the
spot path would score it as zero trades* — and none actually cared about the id.

**Regression safety, measured not assumed:** the predicate is judged from the
strategy's DECLARED DEFAULTS (a stable property, so it cannot flip between
optimizer trials as params vary), and across all 12 shipped strategies it matches
exactly the one strategy the string matched:

```
premium_momentum -> PREMIUM-TRIGGER;  the other 11 -> absent
```

A partial/invalid config is deliberately NOT premium-native — hijacking the
option-native path with a config that cannot drive the sim would score a strategy
through machinery its own configuration never described.

The preflight also picked up the same raw-params re-apply as Step 1, or it would
have measured coverage for a *different* config than the run then uses.

**One pre-existing test updated, intent preserved.**
`test_preflight_report_has_premium_native_branch` pinned the literal string. Its
own docstring says the intent is "the preflight has a premium-native branch
reporting per-session locked-strike coverage (never the spot-derived 0%)" — that
is intact. The assertion now checks for the branch via the predicate AND adds a
new one forbidding re-acquisition of a hardcoded id gate, so it is strictly
stronger than before rather than relaxed.

### Remaining hooks (deployment domain — Step 3, safety-critical)
`deployment_evaluator.py:479` (Track B) and its nested day-stop / VIX / 
`square_at_ist` / exit-plan blocks, plus `runtime.py:244` (live guard-close lazy
arm) and `paper_auto.py:739` (paper lazy arm). These are the ones that make a
strategy actually TRADE, and the original design doc flagged them as needing
their own dedicated session — treated accordingly.

---

## ✅ STEP 2 COMPLETE — the deployment has the block it already promised (3862/0)

`capability.py:174,438` told users *and the authoring LLM* to "configure on the
deployment's premium_trigger block". That field existed nowhere. Now it does:

* `DeploymentCreateReq.premium_trigger: Optional[Dict]`
* `build_deployment_doc(premium_trigger=...)` validates it against
  `PremiumTriggerConfig` **at creation** and raises → the route returns 400
* stored on the doc, and **omitted entirely when absent** (a null would read as
  "configured but empty" downstream)
* `resolve_deployment_premium_trigger(deployment) -> (cfg, source)`

**Precedence: block wins, `params` is the fallback.** Every premium-trigger
deployment created before the block existed carries its config in `params`, so
absence resolves exactly as before — those deployments are byte-identical.

**`source` distinguishes four states**, and `"invalid"` is deliberately NOT
`None`: a malformed config must never be mistaken for "this strategy has none",
or the deployment silently runs a different path.

**Creation-time validation closes agent C's risk #4.** Previously
`build_deployment_doc` never validated `params` against anything, so a malformed
config was accepted cleanly and only manifested as a silent no-op on the first
evaluated bar. `extra="forbid"` now surfaces at creation — which is precisely the
error an authoring LLM produces when it invents a field name, so it lands where
the user can act on it.

The resolver reads `params` **RAW**, so Step 1's allow-list drop
(`stop_pts`/`target_pts`/`trail_x`/`trail_y`) cannot reappear on this path.

### Next: Step 3 — Track B routes on the resolver (SAFETY-CRITICAL)
`deployment_evaluator.py:479` plus the nested day-stop / VIX / `square_at_ist` /
exit-plan blocks, then the live (`runtime.py:244`) and paper
(`paper_auto.py:739`) lazy-arm sites. This is the step that makes an authored
config actually TRADE, and the original design doc flagged it as needing its own
session. It now has a validated, single-source resolver to route on.

---

## 🔄 STEP 3 — Track B routes on capability (SAFETY-CRITICAL) — design pinned first

### The hazard that dictates the design

Track B **replaces** `strategy.evaluate()` entirely — the branch runs a session
state machine and the plugin's `evaluate()` is never called. That is correct for
`premium_momentum` (its `evaluate()` is a deliberate stub returning
`direction="NONE"`).

So the naive generalization — *"any deployment carrying a premium_trigger block
takes Track B"* — is **dangerous**: attaching a block to, say,
`confluence_scalper` would silently disable that strategy's real logic and run a
completely different engine, with no error. The strategy would appear to work
while trading on rules the user never chose.

### The split that avoids it

| Question | Answered by | Why |
|---|---|---|
| **WHO** takes Track B | `is_premium_trigger_strategy(strategy)` — the STRATEGY's own declared defaults | Only a strategy that declares itself premium-native (i.e. whose `evaluate()` is a stub) may have `evaluate()` bypassed. A deployment block can never hijack an ordinary strategy. |
| **WHAT** config it runs | `resolve_deployment_premium_trigger(deployment)` — block wins, `params` fallback | Configuration is a per-deployment concern; capability is a per-strategy one. |

This is the same predicate Step 1b uses for the backtest domain, so backtest and
deployment agree on what "premium-native" means — one definition, two domains.

It also sets the contract Step 4 must satisfy: an AI-authored strategy becomes
premium-native by emitting premium-trigger **defaults in its own
`parameter_schema`**, not merely by having a block attached at deploy time.

### Sites (in dependency order)

1. `deployment_evaluator.py:479` — the branch condition itself
2. nested inside it: day-stop (`:489-501`), VIX gate (`:507-509`)
3. `deployment_evaluator.py:706-727` — exit-plan / lazy-leg risk-hint shaping
4. `deployment_evaluator.py:733-736` — `square_at_ist` population
5. `runtime.py:244` — live guard-close finalize + lazy arm
6. `paper_auto.py:739` — paper exit-marker lazy arm

### Invariants for this step
- `premium_momentum` behaviour must stay byte-identical (it is the only strategy
  the predicate matches today — measured, all 12 checked).
- No safety control weakened: caps, guard, kill switch, account caps and the
  transmit fence are untouched and continue to apply to Track B signals.
- An **invalid** config must refuse the bar, never fall through to the ordinary
  path — the deployment would otherwise trade on rules its config never described.

### Status
- [ ] 3a — branch + config source (sites 1-2)
- [ ] 3b — exit-plan + `square_at_ist` (sites 3-4)
- [ ] 3c — live + paper lazy arm (sites 5-6)
