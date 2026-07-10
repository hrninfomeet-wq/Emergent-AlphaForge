# Design Spec: Premium-Momentum Contingency Breakout Strategy — Phased Integration

**Date:** 2026-07-10
**Branch:** `feat/premium-momentum-strategy`
**Status:** Approved design (user), pending spec review → implementation plan
**Reference strategy:** AlgoTest.in "Configurable Contingency Breakout / Lazy Legs"
(user blueprint: `Nifty_Option_Buy_Strategy_Algotest.in.md`)

---

## 1. Goal

Bring an AlgoTest-style **premium-momentum option-buying strategy** into AlphaForge,
faithfully at 1-minute resolution, in phases that each validate before the next is
built. The strategy, per session:

1. At a **reference time** (e.g. 09:31), select an option strike by **moneyness**
   (ATM / ITM1 / ITM2 / OTM1…) computed from the **spot** index, for the chosen
   **weekly expiry**, and **lock** it for the session.
2. Record that option's **reference premium**.
3. **Monitor that specific strike's premium** and **enter** when it rises by a
   configured **% (or points)** above the reference (leg-level momentum).
4. Manage the position with **target / stop-loss** (% or points on premium) and a
   **stepped trailing SL** (X-Y ratchet).
5. (Endgame) On a primary leg's SL/target, arm an opposite-side **lazy leg** with a
   fresh reference and its own exits.

The engine is **spot-first** (the strategy's `evaluate()` sees only the index; options
are priced in post-hoc). The strategy is **premium-native**. Reconciling these is the
central design problem, and its solution is the **premium-in-context bridge** (§4).

---

## 2. Honest scope boundary

**Faithfully reproducible (1-min):**
- Leg-level premium-momentum entry (% or points above a locked reference premium).
- Moneyness strike selection (ATM/ITM1/ITM2/OTM1…) from spot at the reference bar,
  weekly expiry, side-relative (CE ITM = below spot, PE ITM = above).
- Target / SL on premium (% or points); lots sizing; global daily caps (loss/target/
  max-trades); entry / exit / cutoff windows.
- **Stepped** trailing SL (X-Y ratchet).
- Lazy-leg contingency + re-entry family (Phase 5).

**Approximated (irreducible at 1-min):**
- The momentum **trigger fires on a bar-close cross**, not a tick — an approximation
  of AlgoTest's tick engine.
- **Backtest↔live entry timing differs intra-minute**: backtest reads `options_1m`
  OHLC close; live reads ticks. This is a **parity gap to measure, not eliminate.**
  (Accepted by the user — structural to any backtest-vs-live comparison.)

**Explicitly out of reach (do not claim):**
- Millisecond precision / sub-minute snapshots and fills. Warehouse is 1-minute bars
  (spot + `options_1m`) with no tick history. Live is tick-driven, backtest is not.

---

## 3. Data model & coverage constraints

- **Backtest premium source:** `options_1m` warehouse — per-strike 1-min OHLC premium
  (close = premium), keyed `{instrument_key, ts}`; a single strike's full-session
  series is directly queryable (`warehouse.py` read pattern ~:1038-1071).
- **Ingested moneyness band:** planner supports `atm, itm1, itm2, otm1, otm2, otm3`
  ([option_data_planner.py:12](../../../backend/app/option_data_planner.py)); the daily
  catch-up default is narrower `atm, otm1, itm1`
  ([data_hygiene.py:55](../../../backend/app/data_hygiene.py)). **Actual per-day
  coverage therefore varies** and MUST be verified, not assumed.
- **Coverage gate (hard requirement):** every backtest resolves the selected strike
  per day and checks `option_coverage` (expects ~375 candles/contract/day). Days
  missing the exact reference-bar strike surface as `MISSING_CONTRACT` /
  `MISSING_ENTRY_CANDLE` (`option_backtest.py` ~:611-645) and are **excluded from the
  sample with a reported effective-N**, never silently mis-filled.
- **Live premium source:** ticks / `tick_map` via `resolve_premium`
  ([live/option_premium.py](../../../backend/app/live/option_premium.py), priority
  `live_tick > last_candle > none`) — **there are no intraday `options_1m` candles
  live** (the live roller rolls only index ticks into `candles_1m`).
- **Strike selection (shared, deterministic):** `options_universe.select_contract_for_signal`
  + `strike_offset_for_moneyness`
  ([options_universe.py](../../../backend/app/strategies/../options_universe.py) ~:30/77)
  — the SAME selector in backtest, optimizer, and live, so the strike cannot drift
  between paths.

---

## 4. Architecture: the premium-in-context bridge

**Chosen approach (b):** the engine **locks** the strike from the reference bar's spot,
records `ref_premium`, and injects into `evaluate()`'s context each bar:

```
ctx["option_ref"] = {
    "strike": <locked strike>,
    "side": "CE" | "PE",
    "ref_premium": <premium at/through the reference bar>,
    "premium_now": <premium at/through the CURRENT bar>,   # look-ahead safe
}
```

The **strategy** owns the rule (`premium_now >= ref_premium * (1 + pct)` or the points
variant). The **engine** owns only "here is the locked strike's reference and current
premium." Both backtest and live converge on the same `build_eval_ctx` /
`build_live_eval_ctx` seam ([strategies/base.py](../../../backend/app/strategies/base.py)
~:38-67), so `premium_now` is filled from `options_1m` in backtest and from ticks live,
isolating the one real difference behind a single accessor.

**Why (b) over alternatives:**
- **(a) option-native eval loop** — most faithful conceptually but **breaks live** (no
  intraday option candles) and is the most invasive. Keep only as an internal backtest
  cross-check.
- **(c) config-driven `premium_breakout` policy** — the best END state (declarative,
  no per-strategy code), but the largest up-front surface (schema + validation + UI).
  **Reach it by graduation** (Phase 4), after the edge is proven.

**Look-ahead discipline (non-negotiable):** `ctx["option_ref"]` exposes premium only
**at/through the current bar** — mirror the running-max forward-walk discipline already
used in `_walk_option_exit` (`option_backtest.py` ~:257-264). `session_precompute`
([base.py](../../../backend/app/strategies/base.py) ~:104-111) is the natural place to
PIN the reference strike, but it **cannot do DB/tick I/O**, so the premium series is
injected by the **caller** (`run_backtest` wrapper / `deployment_evaluator`), not the
strategy.

---

## 5. Phase 0 — Fix the Full-Python feasibility panel (S, do first)

**Bug:** in the authoring wizard's "Full Python (powerful)" tab, "Check feasibility"
returns blank. Root cause: the `ruleSet` result panel
([AuthoringWizard.jsx:615](../../../frontend/src/components/strategy/AuthoringWizard.jsx))
is nested inside the `mode === "spec"` block (opens :612, closes ~:883), while the shared
handler `runConverse` sets `ruleSet` in both modes. In python mode the success panel is
unmounted → blank (errors show because the error panel is outside the mode gate). Backend
(`/strategies/author/converse`) and API are mode-agnostic and correct.

**Fix:** hoist the `ruleSet` feasibility panel (and the install-gate caveat note) OUT of
the `mode === "spec"` block into the always-rendered "Describe with AI" section, adjacent
to the `converseError` panel — so the verdict renders in both modes.

**Acceptance:** Full-Python "Check feasibility" renders the same verdict panel as Spec;
JSX babel-parses; a string-pin test asserts the panel is outside the mode gate.

---

## 6. Phase 1 — Backtest premium-momentum entry (M) — **edge-validation gate**

**Deliverable:** a Full-Python `StrategyBase` plugin that, per session, locks the chosen
moneyness strike (weekly) from the reference bar's spot, records `ref_premium`, and emits
an entry Signal when `premium_now >= ref_premium * (1 + pct)` (or `+ pts`). Exits: hard SL
+ target as %/points on premium (**continuous stop only — no trailing yet**). Lots sizing
(`lots × lot_size`) and a global daily loss/target/max-trades cap. Produces per-day P&L +
a **coverage report** so we can judge whether the edge exists on 2025-26 NIFTY before
spending anything on live parity.

**Engine changes:**
1. **Non-trade-driven loader** in `run_backtest` / `runtime.py` (~:824-876): resolve the
   reference strike via `select_contract_for_signal` from the reference bar and load its
   **full-day `options_1m` series independent of spot-trade timing** (today's loader only
   pulls trade-selected strikes, so a strike whose spot signal never fires is missing).
2. **`ctx["option_ref"]` injection** through `build_eval_ctx` ([base.py](../../../backend/app/strategies/base.py) ~:38-52),
   pinned in the session step, premium injected by the caller.
3. **Look-ahead safety** — expose only premium at/through bar `i`.
4. **Per-day coverage gate** — verify the selected strike's coverage; exclude & report
   missing days (`MISSING_CONTRACT`), never mis-fill.

**CE/PE handling in Phase 1 (single-position constraint):** the engine holds ONE open
position, so Phase 1 locks BOTH the CE and PE references at the reference bar, monitors
both premium series, and **enters the first side to cross its momentum threshold** (then
that side owns the single position until it exits; the other is abandoned for the day).
This is the "whichever breaks first" behavior — an honest consequence of the single-leg
engine, not a modelling shortcut. `side` can also be pinned to `CE` or `PE` only. **True
simultaneous two-leg tracking is Phase 5** (multi-leg engine).

**Parameters (strategy):** `reference_time` (default 09:31), `moneyness` (atm/itm1/itm2/
otm1…, validated vs coverage), `side` (`first_to_trigger` (default) / `CE` / `PE`),
`momentum_pct` or `momentum_pts`, `target` (%/pts/none), `stop` (%/pts), `lots`,
`entry_window` / `exit_window` / `cutoff`, `daily_caps`.

**Reproduces:** leg-level %/points momentum entry, moneyness/weekly strike selection,
lots, daily caps, premium SL/TGT.
**Does NOT yet:** stepped trailing (Phase 2), lazy leg (Phase 5), tick-accurate fills
(1-min bar-cross approximation), any live path (Phase 3).

**STOP/GO GATE:** if there is no edge on the backtest here, **stop before Phase 3+.**

**Acceptance:** deterministic backtest over a coverage-gated window; per-day P&L +
effective-N + excluded-days report; unit tests for the momentum trigger (fires at the
right bar), look-ahead safety (no forward premium leaks), and the coverage gate
(missing strike → excluded, not mis-filled).

---

## 7. Phase 2 — Stepped trailing SL (S)

**Deliverable:** the AlgoTest **discrete ratchet**: `SL = SL_base + n*Y` once
`favorable_move >= n*X` (points and % modes). Example TSL 20-20 on a 200 entry: at +20 →
SL 190, at +40 → SL 200. Optional Lock-and-Trail variant. Re-run the Phase-1 backtest
WITH faithful trailing and measure the delta.

**Engine change:** extend the option exit walk (`_walk_option_exit`,
[option_backtest.py](../../../backend/app/option_backtest.py) ~:130-291) to carry a
stepped-SL state — track favorable increments from entry premium and ratchet the SL in
discrete X steps of Y. **NOT** high-water-minus-offset (that continuous form already
exists and is the wrong semantics). No new data or eval-contract changes.

**Convention:** at 1-min resolution, intra-bar ordering of an X-step vs an SL hit in the
same bar is ambiguous — adopt the **conservative** convention (assume SL-before-favorable
within a bar) and document the fidelity note.

**Acceptance:** unit tests pinning the ratchet at the documented worked examples;
byte-identical to Phase 1 when trailing is disabled.

---

## 8. Phases 3–5 — Roadmap (design-frozen, not in the first implementation unit)

**Phase 3 — Live/paper parity (L, highest-risk).** Same rule in the deployment loop:
(i) **lock** the strike at the reference bar and STOP the per-bar re-resolution the live
evaluator does today (`deployment_evaluator.py` ~:397-409); (ii) source `premium_now`
from ticks (`resolve_premium`), not `options_1m`; (iii) **pin the locked strike in the
subscription** — widen `radius_for_deployments` (`live_option_universe.py` ~:27-35,
currently 2 for itm1) or add an explicit locked-key subscription so a trending day can't
drop the strike out of the feed; reset on session rollover; honor expiry-day cutoff.
Requires a market-hours session to validate (per project state, real-money readback is on
hold until a live signal validates the path).

**Phase 4 — Graduate to config-driven `premium_breakout` option_policy (L).** Lift the
proven rule out of Full-Python into a declarative config block (reference-bar, moneyness,
up/down, pct-or-points, SL/TGT units, X-Y TSL, lots, caps) + validation + UI. One engine
rule for both backtest and live; no per-strategy code. Only after Phases 1-3 earn it.

**Phase 5 — Lazy-leg contingency + re-entries (XL, endgame).** A multi-leg state machine
the single-leg engine does not have: a fresh opposite-side leg armed only after the entry
leg's SL/target, with its own strike/reference/exits (premium excluded from combined
momentum), plus RE-EXECUTE / RE-ASAP(±) / RE-COST(±) up to 20 each. Touches both the
backtest exit simulator and the live evaluator/broker. **Gate hard** on Phases 1-3 proving
the base edge is real AND the contingency measurably improving it.

---

## 9. Testing strategy

- **Phase 0:** JSX babel-parse + a host string-pin (feasibility panel outside the mode
  gate); manual Chrome verification (both tabs render the verdict).
- **Phase 1:** host/container unit tests — momentum trigger timing, look-ahead safety,
  coverage gate (missing strike excluded not mis-filled), sizing, daily caps; a
  deterministic golden backtest over a fixed coverage-gated window.
- **Phase 2:** ratchet unit tests at the documented worked examples; disabled-trailing
  byte-identity vs Phase 1.
- **Cross-check:** run the option-native path (alternative a) as an internal oracle on a
  sample to confirm the ctx-bridge produces the same fills.
- **Parity (Phase 3):** measure and report the backtest↔live entry/fill divergence — a
  tracked metric, not a pass/fail.

---

## 10. Open risks & fidelity gaps (carry into the plan)

1. **Coverage / sample integrity** — per-day ITM/OTM coverage is spot-sample-driven at
   15-min granularity; gate every backtest on `option_coverage` and report effective-N,
   or the edge is silently over/under-stated.
2. **Live strike-drift** — the ATM-centered band can drop a locked strike on a trending
   day; the subscription MUST pin the locked key (Phase 3).
3. **Per-bar re-resolution vs lock** — the live evaluator re-resolves the contract every
   bar today; until it reads a persisted locked strike, live diverges from AlgoTest
   "lock at entry-time" and from the backtest (Phase 3).
4. **Backtest/live fill divergence** — 1-min OHLC close vs live ticks; a parity gap to
   measure (accepted).
5. **Order-primitive fidelity** — AlgoTest's exact momentum order type (stop vs
   stop-limit) is undocumented; we approximate trigger-then-limit as the first bar
   crossing the trigger.
6. **Stepped-TSL mismodel risk** — must be the discrete X-Y ratchet, NOT continuous
   high-water-minus-offset (common misread).
7. **PE-side moneyness** — PE ITM1 = atm+step is the standard reading (our code already
   encodes it consistently); flagged as not a verbatim AlgoTest quote.
8. **Session/expiry edge cases** — reference/premium/locked-strike state must reset on
   session rollover and honor the expiry-day cutoff; weekly-expiry must track the correct
   next expiry live.
9. **Multi-leg architecture debt (Phase 5)** — highest complexity-to-edge; gated on the
   base edge proving out.
10. **Validation cadence** — Phase 3 forward-test is calendar-constrained (PC rarely runs
    in market hours); pace so the backtest edge (Phases 1-2) is proven first.

---

## 11. First implementation unit

**Phase 0 + Phase 1 + Phase 2** — fix the feasibility panel, then the backtest-provable
premium-momentum entry with stepped trailing, stopping deliberately at the
**edge-validation gate** before any live or lazy-leg investment. Phases 3-5 are
design-frozen here and specced in detail once Phase 1's backtest shows an edge.
