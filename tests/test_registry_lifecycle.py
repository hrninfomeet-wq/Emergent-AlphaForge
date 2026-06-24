import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import get_registry, StrategyRegistry, StrategyBase


def test_meta_includes_origin_for_builtin():
    reg = get_registry(); reg.auto_discover()
    items = {s["id"]: s for s in reg.list_all()}
    assert items["confluence_scalper"]["origin"] == "builtin"


def test_unregister_removes_strategy():
    reg = get_registry(); reg.auto_discover()
    assert reg.get("confluence_scalper") is not None
    assert reg.unregister("confluence_scalper") is True
    assert reg.get("confluence_scalper") is None
    assert reg.unregister("confluence_scalper") is False  # idempotent
    reg.reload()  # restore for other tests
    assert reg.get("confluence_scalper") is not None


def test_origin_of_unknown_is_none():
    reg = StrategyRegistry()
    assert reg.origin_of("nope") is None


def test_reload_repopulates():
    reg = get_registry(); reg.auto_discover()
    n = len(reg.list_all())
    reg.unregister("vwap_mean_reversion")
    reg.reload()
    assert len(reg.list_all()) == n
