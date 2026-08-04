"""A lost ACK from place_order is INDETERMINATE, never "not placed".

`_transmit_and_arm` claims the intent (state=SUBMITTING, norenordno=None) and THEN
calls `client.place_order`. That call sat OUTSIDE the try that guards
mark_submitted/post_fill/arm, and `flattrade_client.place_order` converts only
non-200 HTTP into RuntimeError — an `httpx.ReadTimeout` on a 20s client, or a
JSONDecodeError on a truncated body, propagates.

The broker may well have ACCEPTED that order. Unhandled, the exception unwound
past auto_live to the evaluator's `log.exception` and left:
  - a REAL position with no guard registration, no OCO and no live_trades row,
  - caps reading zero (they count live_trades), so the NEXT BAR could place again,
  - the engine NOT halted.

Place-twice plus an unprotected live position, from an ordinary network timeout.
The safe reading of "I don't know" is "I may be long": halt and let a human look.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

from app.live.mock_noren import MockNoren  # noqa: E402
from tests.test_live_executor import _GOOD_LIMITS, FakeEngine  # noqa: E402
from tests.test_live_executor_deployed import _place_deployed, _run  # noqa: E402


class _TimeoutClient(MockNoren):
    """Accepts every gate, then the ACK is lost on the wire."""

    def __init__(self, exc: Exception, **kw):
        super().__init__(**kw)
        self._exc = exc
        self.place_calls = 0

    async def place_order(self, intent):
        self.place_calls += 1
        raise self._exc


def _timeout_client(exc: Exception) -> _TimeoutClient:
    return _TimeoutClient(exc, limits_data=_GOOD_LIMITS)


def test_lost_ack_does_not_escape_as_an_exception():
    import httpx
    client = _timeout_client(httpx.ReadTimeout("read timed out"))
    res = _run(_place_deployed(client=client, capped_lots=1, autoplace_armed=True))
    assert client.place_calls == 1, (
        "the test never reached the transmit — an earlier gate blocked, so this "
        "would pass for the wrong reason")
    assert isinstance(res, dict), "the exception escaped instead of being handled"
    assert res["placed"] is False


def test_lost_ack_is_flagged_indeterminate_not_a_clean_reject():
    import httpx
    client = _timeout_client(httpx.ReadTimeout("read timed out"))
    res = _run(_place_deployed(client=client, capped_lots=1, autoplace_armed=True))
    assert client.place_calls == 1
    assert res.get("indeterminate") is True, (
        "a lost ACK must NOT be reported as a clean not-placed — the broker may "
        "have accepted the order")
    assert "ack_lost" in res["reason"]


def test_lost_ack_halts_the_engine():
    """The halt is what stops the next bar placing a second order."""
    import httpx
    engine = FakeEngine()
    client = _timeout_client(httpx.ReadTimeout("read timed out"))
    _run(_place_deployed(client=client, engine=engine, capped_lots=1, autoplace_armed=True))
    assert engine.halt_calls, "engine was not halted after a lost ACK"
    assert any("ack_lost" in r for r in engine.halt_calls)


def test_a_json_decode_failure_is_also_indeterminate():
    """Not just timeouts — any non-RuntimeError from the client counts."""
    client = _timeout_client(ValueError("Expecting value: line 1 column 1"))
    res = _run(_place_deployed(client=client, capped_lots=1, autoplace_armed=True))
    assert res["placed"] is False and res.get("indeterminate") is True


def test_connection_error_before_send_still_halts():
    """We cannot distinguish 'never left the host' from 'ACK lost' — fail safe."""
    import httpx
    engine = FakeEngine()
    client = _timeout_client(httpx.ConnectError("connection refused"))
    res = _run(_place_deployed(client=client, engine=engine, capped_lots=1, autoplace_armed=True))
    assert res.get("indeterminate") is True
    assert engine.halt_calls


def test_a_clean_broker_reject_is_NOT_indeterminate():
    """No over-firing: an explicit reject is a known outcome, engine stays live."""
    engine = FakeEngine()
    client = MockNoren(limits_data=_GOOD_LIMITS)
    client.script_reject("RMS: margin shortfall")
    res = _run(_place_deployed(client=client, engine=engine, capped_lots=1, autoplace_armed=True))
    assert res["placed"] is False
    assert not res.get("indeterminate"), "a clean reject must not be indeterminate"
    assert engine.halt_calls == [], "a clean reject must not halt the engine"
