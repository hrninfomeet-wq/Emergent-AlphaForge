"""M4 - holdout test of PREREG_H1 on the ltpc-mode tick sessions (LTP only), 2026-05-27..2026-07-17.

Per session: index 1-s grid on received_ts; ATM nearest-expiry CE/PE LTP 1-s grid (last LTP <= s, age <= 3 s).
Contracts are mapped by token through option_contracts rows whose expiry is the NEAREST expiry >= day
(tokens are recycled ACROSS expiries, never within the live set).
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

from common import IST, INDEX_KEY, SEG, STRIKE_STEP, db, day_bounds_ms

SPREAD = {"NIFTY": 0.234, "SENSEX": 0.231}            # % of mid, round trip (M1, measured)
STT, TXN, SEBI, STAMP, GST = 0.0015, {"NIFTY": 0.0003553, "SENSEX": 0.000325}, 0.000001, 0.00003, 0.18
WS, KS, HS = [10, 20, 30], [2.5, 3.0], [30, 60]
PRIMARY = {"NIFTY": ("FADE", 30, 2.5, 30), "SENSEX": ("FADE", 30, 3.0, 60)}
L = 1


def stat_pct(und, pin, pout):
    c = STAMP * pin + (TXN[und] + SEBI) * (1 + GST) * (pin + pout) + STT * pout
    return 100 * c / pin


def holdout_days():
    out = []
    for und in ("NIFTY",):
        cur = db().ticks.aggregate([
            {"$match": {"instrument_key": INDEX_KEY[und], "mode": "ltpc"}},
            {"$project": {"d": {"$dateToString": {"date": {"$toDate": {"$add": [{"$toLong": "$ts"}, 19800000]}},
                                                  "format": "%Y-%m-%d"}}}},
            {"$group": {"_id": "$d", "n": {"$sum": 1}}}, {"$match": {"n": {"$gte": 15000}}}, {"$sort": {"_id": 1}}])
        out = [d["_id"] for d in cur]
    return out


def token_map(day, und):
    rows = {}
    for c in db().option_contracts.find({"underlying": und, "segment": SEG[und], "expiry_date": {"$gte": day}},
                                        {"exchange_token": 1, "strike": 1, "side": 1, "expiry_date": 1}).sort("expiry_date", 1):
        key = f"{SEG[und]}|{c['exchange_token']}"
        if key not in rows:
            rows[key] = (c["strike"], c["side"], c["expiry_date"])
    return rows


def secs_for(day):
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    a = int(d.replace(hour=9, minute=15).timestamp()); b = int(d.replace(hour=15, minute=30).timestamp())
    return np.arange(a, b)


def zscore(x, w):
    d1 = np.diff(x, prepend=np.nan)
    sig = pd.Series(d1).rolling(600, min_periods=300).std().to_numpy()
    dw = x - np.concatenate([np.full(w, np.nan), x[:-w]])
    with np.errstate(invalid="ignore", divide="ignore"):
        return dw / (sig * np.sqrt(w))


def run_day(day, und):
    lo, hi = day_bounds_ms(day)
    idx = pd.DataFrame(list(db().ticks.find({"instrument_key": INDEX_KEY[und], "ts": {"$gte": lo, "$lte": hi}},
                                            {"_id": 0, "received_ts": 1, "last_price": 1})))
    if len(idx) < 5000:
        return []
    tm = token_map(day, und)
    opt = pd.DataFrame(list(db().ticks.find({"instrument_key": {"$regex": f"^{SEG[und]}\\|"}, "ts": {"$gte": lo, "$lte": hi}},
                                            {"_id": 0, "instrument_key": 1, "received_ts": 1, "last_price": 1})))
    if opt.empty:
        return []
    opt = opt[opt["instrument_key"].isin(tm.keys())]
    opt["strike"] = opt["instrument_key"].map(lambda k: tm[k][0])
    opt["side"] = opt["instrument_key"].map(lambda k: tm[k][1])
    opt["expiry"] = opt["instrument_key"].map(lambda k: tm[k][2])
    near = sorted(opt["expiry"].unique())[0]
    opt = opt[opt["expiry"] == near]
    secs = secs_for(day)
    n = len(secs)
    sp = idx.assign(s=idx["received_ts"] // 1000).groupby("s")["last_price"].last().reindex(secs).ffill().to_numpy(float)
    step = STRIKE_STEP[und]
    atm = np.round(sp / step) * step
    ser = {}
    for (k, sd), g in opt.assign(s=opt["received_ts"] // 1000).groupby(["strike", "side"]):
        last = g.groupby("s")["last_price"].last().reindex(secs)
        upd = np.where(last.notna(), secs, np.nan)
        ltp = last.ffill().to_numpy(float)
        age = secs - pd.Series(upd).ffill().to_numpy()
        ser[(float(k), sd)] = (ltp, age)
    rows = []
    half = SPREAD[und] / 200
    lo_i, hi_i = 5 * 60, n - 30 * 60

    def run(sig, fire, side, H):
        nxt = -1
        for t in np.flatnonzero(fire):
            if t < lo_i or t >= hi_i or t <= nxt or np.isnan(atm[t]):
                continue
            key = (float(atm[t]), side)
            if key not in ser:
                continue
            ltp, age = ser[key]
            e, x = t + L, t + L + H
            if x >= n or np.isnan(ltp[e]) or age[e] > 3 or np.isnan(ltp[x]):
                continue
            pin = ltp[e] * (1 + half); pout = ltp[x] * (1 - half)
            g = 100 * (pout - pin) / pin
            rows.append({"day": day, "und": und, "sig": sig, "H": H, "ret": g, "net": g - stat_pct(und, pin, pout)})
            nxt = x

    for w in WS:
        z = zscore(sp, w)
        for k in KS:
            for H in HS:
                run(f"FADE_{w}_{k}", z <= -k, "CE", H); run(f"FADE_{w}_{k}", z >= k, "PE", H)
                run(f"IMP_{w}_{k}", z >= k, "CE", H); run(f"IMP_{w}_{k}", z <= -k, "PE", H)
    return rows


def main():
    days = holdout_days()
    print("holdout sessions:", len(days), days[0], "..", days[-1], flush=True)
    rows = []
    for d in days:
        for und in ("NIFTY", "SENSEX"):
            r = run_day(d, und)
            rows += r
        print(d, len(rows), flush=True)
    df = pd.DataFrame(rows)
    df.to_pickle("m4_rows.pkl")
    res = []
    for (und, sig, H), g in df.groupby(["und", "sig", "H"]):
        per = g.groupby("day")["net"].mean()
        pg = g.groupby("day")["ret"].mean()
        t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 else np.nan
        res.append({"und": und, "sig": sig, "H": H, "n": len(g), "sessions": len(per),
                    "gross_sess": round(pg.mean(), 4), "net_sess": round(per.mean(), 4), "t_net": round(float(t), 2),
                    "share_sess_net_pos": round(float((per > 0).mean()), 3), "hit": round(float((g["net"] > 0).mean()), 3)})
    R = pd.DataFrame(res)
    R.to_csv("m4_results.csv", index=False)
    print(R.sort_values(["und", "sig", "H"]).to_string(index=False))
    verdict = {}
    for und, (fam, w, k, H) in PRIMARY.items():
        p = R[(R.und == und) & (R.sig == f"{fam}_{w}_{k}") & (R.H == H)].iloc[0].to_dict()
        famc = R[(R.und == und) & R.sig.str.startswith("FADE_")]
        impc = R[(R.und == und) & R.sig.str.startswith("IMP_")]
        verdict[und] = {"primary": p, "family_net_pos": int((famc.net_sess > 0).sum()), "family_cells": int(len(famc)),
                        "imp_net_neg": int((impc.net_sess < 0).sum()), "imp_cells": int(len(impc))}
    print(json.dumps(verdict, indent=1, default=float))
    json.dump(verdict, open("m4_verdict.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
