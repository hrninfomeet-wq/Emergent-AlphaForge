"""Cross-check the UI's conservative override audit against backend space rules."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.indicator_param_catalog import INDICATOR_PARAM_CATALOG  # noqa: E402
from app.optimizer import NON_ALPHA_PARAM_NAMES, _build_param_space, resolve_indicator_period_search  # noqa: E402
from app.strategies.plugins.premium_momentum import PremiumMomentum  # noqa: E402
from app.premium_trigger_dispatch import is_premium_trigger_strategy  # noqa: E402

_LIB = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "lib", "optimizerBoundAudit.js",
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute the real frontend module",
)


def _audit(schema, overrides, *, expand=False, catalog=None):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (
        f"import * as M from {url!r};\n"
        "const out = M.auditOptimizerBoundOverrides({"
        f"parameterSchema: {json.dumps(schema)}, "
        f"indicatorCatalog: {json.dumps(catalog or INDICATOR_PARAM_CATALOG)}, "
        f"overrides: {json.dumps(overrides)}, expandIndicatorPeriods: {str(expand).lower()}"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", src],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


SCHEMA = {
    "lookback": {"type": "int", "min": 5, "max": 40, "default": 14},
    "threshold": {"type": "float", "default": 1.5},
    "enabled": {"type": "bool", "default": True},
    "locked": {"type": "int", "min": 1, "max": 9, "default": 4, "fixed": 4},
    "mode": {"type": "str", "default": "fast"},
}


def test_saved_numeric_bounds_are_not_misreported_as_effective():
    overrides = {"lookback": {"min": 8, "max": 30}}
    audit = _audit(SCHEMA, overrides)
    backend_space = _build_param_space(SCHEMA, overrides)
    assert backend_space["lookback"]["min"] == 8
    assert backend_space["lookback"]["max"] == 30
    assert "fixed" not in backend_space["lookback"]
    assert audit["savedBounds"] == ["lookback"]
    assert "activeBounds" not in audit


def test_fixed_precedence_and_known_inert_bounds_match_backend():
    overrides = {
        "locked": {"min": 2, "max": 8},
        "enabled": {"min": False, "max": True},
        "threshold": {"min": 2.0},
        "mode": {"fixed": "slow"},
        "lookback": {"min": 8, "max": 30, "fixed": 12},
    }
    audit = _audit(SCHEMA, overrides)
    backend_space = _build_param_space(SCHEMA, overrides)
    assert backend_space["locked"]["fixed"] == 4
    assert backend_space["enabled"]["default"] is True
    assert backend_space["threshold"]["fixed"] == 1.5
    assert "mode" not in backend_space
    assert backend_space["lookback"]["fixed"] == 12
    assert audit["activeFixed"] == ["lookback"]
    reasons = {item["name"]: item["reason"] for item in audit["ignored"]}
    assert "strategy schema fixes" in reasons["locked"]
    assert "boolean search" in reasons["enabled"]
    assert "incomplete effective range" in reasons["threshold"]
    assert "not searched" in reasons["mode"]
    assert "fixed value takes precedence" in reasons["lookback"]


def test_non_alpha_default_pin_is_called_saved_not_active():
    name = sorted(NON_ALPHA_PARAM_NAMES)[0]
    schema = {name: {"type": "int", "min": 1, "max": 5, "default": 2}}
    overrides = {name: {"max": 4}}
    audit = _audit(schema, overrides)
    backend_space = _build_param_space(schema, overrides)
    assert backend_space[name]["fixed"] == 2
    assert audit["savedBounds"] == [name]
    assert name not in audit["activeFixed"]
    assert name not in audit["ignored"]


def test_catalog_override_is_separate_and_backend_injects_only_when_requested():
    catalog_name = next(iter(INDICATOR_PARAM_CATALOG))
    overrides = {catalog_name: {"min": 4, "max": 18}}
    off = _build_param_space(SCHEMA, overrides, include_indicator_periods=False)
    on = _build_param_space(SCHEMA, overrides, include_indicator_periods=True)
    assert catalog_name not in off
    assert catalog_name in on
    assert _audit(SCHEMA, overrides, expand=False)["savedCatalog"] == [catalog_name]
    assert _audit(SCHEMA, overrides, expand=True)["savedCatalog"] == [catalog_name]
    assert catalog_name not in _audit(SCHEMA, overrides)["foreign"]


def test_premium_native_is_forced_off_by_backend_and_catalog_remains_unclaimed():
    premium = is_premium_trigger_strategy(PremiumMomentum())
    assert premium is True
    assert resolve_indicator_period_search(True, premium_native=premium) is False
    catalog_name = next(iter(INDICATOR_PARAM_CATALOG))
    audit = _audit(SCHEMA, {catalog_name: {"max": 12}}, expand=True)
    assert audit["savedCatalog"] == [catalog_name]
    assert catalog_name not in audit["activeFixed"]
    assert catalog_name not in audit["ignored"]


def test_truly_foreign_override_is_absent_from_both_backend_sources():
    overrides = {"retired_param": {"max": 12}}
    audit = _audit(SCHEMA, overrides)
    assert "retired_param" not in _build_param_space(SCHEMA, overrides)
    assert audit["foreign"] == ["retired_param"]
