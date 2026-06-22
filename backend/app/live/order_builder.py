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
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.live.broker_protocol import OrderIntent
from app.live.flattrade_symbol import SymbolResolutionError, resolve
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
        # Marketable LMT: cross slightly above (BUY) or below (SELL) ref_ltp.
        if side == "B":
            prc = round(ref_ltp * (1.0 + eff / 100.0), 2)
        else:
            prc = round(ref_ltp * (1.0 - eff / 100.0), 2)
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
        trgprc = stop  # EXACT parity with execution_policy (audit requirement)
        # Limit price: one tick through the trigger, clamped to exchange floor.
        prc = max(_STOP_FLOOR, round(stop - 0.05, 2))
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
