"""Per-second executable quote grid for the ATM nearest-expiry CE and PE, plus the index.

For each session second s (local ingest clock, 09:15:00..15:29:59):
  spot[s]           last index print at or before s
  For side in CE/PE, the contract = nearest-expiry strike closest to spot[s] (re-centred each second)
  bid/ask/mid[s]    last quote of THAT contract at or before s, with quote_age_s
Forward paths are computed on the SAME contract that was ATM at entry (a held position does
not re-centre), so per contract we keep its own 1-s series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (IST, STRIKE_STEP, attach_spot_and_moneyness, contract_map, load_index_ticks,
                    load_option_ticks, lot_size)

SESSION_START = (9, 15)
SESSION_END = (15, 30)
MAX_QUOTE_AGE_S = 3


def session_seconds(day: str) -> np.ndarray:
    import datetime as dt
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    a = int(d.replace(hour=SESSION_START[0], minute=SESSION_START[1]).timestamp())
    b = int(d.replace(hour=SESSION_END[0], minute=SESSION_END[1]).timestamp())
    return np.arange(a, b)


def build(day: str, und: str):
    idx = load_index_ticks(day, und)
    opt = load_option_ticks(day, und)
    if idx.empty or opt.empty:
        return None
    cmap = contract_map(day, und)
    opt = attach_spot_and_moneyness(opt, idx, cmap, und, day)
    opt = opt[opt["best_bid_price"].notna() & opt["best_ask_price"].notna()
              & (opt["best_bid_price"] > 0) & (opt["best_ask_price"] > opt["best_bid_price"])]
    near_exp = sorted(opt["expiry"].unique())[0]
    opt = opt[opt["expiry"] == near_exp]
    secs = session_seconds(day)
    grid = pd.DataFrame({"s": secs})
    # index
    ix = idx.assign(s=idx["ingest_ts"] // 1000).groupby("s")["last_price"].last()
    grid["spot"] = ix.reindex(secs).ffill().to_numpy()
    grid["spot_fresh"] = ix.reindex(secs).notna().to_numpy()
    step = STRIKE_STEP[und]
    grid["atm"] = (grid["spot"] / step).round() * step

    # per-contract 1-s series
    opt = opt.assign(s=opt["ingest_ts"] // 1000)
    series = {}
    for key, g in opt.groupby("instrument_key"):
        last = g.groupby("s").agg(bid=("best_bid_price", "last"), ask=("best_ask_price", "last"),
                                  bq=("best_bid_quantity", "last"), aq=("best_ask_quantity", "last"),
                                  strike=("strike", "first"), side=("side", "first"))
        r = last.reindex(secs)
        r["upd"] = r["bid"].notna()
        r["last_upd_s"] = np.where(r["upd"], secs, np.nan)
        r = r.ffill()
        r["age"] = secs - r["last_upd_s"]
        series[key] = r
    keymap = {(float(r["strike"].dropna().iloc[0]), r["side"].dropna().iloc[0]): k
              for k, r in series.items() if r["strike"].notna().any()}
    return {"day": day, "und": und, "grid": grid, "series": series, "keymap": keymap,
            "expiry": near_exp, "lot": lot_size(und, day), "secs": secs}


def atm_key(G, i, side, offset=0):
    """Contract key that is ATM(+offset strikes, + = ITM) for `side` at grid row i."""
    step = STRIKE_STEP[G["und"]]
    atm = G["grid"]["atm"].iat[i]
    if np.isnan(atm):
        return None
    strike = atm - offset * step if side == "CE" else atm + offset * step
    return G["keymap"].get((float(strike), side))
