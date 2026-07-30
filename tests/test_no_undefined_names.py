"""No module may reference a name it never binds. Enforced by pyflakes.

Third NameError shipped to the user in a week:
  * `is_premium_trigger_strategy` — six uses, zero imports (every optimizer run).
  * `contract_identity_key` — used in runtime.py with only its sibling imported.
  * `df_enriched` — a `sed -i .../g` added it to TWO call sites; it exists in one
    scope and not the other, so every ASYNC backtest died.

`tests/test_no_unbound_helper_names.py` was written after the first one, but it
only watches a hand-maintained SHARED_HELPERS set — so it could never have caught
`df_enriched`, an ordinary local. Widening that list after each incident is
chasing instances. pyflakes already solves the general problem, and it is already
a dependency.

Why the source-contract tests kept missing these: they assert that a STRING
appears in a file. A string can appear in a use, so a use with no binding
satisfies them. Only real analysis (or executing the path) distinguishes the two.

The baseline below is deliberately explicit and small. Every entry is a pyflakes
FALSE POSITIVE on a string annotation or a `Literal[...]` member — verified one by
one — not a tolerated defect. Nothing is allowlisted by wildcard, so a genuine new
undefined name in any of these files still fails.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app"))

#: (relative posix path, undefined name) pairs pyflakes reports that are NOT bugs.
#: Each verified 2026-07-30 by reading the site.
_BASELINE = {
    # `-> "OrderResult"` string annotations; the file even carries `# noqa: F821`.
    ("live/auto_square.py", "OrderResult"),
    # `now_utc: "datetime"` string annotation; the bodies import datetime locally.
    ("live/mode.py", "datetime"),
    # `Optional["ApprovalStore"]` / `-> "ApprovalStore"` string annotations.
    ("routers/live_broker.py", "ApprovalStore"),
    # pyflakes reads `_Literal["PAPER", "LIVE_OFFLINE", "LIVE_TEST"]` and
    # `_Literal["B"]` members as forward references. They are string VALUES.
    ("routers/live_broker.py", "PAPER"),
    ("routers/live_broker.py", "LIVE_OFFLINE"),
    ("routers/live_broker.py", "LIVE_TEST"),
    ("routers/live_broker.py", "B"),
}

_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):\d+: undefined name '(?P<name>[^']+)'$")


def _findings():
    out = subprocess.run([sys.executable, "-m", "pyflakes", _ROOT],
                         capture_output=True, text=True, timeout=600)
    hits = []
    for raw in (out.stdout or "").splitlines():
        m = _LINE.match(raw.strip())
        if not m:
            continue
        rel = os.path.relpath(m.group("path"), _ROOT).replace("\\", "/")
        hits.append((rel, m.group("name"), int(m.group("line")), raw.strip()))
    return hits


@pytest.fixture(scope="module")
def findings():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes not installed")
    return _findings()


def test_no_undefined_names_outside_the_verified_baseline(findings):
    """THE guard. A name used but never bound is a NameError waiting for a user."""
    offenders = [raw for rel, name, _ln, raw in findings
                 if (rel, name) not in _BASELINE]
    assert not offenders, (
        "undefined name(s) — these raise NameError when the line executes:\n  "
        + "\n  ".join(offenders))


def test_the_three_real_incidents_would_be_caught(findings):
    """Guard the guard: prove the detector fires on the shapes that shipped."""
    src = (
        "def f(strategy):\n"
        "    return is_premium_trigger_strategy(strategy)\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "probe.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(src)
        out = subprocess.run([sys.executable, "-m", "pyflakes", p],
                             capture_output=True, text=True, timeout=120)
    assert "undefined name" in (out.stdout or ""), "the detector would miss it"


def test_the_reported_async_backtest_bug_is_gone(findings):
    """`df_enriched` in run_backtest_job — 'Backtest failed: name df_enriched is
    not defined' on every run started from the Backtest Lab."""
    assert not [1 for rel, name, _l, _r in findings
                if rel == "routers/research.py" and name == "df_enriched"]


def test_the_live_status_logging_bug_is_gone(findings):
    """`logging.getLogger(...)` inside an `except` with no import — turned a
    swallowed lookup failure into a 500 on the live-status endpoint."""
    assert not [1 for rel, name, _l, _r in findings
                if rel == "routers/deployments.py" and name == "logging"]


def test_the_baseline_has_not_rotted(findings):
    """An allowlist that outlives its finding silently widens the guard."""
    seen = {(rel, name) for rel, name, _l, _r in findings}
    stale = sorted(_BASELINE - seen)
    assert not stale, f"remove these from _BASELINE, they no longer occur: {stale}"
