"""Phase 4 engine dispatch — Backtest path.

Thin, pure delegator that:
  1) accepts a PremiumTriggerConfig
  2) translates it to the params dict shape the shipped sim expects
  3) delegates to run_premium_momentum_backtest (unchanged, byte-identical output)
  4) wraps the result to add config-driven traceability

This is a LIFT of the existing bespoke path, not a rewrite. The parity invariant
(see tests/test_premium_trigger_dispatch_parity.py) is:

    run_premium_momentum_backtest(**inputs)
      ==  dispatch_backtest(cfg, ..., inputs) [byte-identical `trades`]

Session 3 adds `dispatch_full_backtest` — the Optimizer/Backtest Lab wiring that was
deferred at the end of session 2 (see docs/EMERGENT_SESSION_NOTES.md and
docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md
§3.2). Running the shipped `premium_momentum` plugin through the general Optimizer or
Backtest Lab called `strategy.evaluate()` (a deliberate stub — the real logic lives only
in deployment_evaluator.py's dedicated branch), producing zero spot signals and the
literal "Option re-rank produced no paired results" message. `dispatch_full_backtest`
fixes this: for `strategy_id == "premium_momentum"` it runs the option-native sim
directly and reshapes its trades into option_backtest.py's canonical PAIRED-trade
contract, then reuses option_backtest.py's own pure aggregators (`_compute_metrics`,
`build_option_equity_curve`, `build_context_breakdown`) and portfolio.py's
`build_rupee_equity_curve` — so the result is shape-indistinguishable from what
`simulate_paired_option_trades` produces for any other strategy, and every existing
consumer (optimizer.py, runtime.py, the frontend) can read it unchanged.

Two honesty gaps this reshape CANNOT close (the premium-native engine never computes
them): no per-trade MFE/MAE walk (option_mfe_pts/option_mae_pts are always None — the
BacktestLab MaeMfeCard will render blank, not wrong), and no regime/time-of-day/DTE/VIX
context annotation (context_breakdown degrades to a single UNKNOWN bucket). Both are
None/absent rather than fabricated.

Still deferred to a follow-up session:
  - Live/deployment_evaluator dispatch on the same config schema (out of scope here —
    too safety-critical to touch without its own dedicated session).
  - The Stage-1 Optuna/grid search's per-trial spot scorer (optimizer.py::_evaluate)
    has no natural premium-native equivalent (there's no cheap "spot proxy" phase for a
    premium-triggered strategy) — candidates still reach Stage-2 re-rank via whatever
    Stage-1 selection already runs, which is degenerate (flat/zero) for premium_momentum
    candidates specifically. Stage-2 (_option_rerank/_survival_eval_oos, fixed here)
    correctly scores whichever candidates arrive; Stage-1 doesn't yet intelligently
    pre-filter them for this strategy family. Frontend config-block builder (deployment
    creation UI) is also still not built.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.instruments import UNDERLYING_META
from app.option_backtest import _compute_metrics, build_context_breakdown, build_option_equity_curve
from app.portfolio import build_rupee_equity_curve
from app.premium_momentum_backtest import run_premium_momentum_backtest
from app.premium_trigger_config import PremiumTriggerConfig

#: premium_momentum_backtest's exit_reason vocabulary -> option_backtest.py's
#: _compute_metrics bucket vocabulary. An unmapped raw string would silently
#: zero out every exit-reason counter downstream.
_EXIT_REASON_MAP = {
    "STOP": "OPTION_STOP",
    "TARGET": "OPTION_TARGET",
    "EOD": "OPTION_SIGNAL_EXIT",
}

#: Fields PremiumTriggerConfig actually accepts (extra="forbid") — merged_params
#: from strategy.merged_params() may carry registry bookkeeping fields (id, name,
#: description, ...) outside this set; filter to these before constructing so a
#: legitimate deployment param dict never raises a spurious ValidationError.
_CONFIG_FIELDS = (
    "reference_time", "moneyness", "side", "momentum_pct", "momentum_pts",
    "stop_pct", "stop_pts", "target_pct", "target_pts", "trail_x", "trail_y",
    "lots", "late_lock_cutoff", "cost_config",
)


def _adapt_premium_trades_to_paired(
    trades: List[Dict[str, Any]], *, instrument: str, lots: int, lot_size: int,
) -> List[Dict[str, Any]]:
    """Reshape run_premium_momentum_backtest's trade dicts into option_backtest.py's
    canonical PAIRED-trade contract, so the existing pure aggregators
    (_compute_metrics / build_option_equity_curve / build_context_breakdown /
    build_rupee_equity_curve) can consume them unchanged. See this module's
    docstring for the two fields this reshape honestly cannot fabricate."""
    quantity = int(lot_size) * int(lots)
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(trades):
        entry_premium = float(t.get("entry_premium", 0.0))
        exit_premium = float(t.get("exit_premium", 0.0))
        entry_fill = float(t.get("entry_fill", entry_premium))
        exit_fill = float(t.get("exit_fill", exit_premium))
        side = str(t.get("side", "")).upper()
        # entry_ts/exit_ts/strike may arrive as numpy scalars (pandas-derived) —
        # BSON encoding (Mongo persistence via backtest_runs.insert_one) rejects
        # numpy.int64/float64 outright, unlike JSON serialization which tolerates
        # them. Cast to native Python types here, once, at the adapter boundary.
        entry_ts = t.get("entry_ts")
        exit_ts = t.get("exit_ts")
        entry_ts = int(entry_ts) if entry_ts is not None else None
        exit_ts = int(exit_ts) if exit_ts is not None else None
        strike = t.get("strike")
        strike = float(strike) if strike is not None else None
        out.append({
            "index_trade_id": i,
            "direction": side,
            "side": side,
            "signal_entry_ts": entry_ts,
            "signal_exit_ts": exit_ts,
            "signal_entry_datetime": "",
            "signal_exit_datetime": "",
            "index_entry_price": None,
            "index_exit_price": None,
            "moneyness": t.get("moneyness", ""),
            "resolved_expiry_date": t.get("expiry_date"),
            "context": {},
            "instrument_key": t.get("instrument_key"),
            "trading_symbol": "",
            "underlying": str(instrument),
            "expiry_date": t.get("expiry_date", ""),
            "strike": strike,
            "status": "PAIRED",
            "atm_at_entry": None,
            "option_entry_ts": entry_ts,
            "option_exit_ts": exit_ts,
            "raw_entry_option_price": round(entry_premium, 3),
            "raw_exit_option_price": round(exit_premium, 3),
            "entry_option_price": round(entry_fill, 3),
            "exit_option_price": round(exit_fill, 3),
            "option_exit_reason": _EXIT_REASON_MAP.get(str(t.get("exit_reason", "")), "OPTION_SIGNAL_EXIT"),
            "option_target_level": None,
            "option_stop_level": None,
            "entry_slippage_pts": round(entry_fill - entry_premium, 4),
            "exit_slippage_pts": round(exit_premium - exit_fill, 4),
            "slippage_bucket": None,
            "expiry_tail_applied": False,
            "lot_size": int(lot_size),
            "lots": int(lots),
            "quantity": quantity,
            "sizing_mode": "fixed_lots",
            "risk_per_unit": None,
            "risk_amount": None,
            "risk_exceeded": False,
            "entry_spread_pts": round(entry_fill - entry_premium, 4),
            "exit_spread_pts": round(exit_premium - exit_fill, 4),
            "gross_option_pnl_value": round(float(t.get("gross_pnl_rupees", 0.0)), 2),
            "charges": None,
            "total_charges": round(float(t.get("charges_rupees", 0.0)), 2),
            "option_pnl_pts": round(float(t.get("net_pnl_pts", 0.0)), 3),
            "option_pnl_value": round(float(t.get("net_pnl_rupees", 0.0)), 2),
            # Honestly absent — the premium-native walk never computes an MFE/MAE
            # window (no equivalent of option_backtest.py's _option_mfe_mae).
            "option_mfe_pts": None,
            "option_mae_pts": None,
        })
    return out


def dispatch_full_backtest(
    *,
    strategy_id: str,
    merged_params: Dict[str, Any],
    spot_df: pd.DataFrame,
    option_candles: pd.DataFrame,
    contracts: List[Dict[str, Any]],
    instrument: str,
    capital: float = 200_000.0,
) -> Optional[Dict[str, Any]]:
    """Full paired-option-backtest envelope for a premium-trigger strategy, or
    None for any other strategy (the entire regression-safety mechanism — every
    other strategy's existing run_backtest + simulate_paired_option_trades path
    is completely untouched by this function's existence).

    Returns None (never raises) when:
      - strategy_id != "premium_momentum"
      - merged_params doesn't validate as a PremiumTriggerConfig (e.g. no entry
        trigger set) — the caller's normal error-handling path takes over.
    """
    if strategy_id != "premium_momentum":
        return None

    cfg_fields = {k: merged_params.get(k) for k in _CONFIG_FIELDS if merged_params.get(k) is not None}
    try:
        cfg = PremiumTriggerConfig(**cfg_fields)
    except Exception:
        return None

    pm_result = run_premium_momentum_backtest(
        spot_df=spot_df, option_candles=option_candles, contracts=contracts,
        instrument=instrument, params=cfg.to_backtest_params(),
    )
    lot_size = int(UNDERLYING_META.get(str(instrument).upper(), {}).get("lot_size", 1))
    paired_trades = _adapt_premium_trades_to_paired(
        pm_result.get("trades", []), instrument=instrument, lots=int(cfg.lots), lot_size=lot_size,
    )
    metrics = _compute_metrics(paired_trades)
    portfolio = build_rupee_equity_curve(paired_trades, capital=capital)
    equity_curve = build_option_equity_curve(paired_trades)
    context_breakdown = build_context_breakdown(paired_trades)
    paired_count = sum(1 for t in paired_trades if t["status"] == "PAIRED")
    coverage = {
        "spot_trade_count": len(paired_trades),
        "paired_trade_count": paired_count,
        "missing_contract": 0,
        "missing_entry_candle": 0,
        "missing_exit_candle": 0,
        "skipped_by_cap": 0,
    }
    return {
        "enabled": True,
        "underlying": str(instrument),
        "moneyness": cfg.moneyness,
        "exit_mode": "premium_trigger_config",
        "option_exit_config": {
            "target_pts": cfg.target_pts, "stop_pts": cfg.stop_pts,
            "target_pct": cfg.target_pct, "stop_pct": cfg.stop_pct,
        },
        "slippage_config": None,
        "cost_config": cfg.cost_config,
        "sizing_config": None,
        "coverage": coverage,
        "metrics": metrics,
        "equity_curve": equity_curve,
        "portfolio": portfolio,
        "context_breakdown": context_breakdown,
        "trades": paired_trades,
        "premium_trigger_config": cfg.model_dump(mode="json"),
        "dispatch": "premium_trigger_config",
    }


def dispatch_backtest(
    *,
    cfg: PremiumTriggerConfig,
    spot_df: pd.DataFrame,
    option_candles: pd.DataFrame,
    contracts: List[Dict[str, Any]],
    instrument: str,
) -> Dict[str, Any]:
    """Run a premium-trigger backtest from a declarative config.

    Byte-identical to calling `run_premium_momentum_backtest` directly with the
    equivalent `params` dict — the whole point of Phase 4 dispatch is to route
    on CONFIG PRESENCE, not on `strategy_id == "premium_momentum"`, without
    changing the sim's behavior in the process (see spec's regression-safety
    invariant, §3.5 of docs/superpowers/specs/2026-07-13-...phase4-5*.md).
    """
    params = cfg.to_backtest_params()
    result = run_premium_momentum_backtest(
        spot_df=spot_df,
        option_candles=option_candles,
        contracts=contracts,
        instrument=instrument,
        params=params,
    )
    # Traceability: record the config that produced this result. Callers that
    # persist a backtest run into Mongo get a schema-versioned record of
    # exactly what config was used, so an Optimizer sweep can round-trip.
    result = dict(result)
    result["premium_trigger_config"] = cfg.model_dump(mode="json")
    result["dispatch"] = "premium_trigger_config"
    return result


__all__ = ["dispatch_backtest"]
