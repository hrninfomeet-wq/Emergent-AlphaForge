"""M3 - exploratory sub-minute signal screen on executable 1-s quotes (6 depth sessions).

Every signal is evaluated as: decide at second t on data available at t, buy at ask[t+L] (L=1 s),
exit at bid[t+L+H] (no bracket) - non-overlapping per (signal, side): after an entry, the next
entry for that signal may only fire after the exit second. Gross % of entry premium; the null
baseline is M2 (about -0.23%).

Signals (all causal):
  IMP_w_k   index impulse momentum: z = (spot[t]-spot[t-w]) / (sigma_1s * sqrt(w)), sigma_1s =
            std of 1-s spot changes over the trailing 600 s; z>=k -> buy ATM CE, z<=-k -> buy ATM PE
  FADE_w_k  same trigger, opposite side
  SYN_w_k   impulse on the synthetic forward F = midCE - midPE + K (ATM strike), normalised the same way
  OBI_th    touch imbalance of the ATM option itself: (bidQ-askQ)/(bidQ+askQ) >= th -> buy that option
Also reports the lead-lag cross-correlation of 1-s changes: spot vs synthetic forward.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from grid import STRIKE_STEP, atm_key, build
from m2_baseline import stat_cost_pct

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
L = 1
HS = [10, 30, 60]
WS = [5, 10, 20, 30]
KS = [1.5, 2.0, 2.5, 3.0]
OBIS = [0.5, 0.7, 0.9]


def side_arrays(G, side):
    """Per-second arrays for the contract that is ATM at each second (bid/ask of THAT contract at the
    same second), and a dict key->series for forward evaluation."""
    n = len(G["grid"])
    keys = [atm_key(G, i, side) for i in range(n)]
    bid = np.full(n, np.nan); ask = np.full(n, np.nan); bq = np.full(n, np.nan); aq = np.full(n, np.nan)
    age = np.full(n, np.inf)
    for k in set(x for x in keys if x):
        S = G["series"][k]
        m = np.array([x == k for x in keys])
        bid[m] = S["bid"].to_numpy()[m]; ask[m] = S["ask"].to_numpy()[m]
        bq[m] = S["bq"].to_numpy()[m]; aq[m] = S["aq"].to_numpy()[m]; age[m] = S["age"].to_numpy()[m]
    return keys, bid, ask, bq, aq, age


def evaluate(G, fire_idx, side, keys, H):
    """Non-overlapping execution of entries at fire_idx (decision seconds) for `side`."""
    n = len(G["grid"])
    out = []
    next_free = -1
    for t in fire_idx:
        if t <= next_free:
            continue
        k = keys[t]
        if not k:
            continue
        S = G["series"][k]
        e, x = t + L, t + L + H
        if x >= n:
            break
        a = S["ask"].iat[e]; b = S["bid"].iat[x]
        if np.isnan(a) or np.isnan(b) or S["age"].iat[e] > 3:
            continue
        out.append((t, side, a, b, 100 * (b - a) / a))
        next_free = x
    return out


def zscore(series, w):
    d1 = np.diff(series, prepend=np.nan)
    sig = pd.Series(d1).rolling(600, min_periods=300).std().to_numpy()
    dw = series - np.concatenate([np.full(w, np.nan), series[:-w]])
    return dw / (sig * np.sqrt(w))


def run_day(day, und):
    G = build(day, und)
    if G is None:
        return [], None
    g = G["grid"]
    n = len(g)
    spot = g["spot"].to_numpy(float)
    kc, bc, ac, bqc, aqc, agec = side_arrays(G, "CE")
    kp, bp, ap, bqp, aqp, agep = side_arrays(G, "PE")
    syn = (bc + ac) / 2 - (bp + ap) / 2 + g["atm"].to_numpy(float)
    # synthetic is only valid when CE and PE are the same strike and fresh
    syn[(agec > 3) | (agep > 3)] = np.nan
    lo, hi = 5 * 60, n - 30 * 60
    window = np.zeros(n, bool); window[lo:hi] = True
    rows = []

    def add(name, fire, side, keys):
        for H in HS:
            for (t, s, a, b, r) in evaluate(G, np.flatnonzero(fire & window), side, keys, H):
                rows.append({"day": day, "und": und, "sig": name, "H": H, "t": t, "side": s, "pin": a,
                             "ret": r, "stat": stat_cost_pct(und, a, b)})

    for w in WS:
        zs = zscore(spot, w)
        zf = zscore(syn, w)
        for k in KS:
            add(f"IMP_{w}_{k}", zs >= k, "CE", kc); add(f"IMP_{w}_{k}", zs <= -k, "PE", kp)
            add(f"FADE_{w}_{k}", zs <= -k, "CE", kc); add(f"FADE_{w}_{k}", zs >= k, "PE", kp)
            add(f"SYN_{w}_{k}", zf >= k, "CE", kc); add(f"SYN_{w}_{k}", zf <= -k, "PE", kp)
    for th in OBIS:
        obc = (bqc - aqc) / (bqc + aqc)
        obp = (bqp - aqp) / (bqp + aqp)
        add(f"OBI_{th}", obc >= th, "CE", kc); add(f"OBI_{th}", obp >= th, "PE", kp)
        add(f"OBIneg_{th}", obc <= -th, "CE", kc); add(f"OBIneg_{th}", obp <= -th, "PE", kp)

    # lead-lag: corr(dspot[t], dsyn[t+lag])
    ds = np.diff(spot); df_ = np.diff(syn)
    ll = {}
    for lag in range(-5, 6):
        a = ds[max(0, -lag): len(ds) - max(0, lag)]
        b = df_[max(0, lag): len(df_) - max(0, -lag)]
        m = ~np.isnan(a) & ~np.isnan(b)
        ll[lag] = round(float(np.corrcoef(a[m], b[m])[0, 1]), 3) if m.sum() > 100 else None
    return rows, ll


def main():
    days = sys.argv[1:] or DAYS
    rows, lls = [], {}
    for day in days:
        for und in ("NIFTY", "SENSEX"):
            r, ll = run_day(day, und)
            rows += r
            lls[f"{day}_{und}"] = ll
            print(day, und, len(r), "leadlag(dspot vs dsyn at +lag)", ll, flush=True)
    df = pd.DataFrame(rows)
    df.to_pickle("m3_rows.pkl")
    res = []
    for (und, sig, H), g in df.groupby(["und", "sig", "H"]):
        per = g.groupby("day")["ret"].mean()
        net = g.groupby("day").apply(lambda x: (x["ret"] - x["stat"]).mean())
        t = per.mean() / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 1 and per.std(ddof=1) > 0 else np.nan
        res.append({"und": und, "sig": sig, "H": H, "n": len(g), "sessions": len(per),
                    "gross_mean_sess": round(per.mean(), 4), "net_mean_sess": round(net.mean(), 4),
                    "t_sess_gross": round(float(t), 2), "sess_pos_gross": int((per > 0).sum()),
                    "pooled_gross": round(g["ret"].mean(), 4), "hit_rate": round(float((g["ret"] > 0).mean()), 3)})
    R = pd.DataFrame(res).sort_values(["und", "gross_mean_sess"], ascending=[True, False])
    R.to_csv("m3_results.csv", index=False)
    with open("m3_leadlag.json", "w") as f:
        json.dump(lls, f, indent=1)
    for und, g in R.groupby("und"):
        print("==", und, "top 15 by session-mean gross % (baseline about -0.23%)")
        print(g.head(15).to_string(index=False))
        print("   bottom 5"); print(g.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
