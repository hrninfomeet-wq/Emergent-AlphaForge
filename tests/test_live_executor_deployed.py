import ast, pathlib
def test_executor_has_exactly_one_place_order_call_site():
    src = pathlib.Path("backend/app/live/executor.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "place_order"]
    assert len(calls) == 1, f"executor.py must have exactly ONE place_order call site, found {len(calls)}"


# ===========================================================================
# place_deployed_order — capped-lots gate chain (armed-deployment entry path)
# ===========================================================================
import asyncio  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.live.executor import place_deployed_order  # noqa: E402
from app.live.mock_noren import MockNoren  # noqa: E402
from app.live.idempotency import IntentStore  # noqa: E402

# Reuse the shared fakes / fixtures from the manual-path test module rather than
# re-deriving them (do NOT modify that module).
from tests.test_live_executor import (  # noqa: E402
    FakeAsyncCollection,
    FakeEngine,
    _CONTRACT,
    _GOOD_LIMITS,
    _LOT_SIZE,
    _REF_LTP,
    _BAND_PCT,
    _fake_search,
    _noop_arm,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Small local helpers specific to the deployed path
# ---------------------------------------------------------------------------

def _fresh_intent_store() -> IntentStore:
    return IntentStore(FakeAsyncCollection())


def _armed_allow():
    """allow_fn returning (True, 'armed')."""
    return (True, "armed")


def _not_armed_allow():
    """allow_fn returning (False, 'not_armed')."""
    return (False, "not_armed")


class _AlwaysAllowThrottle:
    """RateThrottle stand-in whose allow(...) always returns True."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def allow(self, *, is_cancel: bool, now: float) -> bool:
        self.calls.append({"is_cancel": is_cancel, "now": now})
        return True


class _AlwaysDenyThrottle:
    """RateThrottle stand-in whose allow(...) always returns False."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def allow(self, *, is_cancel: bool, now: float) -> bool:
        self.calls.append({"is_cancel": is_cancel, "now": now})
        return False


class _ClaimFalseIntentStore:
    """IntentStore stub: record_intent ok, claim_for_submit returns False."""

    def __init__(self) -> None:
        self.claims = 0

    async def record_intent(self, intent, *, mode: str = "live", deployment_id=None) -> dict:
        return {}

    async def claim_for_submit(self, cid: str) -> bool:
        self.claims += 1
        return False

    async def mark_submitted(self, cid: str, norenordno: str) -> None:
        pass


def _deployed_kwargs(**overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(
        contract=_CONTRACT,
        side="B",
        ref_ltp=_REF_LTP,
        band_pct=_BAND_PCT,
        levels={},
        capped_lots=2,
        search_fn=_fake_search,
        arm=_noop_arm,
        allow_fn=_armed_allow,
        throttle=_AlwaysAllowThrottle(),
        account_max_lots=20,
        deployment_id="dep-1",
        uid="",
        actid="",
        buffer_pct=0.5,
    )
    base.update(overrides)
    return base


async def _place_deployed(**overrides) -> Dict[str, Any]:
    kw = _deployed_kwargs(**overrides)
    if "client" not in kw:
        kw["client"] = MockNoren(limits_data=_GOOD_LIMITS)
    if "intent_store" not in kw:
        kw["intent_store"] = _fresh_intent_store()
    if "engine" not in kw:
        kw["engine"] = FakeEngine()
    return await place_deployed_order(**kw)


def _book(client: MockNoren) -> List[Dict[str, Any]]:
    return _run(client.order_book())


# --- Gate 0: long-only ------------------------------------------------------

def test_deployed_long_only_blocks_sell():
    """side='S' → side_must_be_buy, ZERO broker contact."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = _run(_place_deployed(client=client, side="S"))
    assert result["placed"] is False
    assert result["reason"] == "side_must_be_buy"
    assert _book(client) == []


# --- Gate 1: authorization --------------------------------------------------

def test_deployed_not_armed_blocks():
    """allow_fn returns (False,'not_armed') → blocked 'not_armed:not_armed', no place."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = _run(_place_deployed(client=client, allow_fn=_not_armed_allow))
    assert result["placed"] is False
    assert result["reason"] == "not_armed:not_armed"
    assert _book(client) == []


# --- Dry-run (offline-first transmit boundary) ------------------------------

def test_deployed_dry_run_when_autoplace_unset(monkeypatch):
    """capped_lots=2 + LIVE_AUTOPLACE_ARMED unset → dry_run with would_send, ZERO place_order."""
    monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
    # cash must cover the FULL 2-lot premium (200*65*2*1.05 = 27300) so the dry-run
    # reaches the transmit boundary rather than being blocked at the margin gate.
    client = MockNoren(limits_data={"cash": "99999999"})
    result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result["dry_run"] is True
    assert result["would_send"] is not None
    # would_send carries the broker jdata for the FULL 2-lot order
    assert result["would_send"]["qty"] == str(2 * _LOT_SIZE)
    # Deployed entries must be placed NRML (prd="M") so a resting OCO can attach.
    assert result["would_send"]["prd"] == "M", (
        f"deployed entry must be NRML (prd='M'), got {result['would_send'].get('prd')!r}"
    )
    assert _book(client) == [], "no place_order in dry-run"


# --- Gate 5: lot-cap defense-in-depth ---------------------------------------

def test_deployed_over_ceiling_blocks():
    """capped_lots=25 > account_max_lots=20 → not_within_lot_cap (Gate 5).

    Gate 5's ceiling clause is defense-in-depth: with a correctly-wired
    fat_finger_cap=account_max_lots, build_intent's fat-finger check already
    rejects 25>20 (→ dry_run_failed).  To prove the SEPARATE Gate-5 ceiling
    guard fires, we force build_intent to PASS (all verdicts ok, intent.qty ==
    capped*lot) so the chain reaches Gate 5, where capped_lots(25) >
    account_max_lots(20) blocks with not_within_lot_cap.
    """
    from unittest.mock import patch
    from app.live.broker_protocol import OrderIntent

    lot = _LOT_SIZE
    fake_intent = OrderIntent(
        client_order_id="fake-cid",
        trantype="B",
        prctyp="LMT",
        exch="NFO",
        tsym="NIFTY26JUN26C25000",
        qty=25 * lot,   # qty matches 25 lots → only the ceiling clause can block
        prc=201.0,
        prd="I",
        ret="DAY",
        remarks="fake-cid",
    )
    all_pass = [
        {"check": "symbol", "ok": True, "detail": "ok"},
        {"check": "price_finite", "ok": True, "detail": "ok"},
        {"check": "price_band", "ok": True, "detail": "ok"},
        {"check": "fat_finger", "ok": True, "detail": "ok"},
        {"check": "jdata", "ok": True, "detail": "ok"},
    ]
    mock_return = (fake_intent, all_pass, lot)
    client = MockNoren(limits_data={"cash": "99999999"})
    with patch("app.live.executor.build_intent", return_value=mock_return):
        result = _run(_place_deployed(client=client, capped_lots=25, account_max_lots=20))
    assert result["placed"] is False
    assert result["reason"] == "not_within_lot_cap"
    assert _book(client) == []


def test_deployed_qty_mismatch_blocks():
    """build_intent yields qty != capped*lot → not_within_lot_cap (Gate 5)."""
    from unittest.mock import patch
    from app.live.broker_protocol import OrderIntent

    # intent.qty=130 (2*65) but we will pass capped_lots=2 with resolved_lot=99 →
    # 2*99=198 != 130 → mismatch caught by Gate 5.
    fake_intent = OrderIntent(
        client_order_id="fake-cid",
        trantype="B",
        prctyp="LMT",
        exch="NFO",
        tsym="NIFTY26JUN26C25000",
        qty=130,
        prc=201.0,
        prd="I",
        ret="DAY",
        remarks="fake-cid",
    )
    all_pass = [
        {"check": "symbol", "ok": True, "detail": "ok"},
        {"check": "price_finite", "ok": True, "detail": "ok"},
        {"check": "price_band", "ok": True, "detail": "ok"},
        {"check": "fat_finger", "ok": True, "detail": "ok"},
        {"check": "jdata", "ok": True, "detail": "ok"},
    ]
    mock_return = (fake_intent, all_pass, 99)  # resolved_lot=99 → 2*99=198 != 130
    client = MockNoren(limits_data={"cash": "99999999"})
    with patch("app.live.executor.build_intent", return_value=mock_return):
        result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result["reason"] == "not_within_lot_cap"
    assert _book(client) == []


# --- Gate 3/4: margin must cover the FULL capped size -----------------------

def test_deployed_margin_full_size_blocks():
    """Thin limits vs a 2-lot premium → dry_run_failed (margin verdict fails).

    1-lot premium = 200*65*1.05 = 13650; 2-lot = 27300. cash=20000 covers 1 lot
    but NOT 2 lots → the full-size margin verdict must fail.
    """
    client = MockNoren(limits_data={"cash": "20000"})
    result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    margin_v = next((v for v in result["verdicts"] if v["check"] == "margin"), None)
    assert margin_v is not None, "margin verdict must be present"
    assert margin_v["ok"] is False
    assert _book(client) == []


# --- Gate 3 (broker margin probe): fail-CLOSED on broker reject -------------

def test_deployed_broker_margin_reject_blocks():
    """GetOrderMargin returns stat=Not_Ok (e.g. NRML not allowed) → blocked with
    NO place_order, even though local limits + cash are fine.

    Fail-CLOSED: a broker reject of the margin probe means we must not place an
    entry we can't protect.
    """
    client = MockNoren(limits_data={"cash": "99999999"})
    client.set_order_margin({"stat": "Not_Ok", "emsg": "no"})
    result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    bm = next((v for v in result["verdicts"] if v["check"] == "broker_margin"), None)
    assert bm is not None, "broker_margin verdict must be present"
    assert bm["ok"] is False
    assert _book(client) == []


def test_deployed_broker_margin_ok_does_not_block(monkeypatch):
    """GetOrderMargin returns stat=Ok with ample cash → broker_margin passes and
    the deployed path proceeds (here to the dry-run boundary)."""
    monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
    client = MockNoren(limits_data={"cash": "99999999"})
    client.set_order_margin({"stat": "Ok", "cash": "99999", "marginused": "100"})
    result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result.get("dry_run") is True
    bm = next((v for v in result["verdicts"] if v["check"] == "broker_margin"), None)
    assert bm is not None, "broker_margin verdict must be present"
    assert bm["ok"] is True
    assert _book(client) == [], "dry-run transmits nothing"


def test_deployed_broker_margin_unavailable_fail_open(monkeypatch):
    """No order_margin fixture set → order_margin returns {} → broker_margin
    fail-OPEN (ok True) so a transient probe hiccup does NOT block all trading.

    This is the default MockNoren path used by all the OTHER deployed tests; it
    must remain unblocking (the local margin floor still guards affordability).
    """
    monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
    client = MockNoren(limits_data={"cash": "99999999"})  # no set_order_margin → {}
    result = _run(_place_deployed(client=client, capped_lots=2))
    assert result["placed"] is False
    assert result.get("dry_run") is True
    bm = next((v for v in result["verdicts"] if v["check"] == "broker_margin"), None)
    assert bm is not None
    assert bm["ok"] is True


# --- Gate 8: throttle -------------------------------------------------------

def test_deployed_throttle_blocks(monkeypatch):
    """throttle.allow(...) returns False + LIVE_AUTOPLACE_ARMED=1 → rate_throttled, no claim/place."""
    # Gate 8 only runs on the armed (real-transmit) path.  Without setting
    # LIVE_AUTOPLACE_ARMED the function would return dry_run=True before ever
    # consulting the throttle; set it so the gate is reachable.
    monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
    client = MockNoren(limits_data={"cash": "99999999"})
    intent_store = _ClaimFalseIntentStore()  # would block at claim — must NOT be reached
    throttle = _AlwaysDenyThrottle()
    result = _run(_place_deployed(
        client=client,
        capped_lots=2,
        throttle=throttle,
        intent_store=intent_store,
    ))
    assert result["placed"] is False
    assert result["reason"] == "rate_throttled"
    assert len(throttle.calls) == 1
    assert throttle.calls[0]["is_cancel"] is False
    assert intent_store.claims == 0, "no claim before the throttle gate passes"
    assert _book(client) == []


def test_deployed_dry_run_ignores_deny_throttle(monkeypatch):
    """A deny throttle must NOT be consulted when LIVE_AUTOPLACE_ARMED is unset (dry-run path)."""
    monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
    client = MockNoren(limits_data={"cash": "99999999"})
    throttle = _AlwaysDenyThrottle()
    result = _run(_place_deployed(client=client, capped_lots=2, throttle=throttle))
    # The function must return a dry-run response — throttle must not have been consulted
    assert result["placed"] is False
    assert result.get("dry_run") is True, "expected dry_run=True, throttle must not have blocked"
    assert len(throttle.calls) == 0, "throttle.allow() must NOT be called on the dry-run path"


# --- Idempotency: one winner ------------------------------------------------

def test_deployed_already_claimed(monkeypatch):
    """claim_for_submit returns False (2nd winner) → already_claimed, with autoplace armed."""
    monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
    client = MockNoren(limits_data={"cash": "99999999"})
    intent_store = _ClaimFalseIntentStore()
    result = _run(_place_deployed(client=client, capped_lots=2, intent_store=intent_store))
    assert result["placed"] is False
    assert result["reason"] == "already_claimed"
    assert intent_store.claims == 1
    assert _book(client) == [], "claim lost → no place_order"


# --- Armed transmit: places exactly once ------------------------------------

def test_deployed_armed_places_once(monkeypatch):
    """LIVE_AUTOPLACE_ARMED=1 → placed True, protected True, arm once, exactly one place_order."""
    monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
    client = MockNoren(limits_data={"cash": "99999999"})
    engine = FakeEngine()
    arm_calls: List[Any] = []

    async def tracking_arm(intent, norenordno):
        arm_calls.append((intent, norenordno))

    result = _run(_place_deployed(
        client=client,
        capped_lots=2,
        engine=engine,
        arm=tracking_arm,
    ))

    assert result["placed"] is True
    assert result["protected"] is True
    assert result["norenordno"] == "MOCK1"
    book = _book(client)
    assert len(book) == 1, f"expected exactly 1 order, got {len(book)}"
    assert book[0]["qty"] == 2 * _LOT_SIZE
    assert book[0]["trantype"] == "B"
    assert len(arm_calls) == 1
    assert arm_calls[0][1] == "MOCK1"


# --- Post-fill raise → abort-protect ---------------------------------------

def test_deployed_arm_raises_aborts(monkeypatch):
    """arm raises (env set) → _abort_protect: placed True, protected False, halted True."""
    monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
    client = MockNoren(limits_data={"cash": "99999999"})
    engine = FakeEngine()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("SL backstop rejected")

    result = _run(_place_deployed(
        client=client,
        capped_lots=2,
        engine=engine,
        arm=failing_arm,
    ))

    assert result["placed"] is True
    assert result["protected"] is False
    assert result["halted"] is True
    assert "post_place_failed" in result["reason"]
    assert "SL backstop rejected" in result["reason"]
    assert "post_place_protection_failed" in engine.halt_calls
    canceled = [o for o in _book(client) if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1, "entry must be squared when arm fails"
