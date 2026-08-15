"""Deploy wizard: payload shape, gating, and backend parity.

ROOT CAUSE this covers. `LiveSignals.jsx` built its request inline with

    risk: form.exit_controls_enabled ? { … } : null,

whose own comment claimed the field was "otherwise omitted". `null` is not
omission: `DeploymentCreateReq.risk` is `Dict[str, Any] = Field(default_factory=
dict)` — NOT Optional — so Pydantic answered

    risk: Input should be a valid dictionary

and `getApiErrorMessage` rendered that verbatim. The visible effect was that the
OPTIONAL "Exit / Risk controls" panel had to be ticked for ANY deployment to
succeed, because ticking it was the only way to produce a payload the schema
accepted.

Reproduced against the running backend before the fix:

    risk: null -> 422 {"type":"dict_type","loc":["body","risk"], …}
    risk: {}   -> passes validation, reaches real business logic

The frontend half is driven through node (the established pattern from
`test_max_capital_deployed_card.py`); the parity tests feed the frontend's OWN
output into the real Pydantic model, so the two halves cannot drift apart
without a failure here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "deployPayload.js")
_ERR = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "apiError.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str, lib: str = _LIB):
    url = "file:///" + os.path.abspath(lib).replace("\\", "/")
    src = (f"import * as M from {url!r};\n"
           f"const out = (() => {{ {body} }})();\n"
           "process.stdout.write(JSON.stringify(out ?? null));\n")
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(f"node failed:\n{p.stderr}")
    return json.loads(p.stdout)


def _form(**kw):
    base = {
        "exit_controls_enabled": False,
        "risk_use_breakeven": False,
        "risk_use_trailing": False,
        "risk_use_daily_caps": False,
        "exit_controls_unit": "pct",
        "breakeven_trigger": "", "breakeven_lock": "",
        "trailing_activation": "", "trailing_distance": "",
        "daily_cap_loss": "", "daily_cap_target": "", "daily_cap_max_trades": "",
        "friction_costs_enabled": True,
        "acknowledged_warnings": False,
    }
    base.update(kw)
    return base


def _risk(form):
    return _run_js(f"return M.buildRiskPayload({json.dumps(form)});")


def _acks(form, quality=None):
    return _run_js(f"return M.deployAcknowledgements({json.dumps(form)}, "
                   f"{json.dumps(quality)});")


def _blockers(form, quality=None, source_ready=True):
    return _run_js(f"return M.deployBlockers({json.dumps(form)}, "
                   f"{json.dumps(quality)}, {{sourceReady: {str(source_ready).lower()}}});")


def _field_errors(form):
    return _run_js(f"return M.validateRiskFields({json.dumps(form)});")


# --------------------------------------------------------------------------
# 1. Controls DISABLED — the reported bug
# --------------------------------------------------------------------------

def test_disabled_controls_send_an_object_never_null():
    """THE fix. `null` was the bug; `{}` is the valid 'nothing configured'."""
    assert _risk(_form()) == {}


def test_disabled_controls_ignore_any_stale_field_values():
    """Values typed then toggled off must not leak into the payload."""
    f = _form(breakeven_trigger="0.3", daily_cap_loss="5000",
              risk_use_breakeven=True, risk_use_daily_caps=True)
    assert _risk(f) == {}


def test_disabled_controls_require_an_explicit_acknowledgement():
    ids = [a["id"] for a in _acks(_form())]
    assert "no_exit_controls" in ids


def test_missing_acknowledgement_blocks_deploy():
    blockers = _blockers(_form())
    assert any("Acknowledge" in b for b in blockers)


def test_acknowledged_disabled_controls_can_deploy():
    assert _blockers(_form(acknowledged_warnings=True)) == []


# --------------------------------------------------------------------------
# 2. Controls ENABLED with valid settings
# --------------------------------------------------------------------------

def test_enabled_breakeven_only_emits_breakeven():
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_trigger="0.3", breakeven_lock="0.05")
    r = _risk(f)
    assert r["exit_controls"]["enabled"] is True
    assert r["exit_controls"]["breakeven"] == {"trigger": 0.3, "lock": 0.05}
    assert r["exit_controls"]["trailing"] is None
    assert "daily_caps" not in r


def test_enabled_trailing_only_emits_trailing():
    f = _form(exit_controls_enabled=True, risk_use_trailing=True,
              trailing_activation="0.4", trailing_distance="0.25")
    r = _risk(f)
    assert r["exit_controls"]["trailing"] == {"activation": 0.4, "distance": 0.25}
    assert r["exit_controls"]["breakeven"] is None


def test_both_controls_compose():
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              risk_use_trailing=True, breakeven_trigger="0.3",
              trailing_activation="0.4", trailing_distance="0.25")
    r = _risk(f)["exit_controls"]
    assert r["breakeven"]["trigger"] == 0.3 and r["trailing"]["distance"] == 0.25


def test_daily_caps_are_independent_of_exit_controls():
    f = _form(exit_controls_enabled=True, risk_use_daily_caps=True,
              daily_cap_loss="5000", daily_cap_max_trades="3")
    r = _risk(f)
    assert r["daily_caps"] == {"loss": 5000, "target": None, "max_trades": 3}
    assert "exit_controls" not in r, "an unused exit_controls block must not be posted"


def test_an_enabled_panel_with_nothing_selected_still_sends_an_object():
    """`enabled: true` with every field null arms NOTHING — the live guard would
    report it as 'no_trail'. Posting it would persist a control that silently
    does nothing, so it must collapse to {}."""
    f = _form(exit_controls_enabled=True)
    assert _risk(f) == {}


def test_an_empty_enabled_panel_still_needs_the_acknowledgement():
    ids = [a["id"] for a in _acks(_form(exit_controls_enabled=True))]
    assert "no_exit_controls" in ids


def test_configured_controls_need_no_risk_acknowledgement():
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_trigger="0.3")
    ids = [a["id"] for a in _acks(f)]
    assert "no_exit_controls" not in ids
    assert _blockers(f) == []


# --------------------------------------------------------------------------
# 3. Invalid risk-control values
# --------------------------------------------------------------------------

def test_a_percentage_typed_as_a_percent_is_caught_before_the_round_trip():
    """unit=pct is a FRACTION: 25 means 2500%. The backend rejects it too, but
    telling the user here is faster and names the fix."""
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_trigger="25")
    err = _field_errors(f)["breakeven_trigger"]
    assert "0.25" in err


def test_negative_values_are_rejected():
    f = _form(exit_controls_enabled=True, risk_use_trailing=True,
              trailing_distance="-1")
    assert "negative" in _field_errors(f)["trailing_distance"].lower()


def test_a_lone_breakeven_lock_without_a_trigger_is_rejected():
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_lock="0.05")
    assert "trigger" in _field_errors(f)["breakeven_trigger"].lower()


def test_a_trailing_activation_without_a_distance_is_rejected():
    f = _form(exit_controls_enabled=True, risk_use_trailing=True,
              trailing_activation="0.4")
    assert "distance" in _field_errors(f)["trailing_distance"].lower()


def test_a_rupee_cap_without_costs_is_explained_not_silently_disabled():
    """The backend refuses this; the wizard used to just grey the input out, with
    the cause two steps back on another screen."""
    f = _form(exit_controls_enabled=True, risk_use_daily_caps=True,
              daily_cap_loss="5000", friction_costs_enabled=False)
    assert "step 2" in _field_errors(f)["daily_cap_loss"]


def test_invalid_fields_block_deploy_even_when_acknowledged():
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_trigger="25", acknowledged_warnings=True)
    assert any("Fix 1 exit/risk field" in b for b in _blockers(f))


def test_zero_is_rejected_with_a_pointer_to_blank():
    f = _form(exit_controls_enabled=True, risk_use_trailing=True,
              trailing_distance="0")
    assert "blank" in _field_errors(f)["trailing_distance"].lower()


def test_a_breakeven_lock_of_zero_is_LEGAL():
    """0 means 'lock at exact entry' — a real, useful setting."""
    f = _form(exit_controls_enabled=True, risk_use_breakeven=True,
              breakeven_trigger="0.3", breakeven_lock="0")
    assert "breakeven_lock" not in _field_errors(f)


# --------------------------------------------------------------------------
# 4. Frontend payload / backend schema PARITY
# --------------------------------------------------------------------------

@pytest.mark.parametrize("form_kwargs", [
    {},
    {"exit_controls_enabled": True},
    {"exit_controls_enabled": True, "risk_use_breakeven": True,
     "breakeven_trigger": "0.3", "breakeven_lock": "0.05"},
    {"exit_controls_enabled": True, "risk_use_trailing": True,
     "trailing_activation": "0.4", "trailing_distance": "0.25"},
    {"exit_controls_enabled": True, "risk_use_daily_caps": True,
     "daily_cap_loss": "5000", "daily_cap_max_trades": "3"},
    {"exit_controls_enabled": True, "risk_use_breakeven": True,
     "risk_use_trailing": True, "risk_use_daily_caps": True,
     "breakeven_trigger": "0.3", "trailing_activation": "0.4",
     "trailing_distance": "0.25", "daily_cap_target": "9000"},
])
def test_every_frontend_risk_payload_satisfies_the_backend_schema(form_kwargs):
    """The anti-drift test: the frontend's OWN output is fed to real Pydantic."""
    from app.schemas import DeploymentCreateReq
    risk = _risk(_form(**form_kwargs))
    model = DeploymentCreateReq(name="n", source_type="preset", source_id="x",
                                risk=risk)
    assert isinstance(model.risk, dict)
    assert model.risk == risk


def test_the_backend_normalises_a_null_risk_rather_than_422ing():
    """Defence in depth for any other client (or an older cached bundle)."""
    from app.schemas import DeploymentCreateReq
    m = DeploymentCreateReq(name="n", source_type="preset", source_id="x", risk=None)
    assert m.risk == {}


def test_an_omitted_risk_still_defaults_to_empty():
    from app.schemas import DeploymentCreateReq
    assert DeploymentCreateReq(name="n", source_type="preset",
                               source_id="x").risk == {}


def test_a_non_dict_risk_is_still_rejected():
    """Normalising null must not turn the field into 'anything goes'."""
    from pydantic import ValidationError

    from app.schemas import DeploymentCreateReq
    for bad in ("oops", 5, [1, 2]):
        with pytest.raises(ValidationError):
            DeploymentCreateReq(name="n", source_type="preset", source_id="x",
                                risk=bad)


def test_enabled_payloads_pass_the_backends_own_risk_validator():
    """Parity with `exit_controls.validate_exit_risk_config`, the function the
    create route actually calls."""
    from app.exit_controls import validate_exit_risk_config
    risk = _risk(_form(exit_controls_enabled=True, risk_use_breakeven=True,
                       risk_use_trailing=True, risk_use_daily_caps=True,
                       breakeven_trigger="0.3", breakeven_lock="0.05",
                       trailing_activation="0.4", trailing_distance="0.25",
                       daily_cap_loss="5000", daily_cap_max_trades="3"))
    errs = validate_exit_risk_config(risk.get("exit_controls"),
                                     risk.get("daily_caps"),
                                     costs_on=True, option_exec_on=True)
    assert errs == []


def test_a_value_the_frontend_rejects_is_also_rejected_by_the_backend():
    """Neither layer may be more permissive than the other."""
    from app.exit_controls import validate_exit_risk_config
    bad = {"exit_controls": {"enabled": True, "unit": "pct",
                             "breakeven": {"trigger": 25, "lock": None},
                             "trailing": None}}
    errs = validate_exit_risk_config(bad["exit_controls"], None,
                                     costs_on=True, option_exec_on=True)
    assert errs, "the backend accepted a fraction of 25"


# --------------------------------------------------------------------------
# 5. Raw schema errors must not reach the operator
# --------------------------------------------------------------------------

def _err_js(body):
    return _run_js(body, lib=_ERR)


def test_the_original_pydantic_error_is_translated():
    item = {"type": "dict_type", "loc": ["body", "risk"],
            "msg": "Input should be a valid dictionary"}
    msg = _err_js("return M.getDeploymentErrorMessage({response:{data:{detail:["
                  + json.dumps(item) + "]}}});")
    assert "Exit / Risk controls" in msg
    assert "Input should be a valid dictionary" not in msg
    assert "pydantic" not in msg.lower()


def test_a_nested_field_path_is_named():
    item = {"type": "float_parsing", "loc": ["body", "risk", "daily_caps", "loss"],
            "msg": "Input should be a valid number"}
    msg = _err_js("return M.getDeploymentErrorMessage({response:{data:{detail:["
                  + json.dumps(item) + "]}}});")
    assert "Exit / Risk controls" in msg and "daily_caps.loss" in msg


def test_an_actionable_backend_400_is_passed_through_unchanged():
    """`validate_exit_risk_config` already writes good sentences — don't mangle."""
    detail = "breakeven.trigger must be in (0, 1) for unit=pct (a fraction, e.g. 0.30)."
    msg = _err_js("return M.getDeploymentErrorMessage({response:{data:{detail:"
                  + json.dumps(detail) + "}}});")
    assert msg == detail


def test_an_unmapped_validation_error_degrades_to_the_generic_formatter():
    item = {"type": "some_future_type", "loc": ["body", "brand_new_field"],
            "msg": "nope"}
    msg = _err_js("return M.getDeploymentErrorMessage({response:{data:{detail:["
                  + json.dumps(item) + "]}}});")
    assert "brand_new_field" in msg


def test_a_transport_failure_still_yields_a_sentence():
    msg = _err_js('return M.getDeploymentErrorMessage({message:"Network Error"});')
    assert msg == "Network Error"
