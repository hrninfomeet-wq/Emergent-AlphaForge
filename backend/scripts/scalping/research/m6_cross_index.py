"""M6 - does NIFTY lead SENSEX option quotes? (discovery: 6 depth sessions, executable quotes)

1. Lead-lag correlations of 1-s changes: dNIFTY_spot vs dSENSEX_spot, dNIFTY_syn vs dSENSEX_syn,
   dNIFTY_syn vs dSENSEX option mid (ATM CE), at lags -5..+5 s (+lag = SENSEX later).
2. Laggard trade: z of the NIFTY synthetic forward over w s >= k AND |z| of the SENSEX synthetic over the
   same w < k/2 (SENSEX has not yet moved) -> buy SENSEX ATM CE (PE for down) at ask[t+1], exit bid[t+1+H].
   Non-overlapping per side. Also the same without the laggard condition.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from grid import atm_key, build
from m3_signals import side_arrays, zscore
from m4_ltp_holdout import stat_pct

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
WS, KS, HS, L = [5, 10, 20], [2.0, 2.5, 3.0], [5, 10, 30], 1


def syn_of(G):
    kc, bc, ac, *_ , agec = side_arrays(G, "CE")
    kp, bp, ap, *_ , agep = side_arrays(G, "PE")
    syn = (bc + ac) / 2 - (bp + ap) / 2 + G["grid"]["atm"].to_numpy(float)
    syn[(agec > 3) | (agep > 3)] = np.nan
    return syn, (kc, bc, ac), (kp, bp, ap)


def corr_lags(a, b):
    out = {}
    da, db_ = np.diff(a), np.diff(b)
    for lag in range(-5, 6):
        x = da[max(0, -lag): len(da) - max(0, lag)]
        y = db_[max(0, lag): len(db_) - max(0, -lag)]
        m = ~np.isnan(x) & ~np.isnan(y)
        out[lag] = round(float(np.corrcoef(x[m], y[m])[0, 1]), 3) if m.sum() > 200 else None
    return out


def main():
    rows, corrs = [], {}
    for day in DAYS:
        GN, GS = build(day, "NIFTY"), build(day, "SENSEX")
        if GN is None or GS is None:
            continue
        sn, _, _ = syn_of(GN)
        ss, (kc, bc, ac), (kp, bp, ap) = syn_of(GS)
        spN = GN["grid"]["spot"].to_numpy(float); spS = GS["grid"]["spot"].to_numpy(float)
        corrs[day] = {"spot": corr_lags(spN, spS), "syn": corr_lags(sn, ss),
                      "nifty_syn_vs_sensex_ce_mid": corr_lags(sn, (bc + ac) / 2)}
        print(day, corrs[day], flush=True)
        n = len(GS["grid"])
        for w in WS:
            zn, zs = zscore(sn, w), zscore(ss, w)
            for k in KS:
                for lag_cond in (True, False):
                    for side, fire in (("CE", zn >= k), ("PE", zn <= -k)):
                        if lag_cond:
                            fire = fire & (np.abs(zs) < k / 2)
                        keys = kc if side == "CE" else kp
                        for H in HS:
                            nxt = -1
                            for t in np.flatnonzero(fire):
                                if t < 300 or t >= n - 1800 or t <= nxt or not keys[t]:
                                    continue
                                S = GS["series"][keys[t]]
                                e, x = t + L, t + L + H
                                a, b = S["ask"].iat[e], S["bid"].iat[x]
                                if np.isnan(a) or np.isnan(b) or S["age"].iat[e] > 3:
                                    continue
                                g = 100 * (b - a) / a
                                rows.append({"day": day, "sig": f"NL_{w}_{k}_{'lag' if lag_cond else 'all'}", "H": H,
                                             "ret": g, "net": g - stat_pct("SENSEX", a, b)})
                                nxt = x
    df = pd.DataFrame(rows)
    df.to_pickle("m6_rows.pkl")
    res = []
    for (sig, H), g in df.groupby(["sig", "H"]):
        per = g.groupby("day")["ret"].mean()
        t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 2 and per.std(ddof=1) > 0 else np.nan
        res.append({"sig": sig, "H": H, "n": len(g), "sess": len(per), "gross_sess": round(per.mean(), 3),
                    "net_sess": round(g.groupby("day")["net"].mean().mean(), 3), "t": round(float(t), 2),
                    "sess_pos": int((per > 0).sum())})
    R = pd.DataFrame(res).sort_values("gross_sess", ascending=False)
    R.to_csv("m6_results.csv", index=False)
    print(R.head(20).to_string(index=False)); print(R.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
