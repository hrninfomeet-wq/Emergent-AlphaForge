"""Phase 4 engine dispatch — Backtest path.

Thin, pure delegator that:
  1) accepts a PremiumTriggerConfig
  2) translates it to the params dict shape the shipped sim expects
  3) delegates to run_premium_momentum_backtest (unchanged, byte-identical output)
  4) wraps the result to add config-driven traceability

This is a LIFT of the existing bespoke path, not a rewrite. The parity invariant
(see tests/test_premium_trigger_dispatch_parity.py) is:

    run_premium_momentum_backtest(**inputs)
      ==  dispatch_backtest(cfg, ..., inputs) [byte-identical `trades`]

Session 3 adds `dispatch_full_backtest` — the Optimizer/Backtest Lab wiring that was
deferred at the end of session 2 (see docs/EMERGENT_SESSION_NOTES.md and
docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md
§3.2). Running the shipped `premium_momentum` plugin through the general Optimizer or
Backtest Lab called `strategy.evaluate()` (a deliberate stub — the real logic lives only
in deployment_evaluator.py's dedicated branch), producing zero spot signals and the
literal "Option re-rank produced no paired results" message. `dispatch_full_backtest`
fixes this: for `strategy_id == "premium_momentum"` it runs the option-native sim
directly and reshapes its trades into option_backtest.py's canonical PAIRED-trade
contract, then reuses option_backtest.py's own pure aggregators (`_compute_metrics`,
`build_option_equity_curve`, `build_context_breakdown`) and portfolio.py's
`build_rupee_equity_curve` — so the result is shape-indistinguishable from what
`simulate_paired_option_trades` produces for any other strategy, and every existing
consumer (optimizer.py, runtime.py, the frontend) can read it unchanged.

Two honesty gaps this reshape CANNOT close (the premium-native engine never computes
them): no per-trade MFE/MAE walk (option_mfe_pts/option_mae_pts are always None — the
BacktestLab MaeMfeCard will render blank, not wrong), and no regime/time-of-day/DTE/VIX
context annotation (context_breakdown degrades to a single UNKNOWN bucket). Both are
None/absent rather than fabricated.

Still deferred to a follow-up session:
  - Live/deployment_evaluator dispatch on the same config schema (out of scope here —
    too safety-critical to touch without its own dedicated session).
  - The Stage-1 Optuna/grid search's per-trial spot scorer (optimizer.py::_evaluate)
    has no natural premium-native equivalent (there's no cheap "spot proxy" phase for a
    premium-triggered strategy) — candidates still reach Stage-2 re-rank via whatever
    Stage-1 selection already runs, which is degenerate (flat/zero) for premium_momentum
    candidates specifically. Stage-2 (_option_rerank/_survival_eval_oos, fixed here)
    correctly scores whichever candidates arrive; Stage-1 doesn't yet intelligently
    pre-filter them for this strategy family. Frontend config-block builder (deployment
    creation UI) is also still not built.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.instruments import resolve_lot_size
from app.option_backtest import _compute_metrics, build_context_breakdown, build_option_equity_curve
from app.portfolio import build_rupee_equity_curve
from app.premium_momentum_backtest import run_premium_momentum_backtest
from app.premium_trigger_config import PremiumTriggerConfig

log = logging.getLogger(__name__)

#: premium_momentum_backtest's exit_reason vocabulary -> option_backtest.py's
#: _compute_metrics bucket vocabulary. An unmapped raw string would silently
#: zero out every exit-reason counter downstream.
_EXIT_REASON_MAP = {
    "STOP": "OPTION_STOP",
    "TARGET": "OPTION_TARGET",
    "EOD": "OPTION_SIGNAL_EXIT",
}

#: Fields PremiumTriggerConfig actually accepts (extra="forbid") — merged_params
#: from strategy.merged_params() may carry registry bookkeeping fields (id, name,
#: description, ...) outside this set; filter to these before constructing so a
#: legitimate deployment param dict never raises a spurious ValidationError.
#: DERIVED from the model, never hand-copied. A literal list silently stops
#: extracting any field added to PremiumTriggerConfig later — the extraction
#: would just quietly ignore it, which is the same failure shape as the dropped
#: fields this module already had to work around.
_CONFIG_FIELDS = tuple(PremiumTriggerConfig.model_fields)

#: Fallback starting capital for the rupee equity curve, used only when a caller
#: supplies none. Defined here (not as a signature literal) so the one caller that
#: forwards a possibly-unset form value cannot accidentally pin a second default.
DEFAULT_BACKTEST_CAPITAL = 200_000.0


def extract_premium_trigger_config(
    params: Optional[Dict[str, Any]],
) -> Tuple[Optional[PremiumTriggerConfig], Optional[str]]:
    """Build a PremiumTriggerConfig from RAW strategy params.

    Returns ``(config, reason)``:

      * ``(cfg, None)``        — a valid config block is present
      * ``(None, "absent")``   — no premium-trigger fields at all; this is an
                                 ordinary strategy and callers must no-op
      * ``(None, "invalid:…")``— fields ARE present but do not validate

    **Absent and invalid are deliberately different answers.** Collapsing them
    (which the old inline extraction did, by swallowing every ValidationError
    into ``None``) means a typo'd config silently falls through to a completely
    different execution path and reports plausible numbers for a strategy the
    user never configured.

    **Reads the RAW params, never ``strategy.merged_params()``.** That merge is a
    strict allow-list keyed on the plugin's ``parameter_schema``, and the shipped
    ``premium_momentum`` schema omits 6 of this config's 14 fields — so
    ``stop_pts``, ``target_pts``, ``trail_x`` and ``trail_y`` were being dropped
    on the way in, and the run reported numbers as though the stop/target/trail
    had been applied. The allow-list exists to bound the OPTIMIZER's search
    space; it has no business filtering a declarative execution config.
    """
    src = dict(params or {})
    present = {k: src[k] for k in _CONFIG_FIELDS if src.get(k) is not None}
    if not present:
        return None, "absent"
    try:
        return PremiumTriggerConfig(**present), None
    except Exception as exc:  # pydantic ValidationError (and anything else)
        log.warning("premium-trigger config present but invalid: %s", exc)
        return None, f"invalid:{str(exc)[:200]}"


def premium_trigger_allowed_keys() -> set:
    """Every key an AUTHORED premium-trigger config block may carry.

    The union of ``PremiumTriggerConfig``'s fields and the shipped
    ``premium_momentum`` plugin's own declared params — DERIVED, so it widens
    automatically as the plugin gains capability.

    Why the union rather than just the config model: the config is the narrower
    Session-2 subset the *dispatcher* passes to the sim, while the plugin also
    ships ``leg_mode``, the five ``lazy_*`` knobs, ``entry_cutoff``,
    ``exit_time``, the session P&L caps and the VIX bounds. Validating a spec
    against the config alone made those twelve shipped capabilities
    inexpressible, so an authored strategy asking for a lazy leg came back as
    "couldn't map" even though `classify_rule` correctly called it BUILDABLE_NOW.

    ``PremiumTriggerConfig`` is deliberately NOT widened to close that gap: it is
    what the byte-identical parity test protects and what the sim consumes, so
    adding fields there would put the shipped strategy's numbers at risk.
    """
    # The SIM's own declaration is the deepest truth: the plugin's
    # parameter_schema was incomplete relative to what the engine actually reads
    # (the lazy trail knobs, the points variants, the percent trails), so
    # deriving only from the schema left shipped capability inexpressible.
    from app.premium_momentum_backtest import ENGINE_PARAM_KEYS

    keys = set(PremiumTriggerConfig.model_fields) | set(ENGINE_PARAM_KEYS)
    try:
        from app.strategies.base import get_registry
        reg = get_registry()
        if reg.get("premium_momentum") is None:
            reg.auto_discover()
        keys |= set(reg.get("premium_momentum").parameter_schema or {})
    except Exception:
        # Registry unavailable (host-only context): the config fields alone are a
        # safe floor — narrower, never wider, so nothing invalid slips through.
        pass
    return keys


def _lazy_is_usable(params: Dict[str, Any]) -> bool:
    """The sim raises ``ValueError('lazy_enabled requires lazy_momentum_pct or
    lazy_momentum_pts')``. ``dispatch_full_backtest`` documents that it never
    raises, so the condition is checked here instead — and reported, never
    silently absorbed."""
    if not params.get("lazy_enabled"):
        return False
    return (params.get("lazy_momentum_pct") is not None
            or params.get("lazy_momentum_pts") is not None)


def build_engine_params(
    cfg: PremiumTriggerConfig, merged_params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """The param dict actually handed to ``run_premium_momentum_backtest``.

    ``PremiumTriggerConfig`` carries 14 fields; the sim declares 34 in
    ``ENGINE_PARAM_KEYS`` and genuinely implements them — ``leg_mode="both"``,
    the lazy reversal leg, ``entry_cutoff``/``exit_time``, the percent trails,
    the session P&L caps, the VIX gate. Filtering the caller's params through the
    config alone meant a user could enable the lazy leg and see byte-identical
    results, which is what was reported on 2026-07-29.

    The config stays AUTHORITATIVE for the fields it owns: it is validated
    (cross-field rules, HH:MM shape) and is what the byte-identical parity test
    protects. The extra engine keys travel around it rather than widening it, so
    the shipped strategy's numbers stay pinned.
    """
    from app.premium_momentum_backtest import ENGINE_PARAM_KEYS

    core = cfg.to_backtest_params()
    src = dict(merged_params or {})
    lazy_ok = _lazy_is_usable(src)
    extras: Dict[str, Any] = {}
    for k, v in src.items():
        if k not in ENGINE_PARAM_KEYS or v is None or k in core:
            continue
        if k.startswith("lazy_") and not lazy_ok:
            continue  # an unusable lazy block is dropped whole, and reported
        extras[k] = v
    if not lazy_ok:
        extras.pop("lazy_enabled", None)
    # core last: a raw param must never overwrite a validated config field.
    extras.update(core)
    return extras


def premium_dropped_params(params: Optional[Dict[str, Any]]) -> List[str]:
    """Premium capabilities the caller CONFIGURED that this backtest will ignore.

    ``dispatch_full_backtest`` drives the sim from ``PremiumTriggerConfig`` alone,
    but a strategy may legitimately declare more than that model carries — the
    lazy-leg contingency, ``leg_mode``, ``entry_cutoff``/``exit_time``, the
    percent trail pair, the session P&L caps, the VIX gate. Those are real,
    shipped, live-honoured capabilities (Phase 5B, 2026-07-17), and the
    deployment path DOES apply them.

    Dropping them quietly means the backtest measures a different strategy from
    the one configured and from the one that would trade — the user reported
    exactly this on 2026-07-29 after authoring a two-leg lazy strategy whose
    second leg was never simulated, while the optimizer separately burned trials
    tuning the same inert knobs.

    Scope is deliberately the KNOWN premium surface
    (:func:`premium_trigger_allowed_keys`) minus what the RUN actually consumes,
    so registry bookkeeping (``id``/``name``/``description``) and unrelated keys
    can never be mistaken for a dropped capability. ``None`` values are excluded:
    a field the user left unset was never promised anything.

    Since the dispatcher started forwarding the full ``ENGINE_PARAM_KEYS``
    surface this is normally EMPTY — which is the point. It still fires for a
    lazy block that cannot run (``lazy_enabled`` without a lazy momentum
    trigger), because disabling that silently is the exact failure this whole
    mechanism exists to prevent.
    """
    from app.premium_momentum_backtest import ENGINE_PARAM_KEYS

    src = dict(params or {})
    known = premium_trigger_allowed_keys()
    consumed = set(_CONFIG_FIELDS) | set(ENGINE_PARAM_KEYS)
    out = sorted(
        k for k, v in src.items()
        if v is not None and k in known and k not in consumed
    )
    if src.get("lazy_enabled") and not _lazy_is_usable(src):
        out.append("lazy_enabled (incomplete: needs lazy_momentum_pct or "
                   "lazy_momentum_pts — the lazy leg did NOT run)")
    return out


def is_premium_trigger_strategy(strategy: Any) -> bool:
    """True when *strategy* is premium-native, i.e. driven by a premium-trigger
    config rather than by per-bar ``evaluate()``.

    This is the capability question the backtest domain actually needs answered.
    Six call sites used to ask ``strategy.id == "premium_momentum"`` instead —
    the optimizer's OOS survival, Stage-2 re-rank, Stage-1 preload, Stage-1
    evaluate closure and its two worker-pinning decisions, plus the coverage
    preflight. All of them mean "this strategy's ``evaluate()`` is a deliberate
    stub, so the spot path would score it as zero trades"; none of them actually
    cares about the id.

    Judged from the strategy's DECLARED DEFAULTS, not from a caller's params, so
    the answer is a stable property of the strategy itself and cannot flip
    between trials as the optimizer varies parameters.

    Byte-identical today: across all 12 shipped strategies this matches exactly
    the one the literal string matched. What it adds is that an AI-authored
    strategy declaring the same config stops being silently scored as a
    zero-trade dud.

    A partial or invalid config is NOT premium-native — hijacking the
    option-native path with a config that cannot drive the sim would score the
    strategy through machinery its own configuration never described.
    """
    try:
        defaults = strategy.default_params()
    except Exception:
        return False
    cfg, _reason = extract_premium_trigger_config(defaults)
    return cfg is not None


def _adapt_premium_trades_to_paired(
    trades: List[Dict[str, Any]], *, instrument: str, lots: int, lot_size: int,
) -> List[Dict[str, Any]]:
    """Reshape run_premium_momentum_backtest's trade dicts into option_backtest.py's
    canonical PAIRED-trade contract, so the existing pure aggregators
    (_compute_metrics / build_option_equity_curve / build_context_breakdown /
    build_rupee_equity_curve) can consume them unchanged. See this module's
    docstring for the two fields this reshape honestly cannot fabricate."""
    quantity = int(lot_size) * int(lots)
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(trades):
        entry_premium = float(t.get("entry_premium", 0.0))
        exit_premium = float(t.get("exit_premium", 0.0))
        entry_fill = float(t.get("entry_fill", entry_premium))
        exit_fill = float(t.get("exit_fill", exit_premium))
        side = str(t.get("side", "")).upper()
        # entry_ts/exit_ts/strike may arrive as numpy scalars (pandas-derived) —
        # BSON encoding (Mongo persistence via backtest_runs.insert_one) rejects
        # numpy.int64/float64 outright, unlike JSON serialization which tolerates
        # them. Cast to native Python types here, once, at the adapter boundary.
        entry_ts = t.get("entry_ts")
        exit_ts = t.get("exit_ts")
        entry_ts = int(entry_ts) if entry_ts is not None else None
        exit_ts = int(exit_ts) if exit_ts is not None else None
        strike = t.get("strike")
        strike = float(strike) if strike is not None else None
        out.append({
            "index_trade_id": i,
            "direction": side,
            "side": side,
            "signal_entry_ts": entry_ts,
            "signal_exit_ts": exit_ts,
            "signal_entry_datetime": "",
            "signal_exit_datetime": "",
            "index_entry_price": None,
            "index_exit_price": None,
            "moneyness": t.get("moneyness", ""),
            "resolved_expiry_date": t.get("expiry_date"),
            "context": {},
            "instrument_key": t.get("instrument_key"),
            "trading_symbol": "",
            "underlying": str(instrument),
            "expiry_date": t.get("expiry_date", ""),
            "strike": strike,
            "status": "PAIRED",
            "atm_at_entry": None,
            "option_entry_ts": entry_ts,
            "option_exit_ts": exit_ts,
            "raw_entry_option_price": round(entry_premium, 3),
            "raw_exit_option_price": round(exit_premium, 3),
            "entry_option_price": round(entry_fill, 3),
            "exit_option_price": round(exit_fill, 3),
            "option_exit_reason": _EXIT_REASON_MAP.get(str(t.get("exit_reason", "")), "OPTION_SIGNAL_EXIT"),
            "option_target_level": None,
            "option_stop_level": None,
            "entry_slippage_pts": round(entry_fill - entry_premium, 4),
            "exit_slippage_pts": round(exit_premium - exit_fill, 4),
            "slippage_bucket": None,
            "expiry_tail_applied": False,
            "lot_size": int(lot_size),
            "lots": int(lots),
            "quantity": quantity,
            "sizing_mode": "fixed_lots",
            "risk_per_unit": None,
            "risk_amount": None,
            "risk_exceeded": False,
            "entry_spread_pts": round(entry_fill - entry_premium, 4),
            "exit_spread_pts": round(exit_premium - exit_fill, 4),
            "gross_option_pnl_value": round(float(t.get("gross_pnl_rupees", 0.0)), 2),
            "charges": None,
            "total_charges": round(float(t.get("charges_rupees", 0.0)), 2),
            "option_pnl_pts": round(float(t.get("net_pnl_pts", 0.0)), 3),
            "option_pnl_value": round(float(t.get("net_pnl_rupees", 0.0)), 2),
            # Honestly absent — the premium-native walk never computes an MFE/MAE
            # window (no equivalent of option_backtest.py's _option_mfe_mae).
            "option_mfe_pts": None,
            "option_mae_pts": None,
        })
    return out


def dispatch_full_backtest(
    *,
    strategy_id: str,
    merged_params: Dict[str, Any],
    spot_df: pd.DataFrame,
    option_candles: pd.DataFrame,
    contracts: List[Dict[str, Any]],
    instrument: str,
    capital: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Full paired-option-backtest envelope for ANY strategy carrying a valid
    premium-trigger config, or None for a strategy that carries none (the entire
    regression-safety mechanism — every ordinary strategy's existing run_backtest
    + simulate_paired_option_trades path is completely untouched).

    Routes on **CONFIG PRESENCE, not on ``strategy_id``** — which is what this
    module's docstring always said the design was. The old
    ``strategy_id != "premium_momentum"`` guard meant an AI-authored strategy
    could carry a perfectly valid config and still be refused, even though
    ``ai.capability.classify_rule`` tells the user such a rule is BUILDABLE_NOW.

    ``strategy_id`` is retained for logging/traceability only; it no longer gates
    anything.

    Returns None (never raises) when:
      - no premium-trigger fields are present at all (an ordinary strategy), or
      - fields ARE present but do not validate — refused rather than silently
        falling through to a different execution path that would report numbers
        the user's config never described.
    """
    cfg, reason = extract_premium_trigger_config(merged_params)
    if cfg is None:
        if reason and reason != "absent":
            log.warning("dispatch refused for %s: %s", strategy_id, reason)
        return None

    # The FULL engine surface, not just the config's 14 fields — see
    # build_engine_params. Enabling the lazy leg used to change nothing.
    engine_params = build_engine_params(cfg, merged_params)
    try:
        pm_result = run_premium_momentum_backtest(
            spot_df=spot_df, option_candles=option_candles, contracts=contracts,
            instrument=instrument, params=engine_params,
        )
    except ValueError as exc:
        # Documented contract: this function never raises. build_engine_params
        # already pre-empts the known case (unusable lazy block); anything else
        # is refused rather than silently reported as a different strategy.
        log.warning("premium engine refused params for %s: %s", strategy_id, exc)
        return None
    # Data-driven lot (see instruments.resolve_lot_size) — the static map is stale.
    lot_size, _lot_warnings = resolve_lot_size(contracts, instrument)
    paired_trades = _adapt_premium_trades_to_paired(
        pm_result.get("trades", []), instrument=instrument, lots=int(cfg.lots), lot_size=lot_size,
    )
    metrics = _compute_metrics(paired_trades)
    # `None` (not a literal default in the signature) so callers can forward an
    # unset form value explicitly and the fallback stays defined in ONE place.
    portfolio = build_rupee_equity_curve(
        paired_trades,
        capital=DEFAULT_BACKTEST_CAPITAL if capital is None else float(capital),
    )
    equity_curve = build_option_equity_curve(paired_trades)
    context_breakdown = build_context_breakdown(paired_trades)
    paired_count = sum(1 for t in paired_trades if t["status"] == "PAIRED")
    coverage = {
        "spot_trade_count": len(paired_trades),
        "paired_trade_count": paired_count,
        "missing_contract": 0,
        "missing_entry_candle": 0,
        "missing_exit_candle": 0,
        "skipped_by_cap": 0,
    }
    return {
        "enabled": True,
        "underlying": str(instrument),
        "moneyness": cfg.moneyness,
        "exit_mode": "premium_trigger_config",
        "option_exit_config": {
            "target_pts": cfg.target_pts, "stop_pts": cfg.stop_pts,
            "target_pct": cfg.target_pct, "stop_pct": cfg.stop_pct,
        },
        "slippage_config": None,
        "cost_config": cfg.cost_config,
        # The premium-native walk sizes at a FIXED lot count (see
        # _adapt_premium_trades_to_paired's sizing_mode="fixed_lots"), so report
        # that policy rather than None. `deployment_sizing_from_source` reads this
        # key to pin a deployment's sizing: with None, deploying from a
        # premium-native backtest silently fell back to `default_lots` and traded
        # a different size than the backtest that justified it.
        "sizing_config": {
            "mode": "fixed_lots",
            "fixed_lots": int(cfg.lots),
            "enabled": True,
        },
        "coverage": coverage,
        "metrics": metrics,
        "equity_curve": equity_curve,
        "portfolio": portfolio,
        "context_breakdown": context_breakdown,
        "trades": paired_trades,
        "premium_trigger_config": cfg.model_dump(mode="json"),
        # The EXACT dict that drove the sim. The config echo above is the
        # validated core only, so it cannot show whether the lazy leg, leg_mode
        # or the session times actually ran.
        "engine_params": dict(engine_params),
        # Configured capabilities this run did NOT simulate. Always present (an
        # empty list, never a missing key) so a consumer's check is unambiguous.
        "dropped_params": premium_dropped_params(merged_params),
        "dispatch": "premium_trigger_config",
    }


def dispatch_backtest(
    *,
    cfg: PremiumTriggerConfig,
    spot_df: pd.DataFrame,
    option_candles: pd.DataFrame,
    contracts: List[Dict[str, Any]],
    instrument: str,
) -> Dict[str, Any]:
    """Run a premium-trigger backtest from a declarative config.

    Byte-identical to calling `run_premium_momentum_backtest` directly with the
    equivalent `params` dict — the whole point of Phase 4 dispatch is to route
    on CONFIG PRESENCE, not on `strategy_id == "premium_momentum"`, without
    changing the sim's behavior in the process (see spec's regression-safety
    invariant, §3.5 of docs/superpowers/specs/2026-07-13-...phase4-5*.md).
    """
    params = cfg.to_backtest_params()
    result = run_premium_momentum_backtest(
        spot_df=spot_df,
        option_candles=option_candles,
        contracts=contracts,
        instrument=instrument,
        params=params,
    )
    # Traceability: record the config that produced this result. Callers that
    # persist a backtest run into Mongo get a schema-versioned record of
    # exactly what config was used, so an Optimizer sweep can round-trip.
    result = dict(result)
    result["premium_trigger_config"] = cfg.model_dump(mode="json")
    result["dispatch"] = "premium_trigger_config"
    return result


__all__ = ["dispatch_backtest"]
