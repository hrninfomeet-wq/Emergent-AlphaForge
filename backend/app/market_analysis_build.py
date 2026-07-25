"""Assembly layer for the read-only market-analysis payload.

Composes the PURE primitives in ``app.market_analysis`` with the data the app
already stores/streams:

  * spot 1m candles (``candles_1m``)  -> indicators (ADX/ATR/swings) -> structure + S/R
  * daily closes (IST-day aggregation of the same collection) -> multi-timeframe trend
  * the live option universe + Upstox full-mode ticks -> PCR / max-pain / ATM straddle
  * INDIAVIX candles -> IV-rank (VIX-percentile proxy; there is no stored ATM-IV history)

Design rules:
  * READ-ONLY. No broker mutation, no writes, no order path. Safe to poll.
  * NEVER raises out of the endpoint: every stage is individually guarded and
    contributes a `warnings` code instead of a fabricated number. A missing
    stage yields nulls, so the UI renders "—" rather than a confident lie.
  * Portfolio greeks are deliberately NOT computed here: the cockpit already
    polls ``/live-broker/greeks`` and merges it client-side, which avoids a
    duplicate broker read and keeps this endpoint usable while disconnected.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.market_analysis import (
    atm_straddle,
    classify_structure,
    classify_trend,
    iv_rank,
    levels_from_sr,
    max_pain,
    put_call_ratio,
)

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

#: Bars of 1m history for indicators. recent_sr_levels wants >=60 trailing bars
#: and precompute_all_indicators needs a warm-up; 200 covers both (the same
#: lookback the deployment evaluator uses).
INTRADAY_LOOKBACK = 200

#: How many trailing DAILY closes each higher timeframe reads. The label is the
#: trend "as seen on" that timeframe: ~1 week, ~1 month, ~3 months of sessions.
TF_DAILY_WINDOWS = {"daily": 5, "weekly": 20, "monthly": 60}

#: Calendar days of 1m history scanned to build the daily-close series.
DAILY_SCAN_DAYS = 150


def _now_ist_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


async def _intraday_closes_and_frame(db: Any, instrument: str):
    """Return (enriched_df, closes) for the recent 1m window, or (None, [])."""
    from app.deployment_evaluator import _load_recent_candles
    from app.indicators import precompute_all_indicators

    df = await _load_recent_candles(db, instrument, lookback=INTRADAY_LOOKBACK)
    if df is None or getattr(df, "empty", True) or len(df) < 3:
        return None, []
    closes = [float(c) for c in df["close"].tolist() if c is not None]
    try:
        enriched = precompute_all_indicators(df, {})
    except Exception as exc:  # indicator warm-up can fail on a very short frame
        log.warning("market_analysis: indicator precompute failed (%s)", exc)
        return None, closes
    return enriched, closes


async def _daily_closes(db: Any, instrument: str) -> List[float]:
    """Last close per IST trading day, oldest-first.

    Aggregates ``candles_1m`` server-side (one close per day) instead of pulling
    ~35k intraday rows into the process just to resample them.
    """
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=DAILY_SCAN_DAYS)).timestamp() * 1000)
    pipeline = [
        {"$match": {"instrument": instrument, "ts": {"$gte": start_ms}}},
        {"$sort": {"ts": 1}},
        {"$group": {
            "_id": {"$dateToString": {
                "format": "%Y-%m-%d",
                "date": {"$add": [{"$toDate": "$ts"}, 5 * 3600 * 1000 + 30 * 60 * 1000]},
            }},
            "close": {"$last": "$close"},
            "ts": {"$last": "$ts"},
        }},
        {"$sort": {"ts": 1}},
    ]
    rows = await db.candles_1m.aggregate(pipeline).to_list(length=None)
    return [float(r["close"]) for r in rows if r.get("close") is not None]


async def _vix_series(db: Any) -> tuple:
    """(latest_vix, history_oldest_first) from stored INDIAVIX candles."""
    from app.vix import VIX_INSTRUMENT

    rows = await db.candles_1m.find(
        {"instrument": VIX_INSTRUMENT}, {"_id": 0, "ts": 1, "close": 1}
    ).sort("ts", -1).limit(400).to_list(length=400)
    rows = list(reversed(rows or []))
    hist = [float(r["close"]) for r in rows if r.get("close") is not None]
    return (hist[-1] if hist else None), hist


async def _option_chain(db: Any, instrument: str) -> tuple:
    """Build [{strike, ce_ltp, pe_ltp, ce_oi, pe_oi}] + (spot, expiry, oi_available).

    Joins the live option universe (contract scaffold) with the Upstox tick map
    (prices + open interest). Open interest is only present when the stream runs
    in a full mode; when it is absent we say so via a warning rather than
    reporting a PCR computed from zeros.
    """
    from app.live_option_universe import build_live_option_universe
    from app.runtime import upstox_stream_manager

    tick_map = upstox_stream_manager.latest_tick_map() or {}
    universe = await build_live_option_universe(
        db, latest_ticks=tick_map, underlyings=[instrument], radius=3
    )
    unders = (universe or {}).get("underlyings") or []
    detail = next((u for u in unders if str(u.get("underlying")) == instrument), None)
    if detail is None:
        return [], None, None, False

    by_strike: Dict[Any, Dict[str, Any]] = {}
    oi_seen = False
    for c in detail.get("contracts") or []:
        strike = c.get("strike")
        if strike is None:
            continue
        row = by_strike.setdefault(strike, {"strike": strike})
        tick = tick_map.get(c.get("instrument_key")) or {}
        side = "ce" if str(c.get("side")).upper() == "CE" else "pe"
        row[f"{side}_ltp"] = tick.get("last_price")
        oi = tick.get("open_interest")
        if oi is not None:
            oi_seen = True
        row[f"{side}_oi"] = oi
    chain = [by_strike[k] for k in sorted(by_strike)]
    return chain, detail.get("spot_price"), detail.get("expiry_date"), oi_seen


async def build_market_analysis(db: Any, instrument: str) -> Dict[str, Any]:
    """Assemble the full analysis payload. Never raises; degrades with warnings."""
    instrument = str(instrument or "NIFTY").upper()
    warnings: List[str] = []
    spot: Optional[float] = None
    structure: Optional[Dict[str, Any]] = None
    levels: Optional[Dict[str, Any]] = None
    trend: Dict[str, Optional[str]] = {k: None for k in ("intraday", "daily", "weekly", "monthly")}

    # --- 1. intraday structure + S/R -----------------------------------------
    try:
        enriched, closes = await _intraday_closes_and_frame(db, instrument)
        if closes:
            spot = closes[-1]
            trend["intraday"] = classify_trend(closes)
        if enriched is not None and len(enriched) >= 3:
            last = enriched.iloc[-1]
            adx = last.get("adx") if hasattr(last, "get") else None
            adx = float(adx) if adx is not None and adx == adx else None
            structure = classify_structure(closes=closes, adx=adx)
            try:
                from app.context_signals import recent_sr_levels
                sr = recent_sr_levels(enriched, len(enriched) - 1)
            except Exception as exc:
                log.warning("market_analysis: S/R failed (%s)", exc)
                sr, warnings = {}, warnings + ["sr_unavailable"]
            pivot = None
            try:
                h, l, c = float(last["high"]), float(last["low"]), float(last["close"])
                pivot = round((h + l + c) / 3.0, 2)
            except Exception:
                pivot = None
            levels = levels_from_sr(sr or {}, spot=spot or 0.0, pivot=pivot)
        else:
            warnings.append("intraday_candles_thin")
    except Exception as exc:
        log.warning("market_analysis: intraday stage failed (%s)", exc)
        warnings.append("intraday_unavailable")

    # --- 2. multi-timeframe trend from daily closes --------------------------
    try:
        dailies = await _daily_closes(db, instrument)
        if len(dailies) >= 3:
            for tf, window in TF_DAILY_WINDOWS.items():
                series = dailies[-window:]
                trend[tf] = classify_trend(series) if len(series) >= 3 else None
        else:
            warnings.append("daily_history_thin")
    except Exception as exc:
        log.warning("market_analysis: daily stage failed (%s)", exc)
        warnings.append("daily_unavailable")

    # --- 3. option analytics --------------------------------------------------
    options: Dict[str, Any] = {
        "pcr_oi": None, "max_pain": None, "iv_rank_30d": None,
        "iv_rank_source": "unavailable", "atm_straddle": None,
        "implied_move_pct": None, "net_delta_rupee": None, "net_theta_rupee": None,
        "expiry": None, "chain": [],
    }
    chain_spot = None
    try:
        chain, chain_spot, expiry, oi_seen = await _option_chain(db, instrument)
        options["chain"] = chain
        options["expiry"] = expiry
        if chain_spot is not None:
            spot = float(chain_spot)
        if chain:
            if oi_seen:
                options["pcr_oi"] = put_call_ratio(chain)
                options["max_pain"] = max_pain(chain)
            else:
                warnings.append("option_oi_unavailable")
            if spot:
                strad = atm_straddle(chain, spot=spot)
                if strad:
                    options["atm_straddle"] = strad["straddle"]
                    options["implied_move_pct"] = strad["implied_move_pct"]
        else:
            warnings.append("option_chain_unavailable")
    except Exception as exc:
        log.warning("market_analysis: option stage failed (%s)", exc)
        warnings.append("option_chain_unavailable")

    # --- 4. IV rank (VIX percentile proxy — no stored ATM-IV history) --------
    try:
        vix, vix_hist = await _vix_series(db)
        rank = iv_rank(current_iv=None, iv_history=[], vix=vix, vix_history=vix_hist)
        options["iv_rank_30d"] = rank["rank"]
        options["iv_rank_source"] = rank["source"]
        if rank.get("warning") and rank["warning"] not in warnings:
            warnings.append(rank["warning"])
    except Exception as exc:
        log.warning("market_analysis: vix stage failed (%s)", exc)
        warnings.append("iv_unavailable")

    return {
        "instrument": instrument,
        "as_of": _now_ist_iso(),
        "spot": spot,
        "structure": structure,
        "trend": trend,
        "levels": levels,
        "options": options,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# ~8s single-flight in-process cache (mirrors market_header.py's _UPSTOX_CACHE)
# --------------------------------------------------------------------------- #
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_S = 8.0
_LOCKS: Dict[str, asyncio.Lock] = {}


def _fresh(entry: Optional[Dict[str, Any]]) -> bool:
    if not entry:
        return False
    return (datetime.now(timezone.utc).timestamp() - entry.get("_cached_at", 0)) < _CACHE_TTL_S


async def cached_market_analysis(db: Any, instrument: str) -> Dict[str, Any]:
    """Cached wrapper: bounds cost when several cockpit clients poll at ~10s."""
    key = str(instrument or "NIFTY").upper()
    entry = _CACHE.get(key)
    if _fresh(entry):
        return {k: v for k, v in entry.items() if k != "_cached_at"}
    lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        entry = _CACHE.get(key)
        if _fresh(entry):  # another caller filled it while we waited
            return {k: v for k, v in entry.items() if k != "_cached_at"}
        result = await build_market_analysis(db, key)
        _CACHE[key] = {**result, "_cached_at": datetime.now(timezone.utc).timestamp()}
        return result
