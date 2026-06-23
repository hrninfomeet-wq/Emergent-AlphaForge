"""Tests for app.live.order_builder — L1.3 TDD suite.

Inject a sync fake_search returning one canned NIFTY CE scrip row so that
symbol resolution succeeds without any network call.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend package is importable without installing
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest
from app.live.order_builder import build_intent, round_to_tick, slice_to_freeze
from app.execution_policy import resolve_premium_levels


# ---------------------------------------------------------------------------
# slice_to_freeze — freeze-qty order splitting (P1.2)
# ---------------------------------------------------------------------------
def test_slice_to_freeze_single_lot():
    assert slice_to_freeze(65, 1800) == [65]


def test_slice_to_freeze_exact_multiple():
    assert slice_to_freeze(3600, 1800) == [1800, 1800]


def test_slice_to_freeze_with_remainder():
    assert slice_to_freeze(1900, 1800) == [1800, 100]


def test_slice_to_freeze_sum_equals_qty():
    for qty in (65, 130, 1755, 1800, 1801, 3599, 9000):
        chunks = slice_to_freeze(qty, 1800)
        assert sum(chunks) == qty
        assert all(0 < c <= 1800 for c in chunks)


def test_slice_to_freeze_sensex():
    assert slice_to_freeze(20, 1000) == [20]
    assert slice_to_freeze(2500, 1000) == [1000, 1000, 500]


def test_slice_to_freeze_zero_or_negative():
    assert slice_to_freeze(0, 1800) == []
    assert slice_to_freeze(-65, 1800) == []


def test_slice_to_freeze_hard_cap():
    # qty > 10x freeze is rejected as a fat-finger
    with pytest.raises(ValueError):
        slice_to_freeze(18001, 1800)
    # exactly 10x is allowed
    assert sum(slice_to_freeze(18000, 1800)) == 18000


@pytest.mark.parametrize("bad_freeze", [0, -1, 1.5, "1800", True, None])
def test_slice_to_freeze_bad_freeze(bad_freeze):
    with pytest.raises(ValueError):
        slice_to_freeze(65, bad_freeze)


@pytest.mark.parametrize("bad_qty", [1.5, "65", True])
def test_slice_to_freeze_bad_qty(bad_qty):
    with pytest.raises(ValueError):
        slice_to_freeze(bad_qty, 1800)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

# Canned NIFTY 26JUN26 25000 CE scrip row (matches UNDERLYING_SPEC NIFTY lot=65)
_NIFTY_SCRIP = {
    "tsym": "NIFTY26JUN26C25000",
    "token": "1",
    "ls": "65",
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "26-JUN-2026",
    "dname": "NIFTY 26JUN26 25000 CE",
    "ti": "0.05",  # tick size — index options are 0.05
}

_CONTRACT = {
    "underlying": "NIFTY",
    "strike": 25000.0,
    "side": "CE",
    "expiry_date": "2026-06-26",
    "lot_size": 65,
}

_REF_LTP = 200.0
_BAND_PCT = 5.0
_FAT_FINGER_CAP = 10
_CID = "test-cid-abc123"


def _fake_search(exch: str, query: str):
    """Return the canned NIFTY scrip row unconditionally (sync, no network)."""
    return [_NIFTY_SCRIP]


def _build(**kwargs):
    """Shortcut: call build_intent with sensible defaults, overriding via kwargs."""
    defaults = dict(
        contract=_CONTRACT,
        side="B",
        order_kind="entry",
        lots=2,
        ref_ltp=_REF_LTP,
        band_pct=_BAND_PCT,
        fat_finger_cap=_FAT_FINGER_CAP,
        levels={},
        client_order_id=_CID,
        search_fn=_fake_search,
    )
    defaults.update(kwargs)
    return build_intent(**defaults)


# ---------------------------------------------------------------------------
# Test 1 — valid entry BUY
# ---------------------------------------------------------------------------

def test_valid_entry_buy():
    """Happy-path BUY entry: intent not None, correct fields, all verdicts ok."""
    intent, verdicts, resolved_lot_size = _build(side="B", order_kind="entry", lots=2, buffer_pct=0.5)

    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    assert intent.prctyp == "LMT"
    assert intent.trantype == "B"

    # Price should be ref_ltp * (1 + eff/100), eff = min(0.5, 5.0) = 0.5
    expected_eff = min(0.5, _BAND_PCT)
    expected_prc = round(_REF_LTP * (1.0 + expected_eff / 100.0), 2)
    assert intent.prc == expected_prc, f"prc={intent.prc} != expected {expected_prc}"

    # remarks must equal the client_order_id (broker reconciliation requirement)
    assert intent.remarks == _CID, f"remarks {intent.remarks!r} != cid {_CID!r}"

    # qty = lots * lot_size
    assert intent.qty == 2 * 65

    # trgprc must be None for LMT
    assert intent.trgprc is None

    # resolved_lot_size must equal the broker scrip ls (65)
    assert resolved_lot_size == 65, f"expected broker ls=65, got {resolved_lot_size}"

    # All verdicts must be ok
    bad = [v for v in verdicts if not v["ok"]]
    assert not bad, f"unexpected failed verdicts: {bad}"


# ---------------------------------------------------------------------------
# Test 2 — clamp wins: buffer_pct=10 > band_pct=2 → eff=2, not 10
# ---------------------------------------------------------------------------

def test_clamp_wins():
    """buffer_pct=10 is clamped to band_pct=2; price stays within 2%, intent passes."""
    buffer_pct = 10.0
    band_pct = 2.0
    intent, verdicts, resolved_lot_size = _build(
        side="B",
        order_kind="entry",
        buffer_pct=buffer_pct,
        band_pct=band_pct,
    )

    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"

    # eff must be min(10, 2) = 2
    eff = 2.0
    expected_prc = round(_REF_LTP * (1.0 + eff / 100.0), 2)
    assert intent.prc == expected_prc, (
        f"prc={intent.prc} — clamp did not apply. Expected {expected_prc} "
        f"(eff={eff}%), not {round(_REF_LTP * (1 + buffer_pct / 100), 2)} (unclamped)"
    )

    # Confirm prc is within band_pct of ref_ltp (sanity)
    deviation = abs(intent.prc - _REF_LTP) / _REF_LTP * 100
    assert deviation <= band_pct, f"prc {intent.prc} is {deviation:.2f}% off ref, band={band_pct}%"


# ---------------------------------------------------------------------------
# Test 3 — out-of-band: band_pct=0.0 → price_band verdict false, intent None
# ---------------------------------------------------------------------------

def test_out_of_band_returns_none():
    """band_pct=0.0 with any non-zero buffer produces out-of-band price; intent is None."""
    # band_pct=0.0 — eff = min(buffer, 0.0) = 0.0 → prc == ref_ltp exactly.
    # check_price_band with pct=0.0 and prc == ref_ltp → deviation=0.0 which equals 0.0
    # so that should actually PASS. We need a deviation > 0 with band=0.
    # Use buffer_pct=0.0 (eff=0) so prc=ref exactly, but band also 0 → deviation=0 (pass).
    # To truly force out-of-band we need prc != ref_ltp but band=0. We can't clamp
    # buffer above band (that's the point). So we pass a very tight band with a
    # ref_ltp mismatch by DIRECTLY patching the price.
    #
    # The spec says: "force a prc deviation > band (e.g. pass a tiny band_pct=0.0)"
    # With eff=min(buffer,0)=0, prc=ref exactly, and deviation=0 ≤ 0 → PASSES check_price_band.
    # To get a real failure we need band_pct < eff = 0 → not possible (band can't be negative).
    #
    # Instead we rely on check_price_band blocking when pct<0, but pct=0.0 with prc=ref is
    # actually allowed. The test intent from the spec is: pass a tiny band that the UNCLAMPED
    # buffer would violate — but after clamping it cannot. So the real "out-of-band" test
    # requires a scenario where even after clamping the price is still out of band.
    #
    # We achieve this with a ref_ltp that causes rounding to push prc just outside the band:
    # ref_ltp=200.0, band_pct=0.0 → deviation for ANY prc != 200.0 is > 0%.
    # eff = min(buffer, 0.0) = 0.0 → prc = round(200*(1+0/100), 2) = 200.0 → deviation=0 PASSES.
    #
    # True test: use a separate FAKE search_fn that returns a scrip with different tsym
    # to force a symbol failure, OR use band_pct=0.0 with buffer_pct=0.001 BUT clamp
    # means eff=0, prc=200.0. PASSES.
    #
    # The cleanest way to force a price_band fail is to skip buffer clamping by passing
    # a pre-constructed prc via a custom ref_ltp that makes the band tight.
    # Use: ref_ltp=200, band_pct=0.1, buffer_pct=1.0 → eff=0.1, prc=200.2 → deviation=0.1%
    # which is EXACTLY the band → allowed. Need deviation STRICTLY > band.
    # Use band_pct=0.05, buffer_pct=1.0 → eff=0.05, prc=round(200*(1.0005),2)=200.1
    # deviation=0.1/200*100=0.05% which equals band → should pass (<=, not <).
    # check_price_band uses deviation > pct strictly → 0.05 > 0.05 is False → passes.
    #
    # We need to bypass the clamp to get a genuine band failure. The spec says to
    # pass a tiny band_pct=0.0. With eff clamped to 0, prc=ref, deviation=0 ≤ 0 → passes.
    # So the out-of-band test must use the band check DIRECTLY.
    # The real scenario: check_price_band(prc, ref_ltp, 0.0) when prc != ref_ltp.
    # We force this by passing band_pct=0.0 and ref_ltp DIFFERENT from what the builder
    # will compute (but the builder controls prc, so with eff=0, prc=ref always matches).
    #
    # Resolution: the spec means that when you pass band_pct small enough that the
    # CHECK rejects it — pass band_pct=0.0 and buffer_pct=0.0 and rely on floating
    # point rounding. Actually safest: override check_price_band via a very small ref.
    # We'll use the approach of making ref_ltp a very close value that after rounding
    # is different: ref_ltp=200.001, band_pct=0.0, buffer_pct=0.0 → prc=round(200.001*(1),2)
    # =200.0, deviation=|200.0-200.001|/200.001*100 = 0.0005% > 0.0 → BLOCKED!

    ref_ltp = 200.001  # tiny sub-cent offset
    band_pct = 0.0     # zero tolerance
    intent, verdicts, resolved_lot_size = _build(
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        buffer_pct=0.0,
        order_kind="entry",
        side="B",
    )

    # price_band verdict must be False
    pb_verdict = next((v for v in verdicts if v["check"] == "price_band"), None)
    assert pb_verdict is not None, "no price_band verdict in list"
    assert not pb_verdict["ok"], (
        f"expected price_band to fail but got ok=True; prc={intent and intent.prc}"
    )
    assert intent is None


# ---------------------------------------------------------------------------
# Test 4 — fat-finger: lots > cap → fat_finger verdict false, intent None
# ---------------------------------------------------------------------------

def test_fat_finger_blocks():
    """lots > fat_finger_cap → fat_finger verdict ok=False, intent None."""
    intent, verdicts, resolved_lot_size = _build(lots=20, fat_finger_cap=5)

    ff_verdict = next((v for v in verdicts if v["check"] == "fat_finger"), None)
    assert ff_verdict is not None, "no fat_finger verdict in list"
    assert not ff_verdict["ok"], f"expected fat_finger to block; ok=True"
    assert intent is None
    assert resolved_lot_size is None, "resolved_lot_size must be None when fat_finger blocks"


# ---------------------------------------------------------------------------
# Test 5 — stop parity: trgprc EXACTLY equals resolve_premium_levels output
# ---------------------------------------------------------------------------

def test_stop_parity():
    """SL-LMT stop: trgprc byte-equal to resolve_premium_levels(stop_pct=30); prctyp=SL-LMT."""
    ref_ltp = 200.0
    stop_pct = 30.0

    intent, verdicts, resolved_lot_size = _build(
        side="S",
        order_kind="stop",
        ref_ltp=ref_ltp,
        levels={"stop_pct": stop_pct},
        band_pct=50.0,  # wide band so stop price is within it
    )

    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    assert intent.prctyp == "SL-LMT"

    # Derive expected stop using the SAME call as build_intent
    expected_stop, _ = resolve_premium_levels(
        ref_ltp,
        stop_pct=stop_pct,
        stop_floor=0.05,
        ndigits=2,
    )
    assert expected_stop is not None
    assert intent.trgprc == expected_stop, (
        f"trgprc {intent.trgprc!r} != expected {expected_stop!r} (parity broken)"
    )

    # prc should be max(0.05, round(stop - 0.05, 2))
    expected_prc = max(0.05, round(expected_stop - 0.05, 2))
    assert intent.prc == expected_prc, f"prc={intent.prc} != expected {expected_prc}"


# ---------------------------------------------------------------------------
# Test 6 — unknown underlying → symbol verdict false, intent None (no crash)
# ---------------------------------------------------------------------------

def test_unknown_underlying_no_crash():
    """An unknown underlying raises SymbolResolutionError internally; symbol verdict fails gracefully."""
    bad_contract = dict(_CONTRACT, underlying="FAKEXYZ")

    intent, verdicts, resolved_lot_size = _build(contract=bad_contract)

    sym_verdict = next((v for v in verdicts if v["check"] == "symbol"), None)
    assert sym_verdict is not None, "no symbol verdict returned"
    assert not sym_verdict["ok"], "expected symbol verdict to fail"
    assert intent is None
    assert resolved_lot_size is None, "resolved_lot_size must be None on symbol failure"
    # Must not have raised — reached here, so no crash


# ---------------------------------------------------------------------------
# Test 7 — remarks == client_order_id (explicit broker reconciliation contract)
# ---------------------------------------------------------------------------

def test_remarks_equals_cid():
    """remarks field on the intent must equal the client_order_id passed in."""
    cid = "unique-order-id-xyz987"
    intent, _, resolved_lot_size = _build(client_order_id=cid)

    assert intent is not None
    assert intent.remarks == cid, f"remarks {intent.remarks!r} != cid {cid!r}"


# ---------------------------------------------------------------------------
# Test 8 — verdicts structure: always a list of dicts with check/ok/detail
# ---------------------------------------------------------------------------

def test_verdicts_structure_on_failure():
    """Even when intent is None, verdicts is a non-empty list of dicts."""
    intent, verdicts, resolved_lot_size = _build(lots=999, fat_finger_cap=1)

    assert intent is None
    assert isinstance(verdicts, list)
    assert len(verdicts) > 0
    for v in verdicts:
        assert "check" in v
        assert "ok" in v
        assert "detail" in v


# ---------------------------------------------------------------------------
# Test 9 — SELL exit: prc = ref_ltp * (1 - eff/100) (below ref)
# ---------------------------------------------------------------------------

def test_sell_entry_price_below_ref():
    """SELL entry: prc should be ref_ltp * (1 - eff/100), i.e. below the reference."""
    intent, verdicts, resolved_lot_size = _build(side="S", order_kind="exit", buffer_pct=0.5, band_pct=5.0)

    assert intent is not None, f"verdicts={verdicts}"
    eff = min(0.5, 5.0)
    expected_prc = round(_REF_LTP * (1.0 - eff / 100.0), 2)
    assert intent.prc == expected_prc
    assert intent.trantype == "S"


# ---------------------------------------------------------------------------
# Test 10 — ref_ltp=None → fails closed, no crash (L2.2 in-process caller safety)
# ---------------------------------------------------------------------------

def test_ref_ltp_none_fails_closed():
    """ref_ltp=None must return (None, verdicts, None) with a failed verdict, not crash."""
    intent, verdicts, resolved_lot_size = _build(ref_ltp=None)

    assert intent is None, f"expected None intent for ref_ltp=None, got {intent!r}"
    assert isinstance(verdicts, list) and len(verdicts) > 0, "expected non-empty verdicts list"
    # At least one verdict must be failed and name 'ref_ltp' or 'price'
    failed = [v for v in verdicts if not v["ok"]]
    assert failed, "expected at least one failed verdict"
    assert any(
        "ref_ltp" in v["check"] or "price" in v["check"]
        for v in failed
    ), f"expected a 'ref_ltp' or 'price' failed verdict; got: {[v['check'] for v in failed]}"


# ---------------------------------------------------------------------------
# Test 11 — ref_ltp="abc" (string) → fails closed, no crash (type-safety guard)
# ---------------------------------------------------------------------------

def test_ref_ltp_string_fails_closed():
    """ref_ltp='abc' (wrong type) must return (None, verdicts, None) with a failed verdict, not crash."""
    intent, verdicts, resolved_lot_size = _build(ref_ltp="abc")

    assert intent is None, f"expected None intent for ref_ltp='abc', got {intent!r}"
    assert isinstance(verdicts, list) and len(verdicts) > 0, "expected non-empty verdicts list"
    failed = [v for v in verdicts if not v["ok"]]
    assert failed, "expected at least one failed verdict"
    assert any(
        "ref_ltp" in v["check"] or "price" in v["check"]
        for v in failed
    ), f"expected a 'ref_ltp' or 'price' failed verdict; got: {[v['check'] for v in failed]}"


# ---------------------------------------------------------------------------
# round_to_tick helper unit tests
# ---------------------------------------------------------------------------

def test_round_to_tick_nearest():
    """0.05 tick: nearest mode rounds to nearest multiple."""
    assert round_to_tick(65.325, 0.05, mode="nearest") == 65.35
    assert round_to_tick(65.32, 0.05, mode="nearest") == 65.30
    assert round_to_tick(65.30, 0.05, mode="nearest") == 65.30


def test_round_to_tick_up():
    """mode='up' always rounds UP to the tick multiple (for BUY marketable)."""
    assert round_to_tick(65.325, 0.05, mode="up") == 65.35
    assert round_to_tick(65.30, 0.05, mode="up") == 65.30  # already exact
    assert round_to_tick(65.31, 0.05, mode="up") == 65.35


def test_round_to_tick_down():
    """mode='down' always rounds DOWN to the tick multiple (for SELL marketable)."""
    assert round_to_tick(65.325, 0.05, mode="down") == 65.30
    assert round_to_tick(65.30, 0.05, mode="down") == 65.30  # already exact
    assert round_to_tick(65.34, 0.05, mode="down") == 65.30


def test_round_to_tick_zero_or_negative_tick_falls_back():
    """tick <= 0 → fall back to round(price, 2) — no tick constraint applied."""
    # round(65.325, 2) == 65.33 in CPython (65.325 is stored slightly above)
    result_zero = round_to_tick(65.325, 0.0)
    result_neg = round_to_tick(65.325, -0.05)
    assert result_zero == round(65.325, 2)
    assert result_neg == round(65.325, 2)


def test_round_to_tick_result_is_exact_multiple():
    """Result must be an exact multiple (no float artifacts)."""
    for raw in [65.325, 65.31, 65.33, 100.03, 200.01]:
        result = round_to_tick(raw, 0.05, mode="up")
        # Check it's a multiple of 0.05 within float precision
        assert abs(round(result / 0.05) * 0.05 - result) < 1e-9, (
            f"round_to_tick({raw!r}, 0.05, up) = {result!r} is not a 0.05 multiple"
        )


# ---------------------------------------------------------------------------
# Test 12 — tick rounding: BUY price is exact 0.05 multiple, rounds UP
# ---------------------------------------------------------------------------

def test_buy_entry_price_is_tick_aligned():
    """ref_ltp=65, band=3, BUY: the raw pre-tick prc=65.325 → tick-up to 65.35 (0.05 multiple)."""
    ref_ltp = 65.0
    band_pct = 3.0
    intent, verdicts, resolved_lot_size = _build(
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        buffer_pct=0.5,
        side="B",
        order_kind="entry",
    )
    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    prc = intent.prc
    # Must be an exact 0.05 multiple
    assert abs(round(prc / 0.05) * 0.05 - prc) < 1e-9, (
        f"BUY entry prc={prc!r} is not a 0.05 multiple (tick violation)"
    )
    # Must be >= raw price (rounds UP for BUY marketable)
    raw_prc = round(ref_ltp * (1 + 0.5 / 100), 2)
    assert prc >= raw_prc, f"BUY tick-rounded prc={prc!r} is below raw {raw_prc!r}"
    # Must be within band
    deviation_pct = abs(prc - ref_ltp) / ref_ltp * 100
    assert deviation_pct <= band_pct, (
        f"prc={prc!r} is {deviation_pct:.4f}% from ref={ref_ltp}, band={band_pct}%"
    )
    bad = [v for v in verdicts if not v["ok"]]
    assert not bad, f"unexpected failed verdicts: {bad}"


def test_sell_exit_price_is_tick_aligned_rounds_down():
    """ref_ltp=65, SELL: tick-rounds DOWN to a 0.05 multiple."""
    ref_ltp = 65.0
    band_pct = 3.0
    intent, verdicts, resolved_lot_size = _build(
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        buffer_pct=0.5,
        side="S",
        order_kind="exit",
    )
    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    prc = intent.prc
    # Must be an exact 0.05 multiple
    assert abs(round(prc / 0.05) * 0.05 - prc) < 1e-9, (
        f"SELL exit prc={prc!r} is not a 0.05 multiple (tick violation)"
    )
    # Must be <= raw price (rounds DOWN for SELL)
    raw_prc = round(ref_ltp * (1 - 0.5 / 100), 2)
    assert prc <= raw_prc, f"SELL tick-rounded prc={prc!r} is above raw {raw_prc!r}"


def test_stop_trgprc_is_tick_aligned():
    """stop order: trgprc (nearest) and prc (down) are both 0.05 multiples."""
    ref_ltp = 200.0
    stop_pct = 30.0
    intent, verdicts, resolved_lot_size = _build(
        side="S",
        order_kind="stop",
        ref_ltp=ref_ltp,
        levels={"stop_pct": stop_pct},
        band_pct=50.0,
    )
    assert intent is not None, f"expected intent, got None. verdicts={verdicts}"
    # trgprc is a 0.05 multiple
    assert abs(round(intent.trgprc / 0.05) * 0.05 - intent.trgprc) < 1e-9, (
        f"trgprc={intent.trgprc!r} is not a 0.05 multiple"
    )
    # prc is a 0.05 multiple
    assert abs(round(intent.prc / 0.05) * 0.05 - intent.prc) < 1e-9, (
        f"stop prc={intent.prc!r} is not a 0.05 multiple"
    )
    # protective invariant: prc <= trgprc
    assert intent.prc <= intent.trgprc
