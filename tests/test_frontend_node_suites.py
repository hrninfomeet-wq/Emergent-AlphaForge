"""Drives the node-based frontend test assets in ``tests/frontend/``.

``liveStopState.test.mjs`` was committed in ed737d1 with 113 lines of assertions
about the kill switch's two-stop reset logic — and nothing ever executed it. There
is no pytest config, no conftest, and ``frontend/package.json`` only defines
start/build/test (craco), so the file was dead weight guarding a live-safety path:
`can_trade()` has two independent stops and the operator only had a reset for one
([[two-gates-halt-vs-latch]] is the corresponding trap).

This wrapper makes those assertions real, using the same subprocess pattern as
``test_live_enable_consent_ui.py``. Skips (rather than fails) when node is absent,
so a container run without node still collects.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_MJS = ROOT / "tests" / "frontend" / "liveStopState.test.mjs"


def test_live_stop_state_node_suite_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — cannot run the node:test suite")
    assert _MJS.exists(), f"missing {_MJS}"
    proc = subprocess.run(
        [node, "--test", str(_MJS)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        f"node --test failed (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
    )


def test_the_suite_actually_contains_assertions():
    """Floor against the wrapper silently passing an empty/skipped file — the exact
    failure mode that let this suite sit dormant for weeks."""
    src = _MJS.read_text(encoding="utf-8")
    assert src.count("assert") >= 10, "the node suite has almost no assertions"
    assert "liveStopState" in src
