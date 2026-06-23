"""Tests for validate_and_build — the live order CHOKE-POINT (P1.3).

Every order (direct ticket OR strategy-deployed) flows through validate_and_build
so exchange rules, tick-rounding, freeze-split, and product-pinning can never be
bypassed.  A canned NIFTY CE scrip row makes symbol resolution deterministic with
no network call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.broker_protocol import ALLOWED_PRCTYP, OrderIntent  # noqa: E402
from app.live.order_builder import validate_and_build  # noqa: E402
from app.live.safety import validate_jdata  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — canned NIFTY 26JUN26 25000 CE scrip (broker ls=65, tick 0.05)
# ---------------------------------------------------------------------------
_NIFTY_SCRIP = {
    "tsym": "NIFTY26JUN26C25000",
    "token": "1",
    "ls": "65",
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "26-JUN-2026",
    "dname": "NIFTY 26JUN26 25000 CE",
    "ti": "0.05",
}


def _fake_search(exch, query):
    """Return the canned NIFTY scrip unconditionally (sync, no network)."""
    return [_NIFTY_SCRIP]


def _ticket(**kw):
    t = dict(
        underlying="NIFTY",
        strike=25000.0,
        option_type="CE",
        side="B",
        expiry_date="2026-06-26",
        lots=1,
        order_type="LIMIT",
        product="MIS",
        ref_ltp=200.0,
        band_pct=5.0,
        fat_finger_cap=10,
        levels={},
        client_order_id="cid-xyz",
        buffer_pct=0.5,
        search_fn=_fake_search,
    )
    t.update(kw)
    return t


def _verdict(verdicts, check):
    for v in verdicts:
        if v["check"] == check:
            return v
    return None


def _all_ok(verdicts):
    return all(v["ok"] for v in verdicts)


def _is_tick(p, tick=0.05):
    """True iff p is an exact multiple of tick (to 2dp)."""
    return round(round(p / tick) * tick, 2) == round(p, 2)


# ---------------------------------------------------------------------------
# LIMIT BUY — happy path
# ---------------------------------------------------------------------------
class TestLimitBuy:
    def test_single_lot_one_child(self):
        children, verdicts = validate_and_build(_ticket())
        assert children is not None
        assert len(children) == 1
        c = children[0]
        assert isinstance(c, OrderIntent)
        assert c.prctyp == "LMT"
        assert c.trantype == "B"
        assert c.prd == "I"          # MIS pinned -> I
        assert c.qty == 65           # 1 lot * 65
        assert c.exch == "NFO"
        assert c.tsym == "NIFTY26JUN26C25000"
        assert _is_tick(c.prc)
        assert _all_ok(verdicts)

    def test_price_is_marketable_and_tick_valid(self):
        children, _ = validate_and_build(_ticket(ref_ltp=200.0, buffer_pct=0.5, band_pct=5.0))
        # 200 * 1.005 = 201.0 -> tick-up = 201.0 ; marketable (>= ref) and tick-valid
        assert children[0].prc >= 200.0
        assert _is_tick(children[0].prc)

    def test_nrml_product_maps_to_M(self):
        children, verdicts = validate_and_build(_ticket(product="NRML"))
        assert children is not None
        assert children[0].prd == "M"
        assert _all_ok(verdicts)

    def test_sell_price_rounds_down_and_tick_valid(self):
        children, _ = validate_and_build(_ticket(side="S", ref_ltp=200.0))
        c = children[0]
        assert c.trantype == "S"
        assert c.prc <= 200.0
        assert _is_tick(c.prc)


# ---------------------------------------------------------------------------
# Exchange rules — CO/BO/SL-MKT blocked
# ---------------------------------------------------------------------------
class TestExchangeRules:
    def test_product_CO_blocked(self):
        children, verdicts = validate_and_build(_ticket(product="CO"))
        assert children is None
        assert _verdict(verdicts, "exchange_product")["ok"] is False

    def test_product_BO_blocked(self):
        children, verdicts = validate_and_build(_ticket(product="BO"))
        assert children is None
        assert _verdict(verdicts, "exchange_product")["ok"] is False

    def test_sensex_product_CO_blocked_before_resolve(self):
        # SENSEX (BFO) also forbids CO/BO; fails at exchange validation,
        # so no scrip row is even needed.
        children, verdicts = validate_and_build(
            _ticket(underlying="SENSEX", product="CO")
        )
        assert children is None
        assert _verdict(verdicts, "exchange_product")["ok"] is False
        # never reached symbol resolution
        assert _verdict(verdicts, "symbol") is None

    def test_order_type_SL_MKT_blocked(self):
        children, verdicts = validate_and_build(_ticket(order_type="SL-MKT"))
        assert children is None
        assert _verdict(verdicts, "exchange_order_type")["ok"] is False

    def test_unknown_order_type_blocked(self):
        children, verdicts = validate_and_build(_ticket(order_type="FOO"))
        assert children is None
        assert _verdict(verdicts, "exchange_order_type")["ok"] is False


# ---------------------------------------------------------------------------
# MARKET orders — prctyp MKT, prc=0, finite/band skipped but fat_finger/jdata run
# ---------------------------------------------------------------------------
class TestMarketOrders:
    def test_market_prctyp_and_zero_price(self):
        children, verdicts = validate_and_build(_ticket(order_type="MARKET"))
        assert children is not None
        c = children[0]
        assert c.prctyp == "MKT"
        assert c.prc == 0.0
        assert c.qty == 65
        assert _all_ok(verdicts)

    def test_market_skips_band_but_runs_fat_finger(self):
        # lots over the cap must still fail on a MARKET order
        children, verdicts = validate_and_build(
            _ticket(order_type="MARKET", lots=999, fat_finger_cap=10)
        )
        assert children is None
        assert _verdict(verdicts, "fat_finger")["ok"] is False

    def test_market_with_no_ref_ltp_still_builds(self):
        # MARKET needs no reference price
        children, verdicts = validate_and_build(_ticket(order_type="MARKET", ref_ltp=None))
        assert children is not None
        assert children[0].prc == 0.0
        assert _all_ok(verdicts)


# ---------------------------------------------------------------------------
# SL-LMT — trigger tick-valid, prctyp SL-LMT
# ---------------------------------------------------------------------------
class TestStopLimit:
    def test_sell_stop_trigger_and_price_tick_valid(self):
        children, verdicts = validate_and_build(
            _ticket(side="S", order_type="SL-LMT", ref_ltp=100.0, levels={"stop_pct": 30})
        )
        assert children is not None
        c = children[0]
        assert c.prctyp == "SL-LMT"
        assert c.trgprc is not None
        assert c.trgprc > 0
        assert _is_tick(c.trgprc)
        assert _is_tick(c.prc)
        assert c.prc <= c.trgprc      # sell-stop limit sits at/below trigger
        assert _all_ok(verdicts)

    def test_sl_lmt_missing_levels_fails(self):
        children, verdicts = validate_and_build(
            _ticket(side="S", order_type="SL-LMT", levels={})
        )
        assert children is None
        assert _verdict(verdicts, "stop")["ok"] is False


# ---------------------------------------------------------------------------
# Freeze-qty splitting — > freeze qty produces multiple children
# ---------------------------------------------------------------------------
class TestFreezeSplit:
    def test_thirty_lots_splits_into_two_children(self):
        # 30 lots * 65 = 1950 > freeze 1800 -> [1800, 150]
        children, verdicts = validate_and_build(_ticket(lots=30, fat_finger_cap=50))
        assert children is not None
        assert len(children) == 2
        assert [c.qty for c in children] == [1800, 150]
        assert sum(c.qty for c in children) == 1950
        assert _all_ok(verdicts)

    def test_freeze_children_have_distinct_client_order_ids(self):
        children, _ = validate_and_build(_ticket(lots=30, fat_finger_cap=50))
        cids = [c.client_order_id for c in children]
        assert len(set(cids)) == len(cids)
        assert cids == ["cid-xyz-0", "cid-xyz-1"]
        # remarks must echo the per-child cid for reconciliation
        assert [c.remarks for c in children] == cids

    def test_every_freeze_child_is_tick_valid(self):
        children, _ = validate_and_build(_ticket(lots=30, fat_finger_cap=50))
        for c in children:
            assert _is_tick(c.prc)
            assert 0 < c.qty <= 1800


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------
class TestFailures:
    def test_unknown_underlying(self):
        children, verdicts = validate_and_build(_ticket(underlying="GOLD"))
        assert children is None
        assert _verdict(verdicts, "underlying")["ok"] is False

    def test_bad_side(self):
        children, verdicts = validate_and_build(_ticket(side="X"))
        assert children is None
        assert _verdict(verdicts, "side")["ok"] is False

    @pytest.mark.parametrize("bad_lots", [0, -1, 1.5, "2", True, None])
    def test_bad_lots(self, bad_lots):
        children, verdicts = validate_and_build(_ticket(lots=bad_lots))
        assert children is None
        assert _verdict(verdicts, "qty")["ok"] is False

    def test_limit_with_no_ref_ltp_fails(self):
        children, verdicts = validate_and_build(_ticket(order_type="LIMIT", ref_ltp=None))
        assert children is None
        assert _verdict(verdicts, "price_finite")["ok"] is False

    def test_fat_finger_blocks_excess_lots(self):
        # The marketable buffer is clamped to the band (eff = min(buffer, band)),
        # so a LIMIT price can never breach the band by construction — the
        # independent guard that can reject a too-large order is fat_finger.
        # 11 lots passes the freeze-split sanity but exceeds the cap of 10.
        children, verdicts = validate_and_build(_ticket(lots=11, fat_finger_cap=10))
        assert children is None
        assert _verdict(verdicts, "fat_finger")["ok"] is False


# ---------------------------------------------------------------------------
# Adversarial: the choke-point supports MKT WITHOUT weakening the strict
# legacy gate (validate_jdata stays MKT-free — defense in depth).
# ---------------------------------------------------------------------------
class TestMarketAllowListSafety:
    def test_MKT_NOT_in_strict_legacy_allowed_prctyp(self):
        # The L1/L2 broker-submission gate stays strict — MKT is excluded there.
        assert "MKT" not in ALLOWED_PRCTYP

    def test_validate_jdata_still_blocks_market(self):
        # The strict legacy gate must STILL reject a MKT intent (any price) —
        # only the new choke-point is allowed to emit MKT.
        for prc in (0.0, 200.0):
            mkt = OrderIntent(
                client_order_id="x", trantype="B", prctyp="MKT", exch="NFO",
                tsym="NIFTY26JUN26C25000", qty=65, prc=prc, prd="I", ret="DAY",
            )
            ok, reason = validate_jdata(mkt, lot_size=65)
            assert ok is False
            assert reason is not None

    def test_validate_jdata_still_blocks_sl_mkt(self):
        # SL-MKT remains forbidden everywhere (choke-point and legacy gate).
        bad = OrderIntent(
            client_order_id="x", trantype="B", prctyp="SL-MKT", exch="NFO",
            tsym="NIFTY26JUN26C25000", qty=65, prc=200.0, prd="I", ret="DAY",
        )
        ok, _ = validate_jdata(bad, lot_size=65)
        assert ok is False
