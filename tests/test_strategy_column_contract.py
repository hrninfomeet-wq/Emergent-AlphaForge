"""Contract tests that kill PHANTOM-COLUMN and DEAD-KNOB defects at the source.

Two independent nets over every registered strategy:

1. ``test_strategies_read_only_columns_the_engine_builds`` — every column literal
   a strategy reads off the per-bar row must exist in the frame the engine
   actually assembles. **This is the net that would have caught the
   ``vix_boost_threshold`` dead knob**: ``explosive_reversal`` read
   ``row.get("vix")``, and no engine path has ever produced a ``vix`` column
   (``indicators.precompute_all_indicators`` and ``indicator_groups`` never
   create it, and the ``app/features`` registry is pure-pandas by contract so it
   cannot fetch VIX from the warehouse). The booster branch could therefore never
   fire, which made its threshold parameter tunable-but-inert: it consumed
   optimizer search budget and was persisted into saved presets while changing
   nothing. Same defect class as the ``early_stop`` no-op
   (docs/OPTIMIZER_VERDICT_2026-07.md).

2. ``test_every_declared_parameter_is_reachable`` — every key in a strategy's
   ``parameter_schema`` must be read by *someone*. Note this second net would NOT
   have caught the vix knob: that knob **was** read, by a branch that could never
   fire. That asymmetry is exactly why net 1 exists. Net 2 guards the sibling
   defect — a knob nothing anywhere references.

Feature columns are allowed ONLY to a strategy that declares the owning feature
in ``required_features``, so "reads an SMC column without declaring the feature"
fails too (it would be a NaN column at runtime, since ``materialize_features``
only computes declared groups).

Pure + host-safe: no motor, no DB. The column universe is built by calling the
REAL ``precompute_all_indicators`` on a synthetic frame (via
``app.ai.grounding.build_grounding_catalog``), so this can never drift from what
the engine computes.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.grounding import build_grounding_catalog          # noqa: E402
from app.data_columns import DATA_COLUMN_REGISTRY            # noqa: E402
from app.features.registry import FEATURE_REGISTRY            # noqa: E402
from app.indicator_groups import SHARED_INDICATOR_PARAM_KEYS  # noqa: E402
from app.strategies.base import StrategyBase, get_registry    # noqa: E402

BACKEND = ROOT / "backend"

# Raw warehouse/derived columns present on every frame before any indicator runs
# (mirrors app.ai.grounding._RAW_COLS, which the catalog subtracts out).
RAW_COLUMNS = {"ts", "datetime", "open", "high", "low", "close", "volume",
               "dt", "session_date", "ist_time"}

_CATALOG = build_grounding_catalog()
# Everything precompute_all_indicators + classify_regime_series put on the frame.
ENGINE_COLUMNS = set(_CATALOG["indicator_columns"]) | RAW_COLUMNS

# Per-bar helper modules that read the row on a strategy's behalf. Scanned with
# the same rule so a phantom column cannot hide one call deep.
SHARED_ROW_HELPERS = ("app/context_signals.py",)

# Params consumed by run_backtest itself rather than by any strategy's evaluate()
# (asserted against backtest.py's real source below, so this list cannot rot).
ENGINE_CONSUMED_PARAMS = frozenset({
    "cooldown_bars", "spot_target_pts", "spot_stop_pts", "signal_threshold", "mode",
})

# Registration-only strategies whose params are consumed by a DIFFERENT engine.
# `premium_momentum.evaluate` is deliberately inert — the real per-bar logic runs
# in the deployment evaluator's Track B branch. Self-verifying: each param is
# required to actually appear in one of the named modules, so an entry here can
# never become a rubber stamp for a genuinely dead knob.
EXTERNALLY_CONSUMED_PARAMS = {
    "premium_momentum": (
        "app/premium_momentum_live.py",
        "app/premium_momentum_backtest.py",
        "app/premium_trigger_config.py",
        "app/premium_momentum_tuner.py",
        "app/deployment_evaluator.py",
        "app/auto_live.py",
    ),
}


# --------------------------------------------------------------------------
# source resolution
# --------------------------------------------------------------------------

def _strategy_modules(strategy: StrategyBase):
    """The strategy's own module plus every in-repo ancestor module (adaptive_base,
    scenario_routing_base, ...). Returns [(dotted_name, Path), ...]."""
    out, seen = [], set()
    for cls in type(strategy).__mro__:
        mod = getattr(cls, "__module__", "") or ""
        if not mod.startswith("app.") or mod in seen:
            continue
        path = BACKEND / (mod.replace(".", "/") + ".py")
        if path.exists():
            seen.add(mod)
            out.append((mod, path))
    return out


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# net 1 — column reads
# --------------------------------------------------------------------------

class _RowColumnScanner(ast.NodeVisitor):
    """Collect ``<row>["col"]`` and ``<row>.get("col")`` literals, where <row> is
    a per-bar row variable (the evaluate() signature's row/prev arguments)."""

    def __init__(self, row_names):
        self.row_names = row_names
        self.hits = []      # [(column, lineno)]

    def visit_Subscript(self, node):
        if isinstance(node.value, ast.Name) and node.value.id in self.row_names:
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                self.hits.append((sl.value, node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node):
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "get"
                and isinstance(fn.value, ast.Name) and fn.value.id in self.row_names
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            self.hits.append((node.args[0].value, node.lineno))
        self.generic_visit(node)


def _row_variable_names(tree: ast.Module) -> set:
    """Conventional names plus the actual row/prev argument names of any
    ``evaluate``/``_route`` definition, so a plugin that calls its row ``bar``
    is still covered."""
    names = {"row", "prev"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("evaluate", "_route"):
            args = [a.arg for a in node.args.args if a.arg != "self"]
            names.update(args[:2])
    return names


def _column_reads(path: Path):
    tree = _parse(path)
    scanner = _RowColumnScanner(_row_variable_names(tree))
    scanner.visit(tree)
    return scanner.hits


def _allowed_columns_for(strategy: StrategyBase) -> set:
    allowed = set(ENGINE_COLUMNS)
    for name in getattr(strategy, "required_features", ()) or ():
        group = FEATURE_REGISTRY.get(name)
        if group is not None:
            allowed.update(group.columns)
    # Warehouse-backed load-time columns are legal ONLY to a strategy that
    # declares them — declaring is what makes the engine actually join them
    # (app.warehouse.attach_required_data). An undeclared read stays a hard
    # failure, which is what caught the original dead `vix` read.
    for name in getattr(strategy, "required_data", ()) or ():
        spec = DATA_COLUMN_REGISTRY.get(name)
        if spec is not None:
            allowed.add(spec.column)
    return allowed


def _registered_strategies():
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    return [s for s in reg._strategies.values()]


def test_registry_discovers_strategies():
    """Guard the guards: an empty registry would make both nets vacuously pass."""
    assert len(_registered_strategies()) >= 10


def test_strategies_read_only_columns_the_engine_builds():
    violations = []
    for strategy in _registered_strategies():
        allowed = _allowed_columns_for(strategy)
        for mod, path in _strategy_modules(strategy):
            for column, lineno in _column_reads(path):
                if column in allowed:
                    continue
                owner = next((g.name for g in FEATURE_REGISTRY.values()
                              if column in g.columns), None)
                data_owner = next((d.name for d in DATA_COLUMN_REGISTRY.values()
                                   if column == d.column), None)
                if owner:
                    why = (f"column belongs to feature {owner!r}, which "
                           f"{strategy.id!r} does not declare in required_features")
                elif data_owner:
                    why = (f"column is the warehouse-backed data column "
                           f"{data_owner!r}, which {strategy.id!r} does not declare "
                           f"in required_data — without that the engine never joins "
                           f"it and the read yields None")
                else:
                    why = "no engine path builds this column"
                violations.append(
                    f"{strategy.id}: {mod.replace('.', '/')}.py:{lineno} reads "
                    f"row[{column!r}] — {why}"
                )
    assert not violations, (
        "Strategies read column(s) the engine never builds — the read silently "
        "yields None/NaN, so the branch behind it is dead code:\n  "
        + "\n  ".join(sorted(set(violations)))
    )


def test_shared_row_helpers_read_only_columns_the_engine_builds():
    """Same rule for the per-bar helpers strategies delegate row reads to."""
    violations = []
    for rel in SHARED_ROW_HELPERS:
        for column, lineno in _column_reads(BACKEND / rel):
            if column not in ENGINE_COLUMNS:
                violations.append(f"{rel}:{lineno} reads row[{column!r}]")
    assert not violations, (
        "Shared row helper reads a column the engine never builds:\n  "
        + "\n  ".join(violations)
    )


def test_explosive_reversal_has_no_vix_knob():
    """Targeted regression for the defect this file was written for: the dead
    `vix` read and the `vix_boost_threshold` knob that gated it are gone."""
    reg = get_registry()
    if not reg.list_all():
        reg.auto_discover()
    strategy = reg.get("explosive_reversal")
    assert strategy is not None
    assert "vix_boost_threshold" not in strategy.parameter_schema
    src = (BACKEND / "app/strategies/plugins/explosive_reversal.py").read_text(encoding="utf-8")
    # The docstring deliberately explains the removal, so assert on the CODE:
    # no row read of 'vix' survives anywhere in the module.
    assert not [c for c, _ in _column_reads(
        BACKEND / "app/strategies/plugins/explosive_reversal.py") if c == "vix"]
    assert "params[\"vix_boost_threshold\"]" not in src


# --------------------------------------------------------------------------
# net 2 — parameter reachability
# --------------------------------------------------------------------------

def _string_literals(path: Path) -> set:
    return {n.value for n in ast.walk(_parse(path))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_engine_consumed_param_list_matches_backtest_source():
    """Keeps ENGINE_CONSUMED_PARAMS honest — every entry must really be read by
    run_backtest, so the allowlist cannot quietly absorb a dead knob."""
    src = (BACKEND / "app/backtest.py").read_text(encoding="utf-8")
    missing = [k for k in ENGINE_CONSUMED_PARAMS if f'params.get("{k}"' not in src]
    assert not missing, f"listed as engine-consumed but not read by backtest.py: {missing}"


def test_every_declared_parameter_is_reachable():
    """Every `parameter_schema` key must be referenced somewhere that can act on
    it. Deliberately permissive about HOW (any string literal in the strategy's
    own module or an in-repo ancestor counts) so there are no false alarms — a
    failure here means the name appears literally nowhere, i.e. a knob the UI and
    the optimizer expose that no code can ever read."""
    violations = []
    for strategy in _registered_strategies():
        literals = set()
        for _, path in _strategy_modules(strategy):
            literals |= _string_literals(path)

        external_sources = EXTERNALLY_CONSUMED_PARAMS.get(strategy.id, ())
        external_literals = set()
        for rel in external_sources:
            external_literals |= _string_literals(BACKEND / rel)

        for key in strategy.parameter_schema:
            if (key in literals or key in ENGINE_CONSUMED_PARAMS
                    or key in SHARED_INDICATOR_PARAM_KEYS or key in external_literals):
                continue
            hint = (f" (declared externally-consumed via {list(external_sources)}, "
                    f"but absent from those modules too)" if external_sources else "")
            violations.append(f"{strategy.id}.parameter_schema[{key!r}]{hint}")
    assert not violations, (
        "Tunable parameter(s) that no code reads — the optimizer will search "
        "them and presets will store them, with no effect:\n  "
        + "\n  ".join(sorted(violations))
    )
