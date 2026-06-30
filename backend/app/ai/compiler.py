"""Deterministic compiler: StrategySpec -> safe StrategyBase plugin source.

Two public functions:

  - validate_spec(spec) -> list[str]   (human-readable errors; empty == valid)
  - compile_spec(spec)  -> str         (Python source for a StrategyBase subclass)

SAFETY CONTRACT
---------------
The emitted source is a pure function of the *validated* spec. We never
interpolate an unvalidated string into code:

  * string literals (id/name/description/labels) are emitted via repr()
  * column names are emitted only AFTER they pass the allowed_columns() whitelist
  * numbers are emitted as numbers
  * param references are emitted as params["NAME"] only after the name is checked
    against the declared params

There is no eval/exec in the GENERATED code. compile_spec() raises ValueError if
the spec is invalid, so a caller can never get code from a bad spec.
"""
from __future__ import annotations

import re
from typing import List, Set

from app.ai.spec_schema import CMP_OPS, Condition, StrategySpec

# Raw OHLCV columns that are always present alongside the computed indicators.
_RAW_OHLCV = {"open", "high", "low", "close", "volume"}

# Known regime labels (the gate may only skip these).
_KNOWN_REGIMES = {"TREND", "TREND_EXPANDING", "CHOP", "VOLATILE_CHOP", "MIXED", "UNKNOWN"}

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PARAM_PREFIX = "param:"

# Modes that genuinely need an exit (a no-exit strategy only closes at 15:00).
_EXIT_REQUIRED_MODES = {"SCALP", "INTRADAY"}


def allowed_columns(required_features: "list | tuple" = ()) -> Set[str]:
    """Whitelist of columns a Condition may reference.

    = grounding-catalog indicator columns (computed indicators + regime) + raw
    OHLCV, PLUS the columns of any DECLARED structural features (and their
    dependency closure). Advertise != allow: a feature column is only allowed
    once the strategy declares the feature in required_features, so a Spec can't
    reference fvg_top unless it asked for fvg_zones (which the engine then
    materializes for it). build_grounding_catalog() is imported lazily."""
    from app.ai.grounding import build_grounding_catalog

    cols = set(build_grounding_catalog()["indicator_columns"])
    cols |= _RAW_OHLCV
    if required_features:
        from app.features.registry import resolve_features
        for g in resolve_features(list(required_features)):
            cols |= set(g.columns)
    return cols


def _param_names(spec: StrategySpec) -> Set[str]:
    return {p.name for p in spec.params}


def validate_spec(spec: StrategySpec) -> List[str]:
    """Return a list of human-readable errors (empty == valid)."""
    errors: List[str] = []
    # Declared features must exist; an unknown name is a clean validation error,
    # not an exception (resolve_features would otherwise raise FeatureError).
    from app.features.registry import FEATURE_REGISTRY
    unknown_feats = [f for f in spec.required_features if f not in FEATURE_REGISTRY]
    if unknown_feats:
        errors.append(
            f"required_features: unknown feature(s) {unknown_feats}; "
            f"available: {sorted(FEATURE_REGISTRY)}"
        )
    known_feats = [f for f in spec.required_features if f in FEATURE_REGISTRY]
    cols = allowed_columns(known_feats)
    pnames = _param_names(spec)

    # id must be a clean slug (it becomes a Python identifier + a filename).
    if not _SLUG_RE.match(spec.id or ""):
        errors.append(
            f"id {spec.id!r} is not a valid slug (must match ^[a-z][a-z0-9_]*$)"
        )

    # params: names valid, types valid (Literal already enforces type at parse,
    # but we re-check the name here).
    for p in spec.params:
        if not _SLUG_RE.match(p.name or ""):
            errors.append(
                f"param name {p.name!r} is not a valid identifier (^[a-z][a-z0-9_]*$)"
            )
        if p.type not in ("int", "float", "bool"):
            errors.append(f"param {p.name!r} has invalid type {p.type!r}")

    # at least one entry list non-empty
    if not spec.entry_ce and not spec.entry_pe:
        errors.append("at least one of entry_ce / entry_pe must be non-empty")

    # validate every condition in both entry lists
    for side, conds in (("entry_ce", spec.entry_ce), ("entry_pe", spec.entry_pe)):
        for i, c in enumerate(conds):
            errors.extend(_validate_condition(side, i, c, cols, pnames))

    # exits: at least one field set for SCALP/INTRADAY
    has_exit = any(
        getattr(spec.exits, f) is not None
        for f in ("spot_target_pts", "spot_stop_pts", "target_pct", "stop_pct", "time_stop_minutes")
    )
    if not has_exit and (set(spec.supported_modes) & _EXIT_REQUIRED_MODES):
        errors.append(
            "no exits set: a SCALP/INTRADAY strategy needs at least one exit "
            "(spot_target_pts/spot_stop_pts/target_pct/stop_pct/time_stop_minutes)"
        )

    # gate_skip_regimes within known labels
    for r in spec.gate_skip_regimes:
        if r not in _KNOWN_REGIMES:
            errors.append(
                f"gate_skip_regimes value {r!r} is not a known regime "
                f"(allowed: {', '.join(sorted(_KNOWN_REGIMES))})"
            )

    return errors


def _validate_condition(
    side: str, idx: int, c: Condition, cols: Set[str], pnames: Set[str]
) -> List[str]:
    where = f"{side}[{idx}]"
    out: List[str] = []

    if c.left not in cols:
        out.append(f"{where}: left {c.left!r} is not a known column")

    if c.op not in CMP_OPS:
        out.append(f"{where}: op {c.op!r} is not a valid operator (allowed: {', '.join(CMP_OPS)})")

    # right: number always fine; str must be 'param:NAME' (declared) or a column.
    if isinstance(c.right, str):
        if c.right.startswith(_PARAM_PREFIX):
            pname = c.right[len(_PARAM_PREFIX):]
            if pname not in pnames:
                out.append(f"{where}: right references undeclared param {pname!r}")
        elif c.right not in cols:
            out.append(f"{where}: right {c.right!r} is not a number, a declared param, or a known column")

    return out


# --------------------------------------------------------------------------- #
# Codegen
# --------------------------------------------------------------------------- #
def _pascal_case(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("_") if part)


def _operand_columns(c: Condition) -> List[str]:
    """Columns referenced by a condition (left always; right if it is a column)."""
    out = [c.left]
    if isinstance(c.right, str) and not c.right.startswith(_PARAM_PREFIX):
        out.append(c.right)
    return out


def _render_operand(value, *, prev: bool = False) -> str:
    """Render the RIGHT operand (or a column) as a Python expression.

    - number  -> the numeric literal
    - 'param:NAME' -> params["NAME"]
    - column name -> float(row["col"]) (or float(prev.get("col")) when prev=True)
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    # str: param ref or column (both already validated)
    if value.startswith(_PARAM_PREFIX):
        return f'params[{value[len(_PARAM_PREFIX):]!r}]'
    if prev:
        return f'float(prev.get({value!r}))'
    return f'float(row[{value!r}])'


def _render_condition(c: Condition) -> str:
    """Render one Condition to a parenthesised boolean Python sub-expression."""
    left = f'float(row[{c.left!r}])'
    right = _render_operand(c.right)

    if c.op in (">", ">=", "<", "<=", "==", "!="):
        return f"({left} {c.op} {right})"

    if c.op == "cross_above":
        prev_left = f'prev.get({c.left!r})'
        prev_right = _render_operand(c.right, prev=True) if _is_column(c.right) else _render_operand(c.right)
        return (
            f"({prev_left} is not None and {left} > {right} "
            f"and float({prev_left}) <= {prev_right})"
        )

    if c.op == "cross_below":
        prev_left = f'prev.get({c.left!r})'
        prev_right = _render_operand(c.right, prev=True) if _is_column(c.right) else _render_operand(c.right)
        return (
            f"({prev_left} is not None and {left} < {right} "
            f"and float({prev_left}) >= {prev_right})"
        )

    # Unreachable: validate_spec rejects unknown ops before codegen.
    raise ValueError(f"unhandled op {c.op!r}")


def _is_column(value) -> bool:
    return isinstance(value, str) and not value.startswith(_PARAM_PREFIX)


def _render_and(conds: List[Condition]) -> str:
    if not conds:
        return "False"
    return " and ".join(_render_condition(c) for c in conds)


def _required_columns(spec: StrategySpec) -> List[str]:
    cols: Set[str] = set()
    for c in list(spec.entry_ce) + list(spec.entry_pe):
        cols.update(_operand_columns(c))
    return sorted(cols)


def _param_schema_literal(spec: StrategySpec) -> str:
    """Emit the parameter_schema dict source (numbers as numbers, no eval)."""
    if not spec.params:
        return "{}"
    lines = ["{"]
    for p in spec.params:
        parts = [f'"type": {p.type!r}']
        if p.min is not None:
            parts.append(f'"min": {p.min!r}')
        if p.max is not None:
            parts.append(f'"max": {p.max!r}')
        parts.append(f'"default": {p.default!r}')
        lines.append(f'        {p.name!r}: {{{", ".join(parts)}}},')
    lines.append("    }")
    return "\n".join(lines)


def _exit_kwargs(spec: StrategySpec) -> List[str]:
    """Only the exit kwargs that are set (non-None)."""
    out: List[str] = []
    for f in ("spot_target_pts", "spot_stop_pts", "target_pct", "stop_pct", "time_stop_minutes"):
        v = getattr(spec.exits, f)
        if v is not None:
            out.append(f"{f}={v!r}")
    return out


def _reasons_literal(conds: List[Condition]) -> str:
    labels = [c.label for c in conds if c.label]
    return repr(labels)


def compile_spec(spec: StrategySpec) -> str:
    """Validate, then return source for a StrategyBase subclass.

    Raises ValueError (joined errors) if the spec is invalid."""
    errors = validate_spec(spec)
    if errors:
        raise ValueError("invalid strategy spec:\n- " + "\n- ".join(errors))

    class_name = _pascal_case(spec.id)
    required = _required_columns(spec)
    ce_expr = _render_and(spec.entry_ce)
    pe_expr = _render_and(spec.entry_pe)
    ce_reasons = _reasons_literal(spec.entry_ce)
    pe_reasons = _reasons_literal(spec.entry_pe)
    exit_kwargs = _exit_kwargs(spec)

    lines: List[str] = []
    lines.append("# AUTO-GENERATED by the strategy authoring compiler. Safe to edit, but re-installing")
    lines.append("# from the spec will overwrite. is_builtin=False marks this a custom plugin.")
    lines.append("from __future__ import annotations")
    lines.append("import pandas as pd")
    lines.append("from app.strategies.base import StrategyBase, Signal")
    lines.append("")
    lines.append("")
    lines.append(f"class {class_name}(StrategyBase):")
    lines.append(f"    id = {spec.id!r}")
    lines.append(f"    name = {spec.name!r}")
    lines.append(f"    version = {spec.version!r}")
    lines.append(f"    description = {spec.description!r}")
    lines.append("    is_builtin = False")
    lines.append(f"    supported_instruments = {list(spec.supported_instruments)!r}")
    lines.append(f"    supported_modes = {list(spec.supported_modes)!r}")
    lines.append(f"    supported_timeframes = {list(spec.supported_timeframes)!r}")
    # Declared structural features must survive into the installed plugin so the
    # engine materializes their columns at backtest/live time (else the strategy
    # references columns that never get computed and silently never fires).
    if spec.required_features:
        lines.append(f"    required_features = {list(spec.required_features)!r}")
    # cooldown_bars is declared in the schema (the engine handles cooldown; the
    # generated evaluate() does NOT, mirroring builtins).
    schema_src = _param_schema_literal(spec)
    if spec.cooldown_bars:
        # inject cooldown_bars into the schema dict source
        if schema_src == "{}":
            schema_src = (
                "{\n"
                f'        "cooldown_bars": {{"type": "int", "default": {spec.cooldown_bars!r}}},\n'
                "    }"
            )
        else:
            schema_src = schema_src.rstrip()
            assert schema_src.endswith("}")
            schema_src = (
                schema_src[: -len("\n    }")]
                + f'\n        "cooldown_bars": {{"type": "int", "default": {spec.cooldown_bars!r}}},'
                + "\n    }"
            )
    lines.append(f"    parameter_schema = {schema_src}")
    lines.append("")
    lines.append("    def evaluate(self, row: pd.Series, prev: pd.Series, params, ctx) -> Signal:")
    # Emit with double-quoted column literals (each already whitelist-validated)
    # to match the documented generated-code shape.
    required_src = "[" + ", ".join(f'"{col}"' for col in required) + "]"
    lines.append(f"        required = {required_src}")
    lines.append("        if any(pd.isna(row.get(k)) for k in required):")
    lines.append('            return Signal(direction="NONE", blockers=["indicators warming up"])')
    lines.append("")
    lines.append(f"        ce = {ce_expr}")
    lines.append(f"        pe = {pe_expr}")
    lines.append("")
    lines.append('        direction = "NONE"')
    lines.append("        reasons = []")
    lines.append("        if ce and not pe:")
    lines.append('            direction = "CE"')
    lines.append(f"            reasons = {ce_reasons}")
    lines.append("        elif pe and not ce:")
    lines.append('            direction = "PE"')
    lines.append(f"            reasons = {pe_reasons}")
    lines.append("")
    lines.append("        blockers = []")
    if spec.gate_skip_regimes:
        skip = tuple(spec.gate_skip_regimes)
        lines.append('        if direction != "NONE":')
        lines.append('            regime = row.get("regime", "UNKNOWN")')
        lines.append(f"            if regime in {skip!r}:")
        lines.append('                blockers.append("regime " + str(regime))')
    lines.append("")
    lines.append("        return Signal(")
    lines.append("            direction=direction,")
    lines.append('            score=100 if direction != "NONE" else 0,')
    lines.append("            reasons=reasons,")
    lines.append("            blockers=blockers,")
    for kw in exit_kwargs:
        lines.append(f"            {kw},")
    lines.append("        )")
    lines.append("")

    return "\n".join(lines)
