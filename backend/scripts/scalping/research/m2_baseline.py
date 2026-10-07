"""M2 - the null baseline a scalping signal must beat, on executable 1-s quotes.

Random entry (every ENTRY_EVERY seconds, CE and PE, ATM nearest expiry, 09:20-15:00):
  buy at ask[t+L], sell at bid[t+L+H]   (L = decision->exchange latency in whole seconds)
  plus a symmetric/asymmetric bracket: exit when bid >= entry*(1+X) (target, sell at bid)
  or bid <= entry*(1-Y) (stop, sell at the bid one latency later), else at max hold.
All returns in % of entry premium, gross of statutory charges; charges reported separately.
Session-level aggregation: mean of per-session means, t over sessions.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from grid import atm_key, build

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
ENTRY_EVERY = 5
HORIZONS = [5, 10, 20, 30, 60, 120]
LATENCIES = [0, 1]
BRACKETS = [(x, y) for x in (0.5, 1.0, 2.0, 3.0) for y in (0.5, 1.0, 2.0)]
MAX_HOLD = [30, 60, 120]

# PROVISIONAL statutory rates (fraction of premium turnover) - replaced by the verified table.
STAT = {"NIFTY": {"stt_sell": 0.001, "txn": 0.0003503}, "SENSEX": {"stt_sell": 0.001, "txn": 0.000325}}
SEBI, STAMP_BUY, GST = 0.000001, 0.00003, 0.18


def stat_cost_pct(und, p_in, p_out):
    r = STAT[und]
    c = STAMP_BUY * p_in + (r["txn"] + SEBI) * (1 + GST) * (p_in + p_out) + r["stt_sell"] * p_out
    return 100 * c / p_in


def first_hit(win, tgt, stp):
    """win: (n, M) bids after entry; returns (exit_idx, kind) per row; kind 1=target, -1=stop, 0=none."""
    hit_t = win >= tgt[:, None]
    hit_s = win <= stp[:, None]
    any_t, any_s = hit_t.any(1), hit_s.any(1)
    it = np.where(any_t, hit_t.argmax(1), 10**9)
    is_ = np.where(any_s, hit_s.argmax(1), 10**9)
    kind = np.where(it < is_, 1, np.where(is_ < it, -1, 0))
    idx = np.minimum(it, is_)
    return idx, kind


def run_day(day, und):
    G = build(day, und)
    if G is None:
        return []
    g = G["grid"]
    n = len(g)
    t0, t1 = 5 * 60, n - 30 * 60
    maxM = max(MAX_HOLD + HORIZONS)
    rows = []
    for side in ("CE", "PE"):
        ent = np.arange(t0, t1, ENTRY_EVERY)
        keys = [atm_key(G, i, side) for i in ent]
        for key in set(k for k in keys if k):
            S = G["series"][key]
            bid = S["bid"].to_numpy(float); ask = S["ask"].to_numpy(float); age = S["age"].to_numpy(float)
            bpad = np.concatenate([bid, np.full(maxM + 5, np.nan)])
            sel = np.array([i for i, k in zip(ent, keys) if k == key])
            for L in LATENCIES:
                e = sel + L
                ok = (e < n)
                e = e[ok]
                ok2 = ~np.isnan(ask[e]) & (age[e] <= 3)
                e = e[ok2]
                if not len(e):
                    continue
                pin = ask[e]
                W = np.lib.stride_tricks.sliding_window_view(bpad, maxM)[e + 1]   # bids at e+1 .. e+maxM
                W = pd.DataFrame(W.T).ffill().to_numpy().T  # carry last bid over brief gaps
                rec = pd.DataFrame({"day": day, "und": und, "side": side, "L": L, "i": e - L, "pin": pin,
                                    "spread_pct": 100 * (ask[e] - bid[e]) / pin})
                for H in HORIZONS:
                    xb = W[:, H - 1]
                    rec[f"ret_{H}"] = 100 * (xb - pin) / pin
                    xa = np.concatenate([ask, np.full(maxM + 5, np.nan)])[e + H]
                    rec[f"mid_{H}"] = 100 * (((xb + xa) / 2) - (bid[e] + ask[e]) / 2) / pin
                    rec[f"mfe_{H}"] = 100 * (np.nanmax(W[:, :H], 1) - pin) / pin
                    rec[f"mae_{H}"] = 100 * (np.nanmin(W[:, :H], 1) - pin) / pin
                for (X, Y) in BRACKETS:
                    tgt, stp = pin * (1 + X / 100), pin * (1 - Y / 100)
                    for M in MAX_HOLD:
                        idx, kind = first_hit(W[:, :M], tgt, stp)
                        rr = np.arange(len(e))
                        tgt_px = W[rr, np.minimum(idx, M - 1)]
                        stop_px = W[rr, np.minimum(idx + max(L, 1), maxM - 1)]
                        end_px = W[:, M - 1]
                        out = np.where(kind == 1, tgt_px, np.where(kind == -1, stop_px, end_px))
                        rec[f"br_{X}_{Y}_{M}"] = 100 * (out - pin) / pin
                rec["stat_pct"] = [stat_cost_pct(und, p, p) for p in pin]
                rows.append(rec)
    return rows


def session_stats(df, col):
    per = df.groupby("day")[col].mean().dropna()
    m = per.mean()
    t = m / (per.std(ddof=1) / np.sqrt(len(per))) if len(per) > 1 and per.std(ddof=1) > 0 else np.nan
    return {"mean_of_session_means": round(float(m), 4), "t_sessions": round(float(t), 2),
            "sessions_positive": int((per > 0).sum()), "n_sessions": int(len(per)),
            "pooled_median": round(float(df[col].median()), 4), "n": int(df[col].notna().sum())}


def main():
    days = sys.argv[1:] or DAYS
    allrows = []
    for day in days:
        for und in ("NIFTY", "SENSEX"):
            r = run_day(day, und)
            print(day, und, sum(len(x) for x in r), flush=True)
            allrows += r
    df = pd.concat(allrows, ignore_index=True)
    df.to_pickle("m2_rows.pkl")
    out = {}
    for und, du in df.groupby("und"):
        o = {"spread_pct_entry_p50": round(float(du["spread_pct"].median()), 4),
             "stat_pct_roundtrip_p50": round(float(du["stat_pct"].median()), 4)}
        for L, dl in du.groupby("L"):
            ol = {}
            for H in HORIZONS:
                ol[f"H{H}"] = {"ret": session_stats(dl, f"ret_{H}"),
                               "mid_abs_p50": round(float(dl[f"mid_{H}"].abs().median()), 4),
                               "mid_std": round(float(dl[f"mid_{H}"].std()), 4),
                               "mfe_over_abs_mae_mean": round(float(dl[f"mfe_{H}"].mean() / abs(dl[f"mae_{H}"].mean())), 4)}
            for (X, Y) in BRACKETS:
                for M in MAX_HOLD:
                    c = f"br_{X}_{Y}_{M}"
                    ol[c] = session_stats(dl, c)
            o[f"L{L}"] = ol
        out[und] = o
    with open("m2_results.json", "w") as f:
        json.dump(out, f, indent=1)
    for und in out:
        print("==", und, "spread%", out[und]["spread_pct_entry_p50"], "stat%", out[und]["stat_pct_roundtrip_p50"])
        for L in ("L0", "L1"):
            ol = out[und][L]
            print(" ", L, " ".join(f"H{H}:{ol[f'H{H}']['ret']['mean_of_session_means']:+.3f}%(|mid|p50 {ol[f'H{H}']['mid_abs_p50']:.3f}, mfe/mae {ol[f'H{H}']['mfe_over_abs_mae_mean']:.2f})" for H in HORIZONS))
            best = sorted(((k, v) for k, v in ol.items() if k.startswith("br_")), key=lambda kv: -kv[1]["mean_of_session_means"])[:5]
            for k, v in best:
                print("    ", k, v)


if __name__ == "__main__":
    main()
