"""Guarded executor — the SOLE entry chokepoint for real orders (L3.4).

Every real order placed against the broker MUST go through
``place_live_test_order``.  No other code in this module calls
``client.place_order``; no other module in the codebase may call it for entry.

Gate chain (EXACTLY this order):
  1. Mode gate  — must be LIVE_TEST with unconsumed single-shot.
  2. Fresh dry-run — build_intent (server-side, lots hard-pinned to 1,
     fat_finger_cap clamped to 1) + margin_verdict; ALL verdicts must pass.
  3. qty == lot_size  — defense-in-depth: confirm the intent carries exactly
     one lot's worth of units regardless of what build_intent computed.
  4. Engine gate — engine.can_trade() must return (True, ...).
  5. Idempotency claim — intent_store.claim_for_submit(cid) must return True.
  6. **THE ONLY place_order CALL** — client.place_order(intent).
  7. Post-fill: mark_submitted, consume_single_shot, then arm-or-abort.

Arm-or-abort invariant
----------------------
After a successful fill the position MUST be protected (SL backstop + auto-
square session armed via the injected ``arm`` callable).  If ANY post-fill step
raises (mark_submitted, consume_single_shot, arm, or the abort path itself),
the executor drives a best-effort square + best-effort halt via
``_abort_protect`` and returns without propagating — no unprotected live
position can persist, and the engine is halted so a human must review.

Lots hard-pinned to 1
---------------------
The ``lots`` parameter is NOT exposed at all.  The executor always passes
``lots=1`` and a clamped fat_finger_cap to ``build_intent``.  Non-numeric
fat_finger_cap values (None, str, …) are treated as absent → default-deny,
not a crash.  There is no way for a caller to inject qty > lot_size.
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.live.mode import is_live_order_allowed
from app.live.order_builder import build_intent
from app.live.margin import margin_verdict
from app.live.idempotency import new_client_order_id
from app.live.auto_square import square_position
from app.live.safety import RateThrottle


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blocked(reason: str, verdicts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build a uniform NOT-placed response without any broker contact."""
    return {
        "placed": False,
        "reason": reason,
        "verdicts": verdicts or [],
    }


async def _abort_protect(
    client: Any,
    engine: Any,
    intent: Any,
    norenordno: str,
    ref_ltp: float,
    band_pct: float,
    uid: str,
    actid: str,
    *,
    reason: str,
) -> Dict[str, Any]:
    """Best-effort square + best-effort halt.  NEVER raises.

    Called whenever the post-fill sequence (mark_submitted, consume_single_shot,
    arm, or a previous abort attempt) raises an exception.  Both square and halt
    are attempted unconditionally and independently; their exceptions are caught
    and surfaced in the return value so the operator can see what happened.

    Returns a dict with ``placed=True, protected=False`` plus ``halted`` (bool),
    ``square_result`` (dict), and ``reason`` (str).
    """
    square_result: Dict[str, Any] = {}
    try:
        square_result = await square_position(
            client,
            {
                "tsym": intent.tsym,
                "exch": intent.exch,
                "netqty": 0,
                "lp": ref_ltp,
                "working_norenordno": norenordno,
            },
            reason="abort",
            band_pct=band_pct,
            uid=uid,
            actid=actid,
        )
    except Exception as sq_exc:
        square_result = {"squared": False, "error": str(sq_exc)}

    halted = False
    try:
        await engine.halt("post_place_protection_failed")
        halted = True
    except Exception:
        halted = False

    return {
        "placed": True,
        "protected": False,
        "halted": halted,
        "norenordno": norenordno,
        "reason": reason,
        "square_result": square_result,
    }


# ---------------------------------------------------------------------------
# Shared transmit core — record_intent → claim → THE place_order → arm-or-abort
# ---------------------------------------------------------------------------

async def _transmit_and_arm(
    intent: Any,
    cid: str,
    *,
    client: Any,
    intent_store: Any,
    engine: Any,
    arm: Callable,
    ref_ltp: float,
    band_pct: float,
    uid: str,
    actid: str,
    verdicts: List[Dict[str, Any]],
    deployment_id: Optional[str] = None,
    post_fill: Optional[Callable[[], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Record-intent → atomic claim → THE ONLY place_order → mark_submitted →
    optional post_fill hook → arm, all under the exception-total abort-protect.

    This is the single shared transmit path used by BOTH entry functions
    (``place_live_test_order`` and ``place_deployed_order``).  There is exactly
    ONE ``client.place_order`` call in this module and it lives here.

    Parameters
    ----------
    intent:
        The fully-built, dry-run-passed OrderIntent to transmit.
    cid:
        The client_order_id minted for this attempt (intent.client_order_id).
    post_fill:
        Optional async callable run AFTER mark_submitted and BEFORE arm.  Used by
        ``place_live_test_order`` to ``consume_single_shot``.  Any exception it
        raises is caught by the abort-protect wrapper exactly like an arm failure.
    deployment_id:
        Forwarded to record_intent for audit/grouping (None for the manual path).

    Returns
    -------
    dict — the same response shapes documented on the public functions:
      - blocked claim:   {"placed": False, "reason": "already_claimed", ...}
      - broker reject:   {"placed": False, "reason": "reject:<rejreason>", ...}
      - success:         {"placed": True, "protected": True, ...}
      - post-fill raise: {"placed": True, "protected": False, "halted": ..., ...}
    """
    # ------------------------------------------------------------------
    # Idempotency claim (atomic; only one call can proceed).
    # record_intent MUST be called first to create the INTENT-state doc;
    # claim_for_submit is an atomic INTENT→SUBMITTING transition.
    # ------------------------------------------------------------------
    # Only forward deployment_id when set — keeps backward-compat with stores
    # whose record_intent signature predates the deployment_id kwarg.
    if deployment_id is not None:
        await intent_store.record_intent(intent, mode="live", deployment_id=deployment_id)
    else:
        await intent_store.record_intent(intent, mode="live")
    if not await intent_store.claim_for_submit(cid):
        return _blocked("already_claimed", verdicts)

    # ------------------------------------------------------------------
    # THE ONLY place_order CALL IN THIS MODULE
    # ------------------------------------------------------------------
    result = await client.place_order(intent)

    # ------------------------------------------------------------------
    # Broker reject: do NOT run post_fill, do NOT arm.
    # ------------------------------------------------------------------
    if not result.ok:
        return {
            "placed": False,
            "reason": f"reject:{result.rejreason}",
            "verdicts": verdicts,
        }

    # ------------------------------------------------------------------
    # From here the position is LIVE at the broker.  The ENTIRE post-fill
    # block is exception-total: any exception in mark_submitted, post_fill,
    # or arm drives _abort_protect (best-effort square + best-effort halt)
    # and returns without propagating.  No unprotected live position persists.
    # ------------------------------------------------------------------
    try:
        await intent_store.mark_submitted(cid, result.norenordno)
        if post_fill is not None:
            await post_fill()
        await arm(intent, result.norenordno)
        return {
            "placed": True,
            "protected": True,
            "norenordno": result.norenordno,
            "cid": cid,
            "verdicts": verdicts,
        }
    except Exception as exc:
        return await _abort_protect(
            client, engine, intent, result.norenordno,
            ref_ltp, band_pct, uid, actid,
            reason=f"post_place_failed:{exc}",
        )


# ---------------------------------------------------------------------------
# Public API — the ONLY permitted entry transmit path
# ---------------------------------------------------------------------------

async def place_live_test_order(
    contract: Dict[str, Any],
    *,
    side: str,
    ref_ltp: float,
    band_pct: float,
    levels: Dict[str, Any],
    client: Any,
    mode_store: Any,
    intent_store: Any,
    engine: Any,
    search_fn: Callable,
    arm: Callable,
    fat_finger_cap: Any = 1,
    buffer_pct: float = 0.5,
    uid: str = "",
    actid: str = "",
) -> Dict[str, Any]:
    """Place exactly one real entry order through all safety gates.

    Parameters
    ----------
    contract:
        Option contract dict (underlying/strike/side/expiry_date/lot_size).
    side:
        "B" (buy) or "S" (sell).
    ref_ltp:
        Latest traded price used for band, buffer, and margin checks.
    band_pct:
        Max % price deviation from ref_ltp.
    levels:
        stop_pts / stop_pct / target_pts / target_pct for order_builder.
    client:
        BrokerClient instance (MockNoren in tests, FlattradeClient in prod).
    mode_store:
        ModeStore instance — provides get() and consume_single_shot().
    intent_store:
        IntentStore instance — provides claim_for_submit() and mark_submitted().
    engine:
        Async object exposing can_trade() -> (bool, str) and halt(reason) -> None.
    search_fn:
        Sync callable(exch, query) -> list[scrip_dict] for symbol resolution.
    arm:
        Async callable(intent, norenordno) -> None.  Places the SL backstop and
        registers the auto-square session.  RAISES on hard arm failure.
    fat_finger_cap:
        Passed through to build_intent but CLAMPED to max 1.
    buffer_pct:
        Marketable cross buffer % (default 0.5).
    uid, actid:
        Broker credentials forwarded to square_position on arm-abort.

    Returns
    -------
    dict with at least "placed" (bool) and "reason" (str).
    On success: {"placed": True, "protected": True, "norenordno": ...,
                  "cid": ..., "verdicts": [...]}.
    On arm failure: {"placed": True, "protected": False, "halted": True,
                     "norenordno": ..., "reason": "arm_failed:<exc>"}.
    On any gate block: {"placed": False, "reason": ..., "verdicts": [...]}.
    On broker reject: {"placed": False, "reason": "reject:<rejreason>",
                       "verdicts": [...]}.
    """
    verdicts: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Gate 0 — long-only: LIVE_TEST entries are option BUYS only.
    # A sell entry would open an unprotected naked short; the SL backstop
    # (always a sell-to-close) would then GROW the short instead of closing it.
    # Reject anything that is not "B" before any broker contact.
    # ------------------------------------------------------------------
    if side != "B":
        return _blocked("side_must_be_buy", verdicts)

    # ------------------------------------------------------------------
    # Gate 1 — mode: must be LIVE_TEST with unconsumed single-shot
    # ------------------------------------------------------------------
    mode = await mode_store.get()
    if not is_live_order_allowed(mode):
        return _blocked("mode_not_live_test", verdicts)

    # ------------------------------------------------------------------
    # Gate 2 — fresh server-side dry-run (lots HARD-PINNED to 1, cap ≤ 1)
    # ------------------------------------------------------------------
    # Non-numeric fat_finger_cap (None, str, bool …) → treated as absent so
    # check_fat_finger default-denies with a clean verdict, not a TypeError.
    capped = (
        min(fat_finger_cap, 1)
        if (isinstance(fat_finger_cap, (int, float)) and not isinstance(fat_finger_cap, bool))
        else None
    )
    cid = new_client_order_id()
    intent, verdicts, resolved_lot_size = build_intent(
        contract,
        side=side,
        order_kind="entry",
        lots=1,                             # hard-pinned — callers cannot change this
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        fat_finger_cap=capped,              # None → default-deny; numeric → clamped ≤ 1
        levels=levels,
        client_order_id=cid,
        buffer_pct=buffer_pct,
        search_fn=search_fn,
    )

    # ------------------------------------------------------------------
    # Gate 3 (margin) — use the broker-resolved lot size, not a stale constant.
    # Only append the margin verdict when resolution succeeded (resolved_lot_size
    # is not None); if resolution failed the symbol verdict already blocked.
    # ------------------------------------------------------------------
    limits = await client.limits()
    if resolved_lot_size is not None:
        verdicts.append(margin_verdict(limits, ref_ltp=ref_ltp, lot_size=resolved_lot_size))

    # ------------------------------------------------------------------
    # Gate 4 — all verdicts must pass (intent must be non-None)
    # ------------------------------------------------------------------
    if intent is None or any(not v["ok"] for v in verdicts):
        return _blocked("dry_run_failed", verdicts)

    # ------------------------------------------------------------------
    # Gate 5 — defense-in-depth: qty must equal exactly one resolved lot
    # ------------------------------------------------------------------
    if resolved_lot_size is None or intent.qty != resolved_lot_size:
        return _blocked("not_one_lot", verdicts)

    # ------------------------------------------------------------------
    # Gate 6 — engine must permit trading
    # ------------------------------------------------------------------
    ok, why = await engine.can_trade()
    if not ok:
        return _blocked(f"cannot_trade:{why}", verdicts)

    # ------------------------------------------------------------------
    # Gates 7+8 + post-fill — delegated to the shared transmit core.
    #
    # _transmit_and_arm runs record_intent → claim_for_submit → THE ONLY
    # client.place_order → mark_submitted → post_fill → arm, all wrapped in
    # the exception-total abort-protect.  For the LIVE_TEST path the post-fill
    # hook is consume_single_shot (which locks the single-shot AFTER a confirmed
    # fill, BEFORE arm — preserving the original step-10/step-11 ordering).
    # ------------------------------------------------------------------
    async def _consume() -> None:
        await mode_store.consume_single_shot()

    return await _transmit_and_arm(
        intent,
        cid,
        client=client,
        intent_store=intent_store,
        engine=engine,
        arm=arm,
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        uid=uid,
        actid=actid,
        verdicts=verdicts,
        post_fill=_consume,
    )


# ---------------------------------------------------------------------------
# Deployed entry transmit path — strategy-deployed sibling of the manual ticket
# ---------------------------------------------------------------------------

_AUTOPLACE_ENV = "LIVE_AUTOPLACE_ARMED"


def _autoplace_armed() -> bool:
    """Return True iff the LIVE_AUTOPLACE_ARMED env kill is set (offline-first).

    Any unset / falsy ("", "0", "false", "no", "off") value → False → the
    deployed path dry-run-logs the intent and NEVER calls place_order.
    """
    raw = os.environ.get(_AUTOPLACE_ENV, "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _would_send(intent: Any) -> Dict[str, Any]:
    """Serialise the would-be order for an offline dry-run-log response."""
    return {
        "client_order_id": intent.client_order_id,
        "trantype": intent.trantype,
        "prctyp": intent.prctyp,
        "exch": intent.exch,
        "tsym": intent.tsym,
        "qty": intent.qty,
        "prc": intent.prc,
        "trgprc": intent.trgprc,
        "prd": intent.prd,
        "ret": intent.ret,
        "remarks": intent.remarks,
    }


async def place_deployed_order(
    contract: Dict[str, Any],
    *,
    side: str,
    ref_ltp: float,
    band_pct: float,
    levels: Dict[str, Any],
    capped_lots: int,
    deployment_id: str,
    account_ceiling: Any,
    client: Any,
    intent_store: Any,
    engine: Any,
    allow_fn: Callable[[], Tuple[bool, Optional[str]]],
    search_fn: Callable,
    arm: Callable,
    throttle: Optional[RateThrottle] = None,
    now: float = 0.0,
    buffer_pct: float = 0.5,
    uid: str = "",
    actid: str = "",
    autoplace_armed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Place exactly one strategy-deployed entry through all safety gates.

    Sibling of ``place_live_test_order``.  Differences (per §5.2 of the spec):

    - **No single-shot semantics** — the deployed path does NOT flip global mode
      and does NOT consume any single-shot.
    - **Gate 1 authorization** uses the injected ``allow_fn`` (bound to
      ``is_deployment_live_allowed(deployment, now, connected)``) instead of the
      global mode gate.
    - **Lots = ``capped_lots``** (pre-clamped to the account ceiling in auto_live),
      ``fat_finger_cap`` = the **account ceiling** (default 20), not 1.
    - **Margin** is checked for the **full** ``capped_lots × lot_size`` quantity.
    - **Gate 5** is a lot-cap defense-in-depth: ``intent.qty == capped_lots ×
      resolved_lot_size`` AND ``capped_lots <= account_ceiling`` → else
      ``not_within_lot_cap``.
    - **Gate 8 RateThrottle** — a token-bucket guard (new in this path) protects
      against a multi-deployment same-bar burst near the SEBI 10/sec ORL.
    - **Transmit boundary** — if ``LIVE_AUTOPLACE_ARMED`` is NOT set, the order is
      NOT transmitted: returns ``{placed: False, dry_run: True, would_send,
      verdicts}`` (offline-first).  When set, ``_transmit_and_arm`` runs the one
      real ``place_order`` (no single-shot consume / SessionStore / 10-min timer).

    Parameters
    ----------
    capped_lots:
        Lots to send, pre-clamped to the account ceiling by auto_live; re-verified
        at Gate 5.
    deployment_id:
        The deployment whose signal triggered this order (audit / idempotency).
    account_ceiling:
        Absolute account max lots/order (fat_finger_cap).  Non-numeric → default-deny.
    allow_fn:
        Sync callable() -> (bool, reason) — the per-deployment arm predicate.
    throttle:
        Optional shared RateThrottle.  When None, throttling is skipped (the caller
        owns the shared bucket; a missing bucket is not a fail-closed condition here
        because per-bar cadence already keeps real volume far under the limit).
    now:
        Wall-clock seconds injected into throttle.allow() for determinism.
    autoplace_armed:
        Test override for the env kill.  None → read LIVE_AUTOPLACE_ARMED.

    Returns
    -------
    dict with at least "placed" (bool) and "reason"/"dry_run" describing outcome.
    """
    verdicts: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Gate 0 — long-only: deployed entries are option BUYS only (verbatim).
    # ------------------------------------------------------------------
    if side != "B":
        return _blocked("side_must_be_buy", verdicts)

    # ------------------------------------------------------------------
    # Gate 1 — authorization: the per-deployment arm predicate, not the
    # global mode gate.  No consume_single_shot in this path.
    # ------------------------------------------------------------------
    allowed, why = allow_fn()
    if not allowed:
        return _blocked(f"not_armed:{why}" if why else "not_armed", verdicts)

    # ------------------------------------------------------------------
    # Gate 2 — fresh server-side dry-run.  lots=capped_lots; fat_finger_cap is
    # the ACCOUNT CEILING (not 1).  Non-numeric ceiling → default-deny.
    # ------------------------------------------------------------------
    ceiling = (
        account_ceiling
        if (isinstance(account_ceiling, (int, float)) and not isinstance(account_ceiling, bool))
        else None
    )
    cid = new_client_order_id()
    intent, verdicts, resolved_lot_size = build_intent(
        contract,
        side=side,
        order_kind="entry",
        lots=capped_lots,
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        fat_finger_cap=ceiling,
        levels=levels,
        client_order_id=cid,
        buffer_pct=buffer_pct,
        search_fn=search_fn,
    )

    # ------------------------------------------------------------------
    # Gate 3 — margin for the FULL capped_lots × lot_size size.  margin_verdict
    # multiplies ref_ltp × lot_size × buffer, so pass the full quantity as the
    # effective lot_size (capped_lots × resolved_lot_size).
    # ------------------------------------------------------------------
    limits = await client.limits()
    if resolved_lot_size is not None:
        full_qty = capped_lots * resolved_lot_size
        verdicts.append(margin_verdict(limits, ref_ltp=ref_ltp, lot_size=full_qty))

    # ------------------------------------------------------------------
    # Gate 4 — all verdicts must pass (intent must be non-None)
    # ------------------------------------------------------------------
    if intent is None or any(not v["ok"] for v in verdicts):
        return _blocked("dry_run_failed", verdicts)

    # ------------------------------------------------------------------
    # Gate 5 — lot-cap defense-in-depth: qty must equal exactly
    # capped_lots × resolved_lot_size AND capped_lots must be within the ceiling.
    # ------------------------------------------------------------------
    within_ceiling = (
        ceiling is not None
        and isinstance(capped_lots, int)
        and not isinstance(capped_lots, bool)
        and capped_lots >= 1
        and capped_lots <= ceiling
    )
    if (
        resolved_lot_size is None
        or not within_ceiling
        or intent.qty != capped_lots * resolved_lot_size
    ):
        return _blocked("not_within_lot_cap", verdicts)

    # ------------------------------------------------------------------
    # Gate 6 — engine must permit trading (sticky halt / latch)
    # ------------------------------------------------------------------
    ok, why = await engine.can_trade()
    if not ok:
        return _blocked(f"cannot_trade:{why}", verdicts)

    # ------------------------------------------------------------------
    # Gate 8 — RateThrottle (token-bucket; entries only, never cancels).
    # On a throttle block: NO place_order, NO claim — the signal is naturally
    # retried on the next bar.
    # ------------------------------------------------------------------
    if throttle is not None and not throttle.allow(is_cancel=False, now=now):
        return _blocked("rate_throttled", verdicts)

    # ------------------------------------------------------------------
    # Transmit boundary — offline-first.  If LIVE_AUTOPLACE_ARMED is NOT set,
    # do NOT touch the broker: return a dry-run-log response.
    # ------------------------------------------------------------------
    armed = autoplace_armed if autoplace_armed is not None else _autoplace_armed()
    if not armed:
        return {
            "placed": False,
            "dry_run": True,
            "would_send": _would_send(intent),
            "verdicts": verdicts,
        }

    # ------------------------------------------------------------------
    # Gates 7 + transmit — the shared core (record_intent → claim → THE place
    # → mark_submitted → arm), exception-total.  No single-shot consume / no
    # SessionStore / no 10-min timer for the deployed path.
    # ------------------------------------------------------------------
    return await _transmit_and_arm(
        intent,
        cid,
        client=client,
        intent_store=intent_store,
        engine=engine,
        arm=arm,
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        uid=uid,
        actid=actid,
        verdicts=verdicts,
        deployment_id=deployment_id,
    )
