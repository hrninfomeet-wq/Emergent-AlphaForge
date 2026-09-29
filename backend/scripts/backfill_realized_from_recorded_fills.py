"""Backfill ``realized_pnl`` on ONE closed live trade from a RECORDED trade book.

The reconcile repair (``reboot_reconcile._repair_missing_realized``) can only use
the broker's trade book, which is DAY-SCOPED: once the day ends, a closed trade's
fills are gone and its null ``realized_pnl`` can never be recovered from the
broker. This script is the operator-approved way to apply fills recorded
elsewhere (a saved ``/live-broker/trades`` response, a contract note transcribed
into Noren's field names).

It adds NOTHING of its own judgement. The exit, the entry basis and the filled
quantity come from the same proof the reconcile uses
(``reboot_reconcile._match_close``: this entry's own fills present, the account
flat on the contract at entry, no other order interleaved, SELLs summing to
exactly the filled size), and the journal fields from the same helper every close
uses (``close_loop.realized_fields``). If the proof fails, nothing is written.

One thing the fills alone cannot prove: that no position on the contract was
CARRIED FORWARD from an earlier day (the reconcile checks the live position book's
``cfbuyqty``/``cfsellqty``; a recorded trade book has no such field). The operator
must attest to it — ``--attest-no-carry "<evidence>"`` — and the evidence is
stamped on the row. For 2026-09-16 the evidence is that the broker's own day
``rpnl`` for the contract (822.00) equals the proven round trip exactly; a carried
position would have changed it.

Scope is deliberately one trade:
  * ``--norenordno`` selects exactly one doc, which must be CLOSED with a null
    ``realized_pnl`` — an existing number is never overwritten;
  * dry-run by default; ``--apply`` writes, re-asserting the null filter in the
    write itself;
  * provenance is stamped on the row (``realized_pnl_backfill_source`` and
    ``realized_pnl_backfill_attestation``);
  * a doc whose exit day was never recorded (``exit_day_unknown``, set by the
    stale-OPEN reconcile) gets ``closed_at`` from the proven exit fill, so its P&L
    lands on the day it happened rather than the day it was noticed.

Usage (inside the backend container; fills JSON on stdin, a list of Noren
trade-book rows with at least tsym, trantype, flqty, flprc, fltm, norenordno):

    docker exec -i alphaforge_backend python scripts/backfill_realized_from_recorded_fills.py \
        --norenordno 26091600102830 --source "recorded /live-broker/trades 2026-09-16" \
        --attest-no-carry "broker day rpnl 822.00 == proven round trip" [--apply] < fills.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_db  # noqa: E402
from app.live.close_loop import realized_fields  # noqa: E402
from app.live.reboot_reconcile import _match_close  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


async def backfill(db, *, norenordno: str, fills: list, source: str,
                   attest_no_carry: str, apply: bool) -> dict:
    if not str(attest_no_carry or "").strip():
        return {"ok": False, "reason": "attestation_required: the fills cannot prove "
                "no position was carried forward on the contract (--attest-no-carry)"}
    doc = await db.live_trades.find_one({"norenordno": norenordno}, {"_id": 0})
    if not doc:
        return {"ok": False, "reason": "no_such_trade"}
    if doc.get("status") != "CLOSED":
        return {"ok": False, "reason": f"not_closed:{doc.get('status')}"}
    if doc.get("realized_pnl") is not None:
        return {"ok": False, "reason": "already_journalled",
                "realized_pnl": doc.get("realized_pnl")}
    tsym = str(doc.get("noren_tsym") or "").strip()
    # carry_proven rests on the operator's attestation, checked non-empty above.
    match = _match_close(fills, norenordno=norenordno, tsym=tsym, carry_proven=True,
                         quantity=doc.get("quantity"))
    if match is None or match["entry_px"] is None or match["qty"] is None:
        return {"ok": False, "reason": "exit_not_provable_from_these_fills"}
    fields = realized_fields(doc, match["exit_px"], entry_px=match["entry_px"],
                             quantity=match["qty"])
    if "realized_pnl" not in fields:
        return {"ok": False, "reason": "no_entry_price_on_doc"}
    fields["realized_pnl_backfilled"] = True
    fields["realized_pnl_backfill_source"] = source
    fields["realized_pnl_backfill_attestation"] = attest_no_carry
    fields["realized_pnl_backfilled_at"] = datetime.now(timezone.utc).isoformat()
    if doc.get("exit_day_unknown"):
        if match["exit_at"] is None:
            return {"ok": False, "reason": "exit_day_unknown_and_fills_carry_no_time"}
        # Broker fill times are IST wall-clock; the journal stores UTC.
        fields["closed_at"] = (match["exit_at"].replace(tzinfo=_IST)
                               .astimezone(timezone.utc).isoformat())
        fields["exit_day_unknown"] = False
    out = {"ok": True, "applied": False, "norenordno": norenordno, "tsym": tsym,
           "quantity": doc.get("quantity"),
           "entry_fill_price": doc.get("entry_fill_price"),
           **{k: v for k, v in fields.items() if k != "charges"}}
    if not apply:
        return out
    res = await db.live_trades.update_one(
        {"norenordno": norenordno, "status": "CLOSED", "realized_pnl": None},
        {"$set": fields})
    out["applied"] = bool(res.modified_count)
    if not out["applied"]:
        out["ok"] = False
        out["reason"] = "raced: journalled by something else first"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--norenordno", required=True)
    ap.add_argument("--source", required=True,
                    help="where the fills came from — stamped on the row")
    ap.add_argument("--attest-no-carry", required=True,
                    help="evidence that nothing on the contract was carried forward "
                         "from an earlier day — stamped on the row")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    fills = json.load(sys.stdin)
    if not isinstance(fills, list):
        print(json.dumps({"ok": False, "reason": "stdin must be a JSON list"}))
        return 2
    out = asyncio.run(backfill(get_db(), norenordno=args.norenordno, fills=fills,
                               source=args.source,
                               attest_no_carry=args.attest_no_carry, apply=args.apply))
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
