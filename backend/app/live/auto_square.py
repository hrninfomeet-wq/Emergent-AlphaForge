"""Auto-square engine — L3.3 hard cap (≤10 minutes) for live test positions.

One job: a filled live position is NEVER left open past its deadline.

Architecture
------------
* Time is INJECTED everywhere as ISO strings — NO wall-clock calls inside logic.
  This makes the module fully deterministic under test.

* ``deadline_iso`` computes fill_time + horizon (clamped to SQUARE_HORIZON_SEC).

* ``is_due`` is fail-safe: if either timestamp cannot be parsed it returns True
  ("if we can't tell the time, square NOW rather than risk holding past the cap").

* ``build_sl_backstop_intent`` creates a protective SL-LMT exit for a LONG option.
  Asserts prc <= trgprc and prc > 0 — a violated assert is a programming error, not
  a runtime condition.

* ``square_position`` is the executor:
  - Cancels any unfilled/partial entry remainder first (working_norenordno).
  - Parses filled netqty; if 0 (entry never filled) reports squared=True via cancel.
  - Builds a marketable-limit exit in the CORRECT direction (long→SELL, short→BUY).
  - If lp is missing/non-finite/≤0 → returns {squared: False, reason: 'unpriced'}.
    The caller (engine) MUST halt on squared=False — it NEVER silently skips.
  - Retries a rejected exit ONCE (same qty/prc, fresh client_order_id).
  - On two consecutive rejects → {squared: False, failures: [...]}. NO raise.
  - NEVER applies fat-finger or throttle guards to an exit intent.
  - NEVER raises; always returns a dict.

Key safety properties (mirrors kill_switch.py's language):
- Fat-finger/throttle NEVER applied to exit intents.
- A position whose ref price is invalid is NOT silently dropped → squared=False.
- A rejected exit is retried ONCE, not silently ignored.
- If two consecutive rejects occur the engine is told (squared=False, failures=[...])
  so it can page the operator instead of leaving the position open indefinitely.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.live.broker_protocol import OrderIntent
from app.live.idempotency import new_client_order_id
from app.live.kill_switch import _parse_netqty

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Hard cap on how long a live test position may remain open after entry fill.
#: 600 seconds = 10 minutes. This value MUST NOT be exceeded; ``deadline_iso``
#: clamps any caller-supplied ``horizon_sec`` to this value.
SQUARE_HORIZON_SEC: int = 600


# ---------------------------------------------------------------------------
# 1. Time helpers — fully injected, no wall-clock
# ---------------------------------------------------------------------------

def deadline_iso(fill_time_iso: str, *, horizon_sec: int = SQUARE_HORIZON_SEC) -> str:
    """Return fill_time + horizon as an ISO 8601 string (UTC).

    Parameters
    ----------
    fill_time_iso:
        The time the entry fill was confirmed, as an ISO 8601 string.
    horizon_sec:
        How many seconds from fill_time before the position must be squared.
        Clamped to ``SQUARE_HORIZON_SEC`` (600 s) — callers MUST NOT rely on
        a horizon beyond the hard cap.  If a value > 600 is supplied it is
        silently clamped and documented in the docstring so the behaviour is
        deterministic rather than surprising.

    Returns
    -------
    ISO 8601 string of fill_time + min(horizon_sec, 600) seconds.

    Clamp rationale
    ---------------
    The hard cap is 10 minutes.  Accepting a larger horizon would let a caller
    accidentally extend it — clamping means the invariant "never open past 10 min"
    is enforced here, not left to the caller.
    """
    effective_horizon = min(int(horizon_sec), SQUARE_HORIZON_SEC)
    dt = datetime.fromisoformat(fill_time_iso)
    # Ensure timezone-awareness; if naive, treat as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    result = dt + timedelta(seconds=effective_horizon)
    return result.isoformat()


def is_due(deadline: str, now: str) -> bool:
    """Return True iff now >= deadline (position must be squared immediately).

    Fail-safe: if EITHER string cannot be parsed as ISO 8601, return True.
    Rationale: if we cannot determine the time relationship, the safe action is
    to square now rather than risk holding an open position past the hard cap.

    Parameters
    ----------
    deadline:
        ISO 8601 deadline string (from ``deadline_iso``).
    now:
        Current time as ISO 8601 string (injected by the engine — never
        derived from wall-clock inside this function).

    Returns
    -------
    True  — position is at or past its deadline, must be squared.
    False — position is still within its window.
    """
    try:
        dt_deadline = datetime.fromisoformat(deadline)
        dt_now = datetime.fromisoformat(now)
        # Normalise timezone-awareness so comparison never raises TypeError.
        if dt_deadline.tzinfo is None:
            dt_deadline = dt_deadline.replace(tzinfo=timezone.utc)
        if dt_now.tzinfo is None:
            dt_now = dt_now.replace(tzinfo=timezone.utc)
        return dt_now >= dt_deadline
    except Exception:
        # Any parse failure → fail-safe: square now.
        return True


# ---------------------------------------------------------------------------
# 2. SL-LMT backstop builder — protective exit for a LONG option leg
# ---------------------------------------------------------------------------

def build_sl_backstop_intent(
    *,
    exch: str,
    tsym: str,
    qty: int,
    stop_trigger: float,
    client_order_id: str,
) -> OrderIntent:
    """Build a protective SL-LMT intent for a LONG option leg.

    The order sells (trantype='S') qty lots at a trigger of stop_trigger with
    the limit price set slightly below the trigger so the order becomes marketable
    once triggered.

    Parameters
    ----------
    exch:             Exchange, e.g. "NFO" or "BFO".
    tsym:             Trading symbol.
    qty:              Number of units to sell (positive integer).
    stop_trigger:     Trigger price (trgprc) in ₹.  Must be > 0.
    client_order_id:  Caller-supplied idempotency key.

    Returns
    -------
    An ``OrderIntent`` with prctyp="SL-LMT", trantype="S".

    Raises
    ------
    AssertionError — if the resulting prc violates the protective-price invariant
        (prc <= trgprc and prc > 0).  This is a programming-error guard, not a
        runtime condition; the caller must supply a valid stop_trigger > 0.

    Price formula
    -------------
    trgprc = stop_trigger
    prc    = max(0.05, round(stop_trigger - 0.05, 2))

    The 0.05 floor ensures prc > 0 even for near-zero triggers.  The limit price
    is always <= the trigger price (protective invariant), so the order will be
    filled at trigger or worse — never above trigger (which would be buying, not
    protecting).
    """
    trgprc = stop_trigger
    prc = max(0.05, round(stop_trigger - 0.05, 2))

    # Protective invariant — a wrong direction here could grow the position.
    assert prc <= trgprc, (
        f"build_sl_backstop_intent: prc ({prc}) > trgprc ({trgprc}). "
        "The limit price must be ≤ trigger for a protective SL-LMT."
    )
    assert prc > 0, (
        f"build_sl_backstop_intent: prc ({prc}) must be > 0."
    )

    return OrderIntent(
        client_order_id=client_order_id,
        trantype="S",
        prctyp="SL-LMT",
        exch=exch,
        tsym=tsym,
        qty=qty,
        prc=prc,
        prd="I",
        ret="DAY",
        trgprc=trgprc,
        remarks=client_order_id,
    )


# ---------------------------------------------------------------------------
# 3. square_position — executor (MockNoren in tests, FlattradeClient in L3)
# ---------------------------------------------------------------------------

def _marketable_prc(ref: float, trantype: str, band_pct: float) -> float:
    """Compute a marketable-limit exit price using the clamped formula from kill_switch.

    SELL (long exit): ref * (1 - eff/100)
    BUY  (short exit): ref * (1 + eff/100)
    eff = abs(band_pct)

    Rounds to 2 decimal places, matching kill_switch.plan_squareoff.
    """
    eff = abs(band_pct)
    if trantype == "S":
        return round(ref * (1.0 - eff / 100.0), 2)
    else:  # "B"
        return round(ref * (1.0 + eff / 100.0), 2)


async def square_position(
    client: Any,
    position: Dict[str, Any],
    *,
    reason: str,
    band_pct: float = 1.0,
    uid: str = "",
    actid: str = "",
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Marketable-limit exit of a filled live position.

    This is the executor layer — it calls ``client.cancel_order`` and
    ``client.place_order`` directly, bypassing ALL throttle and fat-finger
    checks.  The engine MUST always be able to exit a position it holds.

    NEVER raises.  Returns a dict describing the outcome.

    Parameters
    ----------
    client:
        A BrokerClient instance (MockNoren in tests, FlattradeClient in L3).
    position:
        A dict describing the position to exit.  Expected keys:

        ``tsym``              — trading symbol (str)
        ``exch``              — exchange, e.g. "NFO" (str; defaults to "NFO")
        ``netqty``            — signed FILLED quantity (int or parseable string);
                                negative = short position.
        ``lp``                — last price / ref price (float or parseable string).
                                If missing, non-finite, or ≤ 0 → returns squared=False
                                with reason='unpriced'.  NEVER silently skipped.
        ``working_norenordno`` — (optional) norenordno of an unfilled/partial entry
                                 order to cancel BEFORE placing the exit.

    reason:
        A human-readable string describing why the exit is being triggered
        (e.g. "auto_square_deadline", "manual_override").
    band_pct:
        Marketable-limit cross buffer (%).  Clamped via abs(); default 1.0.
    uid, actid:
        Broker credentials for intent.to_jdata().
    now_iso:
        Injected timestamp for audit/logging (not used in time logic here;
        the caller drives the deadline check).

    Returns
    -------
    dict with keys:

        ``squared``      — True iff the position is considered closed.
        ``via``          — 'exit_order' | 'cancel' | None
        ``norenordno``   — broker order number of the exit order (if placed).
        ``reason``       — echoes the reason parameter (or 'unpriced' on bad lp).
        ``note``         — human-readable note (e.g. 'no position').
        ``failures``     — list of reject reasons from place_order attempts.

    squared=True conditions:
      • An exit order was placed and accepted by the broker (via='exit_order').
      • The entry was never filled (netqty == 0) and any working order was
        cancelled (via='cancel', note='no position').

    squared=False conditions (engine MUST halt / alert):
      • lp is missing/non-finite/≤0  (reason='unpriced').
      • netqty cannot be parsed       (reason='unpriced').
      • Both place_order attempts were rejected (failures=[...]).

    Direction invariant (CRITICAL):
      long position  (netqty > 0) → trantype = 'S' (SELL)
      short position (netqty < 0) → trantype = 'B' (BUY)
    A wrong direction would GROW the position — never acceptable.

    Retry policy:
      A rejected place_order is retried ONCE with the same qty/prc but a fresh
      client_order_id.  If the retry also fails, squared=False is returned with
      both reject reasons in `failures`.  No further retries; no raise.
    """
    failures: List[str] = []
    working_norenordno: Optional[str] = position.get("working_norenordno")

    # ------------------------------------------------------------------
    # Step 1 — cancel any unfilled/partial entry remainder
    # ------------------------------------------------------------------
    if working_norenordno:
        try:
            await client.cancel_order(working_norenordno)
            # We proceed regardless of cancel result — the position (filled
            # portion) still needs to be exited.  A cancel failure is
            # non-fatal here; the important exit is the flat order below.
        except Exception:
            pass  # never raise; proceed to exit

    # ------------------------------------------------------------------
    # Step 2 — parse filled netqty
    # ------------------------------------------------------------------
    raw_netqty = position.get("netqty", 0)
    netqty = _parse_netqty(raw_netqty)

    if netqty is None:
        # Unparseable netqty — we cannot safely exit without knowing qty.
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "unpriced",
            "note": f"netqty '{raw_netqty}' could not be parsed",
            "failures": [],
        }

    if netqty == 0:
        # Entry was never filled; the cancel in step 1 handled it.
        return {
            "squared": True,
            "via": "cancel",
            "norenordno": None,
            "reason": reason,
            "note": "no position",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 3 — validate ref price (lp)
    # ------------------------------------------------------------------
    tsym = position.get("tsym", "")
    exch = position.get("exch", "NFO")
    ref_raw = position.get("lp")

    try:
        ref = float(ref_raw)
        ref_ok = math.isfinite(ref) and ref > 0
    except (TypeError, ValueError):
        ref = float("nan")
        ref_ok = False

    if not ref_ok:
        # Bad ref price — NEVER silently skip; the engine must be alerted.
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "unpriced",
            "note": f"lp={ref_raw!r} is missing, non-finite, or ≤ 0",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 4 — build and place a marketable-limit exit
    # Direction MUST be correct:
    #   long  (netqty > 0) → SELL
    #   short (netqty < 0) → BUY
    # ------------------------------------------------------------------
    trantype = "S" if netqty > 0 else "B"
    qty = abs(netqty)
    prc = _marketable_prc(ref, trantype, band_pct)

    async def _try_place() -> "OrderResult":  # type: ignore[name-defined]  # noqa: F821
        cid = new_client_order_id()
        intent = OrderIntent(
            client_order_id=cid,
            trantype=trantype,
            prctyp="LMT",
            exch=exch,
            tsym=tsym,
            qty=qty,
            prc=prc,
            prd="I",
            ret="DAY",
            trgprc=None,
            remarks=cid,
        )
        return await client.place_order(intent)

    # First attempt
    try:
        result = await _try_place()
    except Exception as exc:
        failures.append(str(exc))
        result = None  # type: ignore[assignment]

    if result is not None and result.ok:
        return {
            "squared": True,
            "via": "exit_order",
            "norenordno": result.norenordno,
            "reason": reason,
            "note": None,
            "failures": [],
        }

    # Record first failure
    if result is not None and not result.ok:
        failures.append(result.rejreason or "place_order returned ok=False")

    # Retry once with a fresh client_order_id (same qty/prc)
    try:
        result2 = await _try_place()
    except Exception as exc2:
        failures.append(str(exc2))
        result2 = None  # type: ignore[assignment]

    if result2 is not None and result2.ok:
        return {
            "squared": True,
            "via": "exit_order",
            "norenordno": result2.norenordno,
            "reason": reason,
            "note": "placed on retry",
            "failures": failures,
        }

    # Retry also failed — record and return squared=False (never silently leave open)
    if result2 is not None and not result2.ok:
        failures.append(result2.rejreason or "retry place_order returned ok=False")

    return {
        "squared": False,
        "via": None,
        "norenordno": None,
        "reason": reason,
        "note": "exit rejected twice; operator intervention required",
        "failures": failures,
    }
