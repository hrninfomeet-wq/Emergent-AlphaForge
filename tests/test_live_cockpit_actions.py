"""Behavioral checks for consequential Live Cockpit actions.

Keep the async control flow in a plain frontend module and execute it through
Node. Grepping JSX cannot prove that a rejected stand-down request reaches the
operator; the previous empty ``catch`` is exactly that failure mode.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest


_LIB = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "lib", "liveCockpitActions.js"
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (
        f"import * as M from {url!r};\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def test_rejected_stand_down_is_reported_and_refreshes_state():
    result = _run_js(
        """
        const events = [];
        const error = new Error("mode write refused");
        const ok = await M.standDownManualExecution({
          setMode: async () => { events.push("set"); throw error; },
          onFailure: (caught) => events.push(`error:${caught.message}`),
          onSettled: () => events.push("refresh"),
        });
        return {ok, events};
        """
    )
    assert result == {
        "ok": False,
        "events": ["set", "error:mode write refused", "refresh"],
    }


def test_successful_stand_down_refreshes_without_an_error():
    result = _run_js(
        """
        const events = [];
        const ok = await M.standDownManualExecution({
          setMode: async (mode) => events.push(`set:${mode}`),
          onFailure: () => events.push("error"),
          onSettled: () => events.push("refresh"),
        });
        return {ok, events};
        """
    )
    assert result == {
        "ok": True,
        "events": ["set:LIVE_OFFLINE", "refresh"],
    }


def test_pre_cutoff_flattrade_token_is_not_rendered_connected():
    result = _run_js(
        """
        return M.brokerConnectionState({
          connected: true,
          expired: false,
          regenerate_after_6am: true,
        });
        """
    )
    assert result == {
        "connected": False,
        "expired": True,
        "stateLabel": "token expired",
    }


def test_resolved_indeterminate_place_response_is_still_unconfirmed():
    result = _run_js(
        """
        return M.placementOutcome({
          placed: false,
          indeterminate: true,
          reason: "ack_lost:ReadTimeout",
          retryable: false,
        });
        """
    )
    assert result == {
        "placed": False,
        "unconfirmed": True,
        "detail": "ack_lost:ReadTimeout",
    }
