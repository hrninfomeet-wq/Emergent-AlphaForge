"""TDD tests for the executor refactor (SECTION B):

(1) The _transmit_and_arm extraction is behavior-preserving for
    place_live_test_order (regression: place-once, reject-no-consume,
    arm-abort) — a focused regression battery layered on top of the
    existing test_live_executor.py suite.

(2) place_deployed_order — the strategy-deployed sibling — gate-by-gate:
      - Gate 0  long-only block (side != "B")
      - Gate 1  allow_fn False → not_armed
      - Gate 2  capped-lots dry-run (lots = capped_lots; ceiling cap)
      - Gate 3  margin verdict for the FULL capped_lots x lot_size size
      - Gate 5  not_within_lot_cap (over-ceiling AND qty mismatch)
      - Gate 8  RateThrottle block → rate_throttled (no place)
      - Gate 7  idempotency one-winner (claim_for_submit False → already_claimed)
      - Transmit boundary: dry-run when autoplace_armed False vs real place when True
      - arm-or-abort on a post-fill raise (no single-shot consume in this path)

(3) The grep contract: exactly ONE client.place_order call site in executor.py.

All host-only via MockNoren + the fakes from test_live_executor.py.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
# Make sibling test modules importable (no conftest in this repo) so we can
# reuse the fakes/fixtures from test_live_executor.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.live.executor import (  # noqa: E402
    place_live_test_order,
    place_deployed_order,
)
from app.live.mock_noren import MockNoren  # noqa: E402
from app.live.idempotency import IntentStore  # noqa: E402
from app.live.safety import RateThrottle  # noqa: E402

# Reuse the fakes/fixtures from the existing executor test module.
from test_live_executor import (  # noqa: E402
    FakeAsyncCollection,
    FakeEngine,
    MockNoren as _MockNorenAlias,  # noqa: F401  (sanity: same class)
    _CONTRACT,
    _GOOD_LIMITS,
    _LOT_SIZE,
    _REF_LTP,
    _BAND_PCT,
    _NIFTY_SCRIP,
    _fake_search,
    _fresh_intent_store,
    _live_test_mode_store,
)


def run(coro):
    return asyncio.run(coro)


async def _noop_arm(intent, norenordno) -> None:
    pass


# ---------------------------------------------------------------------------
# Allow-fn fakes (Gate 1 — per-deployment arm predicate)
# ---------------------------------------------------------------------------

def _allow_true():
    return (True, None)


def _allow_false():
    return (False, "disarmed")


# ---------------------------------------------------------------------------
# Intent-store stubs (claim winner / loser / raising mark_submitted)
# ---------------------------------------------------------------------------

class _ClaimFalseDeployStore:
    """record_intent accepts deployment_id; claim_for_submit always False."""

    def __init__(self):
        self.recorded: List[Any] = []

    async def record_intent(self, intent, *, mode: str = "live", deployment_id=None) -> dict:
        self.recorded.append((intent, mode, deployment_id))
        return {}

    async def claim_for_submit(self, cid: str) -> bool:
        return False

    async def mark_submitted(self, cid: str, norenordno: str) -> None:
        raise AssertionError("mark_submitted must not be reached when claim is lost")


class _RaisingMarkSubmittedDeployStore:
    """record_intent + claim succeed; mark_submitted raises (post-fill abort)."""

    async def record_intent(self, intent, *, mode: str = "live", deployment_id=None) -> dict:
        return {}

    async def claim_for_submit(self, cid: str) -> bool:
        return True

    async def mark_submitted(self, cid: str, norenordno: str) -> None:
        raise RuntimeError("DB connection lost in mark_submitted")


# ---------------------------------------------------------------------------
# place_deployed_order kwargs builder
# ---------------------------------------------------------------------------

def _dep_kwargs(**overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(
        contract=_CONTRACT,
        side="B",
        ref_ltp=_REF_LTP,
        band_pct=_BAND_PCT,
        levels={},
        capped_lots=1,
        deployment_id="dep-1",
        account_ceiling=20,
        search_fn=_fake_search,
        allow_fn=_allow_true,
        arm=_noop_arm,
        buffer_pct=0.5,
        uid="",
        actid="",
        autoplace_armed=True,  # default to TRANSMIT so most tests exercise the real path
    )
    base.update(overrides)
    return base


async def _place_dep(**overrides) -> Dict[str, Any]:
    kw = _dep_kwargs(**overrides)
    if "client" not in kw:
        kw["client"] = MockNoren(limits_data=_GOOD_LIMITS)
    if "intent_store" not in kw:
        kw["intent_store"] = _fresh_intent_store()
    if "engine" not in kw:
        kw["engine"] = FakeEngine()
    return await place_deployed_order(**kw)


def _count_orders(client: MockNoren) -> int:
    return len(run(client.order_book()))


# ===========================================================================
# (1) REGRESSION — place_live_test_order still behaves identically after the
# _transmit_and_arm extraction.
# ===========================================================================

class TestManualPathRegression:
    def test_places_exactly_once_and_arms(self):
        """All gates pass → exactly ONE place_order, arm called once, protected."""
        client = MockNoren(limits_data=_GOOD_LIMITS)
        arm_calls: List[Any] = []

        async def tracking_arm(intent, norenordno):
            arm_calls.append((intent, norenordno))

        result = run(place_live_test_order(
            contract=_CONTRACT, side="B", ref_ltp=_REF_LTP, band_pct=_BAND_PCT,
            levels={}, client=client, mode_store=_live_test_mode_store(),
            intent_store=_fresh_intent_store(), engine=FakeEngine(),
            search_fn=_fake_search, arm=tracking_arm, fat_finger_cap=1,
        ))
        assert result["placed"] is True
        assert result["protected"] is True
        assert result["norenordno"] == "MOCK1"
        book = run(client.order_book())
        assert len(book) == 1
        assert book[0]["qty"] == _LOT_SIZE
        assert len(arm_calls) == 1 and arm_calls[0][1] == "MOCK1"

    def test_reject_does_not_consume_or_arm(self):
        """Broker reject → placed=False, single-shot NOT consumed, arm NOT called."""
        client = MockNoren(limits_data=_GOOD_LIMITS)
        client.script_reject("RMS limit exceeded")
        mode_store = _live_test_mode_store()
        arm_calls: List[Any] = []

        async def tracking_arm(intent, norenordno):
            arm_calls.append(1)

        result = run(place_live_test_order(
            contract=_CONTRACT, side="B", ref_ltp=_REF_LTP, band_pct=_BAND_PCT,
            levels={}, client=client, mode_store=mode_store,
            intent_store=_fresh_intent_store(), engine=FakeEngine(),
            search_fn=_fake_search, arm=tracking_arm, fat_finger_cap=1,
        ))
        assert result["placed"] is False
        assert "reject" in result["reason"] and "RMS" in result["reason"]
        assert len(arm_calls) == 0
        assert run(mode_store.get())["single_shot_consumed"] is False

    def test_arm_failure_squares_and_halts(self):
        """arm raises → _abort_protect cancels the order + halts; placed=True/protected=False."""
        client = MockNoren(limits_data=_GOOD_LIMITS)
        engine = FakeEngine()

        async def failing_arm(intent, norenordno):
            raise RuntimeError("SL rejected")

        result = run(place_live_test_order(
            contract=_CONTRACT, side="B", ref_ltp=_REF_LTP, band_pct=_BAND_PCT,
            levels={}, client=client, mode_store=_live_test_mode_store(),
            intent_store=_fresh_intent_store(), engine=engine,
            search_fn=_fake_search, arm=failing_arm, fat_finger_cap=1,
        ))
        assert result["placed"] is True
        assert result["protected"] is False
        assert result["halted"] is True
        assert "post_place_failed" in result["reason"]
        assert "post_place_protection_failed" in engine.halt_calls
        book = run(client.order_book())
        assert [o for o in book if o.get("status") == "CANCELED"]


# ===========================================================================
# (2) place_deployed_order — gate-by-gate
# ===========================================================================

class TestDeployedGate0LongOnly:
    @pytest.mark.parametrize("side", ["S", "X", "", "b"])
    def test_non_buy_blocked(self, side):
        client = MockNoren(limits_data=_GOOD_LIMITS)
        result = run(_place_dep(client=client, side=side))
        assert result["placed"] is False
        assert result["reason"] == "side_must_be_buy"
        assert _count_orders(client) == 0


class TestDeployedGate1Authorization:
    def test_allow_fn_false_blocks(self):
        client = MockNoren(limits_data=_GOOD_LIMITS)
        result = run(_place_dep(client=client, allow_fn=_allow_false))
        assert result["placed"] is False
        assert result["reason"].startswith("not_armed")
        assert "disarmed" in result["reason"]
        assert _count_orders(client) == 0

    def test_allow_fn_false_no_reason(self):
        client = MockNoren(limits_data=_GOOD_LIMITS)
        result = run(_place_dep(client=client, allow_fn=lambda: (False, None)))
        assert result["placed"] is False
        assert result["reason"] == "not_armed"


class TestDeployedGate2And5CappedLots:
    def test_capped_lots_dry_run_qty(self):
        """capped_lots=3 → qty == 3 x lot_size; transmit places exactly that."""
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, capped_lots=3))
        assert result["placed"] is True, result
        book = run(client.order_book())
        assert len(book) == 1
        assert book[0]["qty"] == 3 * _LOT_SIZE

    def test_over_ceiling_blocks_not_within_lot_cap(self):
        """capped_lots=25 > ceiling=20 → blocked not_within_lot_cap (fat_finger
        verdict fails first in dry-run, but Gate-5 also rejects), ZERO orders."""
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, capped_lots=25, account_ceiling=20))
        assert result["placed"] is False
        # dry_run_failed (fat_finger) OR not_within_lot_cap — either way no place.
        assert result["reason"] in ("dry_run_failed", "not_within_lot_cap")
        assert _count_orders(client) == 0

    def test_qty_mismatch_blocks_not_within_lot_cap(self):
        """Gate-5 defense-in-depth: build_intent returns qty disagreeing with
        capped_lots x resolved_lot_size → not_within_lot_cap, ZERO orders."""
        from unittest.mock import patch
        from app.live.broker_protocol import OrderIntent

        fake_intent = OrderIntent(
            client_order_id="fake", trantype="B", prctyp="LMT", exch="NFO",
            tsym="NIFTY26JUN26C25000", qty=130, prc=201.0, prd="I", ret="DAY",
            remarks="fake",
        )
        all_pass = [
            {"check": "symbol", "ok": True, "detail": "ok"},
            {"check": "price_finite", "ok": True, "detail": "ok"},
            {"check": "price_band", "ok": True, "detail": "ok"},
            {"check": "fat_finger", "ok": True, "detail": "ok"},
            {"check": "jdata", "ok": True, "detail": "ok"},
        ]
        # resolved_lot_size=65; capped_lots=1 → expected qty 65, but intent.qty=130
        client = MockNoren(limits_data={"cash": "999999999"})
        with patch("app.live.executor.build_intent", return_value=(fake_intent, all_pass, 65)):
            result = run(_place_dep(client=client, capped_lots=1))
        assert result["placed"] is False
        assert result["reason"] == "not_within_lot_cap"
        assert _count_orders(client) == 0

    def test_non_numeric_ceiling_default_denies(self):
        """account_ceiling=None → fat_finger default-deny (no TypeError)."""
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, account_ceiling=None))
        assert result["placed"] is False
        assert result["reason"] in ("dry_run_failed", "not_within_lot_cap")
        assert _count_orders(client) == 0


class TestDeployedGate3Margin:
    def test_margin_uses_full_capped_size(self):
        """Margin checks the FULL capped_lots x lot_size: cash that covers 1 lot
        but NOT 5 lots must block. 5 x 65 x 200 x 1.05 = 68250 required."""
        # cash 16552.95 covers 1 lot (13650) but not 5 lots (68250)
        client = MockNoren(limits_data=_GOOD_LIMITS)
        result = run(_place_dep(client=client, capped_lots=5))
        assert result["placed"] is False
        assert result["reason"] == "dry_run_failed"
        margin_v = next((v for v in result["verdicts"] if v["check"] == "margin"), None)
        assert margin_v is not None and margin_v["ok"] is False
        assert _count_orders(client) == 0

    def test_margin_passes_when_cash_covers_full_size(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, capped_lots=5))
        assert result["placed"] is True
        margin_v = next((v for v in result["verdicts"] if v["check"] == "margin"), None)
        assert margin_v is not None and margin_v["ok"] is True


class TestDeployedGate6Engine:
    def test_cannot_trade_blocks(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        engine = FakeEngine(can_trade_result=(False, "halted"))
        result = run(_place_dep(client=client, engine=engine))
        assert result["placed"] is False
        assert "cannot_trade" in result["reason"] and "halted" in result["reason"]
        assert _count_orders(client) == 0


class TestDeployedGate8Throttle:
    def test_throttle_block_returns_rate_throttled(self):
        """An exhausted RateThrottle blocks the place with rate_throttled, ZERO orders."""
        client = MockNoren(limits_data={"cash": "999999999"})
        throttle = RateThrottle(max_per_sec=1)
        # Exhaust the single token at now=0.0 (a prior entry).
        assert throttle.allow(is_cancel=False, now=0.0) is True
        result = run(_place_dep(client=client, throttle=throttle, now=0.0))
        assert result["placed"] is False
        assert result["reason"] == "rate_throttled"
        assert _count_orders(client) == 0

    def test_throttle_allows_within_budget(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        throttle = RateThrottle(max_per_sec=9)
        result = run(_place_dep(client=client, throttle=throttle, now=0.0))
        assert result["placed"] is True
        assert _count_orders(client) == 1

    def test_no_throttle_allows(self):
        """throttle=None → throttling skipped (caller owns the bucket)."""
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, throttle=None))
        assert result["placed"] is True


class TestDeployedGate7Idempotency:
    def test_claim_loser_blocked_already_claimed(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        store = _ClaimFalseDeployStore()
        result = run(_place_dep(client=client, intent_store=store))
        assert result["placed"] is False
        assert result["reason"] == "already_claimed"
        assert _count_orders(client) == 0
        # record_intent was called with the deployment_id forwarded
        assert store.recorded and store.recorded[0][2] == "dep-1"

    def test_idempotency_one_winner(self):
        """Two concurrent attempts on the SAME cid: only one place_order succeeds.

        Drive both through a shared real IntentStore; the second claim loses.
        """
        client = MockNoren(limits_data={"cash": "999999999"})
        col = FakeAsyncCollection()
        store = IntentStore(col)
        # First placement wins and submits.
        r1 = run(_place_dep(client=client, intent_store=store))
        assert r1["placed"] is True
        # A second placement uses a fresh cid (new_client_order_id) so it also
        # wins on its own cid — that's expected. The one-winner property is
        # per-cid; we assert the store cannot double-submit the SAME cid.
        cid = r1["cid"]
        # Re-claiming the already-submitted cid must fail (SUBMITTED state).
        assert run(store.claim_for_submit(cid)) is False


class TestDeployedTransmitBoundary:
    def test_dry_run_when_autoplace_unarmed(self):
        """autoplace_armed=False → NO place_order; dry-run-log response."""
        client = MockNoren(limits_data={"cash": "999999999"})
        result = run(_place_dep(client=client, autoplace_armed=False))
        assert result["placed"] is False
        assert result["dry_run"] is True
        assert "would_send" in result
        ws = result["would_send"]
        assert ws["trantype"] == "B"
        assert ws["tsym"] == "NIFTY26JUN26C25000"
        assert ws["qty"] == _LOT_SIZE
        assert _count_orders(client) == 0
        # verdicts present and all OK (the order passed every gate; only the env
        # kill held the transmit).
        assert all(v["ok"] for v in result["verdicts"])

    def test_real_place_when_autoplace_armed(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        arm_calls: List[Any] = []

        async def tracking_arm(intent, norenordno):
            arm_calls.append((intent, norenordno))

        result = run(_place_dep(client=client, autoplace_armed=True, arm=tracking_arm))
        assert result["placed"] is True
        assert result["protected"] is True
        assert result["norenordno"] == "MOCK1"
        assert _count_orders(client) == 1
        assert len(arm_calls) == 1 and arm_calls[0][1] == "MOCK1"

    def test_env_var_read_when_override_none(self, monkeypatch):
        """autoplace_armed=None → read LIVE_AUTOPLACE_ARMED env."""
        client = MockNoren(limits_data={"cash": "999999999"})
        monkeypatch.delenv("LIVE_AUTOPLACE_ARMED", raising=False)
        r_unset = run(_place_dep(client=client, autoplace_armed=None))
        assert r_unset["placed"] is False and r_unset["dry_run"] is True
        assert _count_orders(client) == 0

        client2 = MockNoren(limits_data={"cash": "999999999"})
        monkeypatch.setenv("LIVE_AUTOPLACE_ARMED", "1")
        r_set = run(_place_dep(client=client2, autoplace_armed=None))
        assert r_set["placed"] is True
        assert _count_orders(client2) == 1


class TestDeployedRejectAndAbort:
    def test_broker_reject(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        client.script_reject("RMS")
        arm_calls: List[Any] = []

        async def tracking_arm(intent, norenordno):
            arm_calls.append(1)

        result = run(_place_dep(client=client, arm=tracking_arm))
        assert result["placed"] is False
        assert "reject" in result["reason"] and "RMS" in result["reason"]
        assert len(arm_calls) == 0
        assert _count_orders(client) == 0  # rejected order not added to book

    def test_arm_or_abort_on_post_fill_raise(self):
        """A post-fill raise (mark_submitted) → _abort_protect squares + halts;
        no single-shot consume exists on this path."""
        client = MockNoren(limits_data={"cash": "999999999"})
        engine = FakeEngine()
        result = run(_place_dep(
            client=client, engine=engine,
            intent_store=_RaisingMarkSubmittedDeployStore(),
        ))
        assert result["placed"] is True
        assert result["protected"] is False
        assert result["halted"] is True
        assert "post_place_failed" in result["reason"]
        assert "mark_submitted" in result["reason"]
        assert "post_place_protection_failed" in engine.halt_calls
        book = run(client.order_book())
        assert [o for o in book if o.get("status") == "CANCELED"], (
            "cancel_order must run when mark_submitted raises on the deployed path"
        )

    def test_arm_failure_squares_and_halts(self):
        client = MockNoren(limits_data={"cash": "999999999"})
        engine = FakeEngine()

        async def failing_arm(intent, norenordno):
            raise RuntimeError("SL rejected")

        result = run(_place_dep(client=client, engine=engine, arm=failing_arm))
        assert result["placed"] is True
        assert result["protected"] is False
        assert result["halted"] is True
        assert "SL rejected" in result["reason"]
        assert "post_place_protection_failed" in engine.halt_calls


# ===========================================================================
# (3) GREP CONTRACT — exactly ONE client.place_order call site in executor.py
# ===========================================================================

def test_executor_has_exactly_one_place_order_call_site():
    """Structural guard: there must be exactly ONE `client.place_order(` call
    site in executor.py (it lives in _transmit_and_arm — the single shared
    transmit core for BOTH place_live_test_order and place_deployed_order).

    Docstring mentions of place_order are NOT call sites, so we match the
    `client.place_order(` token specifically and exclude comment/docstring
    lines that contain it without the `await `/`= ` call form.
    """
    src = (ROOT / "backend" / "app" / "live" / "executor.py").read_text()
    # Count real call sites: `client.place_order(` preceded by `await ` (or
    # an assignment) — exclude lines inside the module docstring that merely
    # describe it. The single real call is `result = await client.place_order(`.
    call_sites = re.findall(r"await\s+client\.place_order\(", src)
    assert len(call_sites) == 1, (
        f"executor.py must have exactly ONE `await client.place_order(` call "
        f"site (the shared transmit core); found {len(call_sites)}"
    )
