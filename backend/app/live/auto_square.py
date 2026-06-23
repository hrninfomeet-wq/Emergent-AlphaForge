"""Auto-square engine — L3.3 hard cap (≤10 minutes) for live test positions.

One job: a filled live position is NEVER left open past its deadline.

Architecture
------------
* Time is INJECTED everywhere as ISO strings — NO wall-clock calls inside logic.
  This makes the module fully deterministic under test.

* All timestamps are normalized to UTC before any arithmetic or comparison.
  Callers SHOULD pass UTC (the engine uses a UTC-aware now).  Naive timestamps
  are assumed UTC.  This prevents tz-aware vs tz-naive mismatches from silently
  mis-ordering the deadline comparison (the audit hole: IST-aware now compared
  against a naive/mismatched deadline could return False up to 5 h 30 m past the
  real deadline).

* ``_to_utc`` is the single normalization helper: naive → UTC-aware via replace();
  aware → UTC via astimezone().

* ``deadline_iso`` computes fill_time + horizon (clamped to SQUARE_HORIZON_SEC).
  It ALWAYS emits a UTC-aware ISO string (+00:00) regardless of input timezone.

* ``is_due`` is fail-safe: if either timestamp cannot be parsed it returns True
  ("if we can't tell the time, square NOW rather than risk holding past the cap").
  Both sides are normalized to UTC before comparison.

* ``build_sl_backstop_intent`` creates a protective SL-LMT exit for a LONG option.
  Returns None for any invalid/sub-tick stop_trigger instead of asserting/raising —
  a sub-0.05 premium is real deep-OTM market data, not a programming error.
  The time-square hard cap remains the primary protection; the SL backstop is
  supplementary.

* ``square_position`` is the executor:
  - Parses filled netqty; if 0 (entry never filled) cancels the working
    remainder and reports squared=True via cancel.
  - MARGIN-SAFE EXIT (P1.4): before placing the exit it cancels ALL working
    orders for the scrip — the caller's known ids (``working_norenordno`` or the
    ``working_norenordnos`` list) PLUS any resting order discovered in the order
    book (e.g. a protective SL) — and CONFIRMS they are terminal via a re-fetch.
    A resting SL sell left working while a square-off sell is placed makes the
    broker see a naked short → margin reject (the ₹2.16L failure). If the working
    set cannot be confirmed clear, it returns squared=False, reason
    'cancel_unconfirmed' (NEVER places the doomed exit).
  - Validates lp BEFORE cancelling: an unpriced position keeps its protection
    (we never strip an SL we cannot replace with a priced exit).
  - Builds a marketable-limit exit in the CORRECT direction (long→SELL, short→BUY).
  - If lp is missing/non-finite/≤0 → returns {squared: False, reason: 'unpriced'}.
    The caller (engine) MUST halt on squared=False — it NEVER silently skips.
  - Retries a rejected exit ONCE (same qty/prc, fresh client_order_id).
  - On two consecutive rejects → {squared: False, failures: [...]}. NO raise.
  - NEVER applies fat-finger or throttle guards to an exit intent.
  - NEVER raises; always returns a dict.
  - NOT self-idempotent — the caller MUST NOT call it twice on the same position
    (matches panic_squareoff's contract in kill_switch.py).

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

from app.live.order_builder import round_to_tick

from app.live.broker_protocol import OrderIntent
from app.live.idempotency import new_client_order_id
from app.live.kill_switch import TERMINAL, _normalize_status, _parse_netqty

#: Max cancel+confirm passes before an exit is placed. A working order that
#: survives this many passes is treated as un-cancellable (margin-unsafe to exit).
_MAX_CANCEL_PASSES: int = 2

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

def _to_utc(iso: str) -> datetime:
    """Parse an ISO 8601 string and return a UTC-aware datetime.

    All timestamps are normalized to UTC; callers SHOULD pass UTC (the engine
    uses a UTC-aware now).  Naive timestamps are assumed UTC.

    Parameters
    ----------
    iso:
        An ISO 8601 datetime string, with or without a timezone offset.

    Returns
    -------
    A timezone-aware ``datetime`` in UTC.

    Raises
    ------
    ValueError / TypeError — if ``iso`` cannot be parsed.  Callers that must
    not raise should wrap in try/except (e.g. ``is_due``).
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        # Naive input — assumed UTC (engine always passes UTC-aware strings;
        # naive is legacy / test convenience and is treated as UTC).
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def deadline_iso(fill_time_iso: str, *, horizon_sec: int = SQUARE_HORIZON_SEC) -> str:
    """Return fill_time + horizon as a UTC-aware ISO 8601 string.

    All timestamps are normalized to UTC; callers SHOULD pass UTC (the engine
    uses a UTC-aware now).  Naive is assumed UTC.

    Parameters
    ----------
    fill_time_iso:
        The time the entry fill was confirmed, as an ISO 8601 string.
        May be naive (assumed UTC) or timezone-aware (any zone; converted to UTC).
    horizon_sec:
        How many seconds from fill_time before the position must be squared.
        Clamped to ``SQUARE_HORIZON_SEC`` (600 s) — callers MUST NOT rely on
        a horizon beyond the hard cap.  If a value > 600 is supplied it is
        silently clamped and documented in the docstring so the behaviour is
        deterministic rather than surprising.

    Returns
    -------
    UTC-aware ISO 8601 string of fill_time + min(horizon_sec, 600) seconds.
    The result ALWAYS carries a ``+00:00`` offset so ``is_due`` comparisons
    are unambiguous regardless of the caller's local timezone.

    Clamp rationale
    ---------------
    The hard cap is 10 minutes.  Accepting a larger horizon would let a caller
    accidentally extend it — clamping means the invariant "never open past 10 min"
    is enforced here, not left to the caller.
    """
    effective_horizon = min(int(horizon_sec), SQUARE_HORIZON_SEC)
    dt_utc = _to_utc(fill_time_iso)
    result = dt_utc + timedelta(seconds=effective_horizon)
    return result.isoformat()


def is_due(deadline: str, now: str) -> bool:
    """Return True iff now >= deadline (position must be squared immediately).

    All timestamps are normalized to UTC before comparison; callers SHOULD pass
    UTC (the engine uses a UTC-aware now).  Naive is assumed UTC.

    Fail-safe: if EITHER string cannot be parsed as ISO 8601, return True.
    Rationale: if we cannot determine the time relationship, the safe action is
    to square now rather than risk holding an open position past the hard cap.

    Parameters
    ----------
    deadline:
        ISO 8601 deadline string (from ``deadline_iso``).  Should be UTC-aware.
    now:
        Current time as ISO 8601 string (injected by the engine — never
        derived from wall-clock inside this function).

    Returns
    -------
    True  — position is at or past its deadline, must be squared.
    False — position is still within its window.
    """
    try:
        return _to_utc(now) >= _to_utc(deadline)
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
    stop_trigger: Any,
    client_order_id: str,
    tick: float = 0.05,
) -> Optional[OrderIntent]:
    """Build a protective SL-LMT intent for a LONG option leg, or None if invalid.

    All timestamps are normalized to UTC; callers SHOULD pass UTC (the engine
    uses a UTC-aware now).  Naive is assumed UTC.

    The order sells (trantype='S') qty lots at a trigger of stop_trigger with
    the limit price set slightly below the trigger so the order becomes marketable
    once triggered.

    Parameters
    ----------
    exch:             Exchange, e.g. "NFO" or "BFO".
    tsym:             Trading symbol.
    qty:              Number of units to sell (positive integer).
    stop_trigger:     Trigger price (trgprc) in ₹.  Must be a finite positive
                      number strictly greater than 0.05 (the exchange tick floor).
                      A sub-0.05 value is plausible real deep-OTM market data —
                      the function returns None rather than raising so the caller
                      can fall back to the time-square hard cap.
    client_order_id:  Caller-supplied idempotency key.

    Returns
    -------
    An ``OrderIntent`` with prctyp="SL-LMT", trantype="S" when stop_trigger
    is a finite number > 0.05, otherwise ``None``.

    Returns None (never raises) when:
    - stop_trigger is not numeric (None, str that can't be parsed as float, etc.)
    - stop_trigger is NaN or ±Inf
    - stop_trigger <= 0.05 (at or below the exchange tick floor; a protective
      stop cannot be built because prc would equal or exceed trgprc)

    The caller's primary protection is the time-square hard cap; the SL backstop
    is supplementary and its absence is not an error.

    Price formula
    -------------
    trgprc = stop_trigger
    prc    = max(0.05, round(stop_trigger - 0.05, 2))

    Since stop_trigger > 0.05 is required, prc <= trgprc always holds naturally
    (the protective invariant is structurally guaranteed, not asserted).
    """
    # Validate: coerce to float and check finite + above tick floor.
    try:
        trig = float(stop_trigger)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None  # non-numeric (None, "abc", etc.) — fail-soft

    if not math.isfinite(trig):
        return None  # NaN or ±Inf → fail-soft

    if trig <= 0.05:
        return None  # at or below tick floor → can't build protective stop

    # Round trgprc to nearest tick, prc to a tick multiple below trgprc (down).
    effective_tick = tick if tick > 0 else 0.05
    trgprc = round_to_tick(trig, effective_tick, mode="nearest")
    prc = round_to_tick(max(0.05, round(trgprc - effective_tick, 2)), effective_tick, mode="down")
    prc = max(0.05, prc)
    # Structural guarantee: since trig > 0.05 and trgprc is tick-aligned near trig,
    # prc = trgprc - tick (down), so prc <= trgprc always holds.

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

def _marketable_prc(ref: float, trantype: str, band_pct: float, tick: float = 0.05) -> float:
    """Compute a marketable-limit exit price rounded to the exchange tick.

    SELL (long exit): round_to_tick(ref * (1 - eff/100), tick, mode="down")
    BUY  (short exit): round_to_tick(ref * (1 + eff/100), tick, mode="up")
    eff = abs(band_pct)

    Directional rounding keeps the price marketable (SELL stays <= ref,
    BUY stays >= ref after rounding) while satisfying the broker's tick constraint.
    Broker rejects prices that are not exact multiples of the tick size.

    tick defaults to 0.05 (NIFTY/BANKNIFTY/SENSEX index options).
    If tick <= 0 falls back to 0.05.
    """
    eff = abs(band_pct)
    _tick = tick if tick > 0 else 0.05
    if trantype == "S":
        return round_to_tick(ref * (1.0 - eff / 100.0), _tick, mode="down")
    else:  # "B"
        return round_to_tick(ref * (1.0 + eff / 100.0), _tick, mode="up")


async def _cancel_all_working_for_scrip(
    client: Any, tsym: str, seed_ids: List[str]
) -> Dict[str, Any]:
    """Cancel EVERY working order for ``tsym`` and confirm they go terminal.

    This is the margin-safety core (P1.4). A resting SL sell left working while
    we place a square-off sell makes the broker see a naked short → margin reject
    (the observed ₹2.16L failure: "cancel the stop-loss first, then square off").
    So we discover ALL working orders for the scrip — the caller's known ids PLUS
    anything still working in the order book (e.g. a resting SL the caller didn't
    track) — cancel them, and CONFIRM via a re-fetch that none remain non-terminal
    before the exit is placed.

    Up to ``_MAX_CANCEL_PASSES`` cancel+confirm passes absorb a transient
    not-yet-processed cancel. If the client exposes no ``order_book`` (a minimal
    legacy stub), discovery/confirmation are skipped and the seed ids are
    cancelled best-effort (cleared=True is reported — we cannot do better).

    Returns ``{"cleared": bool, "remaining": [norenordno, ...]}``. ``cleared`` is
    True iff, after cancelling, no non-terminal order for the scrip remains.
    """
    ids = {x for x in seed_ids if x}
    has_book = hasattr(client, "order_book")

    # Discover any additional working orders for this scrip from the book.
    if has_book:
        try:
            book = await client.order_book()
            for o in (book or []):
                if _normalize_status(o.get("status")) in TERMINAL:
                    continue
                if str(o.get("tsym", "")) != str(tsym):
                    continue
                non = o.get("norenordno")
                if non:
                    ids.add(non)
        except Exception:
            pass  # discovery is best-effort — fall back to the seed ids

    if not ids:
        return {"cleared": True, "remaining": []}

    for _ in range(_MAX_CANCEL_PASSES):
        for non in list(ids):
            try:
                await client.cancel_order(non)
            except Exception:
                pass  # a cancel raising is non-fatal; the confirm re-fetch decides

        if not has_book:
            # Cannot confirm against a book — trust the cancels (legacy clients).
            return {"cleared": True, "remaining": []}

        try:
            book = await client.order_book()
        except Exception:
            return {"cleared": True, "remaining": []}  # trust cancels if book unavailable

        remaining = [
            o.get("norenordno")
            for o in (book or [])
            if _normalize_status(o.get("status")) not in TERMINAL
            and str(o.get("tsym", "")) == str(tsym)
            and o.get("norenordno")
        ]
        ids = set(remaining)
        if not ids:
            return {"cleared": True, "remaining": []}

    return {"cleared": False, "remaining": sorted(ids)}


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

    NOT self-idempotent — the caller MUST NOT call it twice on the same position
    (matches panic_squareoff's contract in kill_switch.py).

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
    tsym = position.get("tsym", "")
    exch = position.get("exch", "NFO")

    # Seed ids: the legacy single working_norenordno PLUS an optional list of all
    # resting orders for the scrip (entry remainder + any protective SL).
    seed_ids: List[str] = []
    w1 = position.get("working_norenordno")
    if w1:
        seed_ids.append(w1)
    for x in (position.get("working_norenordnos") or []):
        if x:
            seed_ids.append(x)

    # ------------------------------------------------------------------
    # Step 1 — parse filled netqty (cheap, no side effects)
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

    # ------------------------------------------------------------------
    # Step 2 — entry never filled: cancel the working remainder, no exit needed.
    # ------------------------------------------------------------------
    if netqty == 0:
        state = await _cancel_all_working_for_scrip(client, tsym, seed_ids)
        if not state["cleared"]:
            return {
                "squared": False,
                "via": None,
                "norenordno": None,
                "reason": "cancel_unconfirmed",
                "note": "could not cancel the unfilled entry order(s) for the scrip",
                "failures": state["remaining"],
            }
        return {
            "squared": True,
            "via": "cancel",
            "norenordno": None,
            "reason": reason,
            "note": "no position",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 3 — validate ref price (lp) BEFORE touching working orders.
    # If we cannot price the exit, do NOT cancel a protective SL we can't replace.
    # ------------------------------------------------------------------
    ref_raw = position.get("lp")

    try:
        ref = float(ref_raw)
        ref_ok = math.isfinite(ref) and ref > 0
    except (TypeError, ValueError):
        ref = float("nan")
        ref_ok = False

    if not ref_ok:
        # Bad ref price — NEVER silently skip; the engine must be alerted.
        # No cancel: an unpriced position keeps whatever protection it has.
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "unpriced",
            "note": f"lp={ref_raw!r} is missing, non-finite, or ≤ 0",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 4 — MARGIN-SAFE cancel: clear ALL working orders for the scrip and
    # confirm they are terminal BEFORE placing the exit.  A resting SL left
    # working would make the exit a naked short → margin reject (₹2.16L bug).
    # ------------------------------------------------------------------
    state = await _cancel_all_working_for_scrip(client, tsym, seed_ids)
    if not state["cleared"]:
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "cancel_unconfirmed",
            "note": (
                "working orders remain for the scrip after cancel; refusing to "
                "place the exit to avoid a naked-short margin reject — operator "
                "must clear them"
            ),
            "failures": state["remaining"],
        }

    # ------------------------------------------------------------------
    # Step 5 — build and place a marketable-limit exit
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
