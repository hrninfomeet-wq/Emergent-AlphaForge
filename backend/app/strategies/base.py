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
        }


class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, StrategyBase] = {}
        self._errors: Dict[str, str] = {}

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
            items.append({
                "id": plug_id,
                "name": plug_id,
                "version": "?",
                "description": "",
                "supported_instruments": [],
                "supported_modes": [],
                "supported_timeframes": [],
                "parameter_schema": {},
                "is_builtin": False,
                "is_loaded": False,
                "error": err,
            })
        return items

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
                            log.exception(f"Failed to instantiate {cls.__name__}")


_registry = StrategyRegistry()


def get_registry() -> StrategyRegistry:
    return _registry
