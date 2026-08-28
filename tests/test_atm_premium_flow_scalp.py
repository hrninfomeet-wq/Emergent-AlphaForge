"""Candidate A - ATM Premium-Flow Scalp - plugin behaviour.

The strategy is built on a hypothesis its own screen REJECTED (deliverable
section 16). These tests therefore pin what it DOES, never that it works: every
gate fails closed, the entry rule is exactly the frozen section 4.1 trigger, and
nothing here asserts profitability.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import Signal, get_registry, validate_signal  # noqa: E402

SID = "atm_premium_flow_scalp"


def _strategy():
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    s = reg.get(SID)
    assert s is not None, f"{SID} is not registered"
    return s


def bar(i, *, hhmm="10:00", close=24000.0, vwap=23990.0, adx=25.0, atr=20.0,
        ce_vz=2.0, pe_vz=0.0, ce_dz=1.0, pe_dz=0.0,
        ce_vol=500.0, pe_vol=500.0, med=1000.0, session="2025-06-02"):
    """One enriched bar. Defaults are a CLEAN CE setup so each test can break
    exactly one thing and see only that."""
    return {
        "ts": 1_700_000_000_000 + i * 60_000,
        "open": close, "high": close + 5, "low": close - 5, "close": close,
        "session_date": session, "ist_time": hhmm,
        "vwap": vwap, "atr": atr, "adx": adx,
        "ce_volume_z": ce_vz, "pe_volume_z": pe_vz,
        "ce_oi_delta_z": ce_dz, "pe_oi_delta_z": pe_dz,
        "ce_volume": ce_vol, "pe_volume": pe_vol,
        "atm_volume_median_20d": med,
    }


def run(rows, params=None):
    """Evaluate every row through the real ctx builder; return the Signals."""
    from app.strategies.base import build_eval_ctx
    s = _strategy()
    p = s.merged_params(params or {})
    df = pd.DataFrame(rows)
    extras = s.session_precompute(df, p)
    out = []
    for i in range(len(df)):
        ctx = build_eval_ctx(history_df=df, i=i, instrument="NIFTY",
                             session_date=str(df.iloc[i]["session_date"]),
                             session_extras=extras)
        sig = s.evaluate(df.iloc[i], df.iloc[i - 1] if i else df.iloc[i], p, ctx)
        out.append(validate_signal(sig))
    return out


def fired(sigs):
    return [(i, s.direction) for i, s in enumerate(sigs) if s.direction != "NONE"]


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class TestContract:
    def test_registers_and_declares_the_flow_columns_it_reads(self):
        s = _strategy()
        for col in ("ce_volume_z", "pe_volume_z", "ce_oi_delta_z", "pe_oi_delta_z",
                    "ce_volume", "pe_volume", "atm_volume_median_20d"):
            assert col in s.required_data, f"{col} is read but not declared"

    def test_declares_a_window_that_reaches_the_session_open(self):
        """Session VWAP anchors to the WINDOW start, not 09:15. At the 200-bar
        default the live window stops reaching 09:15 after 12:34 and the VWAP
        confirmation inverts (fc424a1). A full 375-bar session must fit."""
        assert _strategy().live_lookback_bars >= 400

    def test_is_not_marked_builtin(self):
        assert _strategy().is_builtin is False

    def test_signal_threshold_is_pinned_out_of_the_search(self):
        """The score is a constant, so every value at or below it behaves
        identically and every value above suppresses everything - an off switch
        wearing a threshold's name. It took 80.2% of parameter importance in a
        real job before it was pinned (register item #4)."""
        schema = _strategy().parameter_schema
        assert schema["signal_threshold"].get("fixed") is not None

    def test_parameter_budget_is_the_frozen_six_dimensions(self):
        """Section 4.1 freezes the search at six dimensions / 324 combinations.
        Anything else searchable is scope the pre-registration does not cover."""
        schema = _strategy().parameter_schema
        searchable = {k for k, v in schema.items() if v.get("fixed") is None}
        assert searchable == {"flow_z_threshold", "hold_minutes", "stop_bps",
                              "target_mult", "adx_min", "max_trades"}


# ---------------------------------------------------------------------------
# The entry trigger, exactly as section 4.1 froze it
# ---------------------------------------------------------------------------

class TestEntryTrigger:
    def test_fires_ce_on_positive_flow_with_price_agreement(self):
        sigs = run([bar(0)])
        assert sigs[0].direction == "CE"

    def test_fires_pe_on_negative_flow_with_price_agreement(self):
        sigs = run([bar(0, ce_vz=0.0, pe_vz=2.0, ce_dz=0.0, pe_dz=1.0,
                        close=24000.0, vwap=24010.0)])
        assert sigs[0].direction == "PE"

    def test_flow_below_threshold_does_not_fire(self):
        sigs = run([bar(0, ce_vz=0.6, pe_vz=0.0, ce_dz=0.2, pe_dz=0.0)])
        assert sigs[0].direction == "NONE"

    def test_all_four_z_terms_enter_the_imbalance(self):
        """flow = (ce_vz - pe_vz) + (ce_dz - pe_dz). Each term must be able to
        veto on its own, or a dropped term would never be noticed."""
        # ce_vz alone clears; a large pe_vz must cancel it
        assert run([bar(0, ce_vz=3.0, pe_vz=3.0, ce_dz=0.0, pe_dz=0.0)])[0].direction == "NONE"
        # oi-delta alone must be able to carry the signal
        assert run([bar(0, ce_vz=0.0, pe_vz=0.0, ce_dz=2.0, pe_dz=0.0)])[0].direction == "CE"
        # and a large pe_dz must cancel it
        assert run([bar(0, ce_vz=0.0, pe_vz=0.0, ce_dz=2.0, pe_dz=2.0)])[0].direction == "NONE"

    def test_price_must_agree_with_the_flow_side(self):
        """Flow without price agreement is not a signal (section 4.1 step 2)."""
        assert run([bar(0, close=23980.0, vwap=23990.0)])[0].direction == "NONE"
        assert run([bar(0, ce_vz=0.0, pe_vz=2.0, ce_dz=0.0, pe_dz=1.0,
                        close=24000.0, vwap=23990.0)])[0].direction == "NONE"

    def test_adx_floor_excludes_chop(self):
        assert run([bar(0, adx=19.9)])[0].direction == "NONE"
        assert run([bar(0, adx=20.0)])[0].direction == "CE"

    def test_liquidity_floor_uses_half_the_causal_median(self):
        assert run([bar(0, ce_vol=200.0, pe_vol=200.0, med=1000.0)])[0].direction == "NONE"
        assert run([bar(0, ce_vol=300.0, pe_vol=200.0, med=1000.0)])[0].direction == "CE"


# ---------------------------------------------------------------------------
# Everything fails CLOSED
# ---------------------------------------------------------------------------

class TestFailsClosed:
    @pytest.mark.parametrize("col", ["ce_volume_z", "pe_volume_z",
                                     "ce_oi_delta_z", "pe_oi_delta_z"])
    def test_an_unavailable_z_score_blocks_rather_than_scoring_zero(self, col):
        """NaN means UNKNOWN, not 'perfectly typical'. On SENSEX 61% of
        oi-delta baselines are flat, so this path is the common case there, not
        an edge case (deliverable 15.4b)."""
        rows = [bar(0)]
        rows[0][col] = float("nan")
        sig = run(rows)[0]
        assert sig.direction == "NONE"
        assert sig.blockers, "a blocked bar must say why"

    def test_missing_atm_bar_blocks(self):
        rows = [bar(0)]
        rows[0]["ce_volume"] = float("nan")
        assert run(rows)[0].direction == "NONE"

    def test_missing_liquidity_median_blocks(self):
        rows = [bar(0)]
        rows[0]["atm_volume_median_20d"] = float("nan")
        assert run(rows)[0].direction == "NONE"

    @pytest.mark.parametrize("col", ["vwap", "atr", "adx"])
    def test_a_nan_indicator_blocks(self, col):
        rows = [bar(0)]
        rows[0][col] = float("nan")
        assert run(rows)[0].direction == "NONE"

    def test_a_missing_column_blocks_rather_than_raising(self):
        rows = [bar(0)]
        del rows[0]["ce_oi_delta_z"]
        assert run(rows)[0].direction == "NONE"


# ---------------------------------------------------------------------------
# Windows, cooldown and the session budget
# ---------------------------------------------------------------------------

class TestWindowAndBudget:
    def test_no_entry_before_0925_or_at_or_after_1448(self):
        assert run([bar(0, hhmm="09:24")])[0].direction == "NONE"
        assert run([bar(0, hhmm="09:25")])[0].direction == "CE"
        assert run([bar(0, hhmm="14:47")])[0].direction == "CE"
        assert run([bar(0, hhmm="14:48")])[0].direction == "NONE"
        assert run([bar(0, hhmm="15:10")])[0].direction == "NONE"

    def test_cooldown_suppresses_a_second_signal_in_the_same_direction(self):
        rows = [bar(i, hhmm=f"10:{i:02d}") for i in range(40)]
        got = fired(run(rows))
        assert got[0] == (0, "CE")
        assert all(i == 0 or i >= 30 for i, _ in got), got

    def test_the_session_budget_is_enforced(self):
        rows = [bar(i, hhmm=f"1{i//60}:{i%60:02d}") for i in range(240)]
        assert len(fired(run(rows, {"max_trades": 1}))) == 1
        assert len(fired(run(rows, {"max_trades": 2}))) == 2

    def test_the_budget_and_cooldown_reset_each_session(self):
        a = [bar(i, hhmm=f"10:{i:02d}", session="2025-06-02") for i in range(5)]
        b = [bar(100 + i, hhmm=f"10:{i:02d}", session="2025-06-03") for i in range(5)]
        got = fired(run(a + b, {"max_trades": 1}))
        assert [i for i, _ in got] == [0, 5]


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

class TestExits:
    def test_stop_is_the_larger_of_the_bps_floor_and_the_atr_term(self):
        # 4 bps of 24000 = 9.6 pts; 0.6 * atr 5 = 3.0 -> bps floor wins
        s = run([bar(0, close=24000.0, atr=5.0)], {"stop_bps": 4.0})[0]
        assert s.spot_stop_pts == pytest.approx(9.6, abs=1e-6)
        # 0.6 * atr 40 = 24.0 beats the 9.6 floor
        s = run([bar(0, close=24000.0, atr=40.0)], {"stop_bps": 4.0})[0]
        assert s.spot_stop_pts == pytest.approx(24.0, abs=1e-6)

    def test_target_is_a_multiple_of_the_resolved_stop(self):
        s = run([bar(0, close=24000.0, atr=40.0)],
                {"stop_bps": 4.0, "target_mult": 2.0})[0]
        assert s.spot_target_pts == pytest.approx(2.0 * s.spot_stop_pts, abs=1e-6)

    def test_stop_scales_with_spot_so_it_transfers_across_indices(self):
        """SENSEX is ~3.2x NIFTY's point scale at the same relative volatility.
        A stop in absolute points cannot serve both."""
        n = run([bar(0, close=24000.0, atr=1.0)], {"stop_bps": 4.0})[0]
        s = run([bar(0, close=81000.0, vwap=80990.0, atr=1.0)], {"stop_bps": 4.0})[0]
        assert s.spot_stop_pts == pytest.approx(n.spot_stop_pts * 81000 / 24000, rel=1e-6)

    def test_time_stop_is_the_hold_horizon(self):
        assert run([bar(0)], {"hold_minutes": 10})[0].time_stop_minutes == 10
        assert run([bar(0)], {"hold_minutes": 15})[0].time_stop_minutes == 15


# ---------------------------------------------------------------------------
# Look-ahead safety - the claim the module docstring makes by name
# ---------------------------------------------------------------------------

class TestLookAhead:
    def test_precompute_is_look_ahead_safe_on_every_prefix(self):
        """`_admitted_entries` scans the whole frame, so it must be shown that
        the set it admits on a PREFIX is exactly the full-history set truncated
        at that prefix. If a later bar could change an earlier admission, the
        backtest would be reading tomorrow."""
        from app.strategies.plugins.atm_premium_flow_scalp import _admitted_entries
        import random
        rnd = random.Random(11)
        rows = []
        for i in range(180):
            rows.append(bar(
                i, hhmm=f"1{i // 60}:{i % 60:02d}",
                ce_vz=rnd.uniform(-3, 3), pe_vz=rnd.uniform(-3, 3),
                ce_dz=rnd.uniform(-3, 3), pe_dz=rnd.uniform(-3, 3),
                adx=rnd.uniform(15, 35), close=24000 + rnd.uniform(-40, 40),
            ))
        df = pd.DataFrame(rows)
        params = _strategy().merged_params({})
        full = _admitted_entries(df, params).get("2025-06-02", {})
        assert full, "guard: the fixture must actually admit something"
        for cut in range(1, len(df) + 1):
            prefix = _admitted_entries(df.iloc[:cut].reset_index(drop=True), params)
            got = prefix.get("2025-06-02", {})
            expected = {i: d for i, d in full.items() if i < cut}
            assert got == expected, f"prefix {cut}: {got} != {expected}"

    def test_evaluate_reads_no_column_outside_the_declared_set(self):
        """A read of an undeclared column would be an all-NaN silent failure in
        every path that does not happen to build it."""
        from app.strategies.plugins import atm_premium_flow_scalp as mod
        s = _strategy()
        declared = set(mod._REQUIRED_COLUMNS)
        assert set(s.required_data) <= declared
        engine = {"close", "session_date", "ist_time", "vwap", "atr", "adx"}
        assert declared - set(s.required_data) == engine


# ---------------------------------------------------------------------------
# Guards that both CALLERS happen to cover, so they need pinning directly.
# `flow_imbalance` is public: an analysis script or an optimizer probe can call
# it without going through evaluate()'s own finiteness gate, and then its
# refusal to invent a value is the only thing standing there.
# ---------------------------------------------------------------------------

class TestFlowImbalanceDirectly:
    def test_computes_the_frozen_four_term_expression(self):
        from app.strategies.plugins.atm_premium_flow_scalp import flow_imbalance
        row = {"ce_volume_z": 2.0, "pe_volume_z": 0.5,
               "ce_oi_delta_z": 1.0, "pe_oi_delta_z": 0.25}
        assert flow_imbalance(row) == pytest.approx((2.0 - 0.5) + (1.0 - 0.25))

    @pytest.mark.parametrize("col", ["ce_volume_z", "pe_volume_z",
                                     "ce_oi_delta_z", "pe_oi_delta_z"])
    def test_refuses_rather_than_treating_a_missing_term_as_zero(self, col):
        """A partial sum is a DIFFERENT quantity wearing the same name. Scoring
        the missing term as 0.0 would read as 'this side is perfectly typical',
        which on SENSEX would be the answer on ~61% of bars."""
        from app.strategies.plugins.atm_premium_flow_scalp import flow_imbalance
        row = {"ce_volume_z": 2.0, "pe_volume_z": 0.5,
               "ce_oi_delta_z": 1.0, "pe_oi_delta_z": 0.25}
        row[col] = float("nan")
        assert flow_imbalance(row) is None
        del row[col]
        assert flow_imbalance(row) is None

    def test_refuses_a_non_finite_term(self):
        from app.strategies.plugins.atm_premium_flow_scalp import flow_imbalance
        row = {"ce_volume_z": float("inf"), "pe_volume_z": 0.0,
               "ce_oi_delta_z": 0.0, "pe_oi_delta_z": 0.0}
        assert flow_imbalance(row) is None


class TestCooldownIsPerDirection:
    def test_an_opposite_side_signal_is_not_held_off_by_the_other_sides_cooldown(self):
        """Section 4.1 says one signal per DIRECTION per 30 bars. A shared
        cooldown would silently halve the strategy's opportunity set."""
        rows = [bar(0, hhmm="10:00")]                      # CE
        rows.append(bar(1, hhmm="10:01", ce_vz=0.0, pe_vz=2.0, ce_dz=0.0,
                        pe_dz=1.0, close=24000.0, vwap=24010.0))   # PE, 1 bar later
        got = fired(run(rows, {"max_trades": 2}))
        assert got == [(0, "CE"), (1, "PE")], got


class TestBlockersNameTheColumn:
    @pytest.mark.parametrize("col", ["ce_oi_delta_z", "vwap", "atr", "adx"])
    def test_a_blocked_bar_names_the_thing_that_was_missing(self, col):
        """'blocked' without a reason sends an operator nowhere. On SENSEX the
        flow columns are absent as a matter of course, so distinguishing that
        from a genuinely broken indicator is the difference between normal
        operation and an incident."""
        rows = [bar(0)]
        rows[0][col] = float("nan")
        sig = run(rows)[0]
        assert sig.direction == "NONE"
        assert any(col in b for b in sig.blockers), sig.blockers
