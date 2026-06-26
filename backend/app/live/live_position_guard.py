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
from app.live.live_sl_monitor import build_monitor_state, evaluate_exit
from app.live.overall_controls import build_overall_state, evaluate_overall

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)
# A spot tick older than this is treated as absent — the guard never squares on
# a minutes-old underlying price (mirrors paper_auto.MARK_TICK_MAX_AGE_SECONDS).
MARK_TICK_MAX_AGE_SECONDS = 120


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


def _to_utc_dt(x: Any) -> Optional[datetime]:
    """Coerce an entry timestamp to a tz-aware UTC datetime, else None.

    Accepts a datetime (naive → assumed UTC) or an ISO-8601 string. Anything
    unparseable returns None so a missing/garbage entry_ts simply disables the
    time-stop rather than raising."""
    if isinstance(x, datetime):
        dt = x
    elif isinstance(x, str) and x:
        try:
            dt = datetime.fromisoformat(x)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
        source: str = "manual",
        deployment_id: Optional[str] = None,
        oco_al_id: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register (or replace) a position to guard. ``state`` is a monitor state
        from ``live_sl_monitor.build_monitor_state``. ``key`` is the entry
        norenordno (stable + unique per position).

        Deployed positions get FULL exit parity with the paper/backtest path via
        the optional keyword-only fields below; the manual single-shot caller
        (``live_broker._make_arm``) omits them entirely, so its items keep
        ``source="manual"``, ``spot_exit=None`` etc. — manual behavior unchanged.

        ``spot_exit``: ``{"direction","instrument_key","spot_target","spot_stop"}``
                       — the live equivalent of the backtest's ``spot_exit`` mode
                       (close the option when the UNDERLYING hits a level).
        ``time_stop_minutes``: close after this many minutes from ``entry_ts``.
        ``entry_ts``: ISO-8601 / datetime entry timestamp (basis for the time-stop).
        ``source``: ``"manual"`` (LIVE_TEST single-shot, keeps its own 10-min cap +
                    EOD-exempt) or e.g. ``"auto_live"`` (deployed → EOD-squared).
        ``deployment_id``: the owning deployment (audit; deployed positions only).
        """
        item = {
            "id": str(key),
            "tsym": str(tsym),
            "exch": str(exch or "NFO"),
            "qty": int(qty),
            "prd": str(prd or "I"),
            "entry_price": float(entry_price),
            "state": state,
            "spot_exit": spot_exit,
            "time_stop_minutes": time_stop_minutes,
            "entry_ts": entry_ts,
            "source": str(source or "manual"),
            "deployment_id": deployment_id,
            "oco_al_id": oco_al_id,
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
                "token": token,
            },
        }
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
        spot_tick_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        eod_square_ist: dtime = dtime(15, 0),
        now_fn: Optional[Callable[[], datetime]] = None,
        on_close: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        self._registry = registry
        self._client_factory = client_factory
        self._square_fn = square_fn
        # Optional close-loop hook fired after EVERY guard square (the single
        # _square_and_record choke-point). Signature:
        #   async on_close(entry, exit_price, exit_reason, result) -> None
        # The production impl (runtime._live_guard_on_close) journals realized P&L
        # to live_trades, but ONLY for a real fill — it inspects `result` and
        # no-ops on a dry-run/failed square. Default None ⇒ no-op (host tests stay
        # db-free). A close-loop failure NEVER kills the guard cycle.
        self._on_close = on_close
        self._poll_seconds = float(poll_seconds)
        # Spot-mirror source: () -> live tick map {instrument_key: {"last_price",
        # "ts"/"received_ts"}}. None ⇒ spot-mirror is skipped entirely (the manual
        # path never sets a spot_exit, so this is a no-op for it regardless).
        self._spot_tick_fn = spot_tick_fn
        # 15:00 IST EOD square cutoff for DEPLOYED (source != "manual") positions.
        self._eod_square_ist = eod_square_ist
        # Injectable clock (time-stop elapsed + EOD + market hours where the cycle
        # needs "now"). Default → wall-clock UTC.
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
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

            book = await client.position_book()
            by_tsym: Dict[str, Dict[str, Any]] = {}
            for p in (book or []):
                by_tsym[str(p.get("tsym", ""))] = p

            now = self._now_fn()
            # Read the live spot tick map ONCE per cycle (None when no source is
            # wired ⇒ spot-mirror is skipped). A factory error is non-fatal — the
            # whole cycle is already wrapped, but degrade to "no spot data" so the
            # premium/basket paths still run.
            spot_map: Dict[str, Any] = {}
            if self._spot_tick_fn is not None:
                try:
                    spot_map = self._spot_tick_fn() or {}
                except Exception:
                    spot_map = {}

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
                    # Capture the broker book row's contract token (best-effort;
                    # None if the row has no token). The depth-aware square refreshes
                    # its exit ref price from a fresh GetQuotes when a token is set.
                    "token": pos.get("token"),
                })

                verdict = evaluate_exit(entry["state"], lp)
                entry["state"] = verdict["state"]
                if verdict["exit"]:
                    await self._square_and_record(
                        client, entry, f"software_{verdict['reason']}",
                        verdict["reason"], exits)
                    continue

                # ── Deployed-position exit parity (paper/backtest mirror) ──
                # Only entries STILL OPEN after the premium evaluate_exit, and only
                # when the relevant fields are set. Spot-mirror first (it can fire
                # on the underlying even when the premium leg is quiet), then the
                # time-stop. Both use the same remove-before-square ordering.
                if await self._evaluate_spot_mirror(client, entry, spot_map, now, exits):
                    continue
                await self._evaluate_time_stop(client, entry, now, exits)

            # ── Basket-level overall controls (overall SL / target / trailing) ──
            await self._evaluate_overall_basket(client, by_tsym, exits)

            # ── 15:00 IST EOD square (deployed positions only) ──
            await self._evaluate_eod_square(client, now, exits)

            self._stats["cycles"] += 1
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live position guard cycle failed: %s", exc)
        return exits

    async def _square_and_record(
        self, client: Any, entry: Dict[str, Any], square_reason: str,
        exit_reason: str, exits: List[Dict[str, Any]],
    ) -> None:
        """Remove-BEFORE-square (so a slow square is never re-issued), square via
        the injected margin-safe square_fn, record the exit. NEVER raises."""
        self._registry.remove(entry["id"])
        try:
            result = await self._square_fn(
                client, entry["position"], reason=square_reason)
        except Exception as exc:  # square must never kill the loop
            log.exception("guard square failed for %s: %s", entry["tsym"], exc)
            result = {"squared": False, "error": str(exc)[:200]}
        # Cancel the resting OCO ONLY AFTER a CONFIRMED REAL square fill (never
        # before — cancel_oco cannot stop an already-triggered OCO; and never on
        # a dry-run/failed square — stripping the broker net without squaring
        # would leave the position fully UNPROTECTED). Gate on all of:
        #   oco_al_id present AND client.cancel_oco exists AND squared AND not dry_run.
        if (
            entry.get("oco_al_id")
            and hasattr(client, "cancel_oco")
            and result.get("squared")
            and not result.get("dry_run")
        ):
            try:
                await client.cancel_oco(entry["oco_al_id"])
            except Exception as exc:  # a cancel failure logs, never breaks the cycle
                log.exception(
                    "guard cancel_oco failed for %s (al_id=%s): %s",
                    entry["tsym"], entry.get("oco_al_id"), exc)
        self._stats["exits"] += 1
        exits.append({"id": entry["id"], "tsym": entry["tsym"],
                      "reason": exit_reason, "result": result})
        # Close-loop: journal realized P&L back to live_trades (real fills only;
        # the production hook no-ops on a dry-run/failed square). exit_price is the
        # broker last-price refreshed onto the entry this cycle (an exit MARK, not
        # a confirmed fill). NEVER let a close-loop failure kill the guard.
        if self._on_close is not None:
            try:
                await self._on_close(
                    entry, entry["position"].get("lp"), exit_reason, result)
            except Exception as exc:
                log.exception("guard on_close failed for %s: %s", entry["tsym"], exc)

    def _fresh_spot_price(
        self, spot_map: Dict[str, Any], instrument_key: str, now: datetime
    ) -> Optional[float]:
        """Latest spot price for the underlying key, but ONLY when FRESH — a tick
        older than MARK_TICK_MAX_AGE_SECONDS (vs ``now``) is treated as absent so
        the guard never squares on a minutes-old underlying (mirrors paper's
        staleness bound). Ticks with no timestamp are treated as current. A
        zero/non-positive/garbage price ⇒ None (held)."""
        if not instrument_key:
            return None
        tick = spot_map.get(instrument_key)
        if not tick or tick.get("last_price") in (None, ""):
            return None
        try:
            price = float(tick["last_price"])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(price) or price <= 0:
            return None
        age_ref = tick.get("received_ts") or tick.get("ts")
        if age_ref is not None:
            try:
                now_ms = int(now.timestamp() * 1000)
                if now_ms - int(age_ref) > MARK_TICK_MAX_AGE_SECONDS * 1000:
                    return None
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        return price

    async def _evaluate_spot_mirror(
        self, client: Any, entry: Dict[str, Any], spot_map: Dict[str, Any],
        now: datetime, exits: List[Dict[str, Any]],
    ) -> bool:
        """Spot-mirror exit (the backtest's spot_exit mode, live): close the option
        when the UNDERLYING hits the strategy's direction-aware level. Stop-first
        (delegates to execution_policy.spot_mirror_exit_reason). Returns True iff
        the position was squared (so the caller skips the time-stop)."""
        spot_exit = entry.get("spot_exit")
        if not spot_exit or self._spot_tick_fn is None:
            return False
        instrument_key = str(spot_exit.get("instrument_key") or "")
        spot_price = self._fresh_spot_price(spot_map, instrument_key, now)
        if spot_price is None:
            return False  # stale / zero / absent ⇒ no spot-mirror square (held)
        reason = spot_mirror_exit_reason(
            str(spot_exit.get("direction") or ""),
            spot_price,
            spot_target=spot_exit.get("spot_target"),
            spot_stop=spot_exit.get("spot_stop"),
        )
        if not reason:
            return False
        await self._square_and_record(
            client, entry, f"software_{reason}", reason, exits)
        return True

    async def _evaluate_time_stop(
        self, client: Any, entry: Dict[str, Any], now: datetime,
        exits: List[Dict[str, Any]],
    ) -> bool:
        """Time-stop exit (parity with the backtest's time exit): close when the
        strategy's time_stop_minutes has elapsed since entry_ts. Returns True iff
        squared."""
        tsm = entry.get("time_stop_minutes")
        entry_ts = _to_utc_dt(entry.get("entry_ts"))
        if not tsm or entry_ts is None:
            return False
        try:
            minutes = float(tsm)
        except (TypeError, ValueError):
            return False
        if minutes <= 0:
            return False
        elapsed_min = (now - entry_ts).total_seconds() / 60.0
        if elapsed_min < minutes:
            return False
        await self._square_and_record(
            client, entry, "software_time_stop", "time_stop", exits)
        return True

    async def _evaluate_eod_square(
        self, client: Any, now: datetime, exits: List[Dict[str, Any]]
    ) -> None:
        """15:00 IST EOD square for DEPLOYED positions only (source != "manual").

        Manual LIVE_TEST positions keep their own 10-min single-shot timer and are
        NEVER touched by EOD. Remove-before-square, reason="eod_square"; the
        actual transmit is gated by the injected square_fn (LIVE_GUARD_ARMED)."""
        if len(self._registry) == 0:
            return
        ist_now = (now.astimezone(timezone.utc) + _IST).time()
        if ist_now < self._eod_square_ist:
            return
        for entry in self._registry.snapshot():
            if str(entry.get("source") or "manual") == "manual":
                continue  # manual positions are EOD-exempt
            await self._square_and_record(
                client, entry, "eod_square", "eod_square", exits)

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

        # Overall breach → square EVERY remaining guarded position through the
        # single _square_and_record choke-point (remove-before-square + exit
        # record + close-loop on_close — identical to the per-position exits).
        for entry in self._registry.snapshot():
            await self._square_and_record(
                client, entry, f"software_{ov['reason']}", ov["reason"], exits)
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

    async def rehydrate_from_broker(self, *, default_stop_pct: float = 50.0) -> int:
        """Re-attach the software guard to open broker positions after a restart.

        The registry is an in-memory singleton (EMPTY on boot), so any position
        opened before a backend/PC restart would be left UNWATCHED — no software
        stop/target/EOD square — while the UI still believed it was guarded. This
        reads the broker position book and re-registers every open (netqty != 0)
        position the registry isn't already tracking, with a DEEP-DEFAULT premium
        catastrophe stop (the original per-position levels are lost on restart) and
        ``source="rehydrated"`` so the UI can flag "levels reset to default". A fresh
        arm that already registered a tsym is never clobbered.

        Best-effort: returns the count rehydrated; never raises out (a broker/feed
        error logs and returns 0). Call ONCE at startup, around guard start.
        """
        try:
            client = await self._client_factory()
            if client is None:
                return 0
            book = await client.position_book()
        except Exception as exc:
            log.warning("guard rehydrate: could not read broker position book: %s", exc)
            return 0
        # Already-watched set keyed by TSYM (registry entries are keyed by
        # norenordno, but the guard matches positions to entries by tsym — so a
        # fresh arm for this tsym must NOT be double-watched/clobbered).
        watched_tsyms = {str(e.get("tsym") or "") for e in self._registry.snapshot()}
        rehydrated = 0
        for pos in (book or []):
            try:
                netqty = _parse_netqty(pos.get("netqty"))
                if netqty is None or netqty == 0:
                    continue  # flat — nothing to guard
                tsym = str(pos.get("tsym", ""))
                if not tsym or tsym in watched_tsyms:
                    continue  # already watched (e.g. a fresh arm) — don't clobber
                # Entry mark: prefer the live lp, else the net/buy average price.
                entry = (_finite_pos(pos.get("lp")) or _finite_pos(pos.get("netavgprc"))
                         or _finite_pos(pos.get("daybuyavgprc")))
                if entry is None:
                    continue
                state = build_monitor_state(float(entry), stop_pct=default_stop_pct)
                self._registry.register(
                    key=tsym, tsym=tsym, exch=str(pos.get("exch", "NFO")),
                    qty=abs(int(netqty)), prd=str(pos.get("prd", "I")),
                    entry_price=float(entry), state=state, source="rehydrated",
                )
                watched_tsyms.add(tsym)  # guard against duplicate tsyms in the book
                rehydrated += 1
                log.warning(
                    "guard rehydrate: re-attached %s (netqty=%s) at default %.0f%% stop "
                    "— original levels lost on restart", tsym, netqty, default_stop_pct,
                )
            except Exception as exc:
                log.warning("guard rehydrate: register failed for %s: %s", pos.get("tsym"), exc)
        return rehydrated

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
