"""Margin pre-check verdict — pure, stateless, fail-closed (L3.2).

Blocks a live order whose 1-lot premium would exceed available account cash.
Parses the Noren ``limits()`` response defensively (Noren sends ``cash`` as a
STRING e.g. "16552.95") and fails CLOSED on any unreadable / garbage / non-finite
input.

Design contract (mirrors safety.py / order_builder.py style):
- NEVER import DB, network, or I/O modules.
- Return values are plain Python dicts / tuples / floats — no Pydantic.
- Verdict dict shape matches order_builder.py:
      {"check": str, "ok": bool, "detail": str}
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _finite_positive(x: object) -> bool:
    """Return True iff x is a finite positive number (int or float, not bool)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
        and x > 0
    )


# ---------------------------------------------------------------------------
# 1. Required premium computation
# ---------------------------------------------------------------------------

def required_premium(
    ref_ltp: Any,
    lot_size: Any,
    *,
    buffer: float = 1.05,
) -> Optional[float]:
    """Compute the premium cash needed to buy one lot at ``ref_ltp``.

    Returns ``ref_ltp * lot_size * buffer``, or ``None`` if either input is not
    finite-positive.  ``buffer`` defaults to 1.05 (5% statutory/slippage pad).

    Fail-closed: returns None for zero, negative, NaN, inf, bool, or non-numeric
    ref_ltp / lot_size so the caller can detect an uncalculable requirement.
    """
    if not _finite_positive(ref_ltp):
        return None
    if not _finite_positive(lot_size):
        return None
    return float(ref_ltp) * float(lot_size) * buffer


# ---------------------------------------------------------------------------
# 2. Parse Noren cash field
# ---------------------------------------------------------------------------

def parse_cash(limits: Any) -> Optional[float]:
    """Extract and parse the ``cash`` field from a Noren ``limits()`` response.

    Noren returns ``cash`` as a string (e.g. "16552.95").  We:
    1. Require ``limits`` to be a dict.
    2. Require the ``cash`` key to be present and not None.
    3. Convert to float, returning None on any parse failure.
    4. Reject non-finite values (NaN, inf) and negative values.

    Returns the cash as a ``float >= 0``, or ``None`` on any failure.
    """
    if not isinstance(limits, dict):
        return None
    raw = limits.get("cash")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value < 0:
        return None
    return value


# ---------------------------------------------------------------------------
# 3. Core margin check
# ---------------------------------------------------------------------------

def check_margin(
    limits: Any,
    *,
    premium_required: Any,
) -> Tuple[bool, str]:
    """Check whether account cash covers ``premium_required``.

    Returns ``(ok, detail)`` where:
    - ``ok=True``  → cash >= premium_required (order may proceed)
    - ``ok=False`` → insufficient cash, or any input is garbage (fail-closed)

    Fail-closed triggers:
    - ``limits`` is not a dict
    - ``limits["cash"]`` is missing, unparseable, negative, non-finite
    - ``premium_required`` is not finite-positive (None / 0 / negative / NaN / inf)
    - ``cash < premium_required``
    """
    # Guard premium_required first so the message is accurate.
    if not _finite_positive(premium_required):
        return (
            False,
            f"premium_required={premium_required!r} is not a finite positive number — "
            "cannot determine margin adequacy",
        )

    cash = parse_cash(limits)
    if cash is None:
        raw_cash = limits.get("cash") if isinstance(limits, dict) else "<limits not a dict>"
        return (
            False,
            f"account cash unreadable (limits.cash={raw_cash!r}); blocking order fail-closed",
        )

    if cash >= premium_required:
        return (
            True,
            f"cash ₹{cash:.2f} >= required ₹{premium_required:.2f} — margin ok",
        )
    return (
        False,
        f"insufficient funds: cash ₹{cash:.2f} < required ₹{premium_required:.2f}",
    )


# ---------------------------------------------------------------------------
# 4. Convenience verdict wrapper
# ---------------------------------------------------------------------------

def margin_verdict(
    limits: Any,
    *,
    ref_ltp: Any,
    lot_size: Any,
    buffer: float = 1.05,
) -> Dict[str, Any]:
    """Compute the margin verdict dict (matches order_builder.py Verdict shape).

    Calls ``required_premium`` internally and then ``check_margin``.  Fails
    closed if ``required_premium`` returns None.

    Returns::

        {"check": "margin", "ok": bool, "detail": str}
    """
    req = required_premium(ref_ltp, lot_size, buffer=buffer)
    if req is None:
        return {
            "check": "margin",
            "ok": False,
            "detail": (
                f"cannot compute required premium: ref_ltp={ref_ltp!r}, "
                f"lot_size={lot_size!r} must both be finite positive numbers"
            ),
        }

    ok, detail = check_margin(limits, premium_required=req)
    return {"check": "margin", "ok": ok, "detail": detail}


# ---------------------------------------------------------------------------
# 5. Broker GetOrderMargin pre-trade gate (A4)
# ---------------------------------------------------------------------------

def _parse_finite_nonneg(raw: object) -> Optional[float]:
    """Coerce a Noren string/numeric to a finite non-negative float, else None.

    Mirrors parse_cash's tolerance (Noren sends amounts as strings e.g. "13000")
    but operates on a raw value rather than a dict field.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value < 0:
        return None
    return value


def broker_margin_verdict(resp: Any) -> Dict[str, Any]:
    """Verdict from the broker's authoritative GetOrderMargin response.

    This is the broker's own pre-trade margin ruling for the order we are about
    to transmit.  Unlike :func:`margin_verdict` (a LOCAL affordability floor),
    this asks the broker directly.  ``resp`` is the ALREADY-FETCHED raw
    GetOrderMargin response dict (this function is pure / no-IO).

    Decision rules
    --------------
    - Empty / ``{}`` / non-dict (transport unavailable) → ``ok=True`` **FAIL-OPEN**.
      The probe is simply unavailable (e.g. a transport error coerced to ``{}``);
      blocking all trading on a transient hiccup would be worse than relying on
      the local ``margin_verdict`` floor that still guards affordability.
    - ``resp["stat"] != "Ok"`` (broker REJECT — e.g. NRML not permitted for the
      account/exchange) → ``ok=False`` **FAIL-CLOSED**.  If the broker won't even
      quote the margin, we must not place an entry we can't protect.
    - ``stat == "Ok"`` → parse ``cash`` (credits available) and ``marginused``
      (margin this order needs); ``ok = cash >= marginused``.
    - ``stat == "Ok"`` but either number is unparseable / missing / non-finite →
      ``ok=False`` **FAIL-CLOSED** (conservative — don't transmit on garbage).

    Returns::

        {"check": "broker_margin", "ok": bool, "detail": str}
    """
    # Transport unavailable → fail-OPEN.
    if not isinstance(resp, dict) or not resp:
        return {
            "check": "broker_margin",
            "ok": True,
            "detail": (
                "broker margin probe unavailable (empty/non-dict response); "
                "fail-open — local margin_verdict floor still guards affordability"
            ),
        }

    # Broker rejected the probe → fail-CLOSED.
    if resp.get("stat") != "Ok":
        emsg = resp.get("emsg")
        return {
            "check": "broker_margin",
            "ok": False,
            "detail": (
                f"broker rejected margin probe (stat={resp.get('stat')!r}, "
                f"emsg={emsg!r}); blocking entry fail-closed"
            ),
        }

    # stat Ok → compare credits available vs margin needed.
    cash = _parse_finite_nonneg(resp.get("cash"))
    margin_used = _parse_finite_nonneg(resp.get("marginused"))
    if cash is None or margin_used is None:
        return {
            "check": "broker_margin",
            "ok": False,
            "detail": (
                f"broker margin numbers unreadable (cash={resp.get('cash')!r}, "
                f"marginused={resp.get('marginused')!r}); blocking entry fail-closed"
            ),
        }

    ok = cash >= margin_used
    if ok:
        detail = f"broker margin ok: cash ₹{cash:.2f} >= marginused ₹{margin_used:.2f}"
    else:
        detail = (
            f"broker insufficient margin: cash ₹{cash:.2f} < marginused ₹{margin_used:.2f}"
        )
    return {"check": "broker_margin", "ok": ok, "detail": detail}


# ---------------------------------------------------------------------------
# 6. Resting-OCO margin pre-check
# ---------------------------------------------------------------------------

def oco_margin_skip_reason(resp: Any) -> Optional[str]:
    """Reason to SKIP the resting OCO, or ``None`` to attempt it.

    Same GetOrderMargin response as :func:`broker_margin_verdict`, opposite
    fallback direction — and the direction is the whole design.

    :func:`broker_margin_verdict` gates an ENTRY, so an unreadable probe fails
    CLOSED: refuse to trade. By the time this runs the entry is already FILLED
    and registered with the software guard, and the OCO is a best-effort
    PC-down catastrophe net. Failing closed here would mean discarding a
    backstop because a probe was unreadable — removing protection to punish a
    transport error. So this returns a reason ONLY on an affirmative, readable
    shortfall; every other case attempts the OCO exactly as before.

    Why it is worth a call at all: a **resting** NRML sell is margined as a
    potential naked short — the broker cannot know the long will still exist
    when the trigger fires. On 2026-08-14 protecting a Rs 4,010 position
    demanded Rs 1,01,830 against Rs 86,932 available, and the reject arrived
    ASYNCHRONOUSLY, after ``PlaceOCOOrder`` had already answered ok with an
    al_id. Where that shortfall is structural, every live entry otherwise
    places an order that is accepted, rejected seconds later, and swept by the
    supervisor tick. This turns that into an explained skip.

    It does NOT replace the supervisor's GTT re-verification (``oco_verify``):
    a probe that passes still proves nothing about survival.
    """
    if not isinstance(resp, dict) or not resp:
        return None                      # probe unavailable → try anyway
    if resp.get("stat") != "Ok":
        return None                      # not evidence of a shortfall
    cash = _parse_finite_nonneg(resp.get("cash"))
    margin_used = _parse_finite_nonneg(resp.get("marginused"))
    if cash is None or margin_used is None:
        return None                      # unreadable → let the real call rule
    if cash >= margin_used:
        return None
    return (
        f"insufficient margin for a resting OCO: needs ₹{margin_used:.2f}, "
        f"cash ₹{cash:.2f}"
    )
