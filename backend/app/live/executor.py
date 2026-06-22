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
square session armed via the injected ``arm`` callable).  If ``arm`` raises the
executor immediately cancels/squares the just-filled position and halts the
engine so no human-unattended open position is ever left unprotected.

Lots hard-pinned to 1
---------------------
The ``lots`` parameter is NOT exposed at all.  The executor always passes
``lots=1`` and ``fat_finger_cap=min(fat_finger_cap, 1)`` to ``build_intent``.
There is no way for a caller to inject qty > lot_size through this function.
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
    # Gate 1 — mode: must be LIVE_TEST with unconsumed single-shot
    # ------------------------------------------------------------------
    mode = await mode_store.get()
    if not is_live_order_allowed(mode):
        return _blocked("mode_not_live_test", verdicts)

    # ------------------------------------------------------------------
    # Gate 2 — fresh server-side dry-run (lots HARD-PINNED to 1, cap ≤ 1)
    # ------------------------------------------------------------------
    cid = new_client_order_id()
    intent, verdicts = build_intent(
        contract,
        side=side,
        order_kind="entry",
        lots=1,                             # hard-pinned — callers cannot change this
        ref_ltp=ref_ltp,
        band_pct=band_pct,
        fat_finger_cap=min(fat_finger_cap, 1),   # clamped — never > 1
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
    # Step 10 — fill confirmed: mark submitted + consume single-shot
    # ------------------------------------------------------------------
    await intent_store.mark_submitted(cid, result.norenordno)
    await mode_store.consume_single_shot()

    # ------------------------------------------------------------------
    # Step 11 — arm-or-abort: position MUST be protected immediately
    # ------------------------------------------------------------------
    try:
        await arm(intent, result.norenordno)
    except Exception as exc:
        # Arm failed — abort: cancel/square the just-placed order then halt
        await square_position(
            client,
            {
                "tsym": intent.tsym,
                "exch": intent.exch,
                "netqty": 0,                         # cancel-only path
                "lp": ref_ltp,
                "working_norenordno": result.norenordno,
            },
            reason="arm_failed",
            band_pct=band_pct,
            uid=uid,
            actid=actid,
        )
        await engine.halt("auto_square_arm_failed")
        return {
            "placed": True,
            "protected": False,
            "halted": True,
            "norenordno": result.norenordno,
            "reason": f"arm_failed:{exc}",
        }

    return {
        "placed": True,
        "protected": True,
        "norenordno": result.norenordno,
        "cid": cid,
        "verdicts": verdicts,
    }
