"""`expired_before` — "is this contract CERTAINLY gone?", from the symbol alone.

Two consumers depend on it answering only what it can prove:
  * the reconcile closes an OPEN journal row on an expired contract without a
    broker read (the token may be expired for days — 2026-09-16 → 09-29);
  * the guard's ownership boundary stops owning a contract that cannot be held.

A false True on either closes a live row or disowns a live position, so anything
unparseable, and anything expiring today, is False.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_marks import expired_before  # noqa: E402


@pytest.mark.parametrize("tsym,today,expect", [
    # BFO weekly <SYM><YY><M><DD><STRIKE><CE|PE> — exact date
    ("SENSEX2691774400PE", "2026-09-18", True),
    ("SENSEX2691774400PE", "2026-09-17", False),   # expiry day still trades
    ("SENSEX2691774400PE", "2026-09-16", False),
    ("SENSEX26O0876000PE", "2026-09-29", False),   # 08 OCT
    # NFO <SYM><DD><MON><YY><C|P><STRIKE> — exact date
    ("NIFTY18AUG26P24300", "2026-08-19", True),
    ("NIFTY18AUG26P24300", "2026-08-18", False),
    # BFO MONTHLY <SYM><YY><MON><STRIKE><CE|PE> — month granularity only
    ("SENSEX26JUN76500CE", "2026-07-01", True),    # June has fully passed
    ("SENSEX26JUN76500CE", "2026-06-30", False),   # still June: day unknown
    ("SENSEX26JUN76500CE", "2026-06-01", False),
    ("SENSEX25DEC76500CE", "2026-01-02", True),    # year boundary
    ("SENSEX26DEC76500CE", "2026-11-30", False),
    # unparseable — never guessed
    ("GARBAGE", "2099-01-01", False),
    ("", "2099-01-01", False),
    (None, "2099-01-01", False),
    ("SENSEX26XYZ76500CE", "2099-01-01", False),
])
def test_expired_before(tsym, today, expect):
    assert expired_before(tsym, today) is expect
