import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import get_registry, StrategyRegistry, StrategyBase


#: The 11 shipped strategies that moved to plugins/ (deletable); only
#: confluence_scalper stays a permanent built-in.
MOVED_TO_PLUGINS = (
    "adaptive_regime_scalper", "explosive_reversal", "fibonacci_pullback",
    "gap_fade", "opening_range_adaptive", "opening_range_breakout",
    "opening_range_regime_router", "smc_liquidity_sweep_fvg",
    "squeeze_expansion_breakout", "vwap_mean_reversion", "vwap_pullback_scalp",
)


def test_meta_includes_origin_for_builtin():
    reg = get_registry(); reg.auto_discover()
    items = {s["id"]: s for s in reg.list_all()}
    assert items["confluence_scalper"]["origin"] == "builtin"


def test_shipped_strategies_are_plugins_except_confluence():
    """Item D: the 11 moved strategies register from plugins/ (origin=custom →
    retire+delete both possible); ids all still present; confluence_scalper is
    the only remaining true built-in (delete refuses)."""
    reg = get_registry(); reg.auto_discover()
    items = {s["id"]: s for s in reg.list_all()}
    for sid in MOVED_TO_PLUGINS:
        assert sid in items, f"{sid} vanished from the registry"
        assert items[sid]["origin"] == "custom", f"{sid} should load from plugins/"
        assert type(reg.get(sid)).__module__.startswith("app.strategies.plugins")
    assert items["confluence_scalper"]["origin"] == "builtin"
    assert reg.origin_of("confluence_scalper") == "builtin"


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
