# Exit / Risk Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add premium-axis trailing-stop + breakeven + soft per-day loss/target/max-trades caps as one execution overlay that the backtest sim enforces, the survival gate scores, and the live paper engine enforces — at decider-level sim↔live parity.

**Architecture:** A new pure module `app/exit_controls.py` owns the two deciders (`effective_premium_stop`, `daily_governor_decision`) + a validator, so sim and live can't drift. The sim wires it into `option_backtest.simulate_paired_option_trades` (one additive `default-None` kwarg pair); live wires it into `paper_auto.mark_open_deployment_trades` (ratchet `risk.stop_price`) and a new soft governor in `deployment_kill_switch.py`. The optimizer forwards the overlay into both finalist call sites so `survival_verdict` scores it. Phased: **Commit 1** enforce+evaluate, **Commit 2** bounded finalist-grid search. Off by default ⇒ byte-identical.

**Tech Stack:** Python 3.11 / FastAPI / pandas / numpy (backend), React/CRA (frontend), pytest (host — NO motor/optuna; `optimizer.py` is verified via `py_compile` + running-stack e2e, never imported in tests), Docker.

**Spec:** [docs/superpowers/specs/2026-06-15-exit-risk-controls-design.md](../specs/2026-06-15-exit-risk-controls-design.md)

---

## Post-verification corrections (a 4-agent plan audit found these — apply them)

The plan below was verified against the real code; these cross-cutting fixes are folded in. **Tests live at repo-root `tests/` and run from the repo root** (`python -m pytest tests/...`); new test files start with the `sys.path.insert(.., "backend")` bootstrap shown in Task 1.

1. **All FOUR option-sim call sites must forward the overlay**, not just the optimizer's two. Besides `_survival_eval_oos` + `_option_rerank` (Task 9), also forward `exit_controls=...`/`daily_caps=...` (read from `option_cfg` / `config.option_backtest`) into:
   - `runtime.py:577` (`_run_paired_option_backtest`) — the **plain backtest** path (Task 8 retargets here);
   - `wfo.py:453` — the **walk-forward OOS** path;
   and have `preset_execution.py` copy `exit_controls`/`daily_caps` into the execution block so a deployed preset enforces them live. Without these the overlay is a **silent no-op** on those paths (the §13 G5 failure class).
2. **max_trades counting is `already-admitted` on BOTH sides** (Task 5 sim + Task 11 live): check the governor with the count of trades **already admitted/created** this session, increment only on a real admit, and the decider halts on `>= max_trades`. This is the sim↔live parity fix (a post-increment sim count admitted N−1 vs live's N).
3. **Parity goldens** (spec §7/§9) — add a step creating/extending `tests/test_execution_policy.py` that feeds `effective_premium_stop` and `daily_governor_decision` the **same** inputs the sim-bar and live-tick paths construct and asserts identical decider outputs (incl. the already-admitted count convention).
4. **Conduit test (§13 F1)** — add a **host** test calling `simulate_paired_option_trades` with `exit_controls.enabled` true-vs-false on a crafted premium series and asserting the resulting ₹ equity curve differs (proves the kwarg reaches `_walk_option_exit`; optimizer.py can't be imported on the host).
5. **V5 response-key pinning** — export the metric **key-name** constants (`METRIC_OPTION_TRAIL_EXITS = "option_trail_exits"`, …, and the `skipped_*` keys) from `schemas.py`; import them in `option_backtest._compute_metrics` for the dict keys; assert their presence in the contract test (Task 7).
6. **Scoping of two §5.7 extras:** the new exit reasons already flow onto each trade's `exit_reason` (via `close_trade`), so the **Signal Journal renders OPTION_TRAIL_STOP/OPTION_BREAKEVEN_STOP automatically** — Task 12 only adds the daily-halt skip surfacing. The **"estimated ₹ impact vs the same finalist without the overlay"** (a per-finalist double-sim) is **deferred to Commit 2** alongside the grid search — Commit 1 ships the count-based attribution; note this in the CHANGELOG.
7. **Deployment overlay home:** `DeploymentCreateReq.risk` is a free `Dict`, so `exit_controls`/`daily_caps` ride inside it (read by `build_auto_trade` + `check_soft_daily_governor`). Add a round-trip test that a `risk` carrying the overlay survives create and is read back (V1's third path).

---

## File Structure

**Create**
- `backend/app/exit_controls.py` — pure deciders + validator + exit-reason/skip-reason constants (host-testable; no motor/optuna).
- `tests/test_exit_controls.py` — unit tests for the pure module.

**Modify (backend)**
- `backend/app/option_backtest.py` — additive `exit_controls`/`daily_caps` kwargs on `simulate_paired_option_trades`; trail/breakeven + gap-open-clamp in `_walk_option_exit`; entry-session daily governor in the pairing loop; `skipped_by_cap` coverage; attribution in `_compute_metrics`.
- `backend/app/schemas.py` — `ExitControlsReq` + `DailyCapsReq` models; attribution/exit-reason key constants; add to `OptionBacktestReq`; `search_exit_controls` on `OptimizerStartReq`.
- `backend/app/routers/research.py` — call `validate_exit_risk_config` in the option-backtest + `optimize_start` paths (400s); never leak `SKIPPED_DAILY_CAP` rows to the response.
- `backend/app/routers/deployments.py` — call `validate_exit_risk_config` in deployment-create (per-path cost flag = `friction.costs`).
- `backend/app/optimizer.py` — forward `exit_controls`/`daily_caps` at both finalist call sites; persist overlay into the saved best; (Commit 2) bounded grid search.
- `backend/app/paper_auto.py` — seed `running_max_premium` in `build_auto_trade`; ratchet `risk.stop_price` in `mark_open_deployment_trades`; soft-governor gate in `auto_paper_trade_for_signal`.
- `backend/app/deployment_kill_switch.py` — async `check_soft_daily_governor` wrapper (entry-session keyed) calling the pure decider.
- `backend/app/runtime.py` — **the plain-backtest path calls `simulate_paired_option_trades` here** (`_run_paired_option_backtest`, runtime.py:577), NOT research.py. Forward the overlay + validate + segregate SKIPPED rows here.
- `backend/app/wfo.py` — walk-forward (honest-OOS) calls the same sim (wfo.py:453). Forward the overlay so OOS reflects it (else silent no-op).
- `backend/app/preset_execution.py` — carry `exit_controls`/`daily_caps` from `option_config` into the execution block so a deployed preset enforces live what survival scored.

**Modify (frontend)**
- `frontend/src/pages/Deployments*/deploy wizard` — Exit/Risk panel.
- `frontend/src/pages/Optimizer.jsx` — (Commit 2) exit-search toggle + per-finalist chosen config.
- `frontend/src/components/backtest/*` — exit-reason mix + attribution block.

**Modify (tests)**
- `tests/test_option_backtest.py`, `test_paper_auto.py`, `test_deployment_kill_switch.py`, contract tests under `tests/`.

**Docs:** `CHANGELOG.md`, `docs/HANDOFF.md` (one batched pass per commit).

---

# COMMIT 1 — Enforce + evaluate the overlay

### Task 1: `exit_controls.py` — config + `effective_premium_stop`

**Files:**
- Create: `backend/app/exit_controls.py`
- Test: `tests/test_exit_controls.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_exit_controls.py
# NEW test files live at repo-root tests/ and must bootstrap backend onto sys.path
# (mirrors tests/test_option_backtest.py / test_deployment_kill_switch.py). Run from REPO ROOT.
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.exit_controls import ExitControlsConfig, effective_premium_stop


def test_disabled_returns_base_stop_only():
    cfg = ExitControlsConfig.from_dict({"enabled": False})
    assert effective_premium_stop(entry=100.0, running_max=200.0, base_stop=80.0, cfg=cfg) == 80.0
    assert effective_premium_stop(entry=100.0, running_max=200.0, base_stop=None, cfg=cfg) is None


def test_breakeven_pct_raises_to_entry_once_triggered():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "breakeven": {"trigger": 0.30, "lock": 0.0}})
    # not yet up 30% -> base stop only
    assert effective_premium_stop(entry=100.0, running_max=120.0, base_stop=80.0, cfg=cfg) == 80.0
    # up 30% -> stop ratchets to entry (100)
    assert effective_premium_stop(entry=100.0, running_max=130.0, base_stop=80.0, cfg=cfg) == 100.0


def test_trailing_pct_trails_running_max():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.40, "distance": 0.25}})
    # not activated (needs +40%)
    assert effective_premium_stop(entry=100.0, running_max=130.0, base_stop=80.0, cfg=cfg) == 80.0
    # activated at +50% -> trail = 150*(1-0.25)=112.5
    assert effective_premium_stop(entry=100.0, running_max=150.0, base_stop=80.0, cfg=cfg) == 112.5


def test_pts_unit_uses_additive_levels():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pts",
                                        "breakeven": {"trigger": 20.0, "lock": 5.0},
                                        "trailing": {"activation": 30.0, "distance": 10.0}})
    # up 35 pts (rm=135): breakeven lock = 105; trail = 135-10 = 125 -> max = 125
    assert effective_premium_stop(entry=100.0, running_max=135.0, base_stop=80.0, cfg=cfg) == 125.0


def test_monotonic_never_below_base():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.90}})
    # huge distance would put trail below base; effective must not drop below base 80
    assert effective_premium_stop(entry=100.0, running_max=120.0, base_stop=80.0, cfg=cfg) == 80.0
```

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.exit_controls'`).

- [ ] **Step 3: Implement the module (config + stop decider + constants)**

```python
# backend/app/exit_controls.py
"""Pure execution-overlay deciders: premium trailing/breakeven stop + per-day
governor + validation. THE single source both the sim (option_backtest) and the
live mark (paper_auto / deployment_kill_switch) call, so they can never drift.

No motor/optuna imports -> host-testable like app/survival.py. Never raises on
bad config (the router validates too); silently ignores out-of-range values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# --- Exit / skip reason taxonomy (single source; schemas re-exports the names) ---
EXIT_TRAIL_STOP = "OPTION_TRAIL_STOP"
EXIT_BREAKEVEN_STOP = "OPTION_BREAKEVEN_STOP"
SKIPPED_STATUS = "SKIPPED_DAILY_CAP"
SKIP_DAILY_LOSS = "DAILY_LOSS_HALT"
SKIP_DAILY_TARGET = "DAILY_TARGET_HALT"
SKIP_MAX_TRADES = "MAX_TRADES_HALT"


def _pos(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


@dataclass
class ExitControlsConfig:
    enabled: bool = False
    unit: str = "pct"              # "pct" (of entry premium) | "pts" (absolute premium)
    be_trigger: float = 0.0        # breakeven: > 0 enables
    be_lock: float = 0.0
    trail_activation: float = 0.0  # trailing: trail_distance > 0 enables
    trail_distance: float = 0.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ExitControlsConfig":
        if not data:
            return cls()
        cfg = cls()
        cfg.enabled = bool(data.get("enabled"))
        if str(data.get("unit") or "pct").lower() == "pts":
            cfg.unit = "pts"
        be = data.get("breakeven") or {}
        tr = data.get("trailing") or {}
        for attr, raw in (("be_trigger", be.get("trigger")), ("be_lock", be.get("lock")),
                          ("trail_activation", tr.get("activation")), ("trail_distance", tr.get("distance"))):
            try:
                if raw is not None and raw != "":
                    setattr(cfg, attr, float(raw))
            except (TypeError, ValueError):
                pass
        return cfg


def effective_premium_stop(*, entry: float, running_max: float,
                           base_stop: Optional[float], cfg: ExitControlsConfig) -> Optional[float]:
    """The ratcheted LONG-option stop = max(base, breakeven?, trailing?). Monotonic
    non-decreasing in running_max. Disabled cfg ⇒ base_stop unchanged."""
    candidates: List[float] = []
    if base_stop is not None:
        candidates.append(float(base_stop))
    if cfg.enabled:
        e = float(entry)
        rm = float(running_max)
        if cfg.be_trigger and cfg.be_trigger > 0:
            if cfg.unit == "pts":
                trigger_level = e + cfg.be_trigger
                lock_level = e + (cfg.be_lock or 0.0)
            else:
                trigger_level = e * (1.0 + cfg.be_trigger)
                lock_level = e * (1.0 + (cfg.be_lock or 0.0))
            if rm >= trigger_level:
                candidates.append(lock_level)
        if cfg.trail_distance and cfg.trail_distance > 0:
            if cfg.unit == "pts":
                activation_level = e + (cfg.trail_activation or 0.0)
                trail_level = rm - cfg.trail_distance
            else:
                activation_level = e * (1.0 + (cfg.trail_activation or 0.0))
                trail_level = rm * (1.0 - cfg.trail_distance)
            if rm >= activation_level:
                candidates.append(trail_level)
    return max(candidates) if candidates else None


def stop_fill_price(level: float, reason: str, bar_open: Optional[float]) -> float:
    """Gap-fill honesty (overlay path only): a LONG stop that gaps below fills at the
    bar OPEN, not the (higher) stop level. Non-stop reasons fill at the level."""
    if reason == "STOP" and bar_open is not None:
        try:
            o = float(bar_open)
            if o < float(level):
                return o
        except (TypeError, ValueError):
            pass
    return float(level)
```

- [ ] **Step 4: Run the tests — expect pass**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/exit_controls.py tests/test_exit_controls.py
git commit -m "feat(exit-controls): pure effective_premium_stop + config + reason constants"
```

---

### Task 2: `exit_controls.py` — `daily_governor_decision`

**Files:**
- Modify: `backend/app/exit_controls.py`
- Test: `tests/test_exit_controls.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_controls.py
from app.exit_controls import (DailyCapsConfig, daily_governor_decision,
                               SKIP_DAILY_LOSS, SKIP_DAILY_TARGET, SKIP_MAX_TRADES)


def test_governor_unset_never_halts():
    cfg = DailyCapsConfig.from_dict(None)
    d = daily_governor_decision(realized_cum_min=-99999.0, realized_cum_max=99999.0, entry_count=99, cfg=cfg)
    assert d == {"halt": False, "reason": None}


def test_governor_loss_trips_on_cumulative_min_sticky():
    cfg = DailyCapsConfig.from_dict({"loss": 15000})
    # current cumulative back above -15000 but the running MIN dipped below -> sticky halt
    d = daily_governor_decision(realized_cum_min=-16000.0, realized_cum_max=2000.0, entry_count=3, cfg=cfg)
    assert d["halt"] and d["reason"] == SKIP_DAILY_LOSS


def test_governor_target_trips_on_cumulative_max():
    cfg = DailyCapsConfig.from_dict({"target": 25000})
    d = daily_governor_decision(realized_cum_min=-1000.0, realized_cum_max=25000.0, entry_count=2, cfg=cfg)
    assert d["halt"] and d["reason"] == SKIP_DAILY_TARGET


def test_governor_max_trades_counts_entries():
    cfg = DailyCapsConfig.from_dict({"max_trades": 6})
    assert daily_governor_decision(realized_cum_min=0.0, realized_cum_max=0.0, entry_count=5, cfg=cfg)["halt"] is False
    d = daily_governor_decision(realized_cum_min=0.0, realized_cum_max=0.0, entry_count=6, cfg=cfg)
    assert d["halt"] and d["reason"] == SKIP_MAX_TRADES


def test_governor_loss_precedes_target_and_maxtrades():
    cfg = DailyCapsConfig.from_dict({"loss": 1000, "target": 1000, "max_trades": 1})
    d = daily_governor_decision(realized_cum_min=-2000.0, realized_cum_max=2000.0, entry_count=5, cfg=cfg)
    assert d["reason"] == SKIP_DAILY_LOSS
```

- [ ] **Step 2: Run it — expect failure**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: FAIL (`ImportError: cannot import name 'DailyCapsConfig'`).

- [ ] **Step 3: Implement**

```python
# add to backend/app/exit_controls.py
@dataclass
class DailyCapsConfig:
    loss: Optional[float] = None        # ₹ (positive); halt when session cum-realized <= -loss
    target: Optional[float] = None      # ₹ (positive); halt when session cum-realized >= target
    max_trades: Optional[int] = None    # entries per IST session

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DailyCapsConfig":
        if not data:
            return cls()
        cfg = cls()
        cfg.loss = _pos(data.get("loss"))
        cfg.target = _pos(data.get("target"))
        mt = data.get("max_trades")
        try:
            cfg.max_trades = int(mt) if mt not in (None, "") and int(mt) > 0 else None
        except (TypeError, ValueError):
            cfg.max_trades = None
        return cfg

    @property
    def active(self) -> bool:
        return self.loss is not None or self.target is not None or self.max_trades is not None


def daily_governor_decision(*, realized_cum_min: float, realized_cum_max: float,
                            entry_count: int, cfg: DailyCapsConfig) -> Dict[str, Any]:
    """Soft per-session halt from the session's cumulative-realized EXTREMA (sticky)
    + the entry count. Loss is surfaced before target before max-trades."""
    if cfg.loss is not None and float(realized_cum_min) <= -abs(cfg.loss):
        return {"halt": True, "reason": SKIP_DAILY_LOSS}
    if cfg.target is not None and float(realized_cum_max) >= abs(cfg.target):
        return {"halt": True, "reason": SKIP_DAILY_TARGET}
    if cfg.max_trades is not None and int(entry_count) >= cfg.max_trades:
        return {"halt": True, "reason": SKIP_MAX_TRADES}
    return {"halt": False, "reason": None}
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/exit_controls.py tests/test_exit_controls.py
git commit -m "feat(exit-controls): sticky daily_governor_decision (cum-extremum + entry count)"
```

---

### Task 3: `exit_controls.py` — `validate_exit_risk_config`

**Files:**
- Modify: `backend/app/exit_controls.py`
- Test: `tests/test_exit_controls.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_exit_controls.py
from app.exit_controls import validate_exit_risk_config


def test_validate_clean_config_no_errors():
    errs = validate_exit_risk_config(
        {"enabled": True, "unit": "pct", "breakeven": {"trigger": 0.3, "lock": 0.0},
         "trailing": {"activation": 0.4, "distance": 0.25}},
        {"loss": 15000, "max_trades": 6},
        costs_on=True, option_exec_on=True)
    assert errs == []


def test_validate_rupee_cap_requires_costs():
    errs = validate_exit_risk_config({}, {"loss": 15000}, costs_on=False, option_exec_on=True)
    assert any("costs" in e.lower() for e in errs)


def test_validate_exit_controls_require_option_exec():
    errs = validate_exit_risk_config({"enabled": True, "trailing": {"activation": 0.4, "distance": 0.25}},
                                     {}, costs_on=True, option_exec_on=False)
    assert any("option" in e.lower() for e in errs)


def test_validate_ranges():
    errs = validate_exit_risk_config(
        {"enabled": True, "unit": "pct", "breakeven": {"trigger": 0.2, "lock": 0.5},  # lock >= trigger
         "trailing": {"activation": 0.4, "distance": 1.5}},                            # distance >= 1 (pct)
        {"max_trades": 0}, costs_on=True, option_exec_on=True)
    assert len(errs) >= 2
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: FAIL (`ImportError: cannot import name 'validate_exit_risk_config'`).

- [ ] **Step 3: Implement**

```python
# add to backend/app/exit_controls.py
def validate_exit_risk_config(exit_controls: Optional[Dict[str, Any]],
                              daily_caps: Optional[Dict[str, Any]],
                              *, costs_on: bool, option_exec_on: bool) -> List[str]:
    """Pure validation; returns a list of error strings (empty = valid). The
    corpus-visible routers call this and raise 400 on any error."""
    errs: List[str] = []
    ec = ExitControlsConfig.from_dict(exit_controls)
    dc = DailyCapsConfig.from_dict(daily_caps)

    if ec.enabled and not option_exec_on:
        errs.append("exit_controls require option execution (option_levels / option re-rank); "
                    "premium trailing is impossible spot-only.")
    if (dc.loss is not None or dc.target is not None) and not costs_on:
        errs.append("daily ₹ caps (loss/target) require costs enabled (else the cap acts on gross P&L).")

    if ec.enabled:
        unit = ec.unit
        if ec.trail_distance and ec.trail_distance > 0:
            if unit == "pct" and not (0.0 < ec.trail_distance < 1.0):
                errs.append("trailing.distance must be in (0, 1) for unit=pct.")
            if unit == "pts" and ec.trail_distance <= 0:
                errs.append("trailing.distance must be > 0 for unit=pts.")
        if ec.be_trigger and ec.be_trigger > 0 and ec.be_lock and ec.be_lock >= ec.be_trigger:
            errs.append("breakeven.lock must be < breakeven.trigger.")
    if dc.loss is not None and dc.loss <= 0:
        errs.append("daily_caps.loss must be > 0.")
    if dc.target is not None and dc.target <= 0:
        errs.append("daily_caps.target must be > 0.")
    if daily_caps and daily_caps.get("max_trades") is not None and dc.max_trades is None:
        errs.append("daily_caps.max_trades must be an integer >= 1.")
    return errs
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_exit_controls.py -q`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/exit_controls.py tests/test_exit_controls.py
git commit -m "feat(exit-controls): pure validate_exit_risk_config (per-path costs + option-exec + ranges)"
```

---

### Task 4: Sim — trail/breakeven + gap-clamp in `_walk_option_exit`

**Files:**
- Modify: `backend/app/option_backtest.py` (signature `simulate_paired_option_trades` ~216-235; `_walk_option_exit` 89-129; call into the walk ~414-432)
- Test: `tests/test_option_backtest.py`

- [ ] **Step 1: Write the failing test (the conduit + behavior + look-ahead)**

```python
# append to tests/test_option_backtest.py
import pandas as pd
from app.option_backtest import _walk_option_exit
from app.exit_controls import ExitControlsConfig


def _bars(rows):
    return pd.DataFrame(rows)


def test_walk_trailing_stop_exits_on_giveback_not_lookahead():
    # entry 100 at ts0. Bars rise to 200 then pull back. Trail distance 0.25.
    # The bar that prints the 200 high must NOT be stopped at 150 within itself.
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.25}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 120, "low": 100, "close": 120},
        {"ts": 2, "open": 120, "high": 200, "low": 118, "close": 190},  # sets peak 200; low 118 must NOT stop
        {"ts": 3, "open": 150, "high": 152, "low": 140, "close": 145},  # trail=200*0.75=150; low 140 <= 150 -> STOP
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=3, entry_price=100.0,
                            target_level=None, stop_level=None, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_TRAIL_STOP"
    assert out["exit_ts"] == 3
    assert out["exit_price"] == 150.0  # filled at the trail level (no gap)


def test_walk_trailing_gap_fills_at_open():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.25}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},  # peak 200 (entry bar excluded by ts>entry_ts)
        {"ts": 2, "open": 130, "high": 131, "low": 120, "close": 125},  # opens 130 < trail 150 -> gap fill at 130
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                            target_level=None, stop_level=None, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_TRAIL_STOP"
    assert out["exit_price"] == 130.0


def test_walk_disabled_cfg_is_legacy_behavior():
    # No exit_cfg -> the old fixed-level walk; no premium levels -> signal exit at last bar.
    rows = _bars([{"ts": 1, "open": 100, "high": 110, "low": 95, "close": 108}])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                            target_level=None, stop_level=None)
    assert out["exit_reason"] == "OPTION_SIGNAL_EXIT"
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_option_backtest.py -q -k walk`
Expected: FAIL (`_walk_option_exit() got an unexpected keyword argument 'exit_cfg'`).

- [ ] **Step 3: Implement — extend `_walk_option_exit`**

Replace `_walk_option_exit` (option_backtest.py:89-129) so it accepts an optional `exit_cfg` and ratchets `running_max` AFTER each bar's check (look-ahead safe), filling stops via the gap-clamp:

```python
def _walk_option_exit(
    rows: pd.DataFrame,
    *,
    entry_ts: int,
    backstop_ts: int,
    entry_price: float,
    target_level: Optional[float],
    stop_level: Optional[float],
    exit_cfg: Optional["ExitControlsConfig"] = None,
) -> Dict[str, Any]:
    """Walk option candles forward to the first premium-level exit. With exit_cfg
    enabled, the stop is ratcheted per bar via effective_premium_stop using the
    running-max premium THROUGH the prior bar (look-ahead safe), and a long stop
    that gaps below fills at the bar open."""
    from app.exit_controls import (effective_premium_stop, stop_fill_price,
                                   EXIT_TRAIL_STOP, EXIT_BREAKEVEN_STOP)
    forward = rows[(rows["ts"] > entry_ts) & (rows["ts"] <= backstop_ts)].sort_values("ts")
    last_close = entry_price
    last_ts = entry_ts
    running_max = float(entry_price)
    use_overlay = exit_cfg is not None and exit_cfg.enabled
    for _, bar in forward.iterrows():
        bar_ts = int(bar["ts"])
        high = float(bar.get("high", bar.get("close", entry_price)))
        low = float(bar.get("low", bar.get("close", entry_price)))
        bar_open = bar.get("open")
        last_close = float(bar.get("close", last_close))
        last_ts = bar_ts
        eff_stop = (effective_premium_stop(entry=entry_price, running_max=running_max,
                                           base_stop=stop_level, cfg=exit_cfg)
                    if use_overlay else stop_level)
        level, reason = intrabar_exit(
            high=high, low=low, stop=eff_stop, target=target_level, is_long=True,
        )
        if level is not None:
            if reason == "STOP":
                fill = stop_fill_price(level, reason, bar_open) if use_overlay else level
                # Tag which control bound the stop (trail/breakeven vs base).
                exit_reason = "OPTION_STOP"
                if use_overlay and stop_level is not None and eff_stop is not None and eff_stop > float(stop_level):
                    exit_reason = (EXIT_BREAKEVEN_STOP
                                   if _breakeven_binding(entry_price, running_max, stop_level, eff_stop, exit_cfg)
                                   else EXIT_TRAIL_STOP)
                elif use_overlay and stop_level is None and eff_stop is not None:
                    exit_reason = EXIT_TRAIL_STOP
                return {"exit_ts": bar_ts, "exit_price": fill, "exit_reason": exit_reason}
            return {"exit_ts": bar_ts, "exit_price": level, "exit_reason": "OPTION_TARGET"}
        running_max = max(running_max, high)
    return {"exit_ts": last_ts, "exit_price": last_close, "exit_reason": "OPTION_SIGNAL_EXIT"}


def _breakeven_binding(entry, running_max, base_stop, eff_stop, cfg) -> bool:
    """True when the breakeven candidate (not trailing) produced eff_stop — for
    exit-reason attribution. Recomputes the breakeven level only."""
    e = float(entry)
    if not (cfg.be_trigger and cfg.be_trigger > 0):
        return False
    if cfg.unit == "pts":
        be_level = e + (cfg.be_lock or 0.0)
        trig = e + cfg.be_trigger
    else:
        be_level = e * (1.0 + (cfg.be_lock or 0.0))
        trig = e * (1.0 + cfg.be_trigger)
    return float(running_max) >= trig and abs(be_level - float(eff_stop)) < 1e-9
```

Add the import at the top of option_backtest.py (after the existing imports):

```python
from app.exit_controls import ExitControlsConfig
```

Add the two `default-None` kwargs to `simulate_paired_option_trades` (after `sizing_config: Optional[Dict[str, Any]] = None,` at option_backtest.py:235):

```python
    exit_controls: Optional[Dict[str, Any]] = None,
    daily_caps: Optional[Dict[str, Any]] = None,
```

Parse them near the other `*_cfg` parsing (after sizing_cfg at ~263):

```python
    exit_cfg = ExitControlsConfig.from_dict(exit_controls)
```

Pass `exit_cfg` into the walk (option_backtest.py:422-429 call):

```python
            walk = _walk_option_exit(
                rows,
                entry_ts=int(entry["ts"]),
                backstop_ts=int(exit_candle["ts"]),
                entry_price=entry_price,
                target_level=option_levels["target_level"],
                stop_level=option_levels["stop_level"],
                exit_cfg=exit_cfg,
            )
```

Also extend the `option_exit_reason` mapping so the new reasons are treated as option exits when classifying (option_backtest.py:432 already assigns `walk["exit_reason"]` to `option_exit_reason`; no change needed — the new tags flow through).

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_option_backtest.py -q`
Expected: PASS (existing + 3 new). Existing tests stay green (disabled path unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/option_backtest.py tests/test_option_backtest.py
git commit -m "feat(option-backtest): premium trail/breakeven + gap-open-clamp in _walk_option_exit (look-ahead safe)"
```

---

### Task 5: Sim — entry-session daily governor in the pairing loop

**Files:**
- Modify: `backend/app/option_backtest.py` (pairing loop 300-506; `_coverage` 34-41; return dict 508-529)
- Test: `tests/test_option_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_option_backtest.py
import pandas as pd
from app.option_backtest import simulate_paired_option_trades


def _spot_trade(idx, entry_ts, exit_ts, entry_price=100.0, direction="CE"):
    return {"direction": direction, "entry_ts": entry_ts, "exit_ts": exit_ts,
            "entry_price": entry_price, "exit_price": entry_price, "entry_datetime": "",
            "exit_datetime": "", "regime": "", "ist_time": ""}


def test_daily_cap_skips_later_same_session_entries():
    # Trade 0 must PAIR (admitted=1); trade 1 same IST session with max_trades=1 -> SKIPPED.
    # If select/pairing needs more contract fields, reuse this file's existing paired-fixture helper.
    day_ms = 1_700_000_000_000
    spot = [_spot_trade(0, day_ms, day_ms + 60000),
            _spot_trade(1, day_ms + 120000, day_ms + 180000)]
    contracts = [{"instrument_key": "NSE_FO|OPT", "underlying": "NIFTY",
                  "expiry_date": "2023-11-16", "strike": 100, "side": "CE",
                  "lot_size": 50, "trading_symbol": "NIFTY-CE-100", "atm": 100}]
    candles = pd.DataFrame([
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms, "open": 10, "high": 11, "low": 9, "close": 10},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 60000, "open": 10, "high": 12, "low": 9, "close": 11},
    ])
    res = simulate_paired_option_trades(
        spot_trades=spot, contracts=contracts, option_candles=candles, underlying="NIFTY",
        moneyness="atm", fixed_expiry_date="2023-11-16", daily_caps={"max_trades": 1})
    statuses = [t["status"] for t in res["trades"]]
    assert statuses.count("PAIRED") == 1              # trade 0 admitted
    assert statuses.count("SKIPPED_DAILY_CAP") == 1   # trade 1 capped
    assert res["coverage"].get("skipped_by_cap", 0) == 1
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_option_backtest.py -q -k daily_cap`
Expected: FAIL (`simulate_paired_option_trades() got an unexpected keyword argument 'daily_caps'`).

- [ ] **Step 3: Implement — governor in the pairing loop**

Add to `_coverage()` (option_backtest.py:34-41): `"skipped_by_cap": 0,`.

Add a helper near the top of option_backtest.py:

```python
def _ist_session_date(ts_ms: Any) -> Optional[str]:
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    try:
        return (_dt.fromtimestamp(int(ts_ms) / 1000, tz=_tz.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
    except Exception:
        return None
```

Parse the caps config (after `exit_cfg = ...`):

```python
    from app.exit_controls import DailyCapsConfig, daily_governor_decision, SKIPPED_STATUS
    caps_cfg = DailyCapsConfig.from_dict(daily_caps)
    # Per-IST-ENTRY-session ledger: running cumulative realized + its extrema + entry count.
    session_ledger: Dict[str, Dict[str, float]] = {}
```

Insert the governor check **right before `if not selected:`** (option_backtest.py:355) — i.e. AFTER `base` (incl. `base["context"]`) is fully built at option_backtest.py:323-354, so the `SKIPPED` row can carry `base` with **no relocation / NameError** (the verifier caught that moving `base` up would leave `resolved_expiry` undefined). **Count convention (the parity fix):** check against the **already-ADMITTED** count and increment **only on a PAIRED realize** — never for missing-contract/candle or cap-skipped rows — so the sim counts the same population live counts (created trades), and `max_trades=N` admits exactly N:

```python
        sess = _ist_session_date(spot_trade.get("entry_ts")) if caps_cfg.active else None
        if sess is not None:
            led = session_ledger.setdefault(sess, {"cum": 0.0, "min": 0.0, "max": 0.0, "admitted": 0})
            decision = daily_governor_decision(
                realized_cum_min=led["min"], realized_cum_max=led["max"],
                entry_count=int(led["admitted"]), cfg=caps_cfg)   # ALREADY-admitted count (pre-this-trade)
            if decision["halt"]:
                coverage["skipped_by_cap"] += 1
                paired_trades.append({**base, "status": SKIPPED_STATUS, "skip_reason": decision["reason"]})
                continue
```

After a trade is realized PAIRED (option_backtest.py:470, where `pnl_value` is computed), increment `admitted` and fold realized ₹ into the ledger:

```python
        if sess is not None:
            led = session_ledger[sess]
            led["admitted"] += 1                      # only PAIRED counts toward max_trades
            led["cum"] += float(pnl_value)
            led["min"] = min(led["min"], led["cum"])
            led["max"] = max(led["max"], led["cum"])
```

Add the kwargs to the return `coverage` (already includes `skipped_by_cap` via `_coverage()`); nothing else changes.

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_option_backtest.py -q`
Expected: PASS (existing + new). Verify a no-caps run is unchanged: `pytest tests/test_option_backtest.py -q` all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/option_backtest.py tests/test_option_backtest.py
git commit -m "feat(option-backtest): entry-session daily governor (SKIPPED_DAILY_CAP + skipped_by_cap coverage)"
```

---

### Task 6: Sim — survival coverage excludes cap-skips + attribution metrics

**Files:**
- Modify: `backend/app/survival.py:178` (coverage denominator); `backend/app/optimizer.py:571-572` (accumulate `skipped_by_cap`); `backend/app/option_backtest.py` `_compute_metrics` (attribution)
- Test: `tests/test_survival.py`, `tests/test_option_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_survival.py
from app.survival import survival_verdict, SurvivalConfig


def test_cap_skips_excluded_from_coverage_denominator():
    cfg = SurvivalConfig(enabled=True)
    port = {"max_drawdown_pct": -5.0, "total_return_pct": 10.0,
            "curve": [{"equity_value": 210000, "ts": 1}]}
    # 10 spot, 6 paired, 4 skipped_by_cap -> eligible = 10-4 = 6, ratio 6/6 = 1.0 (not low coverage)
    verdict = survival_verdict(
        portfolio=port, trade_pnls=[100.0] * 6, cfg=cfg,
        coverage={"spot_trade_count": 10, "paired_trade_count": 6, "skipped_by_cap": 4},
        capital=200000.0)
    assert verdict["low_coverage"] is False
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_survival.py -q -k coverage_denominator`
Expected: FAIL (ratio 6/10 = 0.6 < 0.8 → `low_coverage` True).

- [ ] **Step 3: Implement**

In `survival.py` `survival_verdict` (survival.py:156-179), subtract cap-skips from the denominator:

```python
    spot_ct = int((coverage or {}).get("spot_trade_count", 0) or 0)
    skipped_by_cap = int((coverage or {}).get("skipped_by_cap", 0) or 0)
    eligible_ct = max(0, spot_ct - skipped_by_cap)   # cap-skips are deliberate, not missing data
    paired_ct = int((coverage or {}).get("paired_trade_count", n) or 0)
    ...
    if eligible_ct > 0 and (paired_ct / eligible_ct) < MIN_COVERAGE:
        return {**base, "low_coverage": True, "reason": "low_coverage"}
```

In `optimizer.py` `_survival_eval_oos` (optimizer.py:570-572, 587), accumulate + pass `skipped_by_cap`:

```python
        spot_total += int(cov.get("spot_trade_count", 0) or 0)
        paired_total += int(cov.get("paired_trade_count", 0) or 0)
        skipped_total += int(cov.get("skipped_by_cap", 0) or 0)
```
(init `skipped_total = 0` next to `spot_total = paired_total = 0` at optimizer.py:537) and:
```python
        coverage={"spot_trade_count": spot_total, "paired_trade_count": paired_total,
                  "skipped_by_cap": skipped_total},
```

Attribution in `option_backtest._compute_metrics` (option_backtest.py:141-156) — add counts:

```python
        "option_trail_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_TRAIL_STOP")),
        "option_breakeven_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_BREAKEVEN_STOP")),
        "skipped_by_cap": int(sum(1 for t in trades if t.get("status") == "SKIPPED_DAILY_CAP")),
        "skipped_daily_loss": int(sum(1 for t in trades if t.get("skip_reason") == "DAILY_LOSS_HALT")),
        "skipped_daily_target": int(sum(1 for t in trades if t.get("skip_reason") == "DAILY_TARGET_HALT")),
        "skipped_max_trades": int(sum(1 for t in trades if t.get("skip_reason") == "MAX_TRADES_HALT")),
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_survival.py tests/test_option_backtest.py -q`
Expected: PASS. Existing survival coverage tests stay green (no `skipped_by_cap` → `eligible_ct == spot_ct`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/survival.py backend/app/optimizer.py backend/app/option_backtest.py tests/
git commit -m "feat(survival): cap-skips excluded from coverage denominator + exit-control attribution metrics"
```

---

### Task 7: Schemas — request models + attribution key constants

**Files:**
- Modify: `backend/app/schemas.py` (OptionBacktestReq:34-63; OptimizerStartReq:135-162; add new models + constants)
- Test: `tests/test_contract_exit_risk.py` (new)

- [ ] **Step 1: Write the failing contract test**

```python
# tests/test_contract_exit_risk.py  (repo-root tests/; run pytest from REPO ROOT)
from tests.contract_corpus import backend_api_text

API = backend_api_text()


def test_exit_controls_schema_pinned():
    assert "class ExitControlsReq" in API
    assert "class DailyCapsReq" in API
    assert "exit_controls" in API and "daily_caps" in API


def test_attribution_reason_constants_pinned():
    for name in ("OPTION_TRAIL_STOP", "OPTION_BREAKEVEN_STOP", "DAILY_LOSS_HALT"):
        assert name in API


def test_search_exit_controls_flag_pinned():
    assert "search_exit_controls" in API
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_contract_exit_risk.py -q`
Expected: FAIL (`class ExitControlsReq` not found).

- [ ] **Step 3: Implement — add models + constants to `schemas.py`**

```python
# add near the top-level constants of schemas.py (corpus-visible re-export of the
# exit_controls taxonomy so contract tests + the UI can pin the exact names)
OPTION_TRAIL_STOP = "OPTION_TRAIL_STOP"
OPTION_BREAKEVEN_STOP = "OPTION_BREAKEVEN_STOP"
DAILY_LOSS_HALT = "DAILY_LOSS_HALT"
DAILY_TARGET_HALT = "DAILY_TARGET_HALT"
MAX_TRADES_HALT = "MAX_TRADES_HALT"


class _BreakevenReq(BaseModel):
    trigger: float = 0.0
    lock: float = 0.0


class _TrailingReq(BaseModel):
    activation: float = 0.0
    distance: float = 0.0


class ExitControlsReq(BaseModel):
    """Premium trailing-stop + breakeven overlay (long options). Off by default."""
    enabled: bool = False
    unit: str = "pct"                 # "pct" (of entry premium) | "pts"
    breakeven: _BreakevenReq = Field(default_factory=_BreakevenReq)
    trailing: _TrailingReq = Field(default_factory=_TrailingReq)


class DailyCapsReq(BaseModel):
    """Soft per-IST-session caps (auto-resume next session). Omit a field to disable."""
    mode: str = "soft"
    loss: Optional[float] = None      # ₹ realized loss (positive)
    target: Optional[float] = None    # ₹ realized profit (positive)
    max_trades: Optional[int] = None
```

Add to `OptionBacktestReq` (after sizing_config at schemas.py:63):

```python
    exit_controls: Optional[ExitControlsReq] = None
    daily_caps: Optional[DailyCapsReq] = None
```

Add to `OptimizerStartReq` (after `rerank_diversity` at schemas.py:160):

```python
    # Commit 2: search a bounded grid of exit/cap configs per surviving finalist.
    search_exit_controls: bool = False
```

> The optimizer reads `exit_controls`/`daily_caps` from inside `option_config` (a free dict), matching how the other option params travel; the typed models above document + pin the shape and are reused by the backtest path.

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_contract_exit_risk.py -q && python -c "import app.schemas"`
Expected: PASS + clean import.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_contract_exit_risk.py
git commit -m "feat(schemas): ExitControlsReq/DailyCapsReq + reason constants + search_exit_controls"
```

---

### Task 8: Routers — validation 400s + no SKIPPED leak

**Files:**
- Modify: `backend/app/routers/research.py` (optimize_start:417-441); `backend/app/runtime.py` (`_run_paired_option_backtest`, sim call at :577 — forward + validate + segregate here); `backend/app/routers/deployments.py` (deployment create)
- Test: `tests/test_contract_exit_risk.py`

- [ ] **Step 1: Write the failing contract test**

```python
# append to tests/test_contract_exit_risk.py
def test_routers_call_exit_risk_validator():
    assert "validate_exit_risk_config" in API


def test_response_filters_skipped_daily_cap():
    # the option-backtest response must not surface SKIPPED_DAILY_CAP rows in the
    # public trades list (segregated or filtered)
    assert "SKIPPED_DAILY_CAP" in API  # referenced at the response boundary
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_contract_exit_risk.py -q -k validator`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `research.py optimize_start` (after the survival validation block, research.py:439), add:

```python
    oc = req.option_config or {}
    if oc.get("exit_controls") or oc.get("daily_caps"):
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            oc.get("exit_controls"), oc.get("daily_caps"),
            costs_on=bool(req.costs_enabled),
            option_exec_on=(req.evaluation_mode == "option_rerank"))
        if errs:
            raise HTTPException(400, "; ".join(errs))
```

The plain-backtest sim call is in **`runtime.py` `_run_paired_option_backtest`** (the call is at **runtime.py:577**, with `config = req.option_backtest`), NOT research.py — research.py only calls that wrapper. `runtime.py` is in the contract corpus, so the validation is still corpus-visible. Before the sim call (import `HTTPException` if not already):

```python
    if config.exit_controls or config.daily_caps:
        from app.exit_controls import validate_exit_risk_config
        errs = validate_exit_risk_config(
            config.exit_controls.model_dump() if config.exit_controls else None,
            config.daily_caps.model_dump() if config.daily_caps else None,
            costs_on=bool((config.cost_config or {}).get("enabled")),
            option_exec_on=(config.exit_mode == "option_levels"))
        if errs:
            raise HTTPException(400, "; ".join(errs))
```

Forward into the `simulate_paired_option_trades(...)` call at runtime.py:577: `exit_controls=config.exit_controls.model_dump() if config.exit_controls else None, daily_caps=config.daily_caps.model_dump() if config.daily_caps else None,`.

Segregate SKIPPED rows out of the returned `result` (in `runtime.py`, before it is handed back / embedded as `option_result` at research.py:226/294) so they never ride the public `trades` list:

```python
    _trades = result.get("trades") or []
    result["skipped_trades"] = [t for t in _trades if t.get("status") == "SKIPPED_DAILY_CAP"]
    result["trades"] = [t for t in _trades if t.get("status") != "SKIPPED_DAILY_CAP"]
```

In `deployments.py` create handler, validate the deployment overlay (cost flag = `friction.costs.enabled`):

```python
    risk = req.risk or {}
    if risk.get("exit_controls") or risk.get("daily_caps"):
        from app.exit_controls import validate_exit_risk_config
        costs_on = bool(((req.friction or {}).get("costs") or {}).get("enabled"))
        errs = validate_exit_risk_config(
            risk.get("exit_controls"), risk.get("daily_caps"),
            costs_on=costs_on, option_exec_on=True)  # deployments always pair options live
        if errs:
            raise HTTPException(400, "; ".join(errs))
```

- [ ] **Step 4: Run — expect pass + corpus green**

Run: `python -m pytest tests/test_contract_exit_risk.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/research.py backend/app/routers/deployments.py tests/test_contract_exit_risk.py
git commit -m "feat(routers): exit/risk validation 400s (per-path costs+option-exec) + segregate SKIPPED rows"
```

---

### Task 9: Optimizer — forward overlay + persist into saved best

**Files:**
- Modify: `backend/app/optimizer.py` (`_survival_eval_oos`:554-568; `_option_rerank`:696-705; save block:1006-1019); `backend/app/wfo.py` (sim call at :453 — forward overlay from option_cfg); `backend/app/preset_execution.py` (carry `exit_controls`/`daily_caps` into the execution block)
- Verify: `python -m py_compile` + running-stack e2e (optimizer.py imports optuna — NOT host-importable, so no pytest). `wfo.py`/`preset_execution.py` ARE host-importable — add a small host test for the preset carry.

- [ ] **Step 1: Forward the overlay at both finalist call sites**

In `_option_rerank` read the overlay from `option_cfg` (near optimizer.py:607-613):

```python
    exit_controls = option_cfg.get("exit_controls")
    daily_caps = option_cfg.get("daily_caps")
```
and pass into the sim call (optimizer.py:697-705): add `exit_controls=exit_controls, daily_caps=daily_caps,`.

In `_survival_eval_oos` (optimizer.py:554-568) add `exit_controls=option_cfg.get("exit_controls"), daily_caps=option_cfg.get("daily_caps"),` to the `simulate_paired_option_trades` call.

- [ ] **Step 2: Persist the overlay into the saved best**

In the survivor save block (optimizer.py:1006-1019), add the overlay config so the preset/deployment carries it. After `best = survivors[0]`, include it in `best_so_far["metrics"]` and a top-level field:

```python
                    best_so_far["exit_controls"] = option_cfg.get("exit_controls")
                    best_so_far["daily_caps"] = option_cfg.get("daily_caps")
```

(Also surface `survival_summary` already present.) Ensure the saved best backtest doc / "apply as preset" payload includes `option_config.exit_controls`/`daily_caps` — they already live in `payload["option_config"]`, which is persisted with the job `config`; confirm the preset builder reads them.

- [ ] **Step 3: Verify compile + e2e**

Run: `cd backend && python -m py_compile app/optimizer.py`
Expected: no output (success).

Running-stack e2e (after `docker compose up -d --build backend`): start an `option_rerank` optimization with `option_config.daily_caps.max_trades=2` + `exit_controls.enabled` and confirm via `GET /api/optimize/jobs/{id}` that finalists show overlay attribution / fewer paired trades than without caps (and survival still scores).

- [ ] **Step 4: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): forward exit_controls/daily_caps into finalist sims + persist into saved best"
```

---

### Task 10: Live — trail/breakeven ratchet in the mark loop

**Files:**
- Modify: `backend/app/paper_auto.py` (`build_auto_trade`:279-333 seed running_max; `mark_open_deployment_trades`:499-565 ratchet)
- Test: `tests/test_paper_auto.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_paper_auto.py
import asyncio
from app.paper_auto import mark_open_deployment_trades


class _FakeCursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return list(self._docs)


class _FakeTrades:
    def __init__(self, docs): self.docs = docs; self.replaced = []
    def find(self, *a, **k): return _FakeCursor([d for d in self.docs if d.get("status") == "OPEN"])
    async def replace_one(self, flt, doc, upsert=False):
        self.replaced.append(doc)
        for i, d in enumerate(self.docs):
            if d.get("id") == flt.get("id"):
                self.docs[i] = doc            # next mark cycle sees the update
        class R: matched_count = 1
        return R()


class _FakeDB:
    def __init__(self, trades): self.paper_trades = _FakeTrades(trades)
    @property
    def signals(self):
        class _S:
            async def find_one(self, *a, **k): return None
            async def replace_one(self, *a, **k): return None
        return _S()


def test_live_trail_ratchets_stop_up_over_two_marks():
    # Prior-running-max design (parity with the sim): the tick that sets a new peak
    # does NOT stop itself; the trail ratchets on the NEXT cycle off the prior max.
    trade = {"id": "t1", "status": "OPEN", "instrument_key": "OPT|1",
             "entry_price": 100.0, "quantity": 75, "running_max_premium": 100.0,
             "risk": {"stop_price": 80.0, "target_price": None},
             "exit_controls": {"enabled": True, "unit": "pct",
                               "trailing": {"activation": 0.10, "distance": 0.25}}}
    db = _FakeDB([trade])
    loop = asyncio.get_event_loop()
    # Cycle 1: tick 200. eff uses PRIOR running_max=100 -> activation 110 not reached ->
    #          stop stays 80; running_max advances to 200.
    ticks = {"OPT|1": {"last_price": 200.0}}
    loop.run_until_complete(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: ticks.get(k)))
    after1 = db.paper_trades.docs[0]
    assert after1["running_max_premium"] == 200.0
    assert after1["risk"]["stop_price"] == 80.0
    assert after1["status"] == "OPEN"
    # Cycle 2: tick 160. eff uses PRIOR running_max=200 -> trail = 200*0.75 = 150 (raised);
    #          160 > 150 so no close.
    ticks = {"OPT|1": {"last_price": 160.0}}
    loop.run_until_complete(mark_open_deployment_trades(db, latest_tick_lookup=lambda k: ticks.get(k)))
    after2 = db.paper_trades.docs[0]
    assert after2["risk"]["stop_price"] == 150.0
    assert after2["status"] == "OPEN"
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_paper_auto.py -q -k trail`
Expected: FAIL (no ratchet; stop stays 80 / no `running_max_premium` write).

- [ ] **Step 3: Implement**

In `build_auto_trade` (paper_auto.py:331), seed the running max + carry the overlay config:

```python
    trade["running_max_premium"] = float(fill_entry)
    ec = (deployment.get("risk") or {}).get("exit_controls")
    if ec:
        trade["exit_controls"] = ec
```

In `mark_open_deployment_trades`, inside the per-trade loop BEFORE step-1 mark (paper_auto.py:508), ratchet the stop from the PRIOR running max (look-ahead parity with the sim):

```python
            # 0. Trail/breakeven ratchet (overlay): raise the stored stop from the
            #    PRIOR running-max premium, then (after no-close) advance the max.
            ec_raw = trade.get("exit_controls")
            tick_now = option_price
            if ec_raw and tick_now is not None:
                from app.exit_controls import ExitControlsConfig, effective_premium_stop
                ec_cfg = ExitControlsConfig.from_dict(ec_raw)
                if ec_cfg.enabled:
                    rmax_prev = float(trade.get("running_max_premium") or trade.get("entry_price") or 0.0)
                    base_stop = (trade.get("risk") or {}).get("stop_price")
                    eff = effective_premium_stop(entry=float(trade.get("entry_price") or 0.0),
                                                 running_max=rmax_prev, base_stop=base_stop, cfg=ec_cfg)
                    if eff is not None and (base_stop is None or eff > float(base_stop)):
                        trade.setdefault("risk", {})["stop_price"] = round(float(eff), 2)
                        updated = trade  # carry the raised stop into the mark write
```

After the existing step-1 mark and IF the trade is still OPEN, advance the running max in the doc that gets written (near paper_auto.py:520, before the `replace_one`):

```python
            if str(updated.get("status") or "").upper() == "OPEN" and option_price is not None:
                prev = float(updated.get("running_max_premium") or updated.get("entry_price") or 0.0)
                updated["running_max_premium"] = max(prev, float(option_price))
                wrote = True
```

> The raised stop + new `running_max_premium` ride the single `replace_one({id, status:"OPEN"})` already at paper_auto.py:564 — no extra write. Because `mark_trade_to_market(auto_close_on_risk=True)` reads `risk.stop_price` (paper_trading.py:180-183), raising it first makes the close fire at the ratcheted stop.

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_paper_auto.py -q`
Expected: PASS (existing + new). Non-overlay trades unchanged (`exit_controls` absent → block skipped).

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_auto.py tests/test_paper_auto.py
git commit -m "feat(paper-auto): live premium trail/breakeven ratchet (prior running-max, single mark write)"
```

---

### Task 11: Live — soft daily governor entry gate

**Files:**
- Modify: `backend/app/deployment_kill_switch.py` (new `check_soft_daily_governor`); `backend/app/paper_auto.py` (`auto_paper_trade_for_signal`:336-373 gate)
- Test: `tests/test_deployment_kill_switch.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_deployment_kill_switch.py
import asyncio
from app.deployment_kill_switch import check_soft_daily_governor


# NOTE: this file already defines _DB/_Coll/_Cur for the kill-switch tests — use
# DISTINCT names (_Gov*) so we don't rebind them and break the existing wrapper tests.
class _GovCur:
    def __init__(self, docs): self._docs = docs
    def sort(self, *a, **k): return self
    async def to_list(self, length=None): return list(self._docs)


class _GovColl:
    def __init__(self, docs): self._docs = docs
    def find(self, flt, *a, **k):
        return _GovCur([d for d in self._docs if d.get("deployment_id") == flt.get("deployment_id")])


class _GovDB:
    def __init__(self, docs): self.paper_trades = _GovColl(docs)


def test_soft_governor_halts_on_max_trades_including_open():
    # 1 closed + 1 open entered today; max_trades=2 -> halt
    today = "2026-06-15"
    ts = 1_718_400_000_000  # an IST timestamp on 2026-06-15
    docs = [
        {"deployment_id": "d1", "status": "CLOSED", "created_at": "2026-06-15T04:00:00+00:00", "realized_pnl": -500.0},
        {"deployment_id": "d1", "status": "OPEN", "created_at": "2026-06-15T05:00:00+00:00"},
    ]
    dep = {"id": "d1", "mode": "paper", "risk": {"daily_caps": {"max_trades": 2}}}
    d = asyncio.get_event_loop().run_until_complete(
        check_soft_daily_governor(_GovDB(docs), dep, today_ist=today))
    assert d["halt"] and d["reason"] == "MAX_TRADES_HALT"
```

- [ ] **Step 2: Run — expect failure**

Run: `python -m pytest tests/test_deployment_kill_switch.py -q -k soft_governor`
Expected: FAIL (`cannot import name 'check_soft_daily_governor'`).

- [ ] **Step 3: Implement**

Add to `deployment_kill_switch.py`:

```python
async def check_soft_daily_governor(db, deployment, *, today_ist=None):
    """Entry-session soft governor: halt NEW entries when today's (by ENTRY date)
    realized cum-extremum trips loss/target or the entry count reaches max_trades.
    Stateless (auto-resets next session). Blocks entries only; never pauses."""
    from app.exit_controls import DailyCapsConfig, daily_governor_decision
    risk = dict(deployment.get("risk") or {})
    caps = DailyCapsConfig.from_dict(risk.get("daily_caps"))
    clear = {"halt": False, "reason": None}
    if str(deployment.get("mode") or "").lower() != "paper" or not caps.active:
        return clear
    dep_id = str(deployment.get("id") or "")
    today = today_ist or datetime.now(IST).date().isoformat()
    rows = await db.paper_trades.find(
        {"deployment_id": dep_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(length=None)
    entered_today = [t for t in rows if _ist_date(t.get("created_at")) == today]
    entry_count = len(entered_today)
    cum = cmin = cmax = 0.0
    for t in entered_today:
        if str(t.get("status") or "").upper() == "CLOSED":
            cum += _float(t.get("realized_pnl"))
            cmin = min(cmin, cum)
            cmax = max(cmax, cum)
    decision = daily_governor_decision(realized_cum_min=cmin, realized_cum_max=cmax,
                                        entry_count=entry_count, cfg=caps)
    return decision
```

In `paper_auto.auto_paper_trade_for_signal`, gate BEFORE the claim (paper_auto.py:372, after the `auto_paper_enabled`/CONFIRMED/blocked checks) — hard pause already short-circuits upstream in the evaluator, so the soft governor only adds entry-blocking:

```python
    from app.deployment_kill_switch import check_soft_daily_governor
    gov = await check_soft_daily_governor(db, deployment)
    if gov.get("halt"):
        return {"created": False, "reason": f"daily_cap:{gov.get('reason')}"}
```

- [ ] **Step 4: Run — expect pass**

Run: `python -m pytest tests/test_deployment_kill_switch.py tests/test_paper_auto.py -q`
Expected: PASS. Existing kill-switch tests stay green (new function is additive).

- [ ] **Step 5: Commit**

```bash
git add backend/app/deployment_kill_switch.py backend/app/paper_auto.py tests/
git commit -m "feat(live): soft entry-session daily governor (sticky, OPEN+CLOSED entries, auto-resume)"
```

---

### Task 12: Frontend — deploy wizard panel + results attribution

**Files:**
- Modify: deploy wizard component (grep `auto_paper_stop_pct` in `frontend/src` to find it); `frontend/src/components/backtest/PerformanceOverview.jsx`
- Test: contract test (testid pins) in `tests/` via the frontend corpus, or a frontend smoke

- [ ] **Step 1: Add the Exit/Risk panel** (kebab-case testids per the conventions bible): inputs for `exit_controls.enabled`, `unit`, `breakeven.trigger/lock`, `trailing.activation/distance`, `daily_caps.loss/target/max_trades`. Mirror the existing friction/kill-switch panel markup. Gate the ₹-cap inputs behind "costs enabled" with the same helper text as the 400.

- [ ] **Step 2: Results** — in `PerformanceOverview.jsx` render the attribution block (`option_trail_exits`, `option_breakeven_exits`, `skipped_by_cap` + per-reason) from the run doc metrics.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: compiles (1 pre-existing exhaustive-deps warning only).

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): deploy-wizard exit/risk panel + backtest exit-control attribution"
```

---

### Task 13: Docs + full verification (Commit 1 close)

**Files:** `CHANGELOG.md`, `docs/HANDOFF.md`

- [ ] **Step 1:** Add a CHANGELOG 0.42.x entry (overlay summary + the audit-driven parity model). Update HANDOFF §14 (Piece 2 Commit 1 DONE; Commit 2 next). One batched pass.

- [ ] **Step 2: Full suite + stack**

Run:
```bash
python -m pytest tests -q                  # all green (612 + new), run from REPO ROOT
cd frontend && npm run build && cd ..      # clean
docker compose up -d --build && docker compose ps
curl -s localhost:8001/api/health          # {"db":"ok"}
```
Running-stack smoke: a backtest with `exit_controls.enabled` + `daily_caps` shows trail/breakeven exits + SKIPPED counts; force a live trail-stop (open a paper trade, push a tick up then down) and confirm the stop ratchets and closes at the trailed level; confirm a daily-loss halt blocks new entries and auto-resumes next session.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md docs/HANDOFF.md
git commit -m "docs: exit/risk controls Commit 1 (overlay enforce+evaluate) — CHANGELOG + HANDOFF"
```

---

# COMMIT 2 — Bounded finalist-grid search

### Task 14: Optimizer — grid search over exit configs per survivor

**Files:**
- Modify: `backend/app/optimizer.py` (survival block 985-1019); reuse `_grid_combinations` sub-sampling pattern (optimizer.py:206-244)
- Verify: `py_compile` + e2e (no pytest — optuna)

- [ ] **Step 1:** Behind `payload.get("search_exit_controls")`, after a finalist survives, enumerate a **bounded** grid of exit/cap configs (fixed Cartesian order over the user's bounds; hard `|grid|` ceiling, e.g. 12; seeded sub-sample via the existing `np.random.seed(42)` pattern if `len(survivors) * n_folds * |grid|` exceeds a budget). For each grid point, re-run `_survival_eval_oos` with that overlay merged into `option_cfg`; keep the best-surviving config per finalist; attach `chosen_exit_controls` to the finalist. Never evaluate a grid on a non-survivor (so it can't resurrect a disqualified candidate).

```python
            if survival.enabled and ranked and payload.get("search_exit_controls"):
                grid = _exit_control_grid(option_cfg)          # bounded, fixed order
                for r in [r for r in ranked if r.get("survival", {}).get("survived")]:
                    best_cfg, best_v = None, None
                    for gc in grid:
                        oc2 = {**option_cfg, **gc}
                        v = await _survival_eval_oos(strategy, get_enriched(strategy.merged_params(r["params"])),
                                                     strategy.merged_params(r["params"]), rerank_contracts,
                                                     rerank_candles, instrument, costs, pretrade, oc2, survival)
                        if v.get("survived") and (best_v is None or (v.get("calmar") or -1e9) > best_v):
                            best_v, best_cfg = (v.get("calmar") or -1e9), gc
                    if best_cfg:
                        r["chosen_exit_controls"] = best_cfg
```

Add `_exit_control_grid(option_cfg)` returning a small fixed-order list of `{"exit_controls": {...}}` dicts from the user-provided bounds (default a 2×2 trail-distance × breakeven-trigger grid; ceiling 12).

- [ ] **Step 2: Verify**

Run: `cd backend && python -m py_compile app/optimizer.py`
Expected: success. E2e: an `option_rerank` + `survival` + `search_exit_controls=true` job attaches `chosen_exit_controls` to survivors and never to disqualified finalists.

- [ ] **Step 3: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): bounded finalist-grid search over exit/cap configs (gated, survivors only)"
```

---

### Task 15: Optimizer UI + docs (Commit 2 close)

**Files:** `frontend/src/pages/Optimizer.jsx`, `CHANGELOG.md`, `docs/HANDOFF.md`

- [ ] **Step 1:** Optimizer setup: a `search_exit_controls` toggle + grid bounds; results: show each survivor's `chosen_exit_controls` next to the survivability badge.
- [ ] **Step 2:** `npm run build` clean.
- [ ] **Step 3:** CHANGELOG + HANDOFF batched update (Commit 2 DONE; Piece 2 complete).
- [ ] **Step 4: Commit**

```bash
git add frontend/src CHANGELOG.md docs/HANDOFF.md
git commit -m "feat(optimizer-ui): exit-control search toggle + chosen-config display; docs"
```

---

## Self-Review

**Spec coverage:** §5.1 exit controls → Task 1; §5.2/§5.2a look-ahead+gap → Task 4; §5.3 precedence/taxonomy → Tasks 4-6; §5.4 sim governor+coverage → Tasks 5-6; §5.5 live ratchet+governor → Tasks 10-11; §5.6 optimizer forward+save+search → Tasks 9, 14; §6 config+validation → Tasks 7-8; §7 parity (decider) → Tasks 1-2 deciders + the parity goldens (add to Task 4/10 test files); §9 testing → each task; §10 UI → Tasks 12, 15. §13 resolutions: F1 (Task 9 kwargs + Task 4 conduit test), F2/F3 (Task 11 entry-session+OPEN count), F4 (Task 10 prior-max ordering), F5 (decider parity in Tasks 1/10), D1/D2 (Task 4 gap-clamp), G1/G2 (Tasks 6, 8), G3 (Task 2 extremum), G5 (Task 9 persist), G7 (Task 14 bounds).

**Parity goldens + conduit:** see Post-verification corrections #3 (a `tests/test_execution_policy.py` golden asserting `effective_premium_stop` + `daily_governor_decision` return identical decider results for the same sim-bar vs live-tick inputs — note Task 4's walk and Task 10's ratchet share the expected 150.0 trail from running_max 200, distance 0.25) and #4 (the host conduit test through `simulate_paired_option_trades`).

**Placeholder scan:** none — every code step shows the code. The one prose-described UI task (12/15) is markup mirroring an existing panel; acceptable (no new logic).

**Type consistency:** `ExitControlsConfig`/`DailyCapsConfig`/`effective_premium_stop`/`daily_governor_decision`/`validate_exit_risk_config`/`stop_fill_price` names + signatures are identical across Tasks 1-3 (definition) and Tasks 4-11 (use). `running_max_premium` (trade field), `skipped_by_cap` (coverage + metrics), `SKIPPED_DAILY_CAP` (status), and the five reason constants are used consistently. The `daily_governor_decision` kwargs (`realized_cum_min`, `realized_cum_max`, `entry_count`) match between Task 2, Task 5 (sim), and Task 11 (live).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-15-exit-risk-controls.md`.
