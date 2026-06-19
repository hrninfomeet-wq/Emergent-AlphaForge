# Paper-trade sizing: locked replay of source-run sizing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a paper deployment size lots by replaying its source backtest/optimizer run's sizing policy (pinned at create, recomputed live per signal) instead of a fixed `default_lots`, so forward P&L scales like the backtest.

**Architecture:** At deploy create, a pure deriver extracts the source run's `sizing_config` + `lots` and `build_deployment_doc` pins them (immutable) under `deployment.risk.sizing`. Live, `build_auto_trade` calls a new `resolve_deployment_lots` helper that runs `size_position()` with the pinned policy, the live premium, the live contract `lot_size`, and the computed premium stop — falling back to `default_lots` for legacy deployments. The preset deriver is extended so premium-at-risk survives a preset save; the deploy wizard shows a read-only sizing summary.

**Tech Stack:** Python 3.12 / FastAPI backend (`backend/app`), pytest (flat `tests/`, no conftest, `FakeDB` stubs), React (CRA + craco) frontend (`frontend/src`, no JS unit tests).

---

## Conventions (read once before starting)

- **Run backend tests from the repo root** (`C:\Users\haroo\OneDrive\Documents\New project\Emergent-AlphaForge`) with the `.venv` active:
  - One file: `python -m pytest tests/test_strategy_deployments.py -q`
  - Full suite: `python -m pytest tests -q`
  - If `python` is not the venv interpreter, prefix with the venv: `.venv/Scripts/python -m pytest tests -q`
- **No conftest / no markers.** Each test file bootstraps `sys.path` to `backend` itself; `app.*` imports get a trailing `# noqa: E402`. The target test files already do this — just add functions.
- **Fixtures are module-level factory functions**, not `@pytest.fixture`. `test_paper_auto.py` already defines `make_confirmed_signal(...)`, `make_paper_deployment(**risk)`, and `FakeDB`/`FakeCollection`. Reuse them.
- **Async tests** use `@pytest.mark.asyncio` + `async def`; pure-logic tests are plain `def`.
- **Commit trailer:** end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/strategy_deployments.py` | Modify | Add pure `deployment_sizing_from_source(...)`; call it in `build_deployment_doc` to pin `risk.sizing`. |
| `backend/app/preset_execution.py` | Modify | `execution_from_option_config` carries `sizing_config` so presets keep the policy. |
| `backend/app/paper_auto.py` | Modify | Add `resolve_deployment_lots(...)`; rewire `build_auto_trade` to size via the pin; stamp sizing audit; add `sizing_mode` to the auto_paper snapshot. |
| `frontend/src/pages/LiveSignals.jsx` | Modify | Replace editable "Lots per trade" input with a read-only sizing summary + a visible fallback note. |
| `tests/test_strategy_deployments.py` | Modify | Deriver + pin tests. |
| `tests/test_preset_execution.py` | Modify | `sizing_config` carried/omitted tests. |
| `tests/test_paper_auto.py` | Modify | `resolve_deployment_lots`, `build_auto_trade`, async snapshot tests. |
| `CHANGELOG.md` | Modify | One entry for the slice. |

`backend/app/paper_trading.py` is intentionally **not** changed — sizing audit fields are stamped in `build_auto_trade`, keeping `paper_trade_from_signal` untouched.

---

### Task 1: Source sizing deriver

Pure function that extracts `{sizing_config, lots, source_id}` from a `backtest_run` or `preset` source doc, or `None` when the source carries no sizing config.

**Files:**
- Modify: `backend/app/strategy_deployments.py` (add function after `_params`, ~line 64)
- Test: `tests/test_strategy_deployments.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy_deployments.py`. Update the existing import of `build_deployment_doc` to also import the new function:

```python
from app.strategy_deployments import build_deployment_doc, deployment_sizing_from_source  # noqa: E402


def test_deployment_sizing_from_backtest_run_extracts_policy():
    run = {
        "id": "run-1",
        "option_backtest": {
            "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                              "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
            "request": {"lots": 2},
        },
    }
    pin = deployment_sizing_from_source("backtest_run", run)
    assert pin is not None
    assert pin["sizing_config"]["enabled"] is True
    assert pin["sizing_config"]["mode"] == "premium_at_risk"
    assert pin["sizing_config"]["capital"] == 200_000
    assert pin["lots"] == 2
    assert pin["source_id"] == "run-1"


def test_deployment_sizing_from_preset_extracts_policy():
    preset = {"name": "p1", "config": {"execution": {
        "lots": 3,
        "sizing_config": {"enabled": False, "mode": "fixed_lots", "fixed_lots": 3, "max_lots": 10},
    }}}
    pin = deployment_sizing_from_source("preset", preset)
    assert pin is not None
    assert pin["sizing_config"]["enabled"] is False
    assert pin["lots"] == 3
    assert pin["source_id"] == "p1"


def test_deployment_sizing_none_when_preset_has_no_sizing_config():
    preset = {"name": "old", "config": {"execution": {"lots": 5}}}  # legacy preset
    assert deployment_sizing_from_source("preset", preset) is None


def test_deployment_sizing_none_for_spot_only_or_unknown():
    assert deployment_sizing_from_source("backtest_run", {"id": "r"}) is None
    assert deployment_sizing_from_source("weird", {}) is None


def test_deployment_sizing_defaults_lots_to_one_when_absent():
    run = {"id": "r2", "option_backtest": {
        "sizing_config": {"enabled": True, "mode": "premium_at_risk"}}}  # no request
    pin = deployment_sizing_from_source("backtest_run", run)
    assert pin is not None
    assert pin["lots"] == 1


def test_deployment_sizing_tolerates_non_numeric_preset_lots():
    preset = {"name": "p", "config": {"execution": {
        "lots": "abc",  # corrupted/hand-edited
        "sizing_config": {"enabled": False, "mode": "fixed_lots"}}}}
    pin = deployment_sizing_from_source("preset", preset)
    assert pin is not None
    assert pin["lots"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_strategy_deployments.py -q`
Expected: FAIL — `ImportError: cannot import name 'deployment_sizing_from_source'`.

- [ ] **Step 3: Implement the deriver**

In `backend/app/strategy_deployments.py`, add immediately after the `_params` helper (after line 64):

```python
def deployment_sizing_from_source(
    source_type: str, source_doc: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Extract the source run's position-sizing policy so a deployment can pin it
    and replay it live. Returns {"sizing_config", "lots", "source_id"} or None when
    the source carries no sizing config (→ live falls back to default_lots).

    - backtest_run: the resolved option-sim sizing_config (always present for an
      option run) + the run's fixed `lots` from its request.
    - preset: the execution block's sizing_config (present only when the preset was
      saved with one) + the execution `lots` scalar.
    """
    from app.portfolio import SizingConfig

    st = str(source_type or "").lower()
    if st == "backtest_run":
        ob = source_doc.get("option_backtest") or {}
        sizing_config = ob.get("sizing_config")
        lots = (ob.get("request") or {}).get("lots")
    elif st == "preset":
        ex = (source_doc.get("config") or {}).get("execution") or {}
        sizing_config = ex.get("sizing_config")
        lots = ex.get("lots")
    else:
        return None
    if not isinstance(sizing_config, dict):
        return None
    try:
        lots_n = int(lots or 1)
    except (TypeError, ValueError):
        lots_n = 1  # tolerate a hand-edited/imported preset with non-numeric lots
    return {
        "sizing_config": SizingConfig.from_dict(sizing_config).to_dict(),
        "lots": max(1, lots_n),
        "source_id": _source_id(st, source_doc),
    }
```

`Optional` is already imported (line 6). The `SizingConfig` import is lazy to avoid pulling numpy at module load and to dodge import cycles. `lots` is coerced defensively because a preset's `execution.lots` is a raw, possibly hand-edited value (a `backtest_run` is pydantic-validated, so it is always numeric).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_strategy_deployments.py -q`
Expected: PASS (all four new tests, plus the file's existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategy_deployments.py tests/test_strategy_deployments.py
git commit -m "feat(deploy): deriver to extract source-run sizing policy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Carry `sizing_config` into the preset execution block

`execution_from_option_config` currently drops `sizing_config` (only `lots` survives), so premium-at-risk can never replay from a preset. Carry it when present.

**Files:**
- Modify: `backend/app/preset_execution.py` (before `return execution`, ~line 60)
- Test: `tests/test_preset_execution.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_preset_execution.py`:

```python
def test_execution_carries_sizing_config_when_present():
    ex = execution_from_option_config({
        "moneyness": "atm", "exit_mode": "spot_exit", "lots": 1,
        "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                          "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
    })
    assert ex["sizing_config"]["enabled"] is True
    assert ex["sizing_config"]["mode"] == "premium_at_risk"
    assert ex["sizing_config"]["capital"] == 200_000
    assert ex["sizing_config"]["max_lots"] == 10


def test_execution_omits_sizing_config_when_absent():
    ex = execution_from_option_config({"moneyness": "atm", "exit_mode": "spot_exit", "lots": 1})
    assert "sizing_config" not in ex
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_preset_execution.py -q`
Expected: FAIL — `KeyError: 'sizing_config'` in the first test.

- [ ] **Step 3: Implement**

In `backend/app/preset_execution.py`, add just before `return execution` (after the `daily_caps` block at line 59):

```python
    sizing_config = option_cfg.get("sizing_config")
    if isinstance(sizing_config, dict):
        from app.portfolio import SizingConfig
        execution["sizing_config"] = SizingConfig.from_dict(sizing_config).to_dict()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_preset_execution.py -q`
Expected: PASS (both new tests + existing tests green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/preset_execution.py tests/test_preset_execution.py
git commit -m "feat(preset): carry sizing_config in execution block

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Pin the sizing policy on the deployment doc

`build_deployment_doc` calls the deriver and writes the result under `risk.sizing`. No router change needed (the source doc is already in scope and the API has no route that later mutates `risk`).

**Files:**
- Modify: `backend/app/strategy_deployments.py` (`build_deployment_doc`, ~lines 116 and 148)
- Test: `tests/test_strategy_deployments.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_strategy_deployments.py`:

```python
def test_build_deployment_pins_sizing_from_source():
    run = {
        "id": "run-9", "strategy_id": "s", "instrument": "NIFTY",
        "config": {"strategy_id": "s", "instrument": "NIFTY", "params": {}},
        "option_backtest": {
            "sizing_config": {"enabled": True, "mode": "premium_at_risk",
                              "capital": 200_000, "risk_per_trade_pct": 1.0, "max_lots": 10},
            "request": {"lots": 2},
        },
    }
    doc = build_deployment_doc(source_type="backtest_run", source_doc=run, name="d", mode="paper")
    assert doc["risk"]["sizing"]["sizing_config"]["enabled"] is True
    assert doc["risk"]["sizing"]["sizing_config"]["mode"] == "premium_at_risk"
    assert doc["risk"]["sizing"]["lots"] == 2


def test_build_deployment_no_sizing_when_source_lacks_it():
    preset = {"name": "old", "config": {"instrument": "NIFTY", "strategy_id": "s",
              "params": {}, "execution": {"lots": 5}}}
    doc = build_deployment_doc(source_type="preset", source_doc=preset, name="d", mode="paper")
    assert "sizing" not in doc["risk"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_strategy_deployments.py -q`
Expected: FAIL — `KeyError: 'sizing'` in `test_build_deployment_pins_sizing_from_source`.

- [ ] **Step 3: Implement**

In `backend/app/strategy_deployments.py`, inside `build_deployment_doc`, add the pin computation just before the `return {` (after the `dte_values` loop, ~line 116):

```python
    sizing_pin = deployment_sizing_from_source(source_type, source_doc)
```

Then change the `"risk"` line (currently line 148) from:

```python
        "risk": {**(risk or {}), "allow_overnight": bool(allow_overnight)},
```

to:

```python
        "risk": {
            **(risk or {}),
            "allow_overnight": bool(allow_overnight),
            **({"sizing": sizing_pin} if sizing_pin else {}),
        },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_strategy_deployments.py -q`
Expected: PASS (new tests + all existing `build_deployment_doc` tests still green — the pin is additive and only appears when a source carries sizing).

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategy_deployments.py tests/test_strategy_deployments.py
git commit -m "feat(deploy): pin source-run sizing onto deployment.risk.sizing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Live replay helper + rewire `build_auto_trade`

Add `resolve_deployment_lots` (pure) and have `build_auto_trade` size via the pin instead of `default_lots`, stamping sizing audit onto the trade.

**Files:**
- Modify: `backend/app/paper_auto.py` (add helper before `build_auto_trade`; edit `build_auto_trade` lines 288-324)
- Test: `tests/test_paper_auto.py`

- [ ] **Step 1: Write the failing tests (pure helper)**

Add to `tests/test_paper_auto.py`. The file has **two** `from app.paper_auto import (...)` blocks — edit the **first** one (top of file, ~line 25), NOT the second (~line 402). Add only the two new names (`build_auto_trade`, `resolve_deployment_lots`) to the five already there, so the block reads in full:

```python
from app.paper_auto import (  # noqa: E402
    auto_paper_enabled,
    auto_paper_trade_for_signal,
    build_auto_trade,
    compute_auto_risk_levels,
    mark_open_deployment_trades,
    resolve_deployment_lots,
    resolve_option_entry_price,
)
```

Do **not** drop the other names — `auto_paper_enabled` / `compute_auto_risk_levels` / `mark_open_deployment_trades` / `resolve_option_entry_price` are used by existing tests and removing them would break ~15 of them with `NameError`. Then add:

```python
def test_resolve_lots_premium_at_risk_matches_size_position():
    from app.portfolio import SizingConfig, size_position
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 5.0, "max_lots": 10}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    # budget 200000*5% = 10000; entry 100, stop 70 -> risk/unit 30;
    # lot_size 75 -> per-lot 2250 -> floor(10000/2250) = 4 lots.
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, 70.0)
    expected = size_position(entry_premium=100.0, lot_size=75, stop_level=70.0,
                             cfg=SizingConfig.from_dict(sizing))
    assert lots == 4
    assert lots == int(expected["lots"])
    assert audit["sizing_mode"] == "premium_at_risk"
    assert audit["risk_exceeded"] is False


def test_resolve_lots_adapts_to_contract_lot_size():
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    risk_cfg = {"sizing": {"sizing_config": sizing, "lots": 1}}
    # budget 2000; risk/unit 30.
    # NIFTY lot 75 -> per-lot 2250 -> floor 0 -> min 1 lot (risk_exceeded).
    nifty_lots, nifty_audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, 70.0)
    # BANKNIFTY lot 15 -> per-lot 450 -> floor(2000/450) = 4 lots.
    bn_lots, _ = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 15}, 70.0)
    assert nifty_lots == 1
    assert nifty_audit["risk_exceeded"] is True
    assert bn_lots == 4


def test_resolve_lots_fixed_lots_pin():
    risk_cfg = {"sizing": {"sizing_config": {"enabled": False, "mode": "fixed_lots"}, "lots": 3}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 3
    assert audit["sizing_mode"] == "fixed_lots"


def test_resolve_lots_legacy_fallback_to_default_lots():
    risk_cfg = {"default_lots": 2}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 2
    assert audit["sizing_mode"] == "fixed_lots_legacy"


def test_resolve_lots_pin_without_sizing_config_uses_pin_lots():
    # A malformed pin (sizing present but sizing_config dropped) must honour the
    # pin's own lots, not silently fall back to default_lots.
    risk_cfg = {"sizing": {"lots": 2}}
    lots, audit = resolve_deployment_lots(risk_cfg, 100.0, {"lot_size": 75}, None)
    assert lots == 2
    assert audit["sizing_mode"] == "fixed_lots"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_paper_auto.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_deployment_lots'`.

- [ ] **Step 3: Implement the helper**

In `backend/app/paper_auto.py`, add this function immediately **before** `def build_auto_trade(` (before line 279):

```python
def resolve_deployment_lots(
    risk_cfg: Dict[str, Any],
    fill_entry: float,
    contract: Dict[str, Any],
    stop_price: Optional[float],
) -> Tuple[int, Dict[str, Any]]:
    """Lots for a live auto-paper trade, replaying the source run's pinned sizing
    policy (deployment.risk.sizing). Falls back to deployment.risk.default_lots
    when no policy was pinned (legacy deployments). Returns (lots, audit) where
    audit carries sizing_mode and, for premium_at_risk, the per-unit risk fields.

    lot_size always comes from the live contract; only the lot COUNT is sized — so
    SENSEX/BANKNIFTY (different lot_size) adapt automatically while rupee risk is
    held constant, exactly as the backtest does.
    """
    from app.portfolio import SizingConfig, size_position

    lot_size = max(1, int((contract or {}).get("lot_size") or 1))
    pin = (risk_cfg or {}).get("sizing") or {}
    sizing_config = pin.get("sizing_config")
    if isinstance(sizing_config, dict):
        cfg = SizingConfig.from_dict(sizing_config)
        if cfg.enabled:
            sized = size_position(
                entry_premium=float(fill_entry), lot_size=lot_size,
                stop_level=stop_price, cfg=cfg,
            )
            return int(sized["lots"]), {
                "sizing_mode": sized.get("sizing_mode"),
                "risk_per_unit": sized.get("risk_per_unit"),
                "risk_amount": sized.get("risk_amount"),
                "risk_exceeded": sized.get("risk_exceeded"),
            }
        return max(1, int(pin.get("lots") or 1)), {"sizing_mode": "fixed_lots"}
    if pin:
        # Pin present but sizing_config malformed/absent (only possible via DB
        # corruption — the deriver always co-writes both). Honour the pin's own
        # lot count rather than silently dropping to the deployment default.
        return max(1, int(pin.get("lots") or 1)), {"sizing_mode": "fixed_lots"}
    return max(1, int((risk_cfg or {}).get("default_lots") or 1)), {"sizing_mode": "fixed_lots_legacy"}
```

`Tuple` is already imported (line 60). The returned `audit` is a **curated subset** of `size_position`'s dict — it keeps the four risk fields (`sizing_mode`, `risk_per_unit`, `risk_amount`, `risk_exceeded`) and drops `lots` (returned separately). The non-premium branches return only `sizing_mode`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_paper_auto.py -q`
Expected: PASS for the four helper tests.

- [ ] **Step 5: Rewire `build_auto_trade`**

In `backend/app/paper_auto.py`, in `build_auto_trade`:

(a) **Delete** the early lots line (line 289):

```python
    lots = max(1, int(risk_cfg.get("default_lots") or 1))
```

(Leave `risk_cfg = deployment.get("risk") or {}` on line 288 in place.)

(b) **After** the `compute_auto_risk_levels(...)` call (which ends at line 312), and **before** the `paper_trade_from_signal(...)` call, insert:

```python
    lots, sizing_audit = resolve_deployment_lots(risk_cfg, fill_entry, contract, stop_price)
```

(c) **After** the friction entry-slippage stamping block (after line 324, where `trade["entry_spread_pts"]` is set), insert the audit stamp:

```python
    for _k, _v in sizing_audit.items():
        if _v is not None:
            trade[_k] = _v
```

The `paper_trade_from_signal(signal_doc, lots=lots, ...)` call is unchanged — it still receives `lots`, now from the policy.

- [ ] **Step 6: Write the failing `build_auto_trade` integration tests**

Add to `tests/test_paper_auto.py`:

```python
def test_build_auto_trade_replays_premium_at_risk_policy():
    sig = make_confirmed_signal(lot_size=15)  # BANKNIFTY-like contract lot
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    deployment = make_paper_deployment(
        sizing={"sizing_config": sizing, "lots": 1},
        auto_paper_stop_pct=30,  # premium stop 30% below entry -> stop 70 on entry 100
    )
    trade = build_auto_trade(sig, deployment, entry_price=100.0)
    # entry 100, stop 70 -> risk/unit 30; budget 2000; per-lot 30*15=450 -> 4 lots
    assert trade["lots"] == 4
    assert trade["quantity"] == 4 * 15
    assert trade["sizing_mode"] == "premium_at_risk"
    assert trade["risk"]["stop_price"] == 70.0


def test_build_auto_trade_legacy_uses_default_lots():
    sig = make_confirmed_signal(lot_size=75)
    deployment = make_paper_deployment(default_lots=2)  # no risk.sizing pinned
    trade = build_auto_trade(sig, deployment, entry_price=120.0)
    assert trade["lots"] == 2
    assert trade["sizing_mode"] == "fixed_lots_legacy"


def test_build_auto_trade_tags_risk_exceeded_on_trade_doc():
    # Spec edge case: when one lot exceeds the risk budget, still trade one lot
    # and tag risk_exceeded=True ON THE TRADE DOC (not just the helper audit).
    sig = make_confirmed_signal(lot_size=75)  # NIFTY lot — one lot blows the budget
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 50}
    deployment = make_paper_deployment(
        sizing={"sizing_config": sizing, "lots": 1}, auto_paper_stop_pct=30)
    trade = build_auto_trade(sig, deployment, entry_price=100.0)
    # budget 2000; risk/unit 30; per-lot 30*75=2250 -> floor 0 -> 1 lot, exceeded
    assert trade["lots"] == 1
    assert trade["risk_exceeded"] is True
    assert trade["sizing_mode"] == "premium_at_risk"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_paper_auto.py -q`
Expected: PASS for the two integration tests and all four helper tests; the file's existing 35 tests still green (legacy deployments without `risk.sizing` keep using `default_lots`, so nothing regresses).

- [ ] **Step 8: Commit**

```bash
git add backend/app/paper_auto.py tests/test_paper_auto.py
git commit -m "feat(paper): replay pinned sizing policy live in build_auto_trade

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Surface the sizing audit fields in the auto_paper signal snapshot

So the signal's audit snapshot records how lots were chosen, end-to-end through the DB path — with full parity to the backtest trade rows (spec Unit 4: all four audit fields into the snapshot, not just `sizing_mode`).

**Files:**
- Modify: `backend/app/paper_auto.py` (`auto_paper_trade_for_signal` snapshot, lines 414-424)
- Test: `tests/test_paper_auto.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_paper_auto.py`:

```python
@pytest.mark.asyncio
async def test_auto_trade_snapshot_carries_sizing_audit():
    db = FakeDB()
    sig = make_confirmed_signal()  # default contract lot_size 75
    db.signals.rows.append(dict(sig))
    sizing = {"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
              "risk_per_trade_pct": 1.0, "max_lots": 10}
    deployment = make_paper_deployment(sizing={"sizing_config": sizing, "lots": 1})

    res = await auto_paper_trade_for_signal(
        db, deployment, sig, latest_tick_lookup={KEY: {"last_price": 100.0}}.get)

    assert res["created"] is True
    trade = db.paper_trades.rows[0]
    assert trade["sizing_mode"] == "premium_at_risk"
    # entry 100, no premium stop -> assumed 50% -> risk/unit 50; lot 75 -> per-lot
    # 3750; budget 2000 -> floor 0 -> 1 lot, risk_exceeded.
    snap = db.signals.rows[0]["auto_paper"]
    assert snap["sizing_mode"] == "premium_at_risk"
    assert snap["risk_exceeded"] is True
    assert snap["risk_per_unit"] == 50.0
    assert "risk_amount" in snap
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_paper_auto.py::test_auto_trade_snapshot_carries_sizing_audit -q`
Expected: FAIL — `KeyError: 'sizing_mode'` on the `snap` (signal snapshot) assertion (the trade already carries the audit fields from Task 4; the snapshot does not yet).

- [ ] **Step 3: Implement**

In `backend/app/paper_auto.py`, in `auto_paper_trade_for_signal`, add four lines to the `snapshot = {"auto_paper": {...}}` dict (after the `"lots": trade.get("lots"),` line, ~line 418):

```python
        "sizing_mode": trade.get("sizing_mode"),
        "risk_per_unit": trade.get("risk_per_unit"),
        "risk_amount": trade.get("risk_amount"),
        "risk_exceeded": trade.get("risk_exceeded"),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_paper_auto.py -q`
Expected: PASS (new test + all prior paper_auto tests green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/paper_auto.py tests/test_paper_auto.py
git commit -m "feat(paper): record sizing audit fields in auto_paper signal snapshot

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Read-only sizing summary in the deploy wizard

Replace the editable "Lots per trade" input with a read-only summary derived from the source's sizing, plus a visible fallback note when the source predates policy capture. `form.default_lots` is kept (the create payload still reads it as the legacy fallback).

**Files:**
- Modify: `frontend/src/pages/LiveSignals.jsx` (derive summary ~line 372; replace input lines 600-605; add note after line 606)

There are no JS unit tests in this repo; verification is a production build + a manual check.

- [ ] **Step 1: Add the derived summary**

In `frontend/src/pages/LiveSignals.jsx`, just after `const instrument = (preset?.config?.instrument || "").toUpperCase();` (line 372), add:

```jsx
  const execSizing = preset?.config?.execution?.sizing_config;
  const sizingSummary = execSizing?.enabled
    ? `premium-at-risk · ${execSizing.risk_per_trade_pct ?? 1}% · ₹${fmtNum(execSizing.capital ?? 200000, 0)} · max ${execSizing.max_lots ?? 10}`
    : execSizing
      ? `fixed ${preset?.config?.execution?.lots ?? form.default_lots} lots`
      : `fixed ${form.default_lots} lots`;
```

`fmtNum` is already imported (line 9). Pass `0` decimals so capital reads `₹200,000`, not `₹200,000.00` (matches the app's whole-rupee convention). When `preset`/`execution`/`sizing_config` is absent, `execSizing` is `undefined` → the summary is `fixed N lots` (no throw, no NaN — the `??` defaults cover missing fields).

- [ ] **Step 2: Replace the editable Lots field**

Replace the third `<label>` in the grid (lines 600-605, the "Lots per trade" `Input`):

```jsx
                <label className="block text-[11px] text-dim">
                  Lots per trade
                  <Input type="number" min="1" step="1" value={form.default_lots}
                    onChange={(e) => set("default_lots", e.target.value)} className="mt-1 bg-bg-2 border-line h-8"
                    title="Lot size always comes from the option contract (Upstox)." />
                </label>
```

with:

```jsx
                <label className="block text-[11px] text-dim">
                  Sizing
                  <div className="mt-1 h-8 flex items-center overflow-hidden whitespace-nowrap rounded-md border border-line bg-bg-2 px-2 text-[11px] text-dim"
                    title="Lots replay the source run's sizing policy; the capital shown is the backtest notional used for comparability, not a live balance. Lot size comes from the option contract.">
                    {sizingSummary}
                  </div>
                </label>
```

- [ ] **Step 3: Add the visible fallback note**

Immediately after the closing `</div>` of the `grid grid-cols-3` row (after line 606), add:

```jsx
              {!execSizing && (
                <div className="mt-1 text-[10px] leading-snug text-dimmer">
                  Fixed {form.default_lots} lots — this source predates sizing-policy capture; re-save the preset
                  (or deploy from the backtest run) to inherit premium-at-risk sizing.
                </div>
              )}
```

- [ ] **Step 4: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build completes with no errors referencing `LiveSignals.jsx` (`sizingSummary`/`execSizing` resolve; no unused-var or undefined errors). If the build is impractical offline, instead run the dev server (`cd frontend && npm start`) and confirm the wizard renders.

- [ ] **Step 5: Manual verification**

Open the deploy wizard (Live Signals → New deployment):
- Selecting a preset **with** `execution.sizing_config.enabled` shows `Sizing: risk N% · ₹… · max M`.
- Selecting a preset saved **with** a fixed-lots sizing_config shows `fixed N lots`.
- Selecting a legacy preset (no `sizing_config`) shows `fixed N lots` **and** the dim fallback note.
- The field is read-only (no number input); the Create button still works.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/LiveSignals.jsx
git commit -m "feat(wizard): read-only sizing summary with visible fallback note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full-suite regression + changelog

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full backend suite**

Run: `python -m pytest tests -q`
Expected: PASS — all previously-passing tests (≈796) plus the new tests from Tasks 1-5. Investigate any failure before proceeding; legacy deployments must be unaffected (no `risk.sizing` → `default_lots` path).

- [ ] **Step 2: Add a CHANGELOG entry**

Open `CHANGELOG.md`, find the latest version heading, and add a new entry **matching that file's existing style** (mirror the most recent bullet's format/indentation). Content:

```
- Paper deployments now replay the source run's sizing policy instead of a fixed
  `default_lots`: the run's `sizing_config` + `lots` are pinned (immutable) on
  `deployment.risk.sizing` at create, and `build_auto_trade` recomputes lots per
  signal via `size_position()` (premium-at-risk → lots scale with capital/risk%/
  the option stop; lot_size from the live contract so SENSEX/BANKNIFTY adapt).
  Legacy deployments and pre-capture presets fall back to `default_lots`, shown
  explicitly in the deploy wizard's read-only sizing summary.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): paper-deployment sizing replay

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes & guardrails

- **Comparability is the win, not profit.** This makes the forward test match the backtest's sizing; it does not change the underlying edge (the certifiable option-buying config is still net-negative).
- **Capital basis = the run's notional.** `size_position` uses `cfg.capital` from the pinned config (e.g. ₹200k), not a live account balance — by design, for comparability.
- **No new schema field required.** `DeploymentCreateReq.risk` is a free-form dict and the pin is written server-side in `build_deployment_doc`; no API route mutates `risk` after creation, so the pin is effectively write-once.
- **Backtest_run vs preset asymmetry (expected).** A `backtest_run` source always carries a resolved `sizing_config`, so it pins immediately. A `preset` pins only when it was saved after Task 2 with a sizing_config — otherwise it falls back visibly. This is the agreed "visible fallback, no backfill" behavior.
