"""Contract pins for the 2026-07-05 authoring-UX fixes.

The user hit: a feasibility check flashed a Gemini truncation error as a toast
that vanished before it could be read, the engine's constraints were never
explained up-front, and there was no way to see, rule by rule, what got built.
These pin the fixes:
  * capability_summary() exposes can/can't-build + data limits for the wizard
  * the wizard renders PERSISTENT error panels (not flash toasts) for both
    generate and feasibility failures, plus an engine-capabilities panel
  * the catalog route ships the capability summary
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

ROOT = pathlib.Path(__file__).resolve().parents[1]
WIZ = (ROOT / "frontend/src/components/strategy/AuthoringWizard.jsx").read_text(encoding="utf-8")
ADMIN = (ROOT / "backend/app/routers/strategies_admin.py").read_text(encoding="utf-8")


def test_capability_summary_has_honest_tiers():
    from app.ai.capability import capability_summary
    c = capability_summary()
    for k in ("build_now", "backtest_only", "addable_data", "needs_engine",
              "infeasible", "data_limits", "columns", "features"):
        assert k in c and c[k] is not None, k
    assert len(c["build_now"]["columns"]) > 0
    # backtest-only tier is derived from live_feasible==False -> the SMC/ICT trio
    bt = {f["name"] for f in c["backtest_only"]["features"]}
    assert {"choch", "fvg_zones", "order_block"} <= bt
    # build-now features are all live-feasible (fidelity in both backtest and live)
    for f in c["build_now"]["features"]:
        assert f["live_feasible"] is not False
    # addable = OI/greeks (broker feed carries them); infeasible = order flow / tape
    assert any("Open interest" in x for x in c["addable_data"]["items"])
    assert any("greeks" in x.lower() for x in c["addable_data"]["items"])
    assert any("Order flow" in x for x in c["infeasible"]["items"])
    # the two tiers must NOT be conflated
    assert c["addable_data"]["items"] and c["infeasible"]["items"]


def test_catalog_route_ships_capability():
    assert "capability_summary" in ADMIN
    i = ADMIN.index("async def author_catalog")
    block = ADMIN[i:i + 900]
    assert '"capability": capability_summary()' in block


def test_wizard_persists_errors_not_flash_toasts():
    # dedicated persistent error state, set on failure, rendered in panels
    assert "converseError" in WIZ and "genError" in WIZ
    assert 'data-testid="author-converse-error"' in WIZ
    assert 'data-testid="author-gen-error"' in WIZ
    # the feasibility failure must NOT be a disappearing toast anymore
    i = WIZ.index("async function runConverse")
    block = WIZ[i:i + 700]
    assert "setConverseError" in block
    assert "toast.error" not in block  # no flash toast in the feasibility path


def test_wizard_has_four_tier_capabilities_panel():
    assert 'data-testid="author-caps-panel"' in WIZ
    assert 'data-testid="author-caps-toggle"' in WIZ
    assert "catalog?.capability" in WIZ
    # the four honest tiers must each render, so backtest-only isn't conflated
    # with can't-build and addable-with-data isn't conflated with infeasible
    for t in ("cap-tier-build-now", "cap-tier-backtest-only", "cap-tier-addable",
              "cap-tier-infeasible"):
        assert f'data-testid="{t}"' in WIZ, t
    assert "live fidelity not guaranteed" in WIZ
    assert "Out of reach on this infrastructure" in WIZ
    assert "Data limits" in WIZ
