# Exit/Risk Controls on the Backtest Lab page — Design Spec

**Date:** 2026-06-16
**Status:** Approved design — adversarial audit folded in (§10, 14 findings); pending user spec review
**Branch:** `feat/backtest-exit-controls` (off `feat/adaptive-strategies` — the current working tip; see §3)
**Scope:** Expose the existing exit/risk execution overlay (premium trailing-stop, breakeven,
per-day loss/target/max-trades caps) in the **Backtest Lab** page as an **optional, off-by-default**
control, so a user can backtest any strategy with controlled exits and see the effect — without
degrading any existing backtest behaviour.

> The overlay engine, request schema, validation, and result fields already exist (Piece 2 /
> `feat/exit-risk-controls`). This piece is the missing **input UI** on the Backtest Lab page, plus
> two integration fixes the deep design review uncovered (a dead attribution read, and submit-time
> validation on the async path). No engine changes.

---

## 1. Problem / objective

The exit/risk overlay is selectable in the **Optimizer** (to *search/select* a config) and the
**deploy wizard** (to *enforce* it live), but **not** in the **Backtest Lab** — the one place you'd
*understand* it: see, trade-by-trade, how a trailing stop / breakeven / daily cap changes a specific
strategy's option-₹ result (equity curve, drawdown, exit-reason mix). Today a user can only set the
overlay by hand-crafting the API body. The objective is to add the panel, reuse the existing engine +
results, and close the loop (backtest a config → save as preset → deploy) — at zero risk to existing
backtests.

## 2. What already exists vs what this adds

**Already exists (verified in the running stack):**
- The backtest **engine path** applies the overlay. BacktestLab posts to `/backtest/start` →
  `run_backtest_job` → `_run_paired_option_backtest` (runtime.py); the **same** function is called by
  the sync `/backtest/run`. It already **validates** (`validate_exit_risk_config`, runtime.py:577-585),
  **forwards** `exit_controls`/`daily_caps` into the sim (606-607), and **segregates** `SKIPPED_DAILY_CAP`
  rows out of `trades` into `skipped_trades` (610-611).
- The request schema `OptionBacktestReq` already has `exit_controls`/`daily_caps` fields.
- The engine emits attribution into the **option** metrics (`option_backtest.metrics`:
  `option_trail_exits`, `option_breakeven_exits`, `skipped_by_cap`, `skipped_daily_*`).
- The governor (daily caps) runs in the pairing loop **regardless of `exit_mode`** (option_backtest.py:396-401);
  trail/breakeven run only inside `_walk_option_exit` under `option_levels`.

**This piece adds:**
1. The **Exit/Risk panel** in the Backtest Lab setup form (off by default).
2. Threading the overlay through **every** option-config assembly + prefill + save path on the page
   (six points — §5.1).
3. **Fix:** the dead attribution read in `PerformanceOverview` (§5.2).
4. **Fix:** submit-time validation on the async path (§5.3).
5. A **cross-path execution-shape contract** so backtest-saved and optimizer-saved presets carry the
   overlay identically (§5.4).

## 3. Decisions (locked with the user)

| Decision | Choice |
|---|---|
| Scope | **Option 1 now:** setup panel + reuse existing results (with the attribution-read fix). The moving trail-line on the BacktestChart is **deferred** (Option 2 — its own plan; needs the engine to emit a per-bar trail series). |
| Optional | **Off by default ⇒ byte-identical** payload/behaviour when the panel is untouched. |
| Units | **Fractions** for `pct` (0.25 = 25%), matching the deploy wizard + optimizer overlay panels — distinct from the page's older whole-percent option-target/stop field (clear "(fraction)" labels disambiguate; same split the deploy wizard already has). |
| Gating | Trail/breakeven inputs apply only under `exit_mode = option_levels`; daily ₹ caps require costs on; max-trades does not. Mirrors the backend validation exactly. |
| Branch | `feat/backtest-exit-controls` off `feat/adaptive-strategies` (the running tip), keeping the linear stack. **Note:** the two fixes (§5.2, §5.3) are conceptually Piece-2 corrections — cherry-pick to `feat/exit-risk-controls` if Piece 2 is ever merged standalone. |
| Engine | **No engine change.** |

## 4. Architecture — a thin UI layer over an existing engine

```
BacktestLab.jsx (setup form)
  config state (+9 off-by-default fields)
   ├─ Exit/Risk panel  ──────────────► writes the 9 fields
   ├─ buildPayload()  ───────────────► option_backtest.exit_controls/daily_caps  ─► /backtest/start
   ├─ buildExecutionFromConfig() ────► execution.exit_controls/daily_caps  ─► savePreset (from config)
   ├─ buildExecutionFromRun(run) ────► execution.exit_controls/daily_caps  ─► savePreset (from a run)
   ├─ apply-preset prefill (l.202) ──◄ ex.exit_controls/daily_caps         (preset → form)
   └─ clone-run prefill (l.564) ─────◄ r.config.option_backtest.exit_controls/daily_caps (run → form)

PerformanceOverview.jsx
   attribution block ── read FIX ──► result.option_backtest.metrics (was result.metrics)

research.py backtest_start (handler)
   + validate_exit_risk_config  ──► clean 400 at submit (was: failed run in the worker)
```

## 5. Components

### 5.1 `BacktestLab.jsx` — six edit points (the integration footprint)
The overlay must be threaded through **all** of these or it leaks/drops silently:

1. **`config` defaults** (~line 84): add 9 off-by-default fields — `exit_controls_enabled=false`,
   `exit_controls_unit="pct"`, `breakeven_trigger=""`, `breakeven_lock=""`, `trailing_activation=""`,
   `trailing_distance=""`, `daily_cap_loss=""`, `daily_cap_target=""`, `daily_cap_max_trades=""`.
2. **The Exit/Risk panel** in the option-backtest section, shown only when `option_backtest_enabled`.
   A toggle + unit (fraction/pts) + breakeven (trigger, lock) + trailing (activate, distance) + daily
   caps (loss ₹, target ₹, max trades). Trail/breakeven inputs note/disable when `exit_mode ≠
   option_levels`; ₹-cap inputs disable with a "requires costs" hint when costs off. Fraction labels.
3. **`buildPayload()`** (~line 402): inside `option_backtest`, emit `exit_controls` **only when
   `exit_controls_enabled && option_exit_mode === "option_levels"`** and `daily_caps` **only when at
   least one cap field is non-empty**. Off (or all-blank) ⇒ **neither key emitted** — never an empty
   `{}` (see §6 empty-object guard). **This is the canonical gate; points 4–5 reuse it verbatim.**
4. **`buildExecutionFromConfig()`** (~line 238 → consumed by **`saveAsPreset`**, line 267, which calls
   `api.savePreset` at line 286 — *not* "savePresetFromConfig", which does not exist): the function today
   builds `{moneyness, dte_filter, exit_mode, lots, option_target/stop_*, cost_config}` and **does not
   touch the overlay**. This piece **adds** the emission (it is new code, not a confirmation of existing
   behaviour): write `ex.exit_controls` (nested shape per §5.4) under the **same gate as point 3**
   (`exit_controls_enabled && exit_mode === "option_levels"`); write `ex.daily_caps` when any cap field
   is set, **outside** the option_levels guard (daily caps apply regardless of `exit_mode`; §2).
5. **`buildExecutionFromRun(run)`** (~line 299 → consumed by `savePresetFromResult`, line 350): the same
   **add** (the current function also does not touch the overlay), sourced from
   `run.config.option_backtest.exit_controls`/`daily_caps` (round-tripped via `OptionBacktestReq`).
   **Critical placement:** the existing function wraps the premium-level fields in an
   `if ((ob.exit_mode||'spot_exit')==='option_levels')` block (line 308). Put `exit_controls` **inside**
   that block (gated on `enabled` too) but `daily_caps` **outside** it — so a `spot_exit` run that hit a
   daily cap still carries the cap into the preset (mirrors the engine governor-vs-trail split).
6. **Prefills** — apply-preset → form (~line 202): the current `exFields` map reads
   moneyness/dte/lots/exit_mode/levels/costs but **does not read** the overlay — **add** reads of
   `ex.exit_controls`/`ex.daily_caps` into the 9 fields (mirroring the deploy-wizard prefill at
   LiveSignals.jsx:433-441). Clone-run → form (~line 564): map
   `r.config.option_backtest.exit_controls`/`daily_caps`. Values are fractions (no conversion). So a
   survivor preset or a re-run pre-fills the panel — the "inspect the optimizer's chosen exit" loop.

### 5.2 `PerformanceOverview.jsx` — fix the dead attribution read (BUG)
Line 19 reads `const m = result?.metrics` (the **spot** metrics); the attribution block (lines
~104-108) reads `m.option_trail_exits`/`m.skipped_by_cap`, which are **`undefined` there** — they live
in **`result.option_backtest.metrics`**. So `anyNonZero` is always false and the block **never
renders** (verified against a live run doc: `result.metrics.option_trail_exits === undefined`,
`result.option_backtest.metrics.option_trail_exits === 0`). `result` is confirmed to be the run doc
(`computeKeyMetrics`/`savePresetFromResult` both read `result.option_backtest`/`result.config.option_backtest`).
**Fix (minimal, no unused-var warning):** **repoint line 19** to
`const m = result?.option_backtest?.metrics || {}` — keeping the variable name means lines 104-108 need
no change and no stray `m` is left dead (§8 promises a clean build with no new warnings). Spot-only / failed
runs have no `option_backtest` ⇒ optional chaining gives `{}` ⇒ `anyNonZero` false ⇒ block stays hidden
(correct). This also un-breaks the **existing** (latent) attribution display for any overlay backtest run
via API. **Pin the keys:** the block reads `option_trail_exits`, `option_breakeven_exits`, `skipped_by_cap`,
`skipped_daily_loss`, `skipped_daily_target`, `skipped_max_trades`; all six are emitted into
`option_backtest.metrics` by `_compute_metrics` (option_backtest.py:194-199 — verified by the audit), but
only the first three have pinned constants in schemas.py:23-25. The implementer must **confirm all six keys
are emitted** (else the three per-reason `skipped_daily_*` rows stay perpetually hidden even though
`anyNonZero` still flips via `skipped_by_cap`).

### 5.3 `research.py` `backtest_start` — submit-time validation (UX FIX)
The overlay validation lives inside `_run_paired_option_backtest`, which on the **async** `/backtest/start`
path runs in the worker — so a bad config (e.g. `distance=25`) becomes a **failed run** (verified:
`/backtest/start` returns `run_id`/queued for an invalid overlay, while sync `/backtest/run` returns a
clean `400`). **Fix:** in the `backtest_start` handler, before launching the job, validate the overlay
**mirroring the in-worker call (runtime.py:579-583) exactly** — NOT the `optimize_start` dict idiom.
`req.option_backtest` is a typed `OptionBacktestReq`, so its `exit_controls`/`daily_caps` are **pydantic
models, not dicts** (`optimize_start`'s `option_config` is `Optional[Dict]`, which is why that one uses
`.get()` — the two handlers take different request types):

```python
ob = req.option_backtest
if ob.enabled and (ob.exit_controls or ob.daily_caps):
    errs = validate_exit_risk_config(
        ob.exit_controls.model_dump() if ob.exit_controls else None,
        ob.daily_caps.model_dump() if ob.daily_caps else None,
        costs_on=bool((ob.cost_config or {}).get("enabled")),
        option_exec_on=(ob.exit_mode == "option_levels"),
    )
    if errs:
        raise HTTPException(400, "; ".join(errs))
```

Three things this pins that the audit flagged:
- **`.model_dump()` is mandatory.** `validate_exit_risk_config` → `ExitControlsConfig.from_dict` calls
  `data.get('enabled')`; passing the raw `ExitControlsReq`/`DailyCapsReq` model raises `AttributeError`
  → a **500**, not the intended 400. (The in-worker call already does this conversion, runtime.py:580-581.)
- **Gate on `ob.enabled`.** The in-worker validation only runs when enabled — `_run_paired_option_backtest`
  returns early at runtime.py:413-414 (`if not config.enabled: return None`) **before** the validate at
  line 577. Without this gate, an inert/stale overlay under `enabled=False` would 400 at submit while today
  it runs fine, breaking the off-by-default API contract (§6).
- **`cost_config` is `Optional[Dict]`** (schemas.py:72) — read it dict-safe (`(ob.cost_config or {}).get("enabled")`),
  not `.enabled`.

The in-worker validation stays as the backstop. (UI gating already prevents the common cases; this handles
the range guards authoritatively at submit.)

### 5.4 Cross-path execution-shape contract
A preset's `execution.exit_controls`/`daily_caps` is read by a **single** deploy-wizard prefill, but
presets are now created from **two** sources: the optimizer (`apply_opt_as_preset` →
`execution_from_option_config`, which **already** emits the overlay) and the backtest
(`buildExecutionFromConfig`/`FromRun`, which **today emits nothing** — §5.1.4/.5 add it). **Both must
place `exit_controls`/`daily_caps` at the same key/shape in the execution object** — top-level
`execution.exit_controls = {enabled, unit, breakeven:{trigger,lock}, trailing:{activation,distance}}`
and `execution.daily_caps = {loss, target, max_trades}` — matching what `execution_from_option_config`
already emits and what the single deploy-wizard prefill reads (LiveSignals.jsx:433-441). The spec pins this
shape so the deploy prefill works for presets from **either** source. **Tolerance note:** the optimizer
path copies `daily_caps` **verbatim**, so an optimizer-sourced preset's `execution.daily_caps` also carries
a `mode` key (`'soft'` default, from `DailyCapsReq`); the deploy prefill ignores it. The BacktestLab
build* may omit `mode` (harmless) or pass it through — **do not assert strict 3-key equality** between the
two sources; the contract is "same nested keys the consumer reads", not byte-identical objects.

## 6. Off-by-default + impact (no degradation)
- **Byte-identical when off:** panel untouched ⇒ `buildPayload`/`buildExecution*` emit no new keys ⇒
  existing backtests + saved presets are unchanged. Verified-by-construction (gated emission).
- **Empty-object guard (hard contract — load-bearing).** `ExitControlsReq`/`DailyCapsReq` are Pydantic
  models with **no `__bool__`**, so a parsed instance is **always truthy** even with `enabled=false`/all-None.
  The backend gate `if config.exit_controls or config.daily_caps:` (runtime.py:577) would therefore fire on
  a stray `exit_controls: {}` / `daily_caps: {}`, and `result['request'] = config.model_dump()` (runtime.py:612)
  would then store extra keys — **not byte-identical**. So the emit rule is **"off ⇒ key absent", never
  "off ⇒ empty object"**: `exit_controls` emitted only on `enabled` true (+ option_levels), `daily_caps`
  only on ≥1 non-empty cap field. Never spread the object unconditionally. (This is the same invariant as
  §5.1.3 stated as a hard contract; it is currently asserted only by the frontend emit logic, so the gated
  emission in §5.1.3-.5 is the *only* thing guaranteeing it.)
- **No impact on:** spot-only backtests (panel is option-gated), `optionPreflight` (overlay keys inert
  to it), the BacktestChart, the KPI grid, or DualAxis/MonthlyPnl (they don't read the overlay keys).
- **Walk-forward nuance (expected, documented):** the page's WF toggle runs the *spot* `walk_forward`
  (no option pairing), so the overlay affects the **full-window option-₹ result** (PerformanceOverview)
  but **not** the spot IS/OOS WF panel. State this in the panel help so it isn't read as a gap.
- **Results already correct after §5.2:** the attribution block renders from the option metrics; the
  trade table carries the new exit reasons; the chart's exit markers key off exit reason.
- **`option_levels` with no premium target/stop ⇒ no trail/breakeven exits (expected, not a bug).** The
  engine's `use_option_levels` gate also requires a non-null option target/stop (option_backtest.py:298-303);
  `exit_mode=option_levels` + `exit_controls.enabled` + no target/stop **passes validation** yet applies no
  trail/breakeven at runtime. This pre-existing looseness is mirrored identically by both validators (no code
  change here); note it in the panel help so "why no trail exits?" reads as expected. Daily caps are
  unaffected (governor runs regardless of `exit_mode`).

## 7. Error handling
- Invalid overlay at submit → **400** from `backtest_start` (§5.3), surfaced by the existing run-error
  handling; UI gating prevents the costs/option-exec cases proactively.
- `result.option_backtest` absent (spot-only / failed run) → attribution block hidden (optional chaining).
- No new exceptions in the engine (unchanged).

## 8. Testing & verification
- `cd frontend && npm run build` clean (no new warnings beyond the 2 pre-existing).
- **Running-stack visual check:** panel appears under option execution; gating works (₹ caps disabled
  without costs; trail/breakeven noted outside option_levels); a backtest with trailing + a daily cap
  shows trail-stop exits **and the attribution block now renders** (proving §5.2); saving that backtest
  as a preset then opening the deploy wizard pre-fills the overlay (proving §5.1 + §5.4); a bad overlay
  (distance 25) returns a **400 at submit** (proving §5.3).
- **Backend:** a contract assertion that `backtest_start` references `validate_exit_risk_config`
  (research.py is in the contract corpus). The frontend has no contract corpus for BacktestLab, so
  build + visual is the established pattern.

## 9. Out of scope / future
- **Option 2 — moving trail-line on the BacktestChart:** needs `_walk_option_exit` to emit a per-bar
  running-max / effective-stop series on each trade, then the chart draws it. Its own spec/plan.
- No engine/sim changes. No changes to the optimizer or deploy-wizard panels (already done).

## 10. Audit findings → resolutions

Adversarial multi-agent audit (5 lenses: frontend-completeness, attribution-read,
backend-validation-and-storage, preset-roundtrip-shape, off-by-default-and-impact). **14 findings
(0 blocker, 3 high, 4 medium, 7 low)** + 30 confirmations. The audit **confirmed** the six-edit-point
inventory is complete (no leaked/dropped site), every line anchor (modulo one function name), the §5.2
bug, the §5.3 missing call site, the §5.4 cross-path shape, walk-forward inertness, `optionPreflight`
inertness, and the governor-runs-regardless-of-`exit_mode` claim. All 14 findings are folded in:

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | **High** | §5.3 passes `exit_controls`/`daily_caps` raw; they're pydantic models, not dicts → `from_dict`'s `.get()` raises `AttributeError` → **500 not 400**. | **§5.3 rewritten** with explicit `.model_dump()` conversion mirroring runtime.py:580-581 (code block added). |
| 2 | **High** | §5.4 / §5.1.4-.6 read as "confirm existing emission", but `buildExecutionFromConfig`/`FromRun`/apply-preset prefill **emit/read nothing** today — the gap only closes if §5.1 lands. | **§5.1.4-.6 + §5.4 reworded** to "**add** new emission/read (new code), not confirm existing". |
| 3 | **High** | §5.1.4/.5 gave no `option_levels` gate for the preset emit → a `spot_exit`+panel-on user saves a preset with `exit_controls.enabled=true` the backtest silently dropped → deploys an unvalidated overlay. | **§5.1.3 named the canonical gate**; §5.1.4 reuses it for `exit_controls`; `daily_caps` gated only on "≥1 cap field set" (outside option_levels). |
| 4 | Med | §5.3 validation fired regardless of `option_backtest.enabled`, but the in-worker backstop returns early at runtime.py:413-414 when disabled → a stale overlay under `enabled=False` would 400 at submit (today it runs fine). | **§5.3 guard gated on `ob.enabled`** so handler + backstop agree; off-by-default API contract preserved. |
| 5 | Med | §5.1.4 named `savePresetFromConfig` — no such function; the real one is `saveAsPreset` (line 267). | **Renamed to `saveAsPreset`** in §5.1.4 (line-286 `savePreset` anchor was already correct). |
| 6 | Med | §5.4 pins `daily_caps = {loss,target,max_trades}` but the optimizer path copies it verbatim incl. `mode:'soft'` → the two sources differ by the `mode` key (not byte-identical). | **§5.4 tolerance note added**: `daily_caps` may carry `mode`; deploy ignores it; **do not assert strict 3-key equality**. |
| 7 | Med | "Byte-identical when off" depends on the frontend never emitting `{}` — pydantic models are truthy even when `enabled=false`, so a stray `{}` flips runtime.py:577 and pollutes the stored `config.model_dump()`. | **§6 empty-object hard-contract guard added**: "off ⇒ key absent, never empty object"; emit conditioned on real content. |
| 8 | Low | §5.4's 3-key `daily_caps` shape omits `mode` (dup-angle of #6). | Same resolution as #6 (tolerance note). |
| 9 | Low | §5.3 pseudocode `cost_config?.enabled` — `cost_config` is `Optional[Dict]`, so `.enabled` raises; needs dict-safe read. | **§5.3 uses `(ob.cost_config or {}).get("enabled")`** (in the code block). |
| 10 | Low | §5.2 introduced a new `om` var but left the line-19 `m` dead → unused-var lint warning (§8 promises a clean build). | **§5.2 repoints line 19** `const m = result?.option_backtest?.metrics || {}` — keeps the name, lines 104-108 unchanged, no dead var. |
| 11 | Low | `option_levels` + `enabled` + no premium target/stop passes validation yet applies no trail/breakeven at runtime (`use_option_levels` also needs target/stop). Pre-existing, both validators identical. | **§6 note added** ("no overlay exits ⇒ expected, not a bug"); surface in panel help. No code change. |
| 12 | Low | In `buildExecutionFromRun` the existing `option_levels` block (line 308) wraps level fields; if the impl nests `daily_caps` inside it, a `spot_exit` run that hit a cap saves a preset that drops the cap. | **§5.1.5 critical-placement note**: `exit_controls` inside the option_levels block, `daily_caps` outside it (mirrors governor-vs-trail split). |
| 13 | Low | §5.2 reads four skip keys but only `skipped_by_cap` is a pinned constant; can't confirm `skipped_daily_*` are emitted → those rows could stay hidden. | **§5.2 "pin the keys"**: audit confirms all six are emitted (option_backtest.py:194-199); impl must verify all six wired. |
| 14 | Low | §5.3 type difference (dup-angle of #1/#9): a literal copy of `optimize_start`'s dict `.get()` idiom would `AttributeError` on the pydantic sub-models. | Same resolution as #1+#9 (§5.3 specifies attribute access + `.model_dump()`). |

**Net:** no engine change; the three High findings hardened §5.3 (`.model_dump()`), reframed §5.1/§5.4
("add", not "confirm"), and closed the silent-drop gate (§5.1.4/.5 option_levels gating). The spec is
ready for user review → implementation plan.
