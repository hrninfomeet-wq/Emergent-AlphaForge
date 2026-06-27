"""Pure-AST safety checks for AI-authored full-Python strategies (no execution)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.py_sandbox import static_check

VALID = '''
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class MyStrat(StrategyBase):
    id = "my_strat"
    name = "My Strat"
    is_builtin = False
    parameter_schema = {"thr": {"type": "float", "default": 1.0}}

    def evaluate(self, row, prev, params, ctx) -> Signal:
        if float(row["close"]) > float(row["ema9"]):
            return Signal(direction="CE", spot_target_pts=30, spot_stop_pts=15)
        return Signal(direction="NONE")
'''


def test_valid_module_passes():
    assert static_check(VALID) == []


def test_syntax_error():
    assert static_check("def (:") and "syntax error" in static_check("def (:")[0]


def _has(errs, frag):
    return any(frag in e for e in errs)


def test_forbidden_import_os():
    code = VALID.replace("import pandas as pd", "import pandas as pd\nimport os")
    assert _has(static_check(code), "os")


def test_relative_import_rejected():
    code = VALID.replace("from app.strategies.base", "from .base")
    assert _has(static_check(code), "relative") or _has(static_check(code), "not allowed")


def test_app_import_must_be_base():
    code = VALID.replace("from app.strategies.base import StrategyBase, Signal",
                         "from app.db import get_db\nfrom app.strategies.base import StrategyBase, Signal")
    assert _has(static_check(code), "app.strategies.base")


def test_module_level_statement_rejected():
    code = VALID + "\nprint('hi')\n"
    assert _has(static_check(code), "top-level")


def test_module_level_assign_rejected():
    code = VALID.replace("import pandas as pd", "import pandas as pd\nLOADED = True")
    assert _has(static_check(code), "top-level")


def test_class_decorator_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "@staticmethod\nclass MyStrat(StrategyBase):")
    assert static_check(code) != []


def test_metaclass_kw_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "class MyStrat(StrategyBase, metaclass=type):")
    assert _has(static_check(code), "keyword") or _has(static_check(code), "metaclass")


def test_multiple_bases_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "class MyStrat(StrategyBase, dict):")
    assert _has(static_check(code), "ONLY") or _has(static_check(code), "StrategyBase")


def test_dunder_attr_rejected():
    code = VALID.replace("return Signal(direction=\"NONE\")",
                         "return Signal(direction=type(self).__mro__[1].__name__)")
    assert _has(static_check(code), "dunder") or _has(static_check(code), "__mro__")


def test_reexport_os_walk_rejected():
    code = VALID.replace("return Signal(direction=\"NONE\")",
                         "pd.io.common.os.system('echo x'); return Signal(direction=\"NONE\")")
    assert _has(static_check(code), "io") or _has(static_check(code), "os")


def test_numpy_f2py_walk_rejected():
    code = (VALID.replace("import pandas as pd", "import pandas as pd\nimport numpy as np")
            .replace("return Signal(direction=\"NONE\")",
                     "np.f2py.os.getpid(); return Signal(direction=\"NONE\")"))
    assert _has(static_check(code), "f2py") or _has(static_check(code), "os")


def test_forbidden_calls():
    for name in ("eval", "exec", "open", "getattr", "type", "__import__"):
        codecall = VALID.replace("return Signal(direction=\"NONE\")",
                                 f"{name}('x'); return Signal(direction=\"NONE\")")
        assert _has(static_check(codecall), name), name


def test_method_level_import_rejected():
    code = VALID.replace("        if float(row",
                         "        import os\n        if float(row")
    assert _has(static_check(code), "import")


def test_zero_strategy_classes_rejected():
    code = VALID.replace("(StrategyBase)", "(object)")
    assert _has(static_check(code), "StrategyBase")


def test_two_strategy_classes_rejected():
    code = VALID + "\n\nclass Other(StrategyBase):\n    id = \"other\"\n    def evaluate(self, row, prev, params, ctx):\n        return Signal(direction=\"NONE\")\n"
    assert _has(static_check(code), "exactly one")


def test_missing_evaluate_rejected():
    code = VALID.replace("""    def evaluate(self, row, prev, params, ctx) -> Signal:
        if float(row["close"]) > float(row["ema9"]):
            return Signal(direction="CE", spot_target_pts=30, spot_stop_pts=15)
        return Signal(direction="NONE")
""", "    pass\n")
    assert _has(static_check(code), "evaluate")


def test_class_var_call_rejected():
    code = VALID.replace('    name = "My Strat"', '    name = pd.Timestamp.now()')
    assert _has(static_check(code), "literal")


def test_class_var_comprehension_rejected():
    code = VALID.replace('    name = "My Strat"', '    name = [i for i in range(3)]')
    assert _has(static_check(code), "literal")


def test_valid_literals_still_pass():
    # dict/list literals and negative-number unary ops are fine
    code = VALID.replace('    name = "My Strat"',
                         '    name = "My Strat"\n    levels = [-1, 0, 1]\n    cfg = {"a": 1, "b": [2, 3]}')
    assert static_check(code) == []


def test_app_import_extra_name_rejected():
    code = VALID.replace("from app.strategies.base import StrategyBase, Signal",
                         "from app.strategies.base import StrategyBase, Signal, get_registry")
    assert _has(static_check(code), "StrategyBase or Signal")


def test_app_import_star_rejected():
    code = VALID.replace("from app.strategies.base import StrategyBase, Signal",
                         "from app.strategies.base import *")
    assert _has(static_check(code), "StrategyBase or Signal") or _has(static_check(code), "*")


from app.ai.py_sandbox import extract_strategy_id


def test_extract_id_literal():
    assert extract_strategy_id(VALID) == "my_strat"


def test_extract_id_nonliteral_is_none():
    code = VALID.replace('id = "my_strat"', "id = SLUG")
    assert extract_strategy_id(code) is None


def test_extract_id_missing_is_none():
    code = VALID.replace('    id = "my_strat"\n', "")
    assert extract_strategy_id(code) is None


def test_extract_id_bad_slug_is_none():
    code = VALID.replace('id = "my_strat"', 'id = "Bad-ID"')
    assert extract_strategy_id(code) is None


from app.ai.py_sandbox import _interpret_smoke_result


def test_smoke_result_timeout():
    r = _interpret_smoke_result(returncode=None, stdout="", stderr="", timed_out=True, result=None)
    assert r["ok"] is False and "timeout" in r["error"].lower()


def test_smoke_result_nonzero_exit():
    r = _interpret_smoke_result(returncode=1, stdout="", stderr="boom traceback", timed_out=False, result=None)
    assert r["ok"] is False and "boom" in r["error"]


def test_smoke_result_missing_result_file():
    r = _interpret_smoke_result(returncode=0, stdout="noise", stderr="", timed_out=False, result=None)
    assert r["ok"] is False and "no result" in r["error"].lower()


def test_smoke_result_driver_failed():
    r = _interpret_smoke_result(returncode=0, stdout="", stderr="", timed_out=False,
                                result={"ok": False, "error": "evaluate raised: KeyError"})
    assert r["ok"] is False and "evaluate raised" in r["error"]


def test_smoke_result_ok():
    r = _interpret_smoke_result(returncode=0, stdout="", stderr="", timed_out=False,
                                result={"ok": True, "signal_repr": "Signal(direction='NONE')"})
    assert r["ok"] is True and "NONE" in r["signal_repr"]


def test_smoke_test_reads_result_file(monkeypatch, tmp_path):
    from app.ai import py_sandbox
    import json

    class _FakeProc:
        returncode = 0
        def communicate(self, timeout=None):
            return ("", "")
        def kill(self): pass

    def fake_popen(cmd, **kw):
        # cmd = [python, driver, code_path, result_path, timeout]; write the result file
        Path(cmd[3]).write_text(json.dumps({"ok": True, "signal_repr": "Signal(NONE)"}))
        return _FakeProc()

    monkeypatch.setattr(py_sandbox.subprocess, "Popen", fake_popen)
    out = py_sandbox.smoke_test("ignored code", timeout=5)
    assert out["ok"] is True and "NONE" in (out["signal_repr"] or "")


def test_smoke_test_missing_result_is_failure(monkeypatch, tmp_path):
    from app.ai import py_sandbox

    class _FakeProc:
        returncode = 0
        def communicate(self, timeout=None):
            return ("some stdout", "")
        def kill(self): pass

    monkeypatch.setattr(py_sandbox.subprocess, "Popen", lambda cmd, **kw: _FakeProc())
    out = py_sandbox.smoke_test("ignored", timeout=5)  # nobody wrote the result file
    assert out["ok"] is False


# --- red-team evasion regression battery (all must be REJECTED) ---
def _strat(body_lines, *, future=True, sig="self, row, prev, params, ctx"):
    head = "from __future__ import annotations\n" if future else ""
    body = "\n".join("        " + l for l in body_lines)
    return (head +
            "import pandas as pd\nimport numpy as np\n"
            "from app.strategies.base import StrategyBase, Signal\n\n\n"
            "class E(StrategyBase):\n    id = \"e\"\n    is_builtin = False\n"
            f"    def evaluate({sig}):\n{body}\n")


def test_evasion_private_reexport_os():
    code = _strat(['osmod = pd._testing.threading._os', 'osmod.system("x")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_builtins_via_globals():
    code = _strat(['blt = np._globals.enum.bltns', 'blt.eval("1")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_attribute_eval_call():
    code = _strat(['x = pd.eval("1+1")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_df_query():
    code = _strat(['x = row.query("close > 1")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_read_csv_file():
    code = _strat(['x = pd.read_csv("/etc/passwd")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_read_pickle():
    code = _strat(['x = pd.read_pickle("http://a/p.pkl")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_np_load_pickle():
    code = _strat(['x = np.load("/tmp/x.npy", allow_pickle=True)', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_np_fromfile():
    code = _strat(['x = np.fromfile("/etc/passwd")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_to_pickle_write():
    code = _strat(['row.to_pickle("/tmp/owned.pkl")', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_evasion_default_arg_execution():
    code = _strat(['return Signal(direction="NONE")'], sig='self, row, prev, params, ctx, _x=pd.read_pickle("http://a/p.pkl")')
    assert static_check(code) != []


def test_evasion_kwonly_default_execution():
    code = _strat(['return Signal(direction="NONE")'], sig='self, row, prev, params, ctx, *, _x=np.load("/tmp/e.npy", allow_pickle=True)')
    assert static_check(code) != []


def test_evasion_eager_annotation_when_no_future():
    # without __future__ annotations, a param annotation Call executes at def time
    code = _strat(['return Signal(direction="NONE")'], future=False, sig='self, row, prev=None, params=None, ctx=None, x: pd.read_csv("/etc/passwd")=None')
    assert static_check(code) != []


def test_evasion_missing_future_rejected():
    code = _strat(['return Signal(direction="NONE")'], future=False)
    assert static_check(code) != []


def test_underscore_attribute_rejected():
    code = _strat(['x = pd._libs', 'return Signal(direction="NONE")'])
    assert static_check(code) != []


def test_valid_still_passes_after_hardening():
    # legitimate strategy ops must still pass: arithmetic, np.where, .mean(), .shift(), to_numpy()
    code = _strat([
        'c = float(row["close"]); e = float(row["ema9"])',
        'arr = np.where(c > e, 1, 0)',
        'm = prev.get("rsi")',
        'return Signal(direction="CE" if c > e else "NONE", spot_target_pts=30, spot_stop_pts=15)',
    ])
    assert static_check(code) == []
