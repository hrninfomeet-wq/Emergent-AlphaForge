import ast, pathlib
def test_executor_has_exactly_one_place_order_call_site():
    src = pathlib.Path("backend/app/live/executor.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "place_order"]
    assert len(calls) == 1, f"executor.py must have exactly ONE place_order call site, found {len(calls)}"
