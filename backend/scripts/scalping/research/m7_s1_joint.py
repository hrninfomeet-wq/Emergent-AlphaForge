"""M7 - PREREG_S1 joint-impulse test: discovery (6 depth sessions, executable quotes) + holdout (22 ltpc sessions)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import m4_ltp_holdout as H
from common import INDEX_KEY, SEG, STRIKE_STEP, db, day_bounds_ms
from grid import build
from m3_signals import side_arrays, zscore

WS, KS, HS, L = [10, 20, 30], [2.0, 2.5, 3.0], [30, 60], 1
PRIMARY = ("20", "2.5", 60)
DISC = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]


def run_signals(day, spS, spN, n, get_trade):
    rows = []
    for w in WS:
        zs, zn = zscore(spS, w), zscore(spN, w)
        for k in KS:
            up = (zs >= k) & (zn >= k)
            dn = (zs <= -k) & (zn <= -k)
            for Hh in HS:
                for side, fire, tag in (("CE", up, "MOM"), ("PE", dn, "MOM"), ("PE", up, "REV"), ("CE", dn, "REV")):
                    nxt = -1
                    for t in np.flatnonzero(fire):
                        if t < 300 or t >= n - 1800 or t <= nxt:
                            continue
                        r = get_trade(t, side, Hh)
                        if r is None:
                            continue
                        rows.append({"day": day, "sig": f"{tag}_{w}_{k}", "H": Hh, "ret": r[0], "net": r[1]})
                        nxt = t + L + Hh
    return rows


def discovery():
    rows = []
    for day in DISC:
        GS, GN = build(day, "SENSEX"), build(day, "NIFTY")
        if GS is None or GN is None:
            continue
        n = len(GS["grid"])
        spS = GS["grid"]["spot"].to_numpy(float); spN = GN["grid"]["spot"].to_numpy(float)
        kc = side_arrays(GS, "CE")[0]; kp = side_arrays(GS, "PE")[0]

        def trade(t, side, Hh):
            keys = kc if side == "CE" else kp
            if not keys[t]:
                return None
            S = GS["series"][keys[t]]
            e, x = t + L, t + L + Hh
            if x >= n:
                return None
            a, b = S["ask"].iat[e], S["bid"].iat[x]
            if np.isnan(a) or np.isnan(b) or S["age"].iat[e] > 3:
                return None
            g = 100 * (b - a) / a
            return g, g - H.stat_pct("SENSEX", a, b)
        rows += run_signals(day, spS, spN, n, trade)
    return pd.DataFrame(rows)


def holdout():
    rows = []
    for day in H.holdout_days():
        lo, hi = day_bounds_ms(day)
        secs = H.secs_for(day); n = len(secs)
        sp = {}
        for und in ("SENSEX", "NIFTY"):
            idx = pd.DataFrame(list(db().ticks.find({"instrument_key": INDEX_KEY[und], "ts": {"$gte": lo, "$lte": hi}},
                                                    {"_id": 0, "received_ts": 1, "last_price": 1})))
            if len(idx) < 5000:
                break
            sp[und] = idx.assign(s=idx["received_ts"] // 1000).groupby("s")["last_price"].last().reindex(secs).ffill().to_numpy(float)
        if len(sp) < 2:
            continue
        tm = H.token_map(day, "SENSEX")
        opt = pd.DataFrame(list(db().ticks.find({"instrument_key": {"$regex": "^BSE_FO\\|"}, "ts": {"$gte": lo, "$lte": hi}},
                                                {"_id": 0, "instrument_key": 1, "received_ts": 1, "last_price": 1})))
        if opt.empty:
            continue
        opt = opt[opt["instrument_key"].isin(tm.keys())]
        opt["strike"] = opt["instrument_key"].map(lambda k: tm[k][0]); opt["side"] = opt["instrument_key"].map(lambda k: tm[k][1])
        opt["expiry"] = opt["instrument_key"].map(lambda k: tm[k][2])
        opt = opt[opt["expiry"] == sorted(opt["expiry"].unique())[0]]
        ser = {}
        for (k, sd), g in opt.assign(s=opt["received_ts"] // 1000).groupby(["strike", "side"]):
            last = g.groupby("s")["last_price"].last().reindex(secs)
            upd = np.where(last.notna(), secs, np.nan)
            ser[(float(k), sd)] = (last.ffill().to_numpy(float), secs - pd.Series(upd).ffill().to_numpy())
        atm = np.round(sp["SENSEX"] / 100) * 100
        half = H.SPREAD["SENSEX"] / 200

        def trade(t, side, Hh):
            key = (float(atm[t]), side) if not np.isnan(atm[t]) else None
            if key not in ser:
                return None
            ltp, age = ser[key]
            e, x = t + L, t + L + Hh
            if x >= n or np.isnan(ltp[e]) or age[e] > 3 or np.isnan(ltp[x]):
                return None
            pin, pout = ltp[e] * (1 + half), ltp[x] * (1 - half)
            g = 100 * (pout - pin) / pin
            return g, g - H.stat_pct("SENSEX", pin, pout)
        rows += run_signals(day, sp["SENSEX"], sp["NIFTY"], n, trade)
        print(day, len(rows), flush=True)
    return pd.DataFrame(rows)


def summarize(df, label):
    res = []
    for (sig, Hh), g in df.groupby(["sig", "H"]):
        per = g.groupby("day")["net"].mean()
        t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 and per.std(ddof=1) > 0 else np.nan
        res.append({"sample": label, "sig": sig, "H": Hh, "n": len(g), "sess": len(per),
                    "gross": round(g.groupby("day")["ret"].mean().mean(), 3), "net": round(per.mean(), 3),
                    "t_net": round(float(t), 2), "share_pos": round(float((per > 0).mean()), 3)})
    return pd.DataFrame(res)


def main():
    D = summarize(discovery(), "discovery")
    Hd = summarize(holdout(), "holdout")
    R = pd.concat([D, Hd])
    R.to_csv("m7_results.csv", index=False)
    print(R.sort_values(["sample", "sig", "H"]).to_string(index=False))
    mom_h = Hd[Hd.sig.str.startswith("MOM")]
    p = Hd[(Hd.sig == f"MOM_{PRIMARY[0]}_{PRIMARY[1]}") & (Hd.H == PRIMARY[2])]
    pd_ = D[(D.sig == f"MOM_{PRIMARY[0]}_{PRIMARY[1]}") & (D.H == PRIMARY[2])]
    v = {"primary_holdout": p.to_dict("records"), "primary_discovery": pd_.to_dict("records"),
         "family_holdout_net_pos": int((mom_h.net > 0).sum()), "family_cells": int(len(mom_h))}
    print(json.dumps(v, indent=1, default=float))
    json.dump(v, open("m7_verdict.json", "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
