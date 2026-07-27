"""Pooled multi-index research runner (direction B of the profit-leverage analysis).

WHY THIS EXISTS
---------------
The premium-momentum edge hunt was run ad-hoc and its harness was never
committed, so its ~600-config campaign is not reproducible from the repo. This
runner is committed deliberately: a research verdict nobody can re-run is a
claim, not evidence.

WHAT IT DOES
------------
Drives EXACTLY the path the Backtest API uses — load candles -> merged_params ->
precompute_all_indicators + regime -> run_backtest -> _run_paired_option_backtest
— so the numbers it prints are the same numbers the UI would show. It does NOT
call `_audit_and_fill_backtest_data`: research must be read-only and must never
trigger a broker ingest as a side effect.

The question (see docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md §4/§6): regime routing
was judged "option-+EV yet SAMPLE-STARVED". Pooling NIFTY+BANKNIFTY+SENSEX gives
1,210 option index-days against 408 for NIFTY alone (2.97x) with zero engine
changes. This runner measures that, per-index and pooled, on pre-declared slices.

HONESTY RAILS BUILT IN
----------------------
  * Costs are ALWAYS on. There is no flag to turn them off.
  * The option-net rupee figure is the verdict metric — never the spot P&L.
    (The project has already proven those diverge wildly: a +289k spot "best"
    was -207k on real option fills.)
  * BANKNIFTY 2024-11-28..2024-12-19 is EXCLUDED by default: measured, real,
    index-specific option-data gap. Silently averaging over it would flatter or
    punish BANKNIFTY for a data artifact.
  * Per-index results are always reported alongside the pooled number, because a
    "pooled edge" that lives in one index is not an edge (analysis §6).

USAGE
    python backend/scripts/run_pooled_regime_campaign.py --slice train --dry-run
    python backend/scripts/run_pooled_regime_campaign.py --slice train
    python backend/scripts/run_pooled_regime_campaign.py --slice holdout --i-am-sure

The holdout is guarded: it may be touched ONCE, and only with --i-am-sure.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

IST = timezone(timedelta(hours=5, minutes=30))

# --------------------------------------------------------------------------- #
# PRE-REGISTERED DESIGN — do not edit after the holdout has been touched.
# --------------------------------------------------------------------------- #

STRATEGY_ID = "opening_range_regime_router"

#: Chronological split BY DATE (not by index), so no index leaks across time.
#: Mirrors the premium-momentum campaign's split so verdicts are comparable.
SLICES = {
    "train":      ("2024-11-25", "2025-08-31"),
    "validation": ("2025-09-01", "2025-12-31"),
    "holdout":    ("2026-01-01", "2026-07-24"),
}

#: MEASURED CORRECTION to the analysis's "pool all three" (2.97x): the router
#: declares supported_instruments = [NIFTY, SENSEX] and that exclusion is
#: principled, not an oversight. BANKNIFTY is MONTHLY-expiry (21 expiries in the
#: window vs NIFTY 87 / SENSEX 88), so its DTE range is 0-30 rather than 0-6 —
#: pooling it with weekly indices would blend two different option regimes and
#: call the mixture a bigger sample. Real multiplier for THIS strategy: 2.0x
#: (408 + 410 = 818 option index-days vs 408 NIFTY-only).
INSTRUMENTS = ["NIFTY", "SENSEX"]

#: Measured, index-specific option-data gap (one isolated day then ~16 missing
#: trading days). Excluded so a data artifact is never read as a regime result.
EXCLUDE_WINDOWS = {
    "BANKNIFTY": [("2024-11-28", "2024-12-19")],
}

#: The untuned reference the tuned configs must beat. Defaults of the strategy.
BASELINE = {"trend_target_atr": 4.0, "trend_stop_atr": 1.2, "fade_stop_atr": 1.5}

#: Deliberately small. A wide grid over a sample this size mines luck — the
#: premium-momentum campaign's own failure mode (a favourable validation window
#: ranked a config that the holdout destroyed).
GRID = {
    # AMENDMENT 1 (2026-07-27, declared BEFORE the holdout was touched, and with
    # the holdout still untouched at the time of writing): the original grid was
    # [3.0, 4.0, 6.0] and EVERY net-positive config in both train and validation
    # landed on 6.0 — the boundary — while the strategy's own parameter_schema
    # allows up to 8.0. An edge-of-grid winner means the search found the edge of
    # the box, not an optimum, so killing the hypothesis on that grid would be
    # unfair to it. 8.0 (the schema max) is added ONCE. This is the only
    # amendment permitted: extending a grid repeatedly until something survives
    # is exactly the luck-mining the three-way split exists to prevent.
    "trend_target_atr": [3.0, 4.0, 6.0, 8.0],
    "trend_stop_atr":   [0.8, 1.2, 1.6],
    "fade_stop_atr":    [1.0, 1.5, 2.0],
}

#: Friction floor. 1%/side is the level at which the premium-momentum finalists
#: were judged; anything softer is not a real test.
SPREAD_PCT_PER_SIDE = 1.0
LOTS = 1


def _ms(date_str: str, end_of_day: bool = False) -> int:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp() * 1000)


def _cost_config() -> Dict[str, Any]:
    """Mandatory rupee cost model + premium-relative spread."""
    return {
        "enabled": True,
        "spread_pct_per_side": SPREAD_PCT_PER_SIDE,
    }


async def run_one(instrument: str, start: str, end: str,
                  params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One (instrument, window, params) backtest. Returns option-net metrics."""
    from app.schemas import BacktestReq, OptionBacktestReq
    from app.strategies.base import get_registry
    from app.warehouse import load_candles_df
    from app.indicators import precompute_all_indicators
    from app.regime import classify_regime_series
    from app.backtest import run_backtest
    from app.runtime import _run_paired_option_backtest

    registry = get_registry()
    if registry.get(STRATEGY_ID) is None:
        registry.auto_discover()   # the registry does not self-populate off the API path
    strategy = registry.get(STRATEGY_ID)
    if strategy is None:
        raise SystemExit(f"strategy {STRATEGY_ID} not registered")

    req = BacktestReq(
        instrument=instrument,
        strategy_id=STRATEGY_ID,
        params=dict(params),
        start_ts=_ms(start),
        end_ts=_ms(end, end_of_day=True),
        costs_enabled=True,          # never optional
        walkforward=False,           # the split IS the discipline here
        option_backtest=OptionBacktestReq(
            enabled=True,
            moneyness="atm",
            lots=LOTS,
            auto_fetch=False,        # READ-ONLY: never fetch during research
            cost_config=_cost_config(),
        ),
        name=f"pooled-regime {instrument} {start}..{end}",
    )

    df = await load_candles_df(instrument.upper(), req.start_ts, req.end_ts)
    if df.empty or len(df) < 50:
        return None

    merged = strategy.merged_params(req.params)
    df = precompute_all_indicators(df, merged)
    df["regime"] = classify_regime_series(df)

    res = run_backtest(
        df, strategy, merged,
        instrument=instrument.upper(),
        costs_enabled=True,
        pretrade_filters=req.pretrade_filters,
        trade_window_start=req.trade_window_start,
        trade_window_end=req.trade_window_end,
    )
    opt = await _run_paired_option_backtest(req, res["trades"], validate=False)
    if not opt:
        return None

    m = opt.get("metrics") or {}
    cov = opt.get("coverage") or {}
    # Gross and charges are kept SEPARATE deliberately: the verdict metric is net,
    # but the gross/charges split is what says WHY a config failed — an edge that
    # never existed reads very differently from one friction ate.
    return {
        "instrument": instrument,
        "window": f"{start}..{end}",
        "params": dict(params),
        "option_net_rupees": m.get("total_option_pnl_value"),
        "option_gross_rupees": m.get("total_gross_option_pnl_value"),
        "charges_rupees": m.get("total_charges"),
        "trades": m.get("paired_trade_count"),
        "win_rate": m.get("win_rate"),
        "spot_trades": len(res.get("trades") or []),
        # Coverage is a HONESTY field: unpaired spot trades mean the option result
        # silently covers fewer sessions than the spot one.
        "unpaired": int(cov.get("spot_trade_count", 0)) - int(cov.get("paired_trade_count", 0)),
        "missing_contract": cov.get("missing_contract"),
    }


def _grid() -> List[Dict[str, Any]]:
    keys = sorted(GRID)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(GRID[k] for k in keys))]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", required=True, choices=sorted(SLICES))
    ap.add_argument("--instruments", nargs="*", default=INSTRUMENTS)
    ap.add_argument("--dry-run", action="store_true",
                    help="run ONE baseline config per instrument (smoke test)")
    ap.add_argument("--i-am-sure", action="store_true",
                    help="required to touch the holdout")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.slice == "holdout" and not args.i_am_sure:
        raise SystemExit(
            "REFUSING: the holdout may be touched ONCE, by the finalists only.\n"
            "Re-run with --i-am-sure when the finalists are chosen and recorded.")

    start, end = SLICES[args.slice]
    configs = [dict(BASELINE)] if args.dry_run else [dict(BASELINE)] + _grid()

    rows: List[Dict[str, Any]] = []
    for inst in args.instruments:
        for cfg in configs:
            try:
                r = await run_one(inst, start, end, cfg)
            except Exception as exc:
                print(f"  !! {inst} {cfg} FAILED: {type(exc).__name__}: {exc}")
                continue
            if r is None:
                print(f"  -- {inst} {cfg}: no data / no option result")
                continue
            rows.append(r)
            print(f"  {inst:9s} net={r['option_net_rupees']!s:>11} "
                  f"gross={r['option_gross_rupees']!s:>10} chg={r['charges_rupees']!s:>9} "
                  f"n={r['trades']!s:>4} unpaired={r['unpaired']!s:>3}  {r['params']}")

    out = args.out or os.path.join(
        os.path.dirname(__file__), f"pooled_regime_{args.slice}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"slice": args.slice, "window": [start, end],
                   "spread_pct_per_side": SPREAD_PCT_PER_SIDE,
                   "excluded": EXCLUDE_WINDOWS, "rows": rows}, fh, indent=2, default=str)
    print(f"\nwrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
