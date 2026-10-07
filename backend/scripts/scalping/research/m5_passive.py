"""M5 - passive (inside-spread) entry vs taker entry, 1-s snapshot tape, ATM nearest expiry.

At decision second t (every 5 s, 09:20-15:00, both sides): post BUY limit P = bid[t] + 1 tick when the spread is
>= 3 ticks (else P = bid[t]). Fill rules (both reported):
  CONSERVATIVE: filled at the first j in (t, t+FILL_WAIT] with ask[j] <= P   (an offer reached our price)
  OPTIMISTIC:   filled at the first j with ltp-trade (ltq>0 at j) at price <= P
Unfilled -> cancelled (no trade). After a fill at P: exit
  TAKER_EXIT:   sell at bid[j+H]
  PASSIVE_EXIT: post SELL at ask[j]-1 tick; filled at first k in (j, j+H] with bid[k] >= that price, else bid[j+H]
Reports fill rate, gross % of P per filled trade, and adverse selection = mid[j+H] - mid[j] after fill vs the
unconditional mid drift. Exploratory only (6 sessions; no holdout with quotes exists).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from grid import atm_key, build
from m4_ltp_holdout import stat_pct

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
TICK = 0.05
FILL_WAIT = [5, 15, 30]
HS = [15, 30, 60]


def ltp_series(G, key, day, und):
    from common import db, day_bounds_ms
    lo, hi = day_bounds_ms(day)
    rows = list(db().tick_archive.find({"instrument_key": key, "ts": {"$gte": lo, "$lte": hi}},
                                       {"_id": 0, "ingest_ts": 1, "received_ts": 1, "last_price": 1, "last_trade_quantity": 1}))
    df = pd.DataFrame(rows)
    if "ingest_ts" not in df:
        df["ingest_ts"] = np.nan
    df["ingest_ts"] = df["ingest_ts"].fillna(df["received_ts"])
    df = df[df["last_trade_quantity"].fillna(0) > 0]
    s = df.assign(s=(df["ingest_ts"] // 1000).astype("int64")).groupby("s")["last_price"].min()
    return s.reindex(G["secs"]).to_numpy(float)


def run_day(day, und):
    G = build(day, und)
    if G is None:
        return []
    n = len(G["grid"])
    out = []
    ltp_cache = {}
    for side in ("CE", "PE"):
        for t in range(300, n - 1800, 5):
            key = atm_key(G, t, side)
            if not key:
                continue
            S = G["series"][key]
            bid, ask, age = S["bid"].to_numpy(float), S["ask"].to_numpy(float), S["age"].to_numpy(float)
            if np.isnan(bid[t]) or age[t] > 2:
                continue
            if key not in ltp_cache:
                ltp_cache[key] = ltp_series(G, key, day, und)
            trd = ltp_cache[key]
            spread_ticks = round((ask[t] - bid[t]) / TICK)
            P = bid[t] + TICK if spread_ticks >= 3 else bid[t]
            for W in FILL_WAIT:
                for mode in ("cons", "opt"):
                    j = None
                    for jj in range(t + 1, min(t + W, n - 1) + 1):
                        if (mode == "cons" and ask[jj] <= P) or (mode == "opt" and not np.isnan(trd[jj]) and trd[jj] <= P):
                            j = jj
                            break
                    rec = {"day": day, "und": und, "side": side, "W": W, "mode": mode, "spread_ticks": spread_ticks,
                           "filled": j is not None}
                    if j is not None:
                        for H in HS:
                            x = min(j + H, n - 1)
                            if np.isnan(bid[x]):
                                continue
                            rec[f"taker_{H}"] = 100 * (bid[x] - P) / P
                            sp = ask[j] - TICK
                            kk = next((k for k in range(j + 1, x + 1) if bid[k] >= sp), None)
                            px = sp if kk is not None else bid[x]
                            rec[f"passive_{H}"] = 100 * (px - P) / P
                            rec[f"adv_{H}"] = 100 * (((bid[x] + ask[x]) / 2) - ((bid[j] + ask[j]) / 2)) / P
                            rec[f"stat_{H}"] = stat_pct(und, P, px)
                    out.append(rec)
    return out


def main():
    rows = []
    for d in DAYS:
        for und in ("NIFTY", "SENSEX"):
            rows += run_day(d, und)
            print(d, und, len(rows), flush=True)
    df = pd.DataFrame(rows)
    df.to_pickle("m5_rows.pkl")
    res = []
    for (und, W, mode), g in df.groupby(["und", "W", "mode"]):
        f = g[g.filled]
        r = {"und": und, "W": W, "mode": mode, "fill_rate": round(g.filled.mean(), 3), "fills": len(f),
             "spread_ticks_p50": float(g.spread_ticks.median())}
        for H in HS:
            per_t = f.groupby("day")[f"taker_{H}"].mean()
            per_p = f.groupby("day")[f"passive_{H}"].mean()
            r[f"taker_{H}"] = round(per_t.mean(), 3)
            r[f"passive_{H}"] = round(per_p.mean(), 3)
            r[f"passive_net_{H}"] = round((f[f"passive_{H}"] - f[f"stat_{H}"]).groupby(f["day"]).mean().mean(), 3)
            r[f"adv_{H}"] = round(f.groupby("day")[f"adv_{H}"].mean().mean(), 3)
            r[f"pass_sess_pos_{H}"] = int((per_p > 0).sum())
        res.append(r)
    R = pd.DataFrame(res)
    R.to_csv("m5_results.csv", index=False)
    print(R.to_string(index=False))


if __name__ == "__main__":
    main()
