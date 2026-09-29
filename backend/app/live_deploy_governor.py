"""Per-deployment caps governor for live trading.

Governs new live-trade entries for a deployment by checking three caps
configured under ``deployment["risk"]["live"]``:

  - ``max_concurrent``    — hard cap on open live trades at any moment
  - ``max_lots_per_day``  — rolling daily lots limit (IST calendar day)
  - ``daily_loss_cap``    — positive ₹ magnitude; pause when realized +
                             open-unrealized loss today hits the threshold

(``lots`` is the per-entry ORDER SIZE, not a cap.)

Returns ``{"allow": bool, "reason": str, "pause": bool}``.

Precedence (first match wins):
  0. live_caps_missing      → allow=False, pause=True   (live mode, no cap set)
  0. invalid_daily_loss_cap → allow=False, pause=True   (non-finite cap)
  1. exposure_unknown       → allow=False, pause=False  (loss cap set, a mark
                                                          missing or stale)
  1. daily_loss_cap         → allow=False, pause=True
  2. lots_unmeasurable      → allow=False, pause=False  (a poisoned lots row)
  2. max_lots_per_day       → allow=False, pause=False
  3. max_concurrent         → allow=False, pause=False
  4. else                   → allow=True,  reason="ok", pause=False

If none of the three caps is configured, the DB is never queried.

ONE COMPUTATION, TWO CALLERS. The checks are split into pure steps —
``precheck_live_caps`` → ``measure_exposure`` → ``decide_live_caps`` (and
``decide_account_caps`` for the account layer) — which the enforcing
``check_*`` functions and the read-only ``describe_live_caps`` both run. The
operator's screen therefore shows the governor's own numbers, never a second
derivation that could drift from them (the defect class of the 2026-09-16 BFO
marking bug and the request-vs-resolved envelope).
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
    from app.live.kill_switch import is_entry_blocked

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

    m = measure_exposure(rows, today=today, now_utc=now_utc)
    verdict, action = decide_account_caps(cfg, m)

    if action == "broker_stop_loss" and engine is not None:
        # Halt the ACCOUNT, don't just decline this entry. Never let a failure
        # here mask the refusal we are already returning. This side effect lives
        # HERE, in the enforcing path only — describe_live_caps runs the same
        # decision and never trips anything.
        try:
            await engine.guardrail_tick(m["mtm"], m["open_count"])
        except Exception:
            pass
    return verdict


def decide_account_caps(cfg: Dict[str, Any],
                        m: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """The account verdict from a measured exposure. PURE; ``(verdict, action)``.

    ``action`` is ``evaluate_guardrails``' answer (or ``"none"``) so the enforcing
    caller can apply its side effect; the latch itself is checked before any
    measuring, by the caller.
    """
    from app.live.kill_switch import evaluate_guardrails

    open_count = m["open_count"]
    mtm = m["mtm"]
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
        return {"allow": False, "reason": "account_max_open_block", "pause": False}, "none"

    if not math.isfinite(mtm) or m["exposure_unknown"]:
        # Either a malformed row (json.loads accepts NaN) or an OPEN position whose
        # mark is missing/stale — the guard stopped watching it. Both mean the same
        # thing: we do not know the account's MTM. Refuse the entry (never trade on
        # an unknown P&L) but do NOT pause: escalating a data/liveness defect into
        # an account-wide halt is not the same as a measured breach.
        return {"allow": False, "reason": "account_exposure_invalid", "pause": False}, "none"

    action = evaluate_guardrails(mtm, open_count, cfg)
    if action == "none":
        return {"allow": True, "reason": "ok", "pause": False}, action
    return {
        "allow": False,
        "reason": f"account_{action}",
        "pause": action == "broker_stop_loss",
    }, action


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


def _clock(now_utc: Optional[datetime]) -> Tuple[datetime, str]:
    """(aware UTC now, IST calendar date) — the one clock read of a decision."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc, now_utc.astimezone(IST).date().isoformat()


def precheck_live_caps(
    deployment: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """``(live caps dict, verdict-or-None)`` — the checks that need NO data.

    A verdict here is final: no DB read happens after it.
    """
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
            return live, {"allow": False, "reason": "live_caps_missing", "pause": True}
        return live, {"allow": True, "reason": "ok", "pause": False}

    # Poisoned-cap guard: json.loads accepts NaN/Infinity, and every comparison
    # with NaN is False — a NaN daily_loss_cap would silently disable the loss
    # breaker below. A non-finite cap is a misconfigured deployment: refuse.
    _raw_cap = live.get("daily_loss_cap")
    if isinstance(_raw_cap, float) and not math.isfinite(_raw_cap):
        return live, {"allow": False, "reason": "invalid_daily_loss_cap", "pause": True}
    return live, None


def measure_exposure(rows: List[Dict[str, Any]], *, today: str,
                     now_utc: datetime) -> Dict[str, Any]:
    """Everything the governor measures from ``live_trades`` rows. PURE; never raises.

    The same measurement serves one deployment (its rows) or the whole account
    (all rows). ``lots_today`` is None when a poisoned ``lots`` value makes it
    unmeasurable; ``open_unrealized`` is a PARTIAL sum whenever
    ``exposure_unknown`` is True and must never be shown as a number then.

    Two diagnostics feed no decision and exist for the operator:
    ``open_rows_prior_days`` (OPEN rows from an earlier day — they hold a
    concurrency slot but are outside the loss sum) and
    ``closed_today_null_realized`` (closed today with no P&L journalled — the loss
    sum counts them as zero).
    """
    realized_today = daily_realized_summary(rows, today)["net"]
    open_unrealized, unknown = open_unrealized_today(rows, today, now_utc=now_utc)
    try:
        lots_today: Optional[int] = sum(int(_float(r.get("lots")))
                                        for r in rows if _entered_today(r, today))
    except (ValueError, OverflowError):
        lots_today = None
    is_open = [str(r.get("status") or "").upper() == "OPEN" for r in rows]
    return {
        "realized_today": realized_today,
        "open_unrealized": open_unrealized,
        "exposure_unknown": bool(unknown),
        "mtm": realized_today + open_unrealized,
        "lots_today": lots_today,
        "open_count": sum(is_open),
        "open_rows_prior_days": sum(1 for r, o in zip(rows, is_open)
                                    if o and not _entered_today(r, today)),
        "closed_today_null_realized": sum(
            1 for r in rows
            if str(r.get("status") or "").upper() == "CLOSED"
            and not r.get("exit_day_unknown")
            and _ist_date(r.get("closed_at")) == today
            and r.get("realized_pnl") is None),
    }


def decide_live_caps(live: Dict[str, Any], m: Dict[str, Any], *,
                     capped_lots: int) -> Dict[str, Any]:
    """The deployment verdict from a measured exposure. PURE."""
    # ------------------------------------------------------------------
    # 1. daily_loss_cap (pause=True on breach)
    # ------------------------------------------------------------------
    loss_cap = _float_or_none(live.get("daily_loss_cap"))
    if loss_cap is not None and loss_cap > 0:
        if m["exposure_unknown"]:
            # An OPEN position whose mark is missing or stale: the guard has
            # stopped watching it, so this deployment's exposure is unknown.
            # Refuse the entry without pausing — a liveness defect is not a
            # measured breach of the cap.
            return {"allow": False, "reason": "exposure_unknown", "pause": False}
        if m["realized_today"] + m["open_unrealized"] <= -abs(loss_cap):
            return {"allow": False, "reason": "daily_loss_cap", "pause": True}

    # ------------------------------------------------------------------
    # 2. max_lots_per_day
    # ------------------------------------------------------------------
    max_lots = _int_or_none(live.get("max_lots_per_day"))
    if max_lots is not None and max_lots > 0:
        if m["lots_today"] is None:
            # A poisoned lots row (NaN/inf) used to raise out of the check. Still
            # fail closed, but visibly — an exception explains nothing on screen.
            return {"allow": False, "reason": "lots_unmeasurable", "pause": False}
        if m["lots_today"] + capped_lots > max_lots:
            return {"allow": False, "reason": "max_lots_per_day", "pause": False}

    # ------------------------------------------------------------------
    # 3. max_concurrent
    # ------------------------------------------------------------------
    max_conc = _int_or_none(live.get("max_concurrent"))
    if max_conc is not None and max_conc > 0:
        if m["open_count"] >= max_conc:
            return {"allow": False, "reason": "max_concurrent", "pause": False}

    return {"allow": True, "reason": "ok", "pause": False}


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
    live, verdict = precheck_live_caps(deployment)
    if verdict is not None:
        return verdict
    now_utc, today = _clock(now_utc)
    deployment_id = str(deployment.get("id") or "")
    rows: List[Dict[str, Any]] = await db.live_trades.find(
        {"deployment_id": deployment_id}
    ).to_list(length=None)
    return decide_live_caps(live, measure_exposure(rows, today=today, now_utc=now_utc),
                            capped_lots=capped_lots)


# ---------------------------------------------------------------------------
# Read-only introspection — "why can't this deployment trade right now?"
# ---------------------------------------------------------------------------

def _fin(value: Any) -> Optional[float]:
    """A finite float or None. The status route serializes with allow_nan=False,
    so one NaN anywhere would 500 the whole batch."""
    f = _float_or_none(value)
    return f if f is not None and math.isfinite(f) else None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return _int_or_none(value)
    except (OverflowError, ValueError):
        return None


async def describe_live_caps(
    db: Any,
    deployment: Dict[str, Any],
    *,
    now_utc: Optional[datetime] = None,
    rows: Optional[List[Dict[str, Any]]] = None,
    account_config: Optional[Dict[str, Any]] = None,
    account_rows: Optional[List[Dict[str, Any]]] = None,
    connected: Optional[bool] = None,
) -> Dict[str, Any]:
    """The governor's own answer, for display. READ-ONLY; NEVER raises.

    Runs exactly the steps ``check_live_caps`` / ``check_account_caps`` run —
    precheck, measure, decide — so what the screen says IS what enforcement would
    do. It has no ``engine`` parameter and so can never trip the account latch the
    enforcing account check can; it writes nothing and makes no broker call.

    ``rows`` (this deployment's ``live_trades``), ``account_rows`` (all of them) and
    ``account_config`` (the safety config) may be passed in so a batch of
    deployments shares one read; anything not passed is read here. ``connected``
    (broker session connected and not expired) enables the authorization verdict
    the entry path checks FIRST; without it that verdict is None.

    Any value that cannot be known is None — never 0. ``binding`` is the first
    refusal in the entry path's own order: authorization → account → deployment.
    """
    out: Dict[str, Any] = {
        "applicable": False, "caps": {}, "next_entry_lots": None,
        "consumed": None, "verdict": None, "authorization": None,
        "account_verdict": None, "account": None, "binding": None, "error": None,
    }
    try:
        now_utc, today = _clock(now_utc)
        out["applicable"] = str(deployment.get("mode") or "").strip().lower() == "live"
        live_raw = ((deployment.get("risk") or {}).get("live") or {})
        out["caps"] = {
            "lots": _safe_int(live_raw.get("lots")),
            "max_lots_per_day": _safe_int(live_raw.get("max_lots_per_day")),
            "max_concurrent": _safe_int(live_raw.get("max_concurrent")),
            "daily_loss_cap": _fin(live_raw.get("daily_loss_cap")),
        }

        # --- authorization (mode, hold, broker session, entry cutoff) --------
        if connected is not None:
            from app.live.mode import is_deployment_live_allowed
            ok, why = is_deployment_live_allowed(deployment, now_utc,
                                                 connected=bool(connected))
            out["authorization"] = {"allow": bool(ok), "reason": str(why)}

        # --- account layer ------------------------------------------------------
        if account_config is None:
            account_verdict = {"allow": False, "reason": "account_config_unavailable",
                               "pause": False}
            account_max = None
        else:
            from app.live.kill_switch import is_entry_blocked
            cfg = dict(account_config)
            account_max = _safe_int(cfg.get("max_lots_per_order"))
            if account_rows is None:
                try:
                    account_rows = await db.live_trades.find({}).to_list(length=None)
                except Exception as exc:
                    account_rows = None
                    account_verdict = {
                        "allow": False, "pause": False,
                        "reason": f"account_exposure_unavailable:{str(exc)[:60]}"}
            am = (measure_exposure(account_rows, today=today, now_utc=now_utc)
                  if account_rows is not None else None)
            if is_entry_blocked(cfg):
                account_verdict = {"allow": False, "reason": "account_latched",
                                   "pause": True}
            elif am is not None:
                account_verdict, _action = decide_account_caps(cfg, am)
            out["account"] = {
                "open_count": am["open_count"] if am else None,
                "mtm": (None if am is None or am["exposure_unknown"]
                        else _fin(am["mtm"])),
                "exposure_unknown": bool(am["exposure_unknown"]) if am else None,
                "limits": {
                    "max_open_positions": _safe_int(cfg.get("max_open_positions")),
                    "daily_loss_limit": _fin(cfg.get("daily_loss_limit")),
                    "profit_lock_target": _fin(cfg.get("profit_lock_target")),
                    "max_lots_per_order": account_max,
                },
                "stops": {
                    "latched": bool(is_entry_blocked(cfg)),
                    "engine_halted": bool(cfg.get("engine_halted")),
                    "engine_halt_reason": cfg.get("engine_halt_reason"),
                },
            }
        out["account_verdict"] = account_verdict

        # --- deployment layer ---------------------------------------------------
        # Entry size exactly as auto_live sizes it. An account ceiling below 1
        # disables live entirely (build_live_deploy_context), so no size exists.
        if account_max is not None and account_max >= 1:
            from app.auto_live import resolve_capped_lots
            out["next_entry_lots"] = resolve_capped_lots(deployment, account_max)
        if rows is None:
            if account_rows is not None:
                dep_id = str(deployment.get("id") or "")
                rows = [r for r in account_rows
                        if str(r.get("deployment_id") or "") == dep_id]
            else:
                rows = await db.live_trades.find(
                    {"deployment_id": str(deployment.get("id") or "")}
                ).to_list(length=None)
        m = measure_exposure(rows, today=today, now_utc=now_utc)
        live, verdict = precheck_live_caps(deployment)
        if verdict is None:
            verdict = decide_live_caps(live, m,
                                       capped_lots=out["next_entry_lots"] or 1)
        out["verdict"] = verdict

        loss_cap = out["caps"]["daily_loss_cap"]
        unknown = m["exposure_unknown"]
        day_pnl = None if unknown else _fin(m["mtm"])
        out["consumed"] = {
            "lots_today": m["lots_today"],
            "concurrent_now": m["open_count"],
            "realized_today": _fin(m["realized_today"]),
            "open_unrealized": None if unknown else _fin(m["open_unrealized"]),
            "day_pnl": day_pnl,
            # The governor breaches exactly when headroom <= 0 (same inequality).
            "loss_headroom": (None if day_pnl is None or loss_cap is None or loss_cap <= 0
                              else round(day_pnl + abs(loss_cap), 2)),
            "exposure_unknown": bool(unknown),
            "open_rows_prior_days": m["open_rows_prior_days"],
            "closed_today_null_realized": m["closed_today_null_realized"],
        }

        # --- the binding refusal, in the entry path's own order -----------------
        for layer, v in (("authorization", out["authorization"]),
                         ("account", out["account_verdict"]),
                         ("deployment", out["verdict"])):
            if v is not None and not v.get("allow"):
                out["binding"] = {"layer": layer, "reason": v.get("reason"),
                                  "pause": bool(v.get("pause"))}
                break
        if out["binding"] is None and account_max is not None and account_max < 1:
            out["binding"] = {"layer": "account", "pause": False,
                              "reason": "account_max_lots_per_order_unset"}
    except Exception as exc:  # never raise out of a status route
        out["error"] = f"describe_failed:{type(exc).__name__}"
    return out
