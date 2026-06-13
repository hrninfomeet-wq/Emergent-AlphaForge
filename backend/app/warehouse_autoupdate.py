"""Automatic warehouse catch-up.

Keeps the local warehouse current without manual work by running the existing
data-hygiene plan + execute whenever it is sensible to do so:

  - on backend startup (if Upstox is connected),
  - immediately after a successful Upstox OAuth, and
  - once per day at ~18:00 IST (after market close).

Design notes / why this is safe:
  - Upstox historical returns EMPTY for the current trading day, so auto-update
    can only bring the warehouse up to *yesterday's* close. Today's bars come
    from the live tick -> 1m roller, so there is no gap.
  - Upstox OAuth expires daily, so "run on connect" is the natural trigger; the
    daily timer is a backstop for days the user connects in the morning.
  - The hygiene plan is calendar-aware (skips NSE holidays / weekends), so the
    catch-up never chases non-trading days forever.
  - A single in-flight guard prevents overlapping runs (startup + OAuth + timer
    could otherwise fire close together).

This module keeps the *decision* logic pure and testable; the actual plan and
execute callables are injected by server.py so this stays free of broker and DB
imports.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_DAILY_HOUR_IST = 18
DEFAULT_DAILY_MINUTE_IST = 0


def _now_ist(now_utc: Optional[datetime] = None) -> datetime:
    return (now_utc or datetime.now(timezone.utc)).astimezone(IST)


def should_run_autoupdate(
    *,
    enabled: bool,
    connection_status: Dict[str, Any],
    in_progress: bool,
) -> tuple[bool, str]:
    """Pure guard: decide whether an auto-update run should start.

    Returns (run, reason). `reason` explains a skip or confirms a go.
    """
    if not enabled:
        return False, "disabled"
    if in_progress:
        return False, "already_running"
    if not connection_status.get("connected"):
        return False, "upstox_not_connected"
    if connection_status.get("expired"):
        return False, "upstox_token_expired"
    return True, "ok"


def seconds_until_next_daily_run(
    now_utc: Optional[datetime] = None,
    *,
    hour_ist: int = DEFAULT_DAILY_HOUR_IST,
    minute_ist: int = DEFAULT_DAILY_MINUTE_IST,
) -> float:
    """Seconds from now until the next HH:MM IST occurrence (today or tomorrow)."""
    now = _now_ist(now_utc)
    target = now.replace(hour=hour_ist, minute=minute_ist, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


@dataclass
class AutoUpdateState:
    """In-memory status of the auto-update worker (exposed via the API)."""
    enabled: bool = True
    in_progress: bool = False
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_status: Optional[str] = None          # ok | skipped | error
    last_reason: Optional[str] = None           # trigger reason or skip reason
    last_submitted_count: int = 0
    last_actions_planned: int = 0
    last_error: Optional[str] = None
    runs_count: int = 0
    history: list = field(default_factory=list)  # recent run summaries (capped)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "in_progress": self.in_progress,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_status": self.last_status,
            "last_reason": self.last_reason,
            "last_submitted_count": self.last_submitted_count,
            "last_actions_planned": self.last_actions_planned,
            "last_error": self.last_error,
            "runs_count": self.runs_count,
            "history": list(self.history[-10:]),
        }


# Module-level singleton state.
STATE = AutoUpdateState()


async def run_autoupdate_once(
    *,
    reason: str,
    connection_status_fn: Callable[[], Awaitable[Dict[str, Any]]],
    compute_plan_fn: Callable[[], Awaitable[Dict[str, Any]]],
    execute_plan_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    pre_run_fn: Optional[Callable[[], Awaitable[Any]]] = None,
    state: AutoUpdateState = STATE,
) -> Dict[str, Any]:
    """Run a single catch-up: guard -> [pre_run] -> plan -> execute (only if there
    are actions).

    `pre_run_fn` is an optional best-effort side-task run once the guard passes
    (used to top up India VIX alongside spot/option catch-up so the daily timer
    keeps VIX current too, not just app startup).

    Returns a summary dict. Never raises; failures are captured in the summary
    and the state so a scheduler loop keeps running.
    """
    try:
        connection_status = await connection_status_fn()
    except Exception as exc:  # connection probe failure is a soft skip
        connection_status = {}
        log.warning("autoupdate: connection probe failed: %s", exc)

    run, guard_reason = should_run_autoupdate(
        enabled=state.enabled,
        connection_status=connection_status,
        in_progress=state.in_progress,
    )
    if not run:
        summary = {"status": "skipped", "reason": guard_reason, "trigger": reason}
        state.last_status = "skipped"
        state.last_reason = f"{reason}:{guard_reason}"
        return summary

    state.in_progress = True
    started = datetime.now(timezone.utc).isoformat()
    state.last_started_at = started
    state.last_reason = reason
    state.last_error = None
    if pre_run_fn is not None:
        try:
            await pre_run_fn()
        except Exception as exc:  # best-effort; never block the catch-up
            log.warning("autoupdate(%s): pre_run side-task failed: %s", reason, exc)
    try:
        plan = await compute_plan_fn()
        actions_planned = int((plan.get("summary") or {}).get("total_actions") or 0)
        state.last_actions_planned = actions_planned

        submitted_count = 0
        if actions_planned > 0:
            result = await execute_plan_fn(plan)
            submitted_count = int(result.get("submitted_count") or 0)
        state.last_submitted_count = submitted_count

        state.last_status = "ok"
        summary = {
            "status": "ok",
            "trigger": reason,
            "actions_planned": actions_planned,
            "submitted_count": submitted_count,
            "overall_status": (plan.get("summary") or {}).get("overall_status"),
        }
        log.info(
            "autoupdate(%s): %d actions planned, %d jobs submitted",
            reason, actions_planned, submitted_count,
        )
        return summary
    except Exception as exc:
        state.last_status = "error"
        state.last_error = str(exc)[:300]
        log.exception("autoupdate(%s) failed", reason)
        return {"status": "error", "trigger": reason, "error": str(exc)[:300]}
    finally:
        state.in_progress = False
        state.last_finished_at = datetime.now(timezone.utc).isoformat()
        state.runs_count += 1
        state.history.append({
            "trigger": reason,
            "status": state.last_status,
            "actions_planned": state.last_actions_planned,
            "submitted_count": state.last_submitted_count,
            "finished_at": state.last_finished_at,
            "error": state.last_error,
        })
        state.history = state.history[-10:]


async def daily_autoupdate_loop(
    *,
    connection_status_fn: Callable[[], Awaitable[Dict[str, Any]]],
    compute_plan_fn: Callable[[], Awaitable[Dict[str, Any]]],
    execute_plan_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    pre_run_fn: Optional[Callable[[], Awaitable[Any]]] = None,
    hour_ist: int = DEFAULT_DAILY_HOUR_IST,
    minute_ist: int = DEFAULT_DAILY_MINUTE_IST,
    state: AutoUpdateState = STATE,
) -> None:
    """Sleep until the next HH:MM IST, run a catch-up, repeat. Cancellation-safe.

    `pre_run_fn` (e.g. the India VIX top-up) runs before each daily catch-up so
    the timer keeps VIX current too, matching the startup/connect trigger."""
    log.info("Warehouse auto-update daily loop initialized (%02d:%02d IST)", hour_ist, minute_ist)
    while True:
        try:
            sleep_s = seconds_until_next_daily_run(hour_ist=hour_ist, minute_ist=minute_ist)
            await asyncio.sleep(sleep_s)
            await run_autoupdate_once(
                reason="daily_timer",
                connection_status_fn=connection_status_fn,
                compute_plan_fn=compute_plan_fn,
                execute_plan_fn=execute_plan_fn,
                pre_run_fn=pre_run_fn,
                state=state,
            )
        except asyncio.CancelledError:
            log.info("Warehouse auto-update loop cancelled")
            return
        except Exception as exc:
            log.exception("Warehouse auto-update loop error: %s", exc)
            await asyncio.sleep(60.0)
