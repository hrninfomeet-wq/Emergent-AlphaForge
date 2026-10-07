"""Live-tick PAPER loop for the scalper specs. Env-gated, default OFF (``SCALP_PAPER_ENABLED=1`` to run).

It consumes the SAME Upstox fan-out the exit monitor uses (bounded queue, drop-oldest — it can never
stall the receive loop), advances the 1-s grid on the wall clock, and executes every engine action
against the deterministic ``SimBroker`` (``replay.drive_second`` — the identical step the replay
uses, fed every tick of the second in arrival order). It imports nothing from ``app.live``: there is
no code path from here to a broker order (pinned by tests/test_scalping_sim_costs_runner.py).

Restart: paper fills live only in the in-process simulator, so a restart cannot be reconciled against a
broker. The day's risk state (trades, realized P&L, daily-loss pause, loss-streak pause, cooldown, halt)
IS restored from the snapshot; an open paper round trip found in it is journaled
``paper_restart_abandoned`` and the engines start flat — honest, and it never invents a fill.

Failure policy: any exception in an iteration (Mongo blip, bad data) is logged, recorded in
``status()['last_error']`` and the loop continues on the next second; a failed session setup is retried.
An exception inside the engine step halts the paper engines (fail closed) and is recorded the same way.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.scalping import journal
from app.scalping.config import PRESETS, ScalperConfig
from app.scalping.engine import EngineStats, ScalperEngine
from app.scalping.market import Contract, MarketState
from app.scalping.replay import drive_second, execute_actions
from app.scalping.sim_broker import SimBroker, SimParams

log = logging.getLogger("alphaforge.scalping.paper")
IST = timezone(timedelta(hours=5, minutes=30))
ENV_FLAG = "SCALP_PAPER_ENABLED"
STRATEGIES_ENV = "SCALP_PAPER_STRATEGIES"
#: N1 was KILLED by its pre-registered stage-2 gate (docs/scalping/05); only the untested S1-E runs by default.
DEFAULT_PAPER_STRATEGIES = ("scalp_sensex_s1e_expiry_joint_impulse",)
TRADEABLE_PHASES = {"open", "cas", "derivatives_only"}
SNAPSHOT_EVERY_S = 30
CONTRACT_REFRESH_S = 300
MAX_SECOND_TICKS = 5000
_STATS_FIELDS = {f.name for f in fields(EngineStats)}


def paper_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "0")).strip().lower() in ("1", "true", "yes", "on")


def configured_strategies() -> List[ScalperConfig]:
    """Presets named in SCALP_PAPER_STRATEGIES (comma-separated ids), default S1-E only. Unknown ids raise."""
    raw = os.environ.get(STRATEGIES_ENV)
    ids = [x.strip() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_PAPER_STRATEGIES)
    unknown = [i for i in ids if i not in PRESETS]
    if unknown:
        raise ValueError(f"{STRATEGIES_ENV}: unknown strategy ids {unknown}; known {sorted(PRESETS)}")
    return [PRESETS[i] for i in ids]


async def load_live_contracts(db, day: str, underlyings=("NIFTY", "SENSEX"), expiries_per_und: int = 2) -> List[Contract]:
    out: List[Contract] = []
    for und in underlyings:
        exps = sorted({d["expiry_date"] async for d in db.option_contracts.find(
            {"underlying": und, "expiry_date": {"$gte": day}}, {"expiry_date": 1})})[:expiries_per_und]
        async for c in db.option_contracts.find({"underlying": und, "expiry_date": {"$in": exps}},
                                                {"instrument_key": 1, "strike": 1, "side": 1, "expiry_date": 1,
                                                 "lot_size": 1, "trading_symbol": 1, "exchange_token": 1}):
            key = c["instrument_key"]
            if key.count("|") != 1:          # 3-part keys are the expired-contract identity, not the feed key
                continue
            out.append(Contract(key, und, float(c["strike"]), c["side"], c["expiry_date"],
                                int(c.get("lot_size") or 0), c.get("trading_symbol") or ""))
    return out


def restore_day_state(engine: ScalperEngine, snap: Dict[str, Any]) -> None:
    """Put the day's risk state back on a fresh engine. ``stats.day`` is restored too, otherwise the first
    on_second treats the restart as a new day and wipes the daily-loss pause and counters."""
    st = {k: v for k, v in (snap.get("stats") or {}).items() if k in _STATS_FIELDS}
    engine.stats = EngineStats(**st)
    engine.paused_for_day = snap.get("paused_for_day")
    engine.paused_until_ms = int(snap.get("paused_until_ms") or 0)
    engine.paused_reason = snap.get("paused_reason") or ""
    engine.last_exit_ms = int(snap.get("last_exit_ms") or 0)
    engine.halted = snap.get("halted")
    engine._seq = int(snap.get("seq") or 0) + 1000     # never reuse a client order id of the day


class ScalpPaperRunner:
    def __init__(self, manager: Any, *, db_factory, configs: Optional[List[ScalperConfig]] = None,
                 sim: Optional[SimParams] = None):
        self.manager = manager
        self.db_factory = db_factory
        self.configs = configs or configured_strategies()
        for c in self.configs:
            if c.mode != "paper":
                raise ValueError(f"{c.strategy_id}: paper runner accepts mode='paper' only")
        self.sim = sim or SimParams()
        self._task: Optional[asyncio.Task] = None
        self.session_date: Optional[str] = None
        self.market: Optional[MarketState] = None
        self.pairs: List = []
        self.alerts: List[Dict[str, Any]] = []
        self.last_tick_ingest_ms: Optional[int] = None
        self.ticks_seen = 0
        self.error: Optional[str] = None           # engine-step failure (engines halted)
        self.last_error: Optional[str] = None      # any iteration failure (loop continued)
        self.errors = 0
        self._snapshot_due = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        if not paper_enabled():
            log.info("Scalper paper runner disabled (%s=0)", ENV_FLAG)
            return False
        if self._task and not self._task.done():
            return True
        self._task = asyncio.create_task(self._run(), name="scalp-paper-runner")
        return True

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def kill(self, reason: str = "operator_kill", *, now_ms: Optional[int] = None) -> int:
        """I9 for paper: halt every engine AND execute the cancels it returns against its simulator."""
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        for engine, broker in self.pairs:
            execute_actions(engine, broker, engine.kill(now, reason), now, self.alerts)
        self._snapshot_due = True
        return len(self.pairs)

    def status(self) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        return {
            "enabled": paper_enabled(), "running": bool(self._task and not self._task.done()),
            "session_date": self.session_date, "error": self.error, "last_error": self.last_error,
            "errors": self.errors, "ticks_seen": self.ticks_seen,
            "last_tick_age_ms": (now - self.last_tick_ingest_ms) if self.last_tick_ingest_ms else None,
            "contracts": len(self.market.contracts) if self.market else 0,
            "engines": [{"strategy_id": e.cfg.strategy_id, "halted": e.halted, "paused_for_day": e.paused_for_day,
                         "paused_until_ms": e.paused_until_ms, "reconcile_required": e.reconcile_required,
                         "position_qty": e.position()["qty"], "trades_today": e.stats.trades_today,
                         "realized_net_inr": round(e.stats.realized_net_inr, 2)} for e, _ in self.pairs],
            "recent_alerts": self.alerts[-20:],
        }

    def _note_error(self, where: str, exc: BaseException) -> None:
        self.errors += 1
        self.last_error = f"{where}: {type(exc).__name__}: {exc}"[:400]
        log.exception("Scalper paper runner %s failed; continuing", where)

    # ------------------------------------------------------------------ session setup
    async def _end_session(self, db, now_ms: int) -> None:
        """Journal any engine still holding a position or a trip before it is replaced (no silent loss)."""
        for engine, _ in self.pairs:
            pos = engine.position()
            if pos["qty"] != 0 or engine.trip is not None:
                await journal.write_events(db, [{
                    "kind": "paper_session_end_open_position", "ts_ms": now_ms, "strategy_id": engine.cfg.strategy_id,
                    "qty": pos["qty"], "avg_buy": pos["avg_buy"], "trip": engine.trip,
                    "last_quote_bid": engine.last_quote.bid if engine.last_quote else None}],
                    mode="paper", session_date=self.session_date or "")

    async def _new_session(self, db, day: str, now_ms: int) -> None:
        await journal.ensure_indexes(db)
        contracts = await load_live_contracts(db, day)
        market = MarketState(contracts, lookback_s=max(c.sigma_lookback_s for c in self.configs),
                             min_samples=min(c.sigma_min_samples for c in self.configs),
                             max_window_s=max(c.window_s for c in self.configs))
        pairs = []
        for cfg in self.configs:
            lots = sorted({c.lot_size for c in contracts if c.underlying == cfg.underlying and c.lot_size > 0})
            if not lots:
                log.warning("Scalper %s: no lot size resolvable for %s on %s; engine not started",
                            cfg.strategy_id, cfg.underlying, day)
                continue
            engine = ScalperEngine(cfg, lot_size=lots[0], cid_prefix=f"pp{day.replace('-', '')}")
            snap = await journal.load_snapshot(db, cfg.strategy_id, day)
            if snap:
                restore_day_state(engine, snap)
                if snap.get("trip") or any(o.get("state") not in ("COMPLETE", "REJECTED", "CANCELED")
                                           for o in snap.get("orders") or []):
                    await journal.write_events(db, [{"kind": "paper_restart_abandoned", "ts_ms": now_ms,
                                                     "strategy_id": cfg.strategy_id, "trip": snap.get("trip")}],
                                               mode="paper", session_date=day)
            pairs.append((engine, SimBroker(self.sim)))
        if self.pairs:
            await self._end_session(db, now_ms)
        self.market, self.pairs, self.session_date = market, pairs, day
        log.info("Scalper paper session %s: %d contracts, engines %s", day, len(contracts),
                 [e.cfg.strategy_id for e, _ in self.pairs])

    async def _journal(self, db, day: str) -> None:
        for engine, _ in self.pairs:
            evs = engine.drain_events()
            if evs:
                try:
                    await journal.write_events(db, evs, mode="paper", session_date=day)
                except Exception:
                    engine.events[:0] = evs          # keep them for the next attempt
                    raise
            new = [t for t in engine.closed_trades if not t.get("_journaled")]
            if new:
                await journal.write_trades(db, [{k: v for k, v in t.items() if k != "_journaled"} for t in new],
                                           mode="paper", session_date=day)
                for t in new:
                    t["_journaled"] = True

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        queue = self.manager.subscribe(max_queue=4096)
        db = self.db_factory()
        last_snapshot_s = 0
        last_contract_refresh_s = 0
        try:
            while True:
                second_ticks: List[dict] = []
                now = time.time()
                next_boundary = int(now) + 1
                deadline = next_boundary - now
                try:
                    while True:
                        tick = await asyncio.wait_for(queue.get(), timeout=max(0.0, deadline))
                        if self.market is not None:
                            self.market.on_tick(tick)
                        if len(second_ticks) < MAX_SECOND_TICKS:
                            second_ticks.append(tick)
                        self.ticks_seen += 1
                        ing = tick.get("ingest_ts")
                        if ing:
                            self.last_tick_ingest_ms = int(ing)
                        deadline = next_boundary - time.time()
                        if deadline <= 0:
                            break
                except asyncio.TimeoutError:
                    pass
                now_ms = next_boundary * 1000 - 1
                try:
                    from app.nse_calendar import market_status
                    ist = datetime.fromtimestamp(now_ms / 1000, IST)
                    if market_status(ist).get("phase") not in TRADEABLE_PHASES:
                        continue
                    day = ist.strftime("%Y-%m-%d")
                    if day != self.session_date:
                        await self._new_session(db, day, now_ms)
                        last_contract_refresh_s = next_boundary
                    elif next_boundary - last_contract_refresh_s >= CONTRACT_REFRESH_S:
                        last_contract_refresh_s = next_boundary
                        self.market.add_contracts(await load_live_contracts(db, day))
                    try:
                        drive_second(now_ms, self.market, self.pairs, self.alerts, second_ticks=second_ticks)
                    except Exception as exc:  # noqa: BLE001 — a paper bug must never take the backend down
                        self.error = f"{type(exc).__name__}: {exc}"[:400]
                        self._note_error("engine step", exc)
                        self.kill("paper_step_exception", now_ms=now_ms)
                    await self._journal(db, day)
                    if self._snapshot_due or next_boundary - last_snapshot_s >= SNAPSHOT_EVERY_S:
                        for engine, _ in self.pairs:
                            await journal.save_snapshot(db, engine.cfg.strategy_id, day, engine.snapshot(), now_ms)
                        last_snapshot_s = next_boundary
                        self._snapshot_due = False
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — log, record, keep the loop alive (retry next second)
                    self._note_error("iteration", exc)
        except asyncio.CancelledError:
            raise
        finally:
            self.manager.unsubscribe(queue)
