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


def test_capability_summary_shape():
    from app.ai.capability import capability_summary
    c = capability_summary()
    for k in ("columns", "features", "cannot_build", "needs_engine_work", "data_limits"):
        assert k in c and c[k] is not None, k
    assert len(c["columns"]) > 0
    # the classic can't-builds must be surfaced so users aren't surprised
    blob = " ".join(c["cannot_build"]).lower()
    assert "open interest" in blob and "greeks" in blob and "order flow" in blob
    # every feature entry carries a live-feasible flag for the backtest-only badge
    for f in c["features"]:
        assert "name" in f and "live_feasible" in f


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


def test_wizard_has_engine_capabilities_panel():
    assert 'data-testid="author-caps-panel"' in WIZ
    assert 'data-testid="author-caps-toggle"' in WIZ
    assert "catalog?.capability" in WIZ
    assert "Can't build" in WIZ and "Data limits" in WIZ
