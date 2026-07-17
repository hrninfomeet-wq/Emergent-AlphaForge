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
- A guard square is PLACE-AND-TRACK, not place-and-forget: a place-acceptance is
  not a fill. On a breach the entry is MARKED ``squaring`` (and KEPT registered,
  OCO resting); the flag stops it being re-issued. Only when the broker book
  confirms the position FLAT (netqty→0) does ``_finalize_flat`` cancel the OCO,
  journal the close, and drop the entry — so a resting-unfilled exit never leaves
  the position open-and-unprotected yet reported closed.
- A stale / missing ``lp`` is treated as "no reading" by ``evaluate_exit`` → never
  a spurious square.
- The cycle NEVER raises out — a broker/feed error records and skips.
- The square is the SAME cancel-all-then-confirm-then-close path used everywhere
  (no naked-short margin trap).
- The 15:00 IST EOD square is the ultimate "never left open" backstop for every
  guarded position (manual + deployed); deployed positions additionally carry a
  resting broker OCO for the PC-down case. (The old 10-minute manual auto-square
  timer was removed — see docs/superpowers/specs/2026-07-09-remove-manual-livetest
  -10min-timer-design.md.)
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.execution_policy import spot_mirror_exit_reason
from app.live.broker_protocol import BrokerReadError, TOKEN_EXPIRED_HINT
from app.live.kill_switch import _parse_netqty
from app.live.live_sl_monitor import build_monitor_state, evaluate_exit
from app.live.overall_controls import build_overall_state, evaluate_overall

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)
# A non-confirming square whose reason is one of these placed NOTHING at the
# broker, so it must not burn the square-retry exhaustion budget (whose whole
# rationale is bounding real order-API hammering):
#   exit_in_flight_elsewhere — benign contention: another exit path holds the
#       per-tsym exit claim (see exit_claims); resolves when it finishes.
#   unpriced — no usable mark to price the exit; transient market-data gap.
#   cancel_unconfirmed — the pre-place cancel/confirm could not verify the scrip
#       is clear (unreadable order book / surviving working order); placing would
#       risk a naked short, so nothing was placed. ~40s of an order-book blip
#       must not permanently stop the guard (it previously could).
# All three retry next cycle and surface via last_error; only genuine place
# REJECTS/raises count toward the budget.
_SOFT_RETRY_REASONS = frozenset(
    {"exit_in_flight_elsewhere", "unpriced", "cancel_unconfirmed"})
# A spot tick older than this is treated as absent — the guard never squares on
# a minutes-old underlying price (mirrors paper_auto.MARK_TICK_MAX_AGE_SECONDS).
MARK_TICK_MAX_AGE_SECONDS = 120

# ── Layer 2 widening re-price defaults ──
# Widening marketable band for escalating a resting-unfilled guard exit. band[0]
# is the FIRST square (Layer 1, byte-identical); band[1:] are the escalations.
# Mirrors kill_switch.FLATTEN_BAND_SCHEDULE.
REPRICE_BAND_SCHEDULE = (1.0, 2.0, 4.0)
# Minimum seconds between a square and its re-price (≈ 2-3 poll cycles → the
# marketable LMT gets a real fill chance before we cancel+re-place).
REPRICE_INTERVAL_SECONDS = 4.0
# Max re-prices ISSUED per cycle across ALL guarded positions — bounds a
# synchronized N-leg burst inside Noren's order rate limit (10/s, 40/min); the
# per-position interval alone does not de-synchronize a basket.
REPRICE_MAX_PER_CYCLE = 2
# Fixed epoch for sorting entries with no square_last_ts yet (oldest-first).
_EPOCH0 = datetime(1970, 1, 1, tzinfo=timezone.utc)


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
        square_at_ist: Optional[str] = None,
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
        ``source``: ``"manual"`` (LIVE_TEST single-shot) or e.g. ``"auto_live"``
                    (deployed). BOTH are EOD-squared at 15:00 IST — the manual
                    10-min auto-square timer was removed, so EOD is the manual
                    position's "never left open" backstop too.
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
            # Phase 5B B5: optional per-entry hard square time (IST HH:MM),
            # normalized + clamped at registration below. The 15:00 EOD square
            # remains the universal backstop and always wins if earlier.
            "square_at_ist": None,
            # Async-fill bookkeeping: a just-armed position may not be in the
            # position book yet. seen_filled flips True once we observe netqty!=0;
            # misses counts consecutive not-yet-filled cycles so a never-filling
            # (rejected/canceled) entry is dropped after a grace window — but a
            # pending fill is NEVER dropped before it appears.
            "seen_filled": False,
            "misses": 0,
            # Consecutive authenticated flat reads for a seen-filled entry — the
            # finalize gate (see LivePositionGuard._cycle / flat_confirm_reads).
            "flat_reads": 0,
            # Confirm-flat bookkeeping (Layer 1): once a guard square is placed and
            # accepted, `squaring` flips True and the entry is KEPT (the OCO stays
            # resting) until the broker book confirms netqty→0 — only then is the OCO
            # cancelled, the close journaled (with `square_reason`), and the entry
            # dropped. A place-accept is NOT a fill, so nothing irreversible fires on it.
            "squaring": False,
            "square_reason": None,
            # Layer 2 widening re-price state (only meaningful while `squaring`):
            #   square_band_idx  — index into the band schedule for the NEXT re-price
            #                      (0 = first square used band[0]; 1+ = escalations).
            #   square_last_ts   — when the last square/re-price was ISSUED (interval gate).
            #   square_ordno     — broker id of the currently-resting guard exit (so a
            #                      re-price can cancel exactly IT, and _finalize_flat can
            #                      cancel it on confirmed-flat → no orphaned naked short).
            #   square_qty       — the qty the resting exit was placed for (over-sell math).
            #   reprice_exhausted/reprice_stopped — terminal signals (schedule spent /
            #                      hard reject) so a stuck exit is surfaced, never silent.
            "square_band_idx": 0,
            "square_last_ts": None,
            "square_ordno": None,
            "square_qty": 0,
            "reprice_exhausted": False,
            "reprice_stopped": False,
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
        # Phase 5B B5: normalize-or-drop the per-entry square time. normalize
        # (review C1: raw HH:MM compares are fail-open for unpadded input),
        # then clamp STRICTLY BEFORE the registry's EOD square — the EOD
        # backstop always wins; a later/equal value is dropped with a log
        # (the deploy-side advisory covers user-facing honesty). Invalid
        # values are dropped too (an exit TIME must never break registration
        # of the stop monitor itself).
        if square_at_ist is not None:
            try:
                from app.premium_momentum import normalize_hhmm
                _sq = normalize_hhmm(square_at_ist)
                # The registry has no handle on the guard's configured EOD
                # (that lives on LivePositionGuard); clamp against the shared
                # 15:00 default. Runtime-safe either way: _evaluate_eod_square
                # always squares EVERYTHING once the guard's actual EOD is
                # reached, so a per-entry time can never OUTLIVE a
                # differently-configured EOD — it could only fire earlier,
                # which is the conservative direction.
                _eod = "15:00"
                if _sq is not None and _sq < _eod:
                    item["square_at_ist"] = _sq
                else:
                    log.warning("register %s: square_at_ist %r >= EOD %s — dropped (EOD backstop wins)",
                                key, square_at_ist, _eod)
            except ValueError:
                log.warning("register %s: invalid square_at_ist %r — dropped", key, square_at_ist)
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
        flat_confirm_reads: int = 2,
        max_square_retries: int = 25,
        overall_provider: Optional[Callable[[], Awaitable[Optional[Dict[str, Any]]]]] = None,
        spot_tick_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        eod_square_ist: dtime = dtime(15, 0),
        now_fn: Optional[Callable[[], datetime]] = None,
        on_close: Optional[Callable[..., Awaitable[None]]] = None,
        reprice_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        reprice_band_schedule: tuple = REPRICE_BAND_SCHEDULE,
        reprice_interval_seconds: float = REPRICE_INTERVAL_SECONDS,
        reprice_max_per_cycle: int = REPRICE_MAX_PER_CYCLE,
    ) -> None:
        self._registry = registry
        self._client_factory = client_factory
        self._square_fn = square_fn
        # Layer 2 over-sell-safe widening re-price of a resting-unfilled guard exit.
        # reprice_fn(client, position, *, band_pct, prev_ordno, prev_qty, reason) is a
        # DISTINCT executor from square_fn (NOT square_position — that sizes off a
        # stale netqty and can't reliably cancel the prior exit → over-sell). It
        # cancels the tracked prev order, re-reads its fillshares, and places ONLY the
        # confirmed-remaining qty at a bid-anchored, circuit-clamped price. Default
        # None ⇒ no re-pricing (Layer-1 behavior: the first exit rests until flat —
        # host tests that don't exercise re-price pass None).
        self._reprice_fn = reprice_fn
        self._reprice_band_schedule = tuple(reprice_band_schedule)
        self._reprice_interval_seconds = float(reprice_interval_seconds)
        self._reprice_max_per_cycle = int(reprice_max_per_cycle)
        # Optional close-loop hook fired from ``_finalize_flat`` when the broker
        # confirms a guard-squared position FLAT (NOT on place-acceptance). Signature:
        #   async on_close(entry, exit_price, exit_reason, result) -> None
        # It is called with a synthesized confirmed-flat result
        # ({"squared": True, "via": "confirmed_flat"}) only when a guard square was
        # pending (`squaring`). The production impl (runtime._live_guard_on_close)
        # journals realized P&L to live_trades; should_journal_close still gates out
        # manual/dry-run. Default None ⇒ no-op (host tests stay db-free). A close-loop
        # failure NEVER kills the guard cycle.
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
        # A seen-filled position that reads flat is FINALIZED (OCO cancelled,
        # close journaled, entry dropped) ONLY after this many CONSECUTIVE
        # AUTHENTICATED flat reads — a single flat read (broker book eventual-
        # consistency, a transient blip) must not un-watch a live position. A
        # BrokerReadError read is NOT a flat read and never advances this counter
        # (the cycle skips instead); an UNKNOWN/empty book neither advances nor
        # resets it; a live read resets it.
        self._flat_confirm_reads = max(1, int(flat_confirm_reads))
        # A guard square that does NOT confirm and is NOT a dry-run or benign
        # contention (broker reject / raise) is retried next cycle, bounded by this
        # many attempts. On exhaustion the guard ESCALATES (escalations stat +
        # operator error log) and STOPS re-issuing (square_stopped) — the entry
        # STAYS registered, so the broker OCO remains the backstop and the
        # confirmed-flat finalize still cleans up (audit L20, adapted to Layer 1).
        # 25 × ~1.5s ≈ 40s of retries before escalation.
        self._max_square_retries = max(1, int(max_square_retries))
        # Monotonic per-cycle token: bumped once at the top of each _cycle. An
        # entry whose square FAILED (squaring never set) stamps this on itself so
        # a LATER path in the SAME cycle (overall-basket / EOD) does not square it
        # again — the retry waits for the next cycle (else the retry budget burns
        # 2-3× per cycle and hammers the order API).
        self._cycle_token = 0
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "guarded": 0, "exits": 0, "reprices": 0, "stuck": 0,
            "last_run_at": None, "last_error": None,
            # Durable across cycles (last_error is cleared each clean cycle): a
            # square that exhausted its retry budget and was STOPPED (entry kept
            # registered — the OCO + confirmed-flat finalize remain — but the guard
            # no longer re-issues it). Non-zero = operator intervention required.
            "escalations": 0, "last_escalation": None,
        }

    def status(self) -> Dict[str, Any]:
        st = dict(self._stats)
        st["guarded"] = len(self._registry)
        # `stuck` = guarded positions whose exit machinery has terminated with the
        # position (possibly) still open: the re-price loop ended unfilled
        # (exhausted / hard reject) or the initial square exhausted its retry
        # budget (square_stopped) — surfaced so an operator sees an un-fillable
        # exit, never a silent one.
        st["stuck"] = sum(1 for e in self._registry.snapshot()
                          if e.get("reprice_exhausted") or e.get("reprice_stopped")
                          or e.get("square_stopped"))
        return st

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One guard pass over all registered positions. NEVER raises."""
        exits: List[Dict[str, Any]] = []
        self._cycle_token += 1  # new cycle → a re-added entry is eligible again
        try:
            if len(self._registry) == 0:
                self._stats["cycles"] += 1
                self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
                return exits

            client = await self._client_factory()
            if client is None:
                self._stats["last_error"] = "no client"
                return exits

            # A READ FAILURE (e.g. expired token) is UNKNOWN, not flat. Since the
            # broker-truth contract, FlattradeClient's position_book() RAISES a
            # typed BrokerReadError on any Noren failure instead of returning [].
            # Skip the ENTIRE cycle so the finalize / stop / EOD logic below can
            # never act on an unreadable book and un-watch a live position. (The
            # cycle is also wrapped in a broad except, but handling it here lets
            # us surface token-expiry and guarantees no partial-cycle side effects.)
            try:
                book = await client.position_book()
            except BrokerReadError as exc:
                self._stats["last_error"] = (
                    TOKEN_EXPIRED_HINT if exc.is_session_expired
                    else f"position read failed: {str(exc.emsg)[:180]}")
                return exits

            # A NON-EMPTY list is a KNOWN book. With the raise-on-error contract an
            # empty [] is the doc-confirmed "no data" flat account — but we STILL
            # hold on an empty/non-list book as defense-in-depth: a legacy/mock
            # client may return [] on error, the "no data" discriminator is only
            # doc-verified for PositionBook, and a seen-filled guard entry implies
            # a position existed today (Noren keeps its netqty=0 row intraday), so
            # a truly-empty book while guarding is anomalous, not proof of flat.
            # We must NEVER read UNKNOWN as flat and finalize a still-open position
            # (cancel its OCO / journal a false CLOSE). Mirrors reboot_reconcile's
            # "empty-book false-close hole" guard.
            book_is_known = isinstance(book, list) and len(book) > 0
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

            # Layer 2: pick the ≤ K squaring entries to ESCALATE (widen the band) this
            # cycle — global per-cycle budget so a synchronized basket can't burst past
            # the order rate limit. Empty on an UNKNOWN book (never re-price on a bad read).
            reprice_ids = self._select_reprice_ids(now, by_tsym, book_is_known)
            # Snapshot last_error so the end-of-cycle "clean cycle → clear stale error"
            # reset does NOT clobber a signal a re-price / finalize sets THIS cycle
            # (e.g. exit exhausted / rejected / unpriced — never a silent stuck exit).
            _err_at_start = self._stats.get("last_error")

            for entry in self._registry.snapshot():
                pos = by_tsym.get(entry["tsym"])
                netqty = _parse_netqty(pos.get("netqty")) if pos else None

                # Not a live non-zero position in the book — flat / absent / unparseable.
                if pos is None or netqty is None or netqty == 0:
                    if not entry.get("seen_filled"):
                        # Still pending its fill — KEEP, but bound a never-filling
                        # (rejected/canceled) entry by a grace window. An UNKNOWN
                        # book gives NO information about the fill, so it must not
                        # advance the age-out counter (60s of broker blips would
                        # otherwise age out a perfectly pending entry).
                        if not book_is_known:
                            continue
                        entry["misses"] = int(entry.get("misses", 0)) + 1
                        if entry["misses"] >= self._max_pending_misses:
                            # Aging out an entry that never showed in the position
                            # book: the ENTRY ORDER may still be WORKING (a resting
                            # LMT the market walked away from) — a later fill would
                            # be unwatched. Best-effort cancel it (the registry key
                            # IS its norenordno) BEFORE dropping, then cancel the
                            # resting OCO so no stray alert survives (audit L21). A
                            # confirmed-flat drop cancels its OCO via _finalize_flat
                            # instead.
                            if hasattr(client, "cancel_order"):
                                try:
                                    await client.cancel_order(entry["id"])
                                except Exception as exc:
                                    log.warning(
                                        "guard age-out: cancel entry order %s failed "
                                        "(may already be terminal): %s",
                                        entry["id"], exc)
                            await self._cancel_oco_best_effort(
                                client, entry, "age_out")
                            self._registry.remove(entry["id"])
                        continue
                    # seen_filled: decide CONFIRMED-FLAT vs UNKNOWN. Finalizing
                    # (cancel OCO + journal close + drop) is IRREVERSIBLE, so it must
                    # fire ONLY on a real "the broker says this position is gone",
                    # never on an UNKNOWN read.
                    if not book_is_known:
                        # Empty/non-list book == UNKNOWN (broker hiccup) → HOLD: keep
                        # the entry registered + the OCO resting. The position may
                        # well still be open; the next good read decides. (Does not
                        # advance flat_reads — an UNKNOWN read is not a flat read.)
                        continue
                    if pos is not None and netqty is None:
                        # Present row but UNPARSEABLE netqty ("nan"/"abc") == UNKNOWN
                        # for this scrip → HOLD (never coerce unparseable to flat,
                        # per kill_switch._parse_netqty's contract).
                        continue
                    # book_is_known AND (present row netqty==0, OR this tsym absent
                    # from a complete non-empty book) → an authenticated flat read.
                    # Require N CONSECUTIVE such reads before finalizing, so a single
                    # broker-book eventual-consistency blip (a KNOWN book momentarily
                    # missing this row) can't un-watch a still-live position. UNKNOWN
                    # reads neither advance nor reset the count; a live read resets it.
                    entry["flat_reads"] = int(entry.get("flat_reads", 0)) + 1
                    if entry["flat_reads"] < self._flat_confirm_reads:
                        continue
                    # N consecutive authenticated flat reads → genuinely FLAT. The SOLE
                    # place the OCO is cancelled, the close journaled, and the entry
                    # dropped (however it went flat: the guard's exit filled, the OCO
                    # fired, or it was closed elsewhere).
                    await self._finalize_flat(client, entry)
                    continue

                # Live, filled position.
                entry["seen_filled"] = True
                entry["misses"] = 0
                entry["flat_reads"] = 0
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

                # A guard square is already working for this entry (place accepted,
                # not yet confirmed flat). Do NOT re-evaluate stops or re-issue. If it
                # was selected for escalation this cycle, over-sell-safely re-price the
                # exit at the next (wider) band; otherwise WAIT (the resting exit + the
                # still-intact OCO protect it). Layer-1 invariant preserved: no stop
                # re-eval while squaring; finalize still only on confirmed-flat.
                if entry.get("squaring"):
                    if entry["id"] in reprice_ids:
                        await self._reprice(client, entry, now, exits)
                    continue

                # Log-only guard (LIVE_GUARD_ARMED unset): once a dry-run square has
                # surfaced this position's exit intent, KEEP it registered (so
                # guard_status still shows it as watched) but don't re-fire the same
                # dry-run every cycle. A real (armed) square never sets this flag.
                # square_stopped (retry budget exhausted, audit L20) likewise stays
                # registered — the OCO backstop + confirmed-flat finalize still
                # apply — and its DISCRETIONARY squares (premium/spot/time) stop
                # here; the 15:00 EOD backstop still attempts it every cycle
                # (ignore_square_stopped=True) since a no-OCO manual/rehydrated
                # position has no other automated exit left.
                if entry.get("dry_run_exit_logged") or entry.get("square_stopped"):
                    continue

                verdict = evaluate_exit(entry["state"], lp)
                entry["state"] = verdict["state"]
                if verdict["exit"]:
                    await self._issue_square(
                        client, entry, f"software_{verdict['reason']}",
                        verdict["reason"], exits, now)
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
            await self._evaluate_overall_basket(client, by_tsym, exits, now)

            # ── 15:00 IST EOD square (deployed positions only) ──
            await self._evaluate_eod_square(client, now, exits)

            self._stats["cycles"] += 1
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            # Clear a STALE error only when nothing this cycle set a new one — so a
            # re-price / finalize signal (exhausted / rejected / unpriced) survives to
            # be surfaced in status(), rather than being wiped by the clean-cycle reset.
            if self._stats.get("last_error") == _err_at_start:
                self._stats["last_error"] = None
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live position guard cycle failed: %s", exc)
        return exits

    async def _issue_square(
        self, client: Any, entry: Dict[str, Any], square_reason: str,
        exit_reason: str, exits: List[Dict[str, Any]], now: datetime,
        ignore_square_stopped: bool = False,
    ) -> None:
        """Issue a margin-safe square exit for a breached position and MARK it
        squaring — place-and-track, NOT place-and-forget. NEVER raises.

        This does NOT drop the entry, cancel the OCO, or journal the close. A
        ``square_fn`` result is a place-ACCEPTANCE (or a dry-run), not a fill: on a
        fast crash the marketable exit can rest unfilled. So the entry stays
        registered and the OCO stays resting until the broker book confirms the
        position flat, at which point ``_finalize_flat`` (the sole finalizer) cancels
        the OCO, journals the close, and drops the entry.

        Result handling:
          * real + accepted (squared, not dry_run) → set ``squaring`` so we don't
            re-issue; wait for confirmed-flat.
          * dry-run (LIVE_GUARD_ARMED off) → transmits nothing; keep the entry + OCO,
            journal nothing — and log the intent ONCE (``dry_run_exit_logged``), not
            every 1.5s cycle (audit L20 visibility without spam).
          * contention (``exit_in_flight_elsewhere`` — another path holds the item-#3
            per-tsym exit claim) → retry next cycle; NOT counted toward exhaustion
            (a legitimate concurrent exit must never burn this entry's budget).
          * real + failed (reject / raise) → retry next cycle, bounded by
            ``max_square_retries``; on exhaustion ESCALATE (escalations stat +
            operator log) and STOP re-issuing (``square_stopped``) — the entry STAYS
            registered so the broker-OCO backstop, the confirmed-flat finalize, and
            guard_status visibility all remain (audit L20, adapted to Layer 1:
            un-watching a live position is never the answer).

        Cross-path idempotency (audit-item-#6 hardening): the premium loop, the
        overall-basket square, and the EOD square all funnel through here in the
        same cycle — the per-cycle token makes a FAILED square wait for the next
        cycle instead of burning 2-3 broker attempts per cycle.
        """
        if entry.get("dry_run_exit_logged"):
            return
        if entry.get("square_stopped") and not ignore_square_stopped:
            # Retry budget exhausted — no more discretionary squares. The EOD
            # square passes ignore_square_stopped=True: the 15:00 backstop must
            # keep attempting (a manual/rehydrated position may have NO OCO, so
            # EOD is its ONLY remaining automated exit; a persistent RMS reject
            # retried once per cycle for the last 30 min is the acceptable cost).
            return
        if entry.get("last_square_cycle") == self._cycle_token:
            return
        entry["last_square_cycle"] = self._cycle_token
        try:
            result = await self._square_fn(
                client, entry["position"], reason=square_reason)
        except Exception as exc:  # square must never kill the loop
            log.exception("guard square failed for %s: %s", entry["tsym"], exc)
            result = {"squared": False, "error": str(exc)[:200]}
        squared_ok = bool(result.get("squared"))
        dry_run = bool(result.get("dry_run"))
        if squared_ok and not dry_run:
            entry["squaring"] = True
            entry["square_reason"] = exit_reason        # written ONCE (never by a re-price)
            # A broker-ACCEPTED square proves the path works again — clear the
            # failure bookkeeping so an earlier bad spell can't linger.
            entry["square_retries"] = 0
            entry["square_stopped"] = False
            # Seed the Layer 2 escalation state: the NEXT band, when this exit was
            # issued (interval gate), and the resting exit's id/qty (so a re-price
            # can cancel exactly it and _finalize_flat can cancel it on flat).
            entry["square_band_idx"] = 1
            entry["square_last_ts"] = now
            entry["square_ordno"] = result.get("norenordno")
            # Prefer the qty the executor ACTUALLY placed (it re-confirms and
            # clamps to the fresh book truth post-cancel); fall back to the
            # last-seen netqty. A later re-price sizes off this, so recording the
            # pre-clamp qty could over-sell on a book-unreadable re-price.
            try:
                entry["square_qty"] = int(result.get("qty")
                                          or abs(int(entry["position"].get("netqty") or 0)))
            except (TypeError, ValueError):
                entry["square_qty"] = 0
            # "exits" counts squares the broker ACCEPTED — a dry-run intent or a
            # failed attempt is recorded in the cycle's exits list but must not
            # inflate the stat.
            self._stats["exits"] += 1
        elif dry_run:
            # Log-only mode: surface the intent once; the loop-level skip keeps the
            # entry registered + visible without re-firing every cycle.
            entry["dry_run_exit_logged"] = True
            log.info("guard DRY-RUN square for %s (%s) — LIVE_GUARD_ARMED off, "
                     "nothing transmitted (logged once)", entry["tsym"], exit_reason)
        elif str(result.get("reason") or "") in _SOFT_RETRY_REASONS:
            # Placed NOTHING (contention / unpriced / cancel-unconfirmed). Retry
            # next cycle; never counts toward the retry budget — but surface it so
            # a position that can't square is never silent.
            _soft = str(result.get("reason"))
            self._stats["last_error"] = (
                f'{entry["tsym"]}: square deferred ({_soft}) — retrying')
            log.info("guard square for %s deferred (%s) — retrying next cycle",
                     entry["tsym"], _soft)
        else:
            entry["square_retries"] = int(entry.get("square_retries", 0)) + 1
            if entry["square_retries"] >= self._max_square_retries:
                # Exhausted: stop re-issuing (the broker keeps rejecting — more
                # attempts only burn the order-rate budget) but KEEP the entry
                # registered: the resting OCO is the remaining backstop and the
                # confirmed-flat finalize still cleans up when it fires.
                entry["square_stopped"] = True
                self._stats["escalations"] = int(self._stats.get("escalations", 0)) + 1
                self._stats["last_escalation"] = (
                    f"square exhausted for {entry['tsym']} after "
                    f"{entry['square_retries']} attempts")
                self._stats["last_error"] = (
                    f'{entry["tsym"]}: square FAILED {entry["square_retries"]}x — '
                    "STOPPED re-issuing; broker OCO is the remaining backstop (operator)")
                log.error(
                    "guard: square for %s FAILED %d times — STOPPING re-issue; "
                    "broker OCO is the only remaining automated backstop, OPERATOR "
                    "INTERVENTION REQUIRED", entry["tsym"], entry["square_retries"])
            else:
                log.warning(
                    "guard: square NOT confirmed for %s (%s) — kept under guard, "
                    "retry %d/%d",
                    entry["tsym"], result.get("reason") or result.get("error"),
                    entry["square_retries"], self._max_square_retries)
        exits.append({"id": entry["id"], "tsym": entry["tsym"],
                      "reason": exit_reason, "result": result})

    def _select_reprice_ids(
        self, now: datetime, by_tsym: Dict[str, Dict[str, Any]], book_is_known: bool
    ) -> set:
        """Return the ≤ reprice_max_per_cycle entry ids to ESCALATE (widen the band)
        this cycle. Bounds a synchronized N-leg burst inside the order rate limit.

        Excludes an entry that: has no reprice_fn wired; is on an UNKNOWN book (empty/
        [] read — never re-price on a broker hiccup); is not squaring; has already
        terminated (exhausted / stopped); has no band left; is not a LIVE non-zero
        position this cycle; or whose interval has not elapsed. Eligible entries are
        drained OLDEST-square_last_ts first (round-robin) so no leg starves."""
        if not book_is_known or self._reprice_fn is None:
            return set()
        sched = self._reprice_band_schedule
        eligible: List[Dict[str, Any]] = []
        for e in self._registry.snapshot():
            if not e.get("squaring"):
                continue
            if e.get("reprice_stopped") or e.get("reprice_exhausted"):
                continue
            if int(e.get("square_band_idx", 0)) >= len(sched):
                continue
            pos = by_tsym.get(e["tsym"])
            nq = _parse_netqty(pos.get("netqty")) if pos else None
            if pos is None or nq is None or nq == 0:
                continue  # not a live non-zero position this cycle (flat/absent/unparseable)
            ts = e.get("square_last_ts")
            if ts is not None and (now - ts).total_seconds() < self._reprice_interval_seconds:
                continue  # give the current resting exit time to fill before re-pricing
            eligible.append(e)
        eligible.sort(key=lambda e: e.get("square_last_ts") or _EPOCH0)
        return {e["id"] for e in eligible[: self._reprice_max_per_cycle]}

    async def _reprice(
        self, client: Any, entry: Dict[str, Any], now: datetime,
        exits: List[Dict[str, Any]],
    ) -> None:
        """One over-sell-safe escalation of a resting-unfilled guard exit via the
        injected ``reprice_fn`` (which cancels the TRACKED prior order, re-reads its
        fillshares, and places ONLY the confirmed remaining qty at a bid-anchored,
        circuit-clamped price). NEVER raises. The band advances ONLY on a genuine new
        placement; a re-price NEVER cancels the OCO, journals, or drops the entry —
        those stay with the confirmed-flat finalizer. ``square_reason`` is never
        mutated (the ``_reprice`` suffix is a local remarks string only)."""
        sched = self._reprice_band_schedule
        band = sched[int(entry["square_band_idx"])]
        try:
            result = await self._reprice_fn(
                client, entry["position"], band_pct=band,
                prev_ordno=entry.get("square_ordno"),
                prev_qty=int(entry.get("square_qty", 0)),
                reason=f'{entry.get("square_reason")}_reprice')
        except Exception as exc:  # a re-price must never kill the loop
            log.exception("guard reprice failed for %s: %s", entry["tsym"], exc)
            result = {"squared": False, "reason": "error", "error": str(exc)[:200]}

        reason = result.get("reason")
        via = result.get("via")

        # Stamp square_last_ts on EVERY attempt (not just placements). This is what
        # makes the interval a true rate gate AND — critically — keeps an
        # unpriceable-yet-open leg from monopolizing the oldest-first K-budget and
        # STARVING priceable legs (a leg whose ts never advanced would be re-selected
        # every cycle). An attempt was made this cycle either way.
        entry["square_last_ts"] = now

        # unpriced == the primitive placed NOTHING (no usable anchor — illiquid strike /
        # quote outage); the prior exit still rests. Surface it (so a leg that can't
        # escalate is not silent) and do NOT count it as a re-price; the next interval
        # re-attempts.
        if reason == "unpriced":
            self._stats["last_error"] = f'{entry["tsym"]}: reprice unpriced (no quote) — retrying'
            return

        # Another exit path (kill switch / manual square) holds this tsym's exit
        # claim — the primitive placed NOTHING. Same band, retry next interval (by
        # then the other path has either flattened it — the confirmed-flat finalize
        # cleans up — or released the claim).
        if reason == "exit_in_flight_elsewhere":
            self._stats["last_error"] = (
                f'{entry["tsym"]}: reprice deferred — exit in flight on another path')
            return

        self._stats["reprices"] = self._stats.get("reprices", 0) + 1
        exits.append({"id": entry["id"], "tsym": entry["tsym"],
                      "reason": "reprice", "band_pct": band, "result": result})

        # The fill happened during/before the cancel → do NOT advance the band; the
        # next cycle's confirmed-flat path finalizes.
        if via == "already_flat" or result.get("remaining") == 0:
            return

        if result.get("squared") and via == "exit_order":
            entry["square_band_idx"] = int(entry["square_band_idx"]) + 1
            entry["square_ordno"] = result.get("norenordno")
            try:
                entry["square_qty"] = int(result.get("qty") or entry.get("square_qty", 0))
            except (TypeError, ValueError):
                pass
            if int(entry["square_band_idx"]) >= len(sched):
                # Terminal band placed, still unfilled — surface it (never silent).
                entry["reprice_exhausted"] = True
                self._stats["last_error"] = (
                    f'{entry["tsym"]}: exit resting at {band}% (terminal band) — unfilled')
                log.warning(
                    "guard reprice exhausted for %s at %s%% band — resting unfilled",
                    entry["tsym"], band)
            return

        # ── failures — band NOT advanced ──
        if reason == "cancel_unconfirmed":
            # Could not confirm the prior exit is dead → the primitive placed NOTHING
            # (over-sell-safe). Same band, retry next interval.
            self._stats["last_error"] = f'{entry["tsym"]}: reprice cancel unconfirmed — retrying'
            return
        if result.get("failures"):
            # Hard REJECT (placed ok=False twice) — RMS/margin/session; re-pricing
            # cannot cure it. STOP escalating this entry and page the operator.
            entry["reprice_stopped"] = True
            self._stats["last_error"] = (
                f'{entry["tsym"]}: exit REJECTED — position may be unprotected (operator)')
            log.warning("guard reprice REJECTED for %s: %s", entry["tsym"], result.get("failures"))
            return
        # transport / unknown error → keep same band, retry next interval.
        return

    async def _finalize_flat(self, client: Any, entry: Dict[str, Any]) -> None:
        """The broker confirms this position FLAT (netqty→0). The SOLE place that
        cancels the resting OCO, journals the close, and drops the entry — gated on
        broker truth, never on a place-acceptance. NEVER raises.

        * cancel_oco runs on ANY confirmed flat when an oco_al_id exists: a no-op if
          the OCO itself fired, and it clears an OCO that a close-elsewhere would
          otherwise orphan (a resting alert against a flat account could open a fresh
          naked short).
        * cancel the tracked guard exit (``square_ordno``) too: the guard leaves a
          marketable SELL resting alongside the OCO, so if the OCO (or a manual close)
          filled FIRST, that guard SELL is now orphaned against a flat account → a
          fresh naked short if the market ticks back into it. Best-effort cancel it.
        * on_close journals ONLY when a guard square was pending (``squaring``), using
          the tracked reason and the last-seen broker mark (an estimate; reboot
          reconcile back-fills the true fill price). A position closed OUTSIDE the
          guard is dropped without a journal.
        """
        if entry.get("oco_al_id") and hasattr(client, "cancel_oco"):
            try:
                await client.cancel_oco(entry["oco_al_id"])
            except Exception as exc:  # a cancel failure logs, never breaks the cycle
                log.exception(
                    "guard cancel_oco failed for %s (al_id=%s): %s",
                    entry["tsym"], entry.get("oco_al_id"), exc)
        sq_ordno = entry.get("square_ordno")
        if sq_ordno and hasattr(client, "cancel_order"):
            try:
                await client.cancel_order(sq_ordno)
            except Exception as exc:  # best-effort; a no-op if already terminal
                log.exception(
                    "guard cancel resting exit failed for %s (%s): %s",
                    entry["tsym"], sq_ordno, exc)
        if entry.get("squaring") and self._on_close is not None:
            try:
                await self._on_close(
                    entry, entry["position"].get("lp"),
                    entry.get("square_reason"),
                    {"squared": True, "via": "confirmed_flat"})
            except Exception as exc:
                log.exception("guard on_close failed for %s: %s", entry["tsym"], exc)
        self._registry.remove(entry["id"])

    async def _cancel_oco_best_effort(
        self, client: Any, entry: Dict[str, Any], why: str
    ) -> None:
        """Cancel a carried resting OCO/GTT alert (best-effort) when its position
        leaves the guard by a NON-square path — closed-elsewhere flat-drop or
        never-filled age-out. Otherwise the resting NRML OCO rests ORPHANED at the
        broker and can fill against a position that no longer exists (audit L21).

        A confirmed square cancels via the confirmed-square gate in
        ``_square_and_record`` instead; a retry / exhaustion drop deliberately KEEPS
        the OCO as the remaining backstop, so those paths never call this."""
        al_id = entry.get("oco_al_id")
        if not al_id or not hasattr(client, "cancel_oco"):
            return
        try:
            await client.cancel_oco(al_id)
            log.info("guard: canceled orphaned OCO %s for %s (%s)",
                     al_id, entry.get("tsym"), why)
        except Exception as exc:  # never breaks the cycle — the drop still proceeds
            log.warning("guard: cancel orphaned OCO %s for %s failed (%s): %s",
                        al_id, entry.get("tsym"), why, exc)

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
        await self._issue_square(
            client, entry, f"software_{reason}", reason, exits, now)
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
        await self._issue_square(
            client, entry, "software_time_stop", "time_stop", exits, now)
        return True

    async def _evaluate_eod_square(
        self, client: Any, now: datetime, exits: List[Dict[str, Any]]
    ) -> None:
        """15:00 IST EOD square for guarded positions (manual + deployed) that are
        NOT already being squared.

        The 10-minute auto-square timer that once bounded manual LIVE_TEST positions
        was removed, so the 15:00 IST EOD square is the "never left open" backstop for
        a still-open, not-yet-squared position (no source is EOD-exempt any more).
        Deployed positions additionally follow their strategy rules + resting OCO;
        manual positions rely on the software guard stop + this EOD square. reason=
        "eod_square"; the actual transmit is gated by the injected square_fn
        (LIVE_GUARD_ARMED).

        LIMITATION (Layer-1): an entry already `squaring` (a guard exit was issued but
        the broker hasn't confirmed flat) is SKIPPED here — re-issuing it every ~1.5s
        cycle from 15:00 onward would breach the order rate limit for no benefit at
        the same 1% band. So EOD does NOT force-fill a guard exit that is resting
        unfilled on a blown-through market; that position waits on its resting exit +
        (for deployed) the OCO. Delivering an EOD-time re-price/escalation for a stuck
        `squaring` entry is Layer 2 (interval-gated widening re-price, mirroring
        kill_switch.panic_squareoff_verified)."""
        if len(self._registry) == 0:
            return
        ist_now = (now.astimezone(timezone.utc) + _IST).time()
        eod_reached = ist_now >= self._eod_square_ist
        # Phase 5B B5: entries may carry an EARLIER per-entry square time
        # (square_at_ist, normalized+clamped strictly before EOD at
        # registration). Same semantics as EOD (ignore_square_stopped), reason
        # "exit_time". Entries WITHOUT the field behave byte-identically to the
        # pre-5B EOD-only flow.
        ist_hhmm = f"{ist_now.hour:02d}:{ist_now.minute:02d}"
        if not eod_reached and not any(
            e.get("square_at_ist") and not e.get("squaring")
            and ist_hhmm >= e["square_at_ist"]
            for e in self._registry.snapshot()
        ):
            return
        for entry in self._registry.snapshot():
            if entry.get("squaring"):
                continue  # a square is already working (Layer 2 re-price escalates it)
            if eod_reached:
                reason = "eod_square"
            elif entry.get("square_at_ist") and ist_hhmm >= entry["square_at_ist"]:
                reason = "exit_time"
            else:
                continue
            await self._issue_square(
                client, entry, reason, reason, exits, now,
                ignore_square_stopped=True)

    async def _evaluate_overall_basket(
        self, client: Any, by_tsym: Dict[str, Dict[str, Any]],
        exits: List[Dict[str, Any]], now: datetime,
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
        # single _issue_square choke-point (place-and-mark; the OCO cancel + close-
        # loop happen later on confirmed-flat). An entry already `squaring` is
        # skipped — its exit is already working.
        for entry in self._registry.snapshot():
            if entry.get("squaring"):
                continue
            await self._issue_square(
                client, entry, f"software_{ov['reason']}", ov["reason"], exits, now)
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
