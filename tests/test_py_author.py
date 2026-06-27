import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import llm_client
from app.ai.py_author import author_python, AuthoredPython
from app.ai.strategy_author import Fidelity

_CANNED = AuthoredPython(
    code="from app.strategies.base import StrategyBase, Signal\n",
    fidelity=Fidelity(captured=["x"]), notes="n", suggested_id="demo",
)


def test_author_python_uses_powerful_tier_and_forwards_provider(monkeypatch):
    seen = {}
    def fake(*, tier, system, user, output_model, provider=None, max_tokens=4000):
        seen.update(tier=tier, provider=provider); return _CANNED
    monkeypatch.setattr(llm_client, "complete_structured", fake)
    out = author_python("write me a strategy", provider="gemini")
    assert seen["tier"] == llm_client.POWERFUL
    assert seen["provider"] == "gemini"
    assert out["code"].startswith("from app.strategies.base")
    assert out["suggested_id"] == "demo"


def test_system_prompt_grounded():
    from app.ai.py_author import _system_prompt
    p = _system_prompt({"indicator_columns": ["ema9", "rsi"]})
    assert "StrategyBase" in p and "evaluate" in p and "ema9" in p
    assert "is_builtin" in p
