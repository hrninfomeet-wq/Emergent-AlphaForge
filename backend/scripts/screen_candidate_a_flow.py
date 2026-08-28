"""Candidate A - ATM Premium-Flow Scalp - screened against its frozen spec.

Deliverable INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md section 4.1. Nothing
here is tuned: every threshold is the pre-registered one, and the holdout guard
is left armed (train slice only).

Entry trigger, verbatim from the spec:
    flow_imbalance = (ce_vol_z - pe_vol_z) + (ce_oi_delta_z - pe_oi_delta_z)
    >= +1.5 -> CE eligible; <= -1.5 -> PE eligible
    confirmation: close > vwap for CE, close < vwap for PE
    confirmation: adx >= 20
    at most one signal per direction per 30-bar cooldown
    liquidity: ATM bar volume >= 20-session causal median x 0.5
    DTE 1-3, entry window 09:25-14:48

The flow columns are read through `attach_required_data`, i.e. through exactly
the seam `evaluate()` will use - so this screens what the strategy would
actually see, not a reimplementation of it.

The ATM contract is chosen with `option_flow.first_close_by_session` +
`atm_strike`, the SAME rule the data layer uses. Screening a payoff on a
different contract than the flow was measured on would be meaningless.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# backend/ is two levels up from this file; keep the script runnable
# from anywhere without a hardcoded checkout path.
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
sys.path.insert(0, BACKEND)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from pymongo import MongoClient  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))

FLOW_COLS = ["ce_volume", "pe_volume", "ce_volume_z", "pe_volume_z",
             "ce_oi_delta_z", "pe_oi_delta_z", "atm_volume_median_20d"]

# --- pre-registered, section 4.1. Do not tune. ---
FLOW_Z_THRESHOLD = 1.5
ADX_MIN = 20.0
COOLDOWN_BARS = 30
LIQUIDITY_MULT = 0.5
DTE_ALLOWED = (1, 2, 3)
ENTRY_FROM, ENTRY_TO = "09:25", "14:48"
HORIZONS = (5, 10, 15, 30)
KILL_MFE_MAE = 1.15          # conditioned ratio at 10min must EXCEED this
KILL_T_STAT = 2.0            # session-level t-stat must EXCEED this
# Sessions the operator flagged as not yet ingested; excluded everywhere.
EXCLUDE_SESSIONS = {"2026-08-27", "2026-08-28"}


def sdate(ts):
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d")


def hhmm(ts):
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(IST).strftime("%H:%M")


def day_start_ms(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=IST).timestamp() * 1000)


class ACursor:
    def __init__(self, cur): self._c = cur
    def sort(self, k, d=1): self._c = self._c.sort(k, d); return self
    async def to_list(self, length=None):
        out = []
        for i, doc in enumerate(self._c):
            if length is not None and i >= length: break
            out.append(doc)
        return out


class AColl:
    def __init__(self, c): self._c = c
    def find(self, q=None, p=None): return ACursor(self._c.find(q or {}, p))
    async def distinct(self, f, q=None): return self._c.distinct(f, q or {})


class ADb:
    def __init__(self, db):
        self.candles_1m = AColl(db.candles_1m)
        self.options_1m = AColl(db.options_1m)
        self.option_contracts = AColl(db.option_contracts)


def build_premium_frame(raw, instrument, sessions, expiries, step):
    """ATM CE+PE premium bars, one block per (session, side).

    Contract identity is chosen exactly as `app.option_flow` chooses it, and the
    bars are selected by identity (underlying+expiry+strike+side+ts) - never by
    token (deliverable 11.1).
    """
    from app.option_flow import atm_strike, nearest_upcoming_expiry

    frames = []
    for d in sessions:
        ds, de = day_start_ms(d), day_start_ms(d) + 86_400_000
        spot = list(raw.candles_1m.find(
            {"instrument": instrument, "ts": {"$gte": ds, "$lt": de}},
            {"_id": 0, "ts": 1, "close": 1}).sort("ts", 1))
        if not spot:
            continue
        strike = float(atm_strike(float(spot[0]["close"]), step))
        expiry = nearest_upcoming_expiry(d, expiries)
        if expiry is None:
            continue
        for side in ("CE", "PE"):
            rows = list(raw.options_1m.find(
                {"underlying": instrument, "expiry_date": expiry, "strike": strike,
                 "side": side, "ts": {"$gte": ds, "$lt": de}},
                {"_id": 0, "ts": 1, "open": 1, "high": 1, "low": 1, "close": 1},
            ).sort("ts", 1))
            if len(rows) < 30:
                continue
            f = pd.DataFrame(rows)
            f["session_date"] = d
            f["side"] = side
            frames.append(f)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["session_date", "side", "ts"]).reset_index(drop=True)


def cooldown_mask(fire, sessions, bars=COOLDOWN_BARS):
    """At most one signal per `bars` bars, restarting each session."""
    out = np.zeros(len(fire), dtype=bool)
    last = -10**9
    prev_sess = None
    for i in range(len(fire)):
        if sessions[i] != prev_sess:
            last = -10**9
            prev_sess = sessions[i]
        if fire[i] and (i - last) >= bars:
            out[i] = True
            last = i
    return out


async def run(instrument, args):
    from app.indicators import precompute_all_indicators
    from app.option_screen import (chronological_split, screen_condition,
                                   summarize_screen, BASE_RATE_MFE_MAE)
    from app.warehouse import attach_required_data

    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000)
    raw = client["alphaforge"]
    adb = ADb(raw)

    from app.instruments import UNDERLYING_META
    step = int((UNDERLYING_META.get(instrument) or {}).get("strike_step") or 50)

    all_sessions = sorted({sdate(r["ts"]) for r in raw.candles_1m.find(
        {"instrument": instrument}, {"_id": 0, "ts": 1})} - EXCLUDE_SESSIONS)
    split = chronological_split(all_sessions, train_end=args.train_end,
                                validation_end=args.validation_end,
                                consumed_until=args.consumed_until)
    expiries = sorted({str(e) for e in raw.option_contracts.distinct(
        "expiry_date", {"underlying": instrument}) if e})

    # DTE filter, per session, against the real expiry calendar.
    def dte_of(d):
        nxt = next((e for e in expiries if e >= d), None)
        if nxt is None:
            return None
        return (datetime.strptime(nxt, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days

    train = [d for d in split.train if (dte_of(d) in DTE_ALLOWED)]
    print(f"\n{'='*78}\n  {instrument}\n{'='*78}")
    print(f"sessions total {len(all_sessions)}  train {len(split.train)}  "
          f"validation {len(split.validation)}  consumed {len(split.consumed)}")
    print(f"train sessions with DTE in {list(DTE_ALLOWED)}: {len(train)}  "
          f"({train[0] if train else '-'} .. {train[-1] if train else '-'})")
    print("HOLDOUT NOT READ (guard armed)")
    if not train:
        print("no eligible train sessions"); return None

    # ---- spot frame with flow columns + indicators, exactly as evaluate() sees it
    lo, hi = day_start_ms(train[0]), day_start_ms(train[-1]) + 86_400_000
    spot = pd.DataFrame(list(raw.candles_1m.find(
        {"instrument": instrument, "ts": {"$gte": lo, "$lt": hi}}, {"_id": 0}
    ).sort("ts", 1)))
    spot = spot[[sdate(t) in set(train) for t in spot["ts"]]].reset_index(drop=True)
    spot, cov = await attach_required_data(spot, FLOW_COLS, db=adb, instrument=instrument)
    spot = precompute_all_indicators(spot, {})

    ce_z = pd.to_numeric(spot["ce_volume_z"], errors="coerce")
    pe_z = pd.to_numeric(spot["pe_volume_z"], errors="coerce")
    ce_dz = pd.to_numeric(spot["ce_oi_delta_z"], errors="coerce")
    pe_dz = pd.to_numeric(spot["pe_oi_delta_z"], errors="coerce")
    flow = (ce_z - pe_z) + (ce_dz - pe_dz)
    spot["flow_imbalance"] = flow

    atm_vol = (pd.to_numeric(spot["ce_volume"], errors="coerce")
               + pd.to_numeric(spot["pe_volume"], errors="coerce"))
    med = pd.to_numeric(spot["atm_volume_median_20d"], errors="coerce")
    liquid = (atm_vol >= med * LIQUIDITY_MULT).to_numpy()

    ist = np.array([hhmm(t) for t in spot["ts"]])
    in_window = (ist >= ENTRY_FROM) & (ist < ENTRY_TO)
    vwap = pd.to_numeric(spot.get("vwap"), errors="coerce")
    adx = pd.to_numeric(spot.get("adx"), errors="coerce")
    close = pd.to_numeric(spot["close"], errors="coerce")
    sess = spot["ts"].map(sdate).to_numpy()

    base = in_window & liquid & (adx >= ADX_MIN).to_numpy() & flow.notna().to_numpy()
    if getattr(args, "invert", False):
        # FALSIFICATION CHECK ONLY - deliberately maps the trigger to the WRONG
        # leg. If this comes back strongly positive, the real run had a sign bug
        # rather than a negative result. Not a hypothesis; a wiring test.
        ce_fire = base & (flow <= -FLOW_Z_THRESHOLD).to_numpy() & (close < vwap).to_numpy()
        pe_fire = base & (flow >= FLOW_Z_THRESHOLD).to_numpy() & (close > vwap).to_numpy()
    else:
        ce_fire = base & (flow >= FLOW_Z_THRESHOLD).to_numpy() & (close > vwap).to_numpy()
        pe_fire = base & (flow <= -FLOW_Z_THRESHOLD).to_numpy() & (close < vwap).to_numpy()
    ce_fire = cooldown_mask(ce_fire, sess)
    pe_fire = cooldown_mask(pe_fire, sess)

    print(f"\nflow_imbalance available on {int(flow.notna().sum())}/{len(flow)} "
          f"train bars ({100*flow.notna().mean():.1f}%)")
    fn = flow.dropna()
    if len(fn):
        print(f"  distribution: p01={fn.quantile(.01):+.2f} p50={fn.quantile(.5):+.2f} "
              f"p99={fn.quantile(.99):+.2f}   >=+1.5: {(fn>=1.5).mean():.2%}  "
              f"<=-1.5: {(fn<=-1.5).mean():.2%}")
    print(f"funnel: in_window {int(in_window.sum())} -> liquid {int((in_window&liquid).sum())} "
          f"-> adx>={ADX_MIN} {int(base.sum())} -> CE fires {int(ce_fire.sum())}, "
          f"PE fires {int(pe_fire.sum())} (after {COOLDOWN_BARS}-bar cooldown)")

    # ---- premium frame on the SAME contracts
    prem = build_premium_frame(raw, instrument, train, expiries, step)
    if prem.empty:
        print("no ATM premium series could be built"); return None
    print(f"premium frame: {len(prem)} bars, "
          f"{prem['session_date'].nunique()} sessions x 2 legs")

    fire_by_ts = {}
    for i in range(len(spot)):
        if ce_fire[i]:
            fire_by_ts[(int(spot['ts'].iloc[i]), "CE")] = True
        if pe_fire[i]:
            fire_by_ts[(int(spot['ts'].iloc[i]), "PE")] = True
    cond = np.array([bool(fire_by_ts.get((int(t), s), False))
                     for t, s in zip(prem["ts"], prem["side"])])
    group = (prem["session_date"].astype(str) + "|" + prem["side"].astype(str)).tolist()
    print(f"condition maps onto {int(cond.sum())} premium bars")

    results = {}
    cells_all = []
    for label, mask in (("CandidateA_flow_conditioned", cond),
                        ("UNCONDITIONED_baseline", None)):
        cells = screen_condition(prem, label=label, horizons=list(HORIZONS),
                                 condition=(None if mask is None else pd.Series(mask)),
                                 spread_pct_per_side=args.spread_pct,
                                 group_by=group, side="LONG")
        cells_all.extend(cells)
        results[label] = [c.to_dict() for c in cells]
        print(f"\n  {label}")
        print(f"    {'h':>4}{'n_bars':>9}{'MFE/MAE':>10}{'sessions':>10}"
              f"{'median%':>10}{'t':>8}  verdict")
        for c in cells:
            st = c.net_pct
            print(f"    {c.horizon:>4}{c.n_bars:>9}"
                  f"{('%.3f' % c.mfe_mae) if c.mfe_mae is not None else '-':>10}"
                  f"{st.n_sessions:>10}"
                  f"{('%+.3f' % st.median_of_session_medians) if st.median_of_session_medians is not None else '-':>10}"
                  f"{('%+.2f' % st.t_stat) if st.t_stat is not None else '-':>8}"
                  f"  {c.verdict}")

    # ---- pre-registered kill thresholds
    print(f"\n  PRE-REGISTERED KILL THRESHOLDS (section 4.1)")
    c10 = next((c for c in cells_all
                if c.label == "CandidateA_flow_conditioned" and c.horizon == 10), None)
    verdicts = {c.horizon: c.verdict for c in cells_all
                if c.label == "CandidateA_flow_conditioned"}
    fails = []
    if c10 is None or c10.mfe_mae is None:
        fails.append("MFE/MAE at 10min not measurable")
    else:
        ok = c10.mfe_mae > KILL_MFE_MAE
        print(f"    conditioned MFE/MAE at 10min  = "
              f"{c10.mfe_mae:.3f}  must be > {KILL_MFE_MAE}   {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append(f"MFE/MAE {c10.mfe_mae:.3f} <= {KILL_MFE_MAE}")
        t = c10.net_pct.t_stat
        okt = t is not None and t > KILL_T_STAT
        print(f"    session-level t-stat on net%  = "
              f"{('%+.2f' % t) if t is not None else 'n/a'}  must be > {KILL_T_STAT}   "
              f"{'PASS' if okt else 'FAIL'}")
        if not okt:
            fails.append(f"t-stat {t} <= {KILL_T_STAT}")
    cands = [h for h, v in verdicts.items() if v == "CANDIDATE"]
    print(f"    CANDIDATE horizons            = {cands or 'none'} of {list(HORIZONS)}")
    if len(cands) == 1:
        fails.append(f"CANDIDATE at exactly one horizon ({cands[0]}) -> "
                     "multiple-comparisons artefact")
    print(f"\n  VERDICT: {'REJECT - ' + '; '.join(fails) if fails else 'PASSES the screen gate'}")
    results["_verdict"] = {"fails": fails, "candidate_horizons": cands}
    results["_train_sessions"] = len(train)
    client.close()
    return results


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="*", default=["NIFTY", "SENSEX"])
    ap.add_argument("--train-end", default="2025-08-31")
    ap.add_argument("--validation-end", default="2025-12-31")
    ap.add_argument("--consumed-until", default="2026-07-10")
    ap.add_argument("--spread-pct", type=float, default=1.0)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--flow-z", type=float, default=None)
    ap.add_argument("--adx-min", type=float, default=None)
    args = ap.parse_args()
    global FLOW_Z_THRESHOLD, ADX_MIN
    if args.flow_z is not None: FLOW_Z_THRESHOLD = args.flow_z
    if args.adx_min is not None: ADX_MIN = args.adx_min
    out = {}
    for inst in args.instruments:
        out[inst] = await run(inst, args)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\nwrote {args.json_out}")


asyncio.run(main())
