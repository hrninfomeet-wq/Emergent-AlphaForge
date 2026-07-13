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

Deferred to a follow-up session:
  - Live/deployment_evaluator dispatch on the same config schema.
  - Optimizer tuner dispatch on the same config schema.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from app.premium_momentum_backtest import run_premium_momentum_backtest
from app.premium_trigger_config import PremiumTriggerConfig


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
