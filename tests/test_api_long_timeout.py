"""Pin the per-request long timeout on heavy synchronous endpoints (api.js).

A large-date-range backtest (and warehouse sync / hygiene scans / audits) can run
for minutes; without a per-request override they hit axios's 60s default and the UI
shows "timeout of 60000ms exceeded". These pins ensure the long timeout stays wired
and that the GLOBAL default stays short (so ordinary calls still fail fast).
"""
from __future__ import annotations

from pathlib import Path

API = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")


def test_long_timeout_constant_and_short_global():
    assert "LONG_TIMEOUT_MS" in API
    assert "REACT_APP_API_TIMEOUT_LONG" in API  # build-time configurable
    assert "600000" in API                       # 10-min default
    assert "timeout: 60000" in API               # global default stays short


def test_heavy_endpoints_use_long_timeout():
    for ep in (
        "/backtest/run", "/backtest/option-preflight",
        "/data-hygiene/plan", "/data-hygiene/execute", "/data-hygiene/catch-up",
        "/warehouse/sync", "/warehouse/audit/", "/volatility/audit",
    ):
        idx = API.find(ep)
        assert idx != -1, f"endpoint not found: {ep}"
        # The call site (within a generous window) must pass the long timeout.
        assert "LONG_TIMEOUT_MS" in API[idx:idx + 320], f"missing long timeout on {ep}"
