"""Deploy blocks on infeasibility, warns on "outside the optimizer's search range".

THE DEFECT. `_validate_strategy_deployment_config` raised HTTP 400 whenever a param fell
outside its schema `min`/`max`. That range is the OPTIMIZER'S SEARCH SPACE — the very same
`parameter_schema` is what `_build_param_space` searches, and `param_overrides` exist to
WIDEN it — so the app routinely backtests, ranks and PROMOTES values outside it and then
refused to deploy them.

Measured impact when found: 4 of 12 saved presets were undeployable, including
  fibonacci_pullback NIFTY 154%   spot_target_pts 178.38 > declared max 150
  fibonacci_pullback SENSEX 222%  fib_entry_high 0.881 > 0.75, spot_target_pts 246.58 > 150
  atr_sigma_router  NIFTY 294%    stop_atr_mult 6.93 > declared max 6.0

That this is a search range and not a technical limit is not an inference:
  * a promoted confluence winner sat at spot_target_pts 285.7 against a declared max of 200,
  * `deployment_quality.py` states the rule outright — "Surface them as warnings - never
    block ... the app aids the user, never restricts", and
  * `atr_sigma_router` still carries a deliberate no-op 40-59 band whose comment says raising
    the minimum "made previously-saved presets undeployable (HTTP 400 ... from
    _validate_strategy_deployment_config)".

Verified over real HTTP against the running app before these were written.
"""
from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "backend", "app", "runtime.py")
DEPLOYMENTS = os.path.join(ROOT, "backend", "app", "routers", "deployments.py")
QUALITY = os.path.join(ROOT, "backend", "app", "deployment_quality.py")


@pytest.fixture(scope="module")
def runtime() -> str:
    with open(RUNTIME, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def router() -> str:
    with open(DEPLOYMENTS, encoding="utf-8") as fh:
        return fh.read()


def _validator(runtime: str) -> str:
    """Source of _validate_strategy_deployment_config, up to the next top-level def."""
    i = runtime.index("def _validate_strategy_deployment_config(")
    j = runtime.index(chr(10) + "async def _load_deployment_source(", i)
    return runtime[i:j]


# --- the range is advisory ---------------------------------------------------

def test_out_of_range_no_longer_raises(runtime):
    body = _validator(runtime)
    assert 'must be >= {spec[' not in body, "min violation must not raise"
    assert 'must be <= {spec[' not in body, "max violation must not raise"
    assert "range_findings.append(" in body


def test_out_of_range_is_reported_with_the_declared_bounds(runtime):
    """The operator has to see the value AND what it is outside of."""
    body = _validator(runtime)
    i = body.index("range_findings.append(")
    rec = body[i:i + 260]
    for field in ('"param"', '"value"', '"min"', '"max"'):
        assert field in rec


def test_collector_is_optional_so_revalidation_cannot_block(runtime, router):
    """Re-validation before a deployment goes ACTIVE passes no collector. If an
    out-of-range value blocked there, a deployment could be created with an
    acknowledgment and then never be allowed to activate."""
    assert "range_findings: Optional[List[Dict[str, Any]]] = None," in runtime
    assert "if range_findings is not None:" in _validator(runtime)
    i = router.index("_validate_strategy_deployment_config(")
    assert "range_findings" not in router[i:i + 300]


# --- genuine infeasibility still blocks --------------------------------------

def test_non_positive_still_hard_blocks_where_the_schema_requires_positive(runtime):
    """A zero or negative target/stop/period cannot produce a valid order. Verified
    over HTTP: spot_target_pts of 0 and -5 both returned 400 even WITH acknowledgment."""
    body = _validator(runtime)
    assert "if lo is not None and lo > 0 and value <= 0:" in body
    assert "must be greater than 0" in body
    assert "cannot produce a valid order" in body


def test_type_and_finiteness_blocks_are_untouched(runtime):
    body = _validator(runtime)
    assert "must be boolean" in body
    assert "must be an integer" in body
    assert "must be numeric" in body
    assert "must be finite" in body
    assert "must be non-empty text" in body


# --- it rides the EXISTING acknowledgment chain ------------------------------

def test_findings_become_warnings_on_the_existing_gate(router):
    """No new gate: they join quality["warnings"], which the wizard already renders
    and which acknowledged_warnings already controls."""
    assert "param_range_findings" in router
    assert 'quality.setdefault("warnings", []).append(' in router
    assert 'quality["acknowledgment_required"] = True' in router
    i = router.index("param_out_of_declared_range")
    block = router[i:i + 1400]
    # the wizard reads title/detail; deployment_quality's own rows use label/detail
    for key in ('"label"', '"title"', '"detail"', '"message"', '"value"'):
        assert key in block


def test_the_warning_says_it_is_a_search_range_not_a_broker_limit(router):
    i = router.index("param_out_of_declared_range")
    block = router[i:i + 1400]
    assert "optimizer's" in block and "search space" in block
    assert "not a broker or exchange limit" in block
    assert "executable" in block


def test_only_the_create_path_collects(router):
    """Exactly one collector, on creation — where the acknowledgment happens."""
    assert router.count("param_range_findings: List[Dict[str, Any]] = []") == 1
    assert router.count("range_findings=param_range_findings") == 1


def test_the_never_block_rule_is_still_documented(  ):
    """If this docstring ever changes, the rule these tests encode changed too."""
    with open(QUALITY, encoding="utf-8") as fh:
        head = fh.read(1200)
    assert "never block" in head
    assert "aids the user, never restricts" in head
