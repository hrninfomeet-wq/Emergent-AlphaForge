"""Paper must report the same accounting facts as live, under every setting.

Paper's entire purpose is to rehearse live. It currently books a different
accounting basis depending on a per-deployment toggle, and those trades are then
summed into ONE equity curve:

  friction OFF          -> realized_pnl = gross, total_charges = the REAL figure
  friction ON, costs off -> realized_pnl = gross-after-slippage, total_charges = 0.0

`total_charges = 0.0` is not "costs disabled", it is a false statement about what
the exchange would take — and the deploy wizard's DEFAULT is exactly that branch
(`friction_enabled: true`, `friction_costs_enabled: false`). So the default paper
deployment reports zero STT/GST/stamp while a friction-OFF deployment beside it
reports the truth, and an aggregate over both means nothing.

The friction-OFF branch already had the right idea and says so in its own comment:
"statutory charges are now ALWAYS computed and reported so the operator sees what
the exchange would have taken". This extends that rule to every branch.

Deliberately unchanged: whether charges are DEDUCTED from `realized_pnl` still
follows the operator's cost toggle, and `realized_pnl` remains the field the caps
and kill-switch read. Reporting a number is not the same as changing what the risk
limit means — the operator confirmed the cap is a premium-move limit.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_friction import FrictionConfig, close_economics  # noqa: E402
from app.option_costs import CostConfig, round_trip_charges  # noqa: E402

_QTY = 65
_ENTRY = 100.0
_EXIT = 110.0


def _friction(*, enabled: bool, costs_enabled: bool) -> FrictionConfig:
    f = FrictionConfig()
    f.enabled = enabled
    f.costs = CostConfig(enabled=costs_enabled)
    return f


def _close(**kw):
    return close_economics(
        raw_exit_premium=_EXIT, entry_price=_ENTRY, raw_entry_price=_ENTRY,
        quantity=_QTY, ts_ms=0, friction=kw.pop("friction"))


def test_charges_are_reported_when_friction_is_off():
    """Existing behaviour, pinned so the consolidation cannot regress it."""
    out = _close(friction=_friction(enabled=False, costs_enabled=False))
    assert out["total_charges"] > 0.0


def test_charges_are_reported_when_friction_is_ON_but_costs_are_off():
    """THE default deployment. It reported total_charges = 0.0 — a false claim
    about what the exchange takes, not a disabled feature."""
    out = _close(friction=_friction(enabled=True, costs_enabled=False))
    assert out["total_charges"] > 0.0, (
        "the wizard-default paper deployment reports zero statutory charges while "
        "a friction-OFF deployment beside it reports the truth")


def test_the_COST_TOGGLE_never_changes_the_reported_charges():
    """The bug: the cost toggle changed a statutory FACT.

    The fill model legitimately may — charges are a turnover tax, so a slipped
    fill is taxed on a smaller turnover, and the friction-OFF branch documents
    that asymmetry deliberately ("charges the RAW exit ... deliberately asymmetric
    with the enabled branch, which charges the slipped fill"). What must NOT vary
    is whether the operator asked to MODEL costs in P&L.
    """
    for enabled in (False, True):
        off = _close(friction=_friction(enabled=enabled, costs_enabled=False))
        on = _close(friction=_friction(enabled=enabled, costs_enabled=True))
        assert off["total_charges"] == on["total_charges"], (
            f"friction_enabled={enabled}: the cost toggle changed what the "
            f"exchange takes, which is not the operator's to choose")
        assert off["total_charges"] > 0.0


def test_reported_charges_match_the_shared_statutory_model():
    """Friction OFF has no fill model, so it is taxed on the raw exit — an exact
    comparison against the shared schedule."""
    out = _close(friction=_friction(enabled=False, costs_enabled=False))
    expected = round_trip_charges(entry_premium=_ENTRY, exit_premium=_EXIT,
                                  quantity=_QTY, cfg=CostConfig(enabled=True))
    assert out["total_charges"] == round(float(expected["total_charges"]), 2)


def test_net_realized_is_fully_net_under_every_setting():
    """`net_realized_pnl` is the ONE field comparable across deployments that were
    configured differently: always fully net of the statutory schedule, whatever
    the operator chose to model in `realized_pnl`."""
    for e in (False, True):
        for c in (False, True):
            out = _close(friction=_friction(enabled=e, costs_enabled=c))
            assert out["net_realized_pnl"] <= out["realized_pnl"] + 0.01, (e, c)
            assert out["net_realized_pnl"] < out["gross_realized_pnl"], (e, c)
    # With costs ON the deduction happens once, not twice.
    on = _close(friction=_friction(enabled=True, costs_enabled=True))
    assert on["net_realized_pnl"] == on["realized_pnl"]


def test_deduction_still_follows_the_operator_toggle():
    """Reporting a charge is not the same as charging it. With costs OFF the
    operator asked not to model them in P&L, and realized_pnl obeys."""
    off = _close(friction=_friction(enabled=True, costs_enabled=False))
    on = _close(friction=_friction(enabled=True, costs_enabled=True))
    assert on["realized_pnl"] < off["realized_pnl"], (
        "enabling costs must still deduct them from realized_pnl")


def test_realized_pnl_is_unchanged_by_this_fix():
    """The caps and kill-switch read realized_pnl; its meaning must not shift as
    a side effect of reporting charges."""
    out = _close(friction=_friction(enabled=False, costs_enabled=False))
    assert out["realized_pnl"] == out["gross_realized_pnl"]


def test_a_zero_quantity_close_claims_no_charges():
    out = close_economics(raw_exit_premium=_EXIT, entry_price=_ENTRY,
                          raw_entry_price=_ENTRY, quantity=0, ts_ms=0,
                          friction=_friction(enabled=True, costs_enabled=False))
    assert out["total_charges"] == 0.0


# --- the deployment's OWN schedule must be honoured -------------------------
#
# Found by an adversarial audit of this very file's first version, which passed
# the whole suite. The bug: `cfg=CostConfig(enabled=True)` was substituted for
# `cfg=friction.costs`, silently discarding operator-set brokerage and any pinned
# rate. `round_trip_charges` never reads `cfg.enabled` — only the guard needed to
# change — so the swap was unnecessary AND it moved `realized_pnl`, the field the
# kill switch and daily governor read.
#
# The suite stayed green because every existing fixture leaves all six rates at
# their defaults, so a defaults-substitution bug is invisible to it. These tests
# use a NON-default schedule for exactly that reason.

def _custom_friction(*, costs_enabled: bool) -> FrictionConfig:
    f = FrictionConfig()
    f.enabled = True
    f.costs = CostConfig(enabled=costs_enabled)
    f.costs.brokerage_per_order = 20.0        # e.g. a Zerodha-style preset
    return f


def test_operator_set_brokerage_is_actually_charged():
    out = _close(friction=_custom_friction(costs_enabled=True))
    expected = round_trip_charges(entry_premium=_ENTRY, exit_premium=out["exit_fill_price"],
                                  quantity=_QTY, cfg=_custom_friction(costs_enabled=True).costs)
    assert out["total_charges"] == round(float(expected["total_charges"]), 2), (
        "the deployment's own cost schedule was discarded for module defaults")


def test_a_custom_schedule_is_reported_even_when_costs_are_off():
    """The whole point of the change — report the truth — must use the RIGHT rates."""
    off = _close(friction=_custom_friction(costs_enabled=False))
    on = _close(friction=_custom_friction(costs_enabled=True))
    assert off["total_charges"] == on["total_charges"] > 0.0


def test_realized_pnl_matches_the_deployments_own_schedule():
    """`realized_pnl` is the caps / kill-switch basis: it must reflect what the
    operator configured, not what the module defaults to."""
    out = _close(friction=_custom_friction(costs_enabled=True))
    default_only = _close(friction=_friction(enabled=True, costs_enabled=True))
    assert out["realized_pnl"] != default_only["realized_pnl"], (
        "a Rs 20/order schedule produced the same P&L as a zero-brokerage one — "
        "the configured rates are being ignored")
    assert out["realized_pnl"] < default_only["realized_pnl"]
