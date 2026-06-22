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

from typing import Any, Callable, Dict, List, Optional

from app.live.mode import is_live_order_allowed
from app.live.order_builder import build_intent
from app.live.margin import margin_verdict
from app.live.idempotency import new_client_order_id
from app.live.auto_square import square_position


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
# Public API — the ONLY permitted entry transmit path
# ---------------------------------------------------------------------------

async def place_live_test_order(
    contract: Dict[str, Any],
    *,
    side: str,
    ref_ltp: float,
    band_pct: float,
    levels: Dict[str, Any],
    lot_size: int,
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
    lot_size:
        Expected lot size; the intent's qty MUST equal this exactly.
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
    intent, verdicts = build_intent(
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
    # Gate 3 (margin) — append to verdicts before checking
    # ------------------------------------------------------------------
    limits = await client.limits()
    verdicts.append(margin_verdict(limits, ref_ltp=ref_ltp, lot_size=lot_size))

    # ------------------------------------------------------------------
    # Gate 4 — all verdicts must pass (intent must be non-None)
    # ------------------------------------------------------------------
    if intent is None or any(not v["ok"] for v in verdicts):
        return _blocked("dry_run_failed", verdicts)

    # ------------------------------------------------------------------
    # Gate 5 — defense-in-depth: qty must equal exactly one lot
    # ------------------------------------------------------------------
    if intent.qty != lot_size:
        return _blocked("not_one_lot", verdicts)

    # ------------------------------------------------------------------
    # Gate 6 — engine must permit trading
    # ------------------------------------------------------------------
    ok, why = await engine.can_trade()
    if not ok:
        return _blocked(f"cannot_trade:{why}", verdicts)

    # ------------------------------------------------------------------
    # Gate 7 — idempotency claim (atomic; only one call can proceed)
    #
    # record_intent MUST be called first to create the INTENT-state doc;
    # claim_for_submit is an atomic INTENT→SUBMITTING transition that only
    # one concurrent caller can win.
    # ------------------------------------------------------------------
    await intent_store.record_intent(intent, mode="live")
    if not await intent_store.claim_for_submit(cid):
        return _blocked("already_claimed", verdicts)

    # ------------------------------------------------------------------
    # Gate 8 — THE ONLY place_order CALL IN THIS MODULE
    # ------------------------------------------------------------------
    result = await client.place_order(intent)

    # ------------------------------------------------------------------
    # Step 9 — broker reject: do NOT consume single-shot, do NOT arm
    # ------------------------------------------------------------------
    if not result.ok:
        return {
            "placed": False,
            "reason": f"reject:{result.rejreason}",
            "verdicts": verdicts,
        }

    # ------------------------------------------------------------------
    # Steps 10 + 11 — from here the position is LIVE at the broker.
    # The ENTIRE post-fill block is exception-total: any exception in
    # mark_submitted, consume_single_shot, or arm drives _abort_protect
    # (best-effort square + best-effort halt) and returns without
    # propagating.  No unprotected live position can ever persist.
    # ------------------------------------------------------------------
    try:
        await intent_store.mark_submitted(cid, result.norenordno)
        await mode_store.consume_single_shot()
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
