"""Auditable live-signal lifecycle helpers."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger(__name__)


class SignalStateError(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    "WATCHING": {"FORMING", "AUDITED"},
    "FORMING": {"CONFIRMED", "AUDITED"},
    "CONFIRMED": {"TRIGGERED", "AUDITED"},
    "TRIGGERED": {"ACTIVE", "SKIPPED", "AUDITED"},
    "ACTIVE": {"EXITED", "AUDITED"},
    "EXITED": {"AUDITED"},
    "SKIPPED": {"AUDITED"},
    "AUDITED": set(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _event(from_state: Optional[str], to_state: str, reason: str = "", at: Optional[str] = None, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "at": at or _now_iso(),
        "snapshot": snapshot or {},
    }


def create_signal_doc(
    *,
    instrument: str,
    direction: str,
    strategy_id: str,
    entry_price: Any,
    confidence: Any,
    reasons: Optional[Iterable[str]] = None,
    option_contract: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    state = "WATCHING"
    at = created_at or _now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "instrument": str(instrument or "").upper(),
        "direction": str(direction or "").upper(),
        "strategy_id": str(strategy_id or ""),
        "entry_price": _float_or_none(entry_price),
        "confidence": _float_or_none(confidence),
        "reasons": [str(reason) for reason in (reasons or [])],
        "option_contract": option_contract or {},
        "context": context or {},
        "state": state,
        "created_at": at,
        "updated_at": at,
        "events": [_event(None, state, reason="created", at=at)],
    }
    return doc


def transition_signal(
    signal: Dict[str, Any],
    to_state: str,
    *,
    reason: str = "",
    at: Optional[str] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = str(signal.get("state") or "WATCHING").upper()
    target = str(to_state or "").upper()
    if target not in ALLOWED_TRANSITIONS:
        raise SignalStateError(f"Unknown signal state: {target}")
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise SignalStateError(f"Invalid signal transition: {current} -> {target}")

    updated = dict(signal)
    events = list(signal.get("events") or [])
    timestamp = at or _now_iso()
    events.append(_event(current, target, reason=reason, at=timestamp, snapshot=snapshot))
    updated["state"] = target
    updated["updated_at"] = timestamp
    updated["events"] = events
    if target in {"TRIGGERED", "ACTIVE"} and not updated.get("triggered_at"):
        updated["triggered_at"] = timestamp
    if target == "EXITED":
        updated["exited_at"] = timestamp
    if target == "AUDITED":
        updated["audited_at"] = timestamp
    if target == "SKIPPED":
        updated["skipped_at"] = timestamp
    return updated


#: A CONFIRMED signal is acted on ONLY inside the evaluator pass for its own bar:
#: `evaluate_active_deployments` routes the pass's fresh results to the paper / live
#: sink seconds after the bar closes, and nothing revisits it (no manual-approve route
#: exists; a refused sink releases its claim but nothing retries). So a CONFIRMED signal
#: whose bar is older than this has been passed over for good. Generous on purpose —
#: the sink path is seconds, this is minutes — because the cost of being wrong is
#: expiring a signal that was about to trade.
UNACTIONED_AFTER_MINUTES = 15
# Not "session ended": the 15:00 sweep retires bars from earlier TODAY, while the
# session is still open. What is true for every retired signal is that its bar passed.
UNACTIONED_REASON = "unactioned_bar_passed"


def signal_bar_ms(sig: Dict[str, Any]) -> Optional[int]:
    """The bar's epoch-ms, or None when it cannot be read (an unknown age is never
    'old enough').

    A signal DOC stores its bar as ``candle_ts`` (and ``context.candle.ts``).
    ``bar_ts`` is the evaluation AUDIT record's name for the same minute and is
    absent from every real signal — on 2026-09-29 all 1366 CONFIRMED signals had
    ``bar_ts`` None, so a sweep keyed on it alone moved nothing.
    """
    candle = ((sig.get("context") or {}).get("candle") or {}) \
        if isinstance(sig.get("context"), dict) else {}
    for v in (sig.get("candle_ts"), candle.get("ts"), sig.get("bar_ts")):
        try:
            if v in (None, ""):
                continue
            n = int(float(v))
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


async def expire_unactioned_signals(
    db: Any,
    *,
    now_utc: Optional[datetime] = None,
    older_than_minutes: int = UNACTIONED_AFTER_MINUTES,
    limit: int = 2000,
) -> int:
    """Move CONFIRMED signals nothing will ever act on to AUDITED (terminal).

    ``CONFIRMED`` reads as "awaiting approval". After its bar's evaluator pass it is
    not: no code acts on it again, so it sat CONFIRMED for good and the Signal
    Journal showed past-day signals as pending. ``CONFIRMED -> AUDITED`` is an
    allowed transition; the reason is ``unactioned_bar_passed``.

    NEVER touches a signal the evaluator may still act on:
      * the bar must be OLDER than ``older_than_minutes`` (routing takes seconds);
      * a signal with a claim (``paper_trade_claim`` — a sink is mid-flight, or crashed
        between the trade insert and the signal write) or a trade link
        (``paper_trade_id`` / ``live_trade_id``) is left exactly as found;
      * only engine-produced signals (a string ``deployment_id``) are considered;
      * a blocked signal is already terminal, and one with an unreadable bar is skipped;
      * the write is conditional on ``state == CONFIRMED`` and on the ABSENCE of any
        claim / trade link, so a racing writer's claim is never overwritten.

    Idempotent, and NEVER raises: it is a housekeeping sweep and runs on the far side
    of paths that must not be disturbed. Returns how many signals THIS call moved.
    """
    if db is None:
        return 0
    moved = 0
    try:
        now = now_utc or datetime.now(timezone.utc)
        cutoff_ms = int((now.timestamp() - float(older_than_minutes) * 60.0) * 1000)
        rows = await db.signals.find(
            {"state": "CONFIRMED", "$or": [
                {"candle_ts": {"$lt": cutoff_ms}},
                {"context.candle.ts": {"$lt": cutoff_ms}},
                {"bar_ts": {"$lt": cutoff_ms}},
            ]}, {"_id": 0},
        ).to_list(length=int(limit))
        stamp = now.isoformat()
        for sig in rows:
            try:
                if not isinstance(sig.get("deployment_id"), str) or not sig.get("id"):
                    continue
                if sig.get("blocked"):
                    continue
                if sig.get("paper_trade_claim") or sig.get("paper_trade_id") or sig.get("live_trade_id"):
                    continue
                bar_ms = signal_bar_ms(sig)
                if bar_ms is None or bar_ms >= cutoff_ms:
                    continue
                audited = transition_signal(
                    sig, "AUDITED", reason=UNACTIONED_REASON, at=stamp,
                    snapshot={"bar_ts": bar_ms, "expired_after_minutes": older_than_minutes},
                )
                # Housekeeping, not activity: keep the signal's own updated_at. The
                # retirement time is recorded in audited_at and the appended event.
                # updated_at is read as the refusal time (live-status `last_entry.at`,
                # the timeline's "Entry refused" row) and as the age for "purge older
                # than N days" — stamping the sweep instant on ~1366 signals at once
                # moved every refusal to the sweep time and re-dated June's signals to
                # today.
                if sig.get("updated_at"):
                    audited["updated_at"] = sig["updated_at"]
                res = await db.signals.replace_one(
                    {"id": sig["id"], "state": "CONFIRMED",
                     "paper_trade_claim": {"$exists": False},
                     "paper_trade_id": {"$exists": False},
                     "live_trade_id": {"$exists": False}},
                    audited, upsert=False)
                matched = getattr(res, "matched_count", None)
                if matched is None or matched:
                    moved += 1
            except Exception as exc:  # noqa: BLE001 — one bad doc must not stop the sweep
                log.warning("expire_unactioned_signals: signal %s skipped (%s: %s)",
                            sig.get("id"), type(exc).__name__, str(exc)[:160])
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("expire_unactioned_signals failed (%s: %s)", type(exc).__name__, str(exc)[:160])
    return moved


async def exit_linked_signal(
    db: Any,
    signal_id: Any,
    *,
    reason: str,
    trade_id: Any = None,
    realized_pnl: Any = None,
    at: Optional[str] = None,
) -> bool:
    """Idempotently move the signal behind a CLOSED trade ACTIVE -> EXITED.

    The ONE place a trade close reaches back to its signal. Before this existed the
    only ACTIVE -> EXITED transitions were the paper marker's own stop/target
    auto-close and the manual single-trade close, so a signal whose trade was
    squared by ANY other route (the 15:00 sweep, the boot reconcile, Stop /
    Stop-ALL, the basket controls, retire) stayed ACTIVE for good — and EVERY live
    close (guard, reconcile, kill switch) never touched it, so every live signal
    was ACTIVE forever. The Signal Journal then showed a wall of "active" signals
    for positions that were long flat.

    Returns True iff THIS call moved it. Only an ACTIVE signal moves: EXITED /
    AUDITED / SKIPPED, a missing doc or a blank id is left exactly as found, so a
    repeat call (and a second close route racing this one) is a harmless no-op.
    The write is conditional on ``state == ACTIVE`` for the same reason.

    NEVER raises. It runs on the far side of a close that has already been
    persisted — a journal failure here must not turn a real, completed exit into an
    error or stop the rest of a sweep — so any failure is logged and swallowed.
    """
    if not signal_id or db is None:
        return False
    try:
        sig = await db.signals.find_one({"id": signal_id}, {"_id": 0})
        if not sig or str(sig.get("state") or "").upper() != "ACTIVE":
            return False
        exited = transition_signal(
            sig, "EXITED", reason=reason, at=at,
            snapshot={"trade_id": trade_id, "realized_pnl": realized_pnl},
        )
        res = await db.signals.replace_one(
            {"id": signal_id, "state": "ACTIVE"}, exited, upsert=False)
        matched = getattr(res, "matched_count", None)
        return True if matched is None else bool(matched)
    except Exception as exc:  # noqa: BLE001 — see docstring: the close already happened
        log.warning("exit_linked_signal: signal %s not moved to EXITED (%s: %s)",
                    signal_id, type(exc).__name__, str(exc)[:160])
        return False
