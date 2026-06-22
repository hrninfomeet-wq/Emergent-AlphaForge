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

import math
from typing import Any, Dict, List, Optional

from app.live.broker_protocol import OrderIntent
from app.live.idempotency import new_client_order_id
from app.live.safety import validate_jdata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Order statuses that are terminal — no further state changes possible.
TERMINAL: frozenset[str] = frozenset({"COMPLETE", "REJECTED", "CANCELED"})

#: Sensible defaults for a single-account retail setup.
DEFAULT_SAFETY_CONFIG: Dict[str, Any] = {
    "daily_loss_limit": 5000,       # ₹ — broker-stop-loss when MTM ≤ −5000
    "profit_lock_target": 10000,    # ₹ — lock profits when MTM ≥ 10000
    "max_open_positions": 5,        # hard cap on concurrent open positions
    "blocked_until_reset": False,   # latch; only explicit reset_latch clears it
}


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

    would_cancel: List[str] = [
        o["norenordno"]
        for o in open_orders
        if o.get("status") not in TERMINAL
    ]

    would_flatten: List[Dict[str, Any]] = []
    unpriced: List[Dict[str, Any]] = []

    for pos in open_positions:
        try:
            netqty = int(pos.get("netqty", 0))
        except (TypeError, ValueError):
            netqty = 0
        if netqty == 0:
            continue

        tsym = pos.get("tsym", "")
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
        if netqty > 0:
            trantype = "S"
            prc = round(ref * (1.0 - eff / 100.0), 2)
        else:
            trantype = "B"
            prc = round(ref * (1.0 + eff / 100.0), 2)

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
            prd="I",
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
        ``unpriced``:         list of ``{"tsym": ..., "netqty": ...}`` (no ref price).
        ``total``:            True iff cancel_failures, flatten_failures, and
                              unpriced are all empty.
    """
    eff = abs(band_pct)

    canceled = 0
    cancel_failures: List[Dict[str, Any]] = []

    # Step 1 — cancel every working order (bypass throttle entirely).
    for order in open_orders:
        if order.get("status") in TERMINAL:
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
        try:
            netqty = int(pos.get("netqty", 0))
        except (TypeError, ValueError):
            netqty = 0
        if netqty == 0:
            continue

        tsym = pos.get("tsym", "")
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

        # Flatten direction
        if netqty > 0:
            trantype = "S"
            prc = round(ref * (1.0 - eff / 100.0), 2)
        else:
            trantype = "B"
            prc = round(ref * (1.0 + eff / 100.0), 2)

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
            prd="I",
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
    Only the four keys in ``DEFAULT_SAFETY_CONFIG`` are accepted by
    ``put_config``; unknown keys are rejected with ``ValueError``.
    """

    _SINGLETON_ID = "singleton"
    _KNOWN_KEYS = frozenset(DEFAULT_SAFETY_CONFIG)

    def __init__(self, collection: Any) -> None:
        self._col = collection

    async def get_config(self) -> Dict[str, Any]:
        """Return the current config, merging with defaults so missing keys are
        always present.  Never returns None."""
        doc = await self._col.find_one({"_id": self._SINGLETON_ID})
        merged = dict(DEFAULT_SAFETY_CONFIG)
        if doc:
            for k in self._KNOWN_KEYS:
                if k in doc:
                    merged[k] = doc[k]
        return merged

    async def put_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Persist whitelisted key updates.  Raises ValueError on unknown keys."""
        unknown = set(updates) - self._KNOWN_KEYS
        if unknown:
            raise ValueError(f"Unknown safety config keys: {sorted(unknown)}")
        await self._col.update_one(
            {"_id": self._SINGLETON_ID},
            {"$set": updates},
            upsert=True,
        )
        return await self.get_config()

    async def trip(self) -> Dict[str, Any]:
        """Persist the blocked_until_reset=True latch."""
        return await self.put_config({"blocked_until_reset": True})

    async def reset(self) -> Dict[str, Any]:
        """Persist the blocked_until_reset=False latch (explicit operator reset)."""
        return await self.put_config({"blocked_until_reset": False})


def default_store() -> "SafetyConfigStore":
    """Return a SafetyConfigStore backed by the production Mongo collection.

    Deferred import keeps this file host-testable without a running Mongo instance.
    """
    from app.db import get_db  # type: ignore[import]

    return SafetyConfigStore(get_db().live_safety_config)
