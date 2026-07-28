"""Step 2: give the deployment the `premium_trigger` block it already promises.

`ai/capability.py` tells users — and tells the authoring LLM, which acts on it —
to "configure on the deployment's premium_trigger block" (`capability.py:174,438`).
**No such field existed anywhere**: not on `DeploymentCreateReq`, not on the
document `build_deployment_doc` writes. The only way to actually run a
premium-trigger strategy was to set `strategy_id = "premium_momentum"` verbatim
and bury the knobs in the generic `params` dict of that one plugin.

That made the promise unkeepable, and it is why Step 4 (a `config_block` on
`StrategySpec`) had to wait: an authored spec could emit a config with nowhere
to put it.

Design decisions pinned here:

* **The block is authoritative when present, params are the fallback.** Existing
  premium_momentum deployments carry their config in `params` and must keep
  working byte-identically, so absence of the block changes nothing.
* **An invalid block is refused at CREATION, loudly.** Today a malformed config
  passes deployment creation cleanly and only manifests as a silent no-op at
  evaluation time (`build_deployment_doc` never validates `params` against the
  strategy's schema at all). A deployment that can never trade should not be
  creatable.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402
from app.strategy_deployments import (  # noqa: E402
    build_deployment_doc,
    resolve_deployment_premium_trigger,
)

_VALID = {"reference_time": "09:31", "moneyness": "itm1", "side": "ce",
          "momentum_pct": 15.0, "stop_pct": 20.0, "lots": 2}

_SOURCE = {
    "name": "src", "id": "src", "strategy_id": "premium_momentum",
    "instrument": "NIFTY", "timeframe": "1m",
    "params": {"reference_time": "09:31", "moneyness": "itm1",
               "side": "first_to_trigger", "momentum_pct": 10.0},
    "config": {"strategy_id": "premium_momentum", "instrument": "NIFTY"},
}


def _doc(**kw):
    return build_deployment_doc(
        source_type="preset", source_doc=dict(_SOURCE), name="d", mode="paper", **kw)


# --------------------------------------------------------------------------- #
# the block exists and is stored
# --------------------------------------------------------------------------- #

def test_the_request_schema_finally_has_the_field_capability_promises():
    from app.schemas import DeploymentCreateReq
    assert "premium_trigger" in DeploymentCreateReq.model_fields, (
        "capability.py tells users to configure the deployment's premium_trigger "
        "block; that field must exist")


def test_a_valid_block_is_stored_on_the_deployment():
    doc = _doc(premium_trigger=dict(_VALID))
    assert doc["premium_trigger"]["momentum_pct"] == 15.0
    assert doc["premium_trigger"]["lots"] == 2


def test_no_block_means_no_key_rather_than_a_null():
    """Absence must be absence — a null would look like a configured-but-empty
    block to every downstream reader."""
    doc = _doc()
    assert not doc.get("premium_trigger")


# --------------------------------------------------------------------------- #
# invalid is refused AT CREATION
# --------------------------------------------------------------------------- #

def test_an_invalid_block_is_refused_at_creation_not_at_evaluation():
    """A deployment that can never trade should not be creatable. Today a
    malformed config passes creation and silently no-ops on the first bar."""
    broken = dict(_VALID)
    broken["momentum_pts"] = 9.0          # both pct and pts => invalid
    with pytest.raises(ValueError) as exc:
        _doc(premium_trigger=broken)
    assert "premium_trigger" in str(exc.value).lower()


def test_an_unknown_key_in_the_block_is_refused():
    """extra='forbid' must reach the user at creation — this is exactly what an
    LLM emits when a promise names a field that does not exist."""
    with pytest.raises(ValueError):
        _doc(premium_trigger={**_VALID, "expiry": "weekly"})


def test_a_block_with_no_entry_trigger_is_refused():
    with pytest.raises(ValueError):
        _doc(premium_trigger={"reference_time": "09:31", "moneyness": "itm1"})


# --------------------------------------------------------------------------- #
# resolution: block wins, params are the fallback
# --------------------------------------------------------------------------- #

def test_the_block_is_authoritative_when_present():
    doc = _doc(premium_trigger=dict(_VALID))
    cfg, source = resolve_deployment_premium_trigger(doc)
    assert isinstance(cfg, PremiumTriggerConfig)
    assert cfg.momentum_pct == 15.0, "the block must win over params"
    assert cfg.side == "ce"
    assert source == "premium_trigger"


def test_params_remain_the_fallback_so_existing_deployments_are_unchanged():
    """Every premium_momentum deployment created before this change stores its
    config in params. Those must keep resolving identically."""
    doc = _doc()
    cfg, source = resolve_deployment_premium_trigger(doc)
    assert cfg is not None
    assert cfg.momentum_pct == 10.0, "should have come from params"
    assert source == "params"


def test_an_ordinary_deployment_resolves_to_nothing():
    src = dict(_SOURCE)
    src["strategy_id"] = "confluence_scalper"
    src["params"] = {"ema_fast": 9, "signal_threshold": 62}
    doc = build_deployment_doc(source_type="preset", source_doc=src, name="d", mode="paper")
    cfg, source = resolve_deployment_premium_trigger(doc)
    assert cfg is None
    assert source is None


def test_resolution_reads_params_RAW_so_the_six_dropped_fields_survive():
    """The allow-list drop that Step 1 fixed for the backtest path must not
    reappear here: stop_pts / target_pts / trail_x / trail_y are absent from the
    plugin's parameter_schema."""
    src = dict(_SOURCE)
    src["params"] = {**_SOURCE["params"], "stop_pts": 7.5, "target_pts": 20.0,
                     "trail_x": 5.0, "trail_y": 5.0}
    doc = build_deployment_doc(source_type="preset", source_doc=src, name="d", mode="paper")
    cfg, _ = resolve_deployment_premium_trigger(doc)
    assert cfg.stop_pts == 7.5 and cfg.target_pts == 20.0
    assert cfg.trail_x == 5.0 and cfg.trail_y == 5.0


def test_a_malformed_params_config_resolves_to_none_not_a_wrong_config():
    src = dict(_SOURCE)
    src["params"] = {**_SOURCE["params"], "momentum_pts": 9.0}   # both set
    doc = build_deployment_doc(source_type="preset", source_doc=src, name="d", mode="paper")
    cfg, source = resolve_deployment_premium_trigger(doc)
    assert cfg is None
    assert source == "invalid"
