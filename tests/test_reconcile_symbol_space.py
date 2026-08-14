"""Never close a live position on a symbol comparison that cannot match.

2026-08-14: a REAL open position (NIFTY18AUG26P24300, netqty 65) was journalled
CLOSED with exit_reason="reconciled_closed" and realized_pnl=null while it was
still live at the broker for another ~30 minutes. The caps and day-stop went blind
to it — not OPEN so its unrealized loss was never summed, realized_pnl null so
nothing counted as loss.

Root cause: `_reconcile_open_flat` read `doc["trading_symbol"]` — the UPSTOX
symbol "NIFTY 24300 PE 18 AUG 26" — and tested membership in `open_tsyms`, which
is built from the NOREN-keyed position book ("NIFTY18AUG26P24300"). Those two
symbol spaces can never match, so EVERY open live trade always looked flat and was
closed the instant the position book was non-empty. It survived until 05:14:38
only because an EMPTY book short-circuits the routine entirely — the documented
empty-book guard was working perfectly and hiding this the whole time.

This is the same Upstox-vs-Noren confusion already fixed in live_blotter, but on
the CLOSE path, where the consequence is a live position recorded as flat.

The safety rule this pins: a close requires POSITIVE evidence of flatness in the
broker's own symbol space. An unresolvable identity is UNKNOWN, and unknown must
never close — exactly as an empty book already means unknown.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.reboot_reconcile import _reconcile_open_flat  # noqa: E402

NOREN = "NIFTY18AUG26P24300"
UPSTOX = "NIFTY 24300 PE 18 AUG 26"


class _Coll:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]
        self.updates = []

    def find(self, q=None, proj=None):
        docs = [d for d in self.docs
                if all(d.get(k) == v for k, v in (q or {}).items()
                       if not isinstance(v, dict))]
        class _C:
            async def to_list(_s, length=None): return [dict(d) for d in docs]
        return _C()

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def update_one(self, flt, upd):
        self.updates.append((flt, upd))
        class _R: modified_count = 1
        return _R()


class _DB:
    def __init__(self, docs):
        self.live_trades = _Coll(docs)


class _Client:
    async def trade_book(self):
        return []


def _doc(**over):
    d = {"norenordno": "26081400076294", "status": "OPEN",
         "trading_symbol": UPSTOX, "noren_tsym": NOREN,
         "quantity": 65, "entry_price": 61.7}
    d.update(over)
    return d


def _run(docs, open_tsyms):
    db = _DB(docs)
    out = asyncio.run(_reconcile_open_flat(db, _Client(), open_tsyms))
    return db, out


def test_a_HELD_position_is_never_closed():
    """THE 2026-08-14 case. The position was in the book the whole time."""
    db, out = _run([_doc()], {NOREN: True})
    assert db.live_trades.updates == [], (
        "closed a live position because it compared an Upstox symbol against a "
        "Noren-keyed position book")
    assert out["skipped_held"] == 1
    assert out["closed"] == 0


def test_a_genuinely_flat_position_is_still_closed():
    """No over-correction: reconcile must still do its job."""
    db, out = _run([_doc()], {"SOMEOTHER26AUG1C1": True})
    assert out["closed"] == 1


def test_a_doc_without_a_noren_symbol_is_never_closed():
    """Unresolvable identity is UNKNOWN — and unknown must not close, exactly as
    an empty position book already means unknown. Legacy docs predate noren_tsym."""
    db, out = _run([_doc(noren_tsym=None)], {NOREN: True})
    assert out["closed"] == 0
    assert db.live_trades.updates == []


def test_a_legacy_doc_is_not_closed_even_when_it_looks_flat():
    """The dangerous case: without noren_tsym we cannot prove flatness, so falling
    back to trading_symbol would reproduce the original bug exactly."""
    db, out = _run([_doc(noren_tsym=None)], {"SOMETHINGELSE": True})
    assert out["closed"] == 0, (
        "closed a position whose broker identity could not be resolved")


def test_the_upstox_symbol_is_not_used_for_the_comparison():
    """Even if the book somehow contained the Upstox string, that is not proof."""
    db, out = _run([_doc()], {UPSTOX: True})
    assert out["closed"] == 1, (
        "matching on trading_symbol means the Noren book was never consulted")


def test_a_doc_with_no_norenordno_is_skipped_as_before():
    db, out = _run([_doc(norenordno=None)], {NOREN: True})
    assert out["closed"] == 0
    assert out["skipped_no_norenordno"] == 1
