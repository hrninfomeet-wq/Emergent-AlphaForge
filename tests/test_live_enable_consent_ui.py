"""The live-enable consent gate in DeployToLivePanel (2026-09-08).

The typed-"ENABLE" confirm was removed at the user's request. That is only safe
because a consent checkbox now renders on BOTH evidence paths: the older red
"I approve unvalidated real-money trading" box appeared ONLY when forward
validation FAILED, so removing the typed gate without adding the validated-path
checkbox would have let a passing deployment go live on a bare button click.

These assertions run against the JSX ABSTRACT SYNTAX TREE, not a source grep.
The property that matters — "is the checkbox rendered unconditionally?" — is
structural, and a grep cannot answer it: `{unvalidated && <label><input/></label>}`
and a plain `<label><input/></label>` differ only in their enclosing expression.
The probe (tests/frontend/consent_probe.cjs) parses with the frontend's own
@babel/parser and reports each checkbox's guard chain.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PANEL = ROOT / "frontend" / "src" / "components" / "live" / "DeployToLivePanel.jsx"
_PROBE = ROOT / "tests" / "frontend" / "consent_probe.cjs"
_PARSER = ROOT / "frontend" / "node_modules" / "@babel" / "parser"


@pytest.fixture(scope="module")
def panel_ast() -> dict:
    """Parse the panel via node + the frontend's @babel/parser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — cannot AST-check the JSX")
    if not _PARSER.exists():
        pytest.skip("frontend/node_modules/@babel/parser missing — run npm install")
    proc = subprocess.run(
        [node, str(_PROBE), str(_PANEL)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_exactly_one_consent_checkbox(panel_ast):
    """Two checkboxes would mean two consents, and an operator ticking the wrong
    one would see an enabled button that sends the wrong evidence claim."""
    boxes = panel_ast["consentCheckboxes"]
    assert len(boxes) == 1, f"expected one consent checkbox, found {boxes}"
    assert boxes[0]["testid"] == "deploy-to-live-consent"


def test_the_consent_checkbox_is_not_gated_on_the_evidence_state(panel_ast):
    """THE regression this file exists for.

    The checkbox may be guarded by the dialog step (`confirmOpen`) — that is just
    which view of the dialog is showing. It must NOT be guarded by
    `unvalidated` / `promotionReady` / `forwardValidation`, or a deployment that
    PASSES forward validation renders no consent control at all and goes live on
    a bare click.
    """
    guards = panel_ast["consentCheckboxes"][0]["guards"]
    for guard in guards:
        for banned in ("unvalidated", "promotionReady", "forwardValidation", "promotion_allowed"):
            assert banned not in guard, (
                f"consent checkbox is conditional on evidence state ({guard!r}) — a "
                "validated deployment would have no consent control"
            )


def test_the_submit_is_gated_by_consent(panel_ast):
    disabled = panel_ast["submitDisabledSource"]
    assert disabled is not None, "submit button has no disabled expression"
    assert "consent" in disabled, f"consent does not gate ENABLE: {disabled!r}"


def test_the_typed_enable_confirm_is_gone(panel_ast):
    """The user removed it deliberately; a silent re-introduction would be a
    behaviour change nobody asked for."""
    assert panel_ast["typedConfirmPresent"] is False
    # AST-level: only STRING LITERALS in executable code count. Prose in a comment
    # explaining that the gate was removed must not fail this test — an earlier
    # draft grepped the raw file and tripped on its own explanatory comment.
    assert panel_ast["enableLiterals"] == [], (
        f'an "ENABLE" string literal is back in the panel: {panel_ast["enableLiterals"]}'
    )


def test_accept_unvalidated_live_still_requires_the_unvalidated_path(panel_ast):
    """BACKEND CONTRACT. The consent state is now shared by both paths, so the
    override flag must stay conjoined with `unvalidated`. Sending bare consent
    would claim an evidence override on every enable — including validated ones,
    which have no failed checks to override."""
    expr = panel_ast["acceptUnvalidatedSource"]
    assert expr is not None, "accept_unvalidated_live is no longer sent"
    assert "unvalidated" in expr and "consent" in expr, expr
    assert "&&" in expr, f"expected a conjunction, got {expr!r}"


def test_both_evidence_paths_have_consent_copy():
    """The validated path needs its own wording — reusing the red 'unvalidated'
    text on a deployment that passed would be simply false."""
    src = _PANEL.read_text(encoding="utf-8")
    assert 'data-testid={unvalidated ? "accept-unvalidated-live" : "accept-live"}' in src, (
        "the consent label no longer distinguishes the validated path"
    )
