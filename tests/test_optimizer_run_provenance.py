"""A finished optimization must be auditable: which bounds ran, which incumbents were tried.

Two backend fields were being written and rendered nowhere:

  * `param_space` — the effective search range INCLUDING any override. On a real
    confluence_scalper job a leftover override had widened spot_target_pts from the
    strategy-declared max of 200 to 300, and nothing on screen or in the exported
    file said so. It was additionally stripped from the job export "to keep file
    size manageable", though measured it is 1,779 of 77,359 bytes (2%) while
    `trial_log` is the genuinely large field.

  * `incumbent_seeds` — written by the seeding work, read by nothing. Its `dropped`
    map is the operator-facing half: it names the dimensions the configured bounds
    made UNREACHABLE, which is precisely the situation where a search cannot beat a
    result you already hold.

Source-level guards; the rendered behaviour is checked in the browser walkthrough.
"""
from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMIZER_JSX = os.path.join(ROOT, "frontend", "src", "pages", "Optimizer.jsx")
OPT_EXPORTS = os.path.join(ROOT, "frontend", "src", "lib", "optExports.js")


@pytest.fixture(scope="module")
def jsx() -> str:
    with open(OPTIMIZER_JSX, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def exports() -> str:
    with open(OPT_EXPORTS, encoding="utf-8") as fh:
        return fh.read()


def test_provenance_panel_exists_and_is_rendered(jsx):
    assert "function RunProvenance({ job })" in jsx
    assert "<RunProvenance job={job} />" in jsx
    assert 'data-testid="opt-run-provenance"' in jsx


def test_it_reads_the_job_not_the_setup_form(jsx):
    """The setup form shows bounds you are ABOUT to use; this must show the bounds a
    FINISHED job actually used, so it has to read job.param_space."""
    i = jsx.index("function RunProvenance({ job })")
    body = jsx[i:i + 3000]
    assert "job?.param_space" in body
    assert "job?.incumbent_seeds" in body
    # must NOT reuse the live form state
    assert "config.param_overrides" not in body
    assert "boundsAudit" not in body


def test_seeded_incumbents_and_their_dropped_dimensions_are_shown(jsx):
    i = jsx.index("function RunProvenance({ job })")
    body = jsx[i:i + 3000]
    assert 'data-testid="opt-provenance-seeds"' in body
    assert "s.source" in body
    # the dropped map is the operator-facing part
    assert "s?.dropped" in body
    assert "not seeded" in body


def test_unreachable_bounds_are_called_out_in_the_summary(jsx):
    """If a known-good point could not be fully seeded, that must be visible without
    expanding the panel."""
    i = jsx.index("function RunProvenance({ job })")
    body = jsx[i:i + 3000]
    assert "anyDropped" in body
    assert "outside the bounds" in body
    assert "text-warning" in body


def test_panel_hides_itself_when_there_is_nothing_to_report(jsx):
    i = jsx.index("function RunProvenance({ job })")
    body = jsx[i:i + 3000]
    assert "if (bounded.length === 0 && seeds.length === 0) return null;" in body


def test_export_keeps_the_bounds_and_drops_the_big_field_instead(exports):
    """param_space is the audit trail; trial_log is the size problem."""
    assert "trial_log: undefined" in exports
    assert "param_space: undefined" not in exports
    assert "param_space` is KEPT" in exports or "param_space is KEPT" in exports
