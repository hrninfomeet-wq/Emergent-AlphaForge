"""Phase 4 engine dispatch — Parity + validation tests for PremiumTriggerConfig
and dispatch_backtest.

The HARD invariant (Phase 4-5 spec §3.5 "regression safety"):

    run_premium_momentum_backtest(instrument=..., params=raw_dict)
      MUST equal
    dispatch_backtest(cfg=PremiumTriggerConfig(**raw_dict), instrument=..., ...)

byte-identical on trades, coverage, and summary. The dispatch layer is a lift
of the existing bespoke path; introducing ANY drift here would silently break
existing single-leg `premium_momentum` deployments the moment they graduate to
the config-driven runtime.

Also covers config-schema validation (typos, out-of-range values, mutually-
exclusive knobs — these must fail LOUDLY at validation time, not at sim time).

Host-safe / pure: no motor, no LLM, no network. Fixtures are the same pattern
as tests/test_premium_momentum_backtest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_momentum_backtest import run_premium_momentum_backtest
from app.premium_trigger_config import PremiumTriggerConfig, config_from_dict
from app.premium_trigger_dispatch import dispatch_backtest


# --------------------------------------------------------------------------
# Shared fixtures (mirror tests/test_premium_momentum_backtest.py's shape).
# --------------------------------------------------------------------------
def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


def _simple_ce_wins_scenario():
    """CE premium 100 -> 150 crossing +15% at ts3. PE never crosses.
    Returns (spot_df, option_candles, contracts)."""
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0), _spot_bar(4, "09:34", 24020.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 110.0),
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 150.0),
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 101.0),
        _opt("PE|24050", 3, 102.0), _opt("PE|24050", 4, 103.0),
    ])
    return spot, opt, contracts


# =========================================================================
# The parity invariant (must never regress)
# =========================================================================
def test_dispatch_produces_byte_identical_trades_to_bespoke_path():
    """The CORE Phase 4 invariant. Same inputs -> same trades -> same coverage
    -> same summary. Any drift here silently breaks all existing shipped
    `premium_momentum` deployments the moment they graduate to the config-
    driven runtime."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    raw = {"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
           "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0, "lots": 2}

    bespoke = run_premium_momentum_backtest(
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY", params=dict(raw),
    )
    dispatched = dispatch_backtest(
        cfg=config_from_dict(raw),
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY",
    )

    # Byte-identical trades / coverage / summary.
    assert dispatched["trades"] == bespoke["trades"], (
        "Dispatch drift on `trades` — the config-driven path produced different "
        "trade tuples than the bespoke path. This BREAKS all existing shipped "
        "premium_momentum deployments. Investigate the to_backtest_params() "
        "translation in premium_trigger_config.py."
    )
    assert dispatched["coverage"] == bespoke["coverage"]
    assert dispatched["summary"] == bespoke["summary"]


def test_dispatch_adds_traceability_fields_without_disturbing_result_shape():
    """The dispatch layer records the config used, but must NOT overwrite any
    existing result field (the frontend + optimizer read trades/coverage/summary
    directly)."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    cfg = config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0})
    r = dispatch_backtest(cfg=cfg, spot_df=spot, option_candles=opt,
                          contracts=contracts, instrument="NIFTY")
    assert r["dispatch"] == "premium_trigger_config"
    assert "premium_trigger_config" in r
    assert r["premium_trigger_config"]["momentum_pct"] == 15.0
    # Existing keys still present.
    for k in ("trades", "coverage", "summary", "params"):
        assert k in r


def test_dispatch_parity_with_ce_only_side():
    """Same invariant, `side="ce"` variant — a common config."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    raw = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
           "momentum_pct": 15.0, "stop_pct": 20.0}
    bespoke = run_premium_momentum_backtest(
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY", params=dict(raw),
    )
    dispatched = dispatch_backtest(
        cfg=config_from_dict(raw), spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY",
    )
    assert dispatched["trades"] == bespoke["trades"]


def test_dispatch_parity_with_trail():
    """Same invariant with a stepped X-Y trail configured (both trail_x and
    trail_y must be forwarded — validator ensures they come as a pair)."""
    spot, opt, contracts = _simple_ce_wins_scenario()
    raw = {"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
           "momentum_pct": 15.0, "stop_pct": 20.0, "trail_x": 5.0, "trail_y": 5.0}
    bespoke = run_premium_momentum_backtest(
        spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY", params=dict(raw),
    )
    dispatched = dispatch_backtest(
        cfg=config_from_dict(raw), spot_df=spot.copy(), option_candles=opt.copy(),
        contracts=list(contracts), instrument="NIFTY",
    )
    assert dispatched["trades"] == bespoke["trades"]


# =========================================================================
# Schema validation — typos, out-of-range, mutually-exclusive knobs must
# fail LOUDLY at validation time, not silently at sim time.
# =========================================================================
def test_config_rejects_unknown_field():
    """A typo in a field name (e.g., `moneynesss` with 3 s's) must surface
    IMMEDIATELY — silently accepting an extra field would mean the sim runs
    with a default the caller didn't intend."""
    with pytest.raises(Exception) as exc:
        config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0, "moneynesss": "itm1"})
    assert "extra" in str(exc.value).lower() or "moneynesss" in str(exc.value)


def test_config_rejects_both_momentum_pct_and_pts():
    """Mutually-exclusive entry-trigger knobs. The sim would silently prefer
    momentum_pct and ignore momentum_pts — surfacing this at validation avoids
    a bad-parity surprise vs. the bespoke path (which behaves the same but
    doesn't warn)."""
    with pytest.raises(Exception) as exc:
        config_from_dict({"momentum_pct": 15.0, "momentum_pts": 5.0, "stop_pct": 20.0})
    assert "either" in str(exc.value).lower() or "not both" in str(exc.value).lower()


def test_config_rejects_missing_entry_trigger():
    """A config with no entry threshold is nonsensical."""
    with pytest.raises(Exception) as exc:
        config_from_dict({"reference_time": "09:31", "stop_pct": 20.0})
    assert "momentum" in str(exc.value).lower()


def test_config_rejects_solo_trail_x():
    """trail_x without trail_y is a genuine misconfiguration — the sim ignores
    a lone trail_x, so the caller thinks they set a trail but the sim disables
    it. Surface at validation."""
    with pytest.raises(Exception) as exc:
        config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0, "trail_x": 5.0})
    assert "trail" in str(exc.value).lower()


def test_config_rejects_bad_hhmm():
    with pytest.raises(Exception):
        config_from_dict({"reference_time": "9:31", "momentum_pct": 15.0, "stop_pct": 20.0})
    with pytest.raises(Exception):
        config_from_dict({"reference_time": "25:00", "momentum_pct": 15.0, "stop_pct": 20.0})


def test_config_case_insensitive_on_side_and_moneyness():
    """Wire ergonomics — the frontend may send 'CE' or 'ITM1' in uppercase."""
    cfg = config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0,
                             "side": "CE", "moneyness": "ITM1"})
    assert cfg.side == "ce"
    assert cfg.moneyness == "itm1"


def test_config_to_backtest_params_omits_none_fields():
    """The sim's `params.get('stop_pct')` returns None when omitted — same as
    when the key is missing entirely. Omitting None keeps the dict small and
    matches how the bespoke path is called in the existing test suite."""
    cfg = config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0})
    p = cfg.to_backtest_params()
    for missing_key in ("stop_pts", "target_pct", "target_pts",
                         "trail_x", "trail_y", "momentum_pts",
                         "late_lock_cutoff", "cost_config"):
        assert missing_key not in p, f"expected {missing_key!r} to be omitted"


def test_config_defaults_match_shipped_premium_momentum_behavior():
    """The declarative config's DEFAULTS must match the shipped `premium_
    momentum` plugin's defaults — otherwise a config-driven deployment created
    with just `momentum_pct` set would silently trade differently from a
    strategy_id-based deployment. See docs/STRATEGY_DEPLOYMENTS.md."""
    cfg = config_from_dict({"momentum_pct": 15.0, "stop_pct": 20.0})
    # These four defaults must lock to the shipped values.
    assert cfg.reference_time == "09:31"
    assert cfg.moneyness == "itm1"
    assert cfg.side == "first_to_trigger"
    assert cfg.lots == 1
