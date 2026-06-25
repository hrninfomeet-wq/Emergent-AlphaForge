"""Software-monitored exits for LIVE positions — the margin-free SL/TP/trailing
that REPLACES the always-margin-rejected resting broker SL.

Why this exists (proven live 2026-06-24): a resting SELL stop-loss on a long
option needs full naked-short SPAN margin (~₹1.8L for a SENSEX lot) which an
option-buyer account does not have — so the broker SL backstop is REJECTED every
time and the position is left unprotected. The research-backed redesign (spec §5)
is: never rest a standing SL; watch the live premium in SOFTWARE and square
through the margin-safe cancel-all-then-close path when a stop/target/trailing
breaches. No margin is reserved; the protection always works.

Two pieces
----------
1. ``LiveMonitorRegistry`` — a process-singleton registry of positions to guard.
   Populated on arm (entry placed); each entry carries the per-position monitor
   ``state`` (from ``live_sl_monitor.build_monitor_state``) plus the broker tsym
   used to match the position book.

2. ``LivePositionGuard`` — an async lifecycle loop (mirrors ``LiveExitMonitor``:
   start/stop/status) that every ~1.5 s reads the BROKER position book (the
   broker's own ``lp`` mark + ``netqty`` — no Upstox-stream dependency), evaluates
   each guarded position's stop/target/trailing via the pure
   ``live_sl_monitor.evaluate_exit``, and on a breach squares the position via the
   injected margin-safe ``square_fn`` (``auto_square.square_position``).

Safety properties
-----------------
- A position is removed from the registry BEFORE the square is issued, so a slow
  square can never be issued twice for the same position.
- A position the broker reports FLAT (netqty == 0) is dropped (closed elsewhere).
- A stale / missing ``lp`` is treated as "no reading" by ``evaluate_exit`` → never
  a spurious square.
- The cycle NEVER raises out — a broker/feed error records and skips.
- The square is the SAME cancel-all-then-confirm-then-close path used everywhere
  (no naked-short margin trap).
- The 10-minute auto-square cap remains the ultimate backstop, independent of this.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.execution_policy import spot_mirror_exit_reason
from app.live.kill_switch import _parse_netqty
from app.live.live_sl_monitor import evaluate_exit
from app.live.overall_controls import build_overall_state, evaluate_overall

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)


def _in_market_hours(now_utc: Optional[datetime] = None) -> bool:
    ist = (now_utc or datetime.now(timezone.utc)) + _IST
    if ist.weekday() >= 5:
        return False
    return dtime(9, 15) <= ist.time() < dtime(15, 30)


def _finite_pos(x: Any) -> Optional[float]:
    """Return x as a finite positive float, else None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _finite_num(x: Any) -> Optional[float]:
    """Return x as a finite float (any sign — MTM can be negative), else None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _entry_ts_ms(value: Any) -> Optional[int]:
    """Entry timestamp as epoch-ms. Accepts an ISO-8601 string or an epoch number
    (s or ms — ms when ≥ 1e12). Returns None when unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        if not math.isfinite(v):
            return None
        return int(v) if v >= 1e12 else int(v * 1000)
    try:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. Registry — process-singleton set of positions to guard
# ---------------------------------------------------------------------------

class LiveMonitorRegistry:
    """In-memory registry of live positions under software guard. One instance
    per process (the router + the guard share it)."""

    def __init__(self) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        *,
        key: str,
        tsym: str,
        exch: str,
        qty: int,
        prd: str,
        entry_price: float,
        state: Dict[str, Any],
        spot_exit: Optional[Dict[str, Any]] = None,
        time_stop_minutes: Any = None,
        entry_ts: Any = None,
        source: Optional[str] = None,
        deployment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register (or replace) a position to guard. ``state`` is a monitor state
        from ``live_sl_monitor.build_monitor_state``. ``key`` is the entry
        norenordno (stable + unique per position).

        Full-parity guard extras (all mirror the paper-trade marker in
        ``paper_auto.mark_open_deployment_trades`` so live exits the same way the
        backtest/paper path does):
          spot_exit:          spot-mirror levels dict — ``{instrument_key,
                              direction, spot_target, spot_stop}`` (as produced by
                              ``paper_auto.compute_spot_exit_levels``). The guard
                              watches the UNDERLYING tick and squares the option at
                              its current premium when a spot level is hit. Stop-first
                              via the shared execution_policy. None ⇒ no spot mirror.
          time_stop_minutes:  minutes after ``entry_ts`` at which the position is
                              squared (the backtest's time exit). None ⇒ no time stop.
          entry_ts:           entry timestamp (ISO str or epoch-ms) — the time-stop
                              reference. Defaults to "now" at register time when a
                              time_stop_minutes is set but no entry_ts is given.
          source:             provenance tag ("manual" | a deployment source). The
                              15:00 IST EOD square spares ``source == 'manual'``
                              positions (a hand-placed trade is not auto-flattened).
          deployment_id:      parent deployment id (audit / scoping only).
        """
        item = {
            "id": str(key),
            "tsym": str(tsym),
            "exch": str(exch or "NFO"),
            "qty": int(qty),
            "prd": str(prd or "I"),
            "entry_price": float(entry_price),
            "state": state,
            "spot_exit": dict(spot_exit) if spot_exit else None,
            "time_stop_minutes": _finite_pos(time_stop_minutes),
            "entry_ts": entry_ts,
            "source": str(source) if source is not None else None,
            "deployment_id": str(deployment_id) if deployment_id is not None else None,
            # Async-fill bookkeeping: a just-armed position may not be in the
            # position book yet. seen_filled flips True once we observe netqty!=0;
            # misses counts consecutive not-yet-filled cycles so a never-filling
            # (rejected/canceled) entry is dropped after a grace window — but a
            # pending fill is NEVER dropped before it appears.
            "seen_filled": False,
            "misses": 0,
            # the dict handed to square_fn (the margin-safe square reads tsym/exch/
            # netqty/lp); netqty/lp are refreshed from the broker each cycle.
            "position": {
                "tsym": str(tsym),
                "exch": str(exch or "NFO"),
                "netqty": int(qty),
                "lp": None,
                "prd": str(prd or "I"),
            },
        }
        # A time-stop with no explicit entry_ts anchors to register time (≈ fill).
        if item["time_stop_minutes"] is not None and item["entry_ts"] is None:
            item["entry_ts"] = datetime.now(timezone.utc).isoformat()
        self._items[str(key)] = item
        return item

    def snapshot(self) -> List[Dict[str, Any]]:
        """Live entry dicts (NOT copies) so the guard's state updates persist."""
        return list(self._items.values())

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._items.get(str(key))

    def remove(self, key: str) -> None:
        self._items.pop(str(key), None)

    def clear(self) -> None:
        self._items.clear()

    def keys(self) -> List[str]:
        return list(self._items.keys())

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# 2. Async guard loop
# ---------------------------------------------------------------------------

class LivePositionGuard:
    """Polls the broker position book and software-squares a guarded position on
    a stop/target/trailing breach. Mirrors LiveExitMonitor's lifecycle.

    Injected collaborators (so the guard is host-testable with no network):
      registry:        a LiveMonitorRegistry.
      client_factory:  async () -> broker client | None  (None ⇒ skip the cycle).
      square_fn:       async (client, position, *, reason) -> dict
                       (auto_square.square_position bound with uid/actid/band).
      poll_seconds:    cycle period.
    """

    def __init__(
        self,
        *,
        registry: LiveMonitorRegistry,
        client_factory: Callable[[], Awaitable[Any]],
        square_fn: Callable[..., Awaitable[Dict[str, Any]]],
        poll_seconds: float = POLL_SECONDS,
        max_pending_misses: int = 40,
        overall_provider: Optional[Callable[[], Awaitable[Optional[Dict[str, Any]]]]] = None,
        spot_tick_fn: Optional[Callable[[str], Optional[float]]] = None,
        eod_square_ist: dtime = dtime(15, 0),
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._registry = registry
        self._client_factory = client_factory
        self._square_fn = square_fn
        self._poll_seconds = float(poll_seconds)
        # Full-parity exits (mirror paper_auto.mark_open_deployment_trades):
        #   spot_tick_fn:    (instrument_key) -> latest UNDERLYING spot price | None
        #                    (None ⇒ no spot-mirror evaluation; a None reading for a
        #                    given key = stale/absent → never a spurious square).
        #   eod_square_ist:  IST time-of-day at/after which non-manual positions are
        #                    force-squared (the 15:00 IST backstop).
        #   now_fn:          injectable clock (UTC) for the time-stop + EOD checks.
        self._spot_tick_fn = spot_tick_fn
        self._eod_square_ist = eod_square_ist
        self._now_fn = now_fn
        # Optional basket-level overall controls: async () -> config dict | None.
        # The guard evaluates the AGGREGATE basket MTM each cycle and squares ALL
        # guarded positions on an overall SL / target / trailing breach. The
        # overall_state (monotonic trailing floor) persists across cycles and is
        # reset whenever the guarded set empties.
        self._overall_provider = overall_provider
        self._overall_state: Optional[Dict[str, Any]] = None
        # consecutive not-yet-filled cycles before a never-filling entry is dropped
        # (40 × ~1.5s ≈ 60s — well past a marketable fill, short enough to clean up
        # a rejected/canceled entry).
        self._max_pending_misses = int(max_pending_misses)
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "guarded": 0, "exits": 0, "last_run_at": None, "last_error": None,
        }

    def status(self) -> Dict[str, Any]:
        st = dict(self._stats)
        st["guarded"] = len(self._registry)
        return st

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One guard pass over all registered positions. NEVER raises."""
        exits: List[Dict[str, Any]] = []
        try:
            if len(self._registry) == 0:
                self._stats["cycles"] += 1
                self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
                return exits

            client = await self._client_factory()
            if client is None:
                self._stats["last_error"] = "no client"
                return exits

            now = self._now_fn()
            book = await client.position_book()
            by_tsym: Dict[str, Dict[str, Any]] = {}
            for p in (book or []):
                by_tsym[str(p.get("tsym", ""))] = p

            for entry in self._registry.snapshot():
                pos = by_tsym.get(entry["tsym"])
                netqty = _parse_netqty(pos.get("netqty")) if pos else None

                # Not (yet) a live position in the book — flat / absent / unparseable.
                if pos is None or netqty is None or netqty == 0:
                    if entry.get("seen_filled"):
                        # It filled earlier and is now flat → closed elsewhere; drop.
                        self._registry.remove(entry["id"])
                    else:
                        # Still pending its fill — KEEP, but bound a never-filling
                        # (rejected/canceled) entry by a grace window.
                        entry["misses"] = int(entry.get("misses", 0)) + 1
                        if entry["misses"] >= self._max_pending_misses:
                            self._registry.remove(entry["id"])
                    continue

                # Live, filled position.
                entry["seen_filled"] = True
                entry["misses"] = 0
                lp = _finite_pos(pos.get("lp"))
                # Refresh the square dict from the broker truth.
                entry["position"].update({
                    "netqty": netqty,
                    "lp": lp,
                    "exch": pos.get("exch", entry["exch"]),
                })

                # 1. Premium stop / target / trailing (the existing software SL).
                verdict = evaluate_exit(entry["state"], lp)
                entry["state"] = verdict["state"]
                if verdict["exit"]:
                    await self._square_entry(client, entry, verdict["reason"], exits)
                    continue

                # 2. Spot-mirror exit (the backtest's spot_exit mode, live): square
                #    the option at its current premium when the UNDERLYING tick hits
                #    the strategy's spot level. Direction-aware, stop-first — the same
                #    execution_policy the backtest + paper marker use. A None spot
                #    reading (no spot_tick_fn, unknown key, or stale tick) = no square.
                reason = self._spot_mirror_reason(entry)
                if reason is not None:
                    await self._square_entry(client, entry, reason, exits)
                    continue

                # 3. Tick-level time-stop (the backtest's time exit): square when the
                #    strategy's time_stop_minutes has elapsed since entry_ts.
                if self._time_stop_elapsed(entry, now):
                    await self._square_entry(client, entry, "time_stop", exits)
                    continue

            # ── EOD square: flatten non-manual positions at/after 15:00 IST ──
            await self._evaluate_eod_square(client, by_tsym, now, exits)

            # ── Basket-level overall controls (overall SL / target / trailing) ──
            await self._evaluate_overall_basket(client, by_tsym, exits)

            self._stats["cycles"] += 1
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live position guard cycle failed: %s", exc)
        return exits

    async def _square_entry(
        self, client: Any, entry: Dict[str, Any], reason: str, exits: List[Dict[str, Any]]
    ) -> None:
        """Remove the entry from the registry BEFORE issuing the square so a slow
        square is never re-issued for the same position, then square via the
        injected margin-safe square_fn. NEVER raises (a failed square records and
        is reported, but does not kill the cycle)."""
        self._registry.remove(entry["id"])
        try:
            result = await self._square_fn(
                client, entry["position"], reason=f"software_{reason}"
            )
        except Exception as exc:  # square must never kill the loop
            log.exception("guard square failed for %s: %s", entry["tsym"], exc)
            result = {"squared": False, "error": str(exc)[:200]}
        self._stats["exits"] += 1
        exits.append({"id": entry["id"], "tsym": entry["tsym"],
                      "reason": reason, "result": result})

    def _spot_mirror_reason(self, entry: Dict[str, Any]) -> Optional[str]:
        """Has the UNDERLYING tick hit a spot-mirror level for this position?
        Returns the exit reason (``spot_stop_hit`` / ``spot_target_hit``) or None.

        A missing spot_exit config, no spot_tick_fn, an unknown spot key, or a
        stale/absent spot reading (spot_tick_fn → None / non-finite / ≤0) all yield
        None → never a spurious square (mirrors the marker's freshness guard)."""
        spot_exit = entry.get("spot_exit")
        if not spot_exit or self._spot_tick_fn is None:
            return None
        key = str(spot_exit.get("instrument_key") or "")
        if not key:
            return None
        try:
            raw = self._spot_tick_fn(key)
        except Exception:
            return None
        spot_price = _finite_pos(raw)
        if spot_price is None:
            return None
        return spot_mirror_exit_reason(
            str(spot_exit.get("direction") or ""),
            spot_price,
            spot_target=spot_exit.get("spot_target"),
            spot_stop=spot_exit.get("spot_stop"),
        )

    def _time_stop_elapsed(self, entry: Dict[str, Any], now: datetime) -> bool:
        """True when the position's time_stop_minutes has elapsed since entry_ts."""
        tsm = entry.get("time_stop_minutes")
        if tsm is None:
            return False
        entry_ms = _entry_ts_ms(entry.get("entry_ts"))
        if entry_ms is None:
            return False
        now_ms = int(now.timestamp() * 1000)
        return (now_ms - entry_ms) / 60000.0 >= float(tsm)

    async def _evaluate_eod_square(
        self, client: Any, by_tsym: Dict[str, Dict[str, Any]], now: datetime,
        exits: List[Dict[str, Any]],
    ) -> None:
        """At/after the EOD time-of-day in IST, force-square every remaining
        non-manual guarded position (the 15:00 IST backstop). ``source ==
        'manual'`` positions are spared — a hand-placed trade is never
        auto-flattened. Only positions the broker reports LIVE (netqty != 0) are
        squared; a pending/flat entry is left to the normal fill/flat handling."""
        ist = now + _IST
        if ist.time() < self._eod_square_ist:
            return
        for entry in self._registry.snapshot():
            if str(entry.get("source") or "") == "manual":
                continue
            pos = by_tsym.get(entry["tsym"])
            netqty = _parse_netqty(pos.get("netqty")) if pos else None
            if netqty is None or netqty == 0:
                continue  # not a live position → nothing to flatten
            await self._square_entry(client, entry, "eod_square", exits)

    async def _evaluate_overall_basket(
        self, client: Any, by_tsym: Dict[str, Dict[str, Any]], exits: List[Dict[str, Any]]
    ) -> None:
        """Evaluate the AGGREGATE basket MTM against the overall controls and, on
        an overall SL / target / trailing breach, square ALL remaining guarded
        positions (via the same dry-run-gated margin-safe square_fn). The trailing
        floor (self._overall_state) persists across cycles; it resets when the
        guarded set empties. A config with nothing enabled is a no-op."""
        remaining = self._registry.snapshot()
        if not remaining or self._overall_provider is None:
            self._overall_state = None  # no basket → reset trailing
            return

        basket_mtm = 0.0
        basket_premium = 0.0
        have_mtm = False
        for entry in remaining:
            pos = by_tsym.get(entry["tsym"])
            if pos is None:
                continue
            u = _finite_num(pos.get("urmtom"))
            if u is not None:
                basket_mtm += u
                have_mtm = True
            try:
                basket_premium += float(entry["entry_price"]) * int(entry["qty"])
            except (TypeError, ValueError):
                pass
        if not have_mtm:
            return

        # (Re)build the overall state for a fresh basket. Nothing enabled → ValueError → skip.
        if self._overall_state is None:
            try:
                cfg = await self._overall_provider()
            except Exception:
                cfg = None
            if not cfg:
                return
            try:
                self._overall_state = build_overall_state(cfg, basket_premium)
            except ValueError:
                self._overall_state = None
                return

        ov = evaluate_overall(self._overall_state, basket_mtm)
        self._overall_state = ov["state"]
        if not ov["exit"]:
            return

        # Overall breach → square EVERY remaining guarded position.
        for entry in self._registry.snapshot():
            self._registry.remove(entry["id"])
            try:
                result = await self._square_fn(
                    client, entry["position"], reason=f"software_{ov['reason']}"
                )
            except Exception as exc:
                log.exception("guard overall-square failed for %s: %s", entry["tsym"], exc)
                result = {"squared": False, "error": str(exc)[:200]}
            self._stats["exits"] += 1
            exits.append({"id": entry["id"], "tsym": entry["tsym"],
                          "reason": ov["reason"], "result": result})
        self._overall_state = None  # basket squared → reset

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
        self._task = asyncio.create_task(self._run(), name="live-position-guard")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


# ---------------------------------------------------------------------------
# Process-singleton registry — SHARED between the router (which registers a
# position on arm) and the guard loop (which reads + squares).
# ---------------------------------------------------------------------------

_REGISTRY_SINGLETON: Optional[LiveMonitorRegistry] = None


def get_registry() -> LiveMonitorRegistry:
    """Return the process-wide LiveMonitorRegistry (lazily constructed)."""
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = LiveMonitorRegistry()
    return _REGISTRY_SINGLETON
