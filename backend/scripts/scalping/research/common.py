"""Shared loaders for the scalping microstructure measurements (read-only Mongo)."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd
from pymongo import MongoClient

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MONGO = "mongodb://127.0.0.1:27017"
INDEX_KEY = {"NIFTY": "NSE_INDEX|Nifty 50", "SENSEX": "BSE_INDEX|SENSEX"}
SEG = {"NIFTY": "NSE_FO", "SENSEX": "BSE_FO"}
STRIKE_STEP = {"NIFTY": 50, "SENSEX": 100}


@lru_cache(maxsize=1)
def db():
    return MongoClient(MONGO, serverSelectionTimeoutMS=5000)["alphaforge"]


def day_bounds_ms(day: str):
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    start = d.replace(hour=9, minute=0)
    end = d.replace(hour=15, minute=45)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def contract_map(day: str, underlying: str) -> pd.DataFrame:
    """token -> strike/side/expiry for contracts quoted on `day`, from that day's chain snapshots
    (identity-safe: tokens are recycled across expiries, so only a same-day source is trusted)."""
    rows = {}
    for snap in db().chain_snapshots.find({"session_date": day, "instrument": underlying},
                                          {"expiry_date": 1, "strikes.strike": 1,
                                           "strikes.ce.instrument_key": 1, "strikes.pe.instrument_key": 1}):
        exp = snap["expiry_date"]
        for s in snap.get("strikes") or []:
            for side in ("ce", "pe"):
                leg = s.get(side) or {}
                key = leg.get("instrument_key")
                if key:
                    rows[key] = (s["strike"], side.upper(), exp)
    # Fallback: current contracts (2-part key) with expiry >= day
    for c in db().option_contracts.find({"underlying": underlying, "segment": SEG[underlying],
                                         "expiry_date": {"$gte": day}},
                                        {"instrument_key": 1, "strike": 1, "side": 1, "expiry_date": 1,
                                         "exchange_token": 1, "lot_size": 1}):
        key = f"{SEG[underlying]}|{c['exchange_token']}"
        if key not in rows and c["instrument_key"].count("|") == 1:
            rows[key] = (c["strike"], c["side"], c["expiry_date"])
    df = pd.DataFrame([(k, *v) for k, v in rows.items()], columns=["instrument_key", "strike", "side", "expiry"])
    return df


def lot_size(underlying: str, day: str) -> int:
    c = db().option_contracts.find_one({"underlying": underlying, "expiry_date": {"$gte": day}},
                                       {"lot_size": 1}, sort=[("expiry_date", 1)])
    return int(c["lot_size"])


def load_index_ticks(day: str, underlying: str, coll: str = "tick_archive") -> pd.DataFrame:
    lo, hi = day_bounds_ms(day)
    cur = db()[coll].find({"instrument_key": INDEX_KEY[underlying], "ts": {"$gte": lo, "$lte": hi}},
                          {"_id": 0, "ts": 1, "ingest_ts": 1, "received_ts": 1, "last_price": 1})
    return _finish(pd.DataFrame(list(cur)))


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    """Older recordings (before 2026-09-15) carry no local ingest_ts; fall back to the Upstox
    frame clock received_ts and flag it - latency numbers from those days are NOT local-clock."""
    if df.empty:
        return df
    if "ingest_ts" not in df.columns:
        df["ingest_ts"] = np.nan
    df["clock_fallback"] = df["ingest_ts"].isna()
    df["ingest_ts"] = df["ingest_ts"].fillna(df["received_ts"]).astype("int64")
    return df.sort_values(["ingest_ts", "ts"]).reset_index(drop=True)


def load_option_ticks(day: str, underlying: str, coll: str = "tick_archive") -> pd.DataFrame:
    lo, hi = day_bounds_ms(day)
    proj = {"_id": 0, "instrument_key": 1, "ts": 1, "ingest_ts": 1, "received_ts": 1, "last_price": 1,
            "last_trade_quantity": 1, "best_bid_price": 1, "best_ask_price": 1, "best_bid_quantity": 1,
            "best_ask_quantity": 1, "volume_traded_today": 1, "market_depth": 1, "open_interest": 1}
    cur = db()[coll].find({"instrument_key": {"$regex": f"^{SEG[underlying]}\\|"},
                           "ts": {"$gte": lo, "$lte": hi}}, proj)
    return _finish(pd.DataFrame(list(cur)))


def trading_dte(day: str, expiry: str) -> int:
    """Weekday-count DTE (holidays ignored - good enough for bucketing; 0 = expiry day)."""
    d0 = np.datetime64(day)
    d1 = np.datetime64(expiry)
    return int(np.busday_count(d0, d1))


def attach_spot_and_moneyness(opt: pd.DataFrame, idx: pd.DataFrame, cmap: pd.DataFrame,
                              underlying: str, day: str) -> pd.DataFrame:
    opt = opt.merge(cmap, on="instrument_key", how="inner")
    opt = opt.sort_values("ingest_ts")
    sp = idx[["ingest_ts", "last_price"]].rename(columns={"last_price": "spot"}).sort_values("ingest_ts")
    opt = pd.merge_asof(opt, sp, on="ingest_ts", direction="backward")
    step = STRIKE_STEP[underlying]
    opt["atm_strike"] = (opt["spot"] / step).round() * step
    # signed moneyness in strikes: + = ITM
    k = (opt["strike"] - opt["atm_strike"]) / step
    opt["mny"] = np.where(opt["side"] == "CE", -k, k).astype(float)
    exps = sorted(opt["expiry"].unique())
    opt["expiry_rank"] = opt["expiry"].map({e: i for i, e in enumerate(exps)})
    opt["dte"] = opt["expiry"].map(lambda e: trading_dte(day, e))
    t = pd.to_datetime(opt["ingest_ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    opt["tod_min"] = t.dt.hour * 60 + t.dt.minute
    return opt
