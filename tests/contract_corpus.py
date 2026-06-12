"""Source corpus for contract tests (quality-hardening Slice C).

server.py was split into app/schemas.py + app/runtime.py + app/routers/*.py.
Contract tests that used to string-assert on server.py text now assert on the
concatenation of all backend API source files, so route/decorator pins keep
working no matter which router file a route lives in. Pure text — never
imports server.py or the routers (motor is absent on the host).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def backend_api_text() -> str:
    backend = ROOT / "backend"
    parts = [backend / "server.py", backend / "app" / "schemas.py", backend / "app" / "runtime.py"]
    parts += sorted((backend / "app" / "routers").glob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)


def warehouse_page_text() -> str:
    """DataWarehouse page + its split panel components (W4): testid/route pins
    keep working no matter which warehouse component file they live in."""
    fe = ROOT / "frontend" / "src"
    parts = [fe / "pages" / "DataWarehouse.jsx"]
    parts += sorted((fe / "components" / "warehouse").glob("*.jsx"))
    return "\n".join(q.read_text(encoding="utf-8") for q in parts)
