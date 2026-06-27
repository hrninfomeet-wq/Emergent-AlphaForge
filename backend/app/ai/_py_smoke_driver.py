"""Subprocess driver: load an AI-authored strategy module, run evaluate() on a
synthetic ~2-session frame, write {ok, error, signal_repr} to argv[2]. Run via
py_sandbox.smoke_test with cwd=/app so `from app.strategies.base import ...` resolves."""
import json
import sys
import traceback
import uuid


def _result(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def main():
    code_path, result_path = sys.argv[1], sys.argv[2]
    try:
        import importlib.util
        from app.strategies.base import StrategyBase, Signal
        from app.ai.compiler import allowed_columns

        modname = f"_smoke_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(modname, code_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)

        strat_classes = [
            c for c in vars(mod).values()
            if isinstance(c, type) and issubclass(c, StrategyBase) and c is not StrategyBase
            and getattr(c, "__module__", None) == modname and getattr(c, "id", "")
        ]
        if len(strat_classes) != 1:
            return _result(result_path, {"ok": False, "error": f"expected exactly one strategy class, found {len(strat_classes)}"})
        inst = strat_classes[0]()

        import pandas as pd
        import numpy as np
        cols = sorted(allowed_columns())
        n = 120
        frame = {c: np.linspace(100, 110, n) for c in cols}
        frame["regime"] = ["TREND"] * n
        if "day_type" in cols:
            frame["day_type"] = ["TREND_DAY"] * n
        df = pd.DataFrame(frame)
        base = pd.Timestamp("2026-06-01 09:15:00")
        df["ts"] = [(base + pd.Timedelta(minutes=i)).value // 10**6 for i in range(n)]
        df["datetime"] = [(base + pd.Timedelta(minutes=i)).isoformat() for i in range(n)]
        df["ist_time"] = [(base + pd.Timedelta(minutes=i)).strftime("%H:%M") for i in range(n)]
        df["session_date"] = ["2026-06-01" if i < n // 2 else "2026-06-02" for i in range(n)]

        params = inst.merged_params(None)
        ctx = {"instrument": "NIFTY", "mode": "INTRADAY", "session_date": "2026-06-01"}
        last_repr = None
        for i in range(2, min(n, 20)):
            row, prev = df.iloc[i], df.iloc[i - 1]
            sig = inst.evaluate(row, prev, params, ctx)
            if not isinstance(sig, Signal):
                return _result(result_path, {"ok": False, "error": f"evaluate returned {type(sig).__name__}, not Signal"})
            if sig.direction not in ("CE", "PE", "NONE"):
                return _result(result_path, {"ok": False, "error": f"invalid direction {sig.direction!r}"})
            last_repr = repr(sig)
        return _result(result_path, {"ok": True, "signal_repr": last_repr})
    except Exception:
        return _result(result_path, {"ok": False, "error": "evaluate/import raised:\n" + traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
