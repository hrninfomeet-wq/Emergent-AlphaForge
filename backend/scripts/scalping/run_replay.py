"""Stage-2 recorded-event replay of the pre-registered scalper specs (docs/scalping/prereg/PREREG_STAGE2_replay.md).

Usage (host venv, Mongo on 127.0.0.1:27017; read-only):
    ./.venv/Scripts/python.exe backend/scripts/scalping/run_replay.py [--days D1 D2 ...] [--out docs/scalping/results]

Writes stage2_replay.json (every trade + summary per session x scenario x spec) and prints the table.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.scalping.config import NIFTY_N1, SENSEX_S1E, with_overrides  # noqa: E402
from app.scalping.replay import _db, load_contracts, load_tape, replay_session, stress_variants  # noqa: E402
from app.scalping.market import INDEX_KEYS  # noqa: E402
from app.scalping.sim_broker import SimParams  # noqa: E402

DAYS = ["2026-09-07", "2026-09-09", "2026-09-15", "2026-09-16", "2026-09-29", "2026-10-06"]
# Engine-check only: S1-E is DTE-0-only and no SENSEX expiry day was ever recorded with depth.
S1E_ENGINE_CHECK = with_overrides(SENSEX_S1E, allowed_dte=frozenset({0, 1, 2, 3, 4}),
                                  strategy_id="s1e_engine_check_all_dte")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=DAYS)
    ap.add_argument("--out", default=str(ROOT / "docs" / "scalping" / "results"))
    args = ap.parse_args()
    db = _db()
    specs = [NIFTY_N1, SENSEX_S1E, S1E_ENGINE_CHECK]
    scen = stress_variants(SimParams())
    results = []
    for day in args.days:
        contracts = load_contracts(db, day)
        keys = set(INDEX_KEYS.values()) | {c.instrument_key for c in contracts}
        tape = load_tape(db, day, keys)
        for name, sp in scen.items():
            r = replay_session(day, specs, sim=sp, tape=tape, contracts=contracts)
            for e in r["engines"]:
                safety = {"unintended_short": any(ev["kind"] == "halt" and "short" in str(ev.get("reason"))
                                                  for ev in e["events"]),
                          "open_at_end": e["open_qty_at_end"], "halted": e["halted"]}
                results.append({"day": day, "scenario": name, "strategy_id": e["strategy_id"],
                                "summary": e["summary"], "broker_stats": e["broker_stats"], "safety": safety,
                                "trades": e["trades"],
                                "filtered": sum(1 for ev in e["events"] if ev["kind"] == "signal_filtered"),
                                "unfilled_entries": sum(1 for ev in e["events"] if ev["kind"] == "entry_unfilled")})
                s = e["summary"]
                print(f"{day} {name:11s} {e['strategy_id'][:34]:34s} n={s.get('n', 0):3d} "
                      f"net={s.get('net_inr', 0):9.2f} exp={s.get('expectancy_inr', 0):8.2f} "
                      f"win={s.get('win_rate', 0)} dd={s.get('max_drawdown_inr', 0)} "
                      f"unfilled={results[-1]['unfilled_entries']} safety={safety}", flush=True)
    agg = {}
    for (sid, name) in {(r["strategy_id"], r["scenario"]) for r in results}:
        rows = [r for r in results if r["strategy_id"] == sid and r["scenario"] == name]
        trades = [t for r in rows for t in r["trades"]]
        per_day = [r["summary"].get("net_inr", 0.0) for r in rows if r["summary"].get("n")]
        nets = [t["net_inr"] for t in trades]
        agg[f"{sid}|{name}"] = {
            "trades": len(trades), "sessions_traded": len(per_day),
            "sessions_positive": sum(1 for x in per_day if x > 0),
            "net_inr": round(sum(nets), 2),
            "expectancy_inr": round(sum(nets) / len(nets), 2) if nets else None,
            "median_trade_inr": round(statistics.median(nets), 2) if nets else None,
            "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 3) if nets else None,
            "charges_inr": round(sum(t["charges_inr"] for t in trades), 2),
            "unfilled_entries": sum(r["unfilled_entries"] for r in rows),
            "any_unintended_short": any(r["safety"]["unintended_short"] for r in rows),
            "any_open_at_end": any(r["safety"]["open_at_end"] for r in rows),
            "halts": [r["safety"]["halted"] for r in rows if r["safety"]["halted"]],
        }
    print(json.dumps(agg, indent=1))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stage2_replay.json").write_text(json.dumps({"aggregate": agg, "sessions": results}, default=str, indent=1))


if __name__ == "__main__":
    main()
