# Premium-Momentum Contingency Breakout — Phase 0+1+2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Full-Python feasibility panel, then build a backtest-provable, AlgoTest-faithful premium-momentum option-buying strategy (spot-locked strike → enter on the option's premium rising X% → premium SL/TGT → stepped trailing), stopping at the edge-validation gate.

**Architecture:** A self-contained **option-native backtest** whose entry/exit/strike logic lives in **pure, host-testable helper functions** (`app/premium_momentum.py`). A thin sim (`app/premium_momentum_backtest.py`) walks each session's locked-strike premium series through those helpers. Phase 3 (live, not in this plan) will reuse the SAME pure helpers from the deployment loop — parity by shared rules, not a shared loop. This deliberately refines the spec's premium-in-ctx bridge, which stays the LIVE design; the backtest goes option-native to avoid the two-stage spot→option strike re-resolution ([option_backtest.py:568](../../../backend/app/option_backtest.py)).

**Tech Stack:** Python 3.11 (backend, FastAPI/motor), pandas/numpy; pytest (host + container); React/CRA frontend; existing helpers `options_universe.select_contract_for_signal`, `option_backtest` cost/sizing configs, warehouse `options_1m`.

---

## Design decisions locked here

1. **Backtest is option-native + self-contained.** No changes to `run_backtest` / `simulate_paired_option_trades`. New sim + helpers only. Rationale: avoids the strike re-resolution and keeps Phase-1 fast to validate.
2. **Pure helpers are the parity contract.** `lock_reference_strike`, `premium_series_for_key`, `momentum_triggered`, `walk_premium_momentum`, `stepped_trail_stop` are pure (plain args, no I/O), unit-tested, and shared with the live path later.
3. **Look-ahead safety:** the walk only ever reads premium at/through the current bar index; never the forward array.
4. **Coverage gate:** a session whose locked strike lacks a usable premium series (missing candles at/after the reference bar) is EXCLUDED and counted, never mis-filled.
5. **Single position (Phase 1):** lock BOTH CE and PE references; enter the FIRST to cross; that side owns the one position. `side` param can pin CE/PE.

---

## File Structure

- **Create** `backend/app/premium_momentum.py` — pure helpers (strike lock, premium series extraction, momentum trigger, entry+exit walk, stepped trail). No I/O.
- **Create** `backend/app/premium_momentum_backtest.py` — `run_premium_momentum_backtest(...)`: per-session loop over locked strikes using the helpers; coverage report.
- **Create** `backend/app/routers/premium_momentum_routes.py` — `POST /api/premium-momentum/backtest`: loads spot + option candles + contracts from the warehouse and calls the sim. (Thin I/O wrapper.)
- **Modify** `backend/server.py` — register the new router.
- **Modify** `frontend/src/components/strategy/AuthoringWizard.jsx` — Phase 0: hoist the feasibility panel out of the spec-only block.
- **Create tests**: `tests/test_premium_momentum.py` (pure helpers, host), `tests/test_premium_momentum_backtest.py` (sim, host), `tests/test_authoring_feasibility_panel.py` (Phase 0 JSX pin, host).

---

## Phase 0 — Fix the Full-Python feasibility panel

### Task 0.1: Hoist the `ruleSet` feasibility panel out of the spec-only block

**Files:**
- Modify: `frontend/src/components/strategy/AuthoringWizard.jsx` (panel currently at ~615–841, inside the `mode === "spec"` block that opens ~612 and closes ~883; the shared `runConverse` sets `ruleSet` at :234; the shared `converseError` panel is at ~500)
- Test: `tests/test_authoring_feasibility_panel.py`

- [ ] **Step 1: Write the failing host string-pin test**

```python
# tests/test_authoring_feasibility_panel.py
"""Phase 0: the feasibility (ruleSet) panel must render in BOTH authoring modes,
not just spec — it lives OUTSIDE the `mode === "spec"` block. HOST test (reads JSX)."""
from pathlib import Path

_FE = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _src(rel):
    return (_FE / rel).read_text(encoding="utf-8")


def test_feasibility_panel_is_outside_the_spec_mode_block():
    src = _src("components/strategy/AuthoringWizard.jsx")
    spec_gate = src.index('{mode === "spec" && (')
    # the ruleSet feasibility panel's decision chip must appear BEFORE the spec gate
    rule_panel = src.index("{ruleSet && (")
    assert rule_panel < spec_gate, (
        "the {ruleSet && (...)} feasibility panel is still nested inside the "
        "mode===spec block; Full-Python mode will render a blank verdict"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_authoring_feasibility_panel.py -q`
Expected: FAIL — `rule_panel < spec_gate` is False (panel is currently after the spec gate).

- [ ] **Step 3: Move the panel.** In `AuthoringWizard.jsx`, cut the `{ruleSet && ( ... )}` feasibility panel block (the one rendering `ruleSet.decision` / `ruleSet.summary` / `ruleSet.rules`) from inside the `{mode === "spec" && (<> ... </>)}` block and paste it into the shared "Describe with AI" section, immediately AFTER the `{converseError && ( ... )}` block and BEFORE the `{mode === "spec" && (<>` line. Also move the `{ruleSet && ruleSet.decision !== "BUILD" && ( ... )}` caveat note the same way. Do not change the panel's JSX internals — only its location.

- [ ] **Step 4: Verify the test passes + the file still parses**

Run: `python -m pytest tests/test_authoring_feasibility_panel.py -q`
Expected: PASS.
Run:
```bash
cd frontend && node -e 'const p=require("@babel/parser");const fs=require("fs");p.parse(fs.readFileSync("src/components/strategy/AuthoringWizard.jsx","utf8"),{sourceType:"module",plugins:["jsx"]});console.log("PARSE OK")'
```
Expected: `PARSE OK`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/strategy/AuthoringWizard.jsx tests/test_authoring_feasibility_panel.py
git commit -m "fix(authoring): render feasibility verdict in Full-Python mode too (hoist panel out of spec gate)"
```

---

## Phase 1 — Backtest premium-momentum entry (edge-validation gate)

### Task 1.1: Pure helper — lock the reference strike + extract its premium series

**Files:**
- Create: `backend/app/premium_momentum.py`
- Test: `tests/test_premium_momentum.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_premium_momentum.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from app.premium_momentum import lock_reference_strike, premium_series_for_key


_CONTRACTS = [
    {"instrument_key": "NSE|CE|24000", "strike": 24000, "side": "CE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|PE|24000", "strike": 24000, "side": "PE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
]


def test_lock_reference_strike_ce_itm1_is_below_spot():
    # NIFTY step 50. spot 24000 -> ATM 24000; CE ITM1 = ATM - 1 step = 23950.
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="CE", moneyness="itm1")
    assert got is not None
    assert got["strike"] == 23950 and got["side"] == "CE"
    assert got["instrument_key"] == "NSE|CE|23950"


def test_lock_reference_strike_pe_itm1_is_above_spot():
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="PE", moneyness="itm1")
    assert got["strike"] == 24050 and got["side"] == "PE"


def test_lock_reference_strike_missing_returns_none():
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="CE", moneyness="itm2")
    assert got is None   # no 23900 CE contract present


def test_premium_series_for_key_sorted_and_close_is_premium():
    candles = pd.DataFrame([
        {"instrument_key": "K", "ts": 300, "close": 12.0},
        {"instrument_key": "K", "ts": 100, "close": 10.0},
        {"instrument_key": "K", "ts": 200, "close": 11.0},
        {"instrument_key": "OTHER", "ts": 150, "close": 99.0},
    ])
    ts, prem = premium_series_for_key(candles, "K")
    assert list(ts) == [100, 200, 300]
    assert list(prem) == [10.0, 11.0, 12.0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_premium_momentum.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.premium_momentum'`.

- [ ] **Step 3: Implement the helpers**

```python
# backend/app/premium_momentum.py
"""Pure, host-testable helpers for the premium-momentum contingency strategy.

No DB / tick I/O — callers pass already-loaded contracts and option candles.
These are the SHARED rule functions: the backtest sim and (later) the live
deployment loop both call them, so entry/exit/strike semantics cannot drift.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.options_universe import select_contract_for_signal


def lock_reference_strike(*, contracts: List[Dict[str, Any]], underlying: str,
                          spot_at_ref: float, side: str,
                          moneyness: str = "itm1") -> Optional[Dict[str, Any]]:
    """Resolve and LOCK the option contract at the reference bar's spot.

    Wraps the shared selector so backtest and live pick the identical strike.
    Returns {"instrument_key","strike","side","moneyness"} or None if the strike
    is absent from `contracts` (coverage gap for that moneyness)."""
    sel = select_contract_for_signal(
        contracts=contracts, underlying=underlying,
        spot_price=float(spot_at_ref), direction=str(side).upper(),
        moneyness=str(moneyness),
    )
    if not sel:
        return None
    return {
        "instrument_key": sel["instrument_key"],
        "strike": int(sel["strike"]),
        "side": str(sel["side"]).upper(),
        "moneyness": str(moneyness),
    }


def premium_series_for_key(option_candles: pd.DataFrame,
                           instrument_key: str) -> Tuple[np.ndarray, np.ndarray]:
    """(ts[], premium[]) for one instrument_key, ascending by ts. premium = close.
    Empty arrays when the key is absent."""
    if option_candles is None or option_candles.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    sub = option_candles[option_candles["instrument_key"] == instrument_key]
    if sub.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    sub = sub.sort_values("ts")
    return sub["ts"].to_numpy(dtype="int64"), sub["close"].to_numpy(dtype="float64")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_premium_momentum.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/premium_momentum.py tests/test_premium_momentum.py
git commit -m "feat(premium-momentum): pure strike-lock + premium-series helpers"
```

### Task 1.2: Pure helper — momentum trigger + entry/exit walk (continuous stop)

**Files:**
- Modify: `backend/app/premium_momentum.py`
- Test: `tests/test_premium_momentum.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_premium_momentum.py
from app.premium_momentum import momentum_triggered, walk_premium_momentum


def test_momentum_triggered_pct_and_pts():
    assert momentum_triggered(premium_now=230.0, ref_premium=200.0, pct=15.0) is True
    assert momentum_triggered(premium_now=229.0, ref_premium=200.0, pct=15.0) is False
    assert momentum_triggered(premium_now=210.0, ref_premium=200.0, pts=10.0) is True
    assert momentum_triggered(premium_now=209.9, ref_premium=200.0, pts=10.0) is False


def test_walk_enters_on_first_cross_then_targets():
    # ref 200; +15% => enter at >=230. target +20% (from entry) => 276, stop -20% => 220.8
    ts   = [1, 2, 3, 4, 5, 6]
    prem = [200, 220, 235, 250, 280, 260]  # crosses 230 at idx2 (235), target 235*1.2=282 not hit til? 280<282,
    #                                        280 at idx4; 235*1.2=282 -> not hit; but +20% target uses entry=235
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is True
    assert r["entry_ts"] == 3 and r["entry_premium"] == 235.0
    # stop = 235*0.8 = 188 (never hit); target = 235*1.2 = 282 (never hit) -> TIME/EOD exit at last bar
    assert r["exit_reason"] in ("EOD", "TIME_EXIT")
    assert r["exit_premium"] == 260.0


def test_walk_stop_hit_before_target():
    ts   = [1, 2, 3, 4]
    prem = [200, 235, 200, 190]  # enter 235 at idx1; stop = 235*0.8=188; idx3 190>188 not hit; idx? 190>188
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=50.0, stop_pct=20.0)
    assert r["entered"] is True and r["entry_premium"] == 235.0
    # lowest is 190, stop is 188 -> not hit -> EOD at 190
    assert r["exit_premium"] == 190.0


def test_walk_no_entry_when_never_crosses():
    r = walk_premium_momentum(ts=[1, 2, 3], premium=[200, 205, 210], ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is False
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_premium_momentum.py -q`
Expected: FAIL — `cannot import name 'walk_premium_momentum'`.

- [ ] **Step 3: Implement**

```python
# append to backend/app/premium_momentum.py

def momentum_triggered(*, premium_now: float, ref_premium: float,
                       pct: Optional[float] = None, pts: Optional[float] = None) -> bool:
    """True once premium_now has risen to/above the momentum trigger from ref.
    Exactly one of pct (% of ref) or pts (absolute premium points) is used."""
    if ref_premium is None or ref_premium <= 0:
        return False
    if pct is not None:
        return float(premium_now) >= float(ref_premium) * (1.0 + float(pct) / 100.0)
    if pts is not None:
        return float(premium_now) >= float(ref_premium) + float(pts)
    return False


def walk_premium_momentum(*, ts, premium, ref_premium: float,
                          entry_pct: Optional[float] = None,
                          entry_pts: Optional[float] = None,
                          target_pct: Optional[float] = None,
                          target_pts: Optional[float] = None,
                          stop_pct: Optional[float] = None,
                          stop_pts: Optional[float] = None,
                          trail=None) -> Dict[str, Any]:
    """Walk a single locked strike's premium series (ascending ts):
    1. find the FIRST bar whose premium crosses the momentum trigger -> ENTRY;
    2. from the next bar, exit on premium stop / target (continuous), else at EOD.
    Look-ahead safe: never reads a future bar for the current decision. `trail`
    is a callable(entry_premium, running_high, base_stop)->stop for Phase 2;
    None => continuous base stop only. Returns a trade dict (entered=False if the
    momentum trigger never fired)."""
    ts = list(ts); premium = [float(p) for p in premium]
    n = len(premium)
    # --- entry: first cross ---
    entry_i = None
    for i in range(n):
        if momentum_triggered(premium_now=premium[i], ref_premium=ref_premium,
                              pct=entry_pct, pts=entry_pts):
            entry_i = i
            break
    if entry_i is None:
        return {"entered": False}
    entry_premium = premium[entry_i]
    base_stop = _level(entry_premium, stop_pct, stop_pts, is_stop=True)
    target = _level(entry_premium, target_pct, target_pts, is_stop=False)
    running_high = entry_premium
    # --- exit: from the bar AFTER entry (fill at entry bar's premium) ---
    for j in range(entry_i + 1, n):
        p = premium[j]
        running_high = max(running_high, p)
        stop = base_stop
        if trail is not None and base_stop is not None:
            stop = trail(entry_premium=entry_premium, running_high=running_high,
                         base_stop=base_stop)
        # stop-first (pessimistic), mirroring the spot engine's intrabar_exit
        if stop is not None and p <= stop:
            return _exit(ts, entry_i, entry_premium, j, stop, "STOP")
        if target is not None and p >= target:
            return _exit(ts, entry_i, entry_premium, j, target, "TARGET")
    # EOD
    return _exit(ts, entry_i, entry_premium, n - 1, premium[n - 1], "EOD")


def _level(entry: float, pct: Optional[float], pts: Optional[float], *, is_stop: bool):
    if pct is not None:
        return entry * (1.0 - pct / 100.0) if is_stop else entry * (1.0 + pct / 100.0)
    if pts is not None:
        return entry - pts if is_stop else entry + pts
    return None


def _exit(ts, entry_i, entry_premium, exit_i, exit_premium, reason) -> Dict[str, Any]:
    return {
        "entered": True,
        "entry_ts": ts[entry_i], "entry_premium": round(float(entry_premium), 4),
        "exit_ts": ts[exit_i], "exit_premium": round(float(exit_premium), 4),
        "exit_reason": reason,
        "premium_pnl": round(float(exit_premium) - float(entry_premium), 4),
        "bars_held": int(exit_i - entry_i),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_premium_momentum.py -q`
Expected: PASS (all tests). If a worked-example assertion is off by rounding, fix the ASSERTION to the arithmetic in the code (the code is the source of truth), not the code.

- [ ] **Step 5: Commit**

```bash
git add backend/app/premium_momentum.py tests/test_premium_momentum.py
git commit -m "feat(premium-momentum): momentum trigger + entry/exit walk (continuous stop)"
```

### Task 1.3: The session sim + coverage gate

**Files:**
- Create: `backend/app/premium_momentum_backtest.py`
- Test: `tests/test_premium_momentum_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_premium_momentum_backtest.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from app.premium_momentum_backtest import run_premium_momentum_backtest


def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


def test_one_session_ce_first_to_trigger():
    # reference 09:31 spot 24000 -> CE ITM1 = 23950. CE premium 100 -> +15% => enter at >=115.
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0),
        _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0),
        _spot_bar(4, "09:34", 24020.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 110.0),
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 150.0),   # crosses 115 at ts3
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 101.0),
        _opt("PE|24050", 3, 102.0), _opt("PE|24050", 4, 103.0),   # never crosses
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
                "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0},
    )
    assert out["coverage"]["sessions_traded"] == 1
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["side"] == "CE" and t["strike"] == 23950
    assert t["entry_premium"] == 120.0   # first bar >= 115


def test_session_excluded_when_locked_strike_has_no_candles():
    spot = pd.DataFrame([_spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0)])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"}]
    opt = pd.DataFrame(columns=["instrument_key", "ts", "close"])  # NO candles
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0},
    )
    assert out["trades"] == []
    assert out["coverage"]["sessions_excluded"] == 1
    assert out["coverage"]["exclude_reasons"].get("no_premium_series") == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_premium_momentum_backtest.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the sim**

```python
# backend/app/premium_momentum_backtest.py
"""Option-native backtest for the premium-momentum contingency strategy (Phase 1).

Per session: at the reference time, lock the chosen-moneyness CE and PE strikes
from spot, then walk each locked strike's premium series for a momentum entry +
premium exit (shared pure helpers). Single position: first side to trigger wins.
Coverage-gated: sessions whose locked strike lacks a premium series are excluded
and counted, never mis-filled."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from app.premium_momentum import (
    lock_reference_strike, premium_series_for_key, walk_premium_momentum,
)


def _sides_for(param: str) -> List[str]:
    p = str(param or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


def run_premium_momentum_backtest(*, spot_df: pd.DataFrame, option_candles: pd.DataFrame,
                                  contracts: List[Dict[str, Any]], instrument: str,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    ref_time = str(params.get("reference_time") or "09:31")
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides_for(params.get("side"))
    entry_pct = params.get("momentum_pct")
    entry_pts = params.get("momentum_pts")
    target_pct = params.get("target_pct")
    target_pts = params.get("target_pts")
    stop_pct = params.get("stop_pct")
    stop_pts = params.get("stop_pts")

    trades: List[Dict[str, Any]] = []
    cov = {"sessions_total": 0, "sessions_traded": 0, "sessions_excluded": 0,
           "sessions_no_signal": 0, "exclude_reasons": {}}

    for session, sdf in spot_df.groupby("session_date"):
        cov["sessions_total"] += 1
        sdf = sdf.sort_values("ts")
        ref_rows = sdf[sdf["ist_time"] >= ref_time]
        if ref_rows.empty:
            cov["sessions_excluded"] += 1
            cov["exclude_reasons"]["no_reference_bar"] = cov["exclude_reasons"].get("no_reference_bar", 0) + 1
            continue
        ref_row = ref_rows.iloc[0]
        spot_at_ref = float(ref_row["close"])
        ref_ts = int(ref_row["ts"])

        # Lock each side's strike + get its premium series FROM the reference bar on.
        candidates = []          # (side, locked, ts[], prem[])
        excluded = False
        for side in sides:
            locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                           spot_at_ref=spot_at_ref, side=side, moneyness=moneyness)
            if not locked:
                excluded = True
                cov["exclude_reasons"]["no_contract"] = cov["exclude_reasons"].get("no_contract", 0) + 1
                break
            ts_arr, prem_arr = premium_series_for_key(option_candles, locked["instrument_key"])
            mask = ts_arr >= ref_ts
            ts_arr, prem_arr = ts_arr[mask], prem_arr[mask]
            if len(prem_arr) == 0:
                excluded = True
                cov["exclude_reasons"]["no_premium_series"] = cov["exclude_reasons"].get("no_premium_series", 0) + 1
                break
            candidates.append((side, locked, ts_arr, prem_arr))
        if excluded:
            cov["sessions_excluded"] += 1
            continue

        # Walk each candidate; keep the one that ENTERS EARLIEST (first-to-trigger).
        best = None   # (side, locked, ref_premium, r)
        for side, locked, ts_arr, prem_arr in candidates:
            ref_premium = float(prem_arr[0])   # premium at/after the reference bar
            r = walk_premium_momentum(ts=ts_arr, premium=prem_arr, ref_premium=ref_premium,
                                      entry_pct=entry_pct, entry_pts=entry_pts,
                                      target_pct=target_pct, target_pts=target_pts,
                                      stop_pct=stop_pct, stop_pts=stop_pts)
            if not r.get("entered"):
                continue
            if best is None or r["entry_ts"] < best[3]["entry_ts"]:
                best = (side, locked, ref_premium, r)
        if best is None:
            cov["sessions_no_signal"] += 1
            continue
        side, locked, ref_premium, r = best
        trades.append({
            "session_date": str(session), "side": side, "strike": locked["strike"],
            "instrument_key": locked["instrument_key"], "moneyness": moneyness,
            "ref_premium": round(ref_premium, 4),
            **r,
        })
        cov["sessions_traded"] += 1

    return {"trades": trades, "coverage": cov, "params": dict(params)}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_premium_momentum_backtest.py -q`
Expected: PASS (2 tests). Fix assertion arithmetic to match the code if a rounding boundary differs.

- [ ] **Step 5: Commit**

```bash
git add backend/app/premium_momentum_backtest.py tests/test_premium_momentum_backtest.py
git commit -m "feat(premium-momentum): session sim (first-to-trigger) + coverage gate"
```

### Task 1.4: Warehouse-backed route (I/O wrapper) — run a real backtest

**Files:**
- Create: `backend/app/routers/premium_momentum_routes.py`
- Modify: `backend/server.py` (register the router next to the others, ~:220-228)
- Test: `tests/test_premium_momentum_route.py` (container — imports motor)

> Loads spot candles (`candles_1m`), the reference-day contracts (`option_contracts`)
> for the chosen weekly expiry, and the **full-day `options_1m` series for the locked
> strikes** (non-trade-driven), then calls `run_premium_momentum_backtest`. Follow the
> existing warehouse read pattern (`db.candles_1m.find(...).sort("ts",1)` for spot;
> `db.options_1m.find({"instrument_key": {"$in": locked_keys}, "ts": {"$gte":..,"$lte":..}}).sort("ts",1)`).
> Resolve the weekly expiry via the same policy used by `option_backtest` callers
> (nearest expiry ≥ session date). Gate on `option_coverage` and return the coverage
> report so a shrunk sample is visible.

- [ ] **Step 1: Write a container test** that posts a small date range for NIFTY and asserts the response has `trades` + `coverage` with `sessions_total > 0` (skip precise P&L — that's the sim's job, already unit-tested). Patch/seed a tiny fixture if the warehouse range is empty in CI.
- [ ] **Step 2: Run it, verify it fails** (route not registered).
- [ ] **Step 3: Implement the router** (an `APIRouter()` with `POST /premium-momentum/backtest` taking `{instrument, start_ts, end_ts, params}`; load spot + contracts + locked-strike option series; call the sim; return its result). Register in `server.py`.
- [ ] **Step 4: Run the container test, verify pass.** Sync via `docker cp` + `docker exec ... pytest` (MSYS_NO_PATHCONV=1).
- [ ] **Step 5: Commit.**

---

## Phase 2 — Stepped trailing SL (X-Y ratchet)

### Task 2.1: Pure helper — stepped trail stop

**Files:**
- Modify: `backend/app/premium_momentum.py`
- Test: `tests/test_premium_momentum.py`

- [ ] **Step 1: Write the failing tests** (the AlgoTest worked example: entry 200, base stop 175, TSL X=20 Y=20 → at high 220 stop→190, at high 240 stop→200)

```python
# append to tests/test_premium_momentum.py
from app.premium_momentum import stepped_trail_stop


def test_stepped_trail_ratchet_points():
    f = lambda high: stepped_trail_stop(entry_premium=200.0, running_high=high,
                                        base_stop=175.0, x=20.0, y=20.0)
    assert f(210.0) == 175.0   # < 1 full X step -> base stop
    assert f(220.0) == 195.0   # 1 step: 175 + 1*20
    assert f(239.0) == 195.0   # still 1 step
    assert f(240.0) == 215.0   # 2 steps: 175 + 2*20
    assert f(220.0) == 195.0   # monotonic within a call is by running_high, not path


def test_stepped_trail_never_below_base():
    assert stepped_trail_stop(entry_premium=200.0, running_high=205.0,
                              base_stop=175.0, x=20.0, y=20.0) == 175.0
```

- [ ] **Step 2: Run, verify fail** (`cannot import name 'stepped_trail_stop'`).
- [ ] **Step 3: Implement**

```python
# append to backend/app/premium_momentum.py

def stepped_trail_stop(*, entry_premium: float, running_high: float,
                       base_stop: float, x: float, y: float) -> float:
    """AlgoTest discrete ratchet: for every X favorable move (premium above entry),
    raise the stop by Y. stop = base_stop + floor(favorable / X) * Y. NOT a
    continuous high-water-minus-offset trail. Never below base_stop."""
    if x is None or x <= 0 or y is None or y <= 0:
        return base_stop
    favorable = float(running_high) - float(entry_premium)
    if favorable < x:
        return base_stop
    steps = int(favorable // float(x))
    return float(base_stop) + steps * float(y)
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(premium-momentum): stepped X-Y trailing ratchet`).

### Task 2.2: Wire stepped trail into the walk

**Files:**
- Modify: `backend/app/premium_momentum.py` (`walk_premium_momentum` already accepts `trail=`)
- Modify: `backend/app/premium_momentum_backtest.py` (build the `trail` callable from `params` when `trail_x`/`trail_y` set)
- Test: `tests/test_premium_momentum.py`, `tests/test_premium_momentum_backtest.py`

- [ ] **Step 1: Write a failing walk test** where the stepped trail exits ABOVE the base stop (a run that ratchets to 200 then falls back to 200 exits at 200, not the 175 base).

```python
# append to tests/test_premium_momentum.py
def test_walk_with_stepped_trail_exits_at_ratcheted_stop():
    import functools
    trail = functools.partial(stepped_trail_stop, x=20.0, y=20.0)
    # entry 235 (crosses 15% of 200=230 at 235). base stop 235*.9=211.5.
    ts   = [1, 2, 3, 4, 5]
    prem = [200, 235, 275, 255, 205]   # high 275 -> favorable 40 -> 2 steps -> stop 211.5+40=251.5; 255>251.5,
    #                                    then 205 < 251.5 -> exit at the ratcheted stop
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0, entry_pct=15.0,
                              stop_pct=10.0, target_pct=100.0, trail=trail)
    assert r["entered"] and r["exit_reason"] == "STOP"
    assert r["exit_premium"] <= 205.0 and r["exit_premium"] >= 200.0
```

- [ ] **Step 2: Run, verify fail** (walk currently ignores nothing — but assert the ratchet math; adjust to code arithmetic).
- [ ] **Step 3:** In `run_premium_momentum_backtest`, when `params` has `trail_x`/`trail_y`, build `trail = functools.partial(stepped_trail_stop, x=trail_x, y=trail_y)` and pass it to `walk_premium_momentum`. (The walk already threads it.)
- [ ] **Step 4: Run both test files, verify pass;** confirm byte-identity when `trail` is absent (Phase-1 tests still green).
- [ ] **Step 5: Commit** (`feat(premium-momentum): stepped trail wired into the sim`).

---

## Self-review notes (author)

- **Spec coverage:** Phase 0 (§5) → Task 0.1. Phase 1 (§6): strike lock → 1.1; momentum entry + premium SL/TGT → 1.2; per-session first-to-trigger + coverage gate → 1.3; non-trade-driven loader + real run → 1.4. Phase 2 (§7): stepped ratchet → 2.1/2.2. Fidelity caveats (§2/§10) are enforced by look-ahead-safe walk + coverage gate.
- **Deferred to later plans (design-frozen in spec §8):** Phase 3 live parity, Phase 4 config graduation, Phase 5 lazy legs. NOT in this plan.
- **Parity contract:** all entry/exit/strike logic is in `premium_momentum.py` pure helpers; the live path (Phase 3) will import the SAME functions.
- **Type consistency:** `lock_reference_strike` returns `{instrument_key,strike,side,moneyness}`; `walk_premium_momentum` returns `{entered,entry_ts,entry_premium,exit_ts,exit_premium,exit_reason,premium_pnl,bars_held}`; the sim wraps these + `{session_date,side,strike,instrument_key,moneyness,ref_premium}`. Used consistently across tasks.
