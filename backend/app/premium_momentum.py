# backend/app/premium_momentum.py
"""Pure, host-testable helpers for the premium-momentum contingency strategy.

No DB / tick I/O — callers pass already-loaded contracts and option candles.
These are the SHARED rule functions: the backtest sim and (later) the live
deployment loop both call them, so entry/exit/strike semantics cannot drift.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.instruments import canonical_instrument_key
from app.option_costs import CostConfig, round_trip_charges, spread_pts_for_premium
from app.options_universe import select_contract_for_signal


def lock_reference_strike(*, contracts: List[Dict[str, Any]], underlying: str,
                          spot_at_ref: float, side: str,
                          moneyness: str = "itm1") -> Optional[Dict[str, Any]]:
    """Resolve and LOCK the option contract at the reference bar's spot.

    Wraps the shared selector so backtest and live pick the identical strike.
    Returns {"instrument_key","strike","side","moneyness"} or None if the strike
    is absent from `contracts` (coverage gap for that moneyness)."""
    sel = select_contract_for_signal(
        contracts=contracts, underlying=underlying,
        spot_price=float(spot_at_ref), direction=str(side).upper(),
        moneyness=str(moneyness),
    )
    if not sel:
        return None
    return {
        "instrument_key": sel["instrument_key"],
        "strike": int(sel["strike"]),
        "side": str(sel["side"]).upper(),
        "moneyness": str(moneyness),
    }


def premium_series_for_key(option_candles: pd.DataFrame,
                           instrument_key: str) -> Tuple[np.ndarray, np.ndarray]:
    """(ts[], premium[]) for one instrument_key, ascending by ts. premium = close.
    The key is CANONICALIZED before matching (candles are stored under the plain
    2-part SEGMENT|TOKEN form, while expired-contract metadata carries dated 3-part
    keys — an exact raw match silently finds nothing for any past expiry; root
    cause #3 in option_backtest). Empty arrays when the key is absent."""
    if option_candles is None or option_candles.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    key = canonical_instrument_key(instrument_key)
    sub = option_candles[option_candles["instrument_key"] == key]
    if sub.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    sub = sub.sort_values("ts")
    return sub["ts"].to_numpy(dtype="int64"), sub["close"].to_numpy(dtype="float64")


def premium_ohlc_for_key(option_candles: pd.DataFrame,
                         instrument_key: str) -> Dict[str, np.ndarray]:
    """{ts, open, high, low, close} arrays for one instrument_key, ascending by ts.
    close = premium (the momentum/target basis); low/open drive GAP-HONEST stop
    fills. Missing o/h/l columns fall back to close (so close-only fixtures still
    behave). The key is CANONICALIZED before matching (dated metadata keys vs
    plain candle keys — see premium_series_for_key). Empty arrays when absent."""
    empty = np.array([], dtype="float64")
    blank = {"ts": np.array([], dtype="int64"), "open": empty, "high": empty, "low": empty, "close": empty}
    if option_candles is None or option_candles.empty:
        return blank
    sub = option_candles[option_candles["instrument_key"] == canonical_instrument_key(instrument_key)]
    if sub.empty:
        return blank
    sub = sub.sort_values("ts")
    close = sub["close"].to_numpy(dtype="float64")
    col = lambda name: sub[name].to_numpy(dtype="float64") if name in sub.columns else close
    return {"ts": sub["ts"].to_numpy(dtype="int64"), "open": col("open"),
            "high": col("high"), "low": col("low"), "close": close}


def momentum_triggered(*, premium_now: float, ref_premium: float,
                       pct: Optional[float] = None, pts: Optional[float] = None) -> bool:
    """True once premium_now has risen to/above the momentum trigger from ref.
    Exactly one of pct (% of ref) or pts (absolute premium points) is used."""
    if pct is not None and pts is not None:
        raise ValueError("momentum_triggered: pass exactly one of pct or pts, not both")
    if ref_premium is None or ref_premium <= 0:
        return False
    if pct is not None:
        return float(premium_now) >= float(ref_premium) * (1.0 + float(pct) / 100.0)
    if pts is not None:
        return float(premium_now) >= float(ref_premium) + float(pts)
    return False


def walk_premium_momentum(*, ts, premium, ref_premium: float,
                          entry_pct: Optional[float] = None,
                          entry_pts: Optional[float] = None,
                          target_pct: Optional[float] = None,
                          target_pts: Optional[float] = None,
                          stop_pct: Optional[float] = None,
                          stop_pts: Optional[float] = None,
                          trail=None, low=None, open_=None, high=None,
                          entry_cutoff_ts: Optional[int] = None) -> Dict[str, Any]:
    """Walk a single locked strike's premium series (ascending ts):
    1. find the FIRST bar whose premium (close) crosses the momentum trigger -> ENTRY;
    2. from the next bar, exit on premium stop / target, else at EOD.

    ``entry_cutoff_ts`` (Phase 5A, EXP2 "no new entries after 14:40"): once the
    entry SEARCH reaches a bar with ``ts[i] >= entry_cutoff_ts`` the search ends
    with no entry (that bar and all later bars are never even checked for a
    cross). Default None = today's behavior, byte-identical (the parity test
    pins this). Exits are NOT cutoff-bound: an already-open position keeps
    managing its stop/target/trail past the cutoff — only the ENTRY search is
    gated.

    INTRA-BAR HONEST EXITS when the per-bar ``low``/``open_``/``high`` arrays are
    given (the real backtest path passes all three):
      - STOP: touched when bar LOW <= stop, FILLED at ``min(stop, bar_open)`` — a
        premium that gaps down THROUGH the stop books the real (worse) fill. This is
        the option buyer's dominant tail risk and must not be flattered.
      - TARGET: touched when bar HIGH >= target, FILLED at ``max(target, bar_open)``
        — symmetric with the stop, so intra-bar winners are not silently dropped
        (a close-only target undercounts wins and skews optimization pessimistic).
      - Same-bar stop+target resolves STOP-FIRST (pessimistic), mirroring the spot
        engine's intrabar_exit.
    Without the OHLC arrays (simple fixtures / legacy path) both fall back to
    close-touch, fill-at-level.

    LOOK-AHEAD SAFETY: the trail's high-water mark is updated at the END of each
    bar, so bar j's stop is ratcheted only by highs through bar j-1 — bar j's own
    close can never raise the stop that governs bar j's low (adverse-first
    pessimism inside the bar). An entry on the LAST bar is rejected (no bar left
    to manage the exit — a zero-bar phantom trade must not be booked).
    ``trail`` is the Phase-2 stepped ratchet. Returns entered=False if the
    momentum trigger never fired."""
    ts = list(ts); premium = [float(p) for p in premium]
    lo = [float(v) for v in low] if low is not None else None
    op = [float(v) for v in open_] if open_ is not None else None
    hi = [float(v) for v in high] if high is not None else None
    n = len(premium)
    # --- entry: first cross (cutoff ends the search, no entry beyond it) ---
    entry_i = None
    for i in range(n):
        if entry_cutoff_ts is not None and ts[i] >= entry_cutoff_ts:
            break
        if momentum_triggered(premium_now=premium[i], ref_premium=ref_premium,
                              pct=entry_pct, pts=entry_pts):
            entry_i = i
            break
    if entry_i is None or entry_i == n - 1:
        # No trigger, or triggered on the LAST bar (nothing left to manage — a
        # zero-bar EOD "trade" would be phantom noise in the stats).
        return {"entered": False}
    entry_premium = premium[entry_i]
    base_stop = _stop_or_target_level(entry_premium, stop_pct, stop_pts, is_stop=True)
    target = _stop_or_target_level(entry_premium, target_pct, target_pts, is_stop=False)
    running_high = entry_premium
    # --- exit: from the bar AFTER entry (fill at entry bar's premium) ---
    for j in range(entry_i + 1, n):
        c = premium[j]
        stop = base_stop
        if trail is not None and base_stop is not None:
            # running_high is through bar j-1 (updated at loop END) — bar j's own
            # close must never ratchet the stop applied to bar j's low.
            stop = trail(entry_premium=entry_premium, running_high=running_high,
                         base_stop=base_stop)
        # stop-first (pessimistic), mirroring the spot engine's intrabar_exit.
        if stop is not None:
            # touch: intra-bar LOW when available (gap-honest), else the close.
            touched = (lo[j] <= stop) if lo is not None else (c <= stop)
            if touched:
                # fill: gap-honest min(stop, open) — a bar that OPENED below the stop
                # gapped through it and fills worse; else fills at the stop.
                fill = min(stop, op[j]) if op is not None else stop
                return _exit(ts, entry_i, entry_premium, j, fill, "STOP")
        if target is not None:
            # touch: intra-bar HIGH when available (symmetric honesty), else close.
            hit = (hi[j] >= target) if hi is not None else (c >= target)
            if hit:
                # fill: max(target, open) — a bar that OPENED above the target gapped
                # up through it and fills better (the honest sell at the open).
                fill = max(target, op[j]) if op is not None else target
                return _exit(ts, entry_i, entry_premium, j, fill, "TARGET")
        running_high = max(running_high, c)
    # EOD
    return _exit(ts, entry_i, entry_premium, n - 1, premium[n - 1], "EOD")


def _stop_or_target_level(entry: float, pct: Optional[float], pts: Optional[float], *, is_stop: bool):
    # Fail loud on ambiguous config, symmetric with momentum_triggered — never
    # silently prefer one unit over the other.
    if pct is not None and pts is not None:
        raise ValueError("stop/target: pass exactly one of pct or pts, not both")
    if pct is not None:
        return entry * (1.0 - pct / 100.0) if is_stop else entry * (1.0 + pct / 100.0)
    if pts is not None:
        return entry - pts if is_stop else entry + pts
    return None


def _exit(ts, entry_i, entry_premium, exit_i, exit_premium, reason) -> Dict[str, Any]:
    return {
        "entered": True,
        "entry_ts": ts[entry_i], "entry_premium": round(float(entry_premium), 4),
        "exit_ts": ts[exit_i], "exit_premium": round(float(exit_premium), 4),
        "exit_reason": reason,
        "premium_pnl": round(float(exit_premium) - float(entry_premium), 4),
        "bars_held": int(exit_i - entry_i),
    }


def apply_costs_to_trade(trade: Dict[str, Any], *, cost_cfg: CostConfig,
                         lot_size: int, lots: int) -> Dict[str, Any]:
    """Overlay the engine's rupee cost model on one walked trade (POST-step: the
    verified mark-based walk is untouched; costs adjust FILLS and add net figures).

    Mirrors live_friction.fill_premium's spread convention exactly — BUY pays
    +half-spread, SELL receives -half-spread (spread_pts_for_premium / 2 per side)
    — and app.option_costs.round_trip_charges for the statutory + brokerage ₹,
    charged on the FILL turnovers. Disabled config ⇒ fills = marks, zero charges
    (net == gross), so the added fields are always present and comparable."""
    entry = float(trade["entry_premium"])
    exit_ = float(trade["exit_premium"])
    qty = max(1, int(lot_size)) * max(1, int(lots))
    entry_fill = entry + spread_pts_for_premium(entry, cost_cfg) / 2.0
    exit_fill = max(0.0, exit_ - spread_pts_for_premium(exit_, cost_cfg) / 2.0)
    charges = round_trip_charges(entry_premium=entry_fill, exit_premium=exit_fill,
                                 quantity=qty, cfg=cost_cfg)["total_charges"] if cost_cfg.enabled else 0.0
    gross_rupees = round((exit_fill - entry_fill) * qty, 2)
    net_rupees = round(gross_rupees - charges, 2)
    return {
        **trade,
        "entry_fill": round(entry_fill, 4),
        "exit_fill": round(exit_fill, 4),
        "charges_rupees": round(float(charges), 2),
        "gross_pnl_rupees": gross_rupees,
        "net_pnl_rupees": net_rupees,
        "net_pnl_pts": round(net_rupees / qty, 4),
    }


def stepped_trail_stop(*, entry_premium: float, running_high: float,
                       base_stop: float, x: float, y: float) -> float:
    """AlgoTest discrete ratchet: for every X favorable move (premium above entry),
    raise the stop by Y. stop = base_stop + floor(favorable / X) * Y. NOT a
    continuous high-water-minus-offset trail. Never below base_stop, and never
    ABOVE the traded high-water mark — an aggressive Y > X config must not place
    the stop at a price the premium never printed (which would force a same-bar
    exit at a phantom level)."""
    if x is None or x <= 0 or y is None or y <= 0:
        return base_stop
    favorable = float(running_high) - float(entry_premium)
    if favorable < x:
        return base_stop
    steps = int(favorable // float(x))
    return min(float(base_stop) + steps * float(y), float(running_high))


def stepped_trail_stop_pct(*, entry_premium: float, running_high: float,
                           base_stop: float, x_pct: float, y_pct: float) -> float:
    """%-of-entry variant of ``stepped_trail_stop`` (Phase 5A / EXP2 rule 4:
    "raise the SL by 5% of entry price" for every +5% favorable move — the step
    size is a PERCENT OF ENTRY PRICE, not raw points, so it must be computed
    fresh per trade from that trade's own entry_premium). Delegates to
    ``stepped_trail_stop`` with x/y = entry_premium * pct/100; the delegate's
    running-high cap still applies.

    Pinned arithmetic: entry 100, x_pct=y_pct=5 (x=y=5.0 pts), high=112 ->
    favorable 12 -> floor(12/5)=2 steps -> stop = base_stop + 10.0 pts (capped
    at running_high)."""
    x = float(entry_premium) * float(x_pct) / 100.0
    y = float(entry_premium) * float(y_pct) / 100.0
    return stepped_trail_stop(entry_premium=entry_premium, running_high=running_high,
                              base_stop=base_stop, x=x, y=y)
