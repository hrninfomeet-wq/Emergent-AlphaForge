"""The broker DECORATES our remarks tag; the matcher demanded it verbatim.

We place an OCO tagged `oco:<entry_norenordno>`. When a leg fires, the broker does
NOT return that string — it wraps it with the alert type and the trigger
description. The operator's real 2026-08-14 order-book row:

    LMT_BOS_O: oco:26081400076294: Ltp 61.95 is above 21.45: oco:26081400076294

and the June readback quoted in gtt.py shows the same shape,
`<ai_t>: <our remarks>: <trigger desc>`.

`_parse_oco_norenordno` required `startswith("oco:")` and `_match_exit_fill_price`
required EXACT equality with `oco:<no>`. Neither can be true for a real fired leg,
so the exit fill is never attributed to its entry and `realized_pnl` stays null —
a direct contributor to the 2026-08-14 trade having no P&L at all.

The fallback (exactly one same-tsym SELL) is not a substitute: on a same-strike
re-entry there are two SELLs, it correctly refuses to guess, and the trade closes
priceless.

Fix: find the tag ANYWHERE in the remark rather than at the start, and take the
FIRST occurrence so a trailing repeat cannot change the answer.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.reboot_reconcile import (  # noqa: E402
    _match_exit_fill_price, _parse_oco_norenordno,
)

ENTRY = "26081400076294"
TSYM = "NIFTY18AUG26P24300"
REAL = f"LMT_BOS_O: oco:{ENTRY}: Ltp 61.95 is above 21.45: oco:{ENTRY}"


def _sell(remarks, flprc="58.40", tsym=TSYM):
    return {"trantype": "S", "tsym": tsym, "remarks": remarks,
            "flprc": flprc, "status": "COMPLETE"}


def test_the_real_broker_remark_is_parsed():
    assert _parse_oco_norenordno(REAL) == ENTRY


def test_the_bare_tag_still_parses():
    """No regression for the undecorated form."""
    assert _parse_oco_norenordno(f"oco:{ENTRY}") == ENTRY


def test_the_real_remark_attributes_the_exit_fill():
    assert _match_exit_fill_price([_sell(REAL)], norenordno=ENTRY, tsym=TSYM) == 58.40


def test_a_DIFFERENT_entrys_oco_is_not_matched():
    """The tag exists to bind an exit to ITS entry — matching loosely would
    attribute a same-strike re-entry's exit to the wrong trade."""
    other = f"LMT_BOS_O: oco:99999999999999: Ltp 1 is above 2"
    assert _match_exit_fill_price([_sell(other)], norenordno=ENTRY, tsym=TSYM) is None


def test_a_buy_leg_is_never_taken_as_an_exit():
    row = _sell(REAL); row["trantype"] = "B"
    assert _match_exit_fill_price([row], norenordno=ENTRY, tsym=TSYM) is None


def test_non_string_and_empty_remarks_are_safe():
    for bad in (None, 123, "", "   ", "oco:", "LMT_BOS_O: oco:"):
        assert _parse_oco_norenordno(bad) is None, repr(bad)


def test_the_tagged_leg_wins_over_the_ambiguous_fallback():
    """Two same-tsym SELLs would make the fallback refuse; the tag resolves it."""
    rows = [_sell("something unrelated", flprc="10.0"), _sell(REAL, flprc="58.40")]
    assert _match_exit_fill_price(rows, norenordno=ENTRY, tsym=TSYM) == 58.40
