"""Recorded-tape replay: tape -> MarketState -> ScalperEngine <-> SimBroker, one session at a time.

The loop is the SAME one the paper runner executes on live ticks (``drive_second``), so a replay
of a recorded paper session reproduces its decisions given identical ticks and SimParams.

Data: ``tick_archive`` (full-mode ticks with 5-level depth, preserved past the 30-day TTL) or
``ticks``. Contracts for a session come from that session's ``chain_snapshots`` (same-day token
identity — tokens are recycled across expiries, HANDOFF T5) with ``option_contracts`` as fallback.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.scalping.config import ScalperConfig
from app.scalping.engine import Action, ScalperEngine
from app.scalping.market import INDEX_KEYS, Contract, MarketState, quote_from_tick
from app.scalping.sim_broker import SimBroker, SimParams

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SEG = {"NIFTY": "NSE_FO", "SENSEX": "BSE_FO"}


# ---------------------------------------------------------------------------- shared step
def execute_actions(engine: ScalperEngine, broker: SimBroker, actions: List[Action], now_ms: int,
                    log: List[Dict[str, Any]]) -> None:
    """Carry out engine actions against the simulator, feeding results back (may cascade)."""
    queue = list(actions)
    guard = 0
    while queue and guard < 1000:
        guard += 1
        a = queue.pop(0)
        if a.kind == "place":
            res = broker.submit(a, now_ms)
            queue += engine.on_submit_result(a.cid, res.ack_ms, ok=res.ok, norenordno=res.norenordno,
                                             reject_reason=res.reject_reason, indeterminate=res.indeterminate)
        elif a.kind == "cancel":
            ok = broker.cancel(a.cid, now_ms)
            queue += engine.on_cancel_result(a.cid, now_ms, ok=ok)
        elif a.kind == "reconcile":
            oms, net = broker.reconcile()
            queue += engine.on_reconcile(now_ms, broker_orders=oms, broker_net_qty=net)
        else:
            log.append({"ts_ms": a.ts_ms, "kind": a.kind, "level": a.level, "reason": a.reason})


def drive_second(now_ms: int, market: MarketState, engines: Iterable[Tuple[ScalperEngine, SimBroker]],
                 log: List[Dict[str, Any]]) -> None:
    """One decision second: (ticks already fed) -> grid advance -> broker events -> engine step."""
    market.on_second(now_ms)
    for engine, broker in engines:
        broker.set_reference(market.quotes)
        for key in broker.orders_keys():
            q = market.quotes.get(key)
            if q is not None:
                broker.on_quote(q, market.depth.get(key))
        for t, om in broker.advance(now_ms):
            execute_actions(engine, broker, engine.on_order_event(om, t), now_ms, log)
        execute_actions(engine, broker, engine.on_second(now_ms, market), now_ms, log)


# ---------------------------------------------------------------------------- data loading
def _db():
    from pymongo import MongoClient
    return MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)["alphaforge"]


def session_bounds_ms(day: str) -> Tuple[int, int]:
    d = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    return int(d.replace(hour=9, minute=0).timestamp() * 1000), int(d.replace(hour=15, minute=41).timestamp() * 1000)


def load_contracts(db, day: str, underlyings=("NIFTY", "SENSEX")) -> List[Contract]:
    out: Dict[str, Contract] = {}
    lots: Dict[Tuple[str, str], int] = {}
    for c in db.option_contracts.find({"underlying": {"$in": list(underlyings)}, "expiry_date": {"$gte": day}},
                                      {"underlying": 1, "expiry_date": 1, "lot_size": 1}):
        lots.setdefault((c["underlying"], c["expiry_date"]), int(c.get("lot_size") or 0))
    for und in underlyings:
        for snap in db.chain_snapshots.find({"session_date": day, "instrument": und},
                                            {"expiry_date": 1, "strikes": 1}):
            exp = snap["expiry_date"]
            for s in snap.get("strikes") or []:
                for side in ("ce", "pe"):
                    key = (s.get(side) or {}).get("instrument_key")
                    if key and key not in out:
                        out[key] = Contract(key, und, float(s["strike"]), side.upper(), exp,
                                            lots.get((und, exp), 0), f"{und} {int(s['strike'])} {side.upper()} {exp}")
        if not any(c.underlying == und for c in out.values()):
            for c in db.option_contracts.find({"underlying": und, "segment": SEG[und], "expiry_date": {"$gte": day}},
                                              {"exchange_token": 1, "strike": 1, "side": 1, "expiry_date": 1,
                                               "lot_size": 1, "trading_symbol": 1}).sort("expiry_date", 1):
                key = f"{SEG[und]}|{c['exchange_token']}"
                if key not in out:
                    out[key] = Contract(key, und, float(c["strike"]), c["side"], c["expiry_date"],
                                        int(c.get("lot_size") or 0), c.get("trading_symbol") or "")
    return list(out.values())


def load_tape(db, day: str, keys: Iterable[str], collection: str = "tick_archive") -> List[dict]:
    lo, hi = session_bounds_ms(day)
    proj = {"_id": 0, "instrument_key": 1, "ts": 1, "received_ts": 1, "ingest_ts": 1, "last_price": 1,
            "best_bid_price": 1, "best_ask_price": 1, "best_bid_quantity": 1, "best_ask_quantity": 1,
            "market_depth": 1, "last_trade_quantity": 1}
    rows = list(db[collection].find({"instrument_key": {"$in": list(keys)}, "ts": {"$gte": lo, "$lte": hi}}, proj))
    for r in rows:
        if r.get("ingest_ts") is None:
            r["ingest_ts"] = r.get("received_ts")      # pre-2026-09-15 recordings: Upstox frame clock
    rows = [r for r in rows if r.get("ingest_ts") is not None]
    rows.sort(key=lambda r: (int(r["ingest_ts"]), int(r.get("ts") or 0)))
    return rows


# ---------------------------------------------------------------------------- session replay
def replay_session(day: str, cfgs: List[ScalperConfig], *, sim: Optional[SimParams] = None, db=None,
                   tape: Optional[List[dict]] = None, contracts: Optional[List[Contract]] = None,
                   collection: str = "tick_archive") -> Dict[str, Any]:
    db = db if db is not None else (_db() if tape is None or contracts is None else None)
    contracts = contracts if contracts is not None else load_contracts(db, day)
    market = MarketState(contracts, lookback_s=max(c.sigma_lookback_s for c in cfgs),
                         min_samples=min(c.sigma_min_samples for c in cfgs),
                         max_window_s=max(c.window_s for c in cfgs))
    keys = set(INDEX_KEYS.values()) | {c.instrument_key for c in contracts}
    tape = tape if tape is not None else load_tape(db, day, keys, collection)
    pairs = []
    for cfg in cfgs:
        lots = sorted({c.lot_size for c in contracts if c.underlying == cfg.underlying and c.lot_size > 0})
        if not lots:
            raise ValueError(f"no lot size for {cfg.underlying} on {day}")
        pairs.append((ScalperEngine(cfg, lot_size=lots[0], cid_prefix=f"rp{day.replace('-', '')}"),
                      SimBroker(sim or SimParams())))
    log: List[Dict[str, Any]] = []
    i, n = 0, len(tape)
    if not n:
        return {"day": day, "error": "empty tape"}
    lo_s = int(tape[0]["ingest_ts"]) // 1000
    _, hi_ms = session_bounds_ms(day)
    for s in range(lo_s, hi_ms // 1000):
        end_ms = s * 1000 + 999
        while i < n and int(tape[i]["ingest_ts"]) <= end_ms:
            market.on_tick(tape[i])
            i += 1
        drive_second(end_ms, market, pairs, log)
    out = {"day": day, "ticks": n, "engines": []}
    for engine, broker in pairs:
        out["engines"].append({"strategy_id": engine.cfg.strategy_id, "underlying": engine.cfg.underlying,
                               "lot_size": engine.lot_size, "trades": engine.closed_trades,
                               "events": engine.drain_events(), "broker_stats": dict(broker.stats),
                               "halted": engine.halted, "open_qty_at_end": engine.position()["qty"],
                               "summary": summarize_trades(engine.closed_trades)})
    out["alerts"] = log
    return out


# ---------------------------------------------------------------------------- metrics
def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0}
    nets = [t["net_inr"] for t in trades]
    eq, peak, mdd, streak, worst = 0.0, 0.0, 0.0, 0, 0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        streak = streak + 1 if x < 0 else 0
        worst = max(worst, streak)
    slip = [t["entry_avg"] - (t.get("entry_quote") or {}).get("ask", t["entry_avg"]) for t in trades]
    lat = [o["first_fill_ms"] - o["sent_ms"] for t in trades for o in t["orders"]
           if o.get("first_fill_ms") and o.get("sent_ms")]
    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    return {"n": len(trades), "net_inr": round(sum(nets), 2), "gross_inr": round(sum(t["gross_inr"] for t in trades), 2),
            "charges_inr": round(sum(t["charges_inr"] for t in trades), 2),
            "expectancy_inr": round(sum(nets) / len(nets), 2),
            "mean_ret_pct": round(statistics.fmean(t["ret_pct"] for t in trades), 4),
            "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 3),
            "max_drawdown_inr": round(mdd, 2), "worst_losing_streak": worst,
            "entry_slip_vs_decision_ask_pts_mean": round(statistics.fmean(slip), 4),
            "send_to_first_fill_ms_p50": statistics.median(lat) if lat else None,
            "exit_reasons": reasons}


def stress_variants(base: SimParams) -> Dict[str, SimParams]:
    """Worse-than-measured execution, for the sensitivity table (docs/scalping/05)."""
    d = asdict(base)
    return {
        "base": base,
        "latency_x3": SimParams(**{**d, "ack_latency_ms": 420, "exchange_latency_ms": 420, "cancel_latency_ms": 420}),
        "thin_book": SimParams(**{**d, "depth_take_ratio": 0.2}),
        "faults": SimParams(**{**d, "reject_prob": 0.03, "indeterminate_prob": 0.02, "drop_event_prob": 0.05,
                               "duplicate_event_prob": 0.05}),
    }
