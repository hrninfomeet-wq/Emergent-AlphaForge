"""M1 - measured microstructure per index from recorded Upstox full-mode ticks (tick_archive).

Outputs JSON + a printed summary:
  * index feed cadence (updates/s, inter-arrival) and exchange-ts -> local ingest lag
  * ATM (nearest-expiry, |moneyness| <= 0.5 strike) option quote: spread (pts, % of mid),
    touch size in lots, 5-level depth in lots, quote inter-arrival, by time-of-day bucket
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from common import (attach_spot_and_moneyness, contract_map, load_index_ticks, load_option_ticks,
                    lot_size)

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
TOD_BUCKETS = [(555, 585, "09:15-09:45"), (585, 690, "09:45-11:30"), (690, 810, "11:30-13:30"),
               (810, 900, "13:30-15:00"), (900, 930, "15:00-15:30"), (930, 941, "15:30-15:40")]


def q(s, p):
    s = pd.Series(s).dropna()
    return None if s.empty else round(float(s.quantile(p)), 4)


def tod_label(m):
    for lo, hi, lab in TOD_BUCKETS:
        if lo <= m < hi:
            return lab
    return None


def summarize_day(day, und):
    idx = load_index_ticks(day, und)
    opt = load_option_ticks(day, und)
    if idx.empty or opt.empty:
        return None
    lot = lot_size(und, day)
    cmap = contract_map(day, und)
    out = {"day": day, "underlying": und, "lot_size": lot}

    # --- index feed cadence
    it = idx["ingest_ts"].to_numpy()
    out["index_ticks"] = int(len(idx))
    out["index_interarrival_ms"] = {"p50": q(np.diff(it), .5), "p90": q(np.diff(it), .9),
                                    "p99": q(np.diff(it), .99)}
    secs = (idx["ingest_ts"] // 1000)
    span = secs.max() - secs.min() + 1
    out["index_share_of_seconds_with_update"] = round(secs.nunique() / span, 4)
    out["index_distinct_exchange_ts_per_min"] = round(idx["ts"].nunique() / (span / 60), 2)
    out["index_ts_to_ingest_ms"] = {"p50": q(idx["ingest_ts"] - idx["ts"], .5),
                                    "p90": q(idx["ingest_ts"] - idx["ts"], .9)}
    out["index_received_to_ingest_ms"] = {"p50": q(idx["ingest_ts"] - idx["received_ts"], .5),
                                          "p90": q(idx["ingest_ts"] - idx["received_ts"], .9)}

    # --- option quotes
    opt = attach_spot_and_moneyness(opt, idx, cmap, und, day)
    opt = opt[opt["best_bid_price"].notna() & opt["best_ask_price"].notna()
              & (opt["best_bid_price"] > 0) & (opt["best_ask_price"] > opt["best_bid_price"])]
    out["option_instruments_seen"] = int(opt["instrument_key"].nunique())
    out["expiries_seen"] = sorted(map(str, opt["expiry"].unique()))
    near = opt[(opt["expiry_rank"] == 0) & (opt["mny"].abs() <= 0.5)].copy()
    near["mid"] = (near["best_bid_price"] + near["best_ask_price"]) / 2
    near["spread"] = near["best_ask_price"] - near["best_bid_price"]
    near["spread_pct"] = 100 * near["spread"] / near["mid"]
    near["touch_lots"] = np.minimum(near["best_bid_quantity"], near["best_ask_quantity"]) / lot
    near["ask_lots"] = near["best_ask_quantity"] / lot

    def depth_lots(d, side):
        try:
            return sum((lv.get(f"{side}_quantity") or 0) for lv in d) / lot
        except TypeError:
            return np.nan
    near["ask5_lots"] = near["market_depth"].map(lambda d: depth_lots(d, "ask"))
    near["bid5_lots"] = near["market_depth"].map(lambda d: depth_lots(d, "bid"))
    near["tod"] = near["tod_min"].map(tod_label)
    out["atm_dte"] = int(near["dte"].iloc[0]) if len(near) else None
    out["atm_quote_updates"] = int(len(near))

    # per-instrument inter-arrival for ATM quotes
    ia = near.groupby("instrument_key")["ingest_ts"].diff().dropna()
    out["atm_quote_interarrival_ms"] = {"p50": q(ia, .5), "p90": q(ia, .9), "p99": q(ia, .99)}
    out["atm_ts_to_ingest_ms"] = {"p50": q(near["ingest_ts"] - near["ts"], .5),
                                  "p90": q(near["ingest_ts"] - near["ts"], .9)}
    out["atm_trade_tick_share"] = round(float((near["last_trade_quantity"].fillna(0) > 0).mean()), 4)

    by = {}
    for lab, g in near.groupby("tod"):
        by[lab] = {
            "n": int(len(g)),
            "premium_mid_p50": q(g["mid"], .5),
            "spread_pts": {"p25": q(g["spread"], .25), "p50": q(g["spread"], .5), "p90": q(g["spread"], .9)},
            "spread_pct_mid": {"p50": q(g["spread_pct"], .5), "p90": q(g["spread_pct"], .9)},
            "touch_lots": {"p10": q(g["touch_lots"], .1), "p50": q(g["touch_lots"], .5)},
            "ask_touch_lots_p10": q(g["ask_lots"], .1),
            "ask5_lots": {"p10": q(g["ask5_lots"], .1), "p50": q(g["ask5_lots"], .5)},
            "bid5_lots": {"p10": q(g["bid5_lots"], .1), "p50": q(g["bid5_lots"], .5)},
        }
    out["atm_by_tod"] = by
    out["atm_all"] = {
        "premium_mid_p50": q(near["mid"], .5),
        "spread_pts": {"p25": q(near["spread"], .25), "p50": q(near["spread"], .5),
                       "p75": q(near["spread"], .75), "p90": q(near["spread"], .9),
                       "p99": q(near["spread"], .99)},
        "spread_ticks_p50": q(near["spread"] / 0.05, .5),
        "spread_pct_mid": {"p50": q(near["spread_pct"], .5), "p90": q(near["spread_pct"], .9)},
        "touch_lots": {"p10": q(near["touch_lots"], .1), "p50": q(near["touch_lots"], .5)},
        "ask5_lots": {"p10": q(near["ask5_lots"], .1), "p50": q(near["ask5_lots"], .5)},
    }
    # second expiry ATM for comparison
    nxt = opt[(opt["expiry_rank"] == 1) & (opt["mny"].abs() <= 0.5)].copy()
    if len(nxt):
        nxt["spread"] = nxt["best_ask_price"] - nxt["best_bid_price"]
        nxt["mid"] = (nxt["best_bid_price"] + nxt["best_ask_price"]) / 2
        out["atm_next_expiry"] = {"dte": int(nxt["dte"].iloc[0]), "premium_mid_p50": q(nxt["mid"], .5),
                                  "spread_pts_p50": q(nxt["spread"], .5), "spread_pts_p90": q(nxt["spread"], .9),
                                  "spread_pct_p50": q(100 * nxt["spread"] / nxt["mid"], .5)}
    return out


def main():
    days = sys.argv[1:] or DAYS
    res = []
    for day in days:
        for und in ("NIFTY", "SENSEX"):
            r = summarize_day(day, und)
            if r:
                res.append(r)
                print(json.dumps({k: r[k] for k in ("day", "underlying", "lot_size", "atm_dte", "index_ticks",
                                                    "index_interarrival_ms", "index_ts_to_ingest_ms",
                                                    "atm_quote_updates", "atm_quote_interarrival_ms",
                                                    "atm_ts_to_ingest_ms", "atm_all")}), flush=True)
    with open("m1_results.json", "w") as f:
        json.dump(res, f, indent=1, default=str)


if __name__ == "__main__":
    main()
