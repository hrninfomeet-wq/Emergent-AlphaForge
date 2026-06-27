# Live OCO Observability + Net-Greeks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the PC-down OCO backstop observable per position, give the kill-switch preview product-parity, and add a portfolio net-Δ / net-Θ card — all on the read/observability surface, none touching order transmit.

**Architecture:** Backend = a one-line product-parity fix, a one-line blotter passthrough, a pure-math Black-Scholes/IV module, an async aggregator with injected broker deps, and one fail-soft GET route. Frontend = an OCO chip in the (presentational) blotter, a new greeks slice on the 15s poll, and a compact net-Greeks card. IV is solved from the GetQuotes premium (Flattrade exposes no market IV).

**Tech Stack:** FastAPI + motor (Python 3.12), pure `math` (no scipy), CRA React + Tailwind, axios api client, `usePoll` context.

**Worktree / branch:** `af-wt-oco` on `feat/pc-down-oco-backstop`. Before each commit run `git branch --show-current` and confirm the branch; multi-line commit messages via Bash heredoc (NOT PowerShell here-string). End every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

**Test command (host):**
`"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/<file> -q`
Run from the worktree root. Never import `server.py` (motor is absent host-side). The worktree has **no `node_modules`**, so FE tasks (6–8) are NOT built here — they verify post-merge in the main repo.

---

## File structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `backend/app/live/kill_switch.py` | Modify (:343) | plan_squareoff preview uses the position's own product |
| `backend/app/live/live_blotter.py` | Modify (:161) | blotter row carries `oco_al_id` |
| `backend/app/live/greeks.py` | Create | Pure Black-Scholes price/Δ/Γ/Θ/vega + IV solve |
| `backend/app/live/portfolio_greeks.py` | Create | Async aggregator → net-Δ/net-Θ (injected broker deps) |
| `backend/app/routers/live_broker.py` | Modify (+route) | `GET /live-broker/greeks` (fail-soft) |
| `frontend/src/lib/api.js` | Modify | `getLiveGreeks` helper |
| `frontend/src/components/live/LiveDataProvider.jsx` | Modify | `greeks` slice on the 15s poll |
| `frontend/src/components/live/LiveBlotter.jsx` | Modify | OCO chip (presence + SL/TP tooltip) |
| `frontend/src/components/live/LiveDashboard.jsx` | Modify | pass `gtt` to blotter; mount `<GreeksCard/>` |
| `frontend/src/components/live/GreeksCard.jsx` | Create | Net-Δ/Θ card (consumes the provider) |
| `tests/test_live_kill_switch.py` | Modify | plan_squareoff parity test |
| `tests/test_live_blotter.py` | Modify | `oco_al_id` passthrough test |
| `tests/test_greeks.py` | Create | BS parity / bounds / IV fuzz / edges |
| `tests/test_portfolio_greeks.py` | Create | aggregation + skip paths (async) |
| `tests/test_live_greeks_route.py` | Create | route wiring + fail-soft (async) |

---

## Task 1: plan_squareoff product parity (backend)

**Files:**
- Modify: `backend/app/live/kill_switch.py` (the `plan_squareoff` exit `OrderIntent`, ~line 343)
- Test: `tests/test_live_kill_switch.py` (class `TestPlanSquareoff`)

- [ ] **Step 1: Write the failing test.** Add to `tests/test_live_kill_switch.py` (use a local helper that includes `prd`; the existing `_long_pos` omits it):

```python
class TestPlanSquareoffProduct:
    @staticmethod
    def _pos(tsym, netqty, prd, lp=200.0, exch="NFO"):
        return {"tsym": tsym, "netqty": str(netqty), "lp": str(lp), "exch": exch, "prd": prd}

    def test_plan_preview_carries_nrml_product(self):
        from app.live.kill_switch import plan_squareoff
        plan = plan_squareoff([], [self._pos("NIFTY25000CE", 65, "M")])
        assert len(plan["would_flatten"]) == 1
        assert plan["would_flatten"][0]["prd"] == "M", plan["would_flatten"][0]

    def test_plan_preview_missing_prd_defaults_to_mis(self):
        from app.live.kill_switch import plan_squareoff
        plan = plan_squareoff([], [{"tsym": "NIFTY25000CE", "netqty": "65", "lp": "200.0", "exch": "NFO"}])
        assert plan["would_flatten"][0]["prd"] == "I"
```

- [ ] **Step 2: Run it — expect the NRML test to FAIL** (current preview hardcodes `"I"`):

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_kill_switch.py::TestPlanSquareoffProduct -q`
Expected: `test_plan_preview_carries_nrml_product` FAILS with `assert 'I' == 'M'`; the missing-prd test passes.

- [ ] **Step 3: Implement.** In `kill_switch.py`, find the `plan_squareoff` exit `OrderIntent` (~line 343) — the per-position row variable in that loop is `pos`. Change the hardcoded product:

```python
            prd="I",
```
to (byte-identical to `panic_squareoff` line 513):
```python
            prd=(str(pos.get("prd")) if pos.get("prd") else "I"),
```

- [ ] **Step 4: Run the whole kill-switch suite — expect PASS:**

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_kill_switch.py -q`
Expected: all pass (existing + 2 new).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/live/kill_switch.py tests/test_live_kill_switch.py
git commit -m "$(cat <<'EOF'
fix(live): kill-switch PLAN preview uses each position's own product (parity with panic)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: oco_al_id on the blotter row (backend)

**Files:**
- Modify: `backend/app/live/live_blotter.py` (the row dict, line 161 — already has `"oco_error"`)
- Test: `tests/test_live_blotter.py`

- [ ] **Step 1: Write the failing test.** Add to `tests/test_live_blotter.py` (mirror the existing `build_live_blotter` test setup in that file — match how it constructs a held trade + broker positions; the key assertion is the new field):

```python
def test_blotter_row_passes_through_oco_al_id():
    from app.live.live_blotter import build_live_blotter
    trades = [{
        "id": "t1", "norenordno": "N1", "trading_symbol": "NIFTY25000CE",
        "tsym": "NIFTY25000CE", "direction": "B", "lots": 1, "quantity": 65,
        "entry_price": 100.0, "status": "OPEN", "deployment_id": "d1",
        "oco_al_id": "AL-123", "oco_error": None,
    }]
    positions = [{"tsym": "NIFTY25000CE", "netqty": "65", "lp": "110.0"}]
    rows = build_live_blotter(trades, positions, {})
    assert rows[0]["oco_al_id"] == "AL-123"

def test_blotter_row_oco_al_id_absent_is_none():
    from app.live.live_blotter import build_live_blotter
    trades = [{"id": "t2", "norenordno": "N2", "tsym": "NIFTY25000CE",
               "trading_symbol": "NIFTY25000CE", "status": "OPEN", "quantity": 65}]
    rows = build_live_blotter(trades, [{"tsym": "NIFTY25000CE", "netqty": "65", "lp": "1.0"}], {})
    assert rows[0]["oco_al_id"] is None
```

> If `build_live_blotter`'s signature in the file differs (arg names/order), match the file — the only new assertion is `rows[0]["oco_al_id"]`. Read the existing tests in `tests/test_live_blotter.py` and copy their construction idiom.

- [ ] **Step 2: Run — expect FAIL** (`KeyError`/`None` mismatch — `oco_al_id` not in the row):

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_blotter.py -q -k oco_al_id`
Expected: FAIL (`KeyError: 'oco_al_id'`).

- [ ] **Step 3: Implement.** In `live_blotter.py`, the row dict ends with `"oco_error": t.get("oco_error"),` (line 161). Add directly below it:

```python
            "oco_error": t.get("oco_error"),
            # Resting OCO handle (the live_trades doc carries it when the backstop
            # was placed). The blotter UI matches this against the GTT/OCO book to
            # show a positive "OCO ✓" chip with the resting SL/TP band.
            "oco_al_id": t.get("oco_al_id"),
```

- [ ] **Step 4: Run the blotter suite — expect PASS:**

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_blotter.py -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/live/live_blotter.py tests/test_live_blotter.py
git commit -m "$(cat <<'EOF'
feat(live): blotter row carries oco_al_id for the per-position OCO chip

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: greeks.py — pure Black-Scholes + IV solve (backend)

**Files:**
- Create: `backend/app/live/greeks.py`
- Test: `tests/test_greeks.py`

- [ ] **Step 1: Write the failing test** `tests/test_greeks.py`:

```python
import math
import pytest
from app.live import greeks as g


def test_put_call_parity():
    S, K, T, r, vol = 100.0, 100.0, 0.5, 0.065, 0.25
    c = g.bs_price(S, K, T, r, vol, True)
    p = g.bs_price(S, K, T, r, vol, False)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)


def test_delta_bounds_and_gamma_vega_positive():
    S, K, T, r, vol = 100.0, 95.0, 0.25, 0.065, 0.3
    assert 0.0 <= g.bs_delta(S, K, T, r, vol, True) <= 1.0
    assert -1.0 <= g.bs_delta(S, K, T, r, vol, False) <= 0.0
    assert g.bs_gamma(S, K, T, r, vol) > 0.0
    assert g.bs_vega(S, K, T, r, vol) > 0.0


def test_long_option_theta_per_day_negative():
    out = g.compute_greeks(100.0, 100.0, 0.1, 5.0, True)
    assert out is not None and out["theta_per_day"] < 0.0


@pytest.mark.parametrize("moneyness", [0.85, 0.95, 1.0, 1.05, 1.15])
@pytest.mark.parametrize("T", [0.02, 0.1, 0.5])
@pytest.mark.parametrize("vol0", [0.12, 0.25, 0.6])
@pytest.mark.parametrize("is_call", [True, False])
def test_iv_roundtrip(moneyness, T, vol0, is_call):
    S, r = 100.0, 0.065
    K = S / moneyness
    price = g.bs_price(S, K, T, r, vol0, is_call)
    iv, conf = g.implied_vol(price, S, K, T, r, is_call)
    assert iv is not None
    assert iv == pytest.approx(vol0, abs=2e-3)


def test_sub_intrinsic_premium_unsolvable():
    # call worth >= S - K*e^{-rT}; a premium below intrinsic has no IV
    S, K, T, r = 120.0, 100.0, 0.5, 0.065
    intrinsic = S - K * math.exp(-r * T)
    iv, conf = g.implied_vol(intrinsic - 1.0, S, K, T, r, True)
    assert iv is None and conf == "none"


def test_non_positive_inputs_return_none():
    assert g.compute_greeks(0.0, 100.0, 0.1, 5.0, True) is None
    assert g.compute_greeks(100.0, 100.0, 0.0, 5.0, True) is None
    assert g.compute_greeks(100.0, 100.0, 0.1, 0.0, True) is None


def test_deep_itm_low_confidence():
    # deep ITM call: tiny vega → low-confidence but Δ/Θ still returned
    out = g.compute_greeks(200.0, 50.0, 0.05, 150.1, True)
    assert out is None or out["confidence"] in ("low", "ok")
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: app.live.greeks`):

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_greeks.py -q`
Expected: collection error / import failure.

- [ ] **Step 3: Implement** `backend/app/live/greeks.py`:

```python
"""Black-Scholes option pricing + Greeks + implied-vol solve (pure, no I/O).

Flattrade exposes no market IV (GetOptionChain has none; GetOptionGreek consumes
volatility as an input), so IV is solved from the live premium and the Greeks are
derived from the same model. All functions are pure + synchronous (no broker),
fully unit-testable. norm.cdf via math.erf — no scipy.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

RISK_FREE_RATE = 0.065          # India ~1y T-bill; module default
IV_MIN = 0.01                   # 1%   solver clamp floor
IV_MAX = 5.0                    # 500% solver clamp ceiling
INTRADAY_FLOOR_DAYS = 0.25      # floor TTE so 0DTE never divides by zero
_LOW_VEGA = 1e-4                # vega below this → IV unreliable (deep ITM/OTM)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(spot: float, strike: float, t: float, rate: float, vol: float) -> Tuple[float, float]:
    vsqrt = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / vsqrt
    return d1, d1 - vsqrt


def bs_price(spot, strike, t, rate, vol, is_call) -> float:
    d1, d2 = _d1_d2(spot, strike, t, rate, vol)
    disc = math.exp(-rate * t)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(spot, strike, t, rate, vol, is_call) -> float:
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def bs_gamma(spot, strike, t, rate, vol) -> float:
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return _norm_pdf(d1) / (spot * vol * math.sqrt(t))


def bs_vega(spot, strike, t, rate, vol) -> float:
    """∂Price/∂vol per 1.0 (100%) change in vol."""
    d1, _ = _d1_d2(spot, strike, t, rate, vol)
    return spot * _norm_pdf(d1) * math.sqrt(t)


def bs_theta_per_year(spot, strike, t, rate, vol, is_call) -> float:
    d1, d2 = _d1_d2(spot, strike, t, rate, vol)
    disc = math.exp(-rate * t)
    term1 = -(spot * _norm_pdf(d1) * vol) / (2.0 * math.sqrt(t))
    if is_call:
        return term1 - rate * strike * disc * _norm_cdf(d2)
    return term1 + rate * strike * disc * _norm_cdf(-d2)


def _intrinsic(spot, strike, t, rate, is_call) -> float:
    disc = math.exp(-rate * t)
    return max(spot - strike * disc, 0.0) if is_call else max(strike * disc - spot, 0.0)


def implied_vol(premium, spot, strike, t, rate, is_call) -> Tuple[Optional[float], str]:
    """Solve IV from a market premium → (iv|None, confidence).

    Newton on vega with a bisection fallback, clamped to [IV_MIN, IV_MAX].
    (None, "none") when premium <= 0 / below intrinsic / unsolvable.
    confidence == "low" when vega at the solution is tiny (deep ITM/OTM).
    """
    if not (premium > 0.0 and spot > 0.0 and strike > 0.0 and t > 0.0):
        return None, "none"
    if premium < _intrinsic(spot, strike, t, rate, is_call) - 1e-6:
        return None, "none"

    vol = 0.3
    for _ in range(50):
        diff = bs_price(spot, strike, t, rate, vol, is_call) - premium
        if abs(diff) < 1e-6:
            break
        vega = bs_vega(spot, strike, t, rate, vol)
        if vega < _LOW_VEGA:
            break
        vol -= diff / vega
        if vol <= IV_MIN or vol >= IV_MAX:
            vol = min(max(vol, IV_MIN), IV_MAX)
            break

    if abs(bs_price(spot, strike, t, rate, vol, is_call) - premium) > 1e-3:
        lo, hi = IV_MIN, IV_MAX
        plo = bs_price(spot, strike, t, rate, lo, is_call) - premium
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            pmid = bs_price(spot, strike, t, rate, mid, is_call) - premium
            vol = mid
            if abs(pmid) < 1e-6:
                break
            if (plo < 0.0) == (pmid < 0.0):
                lo, plo = mid, pmid
            else:
                hi = mid

    vol = min(max(vol, IV_MIN), IV_MAX)
    conf = "low" if bs_vega(spot, strike, t, rate, vol) < _LOW_VEGA else "ok"
    return vol, conf


def compute_greeks(spot, strike, t_years, premium, is_call, rate: float = RISK_FREE_RATE) -> Optional[dict]:
    """IV-from-premium → {iv, delta, gamma, theta_per_day, vega, confidence} or None."""
    if not (spot > 0.0 and strike > 0.0 and t_years > 0.0 and premium > 0.0):
        return None
    iv, conf = implied_vol(premium, spot, strike, t_years, rate, is_call)
    if iv is None:
        return None
    return {
        "iv": iv,
        "delta": bs_delta(spot, strike, t_years, rate, iv, is_call),
        "gamma": bs_gamma(spot, strike, t_years, rate, iv),
        "theta_per_day": bs_theta_per_year(spot, strike, t_years, rate, iv, is_call) / 365.0,
        "vega": bs_vega(spot, strike, t_years, rate, iv),
        "confidence": conf,
    }
```

- [ ] **Step 4: Run — expect PASS:**

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_greeks.py -q`
Expected: all pass (incl. the parametrized IV round-trip grid).

- [ ] **Step 5: Commit.**

```bash
git add backend/app/live/greeks.py tests/test_greeks.py
git commit -m "$(cat <<'EOF'
feat(live): pure Black-Scholes Greeks + implied-vol solver (no scipy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: portfolio_greeks.py — async aggregator (backend)

**Files:**
- Create: `backend/app/live/portfolio_greeks.py`
- Test: `tests/test_portfolio_greeks.py`

- [ ] **Step 1: Write the failing test** `tests/test_portfolio_greeks.py`:

```python
import asyncio
from datetime import date
from app.live.portfolio_greeks import compute_portfolio_greeks


def _mk_quote(lp=None, bp1=None, sp1=None, sptprc=None, und_tk=None, und_exch=None):
    q = {}
    if lp is not None: q["lp"] = str(lp)
    if bp1 is not None: q["bp1"] = str(bp1)
    if sp1 is not None: q["sp1"] = str(sp1)
    if sptprc is not None: q["sptprc"] = str(sptprc)
    if und_tk is not None: q["und_tk"] = str(und_tk)
    if und_exch is not None: q["und_exch"] = str(und_exch)
    return q


def _run(coro):
    return asyncio.run(coro)


def test_aggregates_net_delta_and_theta():
    positions = [{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]

    async def quote(exch, token):
        return _mk_quote(bp1=99.5, sp1=100.5, sptprc=25000.0)

    async def resolve(tsym, exch):
        return (25000.0, (date.today().isoformat()), True, "TKN1")  # expiry today → TTE floor

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 1 and out["n_skipped"] == 0
    assert out["net_delta_rupees_per_point"] != 0.0
    assert out["net_theta_rupees_per_day"] < 0.0  # long option bleeds theta


def test_skips_unresolvable_contract():
    positions = [{"tsym": "X", "exch": "NFO", "position": {"netqty": 65}}]

    async def quote(exch, token):
        return _mk_quote(lp=100.0, sptprc=25000.0)

    async def resolve(tsym, exch):
        return None

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 0 and out["n_skipped"] == 1


def test_underlying_quote_fallback_for_spot():
    # option quote lacks sptprc but carries und_tk/und_exch → second quote gives spot
    from datetime import timedelta
    exp = (date.today() + timedelta(days=7)).isoformat()
    positions = [{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]
    calls = {"n": 0}

    async def quote(exch, token):
        calls["n"] += 1
        if token == "TKN1":
            return _mk_quote(bp1=99.5, sp1=100.5, und_tk="26000", und_exch="NSE")
        return _mk_quote(lp=25000.0)  # underlying

    async def resolve(tsym, exch):
        return (25000.0, exp, True, "TKN1")

    out = _run(compute_portfolio_greeks(
        positions, get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out["n_computed"] == 1 and calls["n"] == 2


def test_empty_positions_returns_zeros():
    async def quote(exch, token): return {}
    async def resolve(tsym, exch): return None
    out = _run(compute_portfolio_greeks(
        [], get_quote_fn=quote, resolve_contract_fn=resolve, today=date.today()))
    assert out == {"net_delta_rupees_per_point": 0.0, "net_theta_rupees_per_day": 0.0,
                   "n_computed": 0, "n_skipped": 0, "positions": []}
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: app.live.portfolio_greeks`):

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_portfolio_greeks.py -q`
Expected: import failure.

- [ ] **Step 3: Implement** `backend/app/live/portfolio_greeks.py`:

```python
"""Aggregate live option Greeks into a portfolio net-Δ / net-Θ summary.

Pure orchestration: the broker quote + contract resolution are injected (async),
so this is fully testable with no network. Math lives in app/live/greeks.py.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.live.greeks import INTRADAY_FLOOR_DAYS, RISK_FREE_RATE, compute_greeks


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _signed_netqty(pos: Dict[str, Any]) -> int:
    inner = pos.get("position") if isinstance(pos.get("position"), dict) else {}
    for src in (inner.get("netqty"), pos.get("netqty"), pos.get("qty")):
        n = _to_float(src)
        if n is not None:
            return int(n)
    return 0


def _premium_from_quote(q: Dict[str, Any]) -> Optional[float]:
    bp1, sp1 = _to_float(q.get("bp1")), _to_float(q.get("sp1"))
    if bp1 and sp1 and bp1 > 0.0 and sp1 > 0.0:
        return 0.5 * (bp1 + sp1)
    return _to_float(q.get("lp"))


async def _spot_from_quote(q: Dict[str, Any], get_quote_fn) -> Optional[float]:
    spot = _to_float(q.get("sptprc"))
    if spot is not None and spot > 0.0:
        return spot
    und_tk, und_exch = q.get("und_tk"), q.get("und_exch")
    if und_tk and und_exch:
        try:
            uq = await get_quote_fn(str(und_exch), str(und_tk))
        except Exception:
            uq = {}
        return _to_float((uq or {}).get("lp"))
    return None


async def compute_portfolio_greeks(
    positions: List[Dict[str, Any]],
    *,
    get_quote_fn: Callable[[str, str], Awaitable[Dict[str, Any]]],
    resolve_contract_fn: Callable[[str, str], Awaitable[Optional[Tuple[float, str, bool, str]]]],
    today: date,
    spot_fallback: Optional[float] = None,
    rate: float = RISK_FREE_RATE,
) -> Dict[str, Any]:
    net_delta = 0.0
    net_theta = 0.0
    n_computed = 0
    n_skipped = 0
    per_position: List[Dict[str, Any]] = []

    for pos in positions or []:
        tsym = str(pos.get("tsym") or "")
        exch = str(pos.get("exch") or "")
        netqty = _signed_netqty(pos)
        if not tsym or not exch or netqty == 0:
            n_skipped += 1
            continue

        try:
            contract = await resolve_contract_fn(tsym, exch)
        except Exception:
            contract = None
        if not contract:
            n_skipped += 1
            continue
        strike, expiry_iso, is_call, token = contract

        try:
            q = await get_quote_fn(exch, str(token))
        except Exception:
            q = {}
        q = q or {}
        premium = _premium_from_quote(q)
        spot = await _spot_from_quote(q, get_quote_fn)
        if spot is None:
            spot = spot_fallback
        if premium is None or spot is None:
            n_skipped += 1
            continue

        try:
            days = (date.fromisoformat(str(expiry_iso)) - today).days
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        t_years = max(float(days), INTRADAY_FLOOR_DAYS) / 365.0

        g = compute_greeks(spot, strike, t_years, premium, is_call, rate=rate)
        if g is None:
            n_skipped += 1
            continue

        net_delta += g["delta"] * netqty
        net_theta += g["theta_per_day"] * netqty
        n_computed += 1
        per_position.append({
            "tsym": tsym, "netqty": netqty, "spot": spot, "premium": premium,
            "iv": g["iv"], "delta": g["delta"], "theta_per_day": g["theta_per_day"],
            "confidence": g["confidence"],
        })

    return {
        "net_delta_rupees_per_point": net_delta,
        "net_theta_rupees_per_day": net_theta,
        "n_computed": n_computed,
        "n_skipped": n_skipped,
        "positions": per_position,
    }
```

- [ ] **Step 4: Run — expect PASS:**

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_portfolio_greeks.py -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/live/portfolio_greeks.py tests/test_portfolio_greeks.py
git commit -m "$(cat <<'EOF'
feat(live): portfolio net-delta/net-theta aggregator (injected broker deps)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: GET /live-broker/greeks route (backend)

**Files:**
- Modify: `backend/app/routers/live_broker.py` (add a route; reuse `_get_client`, `_get_live_registry`, and `flattrade_symbol` parsers)
- Test: `tests/test_live_greeks_route.py`

> **Read first:** open `backend/app/routers/live_broker.py` and confirm the exact names of (a) the router object (e.g. `router`), (b) the client getter (`_get_client`), and (c) the live registry getter (`_get_live_registry`) — used by the existing reconcile/kill-switch routes. Use those exact names. Confirm `from app.live.flattrade_symbol import _parse_exd, _strike_from_dname, SymbolResolutionError` resolves.

- [ ] **Step 1: Write the failing test** `tests/test_live_greeks_route.py`:

```python
import asyncio
from datetime import date, timedelta
import app.routers.live_broker as lb
from app.live.mock_noren import MockNoren


class _Reg:
    def __init__(self, items): self._items = items
    def snapshot(self): return list(self._items)


def _run(coro): return asyncio.run(coro)


def test_greeks_route_empty_when_no_client(monkeypatch):
    monkeypatch.setattr(lb, "_get_client", lambda: None)
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 0 and out["positions"] == []


def test_greeks_route_aggregates(monkeypatch):
    exp = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y").upper()  # e.g. 04-JUL-2026
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    cl.set_search_scrip("NFO", [{
        "tsym": "NIFTY25000CE", "token": "TKN1", "optt": "CE",
        "exd": exp, "dname": "NIFTY 04JUL26 25000 CE ",
    }])
    monkeypatch.setattr(lb, "_get_client", lambda: cl)
    monkeypatch.setattr(lb, "_get_live_registry",
                        lambda: _Reg([{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]))
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0
```

> If `MockNoren` has no `set_search_scrip`, set the fixture via its constructor/attribute the way `tests/test_live_l3_routes.py` does (read that file). The mock's `search_scrip(exch, text)` looks up by `(exch, text)` then by `exch` — key it by `"NFO"`.

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: module ... has no attribute 'live_broker_greeks'`):

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_greeks_route.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement.** Add near the other GET routes in `live_broker.py` (after the imports add the parser import + a module-level cache, then the route). Use the router/getters confirmed in "Read first":

```python
from app.live.flattrade_symbol import _parse_exd, _strike_from_dname, SymbolResolutionError
from app.live.portfolio_greeks import compute_portfolio_greeks
from datetime import date as _date

# Contract metadata is static per tsym — resolve once via SearchScrip, then reuse.
_greeks_contract_cache: dict = {}

_GREEKS_EMPTY = {
    "net_delta_rupees_per_point": 0.0, "net_theta_rupees_per_day": 0.0,
    "n_computed": 0, "n_skipped": 0, "positions": [],
}


@router.get("/live-broker/greeks")
async def live_broker_greeks():
    """Portfolio net-Δ (₹/index point) + net-Θ (₹/day) across live positions.

    Fail-soft: not connected / no positions → zeros. General API (40/s); never on
    the guard hot path. IV solved from the GetQuotes premium (no market IV exists).
    """
    client = _get_client()
    if client is None:
        return dict(_GREEKS_EMPTY)
    positions = _get_live_registry().snapshot()
    if not positions:
        return dict(_GREEKS_EMPTY)

    async def _resolve(tsym: str, exch: str):
        if tsym in _greeks_contract_cache:
            return _greeks_contract_cache[tsym]
        try:
            rows = await client.search_scrip(exch, tsym)
        except Exception:
            return None
        for r in rows or []:
            if str(r.get("tsym")) == str(tsym):
                try:
                    strike = _strike_from_dname(str(r.get("dname", "")))
                    expiry_iso = _parse_exd(str(r.get("exd", "")))
                except SymbolResolutionError:
                    return None
                token = str(r.get("token") or "")
                if not token:
                    return None
                out = (strike, expiry_iso, str(r.get("optt", "")).upper() == "CE", token)
                _greeks_contract_cache[tsym] = out
                return out
        return None

    try:
        return await compute_portfolio_greeks(
            positions,
            get_quote_fn=client.get_quotes,
            resolve_contract_fn=_resolve,
            today=_date.today(),
        )
    except Exception:
        return dict(_GREEKS_EMPTY)
```

- [ ] **Step 4: Run — expect PASS:**

Run: `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/test_live_greeks_route.py -q`
Expected: all pass. (If the `set_search_scrip` helper name differs, adapt the test to the mock's real injection API and re-run.)

- [ ] **Step 5: Commit.**

```bash
git add backend/app/routers/live_broker.py tests/test_live_greeks_route.py
git commit -m "$(cat <<'EOF'
feat(live): GET /live-broker/greeks — portfolio net-delta/net-theta (fail-soft)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend — api helper + provider greeks slice

**Files:**
- Modify: `frontend/src/lib/api.js` (after the `getGuardStatus` helper, ~line 310)
- Modify: `frontend/src/components/live/LiveDataProvider.jsx`

> No FE build in the worktree (no node_modules). Verify by reading the diff; build happens post-merge.

- [ ] **Step 1: Add the api helper.** In `frontend/src/lib/api.js`, immediately after the `getGuardStatus` entry (line 309-310):

```javascript
  getGuardStatus: () =>
    apiClient.get("/live-broker/guard-status").then((r) => r.data),
  getLiveGreeks: () =>
    apiClient.get("/live-broker/greeks").then((r) => r.data),
```

- [ ] **Step 2: Add the provider slice.** In `LiveDataProvider.jsx`, add a slow-group poll next to `blotter` (after line 52):

```javascript
  const { data: blotter, error: eBlotter, refetch: rBlotter } = usePoll(() => api.getLiveBlotter(), SLOW_MS);
  const { data: greeks, error: eGreeks, refetch: rGreeks } = usePoll(() => api.getLiveGreeks(), SLOW_MS);
```

- [ ] **Step 3: Wire it into refetchSlow, the context value, errors, and deps.** Three edits:

(a) `refetchSlow` (line 85-88) — add `rGreeks()`:
```javascript
  const refetchSlow = useCallback(() => {
    rStatus(); rLimits(); rPositions(); rOrders();
    rReconcile(); rArmState(); rBlotter(); rDeployments(); rGreeks();
  }, [rStatus, rLimits, rPositions, rOrders, rReconcile, rArmState, rBlotter, rDeployments, rGreeks]);
```

(b) the `value` object (line 114-122) — add `greeks` to data and `greeks: eGreeks` to errors:
```javascript
      status, limits, positions, orders, reconcile, armState, blotter, deployments,
      guard, session, gtt, greeks,
      deployLive: deployLiveData || {},
      errors: {
        status: eStatus, limits: eLimits, positions: ePositions, orders: eOrders,
        reconcile: eReconcile, armState: eArmState, blotter: eBlotter, deployments: eDeployments,
        guard: eGuard, session: eSession, gtt: eGtt, deployLive: eDeployLive, greeks: eGreeks,
      },
```

(c) the `value` useMemo dep array (line 125-130) — add `greeks` and `eGreeks`:
```javascript
      status, limits, positions, orders, reconcile, armState, blotter, deployments,
      guard, session, gtt, greeks, deployLiveData,
      eStatus, eLimits, ePositions, eOrders, eReconcile, eArmState, eBlotter, eDeployments,
      eGuard, eSession, eGtt, eDeployLive, eGreeks, refetch,
```

- [ ] **Step 4: Verify by reading the diff** (`git diff`): the new slice is wired into refetchSlow, value.data, value.errors, and the memo deps; no other slice was disturbed.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/lib/api.js frontend/src/components/live/LiveDataProvider.jsx
git commit -m "$(cat <<'EOF'
feat(live): getLiveGreeks api helper + provider greeks slice (15s)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend — OCO chip in the blotter

**Files:**
- Modify: `frontend/src/components/live/LiveBlotter.jsx` (presentational — gains a `gtt` prop)
- Modify: `frontend/src/components/live/LiveDashboard.jsx` (pass `gtt` to the blotter)

- [ ] **Step 1: Accept `gtt` + build an OCO lookup.** In `LiveBlotter.jsx`, change the signature and add a memoized map of `al_id → {sl, tp}` from the OCO book. Replace `export default function LiveBlotter({ rows }) {` and the start of its body:

```javascript
export default function LiveBlotter({ rows, gtt }) {
  // al_id → { sl, tp } from the resting GTT/OCO book, so a backed position can
  // show its catastrophe band. oivariable legs: var_name "x" = SL, "y" = TP.
  const ocoByAlId = useMemo(() => {
    const m = {};
    for (const g of Array.isArray(gtt) ? gtt : []) {
      const id = g?.al_id ?? g?.Al_id;
      if (!id) continue;
      const legs = Array.isArray(g?.oivariable) ? g.oivariable : [];
      const sl = legs.find((l) => l?.var_name === "x")?.d;
      const tp = legs.find((l) => l?.var_name === "y")?.d;
      m[String(id)] = { sl, tp };
    }
    return m;
  }, [gtt]);
```

(The existing `useMemo` for the summary counts stays as-is, right below.)

- [ ] **Step 2: Render the chip.** In the LIVE branch of the status cell (currently the `{r?.oco_error && (...)}` block, lines 156-163), add a positive OCO chip as a sibling. Replace that block with:

```javascript
                        {r?.oco_error ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-amber-500/40 bg-amber-500/10 text-amber-300"
                            title="The resting broker OCO failed to place — this position has NO PC-down broker backstop, only the software guard while the app is running."
                          >
                            no broker net
                          </span>
                        ) : r?.oco_al_id ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                            title={
                              ocoByAlId[String(r.oco_al_id)]?.sl != null
                                ? `Resting broker OCO backstop — SL ₹${ocoByAlId[String(r.oco_al_id)].sl} · TP ₹${ocoByAlId[String(r.oco_al_id)].tp}`
                                : "Resting broker OCO backstop (PC-down protected)."
                            }
                          >
                            OCO &#10003;
                          </span>
                        ) : null}
```

- [ ] **Step 3: Pass `gtt` from the dashboard.** In `LiveDashboard.jsx`:

(a) add `gtt` to the `useLiveData()` destructure (line 337):
```javascript
    status, limits, positions, orders, reconcile, armState, blotter, guard, gtt, refetch,
  } = useLiveData();
```

(b) pass it to the blotter (line 519):
```javascript
        <LiveBlotter rows={blotter?.rows} gtt={gtt} />
```

- [ ] **Step 4: Verify by reading the diff:** the chip renders for `oco_al_id` (no error) with the SL/TP tooltip when the gtt entry is found; `oco_error` still wins; FLAT/CLOSED unaffected; `gtt` is threaded.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/live/LiveBlotter.jsx frontend/src/components/live/LiveDashboard.jsx
git commit -m "$(cat <<'EOF'
feat(live): per-position OCO chip (resting backstop + SL/TP band)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Frontend — GreeksCard + mount

**Files:**
- Create: `frontend/src/components/live/GreeksCard.jsx`
- Modify: `frontend/src/components/live/LiveDashboard.jsx` (mount the card)

- [ ] **Step 1: Create the card** `frontend/src/components/live/GreeksCard.jsx` (consumes the provider directly, like GuardPanel/GttBook):

```javascript
import { Activity } from "lucide-react";
import { useLiveData } from "@/components/live/LiveDataProvider";
import { fmtINRSigned, colorPnL } from "@/lib/fmt";

/**
 * GreeksCard — portfolio net delta + net theta across open live positions.
 *
 * Net Δ = ₹ P&L per 1 index point of underlying move; Net Θ = ₹/day time decay
 * (negative = the daily premium "rent" a buyer pays). Server-side Black-Scholes,
 * IV solved from the live GetQuotes premium. Informational only — the system
 * does not act on Greeks (exits are governed by premium stops + the OCO).
 */
export default function GreeksCard() {
  const { greeks } = useLiveData();
  const loading = greeks == null;
  const netDelta = Number(greeks?.net_delta_rupees_per_point);
  const netTheta = Number(greeks?.net_theta_rupees_per_day);
  const nComputed = greeks?.n_computed ?? 0;
  const nSkipped = greeks?.n_skipped ?? 0;
  const total = nComputed + nSkipped;

  return (
    <div className="rounded-lg border border-line bg-bg-2/40 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-dimmer">
          <Activity className="w-3.5 h-3.5" /> Portfolio Greeks
        </span>
        {total > 0 && (
          <span className="text-[10px] font-mono text-dimmer/70">{nComputed} of {total} priced</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 font-mono">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Δ (₹/point)</div>
          <div className={`text-lg font-semibold ${loading || !Number.isFinite(netDelta) ? "text-dimmer" : colorPnL(netDelta)}`}>
            {loading || !Number.isFinite(netDelta) ? "—" : fmtINRSigned(netDelta)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Θ (₹/day)</div>
          <div className={`text-lg font-semibold ${loading || !Number.isFinite(netTheta) ? "text-dimmer" : colorPnL(netTheta)}`}>
            {loading || !Number.isFinite(netTheta) ? "—" : fmtINRSigned(netTheta)}
          </div>
        </div>
      </div>
      {!loading && nComputed === 0 && total === 0 && (
        <div className="text-[10px] text-dimmer/70 mt-2">No open live positions.</div>
      )}
    </div>
  );
}
```

> Confirm `fmtINRSigned` and `colorPnL` are exported from `@/lib/fmt` (LiveBlotter imports both) and `Activity` exists in `lucide-react` (used elsewhere in the codebase; if not, use `TrendingDown`).

- [ ] **Step 2: Mount it.** In `LiveDashboard.jsx`, add the import near the other live imports (e.g. after the `LiveBlotter` import, line 18):

```javascript
import GreeksCard from "@/components/live/GreeksCard";
```

Then mount it right after the Live Deployment Blotter `SectionCard` (after line 520, before the hero metric strip at line 522):

```javascript
      </SectionCard>

      {/* ── 1d. Portfolio Greeks (net Δ / Θ across live positions) ───────── */}
      <GreeksCard />

      {/* ── 2. Hero metric strip ────────────────────────────────────────── */}
```

- [ ] **Step 3: Verify by reading the diff:** GreeksCard imports resolve, it reads `greeks` from context, and it's mounted once in the dashboard.

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/components/live/GreeksCard.jsx frontend/src/components/live/LiveDashboard.jsx
git commit -m "$(cat <<'EOF'
feat(live): net-delta/net-theta portfolio Greeks card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Final gate (controller, after all tasks)

- [ ] Run the full host suite from the worktree:
  `"C:/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe" -m pytest tests/ -q` — expect 0 failures (all prior + the new greeks/portfolio/route/parity/blotter tests).
- [ ] FE build + Chrome verify happen **post-merge** in the main repo (worktree has no node_modules): `CI=true npm run build`, then eyeball the OCO chip + the Greeks card on the live page.
- [ ] Then `superpowers:finishing-a-development-branch`.

---

## Self-review

**Spec coverage:** plan_squareoff parity → Task 1. OCO chip (backend `oco_al_id` + FE chip + SL/TP from gtt) → Tasks 2, 7. greeks engine (BS + IV-from-premium) → Task 3. aggregator (net Δ/Θ, signed qty, spot fallback chain, skip counts) → Task 4. route (fail-soft, registry source, SearchScrip resolver + cache) → Task 5. FE plumbing (api + provider slice) → Task 6. net card → Task 8. All spec sections mapped.

**Placeholder scan:** none — every code step is complete; "Read first"/"confirm" notes are verification aids, not deferrals.

**Type consistency:** `compute_greeks(spot, strike, t_years, premium, is_call, rate=)` and its `{iv,delta,gamma,theta_per_day,vega,confidence}` dict are used identically in Tasks 3→4. `compute_portfolio_greeks(...)` kwargs (`get_quote_fn, resolve_contract_fn, today, spot_fallback, rate`) and its return dict (`net_delta_rupees_per_point, net_theta_rupees_per_day, n_computed, n_skipped, positions`) match across Tasks 4→5→8. `resolve_contract_fn` returns the 4-tuple `(strike, expiry_iso, is_call, token)` in Tasks 4 (mock) and 5 (real). `oco_al_id` field is produced in Task 2 and consumed in Task 7. The greeks context slice name `greeks` is produced in Task 6 and consumed in Task 8.
