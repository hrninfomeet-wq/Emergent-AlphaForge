"""Read-only session timeline for ONE live deployment.

Answers "what did this deployment do today, and why did it (not) trade?" by merging
what the system already journals into one ascending list:

  signals       -> a signal (blocked + blockers), its refusal (live_trade_error),
                   its intended entry (live_intended) and each lifecycle transition
  live_trades   -> the entry (created that IST day) and the exit (closed that day)
  live_orders   -> each order intent / broker submission
  deployment    -> the latest hold, disable and caps change (point events)

It NEVER raises: a source that cannot be read becomes a ``gaps`` line, and the
timeline of whatever WAS readable is still returned. It also states, in ``gaps``,
what is not recorded anywhere — the point of the page is to stop an empty stretch of
the day reading as "nothing happened". Nothing here writes, and nothing invents a
number: a null P&L stays "—".
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_MAX_ROWS = 5000

# What is NOT recorded anywhere. Always reported: an operator staring at a quiet
# afternoon needs to know these silences are structural, not "the bot did nothing".
GAP_SKIPPED_ENTRIES = (
    "Entries skipped while a position was already held, after the entry cutoff, or "
    "with the broker not connected are not recorded anywhere — the evaluator never "
    "calls the live sink in those states, so no refusal is journalled."
)
GAP_HALT_HISTORY = (
    "Safety halt / latch history is not recorded — only the current state exists, "
    "and a latch reset wipes it."
)
GAP_ORDER_OUTCOMES = (
    "Order fills and broker rejections carry no timestamp — only each order's "
    "current state is stored."
)
GAP_POINT_EVENTS = (
    "Hold, disable and caps events show only the LATEST of each — an earlier one "
    "was overwritten."
)
GAP_REFUSAL_TIMES = (
    "A refusal's time is its signal's last update, so it can trail the real "
    "refusal slightly."
)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _parse_ts(value: Any) -> Optional[datetime]:
    """A UTC datetime from an ISO string (Z or offset; naive = UTC), a datetime, or
    epoch milliseconds. None when it cannot be read — never a guess."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            n = float(value)
            if not math.isfinite(n):
                return None
            dt = datetime.fromtimestamp(n / 1000.0, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class _Events:
    """Collects (datetime, event) pairs; an event with no readable time cannot be
    placed on a timeline, so it is counted (and reported as a gap), not guessed."""

    def __init__(self, day_start_ms: int, day_end_ms: int) -> None:
        self.items: List[Tuple[datetime, Dict[str, Any]]] = []
        self.dropped = 0
        self._lo, self._hi = day_start_ms, day_end_ms

    def in_day(self, dt: datetime) -> bool:
        ms = dt.timestamp() * 1000.0
        return self._lo <= ms < self._hi

    def add(self, ts_value: Any, kind: str, label: str, detail: str, source: str,
            *, day_filter: bool = False) -> None:
        dt = _parse_ts(ts_value)
        if dt is None:
            self.dropped += 1
            return
        if day_filter and not self.in_day(dt):
            return
        self.items.append((dt, {"kind": kind, "label": label, "detail": detail,
                                "source": source}))

    def ordered(self) -> List[Dict[str, Any]]:
        # sort() is stable: events sharing a timestamp keep the order they were added.
        self.items.sort(key=lambda p: p[0])
        return [{"ts": dt.isoformat(), **ev} for dt, ev in self.items]


def _describe_intended(intended: Any) -> str:
    if not isinstance(intended, dict):
        return "intended entry recorded"
    parts: List[str] = []
    lots = _num(intended.get("lots"))
    if lots is not None:
        parts.append(f"{lots:g} lot{'' if lots == 1 else 's'}")
    ref = _num(intended.get("ref_ltp") if intended.get("ref_ltp") is not None
               else intended.get("ref_premium"))
    if ref is not None:
        parts.append(f"ref {ref:.2f}")
    at = _num(intended.get("premium_at_entry"))
    if at is not None:
        parts.append(f"premium {at:.2f}")
    if intended.get("would_send") is False:
        parts.append("dry-run — not sent")
    return " · ".join(parts) or "intended entry recorded"


def _add_signal(ev: _Events, sig: Dict[str, Any]) -> None:
    direction = str(sig.get("direction") or "").upper() or "?"
    blocked = bool(sig.get("blocked"))
    blockers = sig.get("blockers")
    if blocked:
        listed = [str(b) for b in blockers] if isinstance(blockers, list) else []
        detail = ("blocked: " + "; ".join(listed)) if listed else "blocked (no reason recorded)"
    else:
        spot = _num(sig.get("entry_price"))
        detail = " · ".join(p for p in (
            str(sig.get("instrument") or ""),
            f"spot {spot:.2f}" if spot is not None else "",
        ) if p)
    # candle_ts is the bar's epoch-ms; created_at is when the evaluator journalled it.
    ev.add(sig.get("created_at") or sig.get("candle_ts"), "signal",
           f"{direction} signal" + (" — blocked" if blocked else ""), detail, "signals")
    err = sig.get("live_trade_error")
    if err:
        ev.add(sig.get("updated_at") or sig.get("created_at") or sig.get("candle_ts"),
               "refused", "Entry refused", str(err), "signals.live_trade_error")
    intended = sig.get("live_intended")
    if intended:
        at = intended.get("at") if isinstance(intended, dict) else None
        ev.add(at or sig.get("updated_at") or sig.get("created_at") or sig.get("candle_ts"),
               "intended", "Intended entry", _describe_intended(intended),
               "signals.live_intended")
    for t in sig.get("events") or []:
        if not isinstance(t, dict) or t.get("from_state") is None:
            continue  # the creation entry IS the "signal" event above
        ev.add(t.get("at"), "state",
               f"{t.get('from_state')} → {t.get('to_state')}",
               str(t.get("reason") or ""), "signals.events")


def _add_trade(ev: _Events, t: Dict[str, Any]) -> None:
    tsym = str(t.get("noren_tsym") or t.get("trading_symbol") or "—")
    entry = _num(t.get("entry_price"))
    fill = _num(t.get("entry_fill_price"))
    lots = _num(t.get("lots"))
    bits = [f"{lots:g} lot{'' if lots == 1 else 's'}" if lots is not None else "",
            f"ref {entry:.2f}" if entry is not None else "",
            f"fill {fill:.2f}" if fill is not None else ""]
    ev.add(t.get("created_at"), "entry", f"Entry {tsym}",
           " · ".join(b for b in bits if b), "live_trades", day_filter=True)
    if t.get("closed_at"):
        pnl = _num(t.get("realized_pnl"))
        bits = [f"reason {t.get('exit_reason') or '—'}",
                f"P&L {pnl:+.2f}" if pnl is not None else "P&L —"]
        if t.get("realized_pnl_backfilled"):
            bits.append("P&L backfilled after the fact")
        if t.get("exit_day_unknown"):
            bits.append("exit day unknown — this is when the close was noticed")
        ev.add(t.get("closed_at"), "exit", f"Exit {tsym}", " · ".join(bits),
               "live_trades", day_filter=True)


def _add_order(ev: _Events, o: Dict[str, Any]) -> None:
    intent = o.get("intent") if isinstance(o.get("intent"), dict) else {}
    side = {"B": "BUY", "S": "SELL"}.get(str(intent.get("trantype") or "").upper(), "order")
    qty, prc = _num(intent.get("qty")), _num(intent.get("prc"))
    detail = " · ".join(p for p in (
        str(intent.get("tsym") or ""),
        f"qty {qty:g}" if qty is not None else "",
        f"@ {prc:.2f}" if prc is not None else "",
        f"now {o.get('state')}" if o.get("state") else "",
        f"#{o.get('norenordno')}" if o.get("norenordno") else "",
    ) if p)
    ev.add(o.get("ts_intent"), "order", f"{side} order intended", detail,
           "live_orders", day_filter=True)
    if o.get("ts_submitted"):
        ev.add(o.get("ts_submitted"), "order", f"{side} order sent to broker",
               detail, "live_orders", day_filter=True)


def _add_deployment_points(ev: _Events, deployment: Dict[str, Any]) -> None:
    live = (deployment.get("risk") or {}).get("live") or {}
    if not isinstance(live, dict):
        return
    if live.get("paused") and live.get("paused_at"):
        ev.add(live["paused_at"], "hold", "Held — no new entries",
               "open positions stay open and guarded", "deployment.risk.live.paused_at",
               day_filter=True)
    if live.get("disabled_at"):
        ev.add(live["disabled_at"], "disabled", "Live disabled",
               f"reason {live.get('last_block_reason') or 'not recorded'}",
               "deployment.risk.live.disabled_at", day_filter=True)
    change = live.get("last_caps_change")
    if isinstance(change, dict) and change.get("at"):
        frm, to = change.get("from") or {}, change.get("to") or {}
        detail = "; ".join(f"{k} {frm.get(k)} → {to.get(k)}" for k in to) if isinstance(to, dict) else ""
        ev.add(change["at"], "caps", "Caps tightened", detail,
               "deployment.risk.live.last_caps_change", day_filter=True)


async def _rows(cursor_factory: Any, what: str, gaps: List[str]) -> List[Dict[str, Any]]:
    """Read one source; any failure becomes a gap, never an exception."""
    try:
        return list(await cursor_factory().to_list(length=_MAX_ROWS))
    except Exception as exc:  # noqa: BLE001 — a timeline must degrade, not 500
        log.warning("session timeline: %s unreadable: %s", what, exc)
        gaps.append(f"{what} could not be read ({type(exc).__name__}) — its events are missing.")
        return []


async def build_session_timeline(
    db: Any, deployment_id: str, date_iso: str, day_bounds_ms: Tuple[int, int],
) -> Dict[str, Any]:
    """``{date, events, gaps}`` for one deployment and one IST calendar day.

    ``day_bounds_ms`` is the IST day as [start, end) epoch-ms — the same bounds the
    router's other IST-day reads use — and drives BOTH the signal query (candle_ts
    is an epoch-ms bar time) and the day membership of every ISO-timestamped event.
    """
    start_ms, end_ms = day_bounds_ms
    ev = _Events(start_ms, end_ms)
    gaps: List[str] = []

    signals = await _rows(
        lambda: db.signals.find(
            {"deployment_id": deployment_id,
             "candle_ts": {"$gte": start_ms, "$lt": end_ms}}, {"_id": 0}
        ).sort("candle_ts", 1),
        "signals", gaps)
    for sig in signals:
        _add_signal(ev, sig)

    for t in await _rows(
            lambda: db.live_trades.find({"deployment_id": deployment_id}, {"_id": 0}),
            "live_trades", gaps):
        _add_trade(ev, t)

    # live_orders carry deployment_id + ts_intent (idempotency.record_intent).
    for o in await _rows(
            lambda: db.live_orders.find({"deployment_id": deployment_id}, {"_id": 0}),
            "live_orders", gaps):
        _add_order(ev, o)

    try:
        deployment = await db.strategy_deployments.find_one({"id": deployment_id}, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        log.warning("session timeline: deployment unreadable: %s", exc)
        deployment = None
    if isinstance(deployment, dict):
        _add_deployment_points(ev, deployment)
    else:
        gaps.append("The deployment document could not be read — hold, disable and "
                    "caps events are missing.")

    if ev.dropped:
        gaps.append(f"{ev.dropped} event(s) had no readable timestamp and are omitted.")
    gaps.extend([GAP_SKIPPED_ENTRIES, GAP_HALT_HISTORY, GAP_ORDER_OUTCOMES, GAP_POINT_EVENTS])
    events = ev.ordered()
    if any(e["kind"] == "refused" for e in events):
        gaps.append(GAP_REFUSAL_TIMES)
    return {"date": date_iso, "events": events, "gaps": gaps}
