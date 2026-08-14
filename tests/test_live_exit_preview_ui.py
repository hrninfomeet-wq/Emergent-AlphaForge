"""The enable dialog must SHOW the exits it is about to arm (finding [17]).

On 2026-08-14 deployment 314fb7e8 went live carrying a 50% deep-default premium
stop, no premium target, and a trail the live path then discarded. The confirm step
listed Lots/signal, Max lots/day, Max concurrent, Daily loss cap, Strategy,
Instrument and Option selection — and nothing about how the position would ever be
closed.

Two halves are pinned here:

* the pure formatter `frontend/src/lib/exitPreview.js`, driven through node against
  the REAL payload the backend returns for that deployment — display logic tested by
  execution, never by grepping JSX;
* the router seam `_exit_preview_for`, which sources risk_hints from the most recent
  signal because hints outrank every deployment fallback (auto_live.py:198-213).

The card is ADVISORY. The standing project rule is that live-arming gates are
removed, not added, so nothing here may block ENABLE — pinned by
`test_the_preview_is_not_a_gate`.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib",
                    "exitPreview.js")
_PANEL = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                      "components", "live", "DeployToLivePanel.jsx")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

# The REAL /metrics exit_preview payload for 314fb7e8, captured from the running
# backend on 2026-08-14.
REAL_PREVIEW = {
    "premium_stop": {"pct": 50.0, "pts": None, "level": None,
                     "source": "deep_default", "is_deep_default": True},
    "premium_target": {"pct": None, "pts": None, "level": None,
                       "source": "none", "present": False},
    "trail": {"present": True, "unit": "pts",
              "breakeven": {"trigger": 10.0, "lock": 8.0}, "trailing": None,
              "summary": "breakeven at +10 locks +8"},
    "spot_exit": {"present": True, "target_pts": 161.35554902405147,
                  "stop_pts": 96.29802628652973},
    "time_stop_minutes": None, "eod_square_ist": "15:00",
    "reference_premium": None, "hints_observed": True,
    "notices": [
        {"id": "deep_default_stop", "severity": "warn", "message": "no stop configured"},
        {"id": "no_premium_target", "severity": "warn", "message": "no target"},
    ],
}


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (f"import * as M from {url!r};\n"
           f"const out = (() => {{ {body} }})();\n"
           "process.stdout.write(JSON.stringify(out));\n")
    p = subprocess.run(["node", "--input-type=module", "-e", src],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(f"node failed:\n{p.stderr}")
    return json.loads(p.stdout)


def _rows(preview=REAL_PREVIEW):
    got = _run_js(f"return M.exitRows({json.dumps(preview)});")
    return {r["key"]: r for r in got}, [r["key"] for r in got]


# --------------------------------------------------------------------------
# What the operator would have seen on 2026-08-14
# --------------------------------------------------------------------------

def test_the_deep_default_stop_is_shown_and_flagged():
    by_key, _ = _rows()
    assert by_key["stop"]["value"] == "50% of premium"
    assert by_key["stop"]["danger"] is True
    assert by_key["stop"]["note"] == "deep default — nothing configured"


def test_the_missing_target_is_shown_and_flagged():
    by_key, _ = _rows()
    assert by_key["target"]["value"] == "none"
    assert by_key["target"]["danger"] is True


def test_the_trail_and_spot_and_eod_rows_are_present():
    by_key, order = _rows()
    assert by_key["trail"]["value"] == "breakeven at +10 locks +8"
    assert by_key["spot"]["value"] == "target 161.3555 pts · stop 96.298 pts"
    assert by_key["eod"]["value"] == "15:00 IST"
    assert order == ["stop", "target", "trail", "spot", "eod"]


def test_both_warnings_reach_the_operator():
    got = _run_js(f"return M.exitNotices({json.dumps(REAL_PREVIEW)}).map(n => n.id);")
    assert got == ["deep_default_stop", "no_premium_target"]


# --------------------------------------------------------------------------
# A well-configured deployment must NOT be dressed up as dangerous
# --------------------------------------------------------------------------

def test_a_configured_deployment_raises_no_alarm():
    good = {
        "premium_stop": {"pct": 18, "pts": None, "level": 50.6,
                         "source": "deployment_pct", "is_deep_default": False},
        "premium_target": {"pct": 28, "pts": None, "level": 79.0,
                           "source": "deployment_pct", "present": True},
        "trail": {"present": False, "summary": "none"},
        "spot_exit": {"present": False}, "time_stop_minutes": 45,
        "eod_square_ist": "15:00", "notices": [],
    }
    by_key, order = _rows(good)
    assert by_key["stop"]["value"] == "18% of premium (₹50.6)"
    assert by_key["stop"]["danger"] is False
    assert by_key["target"]["danger"] is False
    assert by_key["time"]["value"] == "45 min"
    assert "spot" not in order


def test_pts_beats_pct_in_the_display_as_in_the_resolver():
    got = _run_js('return M.formatLeg({pts: 12, pct: 18, level: null});')
    assert got == "12 pts of premium"


def test_an_unknown_source_says_so_rather_than_inventing_one():
    assert _run_js('return M.sourceLabel("weird");') == "source unknown"


# --------------------------------------------------------------------------
# It renders inside an arm dialog — it must never throw
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["null", "undefined", "{}", '"x"', "42", "[]",
                                 '{"premium_stop":null}'])
def test_degenerate_payloads_never_throw(bad):
    got = _run_js(f"return {{rows: M.exitRows({bad}).length, "
                  f"notices: M.exitNotices({bad}).length}};")
    assert isinstance(got["rows"], int) and isinstance(got["notices"], int)


def test_a_missing_preview_draws_no_card():
    assert _run_js("return M.hasExitPreview(null);") is False
    assert _run_js(f"return M.hasExitPreview({json.dumps(REAL_PREVIEW)});") is True


# --------------------------------------------------------------------------
# The router seam
# --------------------------------------------------------------------------

class _FakeSignals:
    def __init__(self, doc=None, boom=False):
        self.doc, self.boom, self.query = doc, boom, None

    async def find_one(self, query, projection=None, sort=None):
        if self.boom:
            raise RuntimeError("mongo down")
        self.query = query
        return self.doc


class _FakeDB:
    def __init__(self, signals):
        self.signals = signals


def _preview_for(signals, deployment):
    from app.routers.deployments import _exit_preview_for
    return asyncio.run(_exit_preview_for(_FakeDB(signals), deployment))


_DEP = {"id": "dep1", "risk": {
    "exit_controls": {"enabled": True, "unit": "pts",
                      "breakeven": {"trigger": 10, "lock": 8}, "trailing": None}}}


def test_the_seam_uses_the_latest_signals_hints():
    """Hints outrank the deployment config, so the preview must consume them."""
    sig = {"risk_hints": {"stop_pct": 33, "target_pct": 66}}
    out = _preview_for(_FakeSignals(sig), _DEP)
    assert out["premium_stop"]["pct"] == 33
    assert out["premium_stop"]["source"] == "strategy_hint"
    assert out["premium_target"]["pct"] == 66
    assert out["hints_observed"] is True


def test_the_seam_queries_that_deployment_only():
    signals = _FakeSignals({"risk_hints": {}})
    _preview_for(signals, _DEP)
    assert signals.query["deployment_id"] == "dep1"


def test_no_signal_yet_is_reported_as_provisional():
    out = _preview_for(_FakeSignals(None), _DEP)
    assert out["hints_observed"] is False
    assert "hints_unobserved" in {n["id"] for n in out["notices"]}
    assert out["premium_stop"]["pct"] == 50.0


def test_a_db_failure_degrades_instead_of_breaking_the_dialog():
    """The dialog depends on /metrics; a signal-lookup failure must not 500 it."""
    out = _preview_for(_FakeSignals(boom=True), _DEP)
    assert out["premium_stop"]["pct"] == 50.0
    assert out["hints_observed"] is False


def test_the_preview_is_not_a_gate():
    """The enable route must never consult the preview. Live-arming gates are
    removed on this project, not added — the card informs, it does not block."""
    import ast
    import inspect

    from app.routers import deployments as mod

    src = inspect.getsource(mod)
    tree = ast.parse(src)
    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                   and n.name == "enable_deployment_live")
    called = {n.func.id for n in ast.walk(handler)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "describe_live_exits" not in called
    assert "_exit_preview_for" not in called


def test_the_panel_renders_the_card_without_gating_enable():
    """The ENABLE button's disabled condition must not mention the preview."""
    src = open(_PANEL, encoding="utf-8").read()
    assert 'data-testid="live-exit-preview"' in src
    disabled = src[src.index("disabled={confirmText"):]
    disabled = disabled[:disabled.index("}")]
    for token in ("exitPreview", "exitRows", "hasExitPreview"):
        assert token not in disabled, f"{token} gates ENABLE"
