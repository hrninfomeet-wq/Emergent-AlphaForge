"""Backfill: move ACTIVE signals whose trade is already CLOSED to EXITED.

Until ``signal_lifecycle.exit_linked_signal`` was wired into the close paths, a
signal only left ACTIVE when the paper marker auto-closed its trade or the operator
closed that one trade by hand. Every other close — the 15:00 sweep, the boot
reconcile, Stop / Stop-ALL, the basket controls, retire, and EVERY live close —
left the signal ACTIVE forever. This applies the same transition retroactively.

It adds NOTHING of its own judgement. For each ACTIVE signal it looks up the trade
the signal points at:

  * ``paper_trade_id``  -> ``paper_trades``
  * ``live_trade_id``   -> ``live_trades``  (falling back to the live trade whose
                           ``signal_id`` is this signal)

and only when EVERY linked trade it finds is CLOSED does it move the signal, using
the same helper the live close paths use, with reason ``backfill_trade_closed`` and
``exited_at`` taken from the trade's own ``closed_at`` (normalised to UTC) so the
signal shows when the position actually ended, not when this script ran.

A signal with no findable trade (unlinked, or its trade doc is gone) or with a
trade still OPEN is left alone and counted — an unknown is never called exited.

Dry-run by default (prints what it WOULD move); ``--apply`` writes. Idempotent: an
EXITED signal is no longer ACTIVE, so a second run finds nothing to do.

Usage (inside the backend container):

    docker exec alphaforge_backend python scripts/backfill_signal_exits.py            # dry run
    docker exec alphaforge_backend python scripts/backfill_signal_exits.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.signal_lifecycle import exit_linked_signal  # noqa: E402
from app.trade_time import parse_instant  # noqa: E402

REASON = "backfill_trade_closed"


async def _find_one(coll: Any, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return await coll.find_one(query, {"_id": 0})


async def _linked_trades(db: Any, sig: Dict[str, Any]) -> list:
    """Every trade doc this signal is linked to, as (collection_name, doc)."""
    found = []
    if sig.get("paper_trade_id"):
        doc = await _find_one(db.paper_trades, {"id": sig["paper_trade_id"]})
        if doc is not None:
            found.append(("paper_trades", doc))
    live = None
    if sig.get("live_trade_id"):
        live = await _find_one(db.live_trades, {"id": sig["live_trade_id"]})
    if live is None and sig.get("id"):
        live = await _find_one(db.live_trades, {"signal_id": sig["id"]})
    if live is not None:
        found.append(("live_trades", live))
    return found


async def backfill(db: Any, *, apply: bool = False) -> Dict[str, Any]:
    """Scan ACTIVE signals; move those whose linked trade(s) are all CLOSED.

    Returns counts. ``would_move`` is what a dry run found; ``moved`` is what an
    ``--apply`` run actually wrote (the two can differ only if a signal changed
    state under us — a racing writer wins, never this script).
    """
    out: Dict[str, Any] = {
        "applied": bool(apply), "active_signals": 0, "would_move": 0, "moved": 0,
        "trade_still_open": 0, "no_linked_trade": 0, "raced": 0, "errors": 0,
    }
    signals = await db.signals.find({"state": "ACTIVE"}, {"_id": 0}).to_list(length=None)
    for sig in signals:
        out["active_signals"] += 1
        try:
            trades = await _linked_trades(db, sig)
            if not trades:
                out["no_linked_trade"] += 1
                continue
            if any(str(t.get("status") or "").upper() != "CLOSED" for _, t in trades):
                out["trade_still_open"] += 1
                continue
            out["would_move"] += 1
            if not apply:
                continue
            _, trade = trades[0]
            closed = parse_instant(trade.get("closed_at"))
            moved = await exit_linked_signal(
                db, sig.get("id"), reason=REASON, trade_id=trade.get("id"),
                realized_pnl=trade.get("realized_pnl"),
                at=closed.isoformat() if closed is not None else None)
            if moved:
                out["moved"] += 1
            else:
                out["raced"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad doc never aborts the rest
            out["errors"] += 1
            print(f"signal {sig.get('id')}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    from app.db import get_db  # imported late: the tests import this module without Mongo
    out = asyncio.run(backfill(get_db(), apply=args.apply))
    print(json.dumps(out, indent=2))
    return 0 if not out["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
