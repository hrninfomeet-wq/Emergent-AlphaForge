"""Pre-plugin screen for an intraday option-BUYING campaign. Read-only.

Runs `app.option_screen` against the local warehouse and answers, before any
plugin is written or any optimizer trial is spent: *is there anything to find?*

**Run it inside the backend container** — that is the only place every dependency
(pandas, pymongo) is guaranteed present, and `MONGO_URL` / `DB_NAME` are already
set there by `docker-compose.yml`:

    docker compose up -d --build backend        # REQUIRED after pulling: see below
    docker compose exec backend python scripts/screen_option_buying.py \
        --instrument NIFTY --validate-only
    docker compose exec backend python scripts/screen_option_buying.py \
        --instrument SENSEX --dte 1 2 3

⚠ **The rebuild is not optional after a `git pull`.** `backend/Dockerfile` bakes
the source in with `COPY . .`, and `docker-compose.yml` bind-mounts ONLY
`backend/app/strategies/plugins`. So a new plugin shows up in a running container
immediately, but this script and `app/option_screen.py` do not exist inside it
until the image is rebuilt — you would get `No such file or directory` and might
reasonably blame the checkout.

From the Windows host instead (Mongo is published on 127.0.0.1:27017), matching
the invocation the other scripts in this directory document:

    .venv/Scripts/python.exe backend/scripts/screen_option_buying.py \
        --instrument NIFTY --validate-only

That path needs `pandas` and `pymongo` in the venv; the container path needs
neither.

Order of operations is deliberate and matches the campaign protocol in
[`docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md`](../../docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md):

  1. **Validate the data first.** Spot session completeness, option pairing
     coverage, DTE resolvability. A screen run on holed data measures the holes.
     `--validate-only` stops here.
  2. **Print the split.** Train / validation / holdout session counts and the
     dates that bound them, so the reader can see what the numbers were computed
     on before reading any number.
  3. **Baseline before conditions.** The unconditioned ATM MFE/MAE, which should
     reproduce the 0.90-0.95 already on record. If it does not, the discrepancy
     is the finding and the run stops being about strategies.
  4. **Then the conditions**, each compared against that baseline.

This script NEVER touches the protected holdout. There is no flag to make it.
The holdout is read once, by the Backtest Lab, against a recorded finalist list.

Nothing here places, modifies or cancels an order, changes a deployment, or
touches a broker session. It opens one read-only Mongo connection.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from app.dte import compute_dte  # noqa: E402
from app.instruments import INSTRUMENT_KEYS, UNDERLYING_META  # noqa: E402
from app.option_costs import cost_config_for_exchange, round_trip_charges  # noqa: E402
from app.option_screen import (  # noqa: E402
    BASE_RATE_MFE_MAE,
    chronological_split,
    screen_condition,
    summarize_screen,
)
from app.session_spec import session_spec  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))

#: Exchange segment per underlying — drives the statutory charge schedule.
EXCH_FOR_INSTRUMENT = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO"}

#: Completeness bar the forward-validation policy uses for a "complete" session
#: (>= 357 of 375 one-minute bars, i.e. 95%). Reused here so the screen and the
#: promotion gate agree about which sessions are usable.
MIN_SESSION_COVERAGE = 0.95


def _ist_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d")


def _ist_hhmm(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).astimezone(IST).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Step 1 — data validation
# ---------------------------------------------------------------------------

def validate_spot(db, instrument: str) -> Dict[str, Any]:
    """Per-session spot bar counts against the date-aware expected session length."""
    rows = list(db.candles_1m.find(
        {"instrument": instrument.upper()}, {"_id": 0, "ts": 1}
    ).sort("ts", 1))
    if not rows:
        return {"instrument": instrument, "sessions": 0, "error": "no spot candles"}

    by_session: Dict[str, int] = defaultdict(int)
    for r in rows:
        by_session[_ist_date(r["ts"])] += 1

    complete, partial = [], []
    for date, count in sorted(by_session.items()):
        try:
            expected = session_spec(date, "spot").expected_candles
        except Exception:
            expected = 375
        (complete if count >= expected * MIN_SESSION_COVERAGE else partial).append(
            {"date": date, "bars": count, "expected": expected}
        )

    return {
        "instrument": instrument,
        "sessions": len(by_session),
        "first_session": min(by_session),
        "last_session": max(by_session),
        "complete_sessions": len(complete),
        "partial_sessions": len(partial),
        "worst_partials": sorted(partial, key=lambda d: d["bars"])[:10],
        "total_bars": len(rows),
    }


#: Rows drawn for the open-interest estimate. A random sample this size pins the
#: population share to well under a percentage point, which is far more precision
#: than the go/no-go decision it feeds needs.
OI_SAMPLE_SIZE = 20_000


def _oi_population(db, instrument: str) -> Dict[str, Any]:
    """Estimate what share of this instrument's option bars carry a non-zero `oi`.

    This is the single number that decides whether candidate A is worth building,
    so it has to be honest rather than convenient.

    The obvious implementation is wrong in a way that always looks good:
    ``count_documents({"oi": {"$gt": 0}}, limit=N)`` saturates at N, and dividing
    it by ``min(total, N)`` reports ~100% for any warehouse holding at least N
    populated rows — including one where only 3% are populated. It answers "are
    there at least N?", not "what share?".

    So: draw a genuine random sample scoped to THIS instrument (``options_1m``
    carries `underlying`) and report the share with its sample size, so a reader
    can see what the estimate rests on. Falls back to an unscoped sample if the
    instrument-scoped draw comes back empty, which would itself mean the
    `underlying` field is absent from these rows — worth knowing and reported.
    """
    total = db.options_1m.estimated_document_count()
    if not total:
        return {"sampled": 0, "with_oi": 0, "pct": None, "scope": "none",
                "note": "options_1m is empty"}

    def _draw(match: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        pipeline: List[Dict[str, Any]] = []
        if match:
            pipeline.append({"$match": match})
        pipeline += [
            {"$sample": {"size": min(int(total), OI_SAMPLE_SIZE)}},
            {"$group": {
                "_id": None,
                "n": {"$sum": 1},
                "with_oi": {"$sum": {"$cond": [{"$gt": ["$oi", 0]}, 1, 0]}},
            }},
        ]
        rows = list(db.options_1m.aggregate(pipeline))
        return rows[0] if rows else None

    scope = "instrument"
    row = _draw({"underlying": instrument.upper()})
    if not row or not row.get("n"):
        scope = "all-instruments"
        row = _draw(None)
    if not row or not row.get("n"):
        return {"sampled": 0, "with_oi": 0, "pct": None, "scope": "none",
                "note": "sample returned no rows"}

    n = int(row["n"])
    with_oi = int(row.get("with_oi") or 0)
    note = ("" if scope == "instrument" else
            f"no options_1m rows carry underlying={instrument.upper()!r}; "
            "sampled across all instruments instead")
    return {"sampled": n, "with_oi": with_oi,
            "pct": round(100.0 * with_oi / n, 2), "scope": scope, "note": note}


def validate_options(db, instrument: str) -> Dict[str, Any]:
    """Option-side coverage: contracts, expiries, contract_key hygiene, OI presence."""
    contracts = list(db.option_contracts.find(
        {"underlying": instrument.upper()},
        {"_id": 0, "instrument_key": 1, "expiry_date": 1, "strike": 1,
         "side": 1, "lot_size": 1, "contract_key": 1},
    ))
    if not contracts:
        # Older rows may key the underlying differently; report rather than guess.
        return {"instrument": instrument, "contracts": 0,
                "note": "no option_contracts rows matched underlying="
                        f"{instrument.upper()!r} — check the field name in your warehouse"}

    expiries = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})
    lots = sorted({int(c["lot_size"]) for c in contracts
                   if str(c.get("lot_size") or "").isdigit()})
    keyed = sum(1 for c in contracts if c.get("contract_key"))

    total_candles = db.options_1m.estimated_document_count()
    oi = _oi_population(db, instrument)

    return {
        "instrument": instrument,
        "contracts": len(contracts),
        "expiries": len(expiries),
        "first_expiry": expiries[0] if expiries else None,
        "last_expiry": expiries[-1] if expiries else None,
        "lot_sizes_seen": lots,
        "lot_size_static_fallback": UNDERLYING_META.get(instrument.upper(), {}).get("lot_size"),
        "contract_key_coverage_pct": round(100.0 * keyed / len(contracts), 2),
        "option_candles_total": total_candles,
        "oi_population": oi,
        "chain_snapshots": db.chain_snapshots.estimated_document_count(),
        "ticks_retained": db.ticks.estimated_document_count(),
    }


# ---------------------------------------------------------------------------
# Step 3/4 — build the ATM premium series and screen it
# ---------------------------------------------------------------------------

def _atm_strike(spot: float, step: int) -> int:
    return int(round(float(spot) / step) * step)


def build_atm_series(
    db, instrument: str, sessions: List[str], *, dte_filter: Optional[List[int]],
    entry_from: str, entry_to: str,
) -> pd.DataFrame:
    """One ATM premium frame across the requested sessions.

    For each session the ATM strike is fixed from the session's FIRST eligible
    spot bar (a strike re-selected intrabar would be a look-ahead), and both the
    CE and PE legs of that strike are returned so a direction-agnostic screen can
    measure the buyer's payoff on whichever side a condition selects.
    """
    step = int(UNDERLYING_META.get(instrument.upper(), {}).get("strike_step") or 50)
    expiries = sorted({
        str(c["expiry_date"]) for c in db.option_contracts.find(
            {"underlying": instrument.upper()}, {"_id": 0, "expiry_date": 1})
        if c.get("expiry_date")
    })

    frames: List[pd.DataFrame] = []
    for date in sessions:
        if dte_filter is not None:
            dte = compute_dte(date, expiries)
            if dte is None or dte not in dte_filter:
                continue

        day_start = int(datetime.strptime(date, "%Y-%m-%d")
                        .replace(tzinfo=IST).timestamp() * 1000)
        day_end = day_start + 24 * 3600 * 1000

        spot_rows = list(db.candles_1m.find(
            {"instrument": instrument.upper(), "ts": {"$gte": day_start, "$lt": day_end}},
            {"_id": 0, "ts": 1, "close": 1},
        ).sort("ts", 1))
        eligible = [r for r in spot_rows if entry_from <= _ist_hhmm(r["ts"]) <= entry_to]
        if not eligible:
            continue

        strike = _atm_strike(eligible[0]["close"], step)
        target_expiry = next((e for e in expiries if e >= date), None)
        if target_expiry is None:
            continue

        for side in ("CE", "PE"):
            # The field is `side`, NOT `option_type`. Getting this wrong returns
            # zero contracts and the run reports "no ATM option series could be
            # built ... this is a DATA finding" — i.e. it would blame the
            # warehouse for a typo in this query. Authority: options_universe.py
            # normalises to `side`, and option_candles.py stores `side`.
            contract = db.option_contracts.find_one({
                "underlying": instrument.upper(), "strike": strike,
                "side": side, "expiry_date": target_expiry,
            }, {"_id": 0, "instrument_key": 1, "contract_key": 1})
            if not contract:
                continue
            key_filter = ({"contract_key": contract["contract_key"]}
                          if contract.get("contract_key")
                          else {"instrument_key": contract["instrument_key"]})
            rows = list(db.options_1m.find(
                {**key_filter, "ts": {"$gte": day_start, "$lt": day_end}},
                {"_id": 0, "ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "oi": 1},
            ).sort("ts", 1))
            if len(rows) < 30:
                continue
            f = pd.DataFrame(rows)
            f["session_date"] = date
            f["side"] = side
            f["strike"] = strike
            f["ist"] = [_ist_hhmm(t) for t in f["ts"]]
            frames.append(f)

    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "oi",
                                     "session_date", "side", "strike", "ist"])
    return pd.concat(frames, ignore_index=True).sort_values(["session_date", "side", "ts"])


def charges_pct_for(instrument: str, premium: float, lot_size: int) -> float:
    """Statutory round-trip charges as a % of entry turnover, at this premium."""
    cfg = cost_config_for_exchange(EXCH_FOR_INSTRUMENT.get(instrument.upper(), "NFO"))
    qty = max(1, int(lot_size))
    turnover = max(1e-9, float(premium) * qty)
    ch = round_trip_charges(entry_premium=premium, exit_premium=premium,
                            quantity=qty, cfg=cfg)
    return 100.0 * float(ch["total_charges"]) / turnover


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mongo-url", default=os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    ap.add_argument("--db-name", default=os.environ.get("DB_NAME", "alphaforge"))
    ap.add_argument("--instrument", default="NIFTY", choices=sorted(INSTRUMENT_KEYS))
    ap.add_argument("--dte", nargs="*", type=int, default=[1, 2, 3],
                    help="DTE values to include (default 1 2 3; 0DTE must be asked for)")
    ap.add_argument("--horizons", nargs="*", type=int, default=[5, 10, 15, 30],
                    help="forward hold horizons in minutes")
    ap.add_argument("--train-end", default="2025-08-31")
    ap.add_argument("--validation-end", default="2025-12-31")
    ap.add_argument("--entry-from", default="09:25", help="IST, inclusive")
    ap.add_argument("--entry-to", default="14:48", help="IST, inclusive")
    ap.add_argument("--spread-pct", type=float, default=1.0,
                    help="modelled bid-ask, %% of premium per side")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    client = MongoClient(args.mongo_url, serverSelectionTimeoutMS=5000)
    db = client[args.db_name]
    inst = args.instrument.upper()
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instrument": inst,
        "args": vars(args),
    }

    # --- 1. validate -------------------------------------------------------
    print(f"\n{'=' * 72}\n  DATA VALIDATION — {inst}\n{'=' * 72}")
    spot = validate_spot(db, inst)
    report["spot_validation"] = spot
    if spot.get("error"):
        print(f"  ! {spot['error']}")
        return 2
    print(f"  spot sessions        : {spot['sessions']}  "
          f"({spot['first_session']} -> {spot['last_session']})")
    print(f"  complete (>=95% bars): {spot['complete_sessions']}")
    print(f"  partial              : {spot['partial_sessions']}")
    for p in spot["worst_partials"][:5]:
        print(f"      {p['date']}  {p['bars']}/{p['expected']}")

    opts = validate_options(db, inst)
    report["option_validation"] = opts
    print(f"\n  option contracts     : {opts.get('contracts')}")
    if opts.get("contracts"):
        print(f"  expiries             : {opts['expiries']}  "
              f"({opts['first_expiry']} -> {opts['last_expiry']})")
        print(f"  lot sizes seen       : {opts['lot_sizes_seen']}  "
              f"(static fallback {opts['lot_size_static_fallback']})")
        print(f"  contract_key coverage: {opts['contract_key_coverage_pct']}%")
        print(f"  option candles       : {opts['option_candles_total']:,}")
        _oi = opts["oi_population"]
        _pct = "n/a" if _oi["pct"] is None else f"{_oi['pct']}%"
        print(f"  OI populated         : {_pct}  "
              f"({_oi['with_oi']:,}/{_oi['sampled']:,} sampled, scope={_oi['scope']})")
        if _oi.get("note"):
            print(f"      ! {_oi['note']}")
        print(f"      <- if this is near 0%, candidate A is dead before it starts")
        print(f"  chain snapshots      : {opts['chain_snapshots']}   "
              f"<- 0 means no option-chain history exists to test")
        print(f"  ticks retained       : {opts['ticks_retained']:,}   "
              f"<- 30-day TTL; not a long-horizon replay source")
    if args.validate_only:
        _emit(report, args.json_out)
        return 0

    # --- 2. split ----------------------------------------------------------
    all_sessions = sorted({_ist_date(r["ts"]) for r in
                           db.candles_1m.find({"instrument": inst}, {"_id": 0, "ts": 1})})
    split = chronological_split(all_sessions, train_end=args.train_end,
                                validation_end=args.validation_end)
    report["split"] = split.counts()
    print(f"\n{'=' * 72}\n  SPLIT\n{'=' * 72}")
    print(f"  train      <= {args.train_end} : {len(split.train)} sessions")
    print(f"  validation <= {args.validation_end} : {len(split.validation)} sessions")
    print(f"  holdout     > {args.validation_end} : {split.counts()['holdout']} sessions "
          f"(PROTECTED — this script never reads it)")

    # --- 3/4. baseline then conditions ------------------------------------
    print(f"\n{'=' * 72}\n  SCREEN (train slice only)\n{'=' * 72}")
    frame = build_atm_series(db, inst, split.train, dte_filter=args.dte or None,
                             entry_from=args.entry_from, entry_to=args.entry_to)
    report["train_frame_bars"] = int(len(frame))
    if frame.empty:
        print("  ! no ATM option series could be built for the train slice.")
        print("    Either option coverage is absent for these sessions or the DTE")
        print("    filter excluded them. This is a DATA finding, not a strategy one.")
        _emit(report, args.json_out)
        return 3

    lot = int(UNDERLYING_META.get(inst, {}).get("lot_size") or 1)
    median_premium = float(frame["close"].median())
    charges_pct = charges_pct_for(inst, median_premium, lot)
    print(f"  bars {len(frame):,} | median ATM premium {median_premium:.2f} | "
          f"lot {lot} | statutory {charges_pct:.3f}% RT | spread {args.spread_pct}%/side")

    # One block per (session, leg): the frame stacks a CE and a PE series for every
    # session, and a forward window must never measure one contract's excursion
    # against another's prices.
    blocks = (frame["session_date"].astype(str) + "|" + frame["side"].astype(str))
    cells = screen_condition(frame, label="unconditioned_atm", horizons=args.horizons,
                             spread_pct_per_side=args.spread_pct,
                             charges_pct_round_trip=charges_pct,
                             group_by=blocks)
    report["baseline"] = summarize_screen(cells)

    print(f"\n  {'condition':<24} {'H':>4} {'bars':>8} {'MFE/MAE':>9} "
          f"{'net%med':>9} {'t':>7} {'sess':>6}  verdict")
    print(f"  {'-' * 82}")
    for c in cells:
        _print_cell(c)

    base_ratio = {c.horizon: c.mfe_mae for c in cells}
    print(f"\n  Reference: the register measured 0.90-0.95 unconditioned. A materially")
    print(f"  different baseline here means the DATA changed, and that is the finding.")
    print(f"  Anything below {BASE_RATE_MFE_MAE} is the base rate, not an edge.")
    report["baseline_ratio_by_horizon"] = {str(k): v for k, v in base_ratio.items()}

    _emit(report, args.json_out)
    client.close()
    return 0


def _print_cell(c) -> None:
    s = c.net_pct
    ratio = "-" if c.mfe_mae is None else f"{c.mfe_mae:.3f}"
    med = "-" if s.median_of_session_medians is None else f"{s.median_of_session_medians:+.3f}"
    t = "-" if s.t_stat is None else f"{s.t_stat:+.2f}"
    print(f"  {c.label:<24} {c.horizon:>4} {c.n_bars:>8,} {ratio:>9} "
          f"{med:>9} {t:>7} {s.n_sessions:>6}  {c.verdict}")


def _emit(report: Dict[str, Any], path: Optional[str]) -> None:
    if not path:
        return
    Path(path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\n  wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
