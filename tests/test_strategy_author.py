"""Tests for the AI spec-mapper (Phase 2B): free-text -> StrategySpec + fidelity.

The LLM call is behind a patchable seam (app.ai.llm_client.complete_structured),
so these tests NEVER import anthropic or hit the real API — they patch the seam
to return a canned MappedSpec. The grounding catalog + validate_spec keep the
mapper honest: a bad AI spec surfaces as `errors`, not a crash.
"""
import sys
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import llm_client
from app.ai.strategy_author import map_source_to_spec, MappedSpec, Fidelity
from app.ai.spec_schema import StrategySpec
import app.routers.strategies_admin as sa


# ---------------------------------------------------------------------------
# A canned, VALID MappedSpec the seam returns instead of calling Claude.
# ---------------------------------------------------------------------------

_CANNED = MappedSpec(
    spec=StrategySpec(
        id="ai_demo",
        name="AI Demo",
        entry_ce=[{"left": "close", "op": ">", "right": "ema9"}],
        exits={"spot_target_pts": 30, "spot_stop_pts": 15},
    ),
    fidelity=Fidelity(
        captured=["buy calls when price > EMA9"],
        couldnt_map=[],
        ambiguous=[],
    ),
)


# ---------------------------------------------------------------------------
# 1. map_source_to_spec returns {spec, fidelity, errors}; canned spec is valid
# ---------------------------------------------------------------------------

def test_map_source_returns_spec_and_fidelity():
    with patch.object(llm_client, "complete_structured", return_value=_CANNED):
        out = map_source_to_spec("buy calls when price is above the 9 EMA, target 30 stop 15")
    assert out["spec"]["id"] == "ai_demo"
    assert out["errors"] == []  # the canned spec is valid
    assert "buy calls" in out["fidelity"]["captured"][0]


# ---------------------------------------------------------------------------
# 2. A bad AI spec (unknown column) surfaces as validation errors, not a crash
# ---------------------------------------------------------------------------

def test_map_source_reports_validation_errors_for_bad_ai_spec():
    bad = MappedSpec(
        spec=StrategySpec(
            id="bad",
            name="Bad",
            entry_ce=[{"left": "not_a_column", "op": ">", "right": 1}],
            exits={"spot_target_pts": 10},
        ),
        fidelity=Fidelity(),
    )
    with patch.object(llm_client, "complete_structured", return_value=bad):
        out = map_source_to_spec("...")
    assert any("not_a_column" in e or "column" in e for e in out["errors"])


# ---------------------------------------------------------------------------
# 3. complete_structured raises without an API key (no anthropic import path)
# ---------------------------------------------------------------------------

def test_complete_structured_raises_when_no_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert llm_client.any_configured() is False
    import pytest
    with pytest.raises(RuntimeError):
        llm_client.complete_structured(tier=llm_client.FAST, system="s", user="u", output_model=MappedSpec)


# ---------------------------------------------------------------------------
# 4. The system prompt is grounded: it lists real columns + the op vocabulary
# ---------------------------------------------------------------------------

def test_system_prompt_is_grounded():
    from app.ai.strategy_author import _system_prompt
    catalog = {"indicator_columns": ["ema9", "rsi", "regime"]}
    prompt = _system_prompt(catalog)
    assert "ema9" in prompt and "close" in prompt   # indicator + raw OHLCV
    assert "cross_above" in prompt                  # op vocabulary
    assert "spot_target_pts" in prompt              # exit vocabulary
    assert "entry_ce" in prompt and "entry_pe" in prompt
    assert "couldnt_map" in prompt or "could not" in prompt.lower()  # fidelity honesty


# ---------------------------------------------------------------------------
# Router tests for POST /strategies/author/from-source
# ---------------------------------------------------------------------------

def _make_app():
    app = FastAPI()
    app.include_router(sa.api)
    return TestClient(app, raise_server_exceptions=True)


def test_from_source_200(monkeypatch):
    tc = _make_app()
    canned = {
        "spec": _CANNED.spec.model_dump(),
        "fidelity": _CANNED.fidelity.model_dump(),
        "errors": [],
    }
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.strategy_author.map_source_to_spec", return_value=canned):
        r = tc.post("/strategies/author/from-source", json={"source": "buy calls above ema9"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"]["id"] == "ai_demo"
    assert "captured" in body["fidelity"]
    assert body["errors"] == []


def test_from_source_503_when_not_configured():
    tc = _make_app()
    with patch("app.ai.llm_client.any_configured", return_value=False):
        r = tc.post("/strategies/author/from-source", json={"source": "anything"})
    assert r.status_code == 503, r.text
    assert "configured" in r.json()["detail"].lower()


def test_from_source_400_empty_source():
    tc = _make_app()
    with patch("app.ai.llm_client.any_configured", return_value=True):
        r = tc.post("/strategies/author/from-source", json={"source": "   "})
    assert r.status_code == 400, r.text
    assert "empty" in r.json()["detail"].lower()


def test_from_source_502_on_runtime_error():
    tc = _make_app()
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.strategy_author.map_source_to_spec",
               side_effect=RuntimeError("AI returned no parseable output")):
        r = tc.post("/strategies/author/from-source", json={"source": "buy calls"})
    assert r.status_code == 502, r.text
    assert "ai mapping failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. any_configured returns True when at least one provider key is set
# ---------------------------------------------------------------------------

def test_any_configured_true_with_either_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert llm_client.any_configured() is True


# ---------------------------------------------------------------------------
# 6. map_source_to_spec forwards the optional provider to complete_structured
# ---------------------------------------------------------------------------

def test_map_source_forwards_provider(monkeypatch):
    captured = {}
    def fake(*, tier, system, user, output_model, provider=None, max_tokens=4000):
        captured.update(tier=tier, provider=provider); return _CANNED
    monkeypatch.setattr(llm_client, "complete_structured", fake)
    map_source_to_spec("buy calls above ema9", provider="gemini")
    assert captured["provider"] == "gemini"
    assert captured["tier"] == llm_client.FAST
