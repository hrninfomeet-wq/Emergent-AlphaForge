"""Provider router unit tests — no SDK, no network. Mocks the per-provider seams."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import llm_client


def _clear(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER",
              "ANTHROPIC_FAST_MODEL", "ANTHROPIC_POWERFUL_MODEL",
              "GEMINI_FAST_MODEL", "GEMINI_POWERFUL_MODEL"):
        monkeypatch.delenv(v, raising=False)


def test_model_for_defaults_and_env_override(monkeypatch):
    _clear(monkeypatch)
    assert llm_client.model_for("anthropic", llm_client.FAST) == "claude-sonnet-4-6"
    assert llm_client.model_for("gemini", llm_client.POWERFUL) == "gemini-2.5-pro"
    monkeypatch.setenv("GEMINI_FAST_MODEL", "gemini-3.0-flash")
    assert llm_client.model_for("gemini", llm_client.FAST) == "gemini-3.0-flash"


def test_resolve_explicit_wins_when_configured(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert llm_client.resolve_provider("anthropic") == "anthropic"  # explicit beats env


def test_resolve_env_then_single_then_raise(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")  # only anthropic
    assert llm_client.resolve_provider(None) == "anthropic"  # single configured
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert llm_client.resolve_provider(None) == "gemini"      # env wins
    _clear(monkeypatch)
    with pytest.raises(RuntimeError):
        llm_client.resolve_provider(None)                     # none configured


def test_resolve_selected_but_unconfigured_raises(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    with pytest.raises(RuntimeError):
        llm_client.resolve_provider("anthropic")              # named, no key


def test_providers_status_shape(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    st = llm_client.providers_status()
    ids = {p["id"]: p for p in st["providers"]}
    assert ids["gemini"]["configured"] is True
    assert ids["anthropic"]["configured"] is False
    assert ids["gemini"]["label"] == "Google Gemini"
    assert st["active"] == "gemini"
    _clear(monkeypatch)
    assert llm_client.providers_status()["active"] is None


def test_complete_structured_routes_to_resolved_backend(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    seen = {}
    from app.ai import _gemini
    def fake_call(*, model, system, user, output_model, max_tokens):
        seen.update(model=model, system=system); return "OK"
    monkeypatch.setattr(_gemini, "call", fake_call)
    out = llm_client.complete_structured(tier=llm_client.FAST, system="s", user="u", output_model=str)
    assert out == "OK"
    assert seen["model"] == "gemini-2.5-flash"
