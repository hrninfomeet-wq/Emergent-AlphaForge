"""One-off repair: remap option-leg index_trade_id in saved backtest_runs docs.

Runs saved BEFORE the 2026-07-18 fix, with a DTE filter active, stored option
legs whose index_trade_id was the position in the DTE-FILTERED spot list while
doc["trades"] holds the FULL list — so the Trades pane joined legs onto the
wrong rows (e.g. a CE row showing another trade's 25850 PE leg).

Every leg carries signal_entry_ts + direction copied verbatim from its true
spot trade, so the correct position is recoverable by an exact join against
doc["trades"]. The join must be unique and total for a doc to be repaired;
anything ambiguous is reported and skipped. Original ids are preserved under
option_backtest["index_remap_backup"] so the repair is reversible.

Usage (host, from repo root):
    .venv/Scripts/python.exe backend/scripts/repair_option_leg_index.py           # dry run
    .venv/Scripts/python.exe backend/scripts/repair_option_leg_index.py --apply   # write

Env: MONGO_URL (default mongodb://localhost:27017), DB_NAME (default alphaforge).
"""

import argparse
import os
import sys

from pymongo import MongoClient


def _leg_arrays(ob):
    """The two arrays of legs a saved option_backtest doc may hold."""
    return [("trades", ob.get("trades") or []), ("skipped_trades", ob.get("skipped_trades") or [])]


def analyze_doc(doc):
    """Return (misaligned_count, remap or None, reason). remap is
    {(array_name, position): new_index_trade_id} covering EVERY leg."""
    spot = doc.get("trades") or []
    ob = doc.get("option_backtest") or {}
    legs = [(name, pos, leg) for name, arr in _leg_arrays(ob) for pos, leg in enumerate(arr)]
    if not spot or not legs:
        return 0, None, "no spot trades or no option legs"

    key_to_pos = {}
    dupes = set()
    for i, t in enumerate(spot):
        k = (t.get("entry_ts"), str(t.get("direction", "")).upper())
        if k in key_to_pos:
            dupes.add(k)
        key_to_pos[k] = i

    misaligned = 0
    remap = {}
    for name, pos, leg in legs:
        k = (leg.get("signal_entry_ts"), str(leg.get("direction", "")).upper())
        if k in dupes:
            return misaligned, None, f"ambiguous join key {k} (duplicate spot entry_ts+direction)"
        true_pos = key_to_pos.get(k)
        if true_pos is None:
            return misaligned, None, f"leg {name}[{pos}] has no matching spot trade for key {k}"
        cur = leg.get("index_trade_id")
        if cur != true_pos:
            misaligned += 1
        remap[(name, pos)] = true_pos
    return misaligned, remap, "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the remap (default: dry run)")
    ap.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    ap.add_argument("--db-name", default=os.environ.get("DB_NAME", "alphaforge"))
    args = ap.parse_args()

    db = MongoClient(args.mongo_url)[args.db_name]
    repaired = skipped = clean = 0
    for doc in db.backtest_runs.find(
        {"option_backtest.trades.0": {"$exists": True}},
        {"id": 1, "name": 1, "created_at": 1, "trades": 1, "option_backtest": 1},
    ):
        misaligned, remap, reason = analyze_doc(doc)
        label = f"{doc.get('id')}  {doc.get('name', '')[:70]}"
        if remap is None:
            if misaligned:
                skipped += 1
                print(f"SKIP  ({reason})  [{misaligned} misaligned!]  {label}")
            continue
        if misaligned == 0:
            clean += 1
            continue
        ob = doc["option_backtest"]
        if ob.get("index_remap_backup") is not None:
            skipped += 1
            print(f"SKIP  (already repaired)  {label}")
            continue
        print(f"{'FIX ' if args.apply else 'WOULD FIX'}  {misaligned} misaligned legs  {label}")
        if not args.apply:
            continue
        backup = {}
        sets = {}
        for (name, pos), new_id in remap.items():
            leg = (ob.get(name) or [])[pos]
            backup.setdefault(name, {})[str(pos)] = leg.get("index_trade_id")
            sets[f"option_backtest.{name}.{pos}.index_trade_id"] = new_id
        sets["option_backtest.index_remap_backup"] = backup
        db.backtest_runs.update_one({"_id": doc["_id"]}, {"$set": sets})
        repaired += 1

    print(f"\ndone: {repaired} repaired, {skipped} skipped, {clean} already aligned"
          f"{'' if args.apply else '  (dry run — pass --apply to write)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
