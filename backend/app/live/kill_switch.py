"""Kill switch + account-level guardrails for live trading (Task L2.2).

Architecture
------------
Three layers:

1. **Guardrails (pure)**: ``evaluate_guardrails`` + latch helpers — stateless
   functions over a config dict, no I/O, no mutation.

2. **Plan (pure)**: ``plan_squareoff`` — given the current order book and
   position book, computes WHAT would be done (cancel ids + flatten intents)
   without calling ANY client method.  Used by the kill-switch route so the L2
   "no real-order in the safe core" invariant is maintained end-to-end.

3. **Executor**: ``panic_squareoff`` — calls ``client.cancel_order`` /
   ``client.place_order`` against a BrokerClient (MockNoren in tests, real
   Flattrade in L3).  NEVER raises — always returns a report so a kill is
   audit-trail-friendly even if individual legs fail.

Key safety properties
---------------------
- Fat-finger and throttle checks are NEVER applied to flatten intents.
  The engine must always be able to exit a position it holds.
- A position whose ref price is missing/non-finite/≤0 is NOT silently dropped;
  it goes into the ``unpriced`` list so the operator is alerted.
- ``evaluate_guardrails`` fail-safes unknown / non-finite P&L to
  "broker_stop_loss" — better to block than trade blind.
- The broker-stop-loss latch can ONLY be cleared by an explicit
  ``reset_latch(config)`` call.  It does NOT self-clear.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional, Tuple

from app.live.broker_protocol import OrderIntent
from app.live.idempotency import new_client_order_id
from app.live.order_builder import round_to_tick, slice_to_freeze
from app.live.safety import validate_jdata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Order statuses that are terminal — no further state changes possible.
#: Always compared in UPPER case after normalisation (see _normalize_status).
#: "REJECT" (OrderBook sample spelling) and "CANCELLED" (double-L drift) are
#: included so a rejected order never receives a futile cancel.
TERMINAL: frozenset[str] = frozenset(
    {"COMPLETE", "REJECTED", "REJECT", "CANCELED", "CANCELLED"})

#: Rejected-order statuses (both spellings seen across Noren surfaces).
_REJECTED_STATUSES: frozenset[str] = frozenset({"REJECTED", "REJECT"})

#: Sensible defaults for a single-account retail setup.
DEFAULT_SAFETY_CONFIG: Dict[str, Any] = {
    "daily_loss_limit": 5000,       # ₹ — broker-stop-loss when MTM ≤ −5000
    "profit_lock_target": 10000,    # ₹ — lock profits when MTM ≥ 10000
    "max_open_positions": 5,        # hard cap on concurrent open positions
    "max_lots_per_order": 20,       # account-level ceiling on lots per order
    "blocked_until_reset": False,   # latch; only explicit reset_latch clears it
}

#: Keys that PUT /safety-config is allowed to touch.
#: ``blocked_until_reset`` is deliberately excluded — the latch is controlled
#: ONLY by the dedicated trip()/reset() methods and the reset-latch route.
_PUT_CONFIG_WHITELIST: frozenset[str] = frozenset({
    "daily_loss_limit",
    "profit_lock_target",
    "max_open_positions",
    "max_lots_per_order",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_status(raw: Any) -> str:
    """Return the order status as an UPPER-CASE stripped string.

    Noren can return statuses like ``"complete"``, ``"Canceled"``, ``"OPEN"``
    depending on the API version.  Always compare against ``TERMINAL`` via this
    helper so case drift is never a silent bug.
    """
    return str(raw or "").strip().upper()


def _parse_netqty(raw: Any) -> Optional[int]:
    """Parse a Noren netqty value to int, handling float-form and comma-formatted strings.

    Noren can return netqty as:
    - ``"100"``      → 100  (normal integer string)
    - ``"100.0"``    → 100  (float-form string — bare int() would ValueError)
    - ``"-50.0"``    → -50
    - ``"1,000"``    → 1000 (comma-formatted number)
    - ``"99.9"``     → 99   (truncation: int(float(...)); intentional, matches reconcile.py:172)
    - ``"abc"``      → None (unparseable → caller must surface in unpriced)
    - ``"nan"``      → None (non-finite → caller must surface in unpriced)
    - ``"inf"``      → None (non-finite → caller must surface in unpriced)
    - ``100``        → 100  (already an int)
    - ``100.0``      → 100  (already a float)

    Returns None if the value cannot be converted to a finite integer.  The
    caller is responsible for deciding what to do with None — in both
    plan_squareoff and panic_squareoff, a None result means the position is
    added to ``unpriced`` and ``total`` is forced to False.  A position is
    NEVER silently coerced to netqty=0 (flat) when it cannot be parsed.

    The int(float(...)) truncation is intentional and matches the project's
    established pattern in reconcile.py:172.
    """
    try:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            if not math.isfinite(raw):
                return None
            return int(raw)
        # String path: strip commas and whitespace
        cleaned = str(raw).replace(",", "").strip()
        val = float(cleaned)
        if not math.isfinite(val):
            return None
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. Guardrails — pure, stateless
# ---------------------------------------------------------------------------

def evaluate_guardrails(
    mtm: Any,
    open_count: Any,
    config: Dict[str, Any],
) -> str:
    """Evaluate account-level guardrails given current MTM and open position count.

    Returns one of::

        "none"              — all clear, trading may continue
        "broker_stop_loss"  — daily-loss limit breached (or inputs invalid)
        "max_open_block"    — too many concurrent open positions
        "profit_lock"       — profit target reached; no new entries

    Priority order (first match wins):
      1. Loss check (broker_stop_loss) — loss is always evaluated first; it is
         also the fail-safe for non-finite / None inputs.
      2. Open-count cap (max_open_block).
      3. Profit lock (profit_lock).

    Fail-safe on bad inputs
    -----------------------
    If *mtm* or *open_count* is None, NaN, or non-finite, the function returns
    ``"broker_stop_loss"`` rather than "none".  This is deliberate: trading with
    an unknown P&L or position count is more dangerous than pausing until the
    feed recovers.
    """
    daily_loss_limit = abs(config.get("daily_loss_limit", DEFAULT_SAFETY_CONFIG["daily_loss_limit"]))
    profit_lock_target = config.get("profit_lock_target", DEFAULT_SAFETY_CONFIG["profit_lock_target"])
    max_open_positions = config.get("max_open_positions", DEFAULT_SAFETY_CONFIG["max_open_positions"])

    # Fail-safe: if we cannot evaluate the loss condition, treat it as a breach.
    try:
        mtm_finite = (
            mtm is not None
            and isinstance(mtm, (int, float))
            and not isinstance(mtm, bool)
            and math.isfinite(mtm)
        )
    except Exception:
        mtm_finite = False

    try:
        count_ok = (
            open_count is not None
            and isinstance(open_count, (int, float))
            and not isinstance(open_count, bool)
            and math.isfinite(open_count)
        )
    except Exception:
        count_ok = False

    # Unknown P&L or position count → fail-safe to broker_stop_loss.
    if not mtm_finite or not count_ok:
        return "broker_stop_loss"

    # Priority 1: loss limit (fail-closed side — evaluated first)
    if mtm <= -daily_loss_limit:
        return "broker_stop_loss"

    # Priority 2: open-position cap
    if open_count >= max_open_positions:
        return "max_open_block"

    # Priority 3: profit lock
    if profit_lock_target > 0 and mtm >= abs(profit_lock_target):
        return "profit_lock"

    return "none"


# ---------------------------------------------------------------------------
# 2. Latch helpers — pure; return NEW config dicts (never mutate in place)
# ---------------------------------------------------------------------------

def trip_latch(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new config dict with ``blocked_until_reset=True``.

    The engine calls this when ``evaluate_guardrails`` returns
    ``"broker_stop_loss"``.  The latch CANNOT self-clear; only
    ``reset_latch`` can remove it.
    """
    return {**config, "blocked_until_reset": True}


def reset_latch(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new config dict with ``blocked_until_reset=False``.

    Must be called explicitly by an authorised operator action; the engine
    never calls this automatically.
    """
    return {**config, "blocked_until_reset": False}


def is_entry_blocked(config: Dict[str, Any]) -> bool:
    """Return True iff the latch is set (no new entries allowed)."""
    return bool(config.get("blocked_until_reset", False))


# ---------------------------------------------------------------------------
# 3. Plan (pure) — computes WHAT would be done, never calls client methods
# ---------------------------------------------------------------------------

def plan_squareoff(
    open_orders: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    *,
    band_pct: float = 1.0,
    ref_price_field: str = "lp",
    tick: float = 0.05,
) -> Dict[str, Any]:
    """Compute the squareoff plan without transmitting anything to the broker.

    Parameters
    ----------
    open_orders:
        List of order dicts from ``client.order_book()``.  Working orders
        (status not in TERMINAL) will be cancelled.
    open_positions:
        List of position dicts from ``client.position_book()``.  Positions
        with ``netqty != 0`` will be flattened.
    band_pct:
        Marketable-limit cross buffer (%).  Clamped as in order_builder:
        SELL price = ref * (1 − eff/100), BUY price = ref * (1 + eff/100),
        eff = abs(band_pct).
    ref_price_field:
        Field name for the reference price in each position dict (default
        ``"lp"`` — Noren last price).

    Returns
    -------
    dict with keys:
        ``would_cancel``: list of norenordno strings for working orders.
        ``would_flatten``: list of jdata dicts (``intent.to_jdata("", "")``)
                           for positions that would be flattened.
        ``unpriced``:     list of ``{"tsym": ..., "netqty": ...}`` for
                          positions whose ref price is missing/invalid —
                          these are NOT in would_flatten and must be handled
                          manually.

    Notes
    -----
    - ``validate_jdata`` is run (structural check only) but fat-finger and
      throttle checks are NEVER applied.  The engine must always be able to
      exit a position it holds.
    - If the ref price is missing, non-finite, or ≤ 0, the position goes into
      ``unpriced`` — it is NOT silently skipped.
    """
    eff = abs(band_pct)
    _tick = tick if tick > 0 else 0.05  # guard invalid tick

    # F2: normalise status to UPPER before TERMINAL membership test so that
    # "canceled", "Canceled", "complete", etc. are all treated as terminal.
    would_cancel: List[str] = [
        o["norenordno"]
        for o in open_orders
        if _normalize_status(o.get("status")) not in TERMINAL
    ]

    would_flatten: List[Dict[str, Any]] = []
    unpriced: List[Dict[str, Any]] = []

    for pos in open_positions:
        tsym = pos.get("tsym", "")
        raw_netqty = pos.get("netqty", 0)
        netqty = _parse_netqty(raw_netqty)

        # F1: unparseable netqty → surface in unpriced, never silently skip.
        if netqty is None:
            unpriced.append({"tsym": tsym, "netqty": raw_netqty})
            continue

        # Genuinely flat position — skip (correct behaviour, not a silent drop).
        if netqty == 0:
            continue

        exch = pos.get("exch", "NFO")
        ref_raw = pos.get(ref_price_field)

        # Validate ref price — missing/non-finite/≤0 → unpriced
        try:
            ref = float(ref_raw)
            ref_ok = math.isfinite(ref) and ref > 0
        except (TypeError, ValueError):
            ref = float("nan")
            ref_ok = False

        if not ref_ok:
            unpriced.append({"tsym": tsym, "netqty": netqty})
            continue

        # Flatten direction: long → SELL, short → BUY
        # Round to exchange tick (0.05 for index options) SELL down / BUY up to
        # stay marketable.  round(ref*(1±eff/100), 2) alone is NOT tick-aligned
        # and the broker will reject the order ("Price X is not a multiple of 0.05").
        if netqty > 0:
            trantype = "S"
            prc = round_to_tick(ref * (1.0 - eff / 100.0), _tick, mode="down")
        else:
            trantype = "B"
            prc = round_to_tick(ref * (1.0 + eff / 100.0), _tick, mode="up")

        qty = abs(netqty)
        cid = new_client_order_id()

        intent = OrderIntent(
            client_order_id=cid,
            trantype=trantype,
            prctyp="LMT",
            exch=exch,
            tsym=tsym,
            qty=qty,
            prc=prc,
            prd=(str(pos.get("prd")) if pos.get("prd") else "I"),
            ret="DAY",
            trgprc=None,
            remarks=cid,
        )

        # Structural validation only — no fat-finger, no throttle.
        # We pass qty as lot_size=1 to allow any qty; structural fields are what matter.
        # (The actual lot_size is unknown here; we only care prctyp/prd/ret/prc are sane.)
        ok, _reason = validate_jdata(intent, lot_size=qty)
        # If qty happens to be 0 validate_jdata would block, but we already guard netqty!=0
        # above.  For any non-zero qty, lot_size=qty ensures qty % lot_size == 0.

        would_flatten.append(intent.to_jdata(uid="", actid=""))

    return {
        "would_cancel": would_cancel,
        "would_flatten": would_flatten,
        "unpriced": unpriced,
    }


# ---------------------------------------------------------------------------
# 4. Executor — calls client methods (tested ONLY against MockNoren in L2)
# ---------------------------------------------------------------------------

async def panic_squareoff(
    client: Any,
    open_orders: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    *,
    band_pct: float = 1.0,
    ref_price_field: str = "lp",
    tick: float = 0.05,
    uid: str = "",
    actid: str = "",
) -> Dict[str, Any]:
    """Cancel all working orders and flatten all open positions.

    This is the EXECUTOR layer — it calls ``client.cancel_order`` and
    ``client.place_order`` directly, bypassing all throttle and fat-finger
    checks, because the engine MUST always be able to exit positions it holds.

    NEVER raises — always returns a report even when individual legs fail.

    Parameters
    ----------
    client:
        A BrokerClient (``MockNoren`` in tests; ``FlattradeClient`` in L3).
    open_orders:
        Order book snapshot (list of order dicts).
    open_positions:
        Position book snapshot (list of position dicts).
    band_pct:
        Marketable-limit cross buffer (%).
    ref_price_field:
        Field name for the reference price in position dicts.
    uid, actid:
        Broker credentials for ``intent.to_jdata(uid, actid)``.

    Returns
    -------
    dict with:
        ``canceled``:         number of successfully cancelled orders.
        ``cancel_failures``:  list of ``{"norenordno": ..., "reason": ...}``.
        ``flattened``:        number of successfully placed flatten orders.
        ``flatten_failures``: list of ``{"tsym": ..., "netqty": ..., "reason": ...}``.
        ``unpriced``:         list of ``{"tsym": ..., "netqty": ...}`` (bad netqty or
                              missing ref price).
        ``total``:            True iff cancel_failures, flatten_failures, and
                              unpriced are all empty.

    Caller contracts (L2.3 engine)
    --------------------------------
    (F4) 1. ``open_orders`` MUST be the COMPLETE, freshly-fetched working set at
            the moment of the call.  Passing a stale or partial list means some
            orders will not be cancelled.

         2. The caller MUST re-fetch the order and position books between
            successive kills — panic is NOT self-idempotent.  Re-passing a stale
            open-position list after a first flatten attempt will cause the engine
            to attempt to double-exit the same position.

    # NOTE: panic_squareoff is the EXECUTOR layer.  It is tested against
    # MockNoren in L2 and against FlattradeClient in L3.  The kill-switch
    # ROUTE always calls plan_squareoff (pure — no transmit); only the L2.3
    # engine (and dedicated panic tests) call this function.
    """
    eff = abs(band_pct)
    _tick = tick if tick > 0 else 0.05  # guard invalid tick

    canceled = 0
    cancel_failures: List[Dict[str, Any]] = []

    # Step 1 — cancel every working order (bypass throttle entirely).
    for order in open_orders:
        # F2: normalise status to UPPER so "canceled"/"Canceled"/"complete" etc.
        # are treated as terminal and NOT re-cancelled.
        if _normalize_status(order.get("status")) in TERMINAL:
            continue
        norenordno = order.get("norenordno", "")
        try:
            result = await client.cancel_order(norenordno)
            if result.ok:
                canceled += 1
            else:
                cancel_failures.append({
                    "norenordno": norenordno,
                    "reason": result.rejreason or "cancel returned ok=False",
                })
        except Exception as exc:
            cancel_failures.append({"norenordno": norenordno, "reason": str(exc)})

    # Step 2 — flatten every non-zero position.
    flattened = 0
    flatten_failures: List[Dict[str, Any]] = []
    unpriced: List[Dict[str, Any]] = []

    for pos in open_positions:
        tsym = pos.get("tsym", "")
        raw_netqty = pos.get("netqty", 0)
        netqty = _parse_netqty(raw_netqty)

        # F1: unparseable netqty → surface in unpriced, NEVER silently coerce to 0.
        if netqty is None:
            unpriced.append({"tsym": tsym, "netqty": raw_netqty})
            continue

        # Genuinely flat position — skip (correct, not a silent drop).
        if netqty == 0:
            continue

        exch = pos.get("exch", "NFO")
        ref_raw = pos.get(ref_price_field)

        # Validate ref price
        try:
            ref = float(ref_raw)
            ref_ok = math.isfinite(ref) and ref > 0
        except (TypeError, ValueError):
            ref = float("nan")
            ref_ok = False

        if not ref_ok:
            unpriced.append({"tsym": tsym, "netqty": netqty})
            continue

        # Flatten direction: long → SELL, short → BUY.
        # Round to exchange tick (SELL down / BUY up to stay marketable).
        if netqty > 0:
            trantype = "S"
            prc = round_to_tick(ref * (1.0 - eff / 100.0), _tick, mode="down")
        else:
            trantype = "B"
            prc = round_to_tick(ref * (1.0 + eff / 100.0), _tick, mode="up")

        qty = abs(netqty)
        cid = new_client_order_id()

        intent = OrderIntent(
            client_order_id=cid,
            trantype=trantype,
            prctyp="LMT",
            exch=exch,
            tsym=tsym,
            qty=qty,
            prc=prc,
            # FIX: flatten in the position's OWN product (NRML "M" vs MIS "I").
            # Deployed entries now use NRML; an MIS sell does NOT net an NRML
            # long on Noren. The Positions Book row carries `prd` per position.
            prd=(str(pos.get("prd")) if pos.get("prd") else "I"),
            ret="DAY",
            trgprc=None,
            remarks=cid,
        )

        try:
            result = await client.place_order(intent)
            if result.ok:
                flattened += 1
            else:
                flatten_failures.append({
                    "tsym": tsym,
                    "netqty": netqty,
                    "reason": result.rejreason or "place_order returned ok=False",
                })
        except Exception as exc:
            flatten_failures.append({"tsym": tsym, "netqty": netqty, "reason": str(exc)})

    total = (cancel_failures == [] and flatten_failures == [] and unpriced == [])

    return {
        "canceled": canceled,
        "cancel_failures": cancel_failures,
        "flattened": flattened,
        "flatten_failures": flatten_failures,
        "unpriced": unpriced,
        "total": total,
    }


# ---------------------------------------------------------------------------
# 4b. Verified panic squareoff — bounded re-price loop + per-leg outcomes
# ---------------------------------------------------------------------------

#: Widening marketable-limit band schedule (%): pass 1 crosses 1% through the
#: touch, unfilled legs re-price at 2%, then 4%. len() bounds the loop. Order
#: APIs are rate-limited to 10/sec + 40/min — with a handful of legs this
#: schedule (place + cancel per pass) stays well inside the budget.
FLATTEN_BAND_SCHEDULE: Tuple[float, ...] = (1.0, 2.0, 4.0)

#: Seconds to wait after each placement pass before polling the order book.
FLATTEN_POLL_SECONDS: float = 2.0

#: tsym prefix → exchange single-order freeze qty. Position-book rows don't
#: name their underlying, so the tsym prefix is the only local signal (order
#: matters: BANKNIFTY before NIFTY). Unknown prefix → no slicing; an oversize
#: reject is then surfaced per-leg, never swallowed. Values mirror
#: flattrade_symbol.EXCHANGE_RULES freeze_qty.
_FREEZE_BY_TSYM_PREFIX: Tuple[Tuple[str, int], ...] = (
    ("BANKNIFTY", 600), ("NIFTY", 1800), ("BSXOPT", 1000), ("SENSEX", 1000))

#: Leg outcomes (report vocabulary — the UI colors off these strings).
LEG_FILLED = "FILLED"
LEG_REJECTED = "REJECTED"
LEG_UNCONFIRMED = "PLACED_UNCONFIRMED"
LEG_FAILED = "FAILED"
LEG_UNPRICED = "UNPRICED"


def _pos_float(value: Any) -> Optional[float]:
    """float(value) if it is finite and > 0, else None (Noren sends strings)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _leg_price(netqty: int, ref: Optional[float], band_pct: float,
               tick: float, quote: Dict[str, Any]) -> Optional[float]:
    """Marketable LIMIT price for one flatten leg, exchange-aware.

    Long → SELL priced through the best BID (bp1); short → BUY through the
    best ASK (sp1) — from a fresh GetQuotes snapshot when available, else the
    (possibly stale) position-book lp. The band crosses the anchor to stay
    marketable on a moving market; the price is clamped inside the exchange
    circuit band [lc, uc] when the quote carries it (a limit outside the band
    is an automatic reject), then aligned to the leg's own tick (SELL down /
    BUY up). Returns None when no usable anchor exists.
    """
    if netqty > 0:
        anchor = _pos_float(quote.get("bp1")) or ref
        if anchor is None:
            return None
        prc = anchor * (1.0 - abs(band_pct) / 100.0)
        lc = _pos_float(quote.get("lc"))
        if lc is not None:
            prc = max(prc, lc)
        return round_to_tick(prc, tick, mode="down")
    anchor = _pos_float(quote.get("sp1")) or ref
    if anchor is None:
        return None
    prc = anchor * (1.0 + abs(band_pct) / 100.0)
    uc = _pos_float(quote.get("uc"))
    if uc is not None:
        prc = min(prc, uc)
    return round_to_tick(prc, tick, mode="up")


def _freeze_qty_for_tsym(tsym: str) -> Optional[int]:
    t = str(tsym or "").upper()
    for prefix, freeze in _FREEZE_BY_TSYM_PREFIX:
        if t.startswith(prefix):
            return freeze
    return None


def _build_flatten_legs(open_positions: List[Dict[str, Any]],
                        ref_price_field: str) -> List[Dict[str, Any]]:
    """One leg per position (freeze-sliced when qty exceeds the exchange's
    single-order max). Unpriceable/unparseable rows become UNPRICED legs —
    surfaced, never silently dropped."""
    legs: List[Dict[str, Any]] = []
    for pos in open_positions:
        tsym = pos.get("tsym", "")
        raw_netqty = pos.get("netqty", 0)
        netqty = _parse_netqty(raw_netqty)
        if netqty is None:
            legs.append({"tsym": tsym, "netqty": raw_netqty, "qty": 0,
                         "exch": pos.get("exch", "NFO"), "prd": "I",
                         "attempts": [], "filled_qty": 0,
                         "outcome": LEG_UNPRICED, "reason": "unparseable netqty"})
            continue
        if netqty == 0:
            continue
        ref = _pos_float(pos.get(ref_price_field))
        token = str(pos.get("token") or "") or None
        if ref is None and token is None:
            legs.append({"tsym": tsym, "netqty": netqty, "qty": abs(netqty),
                         "exch": pos.get("exch", "NFO"),
                         "prd": (str(pos.get("prd")) if pos.get("prd") else "I"),
                         "attempts": [], "filled_qty": 0,
                         "outcome": LEG_UNPRICED,
                         "reason": "no usable reference price (stale lp, no token for quotes)"})
            continue
        tick = _pos_float(pos.get("ti")) or 0.05
        base = {
            "tsym": tsym,
            "exch": pos.get("exch", "NFO"),
            # Flatten in the position's OWN product (MIS sell does not net an
            # NRML long) — same rule as panic_squareoff.
            "prd": (str(pos.get("prd")) if pos.get("prd") else "I"),
            "tick": tick,
            "token": token,
            "ref": ref,
        }
        qty = abs(netqty)
        freeze = _freeze_qty_for_tsym(tsym)
        if freeze and qty > freeze:
            try:
                slices = slice_to_freeze(qty, freeze)
            except ValueError:
                # slice_to_freeze fat-finger-caps at 10x freeze — an ENTRY
                # safeguard. A flatten must always exit what the account holds:
                # slice unbounded.
                n = math.ceil(qty / freeze)
                slices = [freeze] * (n - 1) + [qty - freeze * (n - 1)]
        else:
            slices = [qty]
        for i, sqty in enumerate(slices):
            legs.append({
                **base,
                "netqty": sqty if netqty > 0 else -sqty,
                "qty": sqty,
                "slice": f"{i + 1}/{len(slices)}" if len(slices) > 1 else None,
                "attempts": [],
                "filled_qty": 0,
                "outcome": None,
                "reason": None,
            })
    return legs


async def _order_row(client: Any, norenordno: str) -> Optional[Dict[str, Any]]:
    """Best-effort single-order lookup from a fresh order-book poll."""
    try:
        book = await client.order_book()
    except Exception:
        return None
    for row in book or []:
        if str(row.get("norenordno")) == str(norenordno):
            return row
    return None


async def _scan_order_by_remarks(
    client: Any, cid: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Tri-state lost-ack resolver: did a RAISED place_order actually land?

    A place that RAISES (httpx timeout, socket reset) may still have been
    ACCEPTED by the broker — retrying it blind posts a SECOND exit next to the
    live ghost and both can fill (naked short). Every order pins
    ``remarks == client_order_id`` (order builder invariant), so a fresh
    order-book scan on that key resolves the ambiguity — the same adoption
    pattern as ``engine.resume_pending``.

    Returns
    -------
    ``("found", row)``      — the order LANDED (any status; the caller decides
                              whether to adopt it or, for a REJECTED row whose
                              fillshares are 0 by definition, retry fresh).
    ``("absent", None)``    — a READABLE book has no such order: the POST never
                              landed and a fresh retry is safe. (The real
                              client returns [] only for a confirmed-empty
                              book; every other Not_Ok raises BrokerReadError.)
    ``("unreadable", None)``— the book cannot be read, or the client exposes
                              no ``order_book`` at all: the caller must NOT
                              retry (fail closed — never place next to a
                              possible ghost).
    """
    if not hasattr(client, "order_book"):
        return ("unreadable", None)
    try:
        book = await client.order_book()
    except Exception:
        return ("unreadable", None)
    for row in (book or []):
        if str(row.get("remarks") or "") == str(cid):
            return ("found", row)
    return ("absent", None)


async def _confirm_cancels_and_resize_legs(
    client: Any,
    attempted_cancels: List[Tuple[str, str]],
    legs: List[Dict[str, Any]],
) -> None:
    """Post-cancel CONFIRM BARRIER for ``panic_squareoff_verified`` (mutates legs).

    Step-1 cancels used to be fire-and-forget: a pre-existing working order
    (e.g. a resting guard exit) that FILLED before its cancel landed still got
    a full-qty leg placed from the stale position snapshot → over-sell → naked
    short. This barrier runs once between the cancels and the first placement
    pass and mirrors the over-sell-safe sizing ``reprice_exit_leg`` already
    implements (terminal-status requirement + fillshares + book-floor min()):

    * ONE order-book re-fetch must show every attempted cancel TERMINAL. A
      scrip with a non-terminal / missing cancelled order — or ANY untracked
      working order (e.g. an OCO leg that triggered mid-kill) — is BLOCKED:
      its legs become LEG_FAILED (loud, in flatten_failures) and nothing is
      placed for it this invocation. Unreadable re-fetch → every cancelled
      scrip is blocked (fail closed).
    * fillshares of terminal cancelled orders whose trantype matches the leg's
      exit direction reduced the position → subtract them from the leg qty.
    * a fresh position-book read, when KNOWN (non-empty), floors the remaining
      per scrip (absent from a known book = flat). A raising/empty book is
      UNKNOWN — no floor, but the fillshares math still bounds the qty because
      the attempted set is the COMPLETE working set for the scrip.
    * a scrip whose remaining hits 0 closes its legs as LEG_FILLED (the
      resting order did the exit's job); partially-reduced legs are re-sized
      (``resized_from`` records the original qty in the report).
    """
    pending = [l for l in legs if l["outcome"] is None]
    if not pending:
        return
    leg_tsyms = {str(l["tsym"]) for l in pending}
    touched = {t for _, t in attempted_cancels if t in leg_tsyms}
    if not touched:
        return
    # One position per scrip → one exit direction per scrip.
    exit_dir = {str(l["tsym"]): ("S" if l["netqty"] > 0 else "B") for l in pending}

    try:
        book = await client.order_book()
    except Exception as exc:
        emsg = getattr(exc, "emsg", None) or str(exc)
        reason = (f"cancel unconfirmed — order book unreadable after the "
                  f"cancels ({str(emsg)[:80]}); refusing to place exits "
                  "(double-sell risk)")
        for leg in pending:
            if str(leg["tsym"]) in touched:
                leg["outcome"] = LEG_FAILED
                leg["reason"] = reason
        return

    rows_by_no = {str(r.get("norenordno")): r for r in (book or [])}
    attempted_nos = {no for no, _ in attempted_cancels}
    blocked: Dict[str, str] = {}
    reduced: Dict[str, int] = {}

    for no, tsym in attempted_cancels:
        if tsym not in leg_tsyms:
            continue  # no pending leg on this scrip — nothing to protect
        row = rows_by_no.get(str(no))
        if row is None:
            blocked.setdefault(tsym, (
                f"cancel unconfirmed — cancelled order {no} not found in the "
                "re-fetched book; refusing to place (double-sell risk)"))
            continue
        status = _normalize_status(row.get("status"))
        if status not in TERMINAL:
            blocked.setdefault(tsym, (
                f"cancel unconfirmed — order {no} still {status or 'WORKING'} "
                "after the cancel; refusing to place (double-sell risk)"))
            continue
        fs = _parse_netqty(row.get("fillshares")) or 0
        if fs > 0:
            # Only fills in the leg's EXIT direction shrank the position. A
            # missing trantype counts too: an under-sized exit is surfaced by
            # the final residual check; an over-sized one is a naked short.
            tran = _normalize_status(row.get("trantype"))
            if not tran or tran == exit_dir.get(tsym):
                reduced[tsym] = reduced.get(tsym, 0) + fs

    # Discovery: an untracked working order on a leg scrip (e.g. an OCO leg
    # that TRIGGERED between the caller's snapshot and now) can also fill
    # against the position — block that scrip too (a re-fired kill snapshots
    # and cancels it).
    for r in (book or []):
        if _normalize_status(r.get("status")) in TERMINAL:
            continue
        t = str(r.get("tsym") or "")
        if t in leg_tsyms and str(r.get("norenordno")) not in attempted_nos:
            blocked.setdefault(t, (
                f"cancel unconfirmed — untracked working order "
                f"{r.get('norenordno')} live on the scrip; refusing to place "
                "(double-sell risk)"))

    # Position-book floor (tri-state: a raising/empty book is UNKNOWN → no
    # floor; a present-but-unparseable netqty row is UNKNOWN for its scrip).
    pnet: Dict[str, Optional[int]] = {}
    pbook_known = False
    if touched - set(blocked):
        try:
            pbook = await client.position_book()
        except Exception:
            pbook = None
        if isinstance(pbook, list) and pbook:
            pbook_known = True
            for p in pbook:
                pnet[str(p.get("tsym") or "")] = _parse_netqty(p.get("netqty"))

    # Union: `blocked` can contain a leg scrip with NO attempted cancel (a
    # working order discovered post-snapshot, e.g. a mid-kill OCO trigger) —
    # its legs must be failed too, not silently placed next to that order.
    for tsym in sorted(touched | set(blocked)):
        group = [l for l in pending if str(l["tsym"]) == tsym]
        if tsym in blocked:
            for leg in group:
                leg["outcome"] = LEG_FAILED
                leg["reason"] = blocked[tsym]
            continue
        total = sum(int(l["qty"]) for l in group)
        allowed = total - int(reduced.get(tsym, 0))
        if pbook_known:
            if tsym not in pnet:
                allowed = 0  # absent from a KNOWN book → flat
            elif pnet[tsym] is not None:
                allowed = min(allowed, abs(int(pnet[tsym])))
            # unparseable row → UNKNOWN → keep the fillshares-only bound
        allowed = max(0, allowed)
        if allowed >= total:
            continue  # nothing filled against this scrip — legs unchanged
        for leg in group:
            take = min(int(leg["qty"]), allowed)
            allowed -= take
            if take <= 0:
                leg["outcome"] = LEG_FILLED
                leg["reason"] = ("position flattened during the cancel window "
                                 "(a cancelled working order filled) — nothing "
                                 "left for this leg")
            elif take < int(leg["qty"]):
                leg["resized_from"] = int(leg["qty"])
                leg["qty"] = take
                leg["netqty"] = take if leg["netqty"] > 0 else -take


async def panic_squareoff_verified(
    client: Any,
    open_orders: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    *,
    uid: str = "",
    actid: str = "",
    band_schedule: Tuple[float, ...] = FLATTEN_BAND_SCHEDULE,
    poll_seconds: float = FLATTEN_POLL_SECONDS,
    sleep: Any = None,
    ref_price_field: str = "lp",
) -> Dict[str, Any]:
    """panic_squareoff + verification: bounded re-price loop and an honest
    per-leg outcome report.

    The account allows ONLY LIMIT/SL-LIMIT (no market orders; the decoded
    PiConnect reference documents no MKT price type at all), so a panic
    flatten is a marketable LIMIT that may not fill. This executor:

    1. cancels every working order (same semantics as panic_squareoff);
    2. CONFIRM BARRIER: when step 1 attempted any cancel, one order-book
       re-fetch must show the cancelled set terminal before anything is
       placed; fillshares of filled ones + a position-book floor re-size or
       skip the legs, and an unconfirmable scrip is BLOCKED (LEG_FAILED, loud)
       — a working order that filled inside the cancel window already exited
       (part of) the position, so placing the stale full qty would over-sell
       into a naked short (see ``_confirm_cancels_and_resize_legs``);
    3. flattens each position leg with an exchange-aware marketable LIMIT
       (fresh GetQuotes touch when the row carries a token, circuit-band
       clamped, per-leg tick, position's own prd/exch, freeze-qty sliced);
    4. polls the order book after each pass and RE-PRICES unfilled legs at a
       widening band via cancel + re-place (never ModifyOrder — the client's
       modify omits doc-mandatory exch/tsym/qty/ret). Remaining qty after a
       partial fill is re-read from the broker's own fillshares AFTER the
       cancel so a race-window fill can never cause an over-sell. A placement
       that RAISED (lost ack) is resolved against the book via remarks==cid
       before the next pass — adopted if it landed, re-placed only when a
       READABLE book confirms it never did, skipped while the book is
       unreadable (never a blind same-qty re-post next to a live ghost);
    5. re-fetches the position book at the end: ``all_flat`` + ``residual``
       report the broker's truth, and ``legs`` carries every attempt with
       placed/filled/REJECTED + the broker's reason string.

    A broker REJECT ends its leg immediately (re-pricing a margin/RMS reject
    burns rate budget for nothing) and is surfaced loudly; transport errors
    retry on the next pass. An unfilled final attempt is LEFT WORKING at the
    most aggressive price — a resting exit beats no exit — and reported as
    PLACED_UNCONFIRMED. NEVER raises.

    Report: panic_squareoff's keys (canceled / cancel_failures / flattened /
    flatten_failures / unpriced / total) plus ``legs``, ``filled``,
    ``pending``, ``residual``, ``all_flat``. ``flattened`` keeps its historic
    meaning (legs whose exit the broker ACCEPTED); ``filled`` is confirmed.
    ``total`` now additionally requires the final position book to be flat.
    """
    _sleep = sleep or asyncio.sleep

    # Step 1 — cancel every working order (identical semantics to panic).
    # Every attempted cancel is tracked (norenordno, tsym) — the confirm
    # barrier below must verify the WHOLE set went terminal, including the
    # ones whose cancel call itself failed or raised (those are the likeliest
    # to still be live).
    canceled = 0
    cancel_failures: List[Dict[str, Any]] = []
    attempted_cancels: List[Tuple[str, str]] = []
    for order in open_orders:
        if _normalize_status(order.get("status")) in TERMINAL:
            continue
        norenordno = order.get("norenordno", "")
        attempted_cancels.append((str(norenordno), str(order.get("tsym") or "")))
        try:
            result = await client.cancel_order(norenordno)
            if result.ok:
                canceled += 1
            else:
                cancel_failures.append({
                    "norenordno": norenordno,
                    "reason": result.rejreason or "cancel returned ok=False",
                })
        except Exception as exc:
            cancel_failures.append({"norenordno": norenordno, "reason": str(exc)})

    # Step 2 — flatten with verification passes.
    legs = _build_flatten_legs(open_positions, ref_price_field)

    # Step 2.5 — CONFIRM BARRIER: when Step 1 had something to cancel, one
    # order-book re-fetch must confirm the cancelled set is terminal (reading
    # fillshares of the filled ones) and the legs are re-sized/skipped off the
    # broker's truth BEFORE anything is placed. A working order that filled
    # inside the cancel window already exited (part of) the position — placing
    # the stale full qty on top of that fill is the double-sell.
    if attempted_cancels:
        await _confirm_cancels_and_resize_legs(client, attempted_cancels, legs)

    for pass_no, band in enumerate(band_schedule):
        pending = [l for l in legs if l["outcome"] is None]
        if not pending:
            break

        for leg in pending:
            prev = leg["attempts"][-1] if leg["attempts"] else None
            if (prev is not None and not prev.get("placed")
                    and prev.get("cid") and not prev.get("norenordno")):
                # LOST-ACK GUARD (double-sell window): the previous attempt
                # RAISED after transmitting (it carries a cid but no ack) — the
                # broker may have ACCEPTED it. The old behavior skipped the
                # cancel-prev branch (placed=False) and re-placed the remaining
                # qty NEXT TO the live ghost → both fill → naked short. Resolve
                # against the book first (remarks == cid, the resume_pending
                # adoption pattern):
                #   found, not REJECTED → ADOPT it; the normal cancel+resize
                #     branch below then treats it exactly like an acked order;
                #   found REJECTED → no ghost can fill (fillshares 0) → the
                #     leg ends REJECTED like any post-ack book reject;
                #   absent from a READABLE book → it never landed → place;
                #   unreadable → skip this pass (fail closed — never place
                #     next to a possible ghost).
                verdict, ghost = await _scan_order_by_remarks(client, prev["cid"])
                if verdict == "unreadable":
                    prev["reason"] = ((str(prev.get("reason") or "") +
                                       " | lost ack unresolved: order book "
                                       "unreadable — not re-placing").strip(" |"))
                    continue
                if verdict == "found":
                    status = _normalize_status(ghost.get("status"))
                    prev["status"] = status
                    if status in _REJECTED_STATUSES:
                        leg["outcome"] = LEG_REJECTED
                        leg["reason"] = str(ghost.get("rejreason")
                                            or prev.get("reason")
                                            or "rejected (no reason given)")
                        prev["reason"] = leg["reason"]
                        continue
                    prev["placed"] = True
                    prev["norenordno"] = ghost.get("norenordno")
                    prev["filled"] = max(int(prev.get("filled") or 0),
                                         _parse_netqty(ghost.get("fillshares")) or 0)
                    leg["filled_qty"] = sum(int(a.get("filled") or 0)
                                            for a in leg["attempts"])
                # verdict == "absent" → never landed; fall through and place.
            if prev is not None and prev.get("placed"):
                # Cancel the working remainder before re-pricing, then re-read
                # the order's FINAL fill count — it may have (partially) filled
                # in the race window, and over-selling would open a short.
                try:
                    cres = await client.cancel_order(prev["norenordno"])
                    cancel_ok = bool(cres.ok)
                    cancel_reason = cres.rejreason
                except Exception as exc:
                    cancel_ok, cancel_reason = False, str(exc)
                final = await _order_row(client, prev["norenordno"])
                if final is not None:
                    prev["status"] = _normalize_status(final.get("status"))
                    prev["filled"] = max(int(prev.get("filled") or 0),
                                         _parse_netqty(final.get("fillshares")) or 0)
                    leg["filled_qty"] = sum(int(a.get("filled") or 0)
                                            for a in leg["attempts"])
                if not cancel_ok and (prev.get("status") or "") not in TERMINAL:
                    # Cancel failed and the order still looks live: do NOT
                    # place a second exit for the same qty. Report and leave
                    # the working order in place.
                    prev["reason"] = f"cancel failed: {cancel_reason}"
                    continue

            remaining = leg["qty"] - leg["filled_qty"]
            if remaining <= 0:
                leg["outcome"] = LEG_FILLED
                continue

            quote: Dict[str, Any] = {}
            if leg.get("token"):
                try:
                    quote = (await client.get_quotes(leg["exch"], leg["token"])) or {}
                except Exception:
                    quote = {}
            prc = _leg_price(leg["netqty"], leg.get("ref"), band, leg["tick"], quote)
            attempt: Dict[str, Any] = {
                "pass": pass_no + 1, "band_pct": band, "prc": prc,
                "norenordno": None, "placed": False, "status": None,
                "reason": None, "filled": 0,
            }
            leg["attempts"].append(attempt)
            if prc is None:
                attempt["reason"] = "no usable price (stale lp, no quote)"
                continue  # a later pass may get a quote

            cid = new_client_order_id()
            # The cid is recorded BEFORE transmitting: if place_order raises
            # after the broker accepted (lost ack), the next pass resolves the
            # attempt against the book via remarks==cid instead of re-placing.
            attempt["cid"] = cid
            intent = OrderIntent(
                client_order_id=cid,
                trantype="S" if leg["netqty"] > 0 else "B",
                prctyp="LMT",
                exch=leg["exch"],
                tsym=leg["tsym"],
                qty=remaining,
                prc=prc,
                prd=leg["prd"],
                ret="DAY",
                trgprc=None,
                remarks=cid,
            )
            try:
                result = await client.place_order(intent)
            except Exception as exc:
                attempt["reason"] = str(exc)  # transport error → retry next pass
                continue
            if result.ok:
                attempt["placed"] = True
                attempt["norenordno"] = result.norenordno
            else:
                attempt["reason"] = result.rejreason or "place_order returned ok=False"
                leg["outcome"] = LEG_REJECTED
                leg["reason"] = attempt["reason"]

        if not any(l["outcome"] is None for l in legs):
            break

        # Verification poll — classify this pass's placements.
        try:
            await _sleep(poll_seconds)
        except Exception:
            pass
        try:
            book = await client.order_book()
        except Exception:
            book = []
        by_no = {str(o.get("norenordno")): o for o in (book or [])}
        for leg in [l for l in legs if l["outcome"] is None]:
            att = leg["attempts"][-1] if leg["attempts"] else None
            if att is None or not att.get("norenordno"):
                continue
            row = by_no.get(str(att["norenordno"]))
            if row is None:
                continue  # not visible yet — next pass re-checks post-cancel
            status = _normalize_status(row.get("status"))
            att["status"] = status
            att["filled"] = max(int(att.get("filled") or 0),
                                _parse_netqty(row.get("fillshares")) or 0)
            leg["filled_qty"] = sum(int(a.get("filled") or 0) for a in leg["attempts"])
            if status in _REJECTED_STATUSES:
                leg["outcome"] = LEG_REJECTED
                leg["reason"] = str(row.get("rejreason") or "rejected (no reason given)")
                att["reason"] = leg["reason"]
            elif status == "COMPLETE" or leg["filled_qty"] >= leg["qty"]:
                leg["outcome"] = LEG_FILLED

    # Final classification for legs the loop couldn't confirm.
    for leg in legs:
        if leg["outcome"] is not None:
            continue
        if leg["filled_qty"] >= leg["qty"]:
            leg["outcome"] = LEG_FILLED
        elif any(a.get("placed") for a in leg["attempts"]):
            leg["outcome"] = LEG_UNCONFIRMED
            leg["reason"] = (f"unfilled after {len(band_schedule)} pass(es); "
                             "most aggressive exit left working")
        else:
            leg["outcome"] = LEG_FAILED
            last = leg["attempts"][-1] if leg["attempts"] else {}
            leg["reason"] = str(last.get("reason") or "no attempt succeeded")

    # Step 3 — the broker's truth: is the account actually flat?
    residual: List[Dict[str, Any]] = []
    all_flat: Optional[bool] = None
    try:
        final_positions = await client.position_book()
        residual = [
            {"tsym": p.get("tsym"), "netqty": p.get("netqty")}
            for p in (final_positions or [])
            if (_parse_netqty(p.get("netqty")) or 0) != 0
        ]
        all_flat = residual == []
    except Exception as exc:
        residual = [{"tsym": "(position re-check failed)", "netqty": str(exc)}]
        all_flat = None

    unpriced = [{"tsym": l["tsym"], "netqty": l["netqty"]}
                for l in legs if l["outcome"] == LEG_UNPRICED]
    flatten_failures = [{"tsym": l["tsym"], "netqty": l["netqty"], "reason": l["reason"]}
                        for l in legs if l["outcome"] in (LEG_REJECTED, LEG_FAILED)]
    flattened = sum(1 for l in legs if any(a.get("placed") for a in l["attempts"]))
    filled = sum(1 for l in legs if l["outcome"] == LEG_FILLED)
    pending = sum(1 for l in legs if l["outcome"] == LEG_UNCONFIRMED)

    return {
        "canceled": canceled,
        "cancel_failures": cancel_failures,
        "flattened": flattened,
        "flatten_failures": flatten_failures,
        "unpriced": unpriced,
        "filled": filled,
        "pending": pending,
        "legs": legs,
        "residual": residual,
        "all_flat": all_flat,
        "total": (cancel_failures == [] and flatten_failures == []
                  and unpriced == [] and all_flat is True),
    }


# ---------------------------------------------------------------------------
# 5. Config store — DB-backed singleton (mirror of idempotency.py pattern)
# ---------------------------------------------------------------------------

class SafetyConfigStore:
    """Async config store for the live-trading safety guardrails.

    Backed by any async collection that exposes find_one / update_one with upsert.
    Production code uses ``default_store()``; tests inject a ``FakeAsyncCollection``.

    The document always has ``_id="singleton"`` — there is exactly one config
    doc per deployment (the safety config is global, not per-strategy).

    Known / whitelisted keys
    ------------------------
    ``put_config`` accepts only the three numeric/threshold keys:
    ``daily_loss_limit``, ``profit_lock_target``, ``max_open_positions``.
    ``blocked_until_reset`` is deliberately excluded — the latch is controlled
    ONLY by ``trip()``/``reset()`` and the dedicated ``POST /safety-config/reset-latch``
    route.  Passing ``blocked_until_reset`` to ``put_config`` raises ``ValueError``.
    """

    _SINGLETON_ID = "singleton"
    # All keys that exist in the config doc (for get_config merge).
    _ALL_KEYS = frozenset(DEFAULT_SAFETY_CONFIG)
    # Keys that PUT /safety-config may write.  blocked_until_reset is excluded —
    # it is controlled exclusively by trip()/reset() and the reset-latch route.
    _PUT_KEYS = _PUT_CONFIG_WHITELIST

    def __init__(self, collection: Any) -> None:
        self._col = collection

    async def get_config(self) -> Dict[str, Any]:
        """Return the current config, merging with defaults so missing keys are
        always present.  Never returns None."""
        doc = await self._col.find_one({"_id": self._SINGLETON_ID})
        merged = dict(DEFAULT_SAFETY_CONFIG)
        if doc:
            for k in self._ALL_KEYS:
                if k in doc:
                    merged[k] = doc[k]
        return merged

    async def put_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Persist whitelisted threshold-key updates.

        Raises ValueError on unknown keys OR if ``blocked_until_reset`` is
        included — the latch must only be changed via ``trip()``/``reset()``.
        """
        # F3: blocked_until_reset is NOT in _PUT_KEYS so it will be caught here.
        unknown = set(updates) - self._PUT_KEYS
        if unknown:
            raise ValueError(
                f"Unknown safety config keys (or non-whitelisted — "
                f"blocked_until_reset requires reset() / POST /safety-config/reset-latch): "
                f"{sorted(unknown)}"
            )
        # Validate max_lots_per_order: must be a non-bool int >= 1.
        if "max_lots_per_order" in updates:
            v = updates["max_lots_per_order"]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(
                    f"max_lots_per_order must be an int >= 1, got {v!r}"
                )
        await self._col.update_one(
            {"_id": self._SINGLETON_ID},
            {"$set": updates},
            upsert=True,
        )
        return await self.get_config()

    async def _write_latch(self, value: bool) -> Dict[str, Any]:
        """Internal: persist the latch directly, bypassing put_config whitelist."""
        # F3: coerce to strict bool so 0/""/None can't leak in.
        await self._col.update_one(
            {"_id": self._SINGLETON_ID},
            {"$set": {"blocked_until_reset": bool(value)}},
            upsert=True,
        )
        return await self.get_config()

    async def trip(self) -> Dict[str, Any]:
        """Persist the blocked_until_reset=True latch.

        This is the ONLY authorised way to set the latch to True.
        """
        return await self._write_latch(True)

    async def reset(self) -> Dict[str, Any]:
        """Persist the blocked_until_reset=False latch (explicit operator reset).

        This is the ONLY authorised way to clear the latch.  It is also called
        by ``POST /safety-config/reset-latch``.
        """
        return await self._write_latch(False)


def default_store() -> "SafetyConfigStore":
    """Return a SafetyConfigStore backed by the production Mongo collection.

    Deferred import keeps this file host-testable without a running Mongo instance.
    """
    from app.db import get_db  # type: ignore[import]

    return SafetyConfigStore(get_db().live_safety_config)
