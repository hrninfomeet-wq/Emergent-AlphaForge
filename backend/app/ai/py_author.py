"""Map a free-text/transcript description to an arbitrary StrategyBase python module
via the POWERFUL tier. The output is gated by py_sandbox before install."""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel

from app.ai.strategy_author import Fidelity


class AuthoredPython(BaseModel):
    code: str
    fidelity: Fidelity
    notes: str = ""
    suggested_id: str = ""


def _system_prompt(catalog: Dict[str, Any]) -> str:
    cols = ", ".join(sorted(catalog["indicator_columns"]) + ["open", "high", "low", "close", "volume"])
    return f"""You write ONE complete Python module defining EXACTLY ONE StrategyBase subclass for an \
Indian-index option-BUYING intraday backtester. Output only valid python in `code`.

# Hard structural rules (the module is statically validated; ANY violation is rejected)
- The FIRST line of code must be: from __future__ import annotations
- Module top level may contain ONLY: that __future__ import; imports of pandas/numpy/math/typing/dataclasses; \
`from app.strategies.base import StrategyBase, Signal` (ONLY those two names); and the single class. \
NO other top-level statements (no module-level assignments, prints, calls).
- The class inherits ONLY from StrategyBase, has NO decorators and NO metaclass. Methods have NO decorators.
- Class variables AND method default-argument values must be plain literals (numbers, strings, lists, dicts) — \
NO function calls or comprehensions anywhere a value is assigned at class-definition time.
- Do NOT access ANY attribute whose name starts with an underscore (e.g. _libs, __class__, _mgr).
- Do NOT use os/sys/subprocess/eval/exec/open/input/getattr/setattr/type()/__import__, pandas/numpy SUBMODULES \
(pandas.io, numpy.f2py, numpy.ctypeslib), or ANY file/network I/O: no pandas readers/writers (read_csv, read_pickle, \
to_csv, to_pickle, ...), no DataFrame.eval/.query, no numpy.load/.save/.fromfile/.tofile/.memmap. A strategy is a \
PURE in-memory function: index row/prev with row["col"], use arithmetic/comparisons, numpy ufuncs (np.where, np.abs, \
np.sign, np.maximum, np.minimum), pandas Series math (.mean()/.std()/.shift()/.rolling()/.ewm()/.diff()), and math.
- evaluate(self, row, prev, params, ctx) must be a PURE function returning a Signal.

# Required class attributes
id (lowercase slug ^[a-z][a-z0-9_]*$, a STRING LITERAL), name, version="1.0.0", description, \
is_builtin = False, supported_instruments, supported_modes (["SCALP","INTRADAY"]), \
supported_timeframes, parameter_schema (dict literal {{name: {{type,min,max,default}}}}).

# Data available on row/prev (reference ONLY these column names)
{cols}
row/prev are pandas Series; index with row["close"] etc. and wrap in float(...). `params` is a \
dict of your parameter_schema defaults; `ctx` is a dict (may be empty). A SCALP/INTRADAY strategy \
should set at least one exit on the Signal (spot_target_pts/spot_stop_pts/target_pct/stop_pct/time_stop_minutes).

# Signal(direction=..., ...) fields
direction ("CE" buy-call / "PE" buy-put / "NONE"), score, reasons, blockers, target_pct, stop_pct, \
time_stop_minutes, spot_target_pts, spot_stop_pts.

# fidelity (be honest): captured (what you encoded), couldnt_map (rules with no column/representation), \
ambiguous (needs clarification). suggested_id: the id slug you chose."""


def author_python(source_text: str, provider: str | None = None) -> Dict[str, Any]:
    from app.ai.grounding import build_grounding_catalog
    from app.ai import llm_client

    catalog = build_grounding_catalog()
    out: AuthoredPython = llm_client.complete_structured(
        tier=llm_client.POWERFUL,
        system=_system_prompt(catalog),
        user=source_text,
        output_model=AuthoredPython,
        provider=provider,
        max_tokens=8000,
    )
    return {"code": out.code, "fidelity": out.fidelity.model_dump(),
            "notes": out.notes, "suggested_id": out.suggested_id}
