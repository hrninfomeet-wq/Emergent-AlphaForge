"""_gemini.call against a FAKE google.genai (no real SDK / network)."""
import sys, types
from pathlib import Path
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


class Toy(BaseModel):
    x: int


def _install_fake_genai(monkeypatch, *, parsed=None, text=None, raise_api=False, raise_convert=False,
                        raise_fallback=False, finish_reason=None):
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")

    class APIError(Exception):
        def __init__(self, message="boom"):
            super().__init__(message); self.message = message
    errors_mod.APIError = APIError

    class _Cand:
        def __init__(self, fr): self.finish_reason = fr

    class _Resp:
        def __init__(self, parsed, text, fr):
            self.parsed = parsed; self.text = text
            self.candidates = [_Cand(fr)] if fr is not None else []

    class _Models:
        def generate_content(self, *, model, contents, config):
            if raise_convert and "response_schema" in config:
                raise ValueError("schema conversion failed")  # client-side converter error
            if raise_api:
                raise APIError("rate limit exceeded")
            # fallback call (no response_schema) returns text only
            if "response_schema" not in config:
                if raise_fallback:
                    raise RuntimeError("transport boom")
                return _Resp(None, text, finish_reason)
            return _Resp(parsed, text, finish_reason)

    class Client:
        def __init__(self, *a, **k): self.models = _Models()

    genai_mod.Client = Client
    genai_mod.errors = errors_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors_mod)


def test_gemini_success_returns_parsed(monkeypatch):
    _install_fake_genai(monkeypatch, parsed=Toy(x=7))
    from app.ai import _gemini
    out = _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert out.x == 7


def test_gemini_api_error_becomes_runtimeerror(monkeypatch):
    _install_fake_genai(monkeypatch, raise_api=True)
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert "Gemini API error" in str(ei.value)


def test_gemini_schema_convert_failure_falls_back_to_json(monkeypatch):
    # response_schema path raises a converter error; fallback uses .text + model_validate_json
    _install_fake_genai(monkeypatch, raise_convert=True, text='{"x": 5}')
    from app.ai import _gemini
    out = _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert out.x == 5


def test_gemini_shape_mismatch_gives_actionable_message(monkeypatch):
    # fallback gets WELL-FORMED but wrong-shape JSON -> not a truncation, a shape error.
    _install_fake_genai(monkeypatch, raise_convert=True, text='{"x": "not-an-int"}')
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert "didn't match" in str(ei.value)


def test_gemini_truncated_json_gives_cutoff_message(monkeypatch):
    # fallback gets JSON cut off mid-value (unbalanced braces) -> clear "cut off" message,
    # NOT a cryptic pydantic EOF. This is the exact real-world failure the user hit.
    _install_fake_genai(monkeypatch, raise_convert=True, text='{"rules": [{"id": "r1", "text": "buy when in-the-mone')
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert "cut off" in str(ei.value)


def test_gemini_max_tokens_finish_on_primary_gives_cutoff_message(monkeypatch):
    # primary path: parsed=None + finish_reason MAX_TOKENS -> clear message, no fallback loop.
    _install_fake_genai(monkeypatch, parsed=None, text=None, finish_reason="MAX_TOKENS")
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert "cut off" in str(ei.value)


def test_gemini_flash_disables_thinking(monkeypatch):
    # flash tier must spend the whole budget on output (thinking_budget 0), else thinking
    # can eat the budget and truncate the JSON.
    seen = {}

    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")
    errors_mod.APIError = type("APIError", (Exception,), {})

    class _Models:
        def generate_content(self, *, model, contents, config):
            seen.update(config)
            class R: parsed = Toy(x=1); text = None; candidates = []
            return R()

    genai_mod.Client = type("Client", (), {"__init__": lambda self, *a, **k: setattr(self, "models", _Models())})
    genai_mod.errors = errors_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors_mod)

    from app.ai import _gemini
    _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert seen.get("thinking_config") == {"thinking_budget": 0}
    assert seen.get("max_output_tokens") == _gemini.DEFAULT_MAX_TOKENS


def test_gemini_primary_text_recovery(monkeypatch):
    # primary response_schema call returns parsed=None but a usable .text -> validate it (no fallback)
    _install_fake_genai(monkeypatch, parsed=None, text='{"x": 3}')
    from app.ai import _gemini
    out = _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert out.x == 3


def test_gemini_fallback_non_api_error_becomes_runtimeerror(monkeypatch):
    # schema path rejected -> fallback call raises a NON-APIError -> must surface as RuntimeError
    _install_fake_genai(monkeypatch, raise_convert=True, raise_fallback=True)
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert "fallback request failed" in str(ei.value).lower()
