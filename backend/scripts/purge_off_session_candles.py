"""One-off cleanup: delete stored candles that fall outside a real trading session.

Storage is written straight from the vendor with no session filter
(`warehouse.persist_candles_df` / `upstox_index_ingest.persist_index_candles_bulk`),
so stale-feed artifacts accumulate: bars stamped 23:47 on 2026-05-29, one at 00:09
on 2026-06-01, 15:30 option bars on 2025-11-03 (off-session before the CAS change).

`warehouse.load_candles_df` now filters these at read time, so they can no longer
reach a backtest. This script removes them from storage as well, so the stored
count matches what the session can actually hold and the integrity audit reads
exactly rather than reporting a `surplus` day.

**Deletion is irreversible, so this writes every row it removes to a JSON backup
first and does nothing at all without `--apply`.** Restore with `--restore <file>`.

Integrity hashes are recomputed for every affected spot day. Skipping that would
leave `integrity_hashes.hash` covering rows that no longer exist, and the audit
would report `hash_mismatch` on precisely the days this cleaned.

Usage (host, from repo root):
    .venv/Scripts/python.exe backend/scripts/purge_off_session_candles.py            # dry run
    .venv/Scripts/python.exe backend/scripts/purge_off_session_candles.py --apply
    .venv/Scripts/python.exe backend/scripts/purge_off_session_candles.py --restore backup.json

Env: MONGO_URL (default mongodb://127.0.0.1:27017), DB_NAME (default alphaforge).
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd
from pymongo import MongoClient, UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.session_spec import OPTIONS, SPOT, session_rows_mask  # noqa: E402
from app.warehouse import _hash_day  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def _ist(ts_ms):
    return datetime.fromtimestamp(int(ts_ms) / 1000, IST)


def _find_off_session(db, collection, segment, key_fields):
    """Return the rows in `collection` that fall outside a real session.

    The mask is computed from a DataFrame, but the rows returned are the ORIGINAL
    Mongo dicts. Round-tripping them through pandas would coerce absent fields to
    NaT/NaN, which is both unserializable and lossy for a backup that has to be
    able to restore the documents exactly as they were.
    """
    rows = list(db[collection].find({}, {"_id": 0}))
    if not rows:
        return []
    keep = session_rows_mask(pd.DataFrame(rows), segment).to_numpy()
    out = []
    for i, is_session in enumerate(keep):
        if is_session:
            continue
        rec = dict(rows[i])
        rec["_ist"] = _ist(rec["ts"]).strftime("%Y-%m-%d %H:%M")
        rec["_key"] = tuple(str(rec.get(f)) for f in key_fields)
        out.append(rec)
    return out


def _report(label, rows, key_label):
    print(f"\n== {label} ==")
    if not rows:
        print("  nothing outside a session")
        return
    per = defaultdict(int)
    for r in rows:
        per[(r["_key"][0], r["_ist"][:10])] += 1
    print(f"  {len(rows)} row(s) across {len(per)} {key_label}-day(s)")
    for (key, day), n in sorted(per.items()):
        times = sorted({r["_ist"][11:] for r in rows if r["_key"][0] == key and r["_ist"][:10] == day})
        span = f"{times[0]}..{times[-1]}" if len(times) > 1 else times[0]
        print(f"    {key:<12}{day}  {n:>4} row(s)  {span}")


def _rehash_spot_days(db, affected, apply_changes):
    """Recompute integrity_hashes for every (instrument, date) we touched."""
    ops = []
    for instrument, day in sorted(affected):
        start = int(datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=IST).timestamp() * 1000)
        end = int(datetime.fromisoformat(f"{day}T23:59:59").replace(tzinfo=IST).timestamp() * 1000)
        day_rows = list(db.candles_1m.find(
            {"instrument": instrument, "ts": {"$gte": start, "$lte": end}}, {"_id": 0},
        ).sort("ts", 1))
        day_df = pd.DataFrame(day_rows)
        new_hash = _hash_day(day_df)
        print(f"    {instrument:<12}{day}  count -> {len(day_rows):<5} hash -> {new_hash}")
        ops.append(UpdateOne(
            {"instrument": instrument, "date": day},
            {"$set": {
                "instrument": instrument, "date": day,
                "hash": new_hash, "candle_count": int(len(day_df)),
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        ))
    if ops and apply_changes:
        db.integrity_hashes.bulk_write(ops, ordered=False)
    return len(ops)


def restore(db, path):
    payload = json.load(open(path, encoding="utf-8"))
    for coll in ("candles_1m", "options_1m"):
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in payload.get(coll, [])]
        if rows:
            db[coll].insert_many(rows, ordered=False)
            print(f"  restored {len(rows)} row(s) into {coll}")
    affected = {(r["instrument"], r["_ist"][:10]) for r in payload.get("candles_1m", [])}
    if affected:
        print("  re-hashing restored spot days:")
        _rehash_spot_days(db, affected, True)
    print("restore complete")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--restore", metavar="FILE", help="re-insert rows from a backup file")
    ap.add_argument("--backup", default="off_session_backup.json")
    args = ap.parse_args()

    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"),
                         serverSelectionTimeoutMS=5000)
    db = client[os.environ.get("DB_NAME", "alphaforge")]

    if args.restore:
        restore(db, args.restore)
        return

    spot = _find_off_session(db, "candles_1m", SPOT, ["instrument"])
    opts = _find_off_session(db, "options_1m", OPTIONS, ["underlying"])

    _report("SPOT (candles_1m)", spot, "instrument")
    _report("OPTIONS (options_1m)", opts, "underlying")

    total = len(spot) + len(opts)
    if not total:
        print("\nNothing to purge.")
        return

    if not args.apply:
        print(f"\nDRY RUN — {total} row(s) would be deleted. Re-run with --apply to write.")
        return

    with open(args.backup, "w", encoding="utf-8") as fh:
        json.dump({"candles_1m": spot, "options_1m": opts}, fh, indent=1)
    print(f"\nbacked up {total} row(s) -> {args.backup}")

    spot_deleted = 0
    for r in spot:
        spot_deleted += db.candles_1m.delete_one({"instrument": r["instrument"], "ts": r["ts"]}).deleted_count
    opt_deleted = 0
    for r in opts:
        opt_deleted += db.options_1m.delete_one({"instrument_key": r["instrument_key"], "ts": r["ts"]}).deleted_count
    print(f"deleted: candles_1m={spot_deleted}  options_1m={opt_deleted}")

    affected = {(r["instrument"], r["_ist"][:10]) for r in spot}
    if affected:
        print("\nrecomputing integrity hashes for affected spot days:")
        n = _rehash_spot_days(db, affected, True)
        print(f"  {n} day(s) re-hashed")

    print("\nDone. Re-run backend/scripts/audit_cas_session_coverage.py to confirm.")


if __name__ == "__main__":
    main()
