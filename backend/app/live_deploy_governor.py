"""Per-deployment caps governor for live trading.

Governs new live-trade entries for a deployment by checking three caps
configured under ``deployment["risk"]["live"]``:

  - ``max_concurrent``    — hard cap on open live trades at any moment
  - ``max_lots_per_day``  — rolling daily lots limit (IST calendar day)
  - ``daily_loss_cap``    — positive ₹ magnitude; pause when realized +
                             open-unrealized loss today hits the threshold

Returns ``{"allow": bool, "reason": str, "pause": bool}``.

Precedence (first match wins):
  1. daily_loss_cap  → allow=False, pause=True
  2. max_lots_per_day → allow=False, pause=False
  3. max_concurrent  → allow=False, pause=False
  4. else            → allow=True,  reason="ok", pause=False

If none of the three caps is configured, the DB is never queried.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.deployment_kill_switch import (
    IST,
    _float,
    _float_or_none,
    _int_or_none,
    _ist_date,
    daily_realized_summary,
)


async def check_account_caps(
    db: Any,
    *,
    config: Dict[str, Any],
    engine: Any = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """ACCOUNT-WIDE entry gate (C3) — the caps that span every deployment.

    ``check_live_caps`` below is per-deployment: it queries ``live_trades``
    filtered by ``deployment_id``, so exposure opened by a SIBLING deployment is
    invisible to it. Five open positions or a large realised loss elsewhere could
    not block a new entry, and the account-level guardrails in the safety config
    (``max_open_positions`` / ``daily_loss_limit``) had no production caller at
    all. This closes that hole.

    The verdict is delegated to the existing pure
    :func:`app.live.kill_switch.evaluate_guardrails`, so account semantics —
    including its fail-safe on non-finite inputs — live in exactly one place and
    cannot drift from what the engine enforces.

    Side effect: on a loss breach it calls ``engine.guardrail_tick(...)``, which
    trips the safety latch and halts the engine. A daily-loss breach is an
    ACCOUNT event; refusing only the current entry while leaving every other
    deployment free to trade would not be a stop.

    Returns ``{"allow": bool, "reason": str, "pause": bool}``. Fails CLOSED: if
    the account's exposure cannot be read, we refuse rather than trade blind.

    Config freshness: *config* is the per-cadence snapshot taken by
    ``build_live_deploy_context``, so a threshold edited mid-cadence is applied
    on the next pass, not this one. That is safe because it can only ever
    under-block here: the authoritative latch check is re-read fresh downstream
    at the executor chokepoint (``engine.can_trade()``), which no order can skip.
    The exposure numbers themselves are always read fresh from ``live_trades``.
    """
    from app.live.kill_switch import evaluate_guardrails, is_entry_blocked

    cfg = dict(config or {})

    # The latch is deliberate and sticky — only an explicit reset clears it.
    if is_entry_blocked(cfg):
        return {"allow": False, "reason": "account_latched", "pause": True}

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    today: str = now_utc.astimezone(IST).date().isoformat()

    try:
        # NO deployment filter — this is the whole account.
        rows: List[Dict[str, Any]] = await db.live_trades.find({}).to_list(length=None)
    except Exception as exc:
        return {"allow": False, "reason": f"account_exposure_unavailable:{str(exc)[:60]}",
                "pause": False}

    open_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "OPEN")
    realized_today = daily_realized_summary(rows, today)["net"]
    _open_mtm, _exposure_unknown = open_unrealized_today(
        rows, today, now_utc=now_utc)
    mtm = realized_today + _open_mtm

    # Separate "the numbers are UNKNOWN" from "the numbers say STOP" before
    # delegating. json.loads accepts NaN, so one malformed live_trades row makes
    # the account MTM non-finite; evaluate_guardrails' fail-safe maps that to
    # "broker_stop_loss", which we would forward to guardrail_tick — tripping the
    # sticky latch and halting the engine until a human reset it. Refusing the
    # entry is right (never trade on an unknown P&L); escalating a DATA DEFECT
    # into an account-wide halt is not.
    # An open-position COUNT is always knowable — it needs no P&L at all. Decide
    # the count-based ceiling BEFORE the exposure gate below, so a genuine
    # "too many positions" refusal is never mislabelled as "I can't read the P&L".
    _max_open = _int_or_none(cfg.get("max_open_positions"))
    if _max_open is not None and _max_open > 0 and open_count >= _max_open:
        return {"allow": False, "reason": "account_max_open_block", "pause": False}

    if not math.isfinite(mtm) or _exposure_unknown:
        # Either a malformed row (json.loads accepts NaN) or an OPEN position whose
        # mark is missing/stale — the guard stopped watching it. Both mean the same
        # thing: we do not know the account's MTM. Refuse the entry (never trade on
        # an unknown P&L) but do NOT pause: escalating a data/liveness defect into
        # an account-wide halt is not the same as a measured breach.
        return {"allow": False, "reason": "account_exposure_invalid", "pause": False}

    action = evaluate_guardrails(mtm, open_count, cfg)
    if action == "none":
        return {"allow": True, "reason": "ok", "pause": False}

    if action == "broker_stop_loss" and engine is not None:
        # Halt the ACCOUNT, don't just decline this entry. Never let a failure
        # here mask the refusal we are already returning.
        try:
            await engine.guardrail_tick(mtm, open_count)
        except Exception:
            pass

    return {
        "allow": False,
        "reason": f"account_{action}",
        "pause": action == "broker_stop_loss",
    }


def _live_caps_configured(live: Dict[str, Any]) -> bool:
    """Return True if at least one live cap is set to an actionable value."""
    if not live:
        return False
    return bool(
        (_int_or_none(live.get("max_concurrent")) or 0) > 0
        or (_int_or_none(live.get("max_lots_per_day")) or 0) > 0
        or (_float_or_none(live.get("daily_loss_cap")) or 0.0) > 0.0
    )


def _entered_today(row: Dict[str, Any], today: str) -> bool:
    """True when the row's created_at falls on *today* (IST)."""
    return _ist_date(row.get("created_at")) == today


#: How old a position mark may be before the account exposure is UNKNOWN.
#: The guard marks every cycle (~1.5s), so anything beyond this means it stopped
#: watching — a crashed task, an expired token, an unreadable book.
MARK_STALE_AFTER_SECONDS = 120.0


def open_unrealized_today(
    rows: List[Dict[str, Any]], today: str,
    *, now_utc: Optional[datetime] = None,
) -> Tuple[float, bool]:
    """Return ``(total_unrealized, unknown)`` for OPEN trades entered today (IST).

    ``unknown`` is True when ANY open trade's mark is missing or stale, and the
    caller must treat the whole account exposure as unknown rather than trade on a
    partial picture.

    Before 2026-08-04 this summed ``unrealized_pnl`` unconditionally. Nothing in
    the live path ever wrote that field — ``auto_live`` inserts it as 0.0 and only
    paper analytics updated it — so the sum was always exactly zero and the
    mandatory ``daily_loss_cap`` could see CLOSED trades only. The guard now marks
    every cycle; a mark that STOPS updating must read as UNKNOWN rather than
    silently reverting to that blindness at the moment risk is least observable.
    """
    now = now_utc or datetime.now(timezone.utc)
    total = 0.0
    unknown = False
    for row in rows:
        if str(row.get("status") or "").upper() != "OPEN":
            continue          # settled — carries no unrealized risk
        if not _entered_today(row, today):
            continue
        marked_at = _parse_iso(row.get("marked_at"))
        if marked_at is None:
            unknown = True    # never marked: not the same as a 0.0 loss
            continue
        if (now - marked_at).total_seconds() > MARK_STALE_AFTER_SECONDS:
            unknown = True    # the guard stopped watching this position
            continue
        total += _float(row.get("unrealized_pnl"))
    return total, unknown


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp to an aware UTC datetime; None when unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _open_unrealized_today(rows: List[Dict[str, Any]], today: str,
                           *, now_utc: Optional[datetime] = None) -> float:
    """Back-compat shim — prefer ``open_unrealized_today`` (returns unknown too).

    ``now_utc`` MUST be threaded from the caller's injected clock: staleness
    compared against the wall clock makes every decision path time-dependent and
    unpinnable in tests (the same trap the C2 transmit fence hit).
    """
    total, _unknown = open_unrealized_today(rows, today, now_utc=now_utc)
    return total


def evaluate_risk_supervision(
    *,
    rows: List[Dict[str, Any]],
    deployments: List[Dict[str, Any]],
    account_config: Dict[str, Any],
    today: str,
    now_utc: datetime,
) -> Dict[str, Any]:
    """Decide what a periodic risk sweep should do. PURE — no DB, no clock, no I/O.

    Exists because ``check_account_caps``/``check_live_caps`` have exactly one
    caller each: ``auto_live``, the new-entry path. Nothing evaluated them on a
    timer, so a held position running against the account tripped nothing at all —
    the stop could only fire when a NEW signal arrived, which is precisely what
    does not happen while a position is being held. An unattended bot could bleed
    past its configured limits indefinitely.

    Returns ``halt_account`` (trip the account latch + halt the engine) and
    ``pause_deployment_ids`` (per-deployment ``daily_loss_cap`` breaches).

    It NEVER squares. Flattening is the guard's job and a separate operator
    decision; a timer must not acquire the power to place real orders. Supervision
    blocks new entries — it does not liquidate.

    Unknown exposure (a missing or stale position mark) yields NO action: halting
    trips a STICKY latch that only a human can clear, and a liveness defect must
    not be escalated into that.
    """
    # Local import mirrors check_account_caps — avoids a module-level cycle.
    from app.live.kill_switch import evaluate_guardrails

    open_count = sum(1 for r in rows
                     if str(r.get("status") or "").upper() == "OPEN")
    realized = daily_realized_summary(rows, today)["net"]
    open_mtm, unknown = open_unrealized_today(rows, today, now_utc=now_utc)
    mtm = realized + open_mtm

    out: Dict[str, Any] = {
        "halt_account": False,
        "pause_deployment_ids": [],
        "mtm": mtm,
        "open_count": open_count,
        "exposure_unknown": bool(unknown),
        "reasons": {},
    }
    if unknown or not math.isfinite(mtm):
        return out

    if evaluate_guardrails(mtm, open_count, account_config) == "broker_stop_loss":
        out["halt_account"] = True
        out["reasons"]["account"] = "broker_stop_loss"

    for dep in deployments or ():
        if str(dep.get("status") or "").upper() != "ACTIVE":
            continue
        if str(dep.get("mode") or "").lower() != "live":
            continue
        cap = _float_or_none(((dep.get("risk") or {}).get("live") or {})
                             .get("daily_loss_cap"))
        if cap is None or cap <= 0:
            continue
        dep_id = str(dep.get("id") or "")
        dep_rows = [r for r in rows if str(r.get("deployment_id") or "") == dep_id]
        if not dep_rows:
            continue
        d_real = daily_realized_summary(dep_rows, today)["net"]
        d_open, d_unknown = open_unrealized_today(dep_rows, today, now_utc=now_utc)
        if d_unknown:
            continue
        if d_real + d_open <= -abs(cap):
            out["pause_deployment_ids"].append(dep_id)
            out["reasons"][dep_id] = "daily_loss_cap"
    return out


async def check_live_caps(
    db: Any,
    deployment: Dict[str, Any],
    *,
    capped_lots: int,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Check live caps for *deployment* before opening a new trade of *capped_lots*.

    Args:
        db:          Mongo-like DB object; must expose ``db.live_trades.find(...).to_list(length=None)``.
        deployment:  Deployment document; caps are read from ``deployment["risk"]["live"]``.
        capped_lots: Lots the incoming signal wants to trade.
        now_utc:     UTC datetime for the IST date (defaults to ``datetime.now(timezone.utc)``).

    Returns:
        ``{"allow": bool, "reason": str, "pause": bool}``
    """
    _allow = {"allow": True, "reason": "ok", "pause": False}
    risk = dict(deployment.get("risk") or {})
    live = dict(risk.get("live") or {})

    # Fast path: no caps configured → skip DB entirely.
    #
    # FAIL-CLOSED FOR LIVE MODE. The allow-all fast path is only safe for a
    # deployment that cannot reach the real-order path. Once `mode == "live"` is
    # itself the authorization (the arm ceremony that used to guarantee caps were
    # written is gone), a live deployment with no caps would otherwise trade
    # UNBOUNDED — no lot ceiling, no concurrency ceiling, no daily loss stop.
    # /live/enable requires the caps, so reaching here means the doc was crafted or
    # migrated around that route: refuse rather than trade without limits.
    if not _live_caps_configured(live):
        if str(deployment.get("mode") or "").strip().lower() == "live":
            return {"allow": False, "reason": "live_caps_missing", "pause": True}
        return _allow

    # Poisoned-cap guard: json.loads accepts NaN/Infinity, and every comparison
    # with NaN is False — a NaN daily_loss_cap would silently disable the loss
    # breaker below. A non-finite cap is a misconfigured deployment: refuse.
    _raw_cap = live.get("daily_loss_cap")
    if isinstance(_raw_cap, float) and not math.isfinite(_raw_cap):
        return {"allow": False, "reason": "invalid_daily_loss_cap", "pause": True}

    # Resolve IST today
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    today: str = now_utc.astimezone(IST).date().isoformat()

    deployment_id = str(deployment.get("id") or "")
    rows: List[Dict[str, Any]] = await db.live_trades.find(
        {"deployment_id": deployment_id}
    ).to_list(length=None)

    # ------------------------------------------------------------------
    # 1. daily_loss_cap (pause=True on breach)
    # ------------------------------------------------------------------
    loss_cap = _float_or_none(live.get("daily_loss_cap"))
    if loss_cap is not None and loss_cap > 0:
        # daily_realized_summary filters rows by closed_at date; it treats all
        # rows as "closed trades" — rows without a closed_at are simply skipped.
        realized_today = daily_realized_summary(rows, today)["net"]
        _open_mtm, _exposure_unknown = open_unrealized_today(
            rows, today, now_utc=now_utc)
        if _exposure_unknown:
            # An OPEN position whose mark is missing or stale: the guard has
            # stopped watching it, so this deployment's exposure is unknown.
            # Refuse the entry without pausing — a liveness defect is not a
            # measured breach of the cap.
            return {"allow": False, "reason": "exposure_unknown", "pause": False}
        if realized_today + _open_mtm <= -abs(loss_cap):
            return {"allow": False, "reason": "daily_loss_cap", "pause": True}

    # ------------------------------------------------------------------
    # 2. max_lots_per_day
    # ------------------------------------------------------------------
    max_lots = _int_or_none(live.get("max_lots_per_day"))
    if max_lots is not None and max_lots > 0:
        lots_today = sum(
            int(_float(row.get("lots")))
            for row in rows
            if _entered_today(row, today)
        )
        if lots_today + capped_lots > max_lots:
            return {"allow": False, "reason": "max_lots_per_day", "pause": False}

    # ------------------------------------------------------------------
    # 3. max_concurrent
    # ------------------------------------------------------------------
    max_conc = _int_or_none(live.get("max_concurrent"))
    if max_conc is not None and max_conc > 0:
        open_n = sum(
            1 for row in rows
            if str(row.get("status") or "").upper() == "OPEN"
        )
        if open_n >= max_conc:
            return {"allow": False, "reason": "max_concurrent", "pause": False}

    return _allow
