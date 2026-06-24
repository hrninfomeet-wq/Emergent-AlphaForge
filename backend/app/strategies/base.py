"""Strategy plugin base classes + registry + auto-discovery."""
from __future__ import annotations
import importlib
import pkgutil
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

log = logging.getLogger(__name__)


def _origin_from_module(module_name: str) -> str:
    """'custom' if the class/package lives under app.strategies.plugins, else 'builtin'."""
    return "custom" if module_name.startswith("app.strategies.plugins") else "builtin"


@dataclass
class Signal:
    direction: str  # "CE", "PE", or "NONE"
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    target_pct: Optional[float] = None  # target as % of entry (option mode) or pts (spot mode)
    stop_pct: Optional[float] = None
    time_stop_minutes: Optional[int] = None
    spot_target_pts: Optional[float] = None
    spot_stop_pts: Optional[float] = None
    scenario: Optional[str] = None
    spot_target_level: Optional[float] = None
    exit_mode: Optional[str] = None


class StrategyBase:
    """Inherit from this to create a plugin.
    Required class attributes: id, name, version, supported_instruments, supported_modes,
    supported_timeframes, parameter_schema.
    Override evaluate(row, prev, params, ctx) -> Signal.
    """

    id: str = ""
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    supported_instruments: List[str] = ["NIFTY", "BANKNIFTY", "SENSEX"]
    supported_modes: List[str] = ["SCALP", "INTRADAY"]
    supported_timeframes: List[str] = ["1m", "3m", "5m"]
    parameter_schema: Dict[str, Any] = {}
    is_builtin: bool = True

    def default_params(self) -> Dict[str, Any]:
        return {k: v.get("default") for k, v in self.parameter_schema.items()}

    def merged_params(self, override: Dict[str, Any] | None) -> Dict[str, Any]:
        out = self.default_params()
        if override:
            for k, v in override.items():
                if k in out:
                    out[k] = v
        return out

    def session_precompute(self, df: pd.DataFrame, params: Dict[str, Any]) -> Dict[str, Any]:
        """Optional: return per-session-date constants to merge into the per-bar
        ctx, so evaluate() can look them up O(1) instead of re-deriving them per
        bar (which is O(N) per bar -> O(N^2) per backtest). run_backtest calls
        this once before the loop and merges the result into ctx. Default: none.
        See app.strategies.session_features for reusable helpers (opening range,
        gap, ...)."""
        return {}

    def evaluate(self, row: pd.Series, prev: pd.Series, params: Dict[str, Any], ctx: Dict[str, Any]) -> Signal:
        """Override this. Return a Signal (direction='NONE' if no setup)."""
        raise NotImplementedError

    def meta(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "supported_instruments": self.supported_instruments,
            "supported_modes": self.supported_modes,
            "supported_timeframes": self.supported_timeframes,
            "parameter_schema": self.parameter_schema,
            "is_builtin": self.is_builtin,
            "origin": _origin_from_module(type(self).__module__),
        }


class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, StrategyBase] = {}
        self._errors: Dict[str, str] = {}
        self._error_pkgs: Dict[str, str] = {}

    def register(self, strategy: StrategyBase) -> None:
        if not strategy.id:
            raise ValueError("Strategy must define an id")
        self._strategies[strategy.id] = strategy
        log.info(f"Strategy registered: {strategy.id} ({strategy.name})")

    def get(self, strategy_id: str) -> Optional[StrategyBase]:
        return self._strategies.get(strategy_id)

    def list_all(self) -> List[Dict[str, Any]]:
        items = [s.meta() for s in self._strategies.values()]
        # Add failed plugins as metadata-only entries
        for plug_id, err in self._errors.items():
            pkg = self._error_pkgs.get(plug_id, "")
            items.append({
                "id": plug_id, "name": plug_id, "version": "?", "description": "",
                "supported_instruments": [], "supported_modes": [], "supported_timeframes": [],
                "parameter_schema": {}, "is_builtin": False,
                "origin": _origin_from_module(pkg),
                "is_loaded": False, "error": err,
            })
        return items

    def unregister(self, strategy_id: str) -> bool:
        return self._strategies.pop(strategy_id, None) is not None

    def origin_of(self, strategy_id: str) -> Optional[str]:
        s = self._strategies.get(strategy_id)
        if s is not None:
            return _origin_from_module(type(s).__module__)
        pkg = self._error_pkgs.get(strategy_id)
        if pkg is not None:
            return _origin_from_module(pkg)
        return None

    def reload(self) -> None:
        # Re-sync the registry with what's on disk: picks up newly added plugin
        # files and drops deleted ones. NOTE: importlib.import_module is a no-op for
        # already-imported modules, so an EDITED existing plugin won't pick up its
        # changes here — the Phase 2 authoring/edit flow must add importlib.reload.
        self._strategies.clear()
        self._errors.clear()
        self._error_pkgs.clear()
        self.auto_discover()

    def auto_discover(self) -> None:
        """Import all modules under app.strategies.builtin and app.strategies.plugins, instantiate StrategyBase subclasses."""
        for pkg_name in ("app.strategies.builtin", "app.strategies.plugins"):
            try:
                pkg = importlib.import_module(pkg_name)
            except ImportError:
                continue
            for _, modname, _ in pkgutil.iter_modules(pkg.__path__):
                full = f"{pkg_name}.{modname}"
                try:
                    mod = importlib.import_module(full)
                except Exception as e:
                    self._errors[modname] = f"import failed: {e}"
                    self._error_pkgs[modname] = pkg_name
                    log.exception(f"Failed to import strategy {full}")
                    continue
                for _, cls in inspect.getmembers(mod, inspect.isclass):
                    if cls is StrategyBase:
                        continue
                    if issubclass(cls, StrategyBase) and cls.__module__ == mod.__name__:
                        try:
                            inst = cls()
                            if inst.id:
                                self.register(inst)
                        except Exception as e:
                            self._errors[cls.__name__] = f"instantiation failed: {e}"
                            self._error_pkgs[cls.__name__] = pkg_name
                            log.exception(f"Failed to instantiate {cls.__name__}")


_registry = StrategyRegistry()


def get_registry() -> StrategyRegistry:
    return _registry
