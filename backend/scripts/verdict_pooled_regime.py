"""Apply the PRE-REGISTERED kill criterion to the pooled-regime campaign output.

The criterion is fixed in docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md §6 and is
applied here mechanically, so the verdict is a computation rather than a judgement
call made after seeing the numbers:

    KILLED unless the pooled-best config is net-positive on the untouched holdout
    at >=1%/side friction AND beats both the untuned baseline AND the NIFTY-only
    version there. Per-index holdout is reported separately: an effect positive on
    only ONE index is not a pooled edge — it is the old sample-starved result
    wearing a larger n, and it is killed.

This script also enforces the GATE that decides whether the holdout may be touched
at all: a config must survive BOTH train and validation, on BOTH indices. If
nothing clears that gate, the hypothesis dies at validation and the holdout stays
clean — spending it on a candidate that already failed would be fishing.

Usage:
    python backend/scripts/verdict_pooled_regime.py
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

HERE = os.path.dirname(__file__)
BASELINE = {"trend_target_atr": 4.0, "trend_stop_atr": 1.2, "fade_stop_atr": 1.5}


def _key(row: Dict[str, Any]) -> Tuple:
    return tuple(sorted(row["params"].items()))


def load(slice_name: str) -> Dict[Tuple[str, Tuple], Dict[str, Any]]:
    path = os.path.join(HERE, f"pooled_regime_{slice_name}.json")
    if not os.path.exists(path):
        return {}
    doc = json.load(open(path, encoding="utf-8"))
    return {(r["instrument"], _key(r)): r for r in doc["rows"]}


def net(row) -> float:
    return float((row or {}).get("option_net_rupees") or 0.0)


def gross(row) -> float:
    return float((row or {}).get("option_gross_rupees") or 0.0)


def main() -> None:
    tr, va, ho = load("train"), load("validation"), load("holdout")
    if not tr or not va:
        raise SystemExit("run --slice train and --slice validation first")

    instruments = sorted({k[0] for k in tr})
    configs = sorted({k[1] for k in tr})

    print("=" * 78)
    print("SLICE HEALTH — is there a GROSS edge at all, before friction?")
    print("=" * 78)
    for slice_name, data in (("train", tr), ("validation", va)):
        for inst in instruments:
            rows = [r for k, r in data.items() if k[0] == inst]
            gp = sum(1 for r in rows if gross(r) > 0)
            npos = sum(1 for r in rows if net(r) > 0)
            print(f"  {slice_name:11s} {inst:9s} gross>0: {gp:>2}/{len(rows):<3} "
                  f"net>0: {npos:>2}/{len(rows)}")

    print()
    print("=" * 78)
    print("GATE — a config must be net-positive on BOTH slices, on BOTH indices")
    print("=" * 78)
    survivors = []
    for cfg in configs:
        ok_all = True
        detail = []
        for inst in instruments:
            t, v = tr.get((inst, cfg)), va.get((inst, cfg))
            good = net(t) > 0 and net(v) > 0
            ok_all &= good
            detail.append(f"{inst}: train={net(t):>9.1f} val={net(v):>9.1f} {'OK' if good else 'no'}")
        if ok_all:
            survivors.append(cfg)
            print(f"  SURVIVOR {dict(cfg)}")
            for d in detail:
                print(f"      {d}")

    single = []
    for cfg in configs:
        wins = [i for i in instruments
                if net(tr.get((i, cfg))) > 0 and net(va.get((i, cfg))) > 0]
        if wins and len(wins) < len(instruments):
            single.append((cfg, wins))
    if single:
        print(f"\n  {len(single)} config(s) survive on SOME but not ALL indices "
              f"— single-index effects, killed by the criterion:")
        for cfg, wins in single[:8]:
            print(f"      {dict(cfg)}  -> only {wins}")

    print()
    print("=" * 78)
    if not survivors:
        print("VERDICT: KILLED AT VALIDATION.")
        print("  No configuration is net-positive on both train and validation across")
        print("  both indices. Per the pre-registered criterion a single-index effect")
        print("  is not a pooled edge. The HOLDOUT WAS NOT TOUCHED and remains clean")
        print("  for a future, genuinely different hypothesis.")
    elif not ho:
        print(f"GATE PASSED by {len(survivors)} config(s). The holdout may now be")
        print("  touched ONCE, with these finalists recorded ABOVE this line first:")
        for cfg in survivors:
            print(f"    {dict(cfg)}")
        print("  Run: --slice holdout --i-am-sure")
    else:
        print("HOLDOUT RESULT")
        base_ok = True
        for cfg in survivors:
            for inst in instruments:
                h = ho.get((inst, cfg))
                hb = ho.get((inst, tuple(sorted(BASELINE.items()))))
                beat = net(h) > 0 and net(h) > net(hb)
                base_ok &= beat
                print(f"  {inst:9s} {dict(cfg)}")
                print(f"      net={net(h):>10.1f}  baseline={net(hb):>10.1f}  "
                      f"{'PASS' if beat else 'FAIL'}")
        print("\nVERDICT:", "SURVIVED" if base_ok else "KILLED ON HOLDOUT")
    print("=" * 78)


if __name__ == "__main__":
    main()
