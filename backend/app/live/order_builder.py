"""Pure order builder — marketable-limit + execution_policy stop parity (L1.3).

``build_intent`` is the ONLY place where an OrderIntent is constructed for live
trading.  It:

- Resolves the Noren symbol via flattrade_symbol.resolve() (sync search_fn injected).
- Computes the marketable limit price with a CLAMPED cross buffer (eff = min(buffer, band))
  so the buffer can NEVER exceed the price-band guard.
- For stop orders derives trgprc from resolve_premium_levels() with the exact same
  stop_floor=0.05/ndigits=2 parameters used by the backtest engine (byte-for-byte parity).
- Sets remarks = client_order_id on every intent (required by the L1.2 engine for
  broker-side reconciliation via the remarks echo field).
- Runs ALL four safety checks (price_finite, price_band, fat_finger, validate_jdata) and
  returns a full verdicts list regardless of outcome so dry-run callers can see reasoning.

Returns
-------
(intent | None, verdicts, resolved_lot_size | None)
    intent is None if any check failed; verdicts always has one entry per check.
    resolved_lot_size is the broker-authoritative lot size (from the scrip ls field) on
    success, or None on any failure (including symbol resolution failure).
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN
from typing import Any, Callable, Dict, List, Optional, Tuple, Literal

from app.live.broker_protocol import (
    ALLOWED_PRD,
    ALLOWED_RET,
    OrderIntent,
)
from app.live.flattrade_symbol import SymbolResolutionError, resolve, rules_for
from app.execution_policy import resolve_premium_levels
from app.live.safety import check_fat_finger, check_price_band, validate_jdata

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Verdict = Dict[str, Any]  # {"check": str, "ok": bool, "detail": str}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_BUFFER_PCT = 0.5   # default marketable cross buffer
_STOP_FLOOR = 0.05          # exchange floor for option premium
_STOP_NDIGITS = 2           # rounding for stop/trigger prices


# ---------------------------------------------------------------------------
# Tick-size rounding
# ---------------------------------------------------------------------------

def round_to_tick(
    price: float,
    tick: float,
    *,
    mode: Literal["nearest", "up", "down"] = "nearest",
) -> float:
    """Round ``price`` to the nearest multiple of ``tick``.

    Uses ``decimal.Decimal`` arithmetic (via ``str()`` coercion) to avoid
    floating-point precision artifacts such as ``math.floor(65.3 / 0.05)``
    yielding 1305 instead of 1306 due to IEEE-754 rounding.

    Parameters
    ----------
    price:
        The raw price to round.
    tick:
        The tick size (e.g. 0.05 for NIFTY options).  Must be > 0.
        If tick <= 0, falls back to ``round(price, 2)`` (no-op guard).
    mode:
        ``"nearest"`` (default) — round to nearest tick (ROUND_HALF_UP).
        ``"up"``     — ceiling to next tick multiple (keeps BUY marketable).
        ``"down"``   — floor  to prev tick multiple (keeps SELL marketable).

    Returns
    -------
    float
        Price rounded to exactly ``ndigits=2`` decimal places so there are
        no float-representation artifacts (e.g. 65.35000000001).

    Examples
    --------
    >>> round_to_tick(65.325, 0.05, mode="up")
    65.35
    >>> round_to_tick(65.325, 0.05, mode="down")
    65.3
    >>> round_to_tick(65.325, 0.05, mode="nearest")
    65.35
    """
    if tick <= 0:
        return round(price, 2)
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick))
    if mode == "up":
        rounding = ROUND_UP
    elif mode == "down":
        rounding = ROUND_DOWN
    else:  # "nearest"
        rounding = ROUND_HALF_UP
    multiplier = (d_price / d_tick).quantize(Decimal("1"), rounding=rounding)
    return round(float(multiplier * d_tick), 2)


def slice_to_freeze(qty: int, freeze_qty: int) -> List[int]:
    """Split ``qty`` into child orders each no larger than the exchange freeze qty.

    The exchange/broker rejects any single order whose quantity exceeds the
    instrument's freeze quantity, so a large order MUST be sent as multiple child
    orders — the API does NOT auto-slice. The children sum to ``qty``; each is
    ``<= freeze_qty`` (the last carries the remainder).

    Hard-rejects ``qty > 10 * freeze_qty`` (a sanity cap — that many lots is a
    fat-finger, not a real order). ``qty <= 0`` returns ``[]``.

    Raises ValueError on a non-positive-int freeze_qty / non-int qty / the cap.
    """
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise ValueError(f"qty must be an int, got {qty!r}")
    if not isinstance(freeze_qty, int) or isinstance(freeze_qty, bool) or freeze_qty <= 0:
        raise ValueError(f"freeze_qty must be a positive int, got {freeze_qty!r}")
    if qty <= 0:
        return []
    if qty > 10 * freeze_qty:
        raise ValueError(
            f"qty {qty} exceeds 10x the freeze quantity ({freeze_qty}) — rejected as a fat-finger"
        )
    n = math.ceil(qty / freeze_qty)
    children = [freeze_qty] * (n - 1)
    children.append(qty - freeze_qty * (n - 1))
    return children


def _v(check: str, ok: bool, detail: str) -> Verdict:
    return {"check": check, "ok": ok, "detail": detail}


def _fail(verdicts: List[Verdict], check: str, detail: str) -> Tuple[None, List[Verdict], None]:
    verdicts.append(_v(check, False, detail))
    return None, verdicts, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_intent(
    contract: Dict[str, Any],
    *,
    side: str,
    order_kind: str,
    lots: int,
    ref_ltp: float,
    band_pct: float,
    fat_finger_cap: Any,
    levels: Dict[str, Any],
    client_order_id: str,
    buffer_pct: Optional[float] = None,
    search_fn: Callable[[str, str], List[Dict[str, Any]]],
) -> Tuple[Optional[OrderIntent], List[Verdict], Optional[int]]:
    """Build and safety-check a live OrderIntent.

    Parameters
    ----------
    contract:
        option_contract dict (underlying/strike/side/expiry_date/lot_size).
    side:
        "B" (buy) or "S" (sell) — the Noren trantype.
    order_kind:
        "entry" | "exit"  → marketable LMT order.
        "stop"            → SL-LMT order with trgprc from resolve_premium_levels().
    lots:
        Number of lots (multiplied by resolved lot_size to get qty).
    ref_ltp:
        Latest traded price used for price-band and buffer calculation.
    band_pct:
        Max allowed % deviation from ref_ltp (price-band guard).
    fat_finger_cap:
        Maximum lots allowed; None = default-deny.
    levels:
        Dict with optional keys: stop_pts, stop_pct, target_pts, target_pct.
        Passed through to resolve_premium_levels for stop orders.
    client_order_id:
        Stable id minted by idempotency.new_client_order_id(); written to
        intent.remarks so the broker echoes it back for reconciliation.
    buffer_pct:
        Marketable cross buffer (default 0.5%).  CLAMPED by band_pct so it
        can NEVER produce a price outside the band guard.
    search_fn:
        Sync callable(exch, query) -> list[scrip_dict] injected for symbol resolution.

    Returns
    -------
    (intent | None, verdicts, resolved_lot_size | None)
        intent is None if any check fails.  verdicts always contains one entry
        per check run so the dry-run route can surface full reasoning.
        resolved_lot_size is the broker-authoritative lot (scrip ls) on success,
        None on any failure.
    """
    verdicts: List[Verdict] = []
    buf = buffer_pct if buffer_pct is not None else _DEFAULT_BUFFER_PCT

    # ------------------------------------------------------------------
    # Step 1 — resolve symbol
    # ------------------------------------------------------------------
    try:
        resolved = resolve(contract, search_fn=search_fn)
    except SymbolResolutionError as exc:
        return _fail(verdicts, "symbol", str(exc))
    except Exception as exc:
        return _fail(verdicts, "symbol", f"unexpected resolution error: {exc}")

    verdicts.append(_v("symbol", True, f"resolved {resolved['tsym']} on {resolved['exch']}"))
    lot_size: int = resolved["lot_size"]
    tick: float = resolved.get("tick", 0.05)
    qty = lots * lot_size

    # ------------------------------------------------------------------
    # Step 1b — validate ref_ltp before any price arithmetic
    # ------------------------------------------------------------------
    # L2.2 panic_squareoff calls build_intent in-process (no Pydantic gate),
    # so we must guard against None/str/nan/inf explicitly — fail CLOSED.
    if not (
        isinstance(ref_ltp, (int, float))
        and not isinstance(ref_ltp, bool)
        and math.isfinite(ref_ltp)
        and ref_ltp > 0
    ):
        return _fail(
            verdicts,
            "ref_ltp",
            f"ref_ltp={ref_ltp!r} is not a finite positive number",
        )

    # ------------------------------------------------------------------
    # Step 2 — compute price
    # ------------------------------------------------------------------
    # Clamp buffer so it can NEVER exceed the band (guarantees price_band passes
    # for the buffer alone — the band check still runs as an independent guard).
    eff = min(abs(buf), abs(band_pct))

    if order_kind in ("entry", "exit"):
        # Marketable LMT: cross slightly above (BUY) or below (SELL) ref_ltp,
        # then round DIRECTIONALLY so the price stays marketable and is a valid
        # tick multiple (broker rejects non-multiples).
        if side == "B":
            raw_prc = round(ref_ltp * (1.0 + eff / 100.0), 2)
            prc = round_to_tick(raw_prc, tick, mode="up")
        else:
            raw_prc = round(ref_ltp * (1.0 - eff / 100.0), 2)
            prc = round_to_tick(raw_prc, tick, mode="down")
        prctyp = "LMT"
        trgprc = None

    elif order_kind == "stop":
        # SL-LMT: derive trigger from resolve_premium_levels (exact parity).
        stop, _ = resolve_premium_levels(
            ref_ltp,
            stop_pts=levels.get("stop_pts"),
            stop_pct=levels.get("stop_pct"),
            stop_floor=_STOP_FLOOR,
            ndigits=_STOP_NDIGITS,
        )
        if stop is None:
            return _fail(
                verdicts, "stop",
                "resolve_premium_levels returned None for stop — check stop_pts/stop_pct in levels"
            )
        # trgprc: nearest tick (the stop level is already broker-computed)
        trgprc = round_to_tick(stop, tick, mode="nearest")
        # Limit price: one tick through the trigger, clamped to exchange floor.
        # Round DOWN (sell-to-close stop sits at/below the trigger).
        prc = round_to_tick(max(_STOP_FLOOR, round(trgprc - tick, 2)), tick, mode="down")
        prc = max(_STOP_FLOOR, prc)
        prctyp = "SL-LMT"

    else:
        return _fail(verdicts, "order_kind", f"unknown order_kind {order_kind!r}; expected entry/exit/stop")

    # ------------------------------------------------------------------
    # Step 3 — build OrderIntent (remarks = client_order_id, REQUIRED)
    # ------------------------------------------------------------------
    intent = OrderIntent(
        client_order_id=client_order_id,
        trantype=side,
        prctyp=prctyp,
        exch=resolved["exch"],
        tsym=resolved["tsym"],
        qty=qty,
        prc=prc,
        prd="I",
        ret="DAY",
        trgprc=trgprc,
        remarks=client_order_id,  # broker must echo cid for resume-reconcile (L1.2)
    )

    # ------------------------------------------------------------------
    # Step 4 — safety checks (ALL run; first failure returns None)
    # ------------------------------------------------------------------
    # 4a. price finite/positive (pre-check before band — gives a clear verdict name)
    if not (math.isfinite(prc) and prc > 0):
        return _fail(verdicts, "price_finite", f"computed prc={prc!r} is not finite/positive")
    verdicts.append(_v("price_finite", True, f"prc={prc}"))

    # 4b. price band
    pb_ok, pb_reason = check_price_band(prc, ref_ltp, band_pct)
    verdicts.append(_v("price_band", pb_ok, pb_reason or f"prc={prc} within {band_pct}% of ref={ref_ltp}"))
    if not pb_ok:
        return None, verdicts, None

    # 4c. fat-finger cap
    ff_ok, ff_reason = check_fat_finger(lots, fat_finger_cap)
    verdicts.append(_v("fat_finger", ff_ok, ff_reason or f"lots={lots} <= cap={fat_finger_cap}"))
    if not ff_ok:
        return None, verdicts, None

    # 4d. validate_jdata (prctyp/prd/ret/qty/prc/trgprc for SL-LMT)
    jd_ok, jd_reason = validate_jdata(intent, lot_size=lot_size)
    verdicts.append(_v("jdata", jd_ok, jd_reason or "OrderIntent fields valid"))
    if not jd_ok:
        return None, verdicts, None

    return intent, verdicts, lot_size


# ---------------------------------------------------------------------------
# Choke-point: validate_and_build (P1.3)
# ---------------------------------------------------------------------------
# Every live order — direct ticket OR strategy-deployed — flows through
# validate_and_build so that exchange rules, tick-rounding, freeze-qty splitting
# and product-pinning can NEVER be bypassed. It generalises build_intent for
# multi-child orders + exchange-aware order types (LIMIT/MARKET/SL-LMT).

# Exchange-neutral ticket order_type -> Noren prctyp.
_ORDER_TYPE_TO_PRCTYP = {"LIMIT": "LMT", "MARKET": "MKT", "SL-LMT": "SL-LMT"}
# Ticket product -> Noren prd code (MIS=intraday I, NRML=carryforward M).
_PRODUCT_TO_PRD = {"MIS": "I", "NRML": "M"}
# The choke-point's OWN prctyp allow-list. Distinct from broker_protocol.ALLOWED_PRCTYP
# (the strict L1/L2 gate, which excludes MKT): the live-order-page deliberately supports
# MARKET orders, so MKT is permitted here and carries prc=0 (validated below).
_CHOKE_PRCTYP = ("LMT", "SL-LMT", "MKT")


def _fail2(verdicts: List[Verdict], check: str, detail: str) -> Tuple[None, List[Verdict]]:
    """Append a failing verdict and return the (None, verdicts) 2-tuple."""
    verdicts.append(_v(check, False, detail))
    return None, verdicts


def _validate_child_intent(
    intent: OrderIntent, *, freeze_qty: int, is_market: bool
) -> Tuple[bool, Optional[str]]:
    """Field-level validation for one freeze child.

    Unlike validate_jdata, a freeze child qty is NOT required to be a lot
    multiple — 1800 units of a 65-lot instrument is a valid child carved out of
    a lot-multiple parent. The PARENT qty carries the lot-multiple invariant
    (checked once in validate_and_build); each CHILD must only be a positive int
    no larger than the freeze cap, with valid prctyp/prd/ret, a finite positive
    trigger for SL-LMT, and a price that is exactly 0 for MARKET or finite
    positive otherwise.
    """
    if not (
        isinstance(intent.qty, int)
        and not isinstance(intent.qty, bool)
        and 0 < intent.qty <= freeze_qty
    ):
        return False, f"child qty {intent.qty!r} must be a positive int <= freeze {freeze_qty}"
    if intent.prctyp not in _CHOKE_PRCTYP:
        return False, f"prctyp {intent.prctyp!r} not allowed; permitted {_CHOKE_PRCTYP}"
    if intent.prd not in ALLOWED_PRD:
        return False, f"prd {intent.prd!r} not allowed; permitted {ALLOWED_PRD}"
    if intent.ret not in ALLOWED_RET:
        return False, f"ret {intent.ret!r} not allowed; permitted {ALLOWED_RET}"
    if intent.prctyp == "SL-LMT":
        if not (
            isinstance(intent.trgprc, (int, float))
            and not isinstance(intent.trgprc, bool)
            and math.isfinite(intent.trgprc)
            and intent.trgprc > 0
        ):
            return False, f"SL-LMT requires a finite positive trgprc, got {intent.trgprc!r}"
    if is_market:
        # MARKET carries prc=0 (no limit price). Reject anything else.
        if intent.prc not in (0, 0.0):
            return False, f"MARKET order must carry prc=0, got {intent.prc!r}"
    else:
        if not (
            isinstance(intent.prc, (int, float))
            and not isinstance(intent.prc, bool)
            and math.isfinite(intent.prc)
            and intent.prc > 0
        ):
            return False, f"prc must be finite positive, got {intent.prc!r}"
    return True, None


def validate_and_build(
    ticket: Dict[str, Any]
) -> Tuple[Optional[List[OrderIntent]], List[Verdict]]:
    """The single order CHOKE-POINT: validate a ticket and build child OrderIntents.

    Returns (child_intents, verdicts) on success or (None, verdicts) on the first
    failing check. ``verdicts`` always carries one entry per check run so the
    dry-run UI can surface full reasoning.

    ticket keys
    -----------
    underlying, strike, option_type ("CE"/"PE"), side ("B"/"S"), expiry_date,
    lots (int), order_type ("LIMIT"/"MARKET"/"SL-LMT"), product ("MIS"/"NRML"),
    ref_ltp, band_pct, fat_finger_cap, levels (dict, SL-LMT stop), client_order_id,
    buffer_pct, search_fn.

    Guarantees on a successful return
    ---------------------------------
    - order_type and product are PERMITTED on the instrument's exchange
      (CO/BO blocked everywhere, SL-MKT blocked, CO/BO especially on BFO/SENSEX).
    - every non-MARKET child price (and any SL-LMT trigger) is an exact multiple
      of the broker-authoritative tick.
    - parent qty is a positive-int multiple of the resolved lot size; it is split
      into children each <= the exchange freeze quantity.
    - prd is PINNED from the validated product on every child (never defaulted).
    """
    verdicts: List[Verdict] = []

    underlying = ticket.get("underlying")
    rules = rules_for(underlying)
    if rules is None:
        return _fail2(verdicts, "underlying", f"unknown underlying {underlying!r}")

    order_type = str(ticket.get("order_type") or "").strip().upper()
    product = str(ticket.get("product") or "").strip().upper()

    # ------------------------------------------------------------------
    # Exchange rules — order_type + product must be permitted on this exchange.
    # ------------------------------------------------------------------
    if order_type not in rules["price_types"]:
        return _fail2(
            verdicts, "exchange_order_type",
            f"order_type {order_type!r} not permitted for {underlying} on "
            f"{rules['exch']}; allowed: {rules['price_types']}",
        )
    if product not in rules["products"]:
        return _fail2(
            verdicts, "exchange_product",
            f"product {product!r} not permitted for {underlying} on "
            f"{rules['exch']}; allowed: {rules['products']}",
        )
    prctyp = _ORDER_TYPE_TO_PRCTYP[order_type]
    prd = _PRODUCT_TO_PRD[product]
    is_market = prctyp == "MKT"
    verdicts.append(_v("exchange", True, f"{order_type}/{product} permitted on {rules['exch']}"))

    # ------------------------------------------------------------------
    # Direction — trantype must be B or S (resolve() validates CE/PE separately).
    # ------------------------------------------------------------------
    side = str(ticket.get("side") or "").strip().upper()
    if side not in ("B", "S"):
        return _fail2(verdicts, "side", f"side must be 'B' or 'S', got {side!r}")

    # ------------------------------------------------------------------
    # Resolve symbol — authoritative tick + lot_size + tsym + exch.
    # ------------------------------------------------------------------
    option_type = str(ticket.get("option_type") or "").strip().upper()
    contract = {
        "underlying": underlying,
        "strike": ticket.get("strike"),
        "side": option_type,            # resolve() uses 'side' for the CE/PE leg
        "expiry_date": ticket.get("expiry_date"),
        "lot_size": rules["lot_size"],  # advisory; broker scrip ls wins
    }
    try:
        resolved = resolve(contract, search_fn=ticket.get("search_fn"))
    except SymbolResolutionError as exc:
        return _fail2(verdicts, "symbol", str(exc))
    except Exception as exc:  # never leak a raw error out of the choke-point
        return _fail2(verdicts, "symbol", f"unexpected resolution error: {exc}")
    verdicts.append(_v("symbol", True, f"resolved {resolved['tsym']} on {resolved['exch']}"))

    lot_size: int = resolved["lot_size"]
    tick: float = resolved.get("tick", 0.05)
    exch: str = resolved["exch"]
    tsym: str = resolved["tsym"]

    # ------------------------------------------------------------------
    # Quantity — PARENT qty is the lot-multiple invariant; children are split.
    # ------------------------------------------------------------------
    lots = ticket.get("lots")
    if not (isinstance(lots, int) and not isinstance(lots, bool) and lots > 0):
        return _fail2(verdicts, "qty", f"lots must be a positive int, got {lots!r}")

    # fat-finger on PARENT lots runs BEFORE the freeze split so an oversized order
    # gets a clean fat_finger rejection rather than the slice's internal 10x cap.
    ff_ok, ff_reason = check_fat_finger(lots, ticket.get("fat_finger_cap"))
    verdicts.append(_v("fat_finger", ff_ok, ff_reason or f"lots={lots} <= cap={ticket.get('fat_finger_cap')}"))
    if not ff_ok:
        return None, verdicts

    qty = lots * lot_size
    if qty % lot_size != 0:  # invariant guard (always true by construction)
        return _fail2(verdicts, "qty", f"parent qty {qty} is not a multiple of lot_size {lot_size}")
    try:
        child_qtys = slice_to_freeze(qty, rules["freeze_qty"])
    except ValueError as exc:
        return _fail2(verdicts, "qty", str(exc))
    if not child_qtys:
        return _fail2(verdicts, "qty", f"qty {qty} produced no child orders")
    verdicts.append(_v(
        "qty", True,
        f"{lots} lot(s) x {lot_size} = {qty}; split into {len(child_qtys)} child "
        f"order(s) <= freeze {rules['freeze_qty']}: {child_qtys}",
    ))

    # ------------------------------------------------------------------
    # Price — identical across children; only qty differs.
    # ------------------------------------------------------------------
    ref_ltp = ticket.get("ref_ltp")
    band_pct = ticket.get("band_pct")
    buf = ticket.get("buffer_pct")
    buf = buf if buf is not None else _DEFAULT_BUFFER_PCT
    levels = ticket.get("levels") or {}
    trgprc: Optional[float] = None

    def _ref_ok() -> bool:
        return (
            isinstance(ref_ltp, (int, float))
            and not isinstance(ref_ltp, bool)
            and math.isfinite(ref_ltp)
            and ref_ltp > 0
        )

    if is_market:
        prc = 0.0
    elif prctyp == "LMT":
        if not _ref_ok():
            return _fail2(verdicts, "price_finite", f"ref_ltp={ref_ltp!r} is not a finite positive number")
        band_ok = (
            isinstance(band_pct, (int, float)) and not isinstance(band_pct, bool)
            and math.isfinite(band_pct)
        )
        # Clamp the marketable buffer by the band so it can NEVER breach the guard.
        eff = min(abs(buf), abs(band_pct)) if band_ok else abs(buf)
        if side == "B":
            prc = round_to_tick(round(ref_ltp * (1.0 + eff / 100.0), 2), tick, mode="up")
        else:
            prc = round_to_tick(round(ref_ltp * (1.0 - eff / 100.0), 2), tick, mode="down")
    else:  # SL-LMT
        if not _ref_ok():
            return _fail2(verdicts, "price_finite", f"ref_ltp={ref_ltp!r} is not a finite positive number")
        stop, _target = resolve_premium_levels(
            ref_ltp,
            stop_pts=levels.get("stop_pts"),
            stop_pct=levels.get("stop_pct"),
            stop_floor=_STOP_FLOOR,
            ndigits=_STOP_NDIGITS,
        )
        if stop is None:
            return _fail2(
                verdicts, "stop",
                "resolve_premium_levels returned None for stop — check stop_pts/stop_pct in levels",
            )
        trgprc = round_to_tick(stop, tick, mode="nearest")
        if side == "S":  # protective sell-stop: limit one tick below trigger, floored
            prc = round_to_tick(max(_STOP_FLOOR, round(trgprc - tick, 2)), tick, mode="down")
            prc = max(_STOP_FLOOR, prc)
        else:            # buy stop-entry: limit one tick above trigger
            prc = round_to_tick(round(trgprc + tick, 2), tick, mode="up")

    # ------------------------------------------------------------------
    # price_finite + price_band verdicts.
    #   MARKET  → both skipped (prc=0 is valid, has no limit to band-check).
    #   SL-LMT  → finite check runs; band SKIPPED (the trigger is off-market by
    #             design — a band check would reject legitimate stops; the stop
    #             level is derived from ref_ltp so it cannot be fat-fingered).
    #   LIMIT   → both run.
    # ------------------------------------------------------------------
    if is_market:
        verdicts.append(_v("price_finite", True, "MARKET — prc=0 (finite/band not applicable)"))
        verdicts.append(_v("price_band", True, "MARKET — price-band not applicable"))
    else:
        if not (math.isfinite(prc) and prc > 0):
            return _fail2(verdicts, "price_finite", f"computed prc={prc!r} is not finite/positive")
        verdicts.append(_v(
            "price_finite", True,
            f"prc={prc}" + (f", trgprc={trgprc}" if trgprc is not None else ""),
        ))
        if prctyp == "LMT":
            pb_ok, pb_reason = check_price_band(prc, ref_ltp, band_pct)
            verdicts.append(_v("price_band", pb_ok, pb_reason or f"prc={prc} within {band_pct}% of ref={ref_ltp}"))
            if not pb_ok:
                return None, verdicts
        else:  # SL-LMT
            verdicts.append(_v("price_band", True, "SL-LMT — trigger off-market by design; band skipped"))

    # ------------------------------------------------------------------
    # Build one OrderIntent per freeze child (prd PINNED; cid suffixed per child).
    # ------------------------------------------------------------------
    cid = str(ticket.get("client_order_id") or "")
    children: List[OrderIntent] = []
    for i, child_qty in enumerate(child_qtys):
        child_cid = f"{cid}-{i}" if cid else f"child-{i}"
        children.append(OrderIntent(
            client_order_id=child_cid,
            trantype=side,
            prctyp=prctyp,
            exch=exch,
            tsym=tsym,
            qty=child_qty,
            prc=prc,
            prd=prd,            # PINNED from the validated product — never defaulted
            ret="DAY",
            trgprc=trgprc,
            remarks=child_cid,
        ))

    # ------------------------------------------------------------------
    # jdata — field-validate EACH child (qty<=freeze, prctyp/prd/ret, trigger, prc).
    # Parent lot-multiple was already enforced; children need not be lot multiples.
    # ------------------------------------------------------------------
    for i, intent in enumerate(children):
        jd_ok, jd_reason = _validate_child_intent(
            intent, freeze_qty=rules["freeze_qty"], is_market=is_market
        )
        if not jd_ok:
            return _fail2(verdicts, "jdata", f"child {i} ({intent.tsym} qty={intent.qty}): {jd_reason}")
    verdicts.append(_v("jdata", True, f"{len(children)} child intent(s) valid"))

    return children, verdicts
