"""Pair spot/index backtest trades with executable option candles."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from app.options_universe import select_contract_for_signal
from app.slippage import SlippageConfig, apply_slippage, estimate_slippage_per_side


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
    }


def _coverage() -> Dict[str, int]:
    return {
        "spot_trade_count": 0,
        "paired_trade_count": 0,
        "missing_contract": 0,
        "missing_entry_candle": 0,
        "missing_exit_candle": 0,
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
        "avg_option_pnl_pts": round(float(pnls_pts.mean()), 3),
        "best_option_pnl_pts": round(float(pnls_pts.max()), 3),
        "worst_option_pnl_pts": round(float(pnls_pts.min()), 3),
    }


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
) -> Dict[str, Any]:
    """Map each spot signal trade to a long CE/PE option premium trade.

    Slippage: applies per-side option-point slippage at entry (BUY) and exit (SELL)
    via app.slippage.estimate_slippage_per_side. Pass slippage_config={...} to override
    defaults; pass {"atm_pts": 0} etc. to disable a bucket.
    """
    contract_list = list(contracts or [])
    spot_trade_list = list(spot_trades or [])
    candles = option_candles.copy() if option_candles is not None else pd.DataFrame()
    slippage_cfg = SlippageConfig.from_dict(slippage_config)
    if not candles.empty:
        candles["ts"] = candles["ts"].astype(int)
        candles = candles.sort_values(["instrument_key", "ts"]).reset_index(drop=True)

    coverage = _coverage()
    coverage["spot_trade_count"] = len(spot_trade_list)
    paired_trades: List[Dict[str, Any]] = []
    entry_max_age_ms = max(0, int(entry_max_age_sec or 0)) * 1000
    exit_max_age_ms = max(0, int(exit_max_age_sec or 0)) * 1000
    lot_count = max(1, int(lots or 1))

    for idx, spot_trade in enumerate(spot_trade_list):
        direction = str(spot_trade.get("direction", "")).upper()
        resolved_expiry = fixed_expiry_date or (expiry_by_trade or {}).get(idx)
        eligible_contracts = [
            contract
            for contract in contract_list
            if not resolved_expiry or str(contract.get("expiry_date", "")) == str(resolved_expiry)
        ]
        selected = select_contract_for_signal(
            contracts=eligible_contracts,
            underlying=underlying,
            spot_price=float(spot_trade.get("entry_price", 0.0)),
            direction=direction,
            moneyness=moneyness,
        )
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
        if not selected:
            coverage["missing_contract"] += 1
            paired_trades.append({**base, "status": "MISSING_CONTRACT"})
            continue

        instrument_key = selected["instrument_key"]
        rows = candles[candles["instrument_key"] == instrument_key] if not candles.empty else pd.DataFrame()
        entry = _candle_at_or_before(rows, int(spot_trade.get("entry_ts", 0)), entry_max_age_ms)
        if not entry:
            coverage["missing_entry_candle"] += 1
            paired_trades.append({**base, **_contract_fields(selected), "status": "MISSING_ENTRY_CANDLE"})
            continue
        exit_ts = int(spot_trade.get("exit_ts") or spot_trade.get("entry_ts") or 0)
        exit_candle = _candle_at_or_before(rows, exit_ts, exit_max_age_ms)
        if not exit_candle:
            coverage["missing_exit_candle"] += 1
            paired_trades.append({**base, **_contract_fields(selected), "status": "MISSING_EXIT_CANDLE"})
            continue

        raw_entry_price = float(entry["close"])
        raw_exit_price = float(exit_candle["close"])
        # Apply slippage at fill time. Buying entry pays MORE than mid; selling exit
        # receives LESS than mid. Both legs use per-side option-point estimates.
        entry_slip = estimate_slippage_per_side(
            moneyness=moneyness,
            ts_ms=int(entry["ts"]),
            expiry_iso=str(selected.get("expiry_date") or "") or None,
            cfg=slippage_cfg,
        )
        exit_slip = estimate_slippage_per_side(
            moneyness=moneyness,
            ts_ms=int(exit_candle["ts"]),
            expiry_iso=str(selected.get("expiry_date") or "") or None,
            cfg=slippage_cfg,
        )
        entry_price = apply_slippage(fill_price=raw_entry_price, side="BUY", pts=entry_slip["pts"])
        exit_price = apply_slippage(fill_price=raw_exit_price, side="SELL", pts=exit_slip["pts"])
        pnl_pts = round(exit_price - entry_price, 3)
        lot_size = int(selected.get("lot_size") or 1)
        pnl_value = round(pnl_pts * lot_size * lot_count, 2)
        mfe_mae = _option_mfe_mae(rows, int(entry["ts"]), int(exit_candle["ts"]), entry_price)
        coverage["paired_trade_count"] += 1
        paired_trades.append({
            **base,
            **_contract_fields(selected),
            "status": "PAIRED",
            "atm_at_entry": selected.get("atm"),
            "option_entry_ts": int(entry["ts"]),
            "option_exit_ts": int(exit_candle["ts"]),
            "raw_entry_option_price": round(raw_entry_price, 3),
            "raw_exit_option_price": round(raw_exit_price, 3),
            "entry_option_price": round(entry_price, 3),
            "exit_option_price": round(exit_price, 3),
            "entry_slippage_pts": entry_slip["pts"],
            "exit_slippage_pts": exit_slip["pts"],
            "slippage_bucket": entry_slip["bucket"],
            "expiry_tail_applied": bool(entry_slip.get("tail_multiplier_applied") or exit_slip.get("tail_multiplier_applied")),
            "lot_size": lot_size,
            "lots": lot_count,
            "quantity": lot_size * lot_count,
            "option_pnl_pts": pnl_pts,
            "option_pnl_value": pnl_value,
            **mfe_mae,
        })

    return {
        "enabled": True,
        "underlying": underlying.upper(),
        "moneyness": moneyness,
        "slippage_config": slippage_cfg.to_dict(),
        "coverage": coverage,
        "metrics": _compute_metrics(paired_trades),
        "equity_curve": build_option_equity_curve(paired_trades),
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
