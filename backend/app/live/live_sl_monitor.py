"""Software-monitored exits for LIVE option-buying positions (P1.5).

Research-backed redesign: instead of a RESTING broker SL order (which reserves
naked-short margin and caused the ₹2.16L square-off reject), the stop/target/
trailing logic runs in SOFTWARE.  A monitor polls the live premium every ~1.5 s
and, when an exit condition fires, squares the position through the SAME margin-
safe choke-point used everywhere else (cancel-all-working → confirm → close).

This module is split into:

1. ``build_monitor_state`` — pure constructor that turns an entry premium + an
   exit config (stop/target as pct or pts, trailing mode) into an absolute-level
   monitor state.

2. ``evaluate_exit`` — the PURE decision function.  Given the current state and
   the latest premium, it updates the peak + any trailing stop and decides
   whether to exit and why.  No I/O; fully deterministic; the audit-critical core.

3. ``LiveSLMonitor`` — a thin async lifecycle wrapper (mirrors LiveExitMonitor)
   that polls an injected ltp lookup, runs ``evaluate_exit`` per position, and on
   a trigger calls an injected ``square_fn`` (the margin-safe square-off).  It
   NEVER places a resting SL and NEVER raises out of its cycle.

Trailing modes (AlgoTest parity), all for a LONG option (exit = SELL):
- ``none``        — fixed stop + optional target only.
- ``breakeven``   — once premium reaches ``trigger``, move the stop up to entry.
- ``lock``        — once premium reaches ``trigger``, lock the stop at ``lock_to``
                    (entry + locked profit).  Stop never drops back.
- ``lock_trail``  — lock at ``trigger`` then raise the stop by ``raise_by`` for
                    every ``step`` of additional premium above the trigger.
- ``trail``       — classic trailing stop: stop = peak − ``gap`` (rises with the
                    peak premium, never falls).

INVARIANT: the effective stop is MONOTONIC NON-DECREASING.  A trailing stop that
could ratchet DOWN would hand back locked profit — every update uses max().
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.execution_policy import resolve_premium_levels
from app.exit_controls import ExitControlsConfig, effective_premium_stop
from app.session_spec import OPTIONS, segment_close_time

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)

_VALID_MODES = ("none", "breakeven", "lock", "lock_trail", "trail", "stepped_xy")


def _in_market_hours(now_utc: Optional[datetime] = None) -> bool:
    """True while the positions this monitor protects are still tradeable.

    Bounded by the OPTIONS close: from 2026-08-03 the derivatives segment trades
    until 15:40, so a 15:30 bound would stop watching a live stop-loss while the
    position could still move for another ten minutes.
    """
    ist = (now_utc or datetime.now(timezone.utc)) + _IST
    if ist.weekday() >= 5:
        return False
    return dtime(9, 15) <= ist.time() < segment_close_time(ist.strftime("%Y-%m-%d"), OPTIONS)


def _finite_pos(x: Any) -> bool:
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
        and x > 0
    )


# ---------------------------------------------------------------------------
# 1. Pure constructor — entry + config → absolute-level monitor state
# ---------------------------------------------------------------------------

def build_monitor_state(
    entry: float,
    *,
    stop_pct: Any = None,
    stop_pts: Any = None,
    target_pct: Any = None,
    target_pts: Any = None,
    trail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a monitor state for a LONG option position from an exit config.

    stop/target are resolved to ABSOLUTE premium levels via resolve_premium_levels
    (the same helper the backtest + order builder use, so live mirrors sim).
    ``entry`` must be a finite positive premium.

    ``trail`` (optional) keys, all in absolute premium POINTS unless noted:
        mode: one of _VALID_MODES (default "none")
        trigger:  premium at which lock/breakeven activates (lock/lock_trail/breakeven)
        lock_profit: profit (pts above entry) the stop locks to (lock/lock_trail)
        step:     additional premium per trail increment (lock_trail)
        raise_by: how much the locked stop rises per step (lock_trail)
        gap:      trail gap below the running peak (trail mode)

    Returns a state dict consumed by ``evaluate_exit``.  Raises ValueError on a
    non-finite/positive entry (a position with no real entry premium is a bug).
    """
    if not _finite_pos(entry):
        raise ValueError(f"entry must be a finite positive premium, got {entry!r}")

    stop_level, target_level = resolve_premium_levels(
        entry,
        stop_pts=stop_pts,
        stop_pct=stop_pct,
        target_pts=target_pts,
        target_pct=target_pct,
        stop_floor=0.05,
        ndigits=2,
    )

    # Read the NESTED exit_controls overlay before `trail` is narrowed to the flat
    # schema below. Both shapes arrive through this one parameter and compose.
    ec = _parse_exit_controls(trail)

    trail = dict(trail or {})
    mode = str(trail.get("mode") or "none").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"trail mode {mode!r} not in {_VALID_MODES}")

    # An effective stop is required to monitor a stop-based exit; if the config
    # gives no stop AND no target AND no trail, there is nothing to monitor.
    if stop_level is None and target_level is None and mode == "none":
        raise ValueError("monitor needs at least one of stop/target/trail to be set")

    state: Dict[str, Any] = {
        "entry": round(float(entry), 2),
        "initial_stop": stop_level,            # may be None
        "stop_level": stop_level,              # current effective stop (mutates up)
        "target_level": target_level,          # may be None
        "peak": round(float(entry), 2),
        "mode": mode,
        "activated": False,                    # lock/breakeven engaged?
        "trail": {
            "trigger": trail.get("trigger"),
            "lock_profit": trail.get("lock_profit"),
            "step": trail.get("step"),
            "raise_by": trail.get("raise_by"),
            "gap": trail.get("gap"),
            "x": trail.get("x"),
            "y": trail.get("y"),
        },
    }
    # ABSENT, not None, when there is no overlay — so a state dict built by any
    # existing caller stays byte-identical rather than merely equivalent.
    if ec is not None:
        state["exit_controls"] = ec
    return state


def _parse_exit_controls(trail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A nested deployment ``risk.exit_controls`` block -> the flat, JSON-serializable
    overlay carried on the monitor state, or ``None`` when there is no live overlay.

    Returns None for: a non-dict; a FLAT trail dict (``{"mode": ...}`` has no
    ``enabled`` key, so ``from_dict`` yields ``enabled=False``); ``enabled: false``;
    and an enabled block whose every overlay is inert — exactly the arming
    predicates ``effective_premium_stop`` itself applies. A None return leaves the
    state dict identical to what this module produced before the overlay existed.

    Coercion is deliberately NOT re-implemented: ``ExitControlsConfig.from_dict`` is
    the single parser, shared with the sim and paper, and it never raises.

    There is no explicit shape discriminator, on purpose. A ``if trail.get("mode")``
    guard would silently drop the overlay from a dict carrying BOTH keys — the exact
    class of bug this fixes, since ``"none"`` is a truthy string.
    """
    cfg = ExitControlsConfig.from_dict(trail if isinstance(trail, dict) else None)
    if not cfg.enabled:
        return None
    if not ((cfg.be_trigger and cfg.be_trigger > 0)
            or (cfg.trail_distance and cfg.trail_distance > 0)):
        return None
    return {"enabled": True, "unit": cfg.unit,
            "be_trigger": float(cfg.be_trigger), "be_lock": float(cfg.be_lock),
            "trail_activation": float(cfg.trail_activation),
            "trail_distance": float(cfg.trail_distance)}


# ---------------------------------------------------------------------------
# 2. Pure decision — update trailing + decide exit
# ---------------------------------------------------------------------------

def _raise_stop(state: Dict[str, Any], candidate: Optional[float]) -> None:
    """Raise stop_level to ``candidate`` iff it is higher (monotonic invariant)."""
    if candidate is None:
        return
    cur = state["stop_level"]
    new = round(float(candidate), 2)
    if cur is None or new > cur:
        state["stop_level"] = new


def _apply_exit_controls(state: Dict[str, Any], running_max: float) -> None:
    """Raise ``state["stop_level"]`` by the CANONICAL premium-stop overlay.

    Delegates to ``exit_controls.effective_premium_stop`` — the same decider the sim
    (``option_backtest``) and paper (``paper_auto``) call — so the three can never
    drift. That module's docstring already claims to be the single source; live was
    the one caller that never called it, and silently discarded every overlay
    because it spoke the flat-trail dialect instead.

    No-op when the state carries no overlay. Writes ONLY through ``_raise_stop``, so
    it can never lower a live stop. This mirrors the ``stepped_xy`` branch, which
    already delegates to the backtest's own ``stepped_trail_stop`` helper and feeds
    the result through the same ratchet — the pattern this module already endorses.

    ``running_max`` must be the peak through the PREVIOUS observation, for
    look-ahead parity with the sim and paper; it is floored at ``entry`` to mirror
    their seeds (``running_max_prev[0] = entry_price`` / ``running_max_premium =
    fill_entry``). ``state["exit_controls"]`` is read-only — it is shallow-aliased
    across ``evaluate_exit``'s state copy, so mutating it would leak backwards.
    """
    ec = state.get("exit_controls")
    if not ec:
        return
    cfg = ExitControlsConfig(
        enabled=True, unit=ec["unit"], be_trigger=ec["be_trigger"],
        be_lock=ec["be_lock"], trail_activation=ec["trail_activation"],
        trail_distance=ec["trail_distance"])
    entry = float(state["entry"])
    rm = max(float(running_max), entry)
    # base_stop is the STATIC initial_stop, not the already-ratcheted stop_level:
    # it matches the sim's base and keeps the returned level interpretable on its
    # own rather than coupled to whatever another mode has ratcheted to.
    level = effective_premium_stop(entry=entry, running_max=rm,
                                   base_stop=state.get("initial_stop"), cfg=cfg)
    if level is None:
        return
    # Record WHICH candidate binds, so the exit reason can name it honestly
    # (the sim distinguishes OPTION_BREAKEVEN_STOP from OPTION_TRAIL_STOP).
    be_level = None
    if cfg.be_trigger > 0:
        if cfg.unit == "pts":
            trigger_level, lock_level = entry + cfg.be_trigger, entry + cfg.be_lock
        else:
            trigger_level = entry * (1.0 + cfg.be_trigger)
            lock_level = entry * (1.0 + cfg.be_lock)
        if rm >= trigger_level:
            be_level = lock_level
    state["ec_stop"] = round(float(level), 2)
    state["ec_bind"] = ("breakeven"
                        if be_level is not None and abs(be_level - level) <= 1e-9
                        else "trailing")
    _raise_stop(state, level)


def evaluate_exit(state: Dict[str, Any], ltp: Any) -> Dict[str, Any]:
    """Decide whether to exit a LONG option position at the current premium.

    Pure + deterministic.  Returns ``{"exit": bool, "reason": str|None,
    "state": <updated state>}``.  The returned state is a NEW dict (the input is
    not mutated) so callers can persist/compare snapshots safely.

    Order of operations:
      1. Guard ltp — a non-finite/≤0 premium is stale data: NO exit, state
         unchanged (we never square on a bad tick; the time-cap is the backstop).
      2. Update peak = max(peak, ltp).
      3. Apply the trailing rule to possibly RAISE the stop (never lower it).
      4. Exit if ltp <= stop_level (reason: trailing_stop if the stop has been
         raised above the initial, breakeven if moved to entry, else stop), OR
         ltp >= target_level (reason: target).

    The stop is monotonic non-decreasing — a trailing stop can only ratchet up.
    """
    # Work on a copy so the input is never mutated.
    new_state = dict(state)
    new_state["trail"] = dict(state.get("trail") or {})

    if not _finite_pos(ltp):
        # Stale/garbage tick — never act on it.
        return {"exit": False, "reason": None, "state": new_state}

    ltp = round(float(ltp), 2)
    entry = new_state["entry"]
    mode = new_state.get("mode", "none")
    trail = new_state["trail"]

    prev_peak = new_state["peak"]
    # 2. Peak
    if ltp > new_state["peak"]:
        new_state["peak"] = ltp
    peak = new_state["peak"]

    # 3. Trailing — raise the stop where applicable.
    trigger = trail.get("trigger")
    if mode == "breakeven":
        if not new_state["activated"] and _finite_pos(trigger) and ltp >= trigger:
            new_state["activated"] = True
            _raise_stop(new_state, entry)  # move stop to break-even
    elif mode == "lock":
        if not new_state["activated"] and _finite_pos(trigger) and ltp >= trigger:
            new_state["activated"] = True
            lock_profit = trail.get("lock_profit") or 0.0
            _raise_stop(new_state, entry + float(lock_profit))
    elif mode == "lock_trail":
        if not new_state["activated"] and _finite_pos(trigger) and ltp >= trigger:
            new_state["activated"] = True
            lock_profit = trail.get("lock_profit") or 0.0
            _raise_stop(new_state, entry + float(lock_profit))
        if new_state["activated"] and _finite_pos(trail.get("step")):
            lock_profit = trail.get("lock_profit") or 0.0
            raise_by = trail.get("raise_by") or 0.0
            step = float(trail["step"])
            steps = math.floor((ltp - float(trigger)) / step) if ltp > trigger else 0
            if steps > 0 and raise_by:
                _raise_stop(new_state, entry + float(lock_profit) + steps * float(raise_by))
    elif mode == "trail":
        gap = trail.get("gap")
        if _finite_pos(gap):
            _raise_stop(new_state, peak - float(gap))
    elif mode == "stepped_xy":
        # AlgoTest discrete X-Y ratchet, delegated to the SAME pure helper the
        # backtest uses (byte-parity incl. the never-above-high-water cap). The
        # ratchet consumes the peak through the PREVIOUS observation (prev_peak)
        # so the tick that sets a new high is never judged against a stop it
        # raised itself — mirroring the backtest's loop-end high-water update.
        x, y = trail.get("x"), trail.get("y")
        initial = new_state.get("initial_stop")
        if _finite_pos(x) and _finite_pos(y) and initial is not None:
            from app.premium_momentum import stepped_trail_stop
            _raise_stop(new_state, stepped_trail_stop(
                entry_premium=entry, running_high=prev_peak,
                base_stop=float(initial), x=float(x), y=float(y)))

    # 3b. Canonical exit_controls overlay — the SAME decider the sim and paper use,
    #     on the prev-peak basis for look-ahead parity. Composes with whichever flat
    #     mode ran above, through the single monotonic ratchet.
    _apply_exit_controls(new_state, prev_peak)

    # 4. Exit decision.
    stop_level = new_state["stop_level"]
    target_level = new_state["target_level"]

    if stop_level is not None and ltp <= stop_level:
        initial = new_state.get("initial_stop")
        if new_state["activated"] and stop_level <= entry + 1e-9 and stop_level >= entry - 1e-9:
            reason = "breakeven_stop"
        elif (new_state.get("ec_bind") == "breakeven"
              and new_state.get("ec_stop") is not None
              and abs(stop_level - new_state["ec_stop"]) <= 1e-9):
            reason = "breakeven_stop"
        elif initial is None or stop_level > (initial + 1e-9):
            reason = "trailing_stop"
        else:
            reason = "stop"
        return {"exit": True, "reason": reason, "state": new_state}

    if target_level is not None and ltp >= target_level:
        return {"exit": True, "reason": "target", "state": new_state}

    return {"exit": False, "reason": None, "state": new_state}


# ---------------------------------------------------------------------------
# 3. Async lifecycle wrapper — mirrors LiveExitMonitor; squares via choke-point
# ---------------------------------------------------------------------------

class LiveSLMonitor:
    """Polls live premiums and software-squares positions on a stop/target hit.

    Owns NO broker order state and places NO resting SL.  On an exit trigger it
    calls the injected ``square_fn(position, reason)`` — the margin-safe square-off
    (auto_square.square_position bound with a client) — exactly once per position
    (the position is removed from the monitored set as soon as a square is
    initiated, so a position is never double-squared).

    Injected collaborators (all so the monitor is host-testable with no network):
      positions_factory:  () -> list[ {id, tsym, state, position} ]  (current set)
      ltp_lookup_factory:  () -> (tsym -> ltp|None)
      square_fn:           async (position, *, reason) -> dict
      remove_fn:           (id) -> None   (mark a position no longer monitored)
    """

    def __init__(
        self,
        *,
        positions_factory: Callable[[], List[Dict[str, Any]]],
        ltp_lookup_factory: Callable[[], Callable[[str], Optional[float]]],
        square_fn: Callable[..., Awaitable[Dict[str, Any]]],
        remove_fn: Callable[[str], None],
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._positions_factory = positions_factory
        self._ltp_lookup_factory = ltp_lookup_factory
        self._square_fn = square_fn
        self._remove_fn = remove_fn
        self._poll_seconds = float(poll_seconds)
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "checked": 0, "exits": 0, "last_run_at": None, "last_error": None,
        }

    def status(self) -> Dict[str, Any]:
        return dict(self._stats)

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One evaluation pass over all monitored positions. Never raises."""
        exits: List[Dict[str, Any]] = []
        try:
            positions = self._positions_factory() or []
            ltp_lookup = self._ltp_lookup_factory()
            for entry in positions:
                tsym = entry.get("tsym", "")
                state = entry.get("state") or {}
                try:
                    ltp = ltp_lookup(tsym)
                except Exception:
                    ltp = None
                verdict = evaluate_exit(state, ltp)
                entry["state"] = verdict["state"]  # persist updated trailing state
                self._stats["checked"] += 1
                if verdict["exit"]:
                    pid = entry.get("id", "")
                    # Remove from the monitored set BEFORE squaring so a slow
                    # square can never be issued twice for the same position.
                    try:
                        self._remove_fn(pid)
                    except Exception:
                        pass
                    result = await self._square_fn(
                        entry.get("position") or {}, reason=verdict["reason"]
                    )
                    self._stats["exits"] += 1
                    exits.append({"id": pid, "reason": verdict["reason"], "result": result})
            self._stats["cycles"] += 1
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
        except Exception as exc:  # never let a cycle kill the loop
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live SL monitor cycle failed: %s", exc)
        return exits

    async def _run(self) -> None:
        self._stats["running"] = True
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                if not _in_market_hours():
                    continue
                await self._cycle()
        except asyncio.CancelledError:
            raise
        finally:
            self._stats["running"] = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="live-sl-monitor")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
