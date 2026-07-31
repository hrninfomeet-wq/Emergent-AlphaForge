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


def run_smoke(inst, cols):
    """Build a synthetic ~2-session frame over `cols`, run the strategy's
    session_precompute + evaluate() across ~18 bars with the CANONICAL ctx.
    Returns {ok, error?, signal_repr?}. Host-importable (no /app cwd needed)."""
    import pandas as pd
    import numpy as np
    from dataclasses import asdict
    from app.strategies.base import build_eval_ctx, validate_signal

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

    def evaluate_pass():
        session_extras = inst.session_precompute(df, params)  # may raise -> caught by main()
        signals = []
        last_repr = None
        for i in range(2, min(n, 20)):
            row, prev = df.iloc[i], df.iloc[i - 1]
            ctx = build_eval_ctx(
                history_df=df, i=i, instrument="NIFTY",
                session_date=str(df.iloc[i].get("session_date") or ""),
                mode="INTRADAY", session_extras=session_extras,
            )
            sig = validate_signal(inst.evaluate(row, prev, params, ctx))
            signals.append(asdict(sig))
            last_repr = repr(sig)
        return signals, last_repr

    first, last_repr = evaluate_pass()
    second, _ = evaluate_pass()
    if first != second:
        return {
            "ok": False,
            "error": "strategy evaluate/session_precompute is not deterministic for identical inputs",
        }
    return {"ok": True, "signal_repr": last_repr}


def main():
    code_path, result_path = sys.argv[1], sys.argv[2]
    try:
        import importlib.util
        from app.strategies.base import StrategyBase
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

        # required_data MUST be threaded too: without it the smoke frame lacks the
        # declared warehouse column and a strategy that reads row["vix"] — the exact
        # use case required_data exists for — is REJECTED at install with KeyError.
        cols = sorted(allowed_columns(getattr(inst, "required_features", ()),
                                      getattr(inst, "required_data", ())))
        return _result(result_path, run_smoke(inst, cols))
    except Exception:
        return _result(result_path, {"ok": False, "error": "evaluate/import raised:\n" + traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
