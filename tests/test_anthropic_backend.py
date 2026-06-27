"""_anthropic.call against a FAKE anthropic module (no real SDK / network)."""
import sys, types
from pathlib import Path
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


class Toy(BaseModel):
    x: int


def _install_fake_anthropic(monkeypatch, *, parsed_output=None, raise_api=False):
    mod = types.ModuleType("anthropic")

    class APIError(Exception):
        def __init__(self, message="boom"):
            super().__init__(message); self.message = message
    mod.APIError = APIError

    class _Resp:
        def __init__(self, parsed_output): self.parsed_output = parsed_output

    class _Messages:
        def parse(self, *, model, max_tokens, system, messages, output_format):
            if raise_api:
                raise APIError("credit balance too low")
            return _Resp(parsed_output)

    class Anthropic:
        def __init__(self, *a, **k): self.messages = _Messages()

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)


def test_anthropic_success_returns_parsed_output(monkeypatch):
    _install_fake_anthropic(monkeypatch, parsed_output=Toy(x=9))
    from app.ai import _anthropic
    out = _anthropic.call(model="claude-sonnet-4-6", system="s", user="u", output_model=Toy)
    assert out.x == 9


def test_anthropic_api_error_becomes_runtimeerror(monkeypatch):
    _install_fake_anthropic(monkeypatch, raise_api=True)
    from app.ai import _anthropic
    with pytest.raises(RuntimeError) as ei:
        _anthropic.call(model="m", system="s", user="u", output_model=Toy)
    assert "Anthropic API error" in str(ei.value)


def test_anthropic_none_parsed_output_raises(monkeypatch):
    _install_fake_anthropic(monkeypatch, parsed_output=None)
    from app.ai import _anthropic
    with pytest.raises(RuntimeError) as ei:
        _anthropic.call(model="m", system="s", user="u", output_model=Toy)
    assert "no parseable output" in str(ei.value).lower()
