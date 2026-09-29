"""`classify_broker_book` — flat vs open vs unknown, and which open rows the guard
is NOT watching. The Greeks card's "no positions" must rest on this, never on the
guard's registry."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.greeks_book import classify_broker_book, open_rows
from app.live_marks_service import LiveMarksService


def _row(tsym, netqty):
    return {"tsym": tsym, "exch": "NFO", "netqty": netqty}


def test_none_rows_is_unknown_with_the_reason_never_flat():
    b = classify_broker_book(None, [], error="token expired — reconnect Flattrade")
    assert b["state"] == "unknown" and b["open_count"] is None
    assert b["error"] == "token expired — reconnect Flattrade"


def test_none_rows_without_a_reason_still_says_something():
    b = classify_broker_book(None, [])
    assert b["state"] == "unknown" and b["error"]


def test_a_stale_snapshot_cannot_confirm_flat_even_when_it_is_empty():
    b = classify_broker_book([], [], stale=True, error="Session Expired")
    assert b["state"] == "unknown" and b["stale"] is True


def test_an_empty_read_book_is_flat():
    b = classify_broker_book([], ["NIFTYX"])
    assert b["state"] == "flat" and b["open_count"] == 0 and b["unguarded"] == []


def test_zero_quantity_rows_do_not_count_as_open():
    assert open_rows([_row("A", "0"), _row("B", 0), _row("C", "0.0")]) == []
    assert classify_broker_book([_row("A", "0")], [])["state"] == "flat"


def test_a_row_whose_quantity_cannot_be_parsed_counts_as_open_not_flat():
    """An unreadable quantity is not evidence of zero."""
    b = classify_broker_book([_row("A", "n/a")], [])
    assert b["state"] == "open" and b["unguarded"] == ["A"]
    assert len(open_rows([_row("A", None)])) == 1


def test_short_positions_are_open():
    assert classify_broker_book([_row("A", "-65")], ["A"])["state"] == "open"


def test_unguarded_rows_are_those_missing_from_the_guard_registry():
    b = classify_broker_book(
        [_row("A", "65"), _row("B", "-130"), _row("C", "0")], ["A"])
    assert b["state"] == "open" and b["open_count"] == 2
    assert b["guarded_count"] == 1 and b["unguarded"] == ["B"]


def test_an_empty_registry_leaves_every_open_row_unguarded():
    b = classify_broker_book([_row("A", "65")], [])
    assert b["unguarded"] == ["A"] and b["guarded_count"] == 0


def test_non_dict_rows_are_ignored():
    assert classify_broker_book(["junk", None], [])["state"] == "flat"


# --- the shared cache accessor -------------------------------------------------

def test_book_shares_one_broker_read_with_the_marks_payload():
    counters = {"broker": 0}

    async def fetch_positions():
        counters["broker"] += 1
        return [_row("A", "65")]

    async def load_contracts(idents):
        return []

    svc = LiveMarksService(fetch_positions=fetch_positions, load_contracts=load_contracts,
                           tick_map_factory=lambda: {}, now_ms=lambda: 1_000_000)

    async def run():
        b1 = await svc.book()
        await svc.payload()
        b2 = await svc.book()
        return b1, b2

    b1, b2 = asyncio.run(run())
    assert counters["broker"] == 1                    # NO broker call of its own
    assert b1["rows"] == [_row("A", "65")] and b1["stale"] is False
    assert b2["rows"] == b1["rows"]


def test_book_reports_stale_and_the_error_when_the_source_fails_after_a_good_read():
    """Last-good is served while the source fails — and `stale` says so, so the Greeks
    route cannot mistake it for a confirmation."""
    clock = {"t": 0.0}
    state = {"fail": False}

    async def fetch_positions():
        if state["fail"]:
            raise RuntimeError("Session Expired")
        return [_row("A", "65")]

    async def load_contracts(idents):
        return []

    from app.live_mark_cache import SnapshotCache
    svc = LiveMarksService(fetch_positions=fetch_positions, load_contracts=load_contracts,
                           tick_map_factory=lambda: {}, refresh_s=1.0)
    svc._cache = SnapshotCache(fetch=fetch_positions, refresh_s=1.0, clock=lambda: clock["t"])

    async def run():
        first = await svc.book()
        state["fail"] = True
        clock["t"] = 5.0                     # past the refresh interval, inside max_stale
        second = await svc.book()
        return first, second

    first, second = asyncio.run(run())
    assert first["stale"] is False and first["error"] is None
    assert second["stale"] is True and second["error"] == "Session Expired"
    assert second["rows"] == [_row("A", "65")]          # last-good, flagged


def test_book_raises_when_the_source_fails_with_nothing_cached():
    """Refuse rather than return a book-shaped nothing that reads as FLAT."""
    async def fetch_positions():
        raise RuntimeError("Session Expired")

    async def load_contracts(idents):
        return []

    svc = LiveMarksService(fetch_positions=fetch_positions, load_contracts=load_contracts,
                           tick_map_factory=lambda: {})
    try:
        asyncio.run(svc.book())
    except RuntimeError as exc:
        assert "Session Expired" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("book() must raise, not return an empty list")
