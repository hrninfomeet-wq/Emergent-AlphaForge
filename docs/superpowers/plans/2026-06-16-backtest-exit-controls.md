# Backtest-page Exit/Risk Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing exit/risk execution overlay (premium trailing-stop, breakeven, per-day loss/target/max-trades caps) as an **optional, off-by-default** panel on the Backtest Lab page, plus two integration fixes (a dead attribution read; submit-time validation on the async path) — at zero risk to existing backtests.

**Architecture:** A thin UI layer over an unchanged engine. The backtest engine path already validates, forwards, and segregates the overlay (Piece 2). This plan adds: the input panel + 9 config fields, gated emission into `buildPayload`/`buildExecutionFromConfig`/`buildExecutionFromRun`, prefill reads in `applyPreset`/`loadPastRun`, the `PerformanceOverview` attribution-read fix, and submit-time validation in `backtest_start`. **No engine/sim change.**

**Tech Stack:** React (frontend/src), FastAPI + Pydantic (backend/app), pytest contract corpus (text assertions; never imports server/runtime on host).

**Spec:** `docs/superpowers/specs/2026-06-16-backtest-exit-controls-design.md` (audit-hardened, §10 = 14 findings → resolutions).

**Branch:** `feat/backtest-exit-controls` (current).

**Testing convention (per repo + spec §8):** BacktestLab.jsx has **no frontend unit-test corpus** — the established pattern is `npm run build` clean + running-stack visual verification. So the frontend tasks (3–7) verify by build + browser, not unit tests. The **backend** task (1) gets a contract-corpus text assertion (TDD). The final task (8) is the running-stack verification that is the real proof (per the verify skill: runtime observation, not test runs).

---

## File map

- `backend/app/routers/research.py` — **Modify** `backtest_start` (~line 308): add submit-time overlay validation.
- `tests/test_contract_exit_risk_routes.py` — **Modify**: add an assertion that `backtest_start` validates the overlay.
- `frontend/src/components/backtest/PerformanceOverview.jsx` — **Modify** line 19: repoint `m` to the option metrics.
- `frontend/src/pages/BacktestLab.jsx` — **Modify** six points: config defaults (~84), Exit/Risk panel (~1102), `buildPayload` (~402), `buildExecutionFromConfig` (~238), `buildExecutionFromRun` (~299), prefills `applyPreset` (~201) + `loadPastRun` (~564).

---

## Task 1: Backend submit-time validation (`backtest_start`)

**Files:**
- Modify: `backend/app/routers/research.py:308-331`
- Test: `tests/test_contract_exit_risk_routes.py`

The async `/backtest/start` path inserts the run and launches the worker with **no overlay validation** — a bad overlay (e.g. `distance=25`) becomes a *failed run* instead of a clean 400. The sync `/backtest/run` validates because it calls `_run_paired_option_backtest` in-request. Fix: validate in the handler, **mirroring the in-worker backstop (runtime.py:579-583) exactly** — `req.option_backtest.exit_controls`/`daily_caps` are pydantic models (not dicts like `optimize_start`'s `option_config`), so they need `.model_dump()`; gate on `enabled` (the worker returns early at runtime.py:413-414 when disabled, so an inert overlay must stay byte-identical); read `cost_config` dict-safe.

- [ ] **Step 1: Write the failing contract test**

Add to `tests/test_contract_exit_risk_routes.py`:

```python
def test_backtest_start_validates_overlay_at_submit():
    # the async /backtest/start handler validates the overlay BEFORE launching the
    # worker, mirroring the in-worker backstop — converting the pydantic sub-models
    # to dicts (.model_dump()) so a bad overlay 400s at submit, not as a failed run.
    assert "ob.exit_controls.model_dump()" in API
    assert "ob.daily_caps.model_dump()" in API
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_contract_exit_risk_routes.py::test_backtest_start_validates_overlay_at_submit -v`
Expected: FAIL (`ob.exit_controls.model_dump()` not in corpus yet).

- [ ] **Step 3: Implement the handler validation**

In `backend/app/routers/research.py`, in `backtest_start`, insert **after** the strategy 404 check (after `raise HTTPException(404, f"Strategy {req.strategy_id} not found")`, before `run_id = str(uuid.uuid4())`):

```python
    # Submit-time overlay validation. The async path runs the engine (and its
    # in-worker validate) in a background task, so without this a bad overlay
    # becomes a failed run instead of a clean 400. Mirror the in-worker backstop
    # (runtime.py:579-583): option_backtest.exit_controls/daily_caps are pydantic
    # models -> .model_dump() before the dict-based validator; gate on enabled so
    # an inert overlay under enabled=False stays byte-identical (the worker returns
    # early before validating when disabled).
    ob = req.option_backtest
    if ob.enabled and (ob.exit_controls or ob.daily_caps):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            ob.exit_controls.model_dump() if ob.exit_controls else None,
            ob.daily_caps.model_dump() if ob.daily_caps else None,
            costs_on=bool((ob.cost_config or {}).get("enabled")),
            option_exec_on=(ob.exit_mode == "option_levels"),
        )
        if errs:
            raise HTTPException(400, "; ".join(errs))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_contract_exit_risk_routes.py -v`
Expected: PASS (all 5 tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/research.py tests/test_contract_exit_risk_routes.py
git commit -m "feat(backtest): validate exit/risk overlay at submit on /backtest/start"
```

---

## Task 2: Attribution-read fix (`PerformanceOverview`)

**Files:**
- Modify: `frontend/src/components/backtest/PerformanceOverview.jsx:19`

Line 19 reads `const m = result?.metrics || {}` — the **spot** metrics. The attribution block (lines 104-109) reads `m.option_trail_exits`/`m.skipped_by_cap`/`m.skipped_daily_*`, which live in **`result.option_backtest.metrics`**, so `anyNonZero` is always false and the block never renders. `m` is used **only** by that block (audit-confirmed), so repointing line 19 is the minimal fix — no other edits, no dead variable, no new lint warning.

- [ ] **Step 1: Repoint line 19 to the option metrics**

Change:
```jsx
  const m = result?.metrics || {};
```
to:
```jsx
  // Exit-control attribution lives in the OPTION metrics (option_backtest.metrics),
  // not the spot metrics. Spot-only / failed runs have no option_backtest -> {} ->
  // anyNonZero false -> block stays hidden (correct).
  const m = result?.option_backtest?.metrics || {};
```

- [ ] **Step 2: Verify `m` has no other consumer**

Run: `git grep -n "\bm\." frontend/src/components/backtest/PerformanceOverview.jsx`
Expected: only the attribution block (lines ~104-109) references `m.*`. (If anything else uses `m`, STOP — the hero/Stat blocks use `k.*` and `series.*`, not `m`, so this should be clean.)

- [ ] **Step 3: Commit** (committed together with the frontend panel work in Task 7's commit, or standalone here)

```bash
git add frontend/src/components/backtest/PerformanceOverview.jsx
git commit -m "fix(backtest-ui): read exit-control attribution from option metrics"
```

---

## Task 3: BacktestLab config defaults — 9 off-by-default fields

**Files:**
- Modify: `frontend/src/pages/BacktestLab.jsx:84-112` (the `config` initial state)

Add the same 9 fields the deploy wizard uses (LiveSignals.jsx:349-356), so prefill/emit/panel all share one naming.

- [ ] **Step 1: Add the fields**

After `option_assumed_stop_pct: 50,` (line 112, the last option field before `});`), insert:

```jsx
    // Exit / risk overlay (Piece 2 on the Backtest page). Off by default =>
    // buildPayload/buildExecution* emit no new keys => byte-identical. Fractions
    // for pct (0.25 = 25%), matching the deploy wizard + optimizer overlay panels.
    exit_controls_enabled: false,
    exit_controls_unit: "pct",
    breakeven_trigger: "",
    breakeven_lock: "",
    trailing_activation: "",
    trailing_distance: "",
    daily_cap_loss: "",
    daily_cap_target: "",
    daily_cap_max_trades: "",
```

- [ ] **Step 2: Build clean**

Run: `cd frontend && npm run build`
Expected: build succeeds, no new warnings beyond the 2 pre-existing.

(No commit yet — Tasks 3–7 are one logical frontend change; commit at Task 7.)

---

## Task 4: The Exit/Risk panel UI

**Files:**
- Modify: `frontend/src/pages/BacktestLab.jsx` — insert a block inside the "Option Execution" panel body, **after** the option-exit-mode block closes (after line 1102 `</div>`, before line 1103 `</div></Panel>`).

Mirror the deploy-wizard panel (LiveSignals.jsx:769-870): a toggle, unit (fraction/pts) selector, breakeven (trigger, lock), trailing (activate, distance), daily caps (loss ₹, target ₹, max trades). Gating mirrors the backend validator exactly: trail/breakeven inputs note they need `option_levels`; ₹-cap inputs note they need costs on; max-trades needs neither.

- [ ] **Step 1: Insert the panel block**

After the `</div>` that closes the option-exit-mode block (line 1102) and before the `</div>` that closes the panel body (line 1103), insert:

```jsx
            {/* Exit / risk overlay (optional). Premium trailing-stop + breakeven +
                per-day caps — the SAME engine the optimizer searches and the deploy
                wizard enforces. Off by default => byte-identical payload. */}
            {config.option_backtest_enabled && (
              <div className="pt-2 border-t border-line space-y-2" data-testid="exit-controls-panel">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={config.exit_controls_enabled}
                    onCheckedChange={(v) => setConfig({ ...config, exit_controls_enabled: v })}
                    data-testid="exit-controls-switch"
                  />
                  <span className="text-xs text-dim">Exit / risk controls (trailing · breakeven · daily caps)</span>
                </div>
                {config.exit_controls_enabled && (
                  <>
                    {config.option_exit_mode !== "option_levels" && (
                      <div className="text-[10px] text-amber-300 leading-snug" data-testid="exit-controls-mode-note">
                        Trailing &amp; breakeven need exit mode “Option premium SL/target” (they trail the option’s
                        own premium). They’re skipped under “Mirror spot exit”. Daily caps still apply.
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-dim">Unit</span>
                      <div className="flex rounded-md border border-line overflow-hidden">
                        {["pct", "pts"].map((u) => (
                          <button
                            key={u}
                            type="button"
                            onClick={() => setConfig({ ...config, exit_controls_unit: u })}
                            className={`px-2 py-1 text-[11px] font-mono ${config.exit_controls_unit === u ? "bg-info text-bg-0" : "bg-bg-2 text-dim hover:text-foreground"}`}
                            data-testid={`exit-controls-unit-${u}`}
                          >
                            {u === "pct" ? "Fraction" : "Points"}
                          </button>
                        ))}
                      </div>
                      <span className="text-[10px] text-dimmer">
                        {config.exit_controls_unit === "pct" ? "0.25 = 25% of entry premium" : "absolute premium points"}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Row label={`Breakeven trigger ${config.exit_controls_unit === "pts" ? "(pts profit)" : "(fraction)"}`}>
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.breakeven_trigger}
                          onChange={(e) => setConfig({ ...config, breakeven_trigger: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "off" : "0.30 = +30% arms BE"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-be-trigger"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Breakeven lock ${config.exit_controls_unit === "pts" ? "(pts above entry)" : "(fraction)"}`}>
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.breakeven_lock}
                          onChange={(e) => setConfig({ ...config, breakeven_lock: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "0 = exact entry" : "0.0 = lock at entry"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-be-lock"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Trail activate ${config.exit_controls_unit === "pts" ? "(pts profit)" : "(fraction)"}`}>
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.trailing_activation}
                          onChange={(e) => setConfig({ ...config, trailing_activation: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "off" : "0.40 = +40%"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-trail-activation"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Trail distance ${config.exit_controls_unit === "pts" ? "(pts from peak)" : "(fraction)"}`}>
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.trailing_distance}
                          onChange={(e) => setConfig({ ...config, trailing_distance: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "—" : "0.25 = give back 25%"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-trail-distance"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                    </div>
                    <div className="pt-1 grid grid-cols-3 gap-2">
                      <Row label="Daily loss ₹">
                        <Input
                          type="number" min="0" step="500"
                          value={config.daily_cap_loss}
                          onChange={(e) => setConfig({ ...config, daily_cap_loss: e.target.value })}
                          placeholder={config.option_costs_enabled ? "e.g. 5000" : "needs costs"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-loss"
                          disabled={!config.option_costs_enabled}
                        />
                      </Row>
                      <Row label="Daily target ₹">
                        <Input
                          type="number" min="0" step="500"
                          value={config.daily_cap_target}
                          onChange={(e) => setConfig({ ...config, daily_cap_target: e.target.value })}
                          placeholder={config.option_costs_enabled ? "e.g. 8000" : "needs costs"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-target"
                          disabled={!config.option_costs_enabled}
                        />
                      </Row>
                      <Row label="Max trades / day">
                        <Input
                          type="number" min="0" step="1"
                          value={config.daily_cap_max_trades}
                          onChange={(e) => setConfig({ ...config, daily_cap_max_trades: e.target.value })}
                          placeholder="e.g. 5"
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-max-trades"
                        />
                      </Row>
                    </div>
                    <div className="text-[10px] text-dimmer leading-snug">
                      Daily ₹ caps are soft per-session governors (auto-resume next session). Loss/target need
                      costs on (they act on net ₹); max-trades doesn’t. Walk-forward toggle runs the spot WF only —
                      the overlay shows in the full-window option result, not the IS/OOS WF panel.
                    </div>
                  </>
                )}
              </div>
            )}
```

- [ ] **Step 2: Build clean**

Run: `cd frontend && npm run build`
Expected: build succeeds, no new warnings. (`Switch`, `Input`, `Row` are already imported and used elsewhere in this file.)

(No commit — continues into Task 5.)

---

## Task 5: `buildPayload` emission

**Files:**
- Modify: `frontend/src/pages/BacktestLab.jsx:402-436` (the `option_backtest` object)

Emit `exit_controls` **only when `exit_controls_enabled && option_exit_mode === "option_levels"`** (canonical gate), `daily_caps` **only when ≥1 cap field is non-empty** (regardless of exit_mode). Off ⇒ neither key emitted (never `{}` — pydantic models are truthy even when disabled, so an empty `{}` would flip the backend gate and pollute the stored config; §6 empty-object guard). **Pydantic-safe nesting:** `option_backtest.exit_controls` is validated as `ExitControlsReq` whose `breakeven`/`trailing` are non-Optional sub-models with `float = 0.0` fields — so emit breakeven/trailing as **dicts with numeric values only** (no `null`); omit a sub-object when both its fields are blank (defaults apply). `daily_caps` fields are `Optional`, so `null` is fine there.

- [ ] **Step 1: Add the gated keys**

Inside the `option_backtest: { ... }` object, after the `sizing_config: ... ,` entry (line 435, before the closing `},` of `option_backtest`), insert:

```jsx
      // Exit/risk overlay — gated emission (off => key absent, never {}). exit_controls
      // only under option_levels (premium trailing is impossible spot-only); daily_caps
      // whenever a cap is set (the governor runs regardless of exit_mode). Breakeven/
      // trailing emitted as numeric-only dicts (ExitControlsReq sub-models reject null).
      ...(config.exit_controls_enabled && config.option_exit_mode === "option_levels"
        ? {
            exit_controls: (() => {
              const ec = { enabled: true, unit: config.exit_controls_unit };
              const be = {};
              if (config.breakeven_trigger !== "") be.trigger = Number(config.breakeven_trigger);
              if (config.breakeven_lock !== "") be.lock = Number(config.breakeven_lock);
              if (Object.keys(be).length) ec.breakeven = be;
              const tr = {};
              if (config.trailing_activation !== "") tr.activation = Number(config.trailing_activation);
              if (config.trailing_distance !== "") tr.distance = Number(config.trailing_distance);
              if (Object.keys(tr).length) ec.trailing = tr;
              return ec;
            })(),
          }
        : {}),
      ...((config.daily_cap_loss !== "" || config.daily_cap_target !== "" || config.daily_cap_max_trades !== "")
        ? {
            daily_caps: {
              loss: config.daily_cap_loss !== "" ? Number(config.daily_cap_loss) : null,
              target: config.daily_cap_target !== "" ? Number(config.daily_cap_target) : null,
              max_trades: config.daily_cap_max_trades !== "" ? Math.max(0, parseInt(config.daily_cap_max_trades, 10) || 0) : null,
            },
          }
        : {}),
```

- [ ] **Step 2: Build clean**

Run: `cd frontend && npm run build`
Expected: succeeds, no new warnings.

(No commit — continues into Task 6.)

---

## Task 6: Preset-execution emission (`buildExecutionFromConfig` + `buildExecutionFromRun`)

**Files:**
- Modify: `frontend/src/pages/BacktestLab.jsx:238-263` (`buildExecutionFromConfig`)
- Modify: `frontend/src/pages/BacktestLab.jsx:299-322` (`buildExecutionFromRun`)

Both functions **currently emit nothing** for the overlay — this **adds** it (new code, not a confirmation). Use the **same nested shape the deploy wizard reads** (LiveSignals.jsx:433-441 / builds at 489-507): `execution.exit_controls = {enabled, unit, breakeven:{trigger,lock}|null, trailing:{activation,distance}|null}` and `execution.daily_caps = {loss, target, max_trades}`. **Placement (mirrors the engine governor-vs-trail split):** `exit_controls` gated on `enabled && exit_mode === "option_levels"`; `daily_caps` gated only on "≥1 cap field set", **outside** the option_levels guard — so a `spot_exit` run that hit a daily cap still carries it into the preset. These are stored as plain dicts (never re-validated as `ExitControlsReq`), so the deploy-wizard's `null`-tolerant nested shape is correct here.

- [ ] **Step 1: `buildExecutionFromConfig` — add emission**

In `buildExecutionFromConfig`, after the `if (config.option_costs_enabled) { ex.cost_config = {...}; }` block (line 261) and **before** `return ex;` (line 262), insert:

```jsx
    // Exit/risk overlay -> execution (same nested shape the deploy wizard reads).
    // exit_controls only under option_levels (premium trailing); daily_caps whenever
    // a cap is set, regardless of exit_mode (governor-vs-trail split).
    if (config.exit_controls_enabled && config.option_exit_mode === "option_levels") {
      ex.exit_controls = {
        enabled: true,
        unit: config.exit_controls_unit,
        breakeven: (config.breakeven_trigger !== "" || config.breakeven_lock !== "")
          ? {
              trigger: config.breakeven_trigger !== "" ? Number(config.breakeven_trigger) : null,
              lock: config.breakeven_lock !== "" ? Number(config.breakeven_lock) : null,
            }
          : null,
        trailing: (config.trailing_activation !== "" || config.trailing_distance !== "")
          ? {
              activation: config.trailing_activation !== "" ? Number(config.trailing_activation) : null,
              distance: config.trailing_distance !== "" ? Number(config.trailing_distance) : null,
            }
          : null,
      };
    }
    if (config.daily_cap_loss !== "" || config.daily_cap_target !== "" || config.daily_cap_max_trades !== "") {
      ex.daily_caps = {
        loss: config.daily_cap_loss !== "" ? Number(config.daily_cap_loss) : null,
        target: config.daily_cap_target !== "" ? Number(config.daily_cap_target) : null,
        max_trades: config.daily_cap_max_trades !== "" ? Math.max(0, parseInt(config.daily_cap_max_trades, 10) || 0) : null,
      };
    }
```

- [ ] **Step 2: `buildExecutionFromRun` — add emission from the run doc**

In `buildExecutionFromRun(run)`, after the `if (ob.cost_config?.enabled) { ex.cost_config = {...}; }` block (line 320) and **before** `return ex;` (line 321), insert. Source from `ob.exit_controls`/`ob.daily_caps` (round-tripped via `OptionBacktestReq`); `ob.exit_controls` is the pydantic shape `{enabled, unit, breakeven:{trigger,lock}, trailing:{activation,distance}}`:

```jsx
    // Exit/risk overlay from the RUN DOC -> execution (same shape as the deploy
    // wizard reads). exit_controls only under option_levels; daily_caps regardless.
    const rec = ob.exit_controls;
    if (rec?.enabled && (ob.exit_mode || "spot_exit") === "option_levels") {
      ex.exit_controls = {
        enabled: true,
        unit: rec.unit || "pct",
        breakeven: rec.breakeven
          ? { trigger: rec.breakeven.trigger ?? null, lock: rec.breakeven.lock ?? null }
          : null,
        trailing: rec.trailing
          ? { activation: rec.trailing.activation ?? null, distance: rec.trailing.distance ?? null }
          : null,
      };
    }
    const rdc = ob.daily_caps;
    if (rdc && (rdc.loss != null || rdc.target != null || rdc.max_trades != null)) {
      ex.daily_caps = {
        loss: rdc.loss ?? null,
        target: rdc.target ?? null,
        max_trades: rdc.max_trades ?? null,
      };
    }
```

- [ ] **Step 3: Build clean**

Run: `cd frontend && npm run build`
Expected: succeeds, no new warnings.

(No commit — continues into Task 7.)

---

## Task 7: Prefills (`applyPreset` + `loadPastRun`) + commit the frontend change

**Files:**
- Modify: `frontend/src/pages/BacktestLab.jsx:201-217` (`applyPreset` `exFields`)
- Modify: `frontend/src/pages/BacktestLab.jsx:564-587` (`loadPastRun` `setConfig`)

So a survivor preset (or a re-loaded run) pre-fills the panel — the "inspect the optimizer's chosen exit" loop. Values are fractions (no conversion). Read with the deploy-wizard's null-tolerant pattern (LiveSignals.jsx:433-441).

- [ ] **Step 1: `applyPreset` — read overlay from the preset execution**

In `applyPreset`, inside the `exFields` object literal (the `ex ? { ... } : {}` at lines 201-217), after the `...(ex.cost_config?.enabled ? {...} : {}),` spread (line 216) and before the closing `} : {};` (line 217), add:

```jsx
        exit_controls_enabled: Boolean(ex.exit_controls?.enabled),
        exit_controls_unit: ex.exit_controls?.unit || "pct",
        breakeven_trigger: ex.exit_controls?.breakeven?.trigger ?? "",
        breakeven_lock: ex.exit_controls?.breakeven?.lock ?? "",
        trailing_activation: ex.exit_controls?.trailing?.activation ?? "",
        trailing_distance: ex.exit_controls?.trailing?.distance ?? "",
        daily_cap_loss: ex.daily_caps?.loss ?? "",
        daily_cap_target: ex.daily_caps?.target ?? "",
        daily_cap_max_trades: ex.daily_caps?.max_trades ?? "",
```

- [ ] **Step 2: `loadPastRun` — read overlay from the run doc's option_backtest**

In `loadPastRun`, inside the `setConfig((c) => ({ ...c, ... }))` object, after `option_assumed_stop_pct: r.config?.option_backtest?.sizing_config?.assumed_stop_pct_of_premium ?? 50,` (line 587, the last option line before `}))`), add:

```jsx
        exit_controls_enabled: Boolean(r.config?.option_backtest?.exit_controls?.enabled),
        exit_controls_unit: r.config?.option_backtest?.exit_controls?.unit || "pct",
        breakeven_trigger: r.config?.option_backtest?.exit_controls?.breakeven?.trigger ?? "",
        breakeven_lock: r.config?.option_backtest?.exit_controls?.breakeven?.lock ?? "",
        trailing_activation: r.config?.option_backtest?.exit_controls?.trailing?.activation ?? "",
        trailing_distance: r.config?.option_backtest?.exit_controls?.trailing?.distance ?? "",
        daily_cap_loss: r.config?.option_backtest?.daily_caps?.loss ?? "",
        daily_cap_target: r.config?.option_backtest?.daily_caps?.target ?? "",
        daily_cap_max_trades: r.config?.option_backtest?.daily_caps?.max_trades ?? "",
```

- [ ] **Step 3: Build clean**

Run: `cd frontend && npm run build`
Expected: succeeds, no new warnings beyond the 2 pre-existing.

- [ ] **Step 4: Commit the frontend change**

```bash
git add frontend/src/pages/BacktestLab.jsx frontend/src/components/backtest/PerformanceOverview.jsx
git commit -m "feat(backtest-ui): optional exit/risk overlay panel + attribution-read fix"
```

(If Task 2 was already committed standalone, drop `PerformanceOverview.jsx` from this `git add`.)

---

## Task 8: Running-stack verification (the real proof)

**Files:** none (observation only).

Per the verify skill: build the app, run it, drive the changed code, capture what you see. Rebuild the backend container (the `research.py` change) — Docker per the operating context.

- [ ] **Step 1: Rebuild + restart the backend container**

Run: `docker compose up -d --build backend` (from the repo root; adjust service name if different). Confirm health: `GET /api/health` returns ok.

- [ ] **Step 2: Backend e2e — off-path byte-identical**

`POST /api/backtest/start` with the panel **off** (no `exit_controls`/`daily_caps` in `option_backtest`). Expected: queued → run completes exactly as before; stored `config.option_backtest` has no overlay keys.

- [ ] **Step 3: Backend e2e — valid overlay runs + attribution**

`POST /api/backtest/start` with `option_backtest.enabled`, `exit_mode=option_levels`, a premium target/stop, `cost_config.enabled`, `exit_controls={enabled, unit:"pct", trailing:{activation:0.4,distance:0.25}}`, `daily_caps={loss:5000}`. Expected: run completes; `result.option_backtest.metrics.option_trail_exits` ≥ 0 and the skip counts present.

- [ ] **Step 4: Backend e2e — invalid overlay 400s at submit (proves Task 1)**

`POST /api/backtest/start` with `exit_controls.unit="pct"`, `trailing.distance=25` (out of (0,1)). Expected: **HTTP 400** with the validator message — NOT a queued run that later fails. Also confirm `enabled=false` + garbage overlay still **queues** (off-by-default API contract).

- [ ] **Step 5: Browser (Chrome) — panel + gating + attribution**

Open `:3000` Backtest Lab. Enable Option Execution → the Exit/Risk panel appears. Toggle it on: ₹-cap inputs disabled until costs on; trail/breakeven note + disable under "Mirror spot exit"; under "Option premium SL/target" they enable. Run a backtest with trailing + a daily cap → the **Exit controls** attribution block renders in Performance Overview (proves Task 2). Save it as a preset, open the deploy wizard → the overlay pre-fills (proves Tasks 6–7 + §5.4). Load the run back via "Load past run" → the panel pre-fills (proves Task 7 loadPastRun).

- [ ] **Step 6: Report**

Capture: the off-path byte-identical run config, the 400 response body, the attribution block screenshot, the deploy-wizard prefill screenshot. Verdict PASS/FAIL/BLOCKED per the verify skill.

---

## Self-review notes (done during planning)

- **Spec coverage:** §5.1.1 → Task 3; §5.1.2 → Task 4; §5.1.3 → Task 5; §5.1.4/.5 → Task 6; §5.1.6 → Task 7; §5.2 → Task 2; §5.3 → Task 1; §5.4 (shape) → Tasks 6–7 use the deploy-wizard shape; §6 empty-object guard → Task 5 (gated, no `{}`); §8 verification → Task 8. All covered.
- **Pydantic nesting caveat (resolved):** `option_backtest.exit_controls` (Task 5) is `ExitControlsReq` with non-Optional `breakeven`/`trailing` float sub-models → emit numeric-only dicts, omit when blank (no `null`). The **execution** shape (Task 6) is a stored dict, never `ExitControlsReq`-validated → deploy-wizard `null`-tolerant shape is fine. These two intentionally differ; that is correct, not an inconsistency.
- **Type consistency:** the 9 field names match LiveSignals.jsx exactly (`exit_controls_enabled`, `exit_controls_unit`, `breakeven_trigger`, `breakeven_lock`, `trailing_activation`, `trailing_distance`, `daily_cap_loss`, `daily_cap_target`, `daily_cap_max_trades`).
- **Off-by-default:** every emit is gated; no field is added to existing keys; verified by Task 8 Step 2.
