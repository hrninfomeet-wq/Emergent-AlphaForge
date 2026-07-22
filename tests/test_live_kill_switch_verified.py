"""Tests for panic_squareoff_verified (app/live/kill_switch.py) — item B hardening.

Trading-critical invariants:
- flattening orders are exchange-aware marketable LIMITs (never MKT): priced
  through the fresh GetQuotes touch when a token exists, clamped inside the
  circuit band, aligned to the leg's own tick, in the position's own prd/exch;
- unfilled exits are re-priced through a BOUNDED widening-band loop via
  cancel + re-place; remaining qty is re-read from the broker's fillshares
  after the cancel so a race-window fill can never cause an over-sell;
- every leg gets an honest outcome (FILLED / PLACED_UNCONFIRMED / REJECTED
  with the broker's reason / UNPRICED) and the final position book decides
  all_flat — a partial flatten is loudly visible, never swallowed;
- the route returns the per-leg report + a truthful one-line message.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.kill_switch import (  # noqa: E402
    FLATTEN_BAND_SCHEDULE,
    LEG_FILLED,
    LEG_REJECTED,
    LEG_UNCONFIRMED,
    LEG_UNPRICED,
    _leg_price,
    panic_squareoff_verified,
)
from app.live.mock_noren import MockNoren  # noqa: E402


NOSLEEP = None


async def _nosleep(_secs: float) -> None:
    return None


def _run(coro):
    return asyncio.run(coro)


def _pos(tsym="NIFTY26JUN26C25000", netqty="65", lp="200.0", exch="NFO",
         prd="M", token=None, ti=None) -> Dict[str, Any]:
    row = {"tsym": tsym, "netqty": netqty, "lp": lp, "exch": exch, "prd": prd}
    if token is not None:
        row["token"] = token
    if ti is not None:
        row["ti"] = ti
    return row


class FillingClient(MockNoren):
    """MockNoren that fills the Nth accepted placement (1-based) COMPLETE and
    flattens the position book when it fills. Earlier placements stay OPEN so
    the re-price loop has something to chew on."""

    def __init__(self, *, fill_on_attempt: int = 1, partial_first: Optional[int] = None, **kw):
        super().__init__(**kw)
        self._fill_on = fill_on_attempt
        self._accepted = 0
        self._partial_first = partial_first

    async def place_order(self, intent):
        result = await super().place_order(intent)
        if not result.ok:
            return result
        self._accepted += 1
        order = self._orders[result.norenordno]
        if self._accepted == self._fill_on:
            order["status"] = "COMPLETE"
            order["fillshares"] = str(order["qty"])
            self._position_book_data = []
        elif self._partial_first is not None and self._accepted == 1:
            order["fillshares"] = str(self._partial_first)
        return result


class RejectingBookClient(MockNoren):
    """Accepts the placement but the order book later reports it REJECTED —
    the post-ack RMS/exchange reject path (rejreason lives on the book row)."""

    def __init__(self, *, rejreason: str, **kw):
        super().__init__(**kw)
        self._book_rejreason = rejreason

    async def place_order(self, intent):
        result = await super().place_order(intent)
        if result.ok:
            self._orders[result.norenordno]["status"] = "REJECT"  # doc sample spelling
            self._orders[result.norenordno]["rejreason"] = self._book_rejreason
        return result


class CancelFailsButFilledClient(MockNoren):
    """cancel_order fails; the book shows the order COMPLETE (the fill won the
    race). The loop must classify the leg FILLED and never double-place."""

    async def cancel_order(self, norenordno):
        if norenordno in self._orders and self._orders[norenordno]["status"] == "OPEN":
            self._orders[norenordno]["status"] = "COMPLETE"
            self._orders[norenordno]["fillshares"] = str(self._orders[norenordno]["qty"])
            from app.live.broker_protocol import OrderResult
            return OrderResult(ok=False, rejreason="Rejected : ORA:Order not found to Cancel",
                               raw={"stat": "Not_Ok"})
        return await super().cancel_order(norenordno)


# ---------- pure pricing --------------------------------------------------------

def test_leg_price_sells_through_the_bid_and_clamps_to_lower_circuit():
    quote = {"bp1": "200.0", "sp1": "201.0", "lc": "199.5", "uc": "260.0"}
    # 1% through the bid would be 198.0 — below the 199.5 circuit floor → clamp.
    prc = _leg_price(65, 210.0, 1.0, 0.05, quote)
    assert prc == pytest.approx(199.5)
    # Without the clamp binding (band 0.1% → 199.8) rounding is DOWN to tick.
    prc = _leg_price(65, 210.0, 0.1, 0.05, quote)
    assert prc == pytest.approx(199.80)


def test_leg_price_buys_through_the_ask_and_clamps_to_upper_circuit():
    quote = {"bp1": "200.0", "sp1": "201.0", "lc": "150.0", "uc": "202.0"}
    # 1% through the ask = 203.01 → capped at uc 202.0.
    prc = _leg_price(-65, 195.0, 1.0, 0.05, quote)
    assert prc == pytest.approx(202.0)


def test_leg_price_falls_back_to_ref_without_quote():
    assert _leg_price(65, 200.0, 1.0, 0.05, {}) == pytest.approx(198.0)
    assert _leg_price(-65, 200.0, 1.0, 0.05, {}) == pytest.approx(202.0)
    assert _leg_price(65, None, 1.0, 0.05, {}) is None


# ---------- happy path ----------------------------------------------------------

def test_all_legs_fill_first_pass_all_flat():
    cl = FillingClient(fill_on_attempt=1, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    assert out["filled"] == 1
    assert out["flattened"] == 1
    assert out["legs"][0]["outcome"] == LEG_FILLED
    assert out["all_flat"] is True
    assert out["residual"] == []
    assert out["total"] is True
    # Exactly one exit order, SELL, LMT, in the position's own prd.
    book = _run(cl.order_book())
    assert len(book) == 1
    assert book[0]["trantype"] == "S" and book[0]["prctyp"] == "LMT"
    assert book[0]["prd"] == "M"


def test_position_read_failure_verdict_is_unknown_never_all_flat():
    """THE core fix: when the final position re-read RAISES (expired token), the
    kill verdict must be UNKNOWN (all_flat=None), NEVER a false ALL FLAT."""
    cl = MockNoren()
    cl.script_read_error("position_book", "Session Expired : Invalid Session Key")
    out = _run(panic_squareoff_verified(cl, [], [], sleep=_nosleep))
    assert out["all_flat"] is None          # UNKNOWN, not True
    assert out["total"] is False            # never a clean all-clear
    assert out["residual"] != []            # carries the re-check-failed marker


# ---------- reject-and-surface --------------------------------------------------

def test_immediate_reject_surfaces_reason_per_leg():
    cl = MockNoren(position_book_data=[_pos()])
    cl.script_reject("RMS:Rule: Check circuit limit including square off order exceeds")
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_REJECTED
    assert "RMS" in leg["reason"]
    assert out["flatten_failures"][0]["reason"] == leg["reason"]
    assert out["filled"] == 0
    assert out["all_flat"] is False          # position book still shows the leg
    assert out["residual"][0]["tsym"] == "NIFTY26JUN26C25000"
    assert out["total"] is False


def test_post_ack_book_reject_surfaces_rejreason():
    """ACCEPTED then REJECTED on the book (doc sample spelling 'REJECT') — the
    old executor counted this leg as flattened; the verified one must not."""
    cl = RejectingBookClient(rejreason="Insufficient funds",
                             position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_REJECTED
    assert leg["reason"] == "Insufficient funds"
    assert out["filled"] == 0
    assert out["flattened"] == 1  # historic meaning: broker ACCEPTED the order
    assert out["all_flat"] is False


# ---------- bounded re-price loop ------------------------------------------------

def test_reprice_loop_fills_on_second_pass():
    cl = FillingClient(fill_on_attempt=2, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    assert len(leg["attempts"]) == 2
    # Second attempt crosses a wider band → strictly more aggressive SELL price.
    assert leg["attempts"][1]["prc"] < leg["attempts"][0]["prc"]
    assert leg["attempts"][1]["band_pct"] > leg["attempts"][0]["band_pct"]
    # First attempt was cancelled before the re-place.
    first_no = leg["attempts"][0]["norenordno"]
    book = {o["norenordno"]: o for o in _run(cl.order_book())}
    assert book[first_no]["status"] == "CANCELED"
    assert out["all_flat"] is True


def test_loop_is_bounded_and_leaves_final_exit_working():
    cl = MockNoren(position_book_data=[_pos()])  # orders never fill
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_UNCONFIRMED
    assert len(leg["attempts"]) == len(FLATTEN_BAND_SCHEDULE)
    assert out["pending"] == 1
    assert out["all_flat"] is False
    # The most aggressive exit is LEFT WORKING (resting exit beats no exit).
    last_no = leg["attempts"][-1]["norenordno"]
    book = {o["norenordno"]: o for o in _run(cl.order_book())}
    assert book[last_no]["status"] == "OPEN"
    # Earlier attempts were cancelled.
    for att in leg["attempts"][:-1]:
        assert book[att["norenordno"]]["status"] == "CANCELED"


def test_partial_fill_replaces_remaining_qty_only():
    cl = FillingClient(fill_on_attempt=2, partial_first=25,
                       position_book_data=[_pos(netqty="65")])
    out = _run(panic_squareoff_verified(cl, [], [_pos(netqty="65")], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    book = _run(cl.order_book())
    qtys = [o["qty"] for o in book]
    assert qtys == [65, 40]  # second placement covers only the unfilled 40


def test_cancel_raced_by_fill_never_double_places():
    cl = CancelFailsButFilledClient(position_book_data=[_pos()])
    # Empty the position fixture on the fill so all_flat reflects reality.
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FILLED
    assert len([o for o in _run(cl.order_book()) if o["trantype"] == "S"]) == 1
    assert out["filled"] == 1


# ---------- pricing inputs -------------------------------------------------------

def test_quote_priced_leg_uses_touch_and_leg_tick():
    cl = FillingClient(
        fill_on_attempt=1,
        position_book_data=[_pos(token="43854", ti="0.05")],
        quotes_data={"stat": "Ok", "bp1": "190.0", "sp1": "191.0",
                     "lc": "20.0", "uc": "400.0"},
    )
    _run(panic_squareoff_verified(
        cl, [], [_pos(token="43854", ti="0.05", lp="200.0")], sleep=_nosleep))
    book = _run(cl.order_book())
    # Priced off the live bid (190 * 0.99 = 188.1), not the stale lp 200.
    assert float(book[0]["prc"]) == pytest.approx(188.10)


def test_freeze_qty_slicing_builds_child_legs():
    big = _pos(tsym="NIFTY26JUN26C25000", netqty="4000", lp="200.0")
    cl = MockNoren(position_book_data=[big])
    out = _run(panic_squareoff_verified(cl, [], [big], sleep=_nosleep))
    slices = [l for l in out["legs"]]
    assert [l["qty"] for l in slices] == [1800, 1800, 400]
    assert slices[0]["slice"] == "1/3" and slices[2]["slice"] == "3/3"


def test_unpriced_leg_without_lp_or_token_is_loud():
    bad = _pos(netqty="65", lp="0.00")
    cl = MockNoren(position_book_data=[bad])
    out = _run(panic_squareoff_verified(cl, [], [bad], sleep=_nosleep))
    assert out["legs"][0]["outcome"] == LEG_UNPRICED
    assert out["unpriced"] == [{"tsym": "NIFTY26JUN26C25000", "netqty": 65}]
    assert out["total"] is False


# ---------- frontend wiring (text contract, same idiom as
# test_deployment_kill_switch.py) --------------------------------------------------

def test_frontend_kill_switch_panel_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "live" /
             "KillSwitchPanel.jsx").read_text(encoding="utf-8")
    # Gated on the broker book (positions/working orders), not only the session.
    assert "isOpenPosition" in panel and "isWorkingOrder" in panel
    # Typed confirm step before firing.
    assert 'confirmText !== "KILL"' in panel
    assert "kill-switch-confirm-input" in panel
    # Per-leg outcome report + loud residual banner.
    assert "kill-switch-report" in panel
    assert "kill-switch-residual" in panel
    assert "PLACED_UNCONFIRMED" in panel
    # LiveDashboard retired for LiveCockpit (2026-07-22 redesign) — the kill panel
    # is rendered in the cockpit's always-on right column.
    dash = (root / "frontend" / "src" / "components" / "live" /
            "LiveCockpit.jsx").read_text(encoding="utf-8")
    assert "<KillSwitchPanel />" in dash
    # The old session-gated one-click kill button is gone from PositionMonitor.
    pm = (root / "frontend" / "src" / "components" / "live" /
          "PositionMonitor.jsx").read_text(encoding="utf-8")
    assert "liveKillSwitch" not in pm


def test_never_raises_on_client_explosions():
    class ExplodingClient(MockNoren):
        async def place_order(self, intent):
            raise RuntimeError("socket torn")

        async def position_book(self):
            raise RuntimeError("book unavailable")

    cl = ExplodingClient()
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert leg["outcome"] == "FAILED"
    assert "socket torn" in leg["reason"]
    assert out["all_flat"] is None  # flat-check unavailable ≠ flat


# ---------- lost-ack place → next pass must ADOPT, never blind re-place ----------
# A RAISED place_order may have LANDED at the broker (httpx timeout where the
# POST was processed). The old loop left attempt["placed"]=False so the next
# pass skipped the cancel-prev branch and re-placed the full remaining qty next
# to the live ghost → both fill → naked short. The loop must scan the order
# book for remarks == the attempt's client_order_id and adopt what it finds.

from app.live.broker_protocol import OrderResult  # noqa: E402
from app.live.kill_switch import LEG_FAILED, TERMINAL  # noqa: E402


class LostAckKillClient(MockNoren):
    """place_order ACCEPTS (order lands, remarks=cid) then RAISES on the first
    call. fill_ghost=True makes the landed ghost fill COMPLETE immediately
    (position book flattens). break_book_after_raise=True makes subsequent
    order_book reads raise (same degraded broker)."""

    def __init__(self, *, fill_ghost=False, land=True,
                 break_book_after_raise=False, **kw):
        super().__init__(**kw)
        self._to_raise = 1
        self._fill_ghost = fill_ghost
        self._land = land
        self._break_book = break_book_after_raise
        self.place_calls = 0

    async def place_order(self, intent):
        self.place_calls += 1
        if self._to_raise > 0:
            self._to_raise -= 1
            if self._land:
                res = await super().place_order(intent)
                if self._fill_ghost:
                    o = self._orders[res.norenordno]
                    o["status"] = "COMPLETE"
                    o["fillshares"] = str(o["qty"])
                    self._position_book_data = []
            if self._break_book:
                self.script_read_error("order_book", "Server Timeout")
            raise RuntimeError("httpx.ReadTimeout: PlaceOrder")
        return await super().place_order(intent)

    async def cancel_order(self, norenordno):
        o = self._orders.get(norenordno)
        if o is not None and o["status"] == "COMPLETE":
            return OrderResult(ok=False,
                               rejreason="Rejected : ORA:Order not found to Cancel",
                               raw={"stat": "Not_Ok"})
        return await super().cancel_order(norenordno)


def test_lost_ack_place_is_adopted_never_double_placed():
    """The pass-1 place raises but LANDED (ghost OPEN). Pass 2 must adopt the
    ghost via remarks==cid and run the normal cancel+resize path — never place
    a second full-qty SELL next to the live ghost."""
    cl = LostAckKillClient(position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    working_sells = [o for o in cl._orders.values()
                     if o["trantype"] == "S"
                     and str(o["status"]).upper() not in TERMINAL]
    assert len(working_sells) == 1, (
        f"{len(working_sells)} working SELLs — the lost-ack ghost plus a blind "
        "re-place can both fill → naked short")
    # The ghost was adopted into the pass-1 attempt (then cancelled by pass 2).
    assert leg["attempts"][0]["norenordno"] == "MOCK1"
    assert cl._orders["MOCK1"]["status"] == "CANCELED"


def test_lost_ack_ghost_that_filled_places_nothing_more():
    """The lost-ack ghost FILLS before the next pass: the position is flat.
    Adoption must see COMPLETE + fillshares and classify the leg FILLED —
    placing the 'remaining' would be a full-qty SELL on a flat book."""
    cl = LostAckKillClient(fill_ghost=True, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert cl.place_calls == 1, (
        "a second exit was placed after the lost-ack ghost already filled — "
        "naked short")
    assert leg["outcome"] == LEG_FILLED
    assert out["filled"] == 1
    assert out["all_flat"] is True


def test_lost_ack_with_unreadable_book_never_replaces():
    """Fail-CLOSED: the place raised and the order book is unreadable — the
    loop cannot know whether the exit landed, so it must NOT re-place (the
    ghost may be live). The leg ends FAILED (loud), never double-placed."""
    cl = LostAckKillClient(break_book_after_raise=True,
                           position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert cl.place_calls == 1, (
        "place_order was retried while the order book was UNREADABLE — a "
        "possible ghost SELL + this retry = naked short")
    assert leg["outcome"] == LEG_FAILED
    assert "httpx.ReadTimeout" in (leg["reason"] or "")


def test_lost_ack_that_never_landed_is_replaced_next_pass():
    """Guard-rail: the raised place never landed (readable book, cid absent) —
    the next pass must still place the exit (a kill must flatten)."""
    cl = LostAckKillClient(land=False, position_book_data=[_pos()])
    out = _run(panic_squareoff_verified(cl, [], [_pos()], sleep=_nosleep))
    leg = out["legs"][0]
    assert any(a.get("placed") for a in leg["attempts"])
    working_sells = [o for o in cl._orders.values()
                     if o["trantype"] == "S"
                     and str(o["status"]).upper() not in TERMINAL]
    assert len(working_sells) == 1


# ---------- pass-1 confirm barrier — pre-existing working orders --------------
# Step-1 cancels used to fire-and-forget: a pre-existing working order (e.g. a
# resting guard exit) that FILLED before its cancel landed still got a full-qty
# leg placed from the stale position snapshot → over-sell → naked short. After
# the cancels, ONE order-book re-fetch must confirm the cancelled set is
# terminal, read fillshares of the filled ones, and re-size/skip legs (the
# over-sell-safe logic reprice_exit_leg already implements).


class PreexistingFillOnCancelClient(MockNoren):
    """The pre-existing working order fills before its cancel lands: the cancel
    comes back 'not found', the book shows it terminal with fillshares, and the
    position book reflects the fill."""

    def __init__(self, ordno, *, fill_qty, post_book, cancel_ok=False,
                 terminal_status="COMPLETE", **kw):
        super().__init__(**kw)
        self._fill_ordno = ordno
        self._fill_qty = fill_qty
        self._post_book = post_book
        self._cancel_ok = cancel_ok
        self._terminal_status = terminal_status

    async def cancel_order(self, norenordno):
        if norenordno == self._fill_ordno:
            o = self._orders[norenordno]
            o["status"] = self._terminal_status
            o["fillshares"] = str(self._fill_qty)
            self._position_book_data = list(self._post_book)
            if not self._cancel_ok:
                return OrderResult(
                    ok=False,
                    rejreason="Rejected : ORA:Order not found to Cancel",
                    raw={"stat": "Not_Ok"})
            return OrderResult(ok=True, norenordno=norenordno)
        return await super().cancel_order(norenordno)


def _seed_working(cl, ordno="G1", qty=65, trantype="S",
                  tsym="NIFTY26JUN26C25000"):
    row = {"norenordno": ordno, "tsym": tsym, "status": "OPEN",
           "trantype": trantype, "qty": qty, "fillshares": "0",
           "prc": 199.0, "exch": "NFO", "prd": "M"}
    cl._orders[ordno] = row
    return dict(row)


def test_preexisting_exit_fill_during_cancel_places_nothing():
    """THE window: a resting guard exit fills before its cancel lands → the
    position is already flat. The pass-1 leg must be skipped (FILLED), not
    placed full-qty from the stale snapshot."""
    cl = PreexistingFillOnCancelClient(
        "G1", fill_qty=65, post_book=[_pos(netqty="0")],
        position_book_data=[_pos()])
    row = _seed_working(cl)
    out = _run(panic_squareoff_verified(cl, [row], [_pos()], sleep=_nosleep))
    new_orders = [o for o in cl._orders.values() if o["norenordno"] != "G1"]
    assert new_orders == [], (
        f"panic placed {[o['qty'] for o in new_orders]} after the resting exit "
        "already flattened the position → naked short")
    assert out["legs"][0]["outcome"] == LEG_FILLED
    assert out["all_flat"] is True


def test_preexisting_exit_partial_fill_resizes_leg():
    """A partial fill (25/65) in the cancel window shrinks the position to 40 —
    every placed exit must be sized 40, never the stale 65."""
    cl = PreexistingFillOnCancelClient(
        "G1", fill_qty=25, post_book=[_pos(netqty="40")], cancel_ok=True,
        terminal_status="CANCELED", position_book_data=[_pos()])
    row = _seed_working(cl)
    out = _run(panic_squareoff_verified(cl, [row], [_pos()], sleep=_nosleep))
    placed = [o for o in cl._orders.values() if o["norenordno"] != "G1"]
    assert placed, "the remaining 40 must still be flattened"
    assert all(o["qty"] == 40 for o in placed), (
        f"placed qtys {[o['qty'] for o in placed]} — selling the stale 65 "
        "against a 40 position is a 25-lot naked short")
    assert out["legs"][0]["qty"] == 40


def test_preexisting_order_that_wont_cancel_blocks_the_leg():
    """The cancelled set is NOT confirmed terminal (the order survives the
    cancel) → placing the exit next to a live working order risks the
    double-sell; the leg must be blocked LOUDLY, nothing placed."""
    class _StubbornClient(MockNoren):
        async def cancel_order(self, norenordno):
            if norenordno == "G1":
                return OrderResult(ok=True, norenordno=norenordno)  # never clears
            return await super().cancel_order(norenordno)

    cl = _StubbornClient(position_book_data=[_pos()])
    row = _seed_working(cl)
    out = _run(panic_squareoff_verified(cl, [row], [_pos()], sleep=_nosleep))
    new_orders = [o for o in cl._orders.values() if o["norenordno"] != "G1"]
    assert new_orders == [], (
        "a full-qty exit was placed while the resting guard exit is still "
        "WORKING — if both fill the account is short")
    leg = out["legs"][0]
    assert leg["outcome"] == LEG_FAILED
    assert "unconfirmed" in (leg["reason"] or "").lower()
    assert out["total"] is False


def test_cancel_confirm_read_failure_blocks_the_leg():
    """The barrier's order-book re-fetch raises → the cancelled set cannot be
    confirmed terminal → fail CLOSED (no legs placed for that scrip)."""
    cl = MockNoren(position_book_data=[_pos()])
    row = _seed_working(cl)
    cl.script_read_error("order_book", "Session Expired : Invalid Session Key")
    out = _run(panic_squareoff_verified(cl, [row], [_pos()], sleep=_nosleep))
    new_orders = [o for o in cl._orders.values() if o["norenordno"] != "G1"]
    assert new_orders == [], (
        "exits were placed although the cancel-confirm read FAILED — the "
        "resting order may still be live → double-sell risk")
    assert out["legs"][0]["outcome"] == LEG_FAILED
    assert out["total"] is False


def test_untracked_working_order_on_another_scrip_blocks_its_leg():
    """An OCO leg that TRIGGERED between the caller's snapshot and the barrier
    is a working order the kill never cancelled. Its scrip's leg must be
    blocked (that order can fill against the position → double-sell), even
    though the scrip had NO attempted cancel; other scrips flatten normally."""
    tsym_a = "NIFTY26JUN26C25000"
    tsym_b = "BANKNIFTY26JUN26C52000"
    cl = MockNoren(position_book_data=[_pos(tsym=tsym_a),
                                       _pos(tsym=tsym_b, netqty="30")])
    row_a = _seed_working(cl, ordno="G1", tsym=tsym_a)      # in the snapshot
    _seed_working(cl, ordno="X9", qty=30, tsym=tsym_b)      # NOT in the snapshot
    out = _run(panic_squareoff_verified(
        cl, [row_a], [_pos(tsym=tsym_a), _pos(tsym=tsym_b, netqty="30")],
        sleep=_nosleep))
    placed = [o for o in cl._orders.values()
              if o["norenordno"] not in ("G1", "X9")]
    assert placed and all(o["tsym"] == tsym_a for o in placed), (
        "an exit was placed on the scrip with an untracked WORKING order — "
        "if both fill the account is short")
    legs = {l["tsym"]: l for l in out["legs"]}
    assert legs[tsym_b]["outcome"] == LEG_FAILED
    assert "untracked" in (legs[tsym_b]["reason"] or "")
    assert any(a.get("placed") for a in legs[tsym_a]["attempts"])


def test_clean_cancel_still_flattens_full_qty():
    """Green-path guard: the pre-existing order cancels cleanly (terminal,
    fillshares 0, book unchanged) → the barrier must NOT over-block; the leg
    is placed for the full qty as before."""
    cl = MockNoren(position_book_data=[_pos()])
    row = _seed_working(cl)
    out = _run(panic_squareoff_verified(cl, [row], [_pos()], sleep=_nosleep))
    assert cl._orders["G1"]["status"] == "CANCELED"
    placed = [o for o in cl._orders.values() if o["norenordno"] != "G1"]
    assert placed and placed[0]["qty"] == 65
    assert any(a.get("placed") for a in out["legs"][0]["attempts"])
