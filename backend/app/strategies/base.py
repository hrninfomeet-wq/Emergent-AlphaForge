"""Strategy plugin base classes + registry + auto-discovery."""
from __future__ import annotations
import importlib
import pkgutil
import inspect
import logging
import math
from dataclasses import dataclass, field
from numbers import Real
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


_SIGNAL_NUMERIC_FIELDS = (
    "score", "target_pct", "stop_pct", "time_stop_minutes",
    "spot_target_pts", "spot_stop_pts", "spot_target_level",
)


def validate_signal(signal: Any) -> Signal:
    """Validate the runtime output shared by backtest, smoke, paper and live.

    Type annotations do not protect a plugin at runtime. A NaN comparison can
    silently bypass thresholds while infinity can poison targets, trades, and
    saved metrics, so these are competency failures rather than research
    warnings.
    """
    if not isinstance(signal, Signal):
        raise TypeError(f"evaluate returned {type(signal).__name__}, not Signal")
    if signal.direction not in ("CE", "PE", "NONE"):
        raise ValueError(f"invalid Signal direction {signal.direction!r}")
    for field_name in _SIGNAL_NUMERIC_FIELDS:
        value = getattr(signal, field_name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"Signal field {field_name} must be numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"Signal field {field_name} must be finite")
    for field_name in ("reasons", "blockers"):
        value = getattr(signal, field_name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Signal field {field_name} must be a list of strings")
    return signal


EVAL_CTX_KEYS = ("history_df", "i", "instrument", "session_date", "mode")


def build_eval_ctx(*, history_df, i, instrument, session_date, mode="INTRADAY",
                   session_extras=None) -> Dict[str, Any]:
    """Assemble the canonical evaluate() ctx. The SAME builder is used by the
    backtest, paper/live, and smoke paths so the contract can never drift again.
    Canonical keys take precedence over `session_extras` (a strategy's
    session_precompute output cannot clobber the frame index / instrument / mode)."""
    ctx: Dict[str, Any] = dict(session_extras) if session_extras else {}
    ctx.update({
        "history_df": history_df,
        "i": int(i),
        "instrument": instrument,
        "session_date": session_date,
        "mode": mode,
    })
    return ctx


def build_live_eval_ctx(strategy: "StrategyBase", df_enriched, last_idx: int,
                        instrument: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build the canonical ctx for the single-bar paper/live path: call the
    strategy's session_precompute ONCE on the rolling window, then build_eval_ctx.
    Host-safe (no motor import) so it is unit-testable without the live module."""
    session_extras = strategy.session_precompute(df_enriched, params or {})
    last_row = df_enriched.iloc[last_idx]
    return build_eval_ctx(
        history_df=df_enriched, i=last_idx, instrument=instrument,
        session_date=str(last_row.get("session_date") or ""),
        mode=str((params or {}).get("mode") or "INTRADAY"),
        session_extras=session_extras,
    )


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
    required_features: List[str] = []
    # Paper/live evaluator history requirement. Most strategies need only the
    # latest 200 bars; session-anchored strategies may raise this (bounded by
    # the evaluator) so their live context matches the backtest context.
    live_lookback_bars: int = 200
    # Warehouse-backed columns joined AS-OF the bar ts at LOAD time, before
    # indicator enrichment (see app.data_columns). Separate from
    # `required_features` because these need I/O the pure feature registry is
    # forbidden by contract. Empty => no join runs and the frame is
    # byte-identical. Declaring a name the engine cannot supply is a clean
    # DataColumnError, not a silent NaN column.
    required_data: List[str] = []

    def default_params(self) -> Dict[str, Any]:
        return {k: v.get("default") for k, v in self.parameter_schema.items()}

    def merged_params(self, override: Dict[str, Any] | None) -> Dict[str, Any]:
        # Accept schema params PLUS the shared indicator-period keys: the
        # optimizer injects those into its search space, and dropping them here
        # made "optimize indicator periods" a silent no-op (trials, saved
        # presets, and deployments all evaluated at default periods).
        from app.indicator_groups import SHARED_INDICATOR_PARAM_KEYS
        out = self.default_params()
        if override:
            for k, v in override.items():
                if k in out or k in SHARED_INDICATOR_PARAM_KEYS:
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
            "required_features": self.required_features,
            "required_data": self.required_data,
            "live_lookback_bars": self.live_lookback_bars,
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
                    # Plugins can be edited/overwritten at runtime (authoring). A bare
                    # import_module is a no-op for an already-imported module, so drop it
                    # from sys.modules first to force a clean fresh import. NEVER do this
                    # for builtins. A failed fresh import is auto-removed by CPython.
                    # We also invalidate the .pyc bytecache so that a same-second edit
                    # (mtime unchanged) is not served from __pycache__.
                    if pkg_name == "app.strategies.plugins":
                        import sys as _sys
                        import importlib.util as _ilu
                        _sys.modules.pop(full, None)
                        try:
                            _spec = _ilu.find_spec(full)
                            if _spec and _spec.origin:
                                _pyc = importlib.util.cache_from_source(_spec.origin)
                                import os as _os
                                _os.unlink(_pyc)
                        except Exception:
                            pass  # best-effort; a missing .pyc is fine
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
