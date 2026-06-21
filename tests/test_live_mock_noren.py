"""Tests for MockNoren — deterministic in-memory BrokerClient.

All tests are synchronous wrappers via asyncio.run so no pytest-asyncio needed.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.broker_protocol import OrderIntent, OrderResult
from app.live.mock_noren import MockNoren, make_om


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(
    *,
    cid: str = "cid1",
    trantype: str = "B",
    prctyp: str = "LMT",
    exch: str = "NFO",
    tsym: str = "NIFTY25000CE",
    qty: int = 65,
    prc: float = 158.5,
    trgprc: float | None = None,
) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid,
        trantype=trantype,
        prctyp=prctyp,
        exch=exch,
        tsym=tsym,
        qty=qty,
        prc=prc,
        trgprc=trgprc,
        prd="I",
        ret="DAY",
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

def test_place_order_returns_ok_and_norenordno():
    client = MockNoren()
    result = run(client.place_order(_intent(cid="cid1")))
    assert result.ok is True
    assert result.norenordno == "MOCK1"
    assert result.rejreason is None


def test_place_order_increments_norenordno():
    client = MockNoren()
    r1 = run(client.place_order(_intent(cid="a")))
    r2 = run(client.place_order(_intent(cid="b")))
    assert r1.norenordno == "MOCK1"
    assert r2.norenordno == "MOCK2"


def test_place_order_appears_in_order_book():
    client = MockNoren()
    run(client.place_order(_intent(cid="cid1", tsym="NIFTY25000CE")))
    book = run(client.order_book())
    assert len(book) == 1
    assert book[0]["tsym"] == "NIFTY25000CE"
    assert book[0]["norenordno"] == "MOCK1"


def test_place_order_stores_intent_fields():
    client = MockNoren()
    run(client.place_order(_intent(cid="c1", trantype="S", prc=120.0, qty=65)))
    book = run(client.order_book())
    o = book[0]
    assert o["trantype"] == "S"
    assert o["prc"] == 120.0
    assert o["qty"] == 65
    assert o["client_order_id"] == "c1"


# ---------------------------------------------------------------------------
# Scripted reject
# ---------------------------------------------------------------------------

def test_scripted_reject_returns_not_ok():
    client = MockNoren()
    client.script_reject("RMS limit exceeded")
    result = run(client.place_order(_intent()))
    assert result.ok is False
    assert result.norenordno is None
    assert "RMS limit exceeded" in result.rejreason


def test_scripted_reject_does_not_add_to_order_book():
    client = MockNoren()
    client.script_reject("some reason")
    run(client.place_order(_intent()))
    book = run(client.order_book())
    assert len(book) == 0


def test_scripted_reject_only_fires_once():
    """After one rejected call, the next call should succeed."""
    client = MockNoren()
    client.script_reject("one-shot reject")
    r1 = run(client.place_order(_intent(cid="a")))
    r2 = run(client.place_order(_intent(cid="b")))
    assert r1.ok is False
    assert r2.ok is True
    assert r2.norenordno == "MOCK1"


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------

def test_cancel_order_marks_order_canceled():
    client = MockNoren()
    r = run(client.place_order(_intent()))
    cr = run(client.cancel_order(r.norenordno))
    assert cr.ok is True
    book = run(client.order_book())
    assert book[0]["status"] == "CANCELED"


def test_cancel_order_unknown_returns_not_ok():
    client = MockNoren()
    cr = run(client.cancel_order("NONEXISTENT"))
    assert cr.ok is False
    assert "not found" in cr.rejreason


# ---------------------------------------------------------------------------
# modify_order
# ---------------------------------------------------------------------------

def test_modify_order_updates_price():
    client = MockNoren()
    r = run(client.place_order(_intent(prc=100.0)))
    mr = run(client.modify_order(r.norenordno, prc=110.0))
    assert mr.ok is True
    book = run(client.order_book())
    assert book[0]["prc"] == 110.0


def test_modify_order_updates_trgprc():
    client = MockNoren()
    r = run(client.place_order(_intent(prctyp="SL-LMT", prc=119.0, trgprc=120.0)))
    run(client.modify_order(r.norenordno, prc=118.0, trgprc=119.0))
    book = run(client.order_book())
    assert book[0]["trgprc"] == 119.0


def test_modify_order_unknown_returns_not_ok():
    client = MockNoren()
    mr = run(client.modify_order("BADID", prc=100.0))
    assert mr.ok is False


# ---------------------------------------------------------------------------
# Injected fixtures — position_book, limits, search_scrip
# ---------------------------------------------------------------------------

def test_position_book_returns_injected_data():
    positions = [{"tsym": "NIFTY25000CE", "netqty": "65"}]
    client = MockNoren(position_book_data=positions)
    result = run(client.position_book())
    assert len(result) == 1
    assert result[0]["tsym"] == "NIFTY25000CE"


def test_limits_returns_injected_data():
    lim = {"cash": "500000", "payin": "200000"}
    client = MockNoren(limits_data=lim)
    result = run(client.limits())
    assert result["cash"] == "500000"


def test_search_scrip_returns_injected_rows_by_exch():
    rows = [{"tsym": "NIFTY25000CE25JUN", "token": "12345", "ls": "65"}]
    client = MockNoren(search_scrip_data={"NFO": rows})
    result = run(client.search_scrip("NFO", "NIFTY25000CE"))
    assert len(result) == 1
    assert result[0]["tsym"] == "NIFTY25000CE25JUN"


def test_search_scrip_empty_for_unknown_exch():
    client = MockNoren()
    result = run(client.search_scrip("XYZ", "anything"))
    assert result == []


def test_search_scrip_keyed_by_exch_text_tuple():
    """search_scrip_data can be keyed by (exch, text) for fine-grained fixtures."""
    rows = [{"tsym": "SENSEX72000CE25JUN", "token": "99999", "ls": "20"}]
    client = MockNoren(search_scrip_data={("BFO", "SENSEX72000"): rows})
    result = run(client.search_scrip("BFO", "SENSEX72000"))
    assert result[0]["token"] == "99999"


def test_set_search_scrip_helper():
    client = MockNoren()
    client.set_search_scrip("NFO", [{"tsym": "X"}])
    result = run(client.search_scrip("NFO", "anything"))
    assert result[0]["tsym"] == "X"


# ---------------------------------------------------------------------------
# emit_om
# ---------------------------------------------------------------------------

def test_emit_om_appends_to_om_events():
    client = MockNoren()
    event = make_om("MOCK1", "OPEN", "New")
    client.emit_om(event)
    assert len(client.om_events) == 1
    assert client.om_events[0]["norenordno"] == "MOCK1"
    assert client.om_events[0]["status"] == "OPEN"


def test_emit_om_invokes_callback():
    received = []
    client = MockNoren(om_callback=received.append)
    event = make_om("MOCK1", "COMPLETE", "Fill", fillshares="65", avgprc="158.5")
    client.emit_om(event)
    assert len(received) == 1
    assert received[0]["reporttype"] == "Fill"
    assert received[0]["fillshares"] == "65"
    assert received[0]["avgprc"] == "158.5"


def test_emit_om_multiple_events_accumulate():
    client = MockNoren()
    run(client.place_order(_intent()))
    client.emit_om(make_om("MOCK1", "OPEN", "New"))
    client.emit_om(make_om("MOCK1", "COMPLETE", "Fill", fillshares="65", avgprc="155.0"))
    assert len(client.om_events) == 2
    assert client.om_events[-1]["status"] == "COMPLETE"


def test_emit_om_reject_event_has_rejreason():
    client = MockNoren()
    event = make_om("MOCK1", "REJECTED", "Rejected", rejreason="RMS limit exceeded")
    client.emit_om(event)
    assert client.om_events[0]["rejreason"] == "RMS limit exceeded"


# ---------------------------------------------------------------------------
# make_om helper
# ---------------------------------------------------------------------------

def test_make_om_defaults():
    event = make_om("MOCK1", "OPEN", "New")
    assert event["norenordno"] == "MOCK1"
    assert event["status"] == "OPEN"
    assert event["reporttype"] == "New"
    assert event["fillshares"] == "0"
    assert event["avgprc"] == "0"
    assert event["rejreason"] == ""


def test_make_om_full_fields():
    event = make_om(
        norenordno="MOCK2",
        status="COMPLETE",
        reporttype="Fill",
        fillshares="65",
        avgprc="158.75",
        rejreason="",
    )
    assert event["fillshares"] == "65"
    assert event["avgprc"] == "158.75"


# ---------------------------------------------------------------------------
# Protocol conformance sanity check
# ---------------------------------------------------------------------------

def test_mock_noren_satisfies_broker_client_protocol():
    """Structural check that MockNoren has all required async methods."""
    client = MockNoren()
    assert asyncio.iscoroutinefunction(client.place_order)
    assert asyncio.iscoroutinefunction(client.cancel_order)
    assert asyncio.iscoroutinefunction(client.modify_order)
    assert asyncio.iscoroutinefunction(client.order_book)
    assert asyncio.iscoroutinefunction(client.position_book)
    assert asyncio.iscoroutinefunction(client.limits)
    assert asyncio.iscoroutinefunction(client.search_scrip)
