"""Contract pins for the Saved Presets page (route + nav + key surfaces).

Frontend-only feature; like the other FE contract tests these string-assert on
source so a rename/removal of the route, nav entry, or testids is caught.
"""
from __future__ import annotations

from pathlib import Path

FE = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _read(rel: str) -> str:
    return (FE / rel).read_text(encoding="utf-8")


def test_route_and_nav_wired():
    app = _read("App.js")
    assert 'path="/presets"' in app
    assert "SavedPresets" in app
    layout = _read("components/Layout.jsx")
    assert 'to: "/presets"' in layout
    assert "Saved Presets" in layout
    assert "nav-presets" in layout


def test_page_groups_by_source_with_actions():
    src = _read("pages/SavedPresets.jsx")
    for tid in (
        "saved-presets-page", "preset-card", "preset-deploy", "preset-open-lab",
        "preset-rename", "preset-duplicate", "preset-delete",
        "preset-deployed-badge", "preset-search",
    ):
        assert tid in src, tid
    # The two source groups (testid rendered as preset-group-${id}).
    assert "preset-group-" in src
    assert 'id="optimizer"' in src
    assert 'id="backtest"' in src
    # Source grouping + the deploy / open-in-lab deep-links the cards use.
    assert "presetSource" in src
    assert "/live?preset=" in src
    assert "/backtest?preset=" in src


def test_backtest_saves_tag_their_source():
    bl = _read("pages/BacktestLab.jsx")
    assert 'source: "backtest"' in bl
