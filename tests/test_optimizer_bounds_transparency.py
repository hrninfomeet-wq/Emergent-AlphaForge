"""The operator must be able to SEE which search bounds produced a result.

CONFIRMED defect: frontend/src/pages/Optimizer.jsx never cleared `param_overrides`
when the strategy changed, and the bounds panel only renders the SELECTED strategy's
params. A real confluence_scalper job therefore carried 12 overrides:

  * 11 named parameters confluence_scalper does not declare (fib_entry_low,
    fib_entry_high, stop_atr_mult, target_mult, range_mult, max_trades_per_session,
    entry_cutoff_minutes_after_open, signal_cooldown_bars, hold_max_minutes,
    stop_bps, max_trades) - silently ignored by _build_param_space, and
  * spot_target_pts {max: 300}, which WIDENED the strategy's own declared max of 200
    (backend/app/strategies/builtin/confluence_scalper.py:30).

Nothing on screen said either had happened. The widened bound is deliberately NOT
removed - it materially helped, and the operator asked to keep control of it - but it
is now visible, and the inert leftovers can be cleared in one click.

The `boundsAudit` logic lives inside the component, so these are source-level guards;
the rendered behaviour is verified in the browser walkthrough.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMIZER_JSX = os.path.join(ROOT, "frontend", "src", "pages", "Optimizer.jsx")
CONFLUENCE = os.path.join(ROOT, "backend", "app", "strategies", "builtin",
                          "confluence_scalper.py")


@pytest.fixture(scope="module")
def src() -> str:
    with open(OPTIMIZER_JSX, encoding="utf-8") as fh:
        return fh.read()


def test_bounds_audit_splits_active_from_foreign_overrides(src):
    i = src.index("const boundsAudit = useMemo(")
    body = src[i:i + 1200]
    # classified against what the SELECTED strategy declares
    assert "selectedStrategy?.parameter_schema" in body
    assert "declared.has(name)" in body
    # an override with neither min nor max set is not "in force"
    assert "o.min !== undefined || o.max !== undefined" in body


def test_collapsed_panel_states_what_is_in_force(src):
    """The panel is collapsed by default — that is where the leak hid."""
    assert 'data-testid="opt-bounds-summary"' in src
    i = src.index('data-testid="opt-bounds-summary"')
    body = src[i:i + 900]
    assert "declared bounds for every parameter" in body   # the clean case
    assert "overridden" in body                            # the in-force case
    assert "left over from another strategy" in body       # the inert case


def test_foreign_overrides_are_surfaced_and_clearable(src):
    assert 'data-testid="opt-foreign-overrides"' in src
    assert 'data-testid="opt-clear-foreign-overrides"' in src
    i = src.index("const clearForeignOverrides")
    body = src[i:i + 400]
    # clears ONLY the foreign keys — never the operator's real overrides
    assert "boundsAudit.foreign.forEach" in body
    assert "active" not in body


def test_an_overridden_row_is_visually_marked_with_the_declared_range(src):
    i = src.index("overridden — strategy declares") if "overridden — strategy declares" in src \
        else src.index("overridden")
    body = src[max(0, i - 400):i + 400]
    assert "text-warning" in body
    assert "def.min" in body and "def.max" in body


def test_the_beneficial_widened_bound_is_not_auto_removed(src):
    """The operator explicitly wanted to keep control of bounds that DO apply."""
    i = src.index("const clearForeignOverrides")
    body = src[i:i + 400]
    # only `foreign` is deleted; nothing iterates `active` to remove it
    assert "delete next[k]" in body
    assert "boundsAudit.active" not in body


def test_the_real_leak_case_is_classified_correctly():
    """spot_target_pts IS declared by confluence_scalper (so it bites and must show
    as overridden); fib_entry_low is NOT (so it is inert)."""
    with open(CONFLUENCE, encoding="utf-8") as fh:
        strat = fh.read()
    schema = strat[strat.index("parameter_schema = {"):]
    schema = schema[:schema.index("}\n")]
    assert '"spot_target_pts"' in schema
    assert "fib_entry_low" not in schema
    # and the declared ceiling really is 200, which the leftover override raised to 300
    m = re.search(r'"spot_target_pts":\s*\{[^}]*"max":\s*(\d+)', schema)
    assert m and int(m.group(1)) == 200
