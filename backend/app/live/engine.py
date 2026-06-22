"""LiveEngine — read-only orchestrator for the L2 safe core (Task L2.3).

Architecture
------------
LiveEngine glues three existing pure/async modules into a single stateful
orchestrator:

  order_sm.apply_om   → processes each Noren om event into the live_orders store
  reconcile.reconcile → periodic broker ↔ internal diff
  kill_switch.evaluate_guardrails → account-level safety guardrails

INVARIANTS (safe core — never broken before L3):
  1. No .place_order(  call anywhere in this file.
  2. No .cancel_order( call anywhere in this file.
  3. halt is STICKY: once halted, no subsequent clean event clears it.
     Only an explicit manual resume (external to this engine) should do that.
  4. _halt is IDEMPOTENT on the halt flag: keeps the FIRST reason; additional
     halts append further alerts so the full incident trail is preserved.

halt-reason priority (first-to-arrive wins the halt_reason field):
  on_om     : "om_for_unknown_order"         — om references an unknown norenordno
              "order_sm_flagged"             — apply_om set reconcile_required
  reconcile : "reconcile_mismatch"           — broker ↔ internal diff found gaps
  guardrail : "guardrail:broker_stop_loss"   — daily-loss latch tripped

resume_pending — duplicate-order gap closer (L1.2 requirement)
  On restart, intent_store.resume_unsubmitted() returns docs whose norenordno
  is still None (crashed between broker POST and mark_submitted).  Before
  deciding to re-submit we search the broker's live order_book for an entry
  whose ``remarks`` field equals the client_order_id.  If found → ADOPT by
  calling mark_submitted(cid, found_norenordno); do NOT re-POST.  If NOT found →
  tally as needs_submit so L3 can act.  This makes restart crash-safe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.live.order_sm import apply_om
from app.live.reconcile import reconcile
from app.live.kill_switch import evaluate_guardrails, is_entry_blocked

log = logging.getLogger(__name__)


class LiveEngine:
    """Read-only orchestrator for live order flow.

    Constructor parameters
    ----------------------
    client          : async BrokerClient (MockNoren in tests; FlattradeClient in L3)
    orders_collection : async collection (FakeAsyncCollection / motor) for live_orders
    intent_store    : IntentStore — provides resume_unsubmitted / mark_submitted
    config_store    : SafetyConfigStore — provides get_config / trip

    State
    -----
    halted      : bool — True once any halt condition fires; never auto-clears.
    halt_reason : str | None — reason string of the FIRST halt event.
    alerts      : list — all halt events with reason + detail; append-only.
    internal_positions : list — injected/maintained position list used in reconcile.
                         Callers may set this directly (engine holds a reference).
    """

    def __init__(
        self,
        client: Any,
        orders_collection: Any,
        intent_store: Any,
        config_store: Any,
    ) -> None:
        self._client = client
        self._orders_col = orders_collection
        self._intent_store = intent_store
        self._config_store = config_store

        self.halted: bool = False
        self.halt_reason: Optional[str] = None
        self.alerts: List[Dict[str, Any]] = []

        # Injected position list; callers set this to drive position reconcile.
        self.internal_positions: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Halt helpers
    # ------------------------------------------------------------------

    async def halt(self, reason: str) -> None:
        """Public async halt entrypoint — thin wrapper over ``_halt``.

        Called by the executor's ``_abort_protect`` after a post-fill failure
        so the engine is halted by an external caller without knowing about
        the internal ``_halt`` method.

        Idempotent: subsequent calls keep the FIRST reason and append alerts.
        """
        self._halt(reason, {"caller": "executor"})

    def _halt(self, reason: str, detail: Any) -> None:
        """Record a halt event.  Sets halted=True and halt_reason on first call;
        subsequent calls append alerts but keep the FIRST reason.

        This method is idempotent on halt_reason: calling it N times results in
        exactly one halt_reason (the first) and N alert entries.
        """
        if not self.halted:
            # First halt — establish the primary reason
            self.halted = True
            self.halt_reason = reason
        # Always append to the alert trail so the full incident is preserved
        self.alerts.append({"reason": reason, "detail": detail})
        log.warning("LiveEngine halted: reason=%r detail=%r", reason, detail)

    # ------------------------------------------------------------------
    # on_om — feed an om event through the order state machine
    # ------------------------------------------------------------------

    async def on_om(self, om: Dict[str, Any]) -> None:
        """Process a Noren om event.

        1. Look up the order doc in orders_collection by norenordno.
        2. If NOT found → unknown order → halt.
        3. If found → apply_om → persist.
        4. If the returned doc has any reconcile_required / nord_mismatch /
           overfill flag → halt.

        No place/cancel calls are made here.
        """
        norenordno = om.get("norenordno")

        # Find the matching order doc
        doc = await self._orders_col.find_one({"norenordno": norenordno})

        if doc is None:
            # om for an order we don't track — never create a phantom doc
            self._halt(
                "om_for_unknown_order",
                {"norenordno": norenordno, "om_status": om.get("status")},
            )
            return

        # Apply the state machine (pure — returns a new dict)
        new_doc = apply_om(doc, om)

        # Persist the updated doc
        await self._orders_col.update_one(
            {"norenordno": norenordno},
            {"$set": new_doc},
        )

        # Check for flags that require a halt
        if (
            new_doc.get("reconcile_required")
            or new_doc.get("nord_mismatch")
            or new_doc.get("overfill")
        ):
            self._halt(
                "order_sm_flagged",
                {
                    "norenordno": norenordno,
                    "reconcile_required": new_doc.get("reconcile_required"),
                    "nord_mismatch": new_doc.get("nord_mismatch"),
                    "overfill": new_doc.get("overfill"),
                    "state": new_doc.get("state"),
                },
            )

    # ------------------------------------------------------------------
    # reconcile_tick — periodic broker ↔ internal reconcile
    # ------------------------------------------------------------------

    async def reconcile_tick(self) -> Dict[str, Any]:
        """Fetch broker books and diff against internal state.

        A mismatch halts the engine (sticky).  A CLEAN reconcile does NOT
        un-halt — once halted, halt is permanent until a manual resume.

        Returns the reconcile report dict {"ok": bool, "mismatches": [...]}.
        """
        broker_orders = await self._client.order_book()
        broker_positions = await self._client.position_book()

        # Load all internal orders from the collection
        internal_orders_cursor = self._orders_col.find({})
        internal_orders = await internal_orders_cursor.to_list(length=None)

        report = reconcile(
            internal_orders=internal_orders,
            internal_positions=self.internal_positions,
            broker_orders=broker_orders,
            broker_positions=broker_positions,
        )

        if not report["ok"]:
            self._halt("reconcile_mismatch", report["mismatches"])

        return report

    # ------------------------------------------------------------------
    # resume_pending — duplicate-order gap closer
    # ------------------------------------------------------------------

    async def resume_pending(self) -> Dict[str, Any]:
        """Close the crash-between-POST-and-ACK duplicate-order gap.

        For each doc returned by intent_store.resume_unsubmitted():
          - Fetch the current broker order_book.
          - Search for a broker order whose ``remarks`` field equals the
            client_order_id (the order builder pins remarks=cid).
          - If FOUND: call mark_submitted(cid, norenordno) to adopt the existing
            broker order WITHOUT re-POSTing — this is the duplicate-order guard.
          - If NOT found: tally in needs_submit so L3 can decide to re-send.

        Returns
        -------
        {"adopted": [cid, ...], "needs_submit": [cid, ...]}

        Why this is safe
        ----------------
        The adopt path marks the intent as submitted to the existing broker order,
        preventing any subsequent logic from re-POSTing the same intent.  Only
        after adopt completes is the cid removed from resume_unsubmitted() — so
        if this function crashes mid-run, the next call will re-check and re-adopt
        idempotently (mark_submitted is idempotent for the same norenordno).
        """
        docs = await self._intent_store.resume_unsubmitted()

        # Fetch broker order book once; we'll scan it for each pending cid.
        broker_orders = await self._client.order_book()

        adopted: List[str] = []
        needs_submit: List[str] = []

        for doc in docs:
            cid = doc["client_order_id"]

            # Search broker order book for an entry whose remarks == cid
            found: Optional[Dict[str, Any]] = None
            for order in broker_orders:
                if order.get("remarks") == cid:
                    found = order
                    break

            if found is not None:
                # ADOPT — mark the intent as submitted to the found norenordno.
                # Do NOT place a new order.
                norenordno = found["norenordno"]
                await self._intent_store.mark_submitted(cid, norenordno)
                adopted.append(cid)
                log.info(
                    "resume_pending: adopted cid=%r → norenordno=%r", cid, norenordno
                )
            else:
                # Not found at broker — L3 will decide whether to re-submit.
                needs_submit.append(cid)
                log.info("resume_pending: cid=%r not found at broker → needs_submit", cid)

        return {"adopted": adopted, "needs_submit": needs_submit}

    # ------------------------------------------------------------------
    # guardrail_tick — account-level safety check
    # ------------------------------------------------------------------

    async def guardrail_tick(self, mtm: Any, open_count: Any) -> str:
        """Evaluate account guardrails and halt + latch on broker_stop_loss.

        Parameters
        ----------
        mtm        : float — current mark-to-market P&L.
        open_count : int   — number of currently open positions.

        Returns
        -------
        The action string: "none" | "broker_stop_loss" | "max_open_block" |
        "profit_lock".

        Side effects
        ------------
        On "broker_stop_loss": trips the SafetyConfigStore latch (so
        is_entry_blocked returns True from subsequent get_config calls) AND halts
        the engine.  The action is always recorded as an alert regardless of
        the result.
        """
        config = await self._config_store.get_config()
        action = evaluate_guardrails(mtm, open_count, config)

        # Record all guardrail evaluations as alerts (not just halts)
        self.alerts.append({"reason": f"guardrail:{action}", "detail": {"mtm": mtm, "open_count": open_count}})

        if action == "broker_stop_loss":
            # Persist the latch
            await self._config_store.trip()
            # Halt the engine
            self._halt(
                f"guardrail:{action}",
                {"mtm": mtm, "open_count": open_count, "action": action},
            )

        return action

    # ------------------------------------------------------------------
    # can_trade — entry gate
    # ------------------------------------------------------------------

    async def can_trade(self) -> Tuple[bool, str]:
        """Return (True, "") if new entries are permitted; (False, reason) otherwise.

        Blocks if:
          - self.halted (any halt has occurred), OR
          - the SafetyConfigStore latch is set (is_entry_blocked).

        This is async because reading the config requires an async DB call.
        """
        if self.halted:
            return False, f"engine halted: {self.halt_reason}"

        config = await self._config_store.get_config()
        if is_entry_blocked(config):
            return False, "entry blocked: blocked_until_reset latch is set"

        return True, ""
