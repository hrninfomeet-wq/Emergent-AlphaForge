# Capability phase — the plan (2026-07-27)

**User priority, verbatim intent:** make **backtesting, paper trading and live trading
fully usable without constraints** and fit to hand to a user; and build the **strategy
builder** so a strategy described in **plain words** becomes a plugin that backtests,
optimizes, and deploys to paper and/or live. Edge hunting is explicitly **deferred**.

Two audits (authoring pipeline; backtest/paper/live friction) produced the evidence below.
Everything cited is file:line-verified.

---

## ▸ STATUS as of 2026-07-31 (read this before using the plan below)

| Phase | Status |
|---|---|
| **Phase 0** — unlock what is already built | ✅ **COMPLETE** 2026-07-27 (`e1bfd4c`, `6d89370`) — all four capabilities now have UI paths |
| **Phase 1** — config-block generalization | ✅ **COMPLETE** 2026-07-28/29 (`1abc3a9` + the authoring-loop fixes in v0.57.5) — a plain-words premium-trigger strategy now generates, installs, backtests, optimizes and deploys |
| **Phase 2** — remove the remaining friction | ➡ **NEXT.** Still open: spot-data preflight · async backtest error parity · **optimizer cancel honesty (now tracked as finding #17 in [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) §5, with #20 as its WFO twin)** · nav badges still read "P4"/"L0" (`Layout.jsx:36,39`) · `FERNET_KEY` still fails silently (`encryption.py:9-14` carries a comment, not a boot warning) · static-IP guidance. **Resolved since the plan was written:** `PositionMonitor.jsx` is wired (imported by `LiveDataProvider.jsx`) — do not delete it. |
| **Phase 3** — live-readiness | ⛔ still blocked on a Flattrade-registered static IP; `live_trades` is still empty. The scripted-readback prep is NOT blocked and has not been built. |

**One correction to the framing below:** the plan's premise that the backtest/optimizer
surface was merely *high-friction* was optimistic. The 2026-07-29→31 audit found it was
also **wrong in places** — see [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md).
The four confirmed HIGH findings are now closed; Phase 2 proceeds with the eight confirmed
MED findings and the truth/performance work in
[`NEXT_STAGE_ROADMAP_2026-07.md`](NEXT_STAGE_ROADMAP_2026-07.md) Stage 1.

---

## The one-line answer

> **Next step = Phase 0: unlock what is already built.** Four fully-implemented, tested
> backend capabilities have **zero** UI path — including a safety latch that can lock you
> out of live trading with no way back in. Days of work, no new design.
>
> **Then Phase 1: finish the config-block generalization** — the single change that turns
> the strategy builder from "per-bar indicator rules only" into the plain-words→plugin tool
> you actually asked for. It is a *completion of existing design*, not new invention.

---

## What is genuinely GOOD already (do not rebuild)

The authoring stack is stronger than its gaps suggest, and the plan below preserves all of it:

- **Two generation paths**, both real: *Spec mode* compiles a validated `StrategySpec` to
  Python **deterministically** — no `eval`/`exec`, every literal `repr()`'d, every column
  pre-whitelisted (`ai/compiler.py:8-20,262-359`); *Full-Python mode* runs an AST allowlist
  plus a **real subprocess smoke test** with an `RLIMIT_AS` memory cap
  (`ai/py_sandbox.py:129-194,224-264`).
- **Feasibility is decided deterministically, not by the LLM** — `classify_rule` returns
  typed verdicts, so the wizard rejects-with-explanation instead of degrading silently.
- **Install has real rollback** — restores the previous file on overwrite failure, deletes
  the orphan on new-install failure (`routers/strategies_admin.py:318-355`).
- **The Gemini truncation bug is genuinely fixed** and regression-pinned
  (`ai/_gemini.py:37,49-51,62-68`; `tests/test_gemini_token_budget.py`).
- **~226 backend tests** cover this path.
- Deep links already carry install → `/backtest?strategy=` → `/optimizer?strategy=` →
  preset → `/live?preset=`.

---

## PHASE 0 — Unlock what is already built  *(est. 2–4 days)*

Four capabilities exist, are tested, and have **zero** frontend callers (verified by grep:
0 references each). This is the best value-per-effort in the codebase.

### 0.1 — Safety-latch reset UI **[CRITICAL — regression risk introduced TODAY]**
The daily-loss guardrail trips `blocked_until_reset`, which halts **all** new live entries
and cannot self-clear. The only unlatch path is `POST /live-broker/safety-config/reset-latch`
(`routers/live_broker.py:1617`) — **called from nowhere in the frontend**. The sole
breadcrumb is a generic "engine halted" label.

**Why it is newly urgent:** today's C3 work gave `engine.guardrail_tick()` its **first
production caller** (`live_deploy_governor.check_account_caps`). Before C3 the latch was
effectively unreachable, so the missing UI was harmless. It is now a live lockout with no
in-app exit. This is a gap my own change opened, and it should be closed before any
real-money session.

**Build:** a status banner when `blocked_until_reset` is true, stating *why* it tripped and
when, plus an explicit confirm-to-reset control. The trip itself stays exactly as-is — it is
correct safety behaviour and is not being weakened.

### 0.2 — Overnight recovery banner
`GET /live-broker/recovery-status` (`routers/live_broker.py:1481`) exists and its docstring
states its purpose is to drive a red strip while post-reboot position recovery is
incomplete. No caller. Until then the operator cannot tell whether recovery finished — with
real positions open.

### 0.3 — Deploy straight from a backtest run
The backend already accepts `source_type="backtest_run"` (`runtime.py:1747-1794`), but the
deploy wizard only offers *Saved preset* / *Strategy Library*
(`pages/LiveSignals.jsx:638-654`), so **every** deployment is forced through a "save a
preset first" detour. Add the third source and a Deploy button on the results view.

### 0.4 — Strategy pipeline chips
`GET /strategies/{id}/pipeline` (`routers/strategies_admin.py:141`) returns
authored→backtested→optimized→preset→paper→live stage state and was explicitly built to
power Library cards. No caller. Wire it in: the Library becomes a progress board instead of
a flat list.

---

## PHASE 1 — Finish the config-block generalization  *(est. 1.5–3 weeks)*

**This is the highest-value addition for the strategy builder, and it is the app keeping a
promise it currently breaks.**

Today, when a user describes a premium trigger, session gate, position sizing, or
expiry/instrument selection, `classify_rule` answers **BUILDABLE_NOW** — *"Buildable via the
shipped premium-trigger config"* (`ai/capability.py:232-243,403-412`). But:

| Layer | Reality |
|---|---|
| `StrategySpec` | **No field** for any of it (`ai/spec_schema.py:42-56`) |
| `compile_spec` | Emits only `entry_ce`/`entry_pe`/`exits`/`gate_skip_regimes`/`cooldown_bars` |
| Full-Python prompt | Never mentions these mechanisms (`ai/py_author.py:19-56`) |
| Backtest dispatch | `if strategy_id != "premium_momentum": return None` (`premium_trigger_dispatch.py:181`) |

The file contradicts its own stated design **directly below that line**: *"the whole point
of Phase 4 dispatch is to route on CONFIG PRESENCE, not on `strategy_id ==
'premium_momentum'`."* The generalization was specified and never finished, and the
module's docstring lists the remaining pieces itself (`:36-46`).

**Consequence:** the app tells the user their idea is buildable, then offers no path to
build it. Only a developer editing that hardcoded check can deliver it.

**Work, in dependency order:**
1. **Route on config presence, not strategy id** — the design already written down. Keep
   byte-identical parity for `premium_momentum` (there is already a parity test to hold it).
2. **Add a `config_block` to `StrategySpec`** covering premium trigger, session gates,
   sizing, expiry/instrument selection — with the same whitelist discipline the column
   compiler already uses.
3. **Teach both generators** the mechanism exists (spec prompt + full-Python prompt), so a
   plain-words description routes to a shape that can express it.
4. **Config-block builder in the wizard UI** — the deferred follow-up named at
   `routers/premium_momentum_routes.py:250-253`.
5. **Wire the live/paper evaluator on the same schema**, so a config-driven strategy
   deploys, not just backtests.

**Also in Phase 1 — couple the gate to the generator.** "Check feasibility" and "Generate"
are today two independent LLM calls that never talk (`strategies_admin.py:451-477` vs
`:421-448`), and the gate is skippable: Install is blocked only if a `ruleSet` exists *and*
says REJECT (`AuthoringWizard.jsx:874`), and the Python panel ignores the verdict entirely
(`:910`). Feed the gate's verdict forward into generation and honour it in both panels.

---

## PHASE 2 — Remove the remaining friction  *(est. 1 week)*

| Item | Why | Evidence |
|---|---|---|
| Spot-data preflight (Check → Ingest → auto-recheck) | The pattern already exists and works **for options**; spot backtests still dead-end on a raw API string and dump the user on a 10-panel warehouse page | `BacktestLab.jsx:1509-1650` (good pattern) vs `DataWarehouse.jsx` |
| Async backtest error parity | The **sync** path builds a rich audit message; the **async** path the UI actually calls returns a bare string | `research.py:290-293` vs `:204-207` |
| Optimizer cancel honesty | Stop/Pause do not interrupt the `option_rerank` sub-stage — its loop checks only a 30-min wall clock, unlike the survival loop which checks both flags | `optimizer.py:992-1027` vs `:1453-1467` |
| Nav clarity | "Live Signals (P4)" = deploy wizard, "Live Trading (L0)" = broker cockpit. Internal phase codes are exposed as user-facing badges | `Layout.jsx:36,39` |
| `FERNET_KEY` boot warning | Unset ⇒ an ephemeral key is generated silently; after any restart stored broker tokens become undecryptable, surfacing as an opaque error that never names the cause | `encryption.py:8-15` |
| Static-IP guidance | A missing static IP shows as an **absent checkmark**; the real message is buried and names no env var | `LiveBanner.jsx:18,90` |

Also decide, don't leave ambiguous: `components/live/PositionMonitor.jsx` is imported by
nothing — wire it or delete it.

---

## PHASE 3 — Live-readiness  *(blocked on a registered IP; prep is not)*

**The honest status: live trading has zero validation evidence.** `live_trades` is empty —
not one real fill has ever passed through the system — and none of C2 / C4 / H1 / C3 has
been exercised against a live broker. All four are code-complete and test-covered; none is
proven. This is the largest outstanding risk in handing the app to a user for live trading.

Blocked on the unregistered IP, but the *preparation* is not: build a scripted readback
harness against `docs/live-readback-checklist.md` so the eventual validation is one
command and a checklist, not a day of manual clicking.

---

## What this plan deliberately does NOT do

- **No edge research.** Parked per your instruction; the clean 2026 holdout stays unspent.
- **No weakening of safety controls.** `LIVE_AUTOPLACE_ARMED`, the kill switch, caps, the
  guard and the broker-connection checks are deliberate and stay. Phase 0.1 adds a *reset
  path* for a latch, it does not make the latch easier to avoid.
- **No new strategy families.** Capability first, strategies later — your sequencing.

---

## Recommended order

**Phase 0 → Phase 1 → Phase 2**, with Phase 3 prep slotted whenever the IP is sorted.

Phase 0 first because it is days, not weeks; it converts already-paid-for work into visible
capability; and 0.1 closes a lockout that today's own change made reachable. Phase 1 next
because it is the difference between a strategy builder that handles per-bar indicator rules
and one that handles the strategies you actually want to describe.
