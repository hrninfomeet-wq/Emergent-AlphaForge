"""Live-tick PAPER loop for the scalper specs. Env-gated, default OFF (``SCALP_PAPER_ENABLED=1`` to run).

It consumes the SAME Upstox fan-out the exit monitor uses (bounded queue, drop-oldest — it can never
stall the receive loop), advances the 1-s grid on the wall clock, and executes every engine action
against the deterministic ``SimBroker`` (``replay.drive_second`` — the identical step the replay
uses). It imports nothing from ``app.live``: there is no code path from here to a broker order
(pinned by tests/test_scalping_paper_runner.py).

Restart: paper fills live only in the in-process simulator, so a restart cannot be reconciled
against a broker. An open paper round trip found in the day's snapshot is journaled as
``paper_restart_abandoned`` and the engines start flat — honest, and it never invents a fill.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.scalping import journal
from app.scalping.config import PRESETS, ScalperConfig
from app.scalping.engine import ScalperEngine
from app.scalping.market import Contract, INDEX_KEYS, MarketState
from app.scalping.replay import drive_second
from app.scalping.sim_broker import SimBroker, SimParams

log = logging.getLogger("alphaforge.scalping.paper")
IST = timezone(timedelta(hours=5, minutes=30))
ENV_FLAG = "SCALP_PAPER_ENABLED"
TRADEABLE_PHASES = {"open", "cas", "derivatives_only"}
SNAPSHOT_EVERY_S = 30
CONTRACT_REFRESH_S = 300


def paper_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "0")).strip().lower() in ("1", "true", "yes", "on")


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


class ScalpPaperRunner:
    def __init__(self, manager: Any, *, db_factory, configs: Optional[List[ScalperConfig]] = None,
                 sim: Optional[SimParams] = None):
        self.manager = manager
        self.db_factory = db_factory
        self.configs = configs or list(PRESETS.values())
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
        self.error: Optional[str] = None

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

    def kill(self, reason: str = "operator_kill") -> int:
        now = int(time.time() * 1000)
        for engine, _ in self.pairs:
            engine.kill(now, reason)
        return len(self.pairs)

    def status(self) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        return {
            "enabled": paper_enabled(), "running": bool(self._task and not self._task.done()),
            "session_date": self.session_date, "error": self.error, "ticks_seen": self.ticks_seen,
            "last_tick_age_ms": (now - self.last_tick_ingest_ms) if self.last_tick_ingest_ms else None,
            "contracts": len(self.market.contracts) if self.market else 0,
            "engines": [{"strategy_id": e.cfg.strategy_id, "halted": e.halted, "paused_for_day": e.paused_for_day,
                         "paused_until_ms": e.paused_until_ms, "reconcile_required": e.reconcile_required,
                         "position_qty": e.position()["qty"], "trades_today": e.stats.trades_today,
                         "realized_net_inr": round(e.stats.realized_net_inr, 2)} for e, _ in self.pairs],
            "recent_alerts": self.alerts[-20:],
        }

    # ------------------------------------------------------------------ session setup
    async def _new_session(self, db, day: str, now_ms: int) -> None:
        await journal.ensure_indexes(db)
        contracts = await load_live_contracts(db, day)
        self.market = MarketState(contracts, lookback_s=max(c.sigma_lookback_s for c in self.configs),
                                  min_samples=min(c.sigma_min_samples for c in self.configs),
                                  max_window_s=max(c.window_s for c in self.configs))
        self.pairs = []
        for cfg in self.configs:
            lots = sorted({c.lot_size for c in contracts if c.underlying == cfg.underlying and c.lot_size > 0})
            if not lots:
                log.warning("Scalper %s: no lot size resolvable for %s on %s; engine not started",
                            cfg.strategy_id, cfg.underlying, day)
                continue
            snap = await journal.load_snapshot(db, cfg.strategy_id, day)
            engine = ScalperEngine(cfg, lot_size=lots[0], cid_prefix=f"pp{day.replace('-', '')}")
            if snap:
                engine.stats.trades_today = int((snap.get("stats") or {}).get("trades_today") or 0)
                engine.stats.realized_net_inr = float((snap.get("stats") or {}).get("realized_net_inr") or 0.0)
                engine.paused_for_day = snap.get("paused_for_day")
                engine.halted = snap.get("halted")
                engine._seq = int(snap.get("seq") or 0) + 1000
                if snap.get("trip") or snap.get("orders"):
                    await journal.write_events(db, [{"kind": "paper_restart_abandoned", "ts_ms": now_ms,
                                                     "strategy_id": cfg.strategy_id, "trip": snap.get("trip")}],
                                               mode="paper", session_date=day)
            self.pairs.append((engine, SimBroker(self.sim)))
        self.session_date = day
        log.info("Scalper paper session %s: %d contracts, engines %s", day, len(contracts),
                 [e.cfg.strategy_id for e, _ in self.pairs])

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        from app.nse_calendar import market_status
        queue = self.manager.subscribe(max_queue=4096)
        db = self.db_factory()
        last_snapshot_s = 0
        last_contract_refresh_s = 0
        try:
            while True:
                now = time.time()
                next_boundary = int(now) + 1
                deadline = next_boundary - now
                try:
                    while True:
                        tick = await asyncio.wait_for(queue.get(), timeout=max(0.0, deadline))
                        if self.market is not None:
                            self.market.on_tick(tick)
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
                ist = datetime.fromtimestamp(now_ms / 1000, IST)
                if market_status(ist).get("phase") not in TRADEABLE_PHASES:
                    continue
                day = ist.strftime("%Y-%m-%d")
                if day != self.session_date:
                    await self._new_session(db, day, now_ms)
                    last_contract_refresh_s = next_boundary
                elif next_boundary - last_contract_refresh_s >= CONTRACT_REFRESH_S:
                    self.market.add_contracts(await load_live_contracts(db, day))
                    last_contract_refresh_s = next_boundary
                try:
                    drive_second(now_ms, self.market, self.pairs, self.alerts)
                except Exception as exc:  # noqa: BLE001 — a paper bug must never take the backend down
                    self.error = f"{type(exc).__name__}: {exc}"
                    log.exception("Scalper paper step failed; halting paper engines")
                    self.kill("paper_step_exception")
                for engine, _ in self.pairs:
                    evs = engine.drain_events()
                    if evs:
                        await journal.write_events(db, evs, mode="paper", session_date=day)
                    new = [t for t in engine.closed_trades if not t.get("_journaled")]
                    if new:
                        await journal.write_trades(db, [{k: v for k, v in t.items() if k != "_journaled"} for t in new],
                                                   mode="paper", session_date=day)
                        for t in new:
                            t["_journaled"] = True
                if next_boundary - last_snapshot_s >= SNAPSHOT_EVERY_S:
                    for engine, _ in self.pairs:
                        await journal.save_snapshot(db, engine.cfg.strategy_id, day, engine.snapshot(), now_ms)
                    last_snapshot_s = next_boundary
        except asyncio.CancelledError:
            raise
        finally:
            self.manager.unsubscribe(queue)
