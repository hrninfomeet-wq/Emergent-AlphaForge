# backend/app/premium_momentum_backtest.py
"""Option-native backtest for the premium-momentum contingency strategy.

Per session: at the reference time, lock the chosen-moneyness CE and PE strikes
from spot, then walk each locked strike's premium series for a momentum entry +
premium exit (shared pure helpers). Coverage-gated: sessions whose locked
strike lacks a premium series are excluded and counted, never mis-filled.

Phase 1: single position, first side to trigger wins (``leg_mode`` defaults to
"first_to_trigger" -> byte-identical to that original behavior).

Phase 5A (EXP2 full contingency, backtest-only -- see
docs/superpowers/plans/2026-07-14-premium-momentum-phase5a-backtest-contingency.md):
  - ``leg_mode="both"``: CE and PE primaries are fully independent -- either,
    both, or neither may enter in a session.
  - ``lazy_enabled``: when a PRIMARY leg exits with reason STOP (never
    TARGET/EOD), arm the OPPOSITE side as a one-shot "reversal" leg: lock a
    FRESH strike from spot at the stop-out bar, walk its premium series (with
    its own momentum/stop/target/trail params) starting strictly AFTER the
    stop-out bar. One reversal per primary side per session.
  - ``entry_cutoff`` / ``exit_time``: session-level entry gate and hard exit
    bound (both "HH:MM" IST), applied identically to primaries and lazies.

Phase 5A.2 (session day-stop + VIX gate, backtest-only -- see
docs/superpowers/plans/2026-07-14-premium-momentum-phase5a2-overlays-edge-hunt.md):
  - ``session_max_loss_rupees`` / ``session_max_profit_rupees``: a REALIZED,
    bar-close-honest, per-SESSION day-stop. It is a ONE-PASS post-process over
    that session's already-walked trades (primaries + lazies): sort completed
    trades by (exit_ts, entry_ts, side), scan cumulative cost-adjusted
    ``net_pnl_rupees``; the first trade whose cumulative breaches a cap
    defines ``breach_ts``. Trades that share that same exit_ts stay realized
    (they exited on the same bar -- not "blocked"). Any trade with
    entry_ts > breach_ts is DROPPED (blocked entry, counted
    ``blocked_day_stop``). Any trade OPEN at the breach
    (entry_ts <= breach_ts < exit_ts) is FORCE-CLOSED at the first bar of ITS
    OWN premium series with ts >= breach_ts, at that bar's CLOSE, reason
    "DAY_STOP" (costs recomputed for the truncated fill; counted
    ``forced_day_stop_exits``). This is exact for the breach decision because
    it is defined on REALIZED exits only: a forced exit always realizes AT
    breach_ts or later, so it can never move the breach earlier. This is
    explicitly NOT a mark-to-market day-stop -- an open position's unrealized
    loss cannot itself trigger the stop. A mark-to-market day-stop (catching
    intraday MTM bleed on still-open legs) is a different, richer rule,
    deferred.
  - ``vix_min`` / ``vix_max`` with the new ``vix_by_session`` kwarg: a session
    -level India VIX gate. The sim stays PURE -- it receives
    ``vix_by_session: Optional[Dict[str, float]]`` (session_date -> gate
    value), never fetches VIX itself. Gated at session start: a value outside
    [vix_min, vix_max] skips the session (``sessions_excluded_vix_gate``); a
    gate configured but the session missing from the map ALSO skips it
    (``sessions_excluded_vix_missing`` -- trading an unverifiable gate would
    be dishonest, never a silent pass).

All new params default OFF -> byte-identical to the pre-5A / pre-5A.2 engine
(pinned by the parity tests in tests/test_premium_momentum_contingency.py and
tests/test_premium_momentum_overlays.py)."""
from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.instruments import UNDERLYING_META
from app.option_costs import CostConfig
from app.premium_momentum import (
    apply_costs_to_trade, lock_reference_strike, premium_ohlc_for_key,
    stepped_trail_stop, stepped_trail_stop_pct, walk_premium_momentum,
)

# The lazy reversal leg's reference bar is matched to the primary's stop-out
# bar EXACTLY (ts equality) when available, else the nearest EARLIER bar
# within this tolerance (asof). ts is epoch-ms throughout this codebase (see
# option_backtest.exit_max_age_sec's *1000 conversion) -- 180s, matching that
# same house convention, converted to ms here.
LAZY_REF_ASOF_TOLERANCE_MS = 180_000

#: Full warehouse moneyness band — the honest maximum a lazy-leg preload can
#: cover (the band the warehouse actually ingests; a fresh strike outside it on
#: a big intraday move becomes lazy_excluded_no_data, counted, never mis-filled).
FULL_MONEYNESS_BAND = ["itm2", "itm1", "atm", "otm1", "otm2"]


def preload_scope(moneynesses: List[str], sides: List[str],
                  lazy_enabled: bool) -> Tuple[List[str], List[str]]:
    """(moneynesses, sides) the warehouse preload must cover.

    When the lazy reversal leg is enabled, the fresh OPPOSITE-side strike is
    locked mid-session from a moved spot — unpredictable at preload time — so
    the preload must widen to the full ingested moneyness band AND BOTH sides.
    Widening moneyness alone is not enough: a CE-only run with lazy enabled
    would load zero PE candles and silently measure every reversal activation
    as lazy_excluded_no_data (adversarial-review finding C1)."""
    if not lazy_enabled:
        return list(moneynesses), list(sides)
    return sorted(set(moneynesses) | set(FULL_MONEYNESS_BAND)), ["CE", "PE"]


def _sides_for(param: str) -> List[str]:
    p = str(param or "first_to_trigger").lower()
    if p == "ce":
        return ["CE"]
    if p == "pe":
        return ["PE"]
    return ["CE", "PE"]


def expiry_for_session(session_date: str, expiries_sorted: List[str]) -> str | None:
    """Nearest expiry ON/AFTER the session date — the blueprint's 'current weekly'
    (includes the expiry day itself, i.e. 0-DTE on Tuesdays for NIFTY). None when
    no expiry covers the session (surfaced as a coverage exclusion, never a silent
    fallback to a dead contract)."""
    for e in expiries_sorted:
        if e >= session_date:
            return e
    return None


def _resolve_trail(x, y, x_pct, y_pct, *, label: str):
    """XOR-resolve a stepped-trail config: an absolute (x,y) POINTS pair, OR an
    (x_pct,y_pct) PERCENT-of-entry pair (Phase 5A). Both pairs fully given at
    once is ambiguous config -> fail loud, never silently prefer one unit
    (mirrors momentum_triggered / _stop_or_target_level's own convention). A
    PARTIAL pair (only one of x/y, or only one of x_pct/y_pct) silently
    produces no trail -- that is the pre-5A behavior for trail_x/trail_y and
    must stay byte-identical."""
    pts_set = x is not None and y is not None
    pct_set = x_pct is not None and y_pct is not None
    if pts_set and pct_set:
        raise ValueError(f"{label}: pass a points pair (x/y) XOR a percent pair (x_pct/y_pct), not both")
    if pts_set:
        return functools.partial(stepped_trail_stop, x=x, y=y)
    if pct_set:
        return functools.partial(stepped_trail_stop_pct, x_pct=x_pct, y_pct=y_pct)
    return None


def _find_asof_index(ts_arr: np.ndarray, target_ts: int, tolerance_ms: int) -> Optional[int]:
    """Index of the candle at EXACTLY ``target_ts``, else the nearest candle
    STRICTLY BEFORE it within ``tolerance_ms`` (asof-backward). ``ts_arr`` must
    be ascending (premium_ohlc_for_key's contract). None when neither exists —
    the caller must count this as a coverage exclusion, never mis-fill."""
    if ts_arr is None or len(ts_arr) == 0:
        return None
    exact = np.where(ts_arr == target_ts)[0]
    if len(exact) > 0:
        return int(exact[0])
    le = np.where(ts_arr <= target_ts)[0]
    if len(le) == 0:
        return None
    idx = int(le[-1])   # ascending array -> last index <= target is the nearest
    if target_ts - int(ts_arr[idx]) <= tolerance_ms:
        return idx
    return None


def _force_close_at_day_stop(trade: Dict[str, Any], oh: Dict[str, np.ndarray], breach_ts: int, *,
                             cost_cfg: CostConfig, lot_size: int, lots: int) -> Dict[str, Any]:
    """Force-close ONE open-at-breach trade at the first bar of ITS OWN
    premium series with ts >= breach_ts, at that bar's CLOSE (day-stop step
    5). The original entry stands; only the exit changes, and costs are
    recomputed off the truncated fill."""
    ts_arr = oh["ts"]
    close_arr = oh["close"]
    idxs = np.where((ts_arr >= breach_ts) & (ts_arr <= trade["exit_ts"]))[0]
    if len(idxs) == 0:
        # Should not happen: the trade's own original exit_ts is itself a bar
        # in its own series with ts >= breach_ts (entry_ts <= breach_ts <
        # exit_ts implies exit_ts qualifies). Defensive no-op fallback.
        return trade
    exit_i = int(idxs[0])
    exit_ts = int(ts_arr[exit_i])
    exit_premium = float(close_arr[exit_i])
    entry_matches = np.where(ts_arr == trade["entry_ts"])[0]
    bars_held = (exit_i - int(entry_matches[0])) if len(entry_matches) else trade.get("bars_held")
    new_trade = {
        **trade,
        "exit_ts": exit_ts, "exit_premium": round(exit_premium, 4),
        "exit_reason": "DAY_STOP",
        "premium_pnl": round(exit_premium - float(trade["entry_premium"]), 4),
        "bars_held": bars_held,
    }
    return apply_costs_to_trade(new_trade, cost_cfg=cost_cfg, lot_size=lot_size, lots=lots)


def apply_session_day_stop(trades: List[Dict[str, Any]], option_candles: pd.DataFrame, *,
                           max_loss_rupees: Optional[float], max_profit_rupees: Optional[float],
                           cost_cfg: CostConfig, lot_size: int, lots: int,
                           ) -> Tuple[List[Dict[str, Any]], int, int]:
    """Session day-stop post-pass (Phase 5A.2) — see the module docstring's
    'session_max_loss_rupees / session_max_profit_rupees' section for the
    full semantics. Pure and one-pass: does not touch the walks that produced
    ``trades``, only prunes/truncates the finished list. No-op (byte-identical,
    returns ``trades`` unchanged) when both caps are None."""
    if max_loss_rupees is None and max_profit_rupees is None:
        return trades, 0, 0

    by_session: Dict[str, List[int]] = {}
    for i, t in enumerate(trades):
        by_session.setdefault(str(t["session_date"]), []).append(i)

    drop_idx: set = set()
    forced: Dict[int, Dict[str, Any]] = {}
    for _session, idxs in by_session.items():
        session_trades = [(i, trades[i]) for i in idxs]
        ordered = sorted(session_trades, key=lambda it: (it[1]["exit_ts"], it[1]["entry_ts"], it[1]["side"]))
        cumulative = 0.0
        breach_ts = None
        for _i, t in ordered:
            cumulative += float(t["net_pnl_rupees"])
            if breach_ts is not None:
                continue
            if max_loss_rupees is not None and cumulative <= -abs(float(max_loss_rupees)):
                breach_ts = t["exit_ts"]
            elif max_profit_rupees is not None and cumulative >= abs(float(max_profit_rupees)):
                breach_ts = t["exit_ts"]
        if breach_ts is None:
            continue

        for i, t in session_trades:
            if t["exit_ts"] <= breach_ts:
                continue   # realized at/before breach -- stays as-is (same-bar ties included)
            if t["entry_ts"] > breach_ts:
                drop_idx.add(i)
                continue
            # OPEN at breach: entry_ts <= breach_ts < exit_ts -- force-close.
            oh = premium_ohlc_for_key(option_candles, t["instrument_key"])
            forced[i] = _force_close_at_day_stop(t, oh, breach_ts,
                                                 cost_cfg=cost_cfg, lot_size=lot_size, lots=lots)

    kept = [forced.get(i, t) for i, t in enumerate(trades) if i not in drop_idx]
    return kept, len(drop_idx), len(forced)


def _leg_summary(leg_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "trades": len(leg_trades),
        "gross_pnl_pts": round(sum(t["premium_pnl"] for t in leg_trades), 2),
        "net_pnl_rupees": round(sum(t["net_pnl_rupees"] for t in leg_trades), 2),
    }


def run_premium_momentum_backtest(*, spot_df: pd.DataFrame, option_candles: pd.DataFrame,
                                  contracts: List[Dict[str, Any]], instrument: str,
                                  params: Dict[str, Any],
                                  vix_by_session: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
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
    trail_x_pct = params.get("trail_x_pct")
    trail_y_pct = params.get("trail_y_pct")

    # --- Phase 5A params (all default OFF -> byte-identical) -----------------
    leg_mode = str(params.get("leg_mode") or "first_to_trigger").lower()
    lazy_enabled = bool(params.get("lazy_enabled") or False)
    lazy_entry_pct = params.get("lazy_momentum_pct")
    lazy_entry_pts = params.get("lazy_momentum_pts")
    lazy_stop_pct = params.get("lazy_stop_pct")
    lazy_stop_pts = params.get("lazy_stop_pts")
    lazy_target_pct = params.get("lazy_target_pct")
    lazy_target_pts = params.get("lazy_target_pts")
    lazy_trail_x = params.get("lazy_trail_x")
    lazy_trail_y = params.get("lazy_trail_y")
    lazy_trail_x_pct = params.get("lazy_trail_x_pct")
    lazy_trail_y_pct = params.get("lazy_trail_y_pct")
    lazy_moneyness = str(params.get("lazy_moneyness") or moneyness)
    entry_cutoff = params.get("entry_cutoff")
    exit_time = params.get("exit_time")

    # --- Phase 5A.2 params (all default OFF -> byte-identical) ---------------
    session_max_loss_rupees = params.get("session_max_loss_rupees")
    session_max_profit_rupees = params.get("session_max_profit_rupees")
    vix_min = params.get("vix_min")
    vix_max = params.get("vix_max")
    vix_gate_configured = vix_min is not None or vix_max is not None

    # --- Fail-loud validation (BEFORE any session is processed — ambiguous or
    # incomplete config must never silently do something reasonable-looking). ---
    trail = _resolve_trail(trail_x, trail_y, trail_x_pct, trail_y_pct, label="trail")
    lazy_trail = _resolve_trail(lazy_trail_x, lazy_trail_y, lazy_trail_x_pct, lazy_trail_y_pct,
                                label="lazy_trail")
    if lazy_enabled:
        if lazy_entry_pct is None and lazy_entry_pts is None:
            raise ValueError("lazy_enabled requires lazy_momentum_pct or lazy_momentum_pts")
        if lazy_entry_pct is not None and lazy_entry_pts is not None:
            raise ValueError("lazy_momentum_pct and lazy_momentum_pts are mutually exclusive")
    if session_max_loss_rupees is not None and float(session_max_loss_rupees) < 0:
        raise ValueError("session_max_loss_rupees must be non-negative (pass the loss magnitude)")
    if session_max_profit_rupees is not None and float(session_max_profit_rupees) < 0:
        raise ValueError("session_max_profit_rupees must be non-negative")
    if vix_min is not None and vix_max is not None and float(vix_min) > float(vix_max):
        raise ValueError("vix_min must be <= vix_max")

    # Cost model (Phase 1.2): the engine's option cost schedule, applied as a
    # post-step on each trade's fills. Disabled ⇒ net == gross (fields always
    # present so results are shape-stable and comparable).
    cost_cfg = CostConfig.from_dict(params.get("cost_config"))
    lots = max(1, int(params.get("lots") or 1))
    lot_size = int(UNDERLYING_META.get(str(instrument).upper(), {}).get("lot_size", 1))

    trades: List[Dict[str, Any]] = []
    cov = {"sessions_total": 0, "sessions_traded": 0, "sessions_excluded": 0,
           "sessions_no_signal": 0, "exclude_reasons": {},
           "lazy_armed": 0, "lazy_entered": 0, "lazy_blocked_cutoff": 0,
           "lazy_excluded_no_data": 0,
           "blocked_day_stop": 0, "forced_day_stop_exits": 0,
           "sessions_excluded_vix_gate": 0, "sessions_excluded_vix_missing": 0}

    # Per-session expiry resolution (the blueprint's "current weekly"): when the
    # contract universe carries expiry metadata, each session trades the nearest
    # expiry on/after ITS OWN date — a multi-week window must never pair every
    # session against the window's FIRST week (dead contracts, collapsed sample).
    expiries_sorted = sorted({str(c.get("expiry_date")) for c in contracts if c.get("expiry_date")})

    for session, sdf in spot_df.groupby("session_date"):
        cov["sessions_total"] += 1
        sdf = sdf.sort_values("ts")

        # India VIX gate (Phase 5A.2) — gated at session start, BEFORE the
        # reference bar / strike lock, so an excluded session costs nothing
        # further. Configured-but-unverifiable (session missing from the
        # map) is a DEDICATED counter, never a silent pass.
        if vix_gate_configured:
            vix_val = (vix_by_session or {}).get(str(session))
            if vix_val is None:
                cov["sessions_excluded_vix_missing"] += 1
                continue
            vix_val = float(vix_val)
            if (vix_min is not None and vix_val < float(vix_min)) or \
               (vix_max is not None and vix_val > float(vix_max)):
                cov["sessions_excluded_vix_gate"] += 1
                continue

        ref_rows = sdf[sdf["ist_time"] >= ref_time]
        if ref_rows.empty:
            cov["sessions_excluded"] += 1
            cov["exclude_reasons"]["no_reference_bar"] = cov["exclude_reasons"].get("no_reference_bar", 0) + 1
            continue
        ref_row = ref_rows.iloc[0]
        spot_at_ref = float(ref_row["close"])
        ref_ts = int(ref_row["ts"])
        session_end_ts = int(sdf["ts"].max())

        # Session-level entry cutoff / hard exit bound (Phase 5A). Resolved off
        # this session's own spot clock (same convention as ref_row above):
        # first bar whose ist_time >= the "HH:MM" cutoff/exit. If the session
        # never reaches that clock time, the bound has no effect this session.
        cutoff_ts: Optional[int] = None
        if entry_cutoff:
            cutoff_rows = sdf[sdf["ist_time"] >= str(entry_cutoff)]
            if not cutoff_rows.empty:
                cutoff_ts = int(cutoff_rows.iloc[0]["ts"])
        exit_ts_bound = session_end_ts
        if exit_time:
            exit_rows = sdf[sdf["ist_time"] >= str(exit_time)]
            if not exit_rows.empty:
                exit_ts_bound = min(session_end_ts, int(exit_rows.iloc[0]["ts"]))

        # This session's contract set: nearest weekly expiry >= session date.
        # No expiry metadata at all (simple fixtures) => permissive full set,
        # mirroring option_backtest's convention.
        sess_expiry = None
        sess_contracts = contracts
        if expiries_sorted:
            sess_expiry = expiry_for_session(str(session), expiries_sorted)
            if sess_expiry is None:
                cov["sessions_excluded"] += 1
                cov["exclude_reasons"]["no_expiry"] = cov["exclude_reasons"].get("no_expiry", 0) + 1
                continue
            sess_contracts = [c for c in contracts if str(c.get("expiry_date")) == sess_expiry]

        # Lock each side's strike + get its OHLC premium series bounded to THIS
        # SESSION (reference bar -> exit bound). Without the exit-bound a locked
        # key's later candles would leak in and the walk would "EOD"-exit on the
        # wrong bar (intraday strategy = same-day, same-bound square-off).
        candidates = []          # (side, locked, ohlc dict of arrays)
        excluded = False
        for side in sides:
            locked = lock_reference_strike(contracts=sess_contracts, underlying=instrument,
                                           spot_at_ref=spot_at_ref, side=side, moneyness=moneyness)
            if not locked:
                excluded = True
                cov["exclude_reasons"]["no_contract"] = cov["exclude_reasons"].get("no_contract", 0) + 1
                break
            oh = premium_ohlc_for_key(option_candles, locked["instrument_key"])
            mask = (oh["ts"] >= ref_ts) & (oh["ts"] <= exit_ts_bound)
            oh = {k: v[mask] for k, v in oh.items()}
            if len(oh["close"]) == 0:
                excluded = True
                cov["exclude_reasons"]["no_premium_series"] = cov["exclude_reasons"].get("no_premium_series", 0) + 1
                break
            candidates.append((side, locked, oh))
        if excluded:
            cov["sessions_excluded"] += 1
            continue

        # Walk every candidate. leg_mode="both": keep EVERY side that entered
        # (0, 1, or 2 primary trades). leg_mode="first_to_trigger" (default):
        # keep only the EARLIEST entry — byte-identical to the pre-5A "best"
        # tie-break (first-seen wins ties, same as the old strict `<` loop).
        entered_candidates = []   # (side, locked, ref_premium, r)
        for side, locked, oh in candidates:
            ref_premium = float(oh["close"][0])   # premium at/after the reference bar
            r = walk_premium_momentum(ts=oh["ts"], premium=oh["close"], ref_premium=ref_premium,
                                      entry_pct=entry_pct, entry_pts=entry_pts,
                                      target_pct=target_pct, target_pts=target_pts,
                                      stop_pct=stop_pct, stop_pts=stop_pts, trail=trail,
                                      low=oh["low"], open_=oh["open"], high=oh["high"],
                                      entry_cutoff_ts=cutoff_ts)
            if not r.get("entered"):
                continue
            entered_candidates.append((side, locked, ref_premium, r))

        if leg_mode == "both":
            chosen = entered_candidates
        else:
            chosen = []
            if entered_candidates:
                chosen = [min(entered_candidates, key=lambda c: c[3]["entry_ts"])]

        if not chosen:
            cov["sessions_no_signal"] += 1
            continue

        for side, locked, ref_premium, r in chosen:
            trade = {
                "session_date": str(session), "side": side, "strike": locked["strike"],
                "instrument_key": locked["instrument_key"], "moneyness": moneyness,
                "expiry_date": sess_expiry, "ref_premium": round(ref_premium, 4),
                "leg": "primary",
                **r,
            }
            trades.append(apply_costs_to_trade(trade, cost_cfg=cost_cfg,
                                               lot_size=lot_size, lots=lots))
        cov["sessions_traded"] += 1

        # --- Phase 5A: lazy (reversal) legs ----------------------------------
        # One arming attempt per PRIMARY trade that stopped out (never on
        # TARGET/EOD). Lazy trades are never themselves re-armed (this loop
        # only ever iterates the primaries chosen above) -> structurally
        # one-shot per side per session.
        if lazy_enabled:
            for side, locked, ref_premium, r in chosen:
                if r.get("exit_reason") != "STOP":
                    continue
                stop_out_ts = int(r["exit_ts"])
                if cutoff_ts is not None and stop_out_ts >= cutoff_ts:
                    cov["lazy_blocked_cutoff"] += 1
                    continue
                cov["lazy_armed"] += 1
                opposite = "PE" if side == "CE" else "CE"

                spot_match = sdf[sdf["ts"] == stop_out_ts]
                if spot_match.empty:
                    cov["lazy_excluded_no_data"] += 1
                    continue
                fresh_spot = float(spot_match.iloc[0]["close"])

                fresh_locked = lock_reference_strike(
                    contracts=sess_contracts, underlying=instrument,
                    spot_at_ref=fresh_spot, side=opposite, moneyness=lazy_moneyness)
                if not fresh_locked:
                    cov["lazy_excluded_no_data"] += 1
                    continue

                full_oh = premium_ohlc_for_key(option_candles, fresh_locked["instrument_key"])
                ref_idx = _find_asof_index(full_oh["ts"], stop_out_ts, LAZY_REF_ASOF_TOLERANCE_MS)
                if ref_idx is None:
                    cov["lazy_excluded_no_data"] += 1
                    continue
                ref_bar_ts = int(full_oh["ts"][ref_idx])
                lazy_ref_premium = float(full_oh["close"][ref_idx])

                # Walk STRICTLY AFTER the ref bar (look-ahead safety — the ref
                # bar itself, whether an exact match or an asof-backward one,
                # must never be a candidate entry bar), bounded to this
                # session's exit bound.
                wmask = (full_oh["ts"] > ref_bar_ts) & (full_oh["ts"] <= exit_ts_bound)
                walk_oh = {k: v[wmask] for k, v in full_oh.items()}

                lr = walk_premium_momentum(
                    ts=walk_oh["ts"], premium=walk_oh["close"], ref_premium=lazy_ref_premium,
                    entry_pct=lazy_entry_pct, entry_pts=lazy_entry_pts,
                    target_pct=lazy_target_pct, target_pts=lazy_target_pts,
                    stop_pct=lazy_stop_pct, stop_pts=lazy_stop_pts, trail=lazy_trail,
                    low=walk_oh["low"], open_=walk_oh["open"], high=walk_oh["high"],
                    entry_cutoff_ts=cutoff_ts)
                if not lr.get("entered"):
                    continue
                cov["lazy_entered"] += 1
                lazy_trade = {
                    "session_date": str(session), "side": opposite, "strike": fresh_locked["strike"],
                    "instrument_key": fresh_locked["instrument_key"], "moneyness": lazy_moneyness,
                    "expiry_date": sess_expiry, "ref_premium": round(lazy_ref_premium, 4),
                    "leg": "lazy", "lazy_parent_side": side, "lazy_activated_ts": stop_out_ts,
                    **lr,
                }
                trades.append(apply_costs_to_trade(lazy_trade, cost_cfg=cost_cfg,
                                                   lot_size=lot_size, lots=lots))

    # --- Phase 5A.2: session day-stop post-pass ------------------------------
    trades, blocked_day_stop, forced_day_stop_exits = apply_session_day_stop(
        trades, option_candles,
        max_loss_rupees=session_max_loss_rupees, max_profit_rupees=session_max_profit_rupees,
        cost_cfg=cost_cfg, lot_size=lot_size, lots=lots,
    )
    cov["blocked_day_stop"] = blocked_day_stop
    cov["forced_day_stop_exits"] = forced_day_stop_exits

    primary_trades = [t for t in trades if t.get("leg") == "primary"]
    lazy_trades = [t for t in trades if t.get("leg") == "lazy"]
    summary = {
        "lot_size": lot_size, "lots": lots, "costs_enabled": bool(cost_cfg.enabled),
        "gross_pnl_pts": round(sum(t["premium_pnl"] for t in trades), 2),
        "net_pnl_pts": round(sum(t["net_pnl_pts"] for t in trades), 2),
        "net_pnl_rupees": round(sum(t["net_pnl_rupees"] for t in trades), 2),
        "charges_rupees": round(sum(t["charges_rupees"] for t in trades), 2),
        "by_leg": {"primary": _leg_summary(primary_trades), "lazy": _leg_summary(lazy_trades)},
    }
    return {"trades": trades, "coverage": cov, "summary": summary, "params": dict(params)}
