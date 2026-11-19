"""Offline entrypoint for actual saved-run policy and wizard-payload behavior.

Node executes pure source modules and the wizard's request-object expression;
no React app, HTTP client, broker, application runtime or DB is started.
"""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_backtest_execution_transfer_and_async_selection_contract():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/js/backtestDeploySource.test.mjs")],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
