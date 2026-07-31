"""Stage-1 spot-data preflight: check, ingest through one pipeline, recheck."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import research  # noqa: E402
from app.schemas import BacktestReq  # noqa: E402


def _request(**overrides) -> BacktestReq:
    values = {
        "instrument": "NIFTY",
        "strategy_id": "confluence_scalper",
        "start_ts": 1_767_220_200_000,
        "end_ts": 1_767_306_599_999,
    }
    values.update(overrides)
    return BacktestReq(**values)


def _audit(*, complete: bool = False) -> dict:
    summary = {
        "instrument": "NIFTY",
        "expected_days": 2,
        "complete_days": 2 if complete else 1,
        "missing_days": 0 if complete else 1,
        "incomplete_days": 0,
        "hash_mismatch_days": 0,
        "unverified_days": 0,
        "complete": complete,
    }
    return {"summary": summary, "days": [{"date": "2026-01-01", "status": "ok"}]}


def test_check_is_read_only_and_returns_the_selected_window_audit(monkeypatch):
    calls = []

    async def audit(instrument, *, start_ts, end_ts):
        calls.append((instrument, start_ts, end_ts))
        return _audit()

    async def unexpected_fill(_req):
        raise AssertionError("a check-only preflight must not fetch data")

    monkeypatch.setattr(research, "audit_integrity", audit)
    monkeypatch.setattr(research, "_audit_and_fill_backtest_data", unexpected_fill)

    result = asyncio.run(research.backtest_spot_preflight(_request(), ingest_missing=False))

    assert calls == [("NIFTY", 1_767_220_200_000, 1_767_306_599_999)]
    assert result["before"] == result["after"]
    assert result["after"]["complete"] is False
    assert result["fill"] == {
        "attempted": False,
        "status": "skipped",
        "reason": "check_only",
    }
    assert result["days"][0]["status"] == "ok"


def test_ingest_delegates_to_the_backtest_gap_fill_and_auto_recheck_pipeline(monkeypatch):
    expected = {
        "before": _audit()["summary"],
        "after": _audit(complete=True)["summary"],
        "fill": {"attempted": True, "status": "ok", "fetched": 375},
        "days": [],
    }
    seen = []

    async def fill(req):
        seen.append(req)
        return expected

    async def unexpected_audit(*_args, **_kwargs):
        raise AssertionError("ingest must use the shared audit-fill-recheck pipeline")

    monkeypatch.setattr(research, "_audit_and_fill_backtest_data", fill)
    monkeypatch.setattr(research, "audit_integrity", unexpected_audit)
    req = _request()

    result = asyncio.run(research.backtest_spot_preflight(req, ingest_missing=True))

    assert seen == [req]
    assert result == expected
    assert result["after"]["complete"] is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"start_ts": None}, "requires both start and end dates"),
        ({"end_ts": None}, "requires both start and end dates"),
        ({"start_ts": 20, "end_ts": 10}, "start date must not be after end date"),
    ],
)
def test_preflight_rejects_an_undefined_or_reversed_window(overrides, message):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(research.backtest_spot_preflight(_request(**overrides), ingest_missing=False))

    assert exc.value.status_code == 400
    assert message in str(exc.value.detail)


def test_backtest_lab_exposes_check_ingest_and_result_without_gating_run():
    page = (ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")

    assert "spotPreflight" in api
    assert 'data-testid="spot-preflight-panel"' in page
    assert 'data-testid="spot-preflight-check"' in page
    assert 'data-testid="spot-preflight-ingest"' in page
    assert "Ingest missing & recheck" in page
    assert "disabled={running}" in page

