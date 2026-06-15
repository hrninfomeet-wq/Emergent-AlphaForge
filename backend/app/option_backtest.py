"""Pair spot/index backtest trades with executable option candles."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from app.options_universe import select_contract_for_signal
from app.slippage import SlippageConfig
from app.exit_engine import intrabar_exit
from app.option_costs import CostConfig, round_trip_charges
from app.live_friction import fill_premium
from app.portfolio import SizingConfig, size_position, build_rupee_equity_curve
from app.market_context import build_trade_context
from app.dte import compute_dte
from app.exit_controls import (ExitControlsConfig, effective_premium_stop,
                               stop_fill_price, EXIT_TRAIL_STOP, EXIT_BREAKEVEN_STOP,
                               DailyCapsConfig, daily_governor_decision, SKIPPED_STATUS)


def _empty_metrics() -> Dict[str, Any]:
    return {
        "paired_trade_count": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_option_pnl_pts": 0.0,
        "total_option_pnl_value": 0.0,
        "avg_option_pnl_pts": 0.0,
        "best_option_pnl_pts": 0.0,
        "worst_option_pnl_pts": 0.0,
        "option_trail_exits": 0,
        "option_breakeven_exits": 0,
        "skipped_by_cap": 0,
        "skipped_daily_loss": 0,
        "skipped_daily_target": 0,
        "skipped_max_trades": 0,
    }


def _ist_session_date(ts_ms: Any) -> Optional[str]:
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    try:
        return (_dt.fromtimestamp(int(ts_ms) / 1000, tz=_tz.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
    except Exception:
        return None


def _coverage() -> Dict[str, int]:
    return {
        "spot_trade_count": 0,
        "paired_trade_count": 0,
        "missing_contract": 0,
        "missing_entry_candle": 0,
        "missing_exit_candle": 0,
        "skipped_by_cap": 0,
    }


def _candle_at_or_before(rows: pd.DataFrame, target_ts: int, max_age_ms: int) -> Optional[Dict[str, Any]]:
    if rows.empty:
        return None
    eligible = rows[(rows["ts"] <= target_ts) & ((target_ts - rows["ts"]) <= max_age_ms)]
    if eligible.empty:
        return None
    return eligible.sort_values("ts").iloc[-1].to_dict()


def _option_mfe_mae(rows: pd.DataFrame, entry_ts: int, exit_ts: int, entry_price: float) -> Dict[str, float]:
    window = rows[(rows["ts"] >= entry_ts) & (rows["ts"] <= exit_ts)]
    if window.empty:
        return {"option_mfe_pts": 0.0, "option_mae_pts": 0.0}
    high = float(window["high"].max()) if "high" in window else entry_price
    low = float(window["low"].min()) if "low" in window else entry_price
    return {
        "option_mfe_pts": round(max(0.0, high - entry_price), 3),
        "option_mae_pts": round(max(0.0, entry_price - low), 3),
    }


def _resolve_option_levels(
    entry_price: float,
    *,
    target_pts: Optional[float],
    stop_pts: Optional[float],
    target_pct: Optional[float],
    stop_pct: Optional[float],
) -> Dict[str, Optional[float]]:
    """Resolve absolute premium target/stop levels from points or percent.

    Delegates to the shared execution policy (`app.execution_policy`) so the
    sim and the live marker can never disagree about level math. Points take
    precedence over percent; target ABOVE entry, stop BELOW, floored at 0.
    """
    from app.execution_policy import resolve_premium_levels
    stop_level, target_level = resolve_premium_levels(
        entry_price,
        target_pts=target_pts, stop_pts=stop_pts,
        target_pct=target_pct, stop_pct=stop_pct,
        stop_floor=0.0,
    )
    return {"target_level": target_level, "stop_level": stop_level}


def _walk_option_exit(
    rows: pd.DataFrame,
    *,
    entry_ts: int,
    backstop_ts: int,
    entry_price: float,
    target_level: Optional[float],
    stop_level: Optional[float],
    exit_cfg: Optional[ExitControlsConfig] = None,
) -> Dict[str, Any]:
    """Walk option candles forward to the first premium-level exit. With exit_cfg
    enabled, the stop is ratcheted per bar via effective_premium_stop using the
    running-max premium THROUGH the prior bar (look-ahead safe), and a long stop
    that gaps below fills at the bar open."""
    forward = rows[(rows["ts"] > entry_ts) & (rows["ts"] <= backstop_ts)].sort_values("ts")
    last_close = entry_price
    last_ts = entry_ts
    running_max = float(entry_price)
    use_overlay = exit_cfg is not None and exit_cfg.enabled
    for _, bar in forward.iterrows():
        bar_ts = int(bar["ts"])
        high = float(bar.get("high", bar.get("close", entry_price)))
        low = float(bar.get("low", bar.get("close", entry_price)))
        bar_open = bar.get("open")
        last_close = float(bar.get("close", last_close))
        last_ts = bar_ts
        eff_stop = (effective_premium_stop(entry=entry_price, running_max=running_max,
                                           base_stop=stop_level, cfg=exit_cfg)
                    if use_overlay else stop_level)
        level, reason = intrabar_exit(
            high=high, low=low, stop=eff_stop, target=target_level, is_long=True,
        )
        if level is not None:
            if reason == "STOP":
                fill = stop_fill_price(level, reason, bar_open) if use_overlay else level
                exit_reason = "OPTION_STOP"
                if use_overlay and stop_level is not None and eff_stop is not None and eff_stop > float(stop_level):
                    exit_reason = (EXIT_BREAKEVEN_STOP
                                   if _breakeven_binding(entry_price, running_max, eff_stop, exit_cfg)
                                   else EXIT_TRAIL_STOP)
                elif use_overlay and stop_level is None and eff_stop is not None:
                    exit_reason = EXIT_TRAIL_STOP
                return {"exit_ts": bar_ts, "exit_price": fill, "exit_reason": exit_reason}
            return {"exit_ts": bar_ts, "exit_price": level, "exit_reason": "OPTION_TARGET"}
        running_max = max(running_max, high)
    return {"exit_ts": last_ts, "exit_price": last_close, "exit_reason": "OPTION_SIGNAL_EXIT"}


def _breakeven_binding(entry, running_max, eff_stop, cfg) -> bool:
    """True when the breakeven candidate (not trailing) produced eff_stop — for
    exit-reason attribution. Recomputes the breakeven level only."""
    e = float(entry)
    if not (cfg.be_trigger and cfg.be_trigger > 0):
        return False
    if cfg.unit == "pts":
        be_level = e + (cfg.be_lock or 0.0)
        trig = e + cfg.be_trigger
    else:
        be_level = e * (1.0 + (cfg.be_lock or 0.0))
        trig = e * (1.0 + cfg.be_trigger)
    return float(running_max) >= trig and abs(be_level - float(eff_stop)) < 1e-9


def _compute_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    paired = [t for t in trades if t.get("status") == "PAIRED"]
    if not paired:
        return _empty_metrics()

    pnls_pts = np.array([float(t.get("option_pnl_pts", 0.0)) for t in paired])
    pnls_value = np.array([float(t.get("option_pnl_value", 0.0)) for t in paired])
    wins = pnls_pts[pnls_pts > 0]
    losses = pnls_pts[pnls_pts <= 0]
    return {
        "paired_trade_count": int(len(paired)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(paired) * 100, 2),
        "total_option_pnl_pts": round(float(pnls_pts.sum()), 3),
        "total_option_pnl_value": round(float(pnls_value.sum()), 2),
        "total_charges": round(float(sum(float(t.get("total_charges") or 0.0) for t in paired)), 2),
        "total_gross_option_pnl_value": round(float(sum(float(t.get("gross_option_pnl_value", t.get("option_pnl_value", 0.0))) for t in paired)), 2),
        "avg_option_pnl_pts": round(float(pnls_pts.mean()), 3),
        "best_option_pnl_pts": round(float(pnls_pts.max()), 3),
        "worst_option_pnl_pts": round(float(pnls_pts.min()), 3),
        "option_target_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_TARGET")),
        "option_stop_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_STOP")),
        "option_signal_exits": int(sum(1 for t in paired if t.get("option_exit_reason") in ("OPTION_SIGNAL_EXIT", "SPOT_EXIT"))),
        "option_trail_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_TRAIL_STOP")),
        "option_breakeven_exits": int(sum(1 for t in paired if t.get("option_exit_reason") == "OPTION_BREAKEVEN_STOP")),
        "skipped_by_cap": int(sum(1 for t in trades if t.get("status") == "SKIPPED_DAILY_CAP")),
        "skipped_daily_loss": int(sum(1 for t in trades if t.get("skip_reason") == "DAILY_LOSS_HALT")),
        "skipped_daily_target": int(sum(1 for t in trades if t.get("skip_reason") == "DAILY_TARGET_HALT")),
        "skipped_max_trades": int(sum(1 for t in trades if t.get("skip_reason") == "MAX_TRADES_HALT")),
    }


def build_context_breakdown(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate paired-trade P&L by context dimension (regime / time-of-day /
    DTE / VIX bucket) so the user can see WHERE a strategy has edge.

    Each bucket reports trade_count, win_rate, total rupee P&L, and avg P&L.
    Only PAIRED trades with a context block are counted.
    """
    dims = {"regime": {}, "time_of_day": {}, "dte": {}, "vix_bucket": {}}
    for t in trades:
        if t.get("status") != "PAIRED":
            continue
        ctx = t.get("context") or {}
        pnl = float(t.get("option_pnl_value", 0.0))
        win = pnl > 0
        for dim in dims:
            key = ctx.get(dim)
            if key is None:
                key = "UNKNOWN"
            key = str(key)
            b = dims[dim].setdefault(key, {"trade_count": 0, "wins": 0, "total_pnl_value": 0.0})
            b["trade_count"] += 1
            b["wins"] += 1 if win else 0
            b["total_pnl_value"] += pnl
    # Finalize derived stats.
    out: Dict[str, Any] = {}
    for dim, buckets in dims.items():
        out[dim] = {}
        for key, b in buckets.items():
            n = b["trade_count"]
            out[dim][key] = {
                "trade_count": n,
                "win_rate": round(b["wins"] / n * 100, 2) if n else 0.0,
                "total_pnl_value": round(b["total_pnl_value"], 2),
                "avg_pnl_value": round(b["total_pnl_value"] / n, 2) if n else 0.0,
            }
    return out


def build_option_equity_curve(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    equity = 0.0
    peak = 0.0
    curve: List[Dict[str, Any]] = []
    for trade in trades:
        if trade.get("status") != "PAIRED":
            continue
        equity += float(trade.get("option_pnl_value", 0.0))
        peak = max(peak, equity)
        curve.append({
            "ts": trade.get("option_exit_ts"),
            "datetime": trade.get("signal_exit_datetime", ""),
            "equity_value": round(equity, 2),
            "drawdown_value": round(equity - peak, 2),
            "pnl_value": round(float(trade.get("option_pnl_value", 0.0)), 2),
        })
    return curve


def simulate_paired_option_trades(
    *,
    spot_trades: Iterable[Dict[str, Any]],
    contracts: Iterable[Dict[str, Any]],
    option_candles: pd.DataFrame,
    underlying: str,
    moneyness: str = "otm1",
    lots: int = 1,
    entry_max_age_sec: int = 120,
    exit_max_age_sec: int = 180,
    expiry_by_trade: Optional[Dict[int, str]] = None,
    fixed_expiry_date: Optional[str] = None,
    slippage_config: Optional[Dict[str, Any]] = None,
    exit_mode: str = "spot_exit",
    option_target_pts: Optional[float] = None,
    option_stop_pts: Optional[float] = None,
    option_target_pct: Optional[float] = None,
    option_stop_pct: Optional[float] = None,
    cost_config: Optional[Dict[str, Any]] = None,
    sizing_config: Optional[Dict[str, Any]] = None,
    exit_controls: Optional[Dict[str, Any]] = None,
    daily_caps: Optional[Dict[str, Any]] = None,  # wired in the next task (daily governor)
) -> Dict[str, Any]:
    """Map each spot signal trade to a long CE/PE option premium trade.

    Slippage: applies per-side option-point slippage at entry (BUY) and exit (SELL)
    via app.slippage.estimate_slippage_per_side. Pass slippage_config={...} to override
    defaults; pass {"atm_pts": 0} etc. to disable a bucket.

    Exit modes:
      - "spot_exit" (default): the option is sold when the spot trade exits
        (target/stop/time on the INDEX). The option just mirrors the spot trade.
      - "option_levels": the option is exited on its OWN premium target/stop
        (points or percent), scanning option candles forward from entry. If
        neither level triggers, the spot exit acts as a backstop. This models a
        pure option buyer who manages the position on premium, not on the index.
    """
    exit_mode = str(exit_mode or "spot_exit").lower()
    use_option_levels = exit_mode == "option_levels" and (
        (option_target_pts and option_target_pts > 0)
        or (option_stop_pts and option_stop_pts > 0)
        or (option_target_pct and option_target_pct > 0)
        or (option_stop_pct and option_stop_pct > 0)
    )
    contract_list = list(contracts or [])
    spot_trade_list = list(spot_trades or [])
    candles = option_candles.copy() if option_candles is not None else pd.DataFrame()
    slippage_cfg = SlippageConfig.from_dict(slippage_config)
    cost_cfg = CostConfig.from_dict(cost_config)
    sizing_cfg = SizingConfig.from_dict(sizing_config)
    exit_cfg = ExitControlsConfig.from_dict(exit_controls)
    caps_cfg = DailyCapsConfig.from_dict(daily_caps)
    session_ledger: Dict[str, Dict[str, float]] = {}
    if not candles.empty:
        candles["ts"] = candles["ts"].astype(int)
        candles = candles.sort_values(["instrument_key", "ts"]).reset_index(drop=True)
    # Pre-group candles by instrument_key ONCE. The pairing loop below looks up
    # the candles for a contract on every trade; scanning the full frame per
    # trade is O(trades x candles) and becomes the bottleneck when the optimizer
    # re-ranks many candidates. A dict of per-key frames makes each lookup O(1).
    # Keys are CANONICALIZED (2-part broker form): the same contract can be
    # selected via a plain-keyed (current-sync) or dated-keyed (expired-sync)
    # metadata doc, and candles must pair either way (root cause #3, 2026-06-12).
    from app.instruments import canonical_instrument_key
    candles_by_key: Dict[str, pd.DataFrame] = {}
    if not candles.empty:
        grouped: Dict[str, List[pd.DataFrame]] = {}
        for k, g in candles.groupby("instrument_key", sort=False):
            grouped.setdefault(canonical_instrument_key(str(k)), []).append(g)
        candles_by_key = {
            key: (frames[0] if len(frames) == 1 else pd.concat(frames).sort_values("ts"))
            for key, frames in grouped.items()
        }

    coverage = _coverage()
    coverage["spot_trade_count"] = len(spot_trade_list)
    paired_trades: List[Dict[str, Any]] = []
    entry_max_age_ms = max(0, int(entry_max_age_sec or 0)) * 1000
    exit_max_age_ms = max(0, int(exit_max_age_sec or 0)) * 1000
    lot_count = max(1, int(lots or 1))
    # Sorted expiry universe for DTE classification (metadata-driven).
    _all_expiries_sorted = sorted({
        str(c.get("expiry_date")) for c in contract_list if c.get("expiry_date")
    })

    # Whether the contract universe carries expiry metadata at all. When it does
    # not (e.g. single-expiry test fixtures), we keep the permissive behavior.
    _contracts_have_expiry = any(c.get("expiry_date") for c in contract_list)

    for idx, spot_trade in enumerate(spot_trade_list):
        direction = str(spot_trade.get("direction", "")).upper()
        resolved_expiry = fixed_expiry_date or (expiry_by_trade or {}).get(idx)
        # Build eligible contracts. If an expiry is resolved, filter to it. If no
        # expiry is resolved AND the universe has expiry metadata, do NOT fall
        # back to all contracts — that silently selects the OLDEST expiry and
        # pairs to long-dead contracts (the bug behind near-zero pairing). Only
        # when contracts carry no expiry metadata do we allow the full set.
        if resolved_expiry:
            eligible_contracts = [
                c for c in contract_list if str(c.get("expiry_date", "")) == str(resolved_expiry)
            ]
        elif _contracts_have_expiry:
            eligible_contracts = []
        else:
            eligible_contracts = list(contract_list)
        selected = select_contract_for_signal(
            contracts=eligible_contracts,
            underlying=underlying,
            spot_price=float(spot_trade.get("entry_price", 0.0)),
            direction=direction,
            moneyness=moneyness,
        ) if eligible_contracts else None
        base = {
            "index_trade_id": idx,
            "direction": direction,
            "signal_entry_ts": spot_trade.get("entry_ts"),
            "signal_exit_ts": spot_trade.get("exit_ts"),
            "signal_entry_datetime": spot_trade.get("entry_datetime", ""),
            "signal_exit_datetime": spot_trade.get("exit_datetime", ""),
            "index_entry_price": spot_trade.get("entry_price"),
            "index_exit_price": spot_trade.get("exit_price"),
            "moneyness": moneyness,
            "resolved_expiry_date": resolved_expiry,
        }
        # Market-context snapshot: regime + time-of-day (from the spot signal)
        # and DTE (from expiry metadata). VIX is joined later by the caller when
        # available. This lets us analyze where the strategy actually has edge.
        entry_date_iso = _ist_session_date(spot_trade.get("entry_ts"))
        trade_dte = compute_dte(entry_date_iso, _all_expiries_sorted) if entry_date_iso else None
        base["context"] = build_trade_context(
            regime=spot_trade.get("regime"),
            ist_time=spot_trade.get("ist_time"),
            ts_ms=spot_trade.get("entry_ts"),
            dte=trade_dte,
            vix=spot_trade.get("vix"),
        )
        sess = entry_date_iso if caps_cfg.active else None
        if sess is not None:
            led = session_ledger.setdefault(sess, {"cum": 0.0, "min": 0.0, "max": 0.0, "admitted": 0})
            decision = daily_governor_decision(
                realized_cum_min=led["min"], realized_cum_max=led["max"],
                entry_count=int(led["admitted"]), cfg=caps_cfg)   # ALREADY-admitted (pre-this-trade)
            if decision["halt"]:
                coverage["skipped_by_cap"] += 1
                # contract not resolved at this point -> no _contract_fields (governor gates the ENTRY)
                paired_trades.append({**base, "status": SKIPPED_STATUS, "skip_reason": decision["reason"]})
                continue

        if not selected:
            coverage["missing_contract"] += 1
            miss = ("no_expiry_resolved (no upcoming expiry on/after the trade date in contract metadata)"
                    if not resolved_expiry
                    else "no_contract_for_strike (no contract at the resolved ATM/moneyness strike for this expiry)")
            paired_trades.append({**base, "status": "MISSING_CONTRACT", "miss_reason": miss})
            continue

        instrument_key = selected["instrument_key"]
        rows = candles_by_key.get(canonical_instrument_key(str(instrument_key)), pd.DataFrame()) if candles_by_key else pd.DataFrame()
        entry = _candle_at_or_before(rows, int(spot_trade.get("entry_ts", 0)), entry_max_age_ms)
        if not entry:
            coverage["missing_entry_candle"] += 1
            # Distinguish "contract has zero candles in the loaded set" from
            # "candles exist but none near the entry minute" so the UI can guide
            # the user (fetch the strike vs. widen the entry age window).
            if rows.empty:
                miss_reason = "no_candles_for_strike (this strike/expiry was never fetched into options_1m)"
            else:
                miss_reason = "no_candle_near_entry (candles exist for the strike but none within the entry age window)"
            paired_trades.append({
                **base, **_contract_fields(selected),
                "status": "MISSING_ENTRY_CANDLE",
                "miss_reason": miss_reason,
            })
            continue
        exit_ts = int(spot_trade.get("exit_ts") or spot_trade.get("entry_ts") or 0)
        exit_candle = _candle_at_or_before(rows, exit_ts, exit_max_age_ms)
        if not exit_candle:
            coverage["missing_exit_candle"] += 1
            paired_trades.append({
                **base, **_contract_fields(selected),
                "status": "MISSING_EXIT_CANDLE",
                "miss_reason": "no_candle_near_exit (candles exist for the strike but none within the exit age window)",
            })
            continue

        raw_entry_price = float(entry["close"])
        # Apply entry slippage first so option target/stop levels are measured
        # against the actual (slipped) fill, matching how a real buyer manages.
        # fill_premium (app.live_friction) is the SHARED fill model: point
        # slippage + half the %-of-premium spread. The live paper path books
        # entry/exit fills through the exact same function, so sim and live can
        # never disagree about the fill price (see tests/test_live_friction.py).
        entry_fill = fill_premium(
            raw_premium=raw_entry_price, side="BUY",
            moneyness=moneyness, ts_ms=int(entry["ts"]),
            expiry_iso=str(selected.get("expiry_date") or "") or None,
            slippage_cfg=slippage_cfg, cost_cfg=cost_cfg,
        )
        entry_spread_pts = entry_fill["spread_pts"]
        entry_price = entry_fill["price"]

        # Determine the exit. In spot_exit mode the option is sold at the spot
        # trade's exit candle. In option_levels mode we scan option candles
        # forward from entry for the first premium target/stop hit, with the
        # spot exit as a backstop.
        option_exit_reason = "SPOT_EXIT"
        option_levels = {"target_level": None, "stop_level": None}
        if use_option_levels:
            option_levels = _resolve_option_levels(
                entry_price,
                target_pts=option_target_pts,
                stop_pts=option_stop_pts,
                target_pct=option_target_pct,
                stop_pct=option_stop_pct,
            )
            walk = _walk_option_exit(
                rows,
                entry_ts=int(entry["ts"]),
                backstop_ts=int(exit_candle["ts"]),
                entry_price=entry_price,
                target_level=option_levels["target_level"],
                stop_level=option_levels["stop_level"],
                exit_cfg=exit_cfg,
            )
            raw_exit_price = float(walk["exit_price"])
            exit_candle_ts = int(walk["exit_ts"])
            option_exit_reason = walk["exit_reason"]
        else:
            raw_exit_price = float(exit_candle["close"])
            exit_candle_ts = int(exit_candle["ts"])

        # Apply exit slippage. Selling exit receives LESS than mid. Same shared
        # fill model as the entry (and the live close path).
        exit_fill = fill_premium(
            raw_premium=raw_exit_price, side="SELL",
            moneyness=moneyness, ts_ms=exit_candle_ts,
            expiry_iso=str(selected.get("expiry_date") or "") or None,
            slippage_cfg=slippage_cfg, cost_cfg=cost_cfg,
        )
        exit_spread_pts = exit_fill["spread_pts"]
        exit_price = exit_fill["price"]
        pnl_pts = round(exit_price - entry_price, 3)
        lot_size = int(selected.get("lot_size") or 1)
        # Position sizing. lot SIZE comes from the contract; the lot COUNT is
        # either the user's fixed lots or sized from premium-at-risk. The option
        # stop level (when present) drives the per-unit risk estimate.
        sizing = size_position(
            entry_premium=entry_price,
            lot_size=lot_size,
            stop_level=option_levels.get("stop_level"),
            cfg=sizing_cfg,
        )
        sized_lots = int(sizing["lots"]) if sizing_cfg.enabled else lot_count
        quantity = lot_size * sized_lots
        gross_pnl_value = round(pnl_pts * quantity, 2)
        # Statutory + brokerage charges (rupees) for the round trip. Zero when
        # the cost model is disabled, preserving legacy gross behavior.
        charges = round_trip_charges(
            entry_premium=entry_price,
            exit_premium=exit_price,
            quantity=quantity,
            cfg=cost_cfg,
        ) if cost_cfg.enabled else None
        total_charges = float(charges["total_charges"]) if charges else 0.0
        pnl_value = round(gross_pnl_value - total_charges, 2)
        if sess is not None:
            led = session_ledger[sess]
            led["admitted"] += 1
            led["cum"] += float(pnl_value)
            led["min"] = min(led["min"], led["cum"])
            led["max"] = max(led["max"], led["cum"])
        mfe_mae = _option_mfe_mae(rows, int(entry["ts"]), exit_candle_ts, entry_price)
        coverage["paired_trade_count"] += 1
        paired_trades.append({
            **base,
            **_contract_fields(selected),
            "status": "PAIRED",
            "atm_at_entry": selected.get("atm"),
            "option_entry_ts": int(entry["ts"]),
            "option_exit_ts": exit_candle_ts,
            "raw_entry_option_price": round(raw_entry_price, 3),
            "raw_exit_option_price": round(raw_exit_price, 3),
            "entry_option_price": round(entry_price, 3),
            "exit_option_price": round(exit_price, 3),
            "option_exit_reason": option_exit_reason,
            "option_target_level": round(option_levels["target_level"], 3) if option_levels["target_level"] is not None else None,
            "option_stop_level": round(option_levels["stop_level"], 3) if option_levels["stop_level"] is not None else None,
            "entry_slippage_pts": entry_fill["slippage_pts"],
            "exit_slippage_pts": exit_fill["slippage_pts"],
            "slippage_bucket": entry_fill["bucket"],
            "expiry_tail_applied": bool(entry_fill["tail"] or exit_fill["tail"]),
            "lot_size": lot_size,
            "lots": sized_lots,
            "quantity": quantity,
            "sizing_mode": sizing.get("sizing_mode"),
            "risk_per_unit": sizing.get("risk_per_unit"),
            "risk_amount": sizing.get("risk_amount"),
            "risk_exceeded": sizing.get("risk_exceeded"),
            "entry_spread_pts": round(entry_spread_pts, 4),
            "exit_spread_pts": round(exit_spread_pts, 4),
            "gross_option_pnl_value": gross_pnl_value,
            "charges": charges,
            "total_charges": round(total_charges, 2),
            "option_pnl_pts": pnl_pts,
            "option_pnl_value": pnl_value,
            **mfe_mae,
        })

    return {
        "enabled": True,
        "underlying": underlying.upper(),
        "moneyness": moneyness,
        "exit_mode": exit_mode,
        "option_exit_config": {
            "target_pts": option_target_pts,
            "stop_pts": option_stop_pts,
            "target_pct": option_target_pct,
            "stop_pct": option_stop_pct,
            "applied": bool(use_option_levels),
        },
        "slippage_config": slippage_cfg.to_dict(),
        "cost_config": cost_cfg.to_dict(),
        "sizing_config": sizing_cfg.to_dict(),
        "coverage": coverage,
        "metrics": _compute_metrics(paired_trades),
        "equity_curve": build_option_equity_curve(paired_trades),
        "portfolio": build_rupee_equity_curve(paired_trades, capital=sizing_cfg.capital),
        "context_breakdown": build_context_breakdown(paired_trades),
        "trades": paired_trades,
    }


def _contract_fields(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "instrument_key": contract.get("instrument_key"),
        "trading_symbol": contract.get("trading_symbol", ""),
        "underlying": contract.get("underlying", ""),
        "expiry_date": contract.get("expiry_date", ""),
        "strike": contract.get("strike"),
        "side": contract.get("side"),
    }
