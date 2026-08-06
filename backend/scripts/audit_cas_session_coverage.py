"""Measure what the data feed actually stores now that the session has split.

SEBI's Closing Auction Session took effect 2026-08-03. Two things changed shape:

  * **spot** — the index stops trading at 15:15 and settles by auction. Bars from
    15:15 are frozen (zero range) and the auction close lands as one jump bar.
    Still 375 bars a day, but the last ~15 carry no information.
  * **options** — the derivatives segment now trades to 15:40, so a complete
    contract-day is **385** bars, not 375.

Whether our warehouse actually *has* those extra ten minutes depends on the data
vendor, and that is not something to assume. Flattrade's historical API was
observed on 2026-08-03 returning only 375 bars with the post-15:30 activity
apparently compressed into the final 15:29 bar. If Upstox does the same, the
15:30-15:40 tape is simply absent from our warehouse and no application code can
recover it — that is a vendor escalation, not a bug to fix here.

This script answers that question from stored data. It reads only.

Usage (host, from repo root):
    .venv/Scripts/python.exe backend/scripts/audit_cas_session_coverage.py
    .venv/Scripts/python.exe backend/scripts/audit_cas_session_coverage.py --start 2026-08-03 --end 2026-08-07

Env: MONGO_URL (default mongodb://127.0.0.1:27017), DB_NAME (default alphaforge).
Note 127.0.0.1, not localhost — on Windows `localhost` resolves to ::1 first and
stalls ~2s per probe against an IPv4-only Docker binding.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.session_spec import (  # noqa: E402
    CAS_EFFECTIVE_ISO,
    OPTIONS,
    SPOT,
    expected_candle_count,
)

IST = timezone(timedelta(hours=5, minutes=30))
CASH_CLOSE_MIN = 15 * 60 + 30


def _ist(ts_ms):
    return datetime.fromtimestamp(int(ts_ms) / 1000, IST)


def _ist_date(ts_ms):
    return _ist(ts_ms).strftime("%Y-%m-%d")


def _minute(ts_ms):
    dt = _ist(ts_ms)
    return dt.hour * 60 + dt.minute


def _bounds_ms(start_iso, end_iso):
    start = datetime.fromisoformat(f"{start_iso}T00:00:00").replace(tzinfo=IST)
    end = datetime.fromisoformat(f"{end_iso}T23:59:59").replace(tzinfo=IST)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def audit_spot(db, start_iso, end_iso):
    """Per-day bar count, plus how many of the tail bars are frozen."""
    lo, hi = _bounds_ms(start_iso, end_iso)
    per_day = defaultdict(list)
    cursor = db.candles_1m.find(
        {"ts": {"$gte": lo, "$lte": hi}},
        {"_id": 0, "instrument": 1, "ts": 1, "open": 1, "high": 1, "low": 1, "close": 1},
    )
    for row in cursor:
        per_day[(row.get("instrument"), _ist_date(row["ts"]))].append(row)

    out = []
    for (instrument, day), rows in sorted(per_day.items()):
        rows.sort(key=lambda r: r["ts"])
        tail = [r for r in rows if _minute(r["ts"]) >= 15 * 60 + 15]
        frozen = sum(1 for r in tail if float(r["high"]) == float(r["low"]))
        out.append({
            "instrument": instrument,
            "date": day,
            "stored": len(rows),
            "expected": expected_candle_count(day, SPOT),
            "first": _ist(rows[0]["ts"]).strftime("%H:%M"),
            "last": _ist(rows[-1]["ts"]).strftime("%H:%M"),
            "tail_bars": len(tail),
            "frozen_bars": frozen,
        })
    return out


def audit_options(db, start_iso, end_iso):
    """Per-day per-contract bar counts, and whether any bar lands after 15:30."""
    lo, hi = _bounds_ms(start_iso, end_iso)
    per_contract = defaultdict(lambda: {"n": 0, "after_cash_close": 0, "last": None})
    cursor = db.options_1m.find(
        {"ts": {"$gte": lo, "$lte": hi}},
        {"_id": 0, "underlying": 1, "instrument_key": 1, "ts": 1},
    )
    for row in cursor:
        key = (row.get("underlying"), _ist_date(row["ts"]), row.get("instrument_key"))
        bucket = per_contract[key]
        bucket["n"] += 1
        if _minute(row["ts"]) >= CASH_CLOSE_MIN:
            bucket["after_cash_close"] += 1
        if bucket["last"] is None or row["ts"] > bucket["last"]:
            bucket["last"] = row["ts"]

    per_day = defaultdict(lambda: {"contracts": 0, "full": 0, "with_extended": 0,
                                   "max_bars": 0, "latest": None})
    for (underlying, day, _key), bucket in per_contract.items():
        agg = per_day[(underlying, day)]
        agg["contracts"] += 1
        agg["max_bars"] = max(agg["max_bars"], bucket["n"])
        if bucket["n"] >= expected_candle_count(day, OPTIONS):
            agg["full"] += 1
        if bucket["after_cash_close"] > 0:
            agg["with_extended"] += 1
        if agg["latest"] is None or (bucket["last"] or 0) > agg["latest"]:
            agg["latest"] = bucket["last"]

    return [
        {
            "underlying": underlying,
            "date": day,
            "expected_per_contract": expected_candle_count(day, OPTIONS),
            **agg,
            "latest_ist": _ist(agg["latest"]).strftime("%H:%M") if agg["latest"] else "-",
        }
        for (underlying, day), agg in sorted(per_day.items())
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", default=CAS_EFFECTIVE_ISO)
    ap.add_argument("--end", default=CAS_EFFECTIVE_ISO)
    args = ap.parse_args()

    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"),
                         serverSelectionTimeoutMS=5000)
    db = client[os.environ.get("DB_NAME", "alphaforge")]

    print(f"\nCAS session audit  {args.start} .. {args.end}   (effective {CAS_EFFECTIVE_ISO})")

    print("\n== SPOT (index) ==")
    print(f"{'instrument':<12}{'date':<12}{'stored':>7}{'exp':>6}{'first':>7}{'last':>7}"
          f"{'tail':>6}{'frozen':>8}")
    spot = audit_spot(db, args.start, args.end)
    for row in spot:
        print(f"{str(row['instrument']):<12}{row['date']:<12}{row['stored']:>7}{row['expected']:>6}"
              f"{row['first']:>7}{row['last']:>7}{row['tail_bars']:>6}{row['frozen_bars']:>8}")
    if not spot:
        print("  (no spot rows in range)")

    print("\n== OPTIONS (F&O) ==")
    print(f"{'underlying':<12}{'date':<12}{'contracts':>10}{'exp/ct':>8}{'max':>6}"
          f"{'full':>6}{'w/ext':>7}{'latest':>8}")
    opts = audit_options(db, args.start, args.end)
    for row in opts:
        print(f"{str(row['underlying']):<12}{row['date']:<12}{row['contracts']:>10}"
              f"{row['expected_per_contract']:>8}{row['max_bars']:>6}{row['full']:>6}"
              f"{row['with_extended']:>7}{row['latest_ist']:>8}")
    if not opts:
        print("  (no option rows in range)")

    print("\n-- verdict --")
    post_cas_opts = [r for r in opts if r["date"] >= CAS_EFFECTIVE_ISO]
    if not post_cas_opts:
        print("  No post-CAS option data stored yet — rerun after an ingest.")
    elif all(r["with_extended"] == 0 for r in post_cas_opts):
        print("  NO option bars after 15:30 on any post-CAS day.")
        print("  => The vendor is truncating or compressing the 15:30-15:40 window.")
        print("     That tape is missing from the warehouse and cannot be recovered")
        print("     in application code. Escalate to the data vendor.")
    elif any(r["full"] < r["contracts"] for r in post_cas_opts):
        print("  Extended bars present but some contracts are short of 385.")
        print("  => Partial capture; re-run the option catch-up for these days.")
    else:
        print("  Extended session fully captured (385 bars/contract). Nothing to do.")

    frozen_days = [r for r in spot if r["date"] >= CAS_EFFECTIVE_ISO and r["frozen_bars"] > 0]
    if frozen_days:
        print(f"  Spot: {len(frozen_days)} post-CAS day(s) show frozen tail bars, as expected.")
        print("     `in_cas_window` keeps these out of indicator input.")


if __name__ == "__main__":
    main()
