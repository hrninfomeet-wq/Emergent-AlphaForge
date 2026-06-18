# Scenario-Adaptive Framework (proof-first) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a general, reusable scenario-adaptive layer and prove the discovered opening-range regime edge survives as option-₹ — gated, proof-first.

**Architecture:** Four thin layers (pure `scenario_classifier` + causal `orb_width` feature → routing base → `exit_plan` dispatch) + one new `level_exit` primitive (delegating to `intrabar_exit`), exercised end-to-end by the minimal `opening_range_regime_router` (ORR) strategy, validated as an option buyer through the existing `option_rerank`+survival gauntlet. STOP if the gate fails.

**Tech Stack:** Python 3.11, pandas, FastAPI, Optuna; pytest. Spec: `docs/superpowers/specs/2026-06-18-scenario-adaptive-framework-design.md`. **Branch:** `feat/scenario-adaptive-framework`.

**Standing constraints:** Host tests must NOT import `server`/`optimizer`/`runtime`/`paper_auto`. Run from repo root: `python -m pytest tests/...`. The P4 gate runs on the Docker stack. Leave the user's uncommitted files untouched. No push without approval. **P5 (optimizer option-₹ deepening) and P6 (generalization) are DEFERRED behind the P4 gate — not in this plan.**

---

## File Structure
- Create `backend/app/scenario_classifier.py` — pure `classify_scenario(...)` (P1).
- Create `backend/app/scenarios.py` — `exit_plan(scenario, ctx)` dispatcher (P2).
- Create `backend/app/strategies/scenario_routing_base.py` — `ScenarioRoutedStrategyBase` (P2).
- Create `backend/app/strategies/builtin/opening_range_regime_router.py` — ORR proof strategy (P4).
- Modify `backend/app/indicator_groups.py` — `_compute_orb_width` group keyed on `or_minutes` (P1).
- Modify `backend/app/strategies/base.py` — `Signal` optional fields (P2).
- Modify `backend/app/backtest.py` — `Trade` fields + `_clean_trade_dict` + level-target branch (P2/P3).
- Create `backend/app/exit_controls_level.py` — `level_exit_decision` (P3).
- Tests: `tests/test_scenario_classifier.py`, `tests/test_scenario_adaptive_exits.py`, extend `tests/test_indicator_equivalence.py` + `tests/test_backtest_characterization.py`.

---

## PHASE 1 — Classification + causal feature (pure, ships dark)

### Task 1: `orb_width` feature as a keyed indicator group

**Files:** Modify `backend/app/indicator_groups.py`; Test: `tests/test_indicator_equivalence.py`.

- [ ] **Step 1: Add the compute fn** (after `_compute_cpr`, ~line 199). It mirrors `_compute_cpr`'s session-windowed pattern; `or_minutes` defaults to 30 (09:15→09:45). Causal: `_prior` is the prior session's width (`shift(1)` across sessions, always available); `_partial` is the current session's width once `or_minutes` have elapsed (else `NaN`).

```python
def _compute_orb_width(df: pd.DataFrame, p: dict) -> Dict[str, pd.Series]:
    """Opening-range width as % of the prior-day pivot (cpr_p), scale-free.
    orb_width_pct_partial: current session's 09:15..(09:15+or_minutes) high-low /cpr_p,
      NaN until or_minutes bars have elapsed (no look-ahead).
    orb_width_pct_prior: the PRIOR completed session's value (shift across sessions),
      always available at session start.
    Reuses session_date + cpr_p (already computed by the time/cpr groups)."""
    or_minutes = int(p.get("or_minutes", 30))
    import numpy as np
    partial = pd.Series(np.nan, index=df.index, dtype="float64")
    per_session = {}
    for sdate, g in df.groupby("session_date", sort=False):
        start = g["dt"].iloc[0]
        cutoff = start + pd.Timedelta(minutes=or_minutes)
        win = g[g["dt"] < cutoff]
        if len(win):
            hi, lo = float(win["high"].max()), float(win["low"].min())
            piv = float(g["cpr_p"].iloc[0]) if "cpr_p" in g and len(g) else 0.0
            w = 100.0 * (hi - lo) / piv if piv else np.nan
            per_session[sdate] = w
            # partial known only from the cutoff bar onward (causal)
            partial.loc[g.index[g["dt"] >= cutoff]] = w
    order = list(dict.fromkeys(df["session_date"].tolist()))
    prior_map = {order[i]: per_session.get(order[i-1], np.nan) for i in range(len(order))}
    prior = df["session_date"].map(lambda s: prior_map.get(s, np.nan)).astype("float64")
    return {"orb_width_pct_partial": partial, "orb_width_pct_prior": prior}
```

- [ ] **Step 2: Register the group** in `GROUPS` (indicator_groups.py:218-240). Insert it AFTER `cpr` (it reads `cpr_p` from the cpr group + `session_date`/`dt` from the time group) and BEFORE `regime`. Keyed on `or_minutes` (NOT param-independent — the width depends on it):

```python
    IndicatorGroup("cpr", ("cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window"), _compute_cpr),
    IndicatorGroup("orb_width", ("or_minutes",), _compute_orb_width),
    IndicatorGroup("tod_tradeable", ("tod_lookback_sessions", "tod_min_atr_frac", "atr_length"), _compute_tod_tradeable),
```

**NOTE:** This adds columns to `enrich_with_cache` (the optimizer/WFO path) but NOT to `precompute_all_indicators` (the golden reference). The byte-identical harness compares them — so `precompute_all_indicators` must ALSO emit these columns, OR the harness's `_enrich_ref` must add them. Choose: **add the same two columns to `precompute_all_indicators`** (indicators.py, after the cpr block) by calling an equivalent inline computation, so golden == memoized. (Mirror exactly; the harness proves equality.)

- [ ] **Step 3: Extend the equivalence harness sweep.** In `tests/test_indicator_equivalence.py`, add `{"or_minutes": 20}` and `{"or_minutes": 45}` to `_PARAM_SWEEP`, and add `"orb_width_pct_prior"` to the expected-columns list in `test_expected_columns_present`.

- [ ] **Step 4: Run the harness** — `python -m pytest tests/test_indicator_equivalence.py -v` → all pass (memoized == golden incl. the new columns, across the `or_minutes` sweep).

- [ ] **Step 5: Full suite** `python -m pytest tests/ -q` → green. **Commit** `backend/app/indicator_groups.py backend/app/indicators.py tests/test_indicator_equivalence.py` with `feat(indicators): causal orb_width_pct feature (keyed group + golden parity)`.

### Task 2: `scenario_classifier.py` (pure, host-TDD)

**Files:** Create `backend/app/scenario_classifier.py`; Test: `tests/test_scenario_classifier.py`.

- [ ] **Step 1: Write the failing tests** (truth table + look-ahead guard + regime precondition + the column-swap guard):

```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenario_classifier import classify_scenario, SCENARIOS

def test_wide_open_is_volatile_fade():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.9, day_type="RANGE",
                             nr7=False, atr_ratio=1.2) == "VOLATILE_FADE"

def test_narrow_open_is_trend_continuation():
    assert classify_scenario(regime="TREND", orb_width_pct=0.2, day_type="TREND",
                             nr7=True, atr_ratio=1.0) == "TREND_CONTINUATION"

def test_mid_chop_regime_is_chop():
    assert classify_scenario(regime="CHOP", orb_width_pct=0.45, day_type="NEUTRAL",
                             nr7=False, atr_ratio=1.0) == "CHOP"

def test_none_when_orb_width_missing():
    assert classify_scenario(regime="TREND", orb_width_pct=None, day_type="TREND",
                             nr7=False, atr_ratio=1.0) == "NONE"

def test_column_swap_guard_keys_off_orb_not_cpr():
    # Quiet OPEN today (narrow orb) but WIDE prior pivot must classify on the OPEN -> TREND_CONTINUATION,
    # NOT VOLATILE_FADE. classify_scenario takes orb_width_pct only; there is NO cpr_width_pct param,
    # so a swap is structurally impossible — this test pins that contract.
    import inspect
    params = inspect.signature(classify_scenario).parameters
    assert "orb_width_pct" in params and "cpr_width_pct" not in params

def test_thresholds_overridable():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.5, day_type="RANGE", nr7=False,
                             atr_ratio=1.0, narrow_thr=0.55, wide_thr=0.45) == "VOLATILE_FADE"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`). `python -m pytest tests/test_scenario_classifier.py -v`.

- [ ] **Step 3: Implement** `backend/app/scenario_classifier.py`:

```python
"""Pure market-scenario classifier. Re-combines ALREADY-computed columns only
(regime, today's opening-range width, day_type, nr7, atr/atr_avg ratio, optional
vix_bucket) -> a scenario string. NEVER re-derives adx/atr/chop/regime.
Discovered edge (NIFTY 2025-26): narrow opening range -> the drive CONTINUES
(trend-follow); wide opening range -> the drive FADES (toward the open)."""
from __future__ import annotations
from typing import Any, Optional

SCENARIOS = ("TREND_CONTINUATION", "VOLATILE_FADE", "CHOP", "NONE")
_CHOP_REGIMES = ("CHOP", "MIXED", "VOLATILE_CHOP")


def classify_scenario(*, regime: Any, orb_width_pct: Optional[float], day_type: Any,
                      nr7: Any, atr_ratio: Any, vix_bucket: str = "UNKNOWN",
                      narrow_thr: float = 0.30, wide_thr: float = 0.60) -> str:
    """`orb_width_pct` = TODAY's opening-range width as % of pivot (the causal
    decision input). Thresholds are optimizable. Returns one of SCENARIOS."""
    try:
        w = None if orb_width_pct is None else float(orb_width_pct)
    except (TypeError, ValueError):
        w = None
    if w is None or w != w:  # None or NaN -> no decision
        return "NONE"
    if w >= wide_thr:
        return "VOLATILE_FADE"
    if w <= narrow_thr:
        return "TREND_CONTINUATION"
    if str(regime) in _CHOP_REGIMES:
        return "CHOP"
    return "NONE"
```

- [ ] **Step 4: Run — expect PASS.** `python -m pytest tests/test_scenario_classifier.py -v` → 6 passed.

- [ ] **Step 5: Commit** `backend/app/scenario_classifier.py tests/test_scenario_classifier.py` with `feat(scenario): pure market-scenario classifier (opening-range regime)`.

---

## PHASE 2 — Signal/Trade plumbing + routing base + exit dispatch

### Task 3: Signal/Trade fields + serialization (the T9 hazard)

**Files:** Modify `backend/app/strategies/base.py` (Signal), `backend/app/backtest.py` (Trade + `_clean_trade_dict` + entry snapshot); Test: extend `tests/test_backtest_characterization.py`.

- [ ] **Step 1: Add Signal fields** (base.py:23-24, after `spot_stop_pts`):

```python
    spot_target_pts: Optional[float] = None
    spot_stop_pts: Optional[float] = None
    scenario: Optional[str] = None
    spot_target_level: Optional[float] = None
    exit_mode: Optional[str] = None
```

- [ ] **Step 2: Add Trade fields** — read `backtest.py` Trade dataclass (~22-49); add `scenario: str = ''` and `spot_target_level: Optional[float] = None` next to the existing `regime`/`ist_time` fields.

- [ ] **Step 3: Snapshot at entry** — in the `Trade(...)` construction (~backtest.py:202-214) set `scenario=(sig.scenario or '')` and `spot_target_level=sig.spot_target_level` alongside the existing `regime=`/`ist_time=`.

- [ ] **Step 4: Pin serialization** — in `_clean_trade_dict` (~backtest.py:242-250), AFTER `d = asdict(t)` and the existing T9 pops, add `d.pop('spot_target_level', None)` (drop internal bookkeeping). LEAVE `scenario` in `d` (emit it like `regime`).

- [ ] **Step 5: Update the characterization golden** — in `tests/test_backtest_characterization.py`, every pinned trade dict now gains `"scenario": ""` (empty for non-routed strategies). Update the golden assertions to expect the `scenario` key (value `""`). Run `python -m pytest tests/test_backtest_characterization.py -v` → PASS (proves only the one new stable key was added, `spot_target_level` does NOT leak).

- [ ] **Step 6: Full suite** `python -m pytest tests/ -q` → green. **Commit** `backend/app/strategies/base.py backend/app/backtest.py tests/test_backtest_characterization.py` with `feat(backtest): Signal/Trade scenario + spot_target_level plumbing (emit scenario, drop level)`.

### Task 4: `scenarios.py` — `exit_plan` dispatcher (host-TDD)

**Files:** Create `backend/app/scenarios.py`; Test: `tests/test_scenario_adaptive_exits.py`.

- [ ] **Step 1: Write failing tests:**

```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenarios import exit_plan

def test_trend_continuation_is_let_run_target_no_level():
    p = exit_plan("TREND_CONTINUATION", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None
    assert p["spot_target_pts"] >= 90 and p["spot_stop_pts"] > 0 and p["trail"] is True

def test_volatile_fade_targets_the_open_level():
    p = exit_plan("VOLATILE_FADE", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] == 24000.0 and p["trail"] is False

def test_chop_is_small_scalp():
    p = exit_plan("CHOP", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None and p["spot_target_pts"] < 90

def test_none_returns_no_trade_plan():
    assert exit_plan("NONE", {"atr": 40.0, "open": 24000.0}, params={}) is None
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** `backend/app/scenarios.py`:

```python
"""Scenario -> exit plan dispatcher. Single source of per-scenario exit semantics.
Defaults encode the discovered edge; all magnitudes are OPTIMIZABLE via `params`.
Returns {spot_target_pts, spot_stop_pts, spot_target_level, trail, exit_mode} or
None (no trade for this scenario)."""
from __future__ import annotations
from typing import Any, Dict, Optional


def exit_plan(scenario: str, ctx: Dict[str, Any], *, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    atr = float(ctx.get("atr") or 0.0)
    open_px = ctx.get("open")
    if scenario == "TREND_CONTINUATION":
        return {"spot_target_pts": float(params.get("trend_target_atr", 4.0)) * atr,
                "spot_stop_pts": float(params.get("trend_stop_atr", 1.2)) * atr,
                "spot_target_level": None, "trail": True, "exit_mode": "spot_exit"}
    if scenario == "VOLATILE_FADE":
        return {"spot_target_pts": None, "spot_target_level": float(open_px) if open_px is not None else None,
                "spot_stop_pts": float(params.get("fade_stop_atr", 1.5)) * atr,
                "trail": False, "exit_mode": "spot_exit"}
    if scenario == "CHOP":
        return {"spot_target_pts": float(params.get("chop_target_atr", 1.0)) * atr,
                "spot_stop_pts": float(params.get("chop_stop_atr", 0.8)) * atr,
                "spot_target_level": None, "trail": False, "exit_mode": "spot_exit"}
    return None
```

- [ ] **Step 4: Run — PASS.** **Step 5: Commit** `backend/app/scenarios.py tests/test_scenario_adaptive_exits.py` with `feat(scenario): per-scenario exit_plan dispatcher (let-run / fade-to-open / scalp)`.

### Task 5: `scenario_routing_base.py` (the routing hook)

**Files:** Create `backend/app/strategies/scenario_routing_base.py`. (Mirrors `adaptive_base.py`.)

- [ ] **Step 1: Implement** — read `adaptive_base.py` (BASE_PARAMS, `__init_subclass__`, `evaluate`, `_time_ok`) as the template. The base classifies, routes to `exit_plan`, validates `scenarios_traded` at class-definition:

```python
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import pandas as pd
from app.strategies.base import StrategyBase, Signal
from app.scenario_classifier import classify_scenario, SCENARIOS
from app.scenarios import exit_plan

ROUTING_BASE_PARAMS: Dict[str, Any] = {
    "or_minutes": {"type": "int", "min": 10, "max": 45, "default": 30},
    "narrow_thr": {"type": "float", "min": 0.1, "max": 0.6, "default": 0.30},
    "wide_thr": {"type": "float", "min": 0.4, "max": 1.5, "default": 0.60},
    "entry_cutoff_hhmm": {"type": "str", "default": "14:00"},
}


class ScenarioRoutedStrategyBase(StrategyBase):
    supported_instruments = ["NIFTY", "SENSEX"]
    supported_modes = ["SCALP", "INTRADAY"]
    supported_timeframes = ["1m"]
    extra_params: Dict[str, Any] = {}
    scenarios_traded: Tuple[str, ...] = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls.parameter_schema = {**ROUTING_BASE_PARAMS, **getattr(cls, "extra_params", {})}
        bad = [s for s in getattr(cls, "scenarios_traded", ()) if s not in SCENARIOS]
        if bad:
            raise ValueError(f"{cls.__name__}.scenarios_traded has unknown scenarios: {bad}")

    def _route(self, row, prev, params, ctx) -> Tuple[str, int, List[str], List[str], str]:
        """Return (direction, score, reasons, blockers, scenario). Concrete strategies override."""
        raise NotImplementedError

    def evaluate(self, row, prev, params, ctx) -> Signal:
        t = str(row.get("ist_time") or "")
        if t and t >= str(params.get("entry_cutoff_hhmm", "14:00")):
            return Signal(direction="NONE", blockers=["time gate"])
        orbw = row.get("orb_width_pct_partial")
        atr = row.get("atr"); atr_avg = row.get("atr_avg")
        atr_ratio = (float(atr) / float(atr_avg)) if (atr and atr_avg) else 1.0
        scenario = classify_scenario(regime=row.get("regime"), orb_width_pct=orbw,
            day_type=row.get("day_type"), nr7=row.get("nr7"), atr_ratio=atr_ratio,
            narrow_thr=float(params.get("narrow_thr", 0.30)), wide_thr=float(params.get("wide_thr", 0.60)))
        if scenario not in self.scenarios_traded:
            return Signal(direction="NONE", scenario=scenario, blockers=[f"scenario {scenario} not traded"])
        direction, score, reasons, blockers, scen = self._route(row, prev, params, ctx)
        if direction not in ("CE", "PE"):
            return Signal(direction="NONE", score=int(score or 0), scenario=scen, reasons=reasons or [], blockers=blockers or [])
        plan = exit_plan(scen, {"atr": row.get("atr"), "open": ctx.get("session_open") if ctx else None}, params=params)
        if plan is None:
            return Signal(direction="NONE", scenario=scen, blockers=["no exit plan"])
        return Signal(direction=direction, score=int(score), scenario=scen, reasons=reasons or [],
                      blockers=list(blockers or []), spot_target_pts=plan["spot_target_pts"],
                      spot_stop_pts=plan["spot_stop_pts"], spot_target_level=plan["spot_target_level"],
                      exit_mode=plan["exit_mode"])
```

*(Note: `ctx['session_open']` — the routing base needs the session open for the fade level. VERIFY the backtest `ctx_global`/`ctx_local` exposes the session open; if not, derive from `history_df` at the session's first bar in `_route` and pass via the ctx the strategy already has. Resolve in this task before the level-exit P3 depends on it.)*

- [ ] **Step 2:** No host test for the base alone (it needs a concrete strategy); it is covered by the ORR characterization in P4. Verify import-safe: `python -c "import sys; sys.path.insert(0,'backend'); import app.strategies.scenario_routing_base"`.
- [ ] **Step 3: Commit** with `feat(strategies): ScenarioRoutedStrategyBase routing hook`.

---

## PHASE 3 — Level-based exit primitive

### Task 6: `level_exit_decision` + the backtest level-target branch + parity test

**Files:** Create `backend/app/exit_controls_level.py`; Modify `backend/app/backtest.py` (exit loop); Test: `tests/test_scenario_adaptive_exits.py` (extend).

- [ ] **Step 1: Implement the helper** — delegates to `intrabar_exit` with the absolute level as `target`:

```python
# backend/app/exit_controls_level.py
"""Absolute-price (level) exit target, resolved by the SAME intrabar rule as
delta targets so spot/option fills never drift. Used by VOLATILE_FADE
(fade back to the session OPEN)."""
from __future__ import annotations
from typing import Optional, Tuple
from app.exit_engine import intrabar_exit


def level_exit_decision(*, high: float, low: float, stop: Optional[float],
                        level_target: Optional[float], is_long: bool) -> Tuple[Optional[float], Optional[str]]:
    return intrabar_exit(high=high, low=low, stop=stop, target=level_target, is_long=is_long, stop_first=True)
```

- [ ] **Step 2: Wire the backtest branch** — read `backtest.py:140-156` (the delta-only target resolution). After `tgt_p` (the delta) is computed and `target = entry ± tgt_p`, add the parallel branch:

```python
    # ... after target = entry + tgt_p (long) / entry - tgt_p (short) ...
    if open_trade.spot_target_level is not None:
        target = float(open_trade.spot_target_level)   # absolute level (VOLATILE_FADE)
```

(The downstream `intrabar_exit(... target=target ...)` call is unchanged — it already takes an absolute `target`.)

- [ ] **Step 3: Parity test** — extend `tests/test_scenario_adaptive_exits.py`: a long trade with `spot_target_level` set must exit at the SAME bar/level that `intrabar_exit(target=level)` returns; assert `level_exit_decision` and `intrabar_exit` agree, and that a points-target and a level-target at the same absolute price produce identical fills. (Pure, host-testable.)

- [ ] **Step 4:** `python -m pytest tests/test_scenario_adaptive_exits.py tests/test_backtest_characterization.py -q` → green (the level branch is inert when `spot_target_level is None`, so existing characterization unchanged). Full suite green.
- [ ] **Step 5: Commit** `backend/app/exit_controls_level.py backend/app/backtest.py tests/test_scenario_adaptive_exits.py` with `feat(backtest): level-based exit target via intrabar_exit (fade-to-level)`.

---

## PHASE 4 — The ORR proof + the GATE

### Task 7: `opening_range_regime_router.py` (ORR)

**Files:** Create `backend/app/strategies/builtin/opening_range_regime_router.py`.

- [ ] **Step 1: Implement** — a concrete `ScenarioRoutedStrategyBase`. `_route` reads `orb_width_pct_partial` (available after `or_minutes`), the opening-drive direction (close vs session open), and routes: `TREND_CONTINUATION` → enter the drive direction; `VOLATILE_FADE` → enter OPPOSITE (fade toward open). Declares `scenarios_traded = ("TREND_CONTINUATION", "VOLATILE_FADE")` and `extra_params` (the per-scenario exit ATR multiples from `scenarios.exit_plan`).

```python
from __future__ import annotations
import pandas as pd
from app.strategies.scenario_routing_base import ScenarioRoutedStrategyBase

class OpeningRangeRegimeRouter(ScenarioRoutedStrategyBase):
    id = "opening_range_regime_router"
    name = "Opening-Range Regime Router"
    version = "1.0.0"
    description = "Routes on opening-range width: narrow->trend-follow the drive; wide->fade toward the open."
    scenarios_traded = ("TREND_CONTINUATION", "VOLATILE_FADE")
    extra_params = {
        "trend_target_atr": {"type": "float", "min": 2.0, "max": 8.0, "default": 4.0},
        "trend_stop_atr":   {"type": "float", "min": 0.5, "max": 2.0, "default": 1.2},
        "fade_stop_atr":    {"type": "float", "min": 0.5, "max": 3.0, "default": 1.5},
    }

    def _route(self, row, prev, params, ctx):
        for k in ("orb_width_pct_partial", "close", "regime"):
            if pd.isna(row.get(k)):
                return ("NONE", 0, [], ["warming up"], "NONE")
        open_px = ctx.get("session_open") if ctx else None
        if open_px is None:
            return ("NONE", 0, [], ["no session open"], "NONE")
        drive_up = float(row["close"]) >= float(open_px)
        # classifier (called by base) decides scenario; here we only set direction per scenario.
        # Re-derive scenario cheaply for direction routing (base re-validates):
        from app.scenario_classifier import classify_scenario
        atr, atr_avg = row.get("atr"), row.get("atr_avg")
        scen = classify_scenario(regime=row.get("regime"), orb_width_pct=row.get("orb_width_pct_partial"),
            day_type=row.get("day_type"), nr7=row.get("nr7"),
            atr_ratio=(float(atr)/float(atr_avg)) if (atr and atr_avg) else 1.0,
            narrow_thr=float(params.get("narrow_thr", 0.30)), wide_thr=float(params.get("wide_thr", 0.60)))
        if scen == "TREND_CONTINUATION":
            return ("CE" if drive_up else "PE", 60, ["narrow-open trend-follow"], [], scen)
        if scen == "VOLATILE_FADE":
            return ("PE" if drive_up else "CE", 60, ["wide-open fade-to-open"], [], scen)
        return ("NONE", 0, [], [f"scenario {scen}"], scen)
```

*(Resolve here: confirm `ctx['session_open']` is provided to `evaluate`/`_route` by the backtest loop; if absent, the base/strategy computes it from `history_df` at the session's first bar. This is the one ctx field the fade arm needs.)*

- [ ] **Step 2: Smoke + characterization** — add ORR to a host smoke test: enrich a fixture, run `run_backtest` with ORR, assert it produces trades with `scenario` in `{TREND_CONTINUATION, VOLATILE_FADE}` and that `VOLATILE_FADE` trades carry a non-None `spot_target_level`. Run; commit `backend/app/strategies/builtin/opening_range_regime_router.py` + the smoke test with `feat(strategies): opening_range_regime_router (ORR) proof strategy`.

### Task 8: The GATE (running Docker stack — controller-run)

**Files:** none (verification). This is the binary proof.

- [ ] **Step 1: Rebuild backend** so ORR is registered (auto-discover) + the level-exit/orb_width code is live; confirm health + `get_registry().get('opening_range_regime_router')` resolves.
- [ ] **Step 2: Run the gauntlet** — `POST /api/optimize/start` for `opening_range_regime_router`, NIFTY, the SOLID window (2025-10→2026-06), `evaluation_mode="option_rerank"`, `costs_enabled=true`, `option_config` (ATM, `cost_config.enabled=true`), `survival_config.enabled=true`. (Option mode REQUIRES `option_config` — fail-fast.)
- [ ] **Step 3: Read the verdict** — does the job produce **≥1 survivor** (passed the per-fold OOS survival filter with positive OOS `total_return_pct` on the stitched option-rupee curve), and does the promoted best run's `quality` clear `deployment_quality` (no fatal flags; deflated-Sharpe/coverage acceptable)? Record the survivor's full-window option net-₹ AND OOS net-₹.
- [ ] **Step 4: The binary decision.**
  - **If ≥1 option-₹ survivor that also passes the FULL-WINDOW option-₹ cross-check (per the edge-hunt lesson) → PROOF PASSES.** Report the survivor; proceed (in a NEW plan) to P5 (optimizer option-₹ deepening) + P6 (generalize the routing base to other strategies + live parity).
  - **If NO survivor (or OOS-positive but full-window-negative = fragile) → PROOF FAILS. STOP.** Report the evidence; do NOT build P5/P6. The discovered spot edge did not survive option costs — the honest, valuable negative result.
- [ ] **Step 5: Clean up** the gate's job/run artifacts; restore the backend to timing-off default.

---

## Completion

After P1-P3 land (host-green) and the P4 gate runs: **Use superpowers:finishing-a-development-branch** — verify host tests (`python -m pytest tests/...`), then present merge/PR/keep options. Do NOT push without explicit instruction. **The P4 result decides everything downstream:** pass → P5/P6 get their own spec+plan; fail → STOP with evidence and revisit the signal/structure.
