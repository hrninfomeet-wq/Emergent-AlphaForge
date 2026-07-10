# backend/app/premium_momentum_backtest.py
"""Option-native backtest for the premium-momentum contingency strategy (Phase 1).

Per session: at the reference time, lock the chosen-moneyness CE and PE strikes
from spot, then walk each locked strike's premium series for a momentum entry +
premium exit (shared pure helpers). Single position: first side to trigger wins.
Coverage-gated: sessions whose locked strike lacks a premium series are excluded
and counted, never mis-filled."""
from __future__ import annotations

import functools
from typing import Any, Dict, List

import pandas as pd

from app.premium_momentum import (
    lock_reference_strike, premium_ohlc_for_key, stepped_trail_stop,
    walk_premium_momentum,
)


def _sides_for(param: str) -> List[str]:
    p = str(param or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


def run_premium_momentum_backtest(*, spot_df: pd.DataFrame, option_candles: pd.DataFrame,
                                  contracts: List[Dict[str, Any]], instrument: str,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    ref_time = str(params.get("reference_time") or "09:31")
    moneyness = str(params.get("moneyness") or "itm1")
    sides = _sides_for(params.get("side"))
    entry_pct = params.get("momentum_pct")
    entry_pts = params.get("momentum_pts")
    target_pct = params.get("target_pct")
    target_pts = params.get("target_pts")
    stop_pct = params.get("stop_pct")
    stop_pts = params.get("stop_pts")
    trail_x = params.get("trail_x")
    trail_y = params.get("trail_y")
    trail = None
    if trail_x is not None and trail_y is not None:
        trail = functools.partial(stepped_trail_stop, x=trail_x, y=trail_y)

    trades: List[Dict[str, Any]] = []
    cov = {"sessions_total": 0, "sessions_traded": 0, "sessions_excluded": 0,
           "sessions_no_signal": 0, "exclude_reasons": {}}

    for session, sdf in spot_df.groupby("session_date"):
        cov["sessions_total"] += 1
        sdf = sdf.sort_values("ts")
        ref_rows = sdf[sdf["ist_time"] >= ref_time]
        if ref_rows.empty:
            cov["sessions_excluded"] += 1
            cov["exclude_reasons"]["no_reference_bar"] = cov["exclude_reasons"].get("no_reference_bar", 0) + 1
            continue
        ref_row = ref_rows.iloc[0]
        spot_at_ref = float(ref_row["close"])
        ref_ts = int(ref_row["ts"])

        # Lock each side's strike + get its OHLC premium series FROM the reference bar on.
        candidates = []          # (side, locked, ohlc dict of arrays)
        excluded = False
        for side in sides:
            locked = lock_reference_strike(contracts=contracts, underlying=instrument,
                                           spot_at_ref=spot_at_ref, side=side, moneyness=moneyness)
            if not locked:
                excluded = True
                cov["exclude_reasons"]["no_contract"] = cov["exclude_reasons"].get("no_contract", 0) + 1
                break
            oh = premium_ohlc_for_key(option_candles, locked["instrument_key"])
            mask = oh["ts"] >= ref_ts
            oh = {k: v[mask] for k, v in oh.items()}
            if len(oh["close"]) == 0:
                excluded = True
                cov["exclude_reasons"]["no_premium_series"] = cov["exclude_reasons"].get("no_premium_series", 0) + 1
                break
            candidates.append((side, locked, oh))
        if excluded:
            cov["sessions_excluded"] += 1
            continue

        # Walk each candidate; keep the one that ENTERS EARLIEST (first-to-trigger).
        best = None   # (side, locked, ref_premium, r)
        for side, locked, oh in candidates:
            ref_premium = float(oh["close"][0])   # premium at/after the reference bar
            r = walk_premium_momentum(ts=oh["ts"], premium=oh["close"], ref_premium=ref_premium,
                                      entry_pct=entry_pct, entry_pts=entry_pts,
                                      target_pct=target_pct, target_pts=target_pts,
                                      stop_pct=stop_pct, stop_pts=stop_pts, trail=trail,
                                      low=oh["low"], open_=oh["open"])
            if not r.get("entered"):
                continue
            if best is None or r["entry_ts"] < best[3]["entry_ts"]:
                best = (side, locked, ref_premium, r)
        if best is None:
            cov["sessions_no_signal"] += 1
            continue
        side, locked, ref_premium, r = best
        trades.append({
            "session_date": str(session), "side": side, "strike": locked["strike"],
            "instrument_key": locked["instrument_key"], "moneyness": moneyness,
            "ref_premium": round(ref_premium, 4),
            **r,
        })
        cov["sessions_traded"] += 1

    return {"trades": trades, "coverage": cov, "params": dict(params)}
