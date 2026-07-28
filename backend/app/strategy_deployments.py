"""Strategy Deployment document builder and validation helpers."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_SOURCE_TYPES = {"strategy", "preset", "backtest_run"}
# Two modes (user decision 2026-06-12): "signal_only" journals signals without
# trading; "paper" auto-trades clean signals. Legacy values map on create:
# "shadow" -> signal_only; "recommendation" was retired (treated as signal_only
# on old stored docs — the evaluator only ever trades when mode == "paper").
# "live" is a REAL-MONEY mode: a live deployment's confirmed signals route to the
# auto_live sink (auto_paper is suppressed) with no further per-session ceremony.
# It is never reachable from the ordinary create/update body — POST
# /deployments/{id}/live/enable is the only writer, and it runs the live preflight
# chain (ACTIVE, not retired, not drift-paused, broker connected, engine can_trade)
# plus the mandatory risk caps before flipping the mode.
ALLOWED_MODES = {"signal_only", "paper", "live"}
#: Modes an ordinary deployment create/update body may request. Live must go
#: through the preflighted enable route, so it is deliberately excluded here.
CREATABLE_MODES = {"signal_only", "paper"}
LEGACY_MODE_MAP = {"shadow": "signal_only", "recommendation": "signal_only"}
ALLOWED_CONFIRMATION_MODES = {"1m_close", "tick"}
ALLOWED_MONEYNESS = {"atm", "otm1", "itm1"}


def compute_forward_config_hash(deployment: Dict[str, Any]) -> str:
    """Fingerprint every decision-bearing field of a paper forward cohort.

    ``strategy_hash`` intentionally identifies only strategy id/version/params
    for signal audit compatibility.  Promotion needs a broader fingerprint:
    option selection, execution/risk policy and pre-trade filtering must not be
    changed while evidence is being collected.  ``risk.live`` is excluded
    because those caps are written only after a cohort passes promotion.
    """
    risk = dict(deployment.get("risk") or {})
    risk.pop("live", None)
    payload = {
        "strategy_id": str(deployment.get("strategy_id") or ""),
        "strategy_version": str(deployment.get("strategy_version") or ""),
        "strategy_source_sha": str(deployment.get("strategy_source_sha") or ""),
        "params": deployment.get("params") or {},
        "instrument": str(deployment.get("instrument") or ""),
        "timeframe": str(deployment.get("timeframe") or ""),
        "confirmation_mode": str(deployment.get("confirmation_mode") or ""),
        "option_policy": deployment.get("option_policy") or {},
        "pretrade_profile": str(deployment.get("pretrade_profile") or ""),
        "pretrade_settings_snapshot": deployment.get("pretrade_settings_snapshot") or {},
        "risk": risk,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_moneyness(values: Optional[Iterable[str]]) -> List[str]:
    cleaned: List[str] = []
    for value in values or ["atm"]:
        item = str(value or "").lower()
        if item not in ALLOWED_MONEYNESS:
            raise ValueError(f"Unsupported deployment moneyness: {value}")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned or ["atm"]


def _source_id(source_type: str, source_doc: Dict[str, Any]) -> str:
    if source_type == "preset":
        return str(source_doc.get("name") or "")
    return str(source_doc.get("id") or "")


def _source_config(source_doc: Dict[str, Any]) -> Dict[str, Any]:
    config = source_doc.get("config")
    return dict(config) if isinstance(config, dict) else {}


def _strategy_id(source_doc: Dict[str, Any]) -> str:
    config = _source_config(source_doc)
    return str(source_doc.get("strategy_id") or config.get("strategy_id") or "")


def _instrument(source_doc: Dict[str, Any]) -> str:
    config = _source_config(source_doc)
    return str(source_doc.get("instrument") or config.get("instrument") or "").upper()


def _params(source_type: str, source_doc: Dict[str, Any]) -> Dict[str, Any]:
    config = _source_config(source_doc)
    if source_type == "backtest_run" and isinstance(source_doc.get("params_applied"), dict):
        return dict(source_doc["params_applied"])
    if isinstance(config.get("params"), dict):
        return dict(config["params"])
    if isinstance(source_doc.get("params"), dict):
        return dict(source_doc["params"])
    return {}


def deployment_sizing_from_source(
    source_type: str, source_doc: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Extract the source run's position-sizing policy so a deployment can pin it
    and replay it live. Returns {"sizing_config", "lots", "source_id"} or None when
    the source carries no sizing config (→ live falls back to default_lots).

    - backtest_run: the resolved option-sim sizing_config (always present for an
      option run) + the run's fixed `lots` from its request.
    - preset: the execution block's sizing_config (present only when the preset was
      saved with one) + the execution `lots` scalar.
    """
    from app.portfolio import SizingConfig

    st = str(source_type or "").lower()
    if st == "backtest_run":
        ob = source_doc.get("option_backtest") or {}
        sizing_config = ob.get("sizing_config")
        lots = (ob.get("request") or {}).get("lots")
    elif st == "preset":
        ex = (source_doc.get("config") or {}).get("execution") or {}
        sizing_config = ex.get("sizing_config")
        lots = ex.get("lots")
    else:
        return None
    if not isinstance(sizing_config, dict):
        return None
    try:
        lots_n = int(lots or 1)
    except (TypeError, ValueError):
        lots_n = 1  # tolerate a hand-edited/imported preset with non-numeric lots
    return {
        "sizing_config": SizingConfig.from_dict(sizing_config).to_dict(),
        "lots": max(1, lots_n),
        "source_id": _source_id(st, source_doc),
    }


def build_deployment_doc(
    *,
    source_type: str,
    source_doc: Dict[str, Any],
    name: str,
    mode: str = "signal_only",
    confirmation_mode: str = "1m_close",
    option_moneyness: Optional[Iterable[str]] = None,
    pretrade_profile: str = "Balanced",
    risk: Optional[Dict[str, Any]] = None,
    dte_filter: Optional[Iterable[int]] = None,
    allow_overnight: bool = False,
    strategy_source_sha: Optional[str] = None,
    premium_trigger: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    source_type = str(source_type or "").lower()
    mode = str(mode or "signal_only").lower()
    mode = LEGACY_MODE_MAP.get(mode, mode)
    confirmation_mode = str(confirmation_mode or "1m_close").lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError("Deployment source_type must be strategy, preset or backtest_run")
    # Deliberately CREATABLE_MODES, not ALLOWED_MODES: a deployment can never be
    # born live. Live is reached only via the preflighted live/enable route, so a
    # crafted create body can't skip the caps + safety chain into real money.
    if mode not in CREATABLE_MODES:
        raise ValueError("Deployment mode must be signal_only or paper")
    if confirmation_mode not in ALLOWED_CONFIRMATION_MODES:
        raise ValueError("Deployment confirmation_mode must be 1m_close or tick")

    # Validate the declarative premium-trigger block AT CREATION. Without this a
    # malformed config is accepted cleanly and only manifests as a silent no-op
    # on the first evaluated bar — a deployment that can never trade should not
    # be creatable. extra="forbid" also surfaces here, which is exactly the error
    # an authoring LLM produces when it invents a field name.
    premium_trigger_block: Optional[Dict[str, Any]] = None
    if premium_trigger:
        from app.premium_trigger_config import PremiumTriggerConfig
        try:
            premium_trigger_block = PremiumTriggerConfig(**dict(premium_trigger)).model_dump()
        except Exception as exc:
            raise ValueError(f"Invalid premium_trigger block: {exc}") from exc

    source_id = _source_id(source_type, source_doc)
    strategy_id = _strategy_id(source_doc)
    instrument = _instrument(source_doc)
    if not source_id:
        raise ValueError("Deployment source is missing an id/name")
    if not strategy_id:
        raise ValueError("Deployment source is missing strategy_id")
    if not instrument:
        raise ValueError("Deployment source is missing instrument")

    timestamp = now or _now_iso()
    source_config = _source_config(source_doc)
    metrics = source_doc.get("metrics") if isinstance(source_doc.get("metrics"), dict) else {}
    strategy_version = str(
        source_doc.get("strategy_version")
        or source_config.get("strategy_version")
        or ""
    )
    params = _params(source_type, source_doc)
    strategy_hash = str(
        source_doc.get("strategy_hash")
        or source_config.get("strategy_hash")
        or ""
    )
    if not strategy_hash:
        # A deployment is the boundary of a forward cohort.  Older presets did
        # not persist this hash, so compute the same stable id/version/params
        # fingerprint used by the evaluator at creation time.  Without it a
        # brand-new cohort could collect forever but never satisfy config_frozen.
        from app.deployment_evaluator import compute_strategy_hash
        strategy_hash = compute_strategy_hash(strategy_id, strategy_version, params)

    # DTE filter: which days-to-expiry are eligible for the chosen contract.
    # User default 2026-05-27: 0-6 (full weekly window + a couple of days into next week).
    dte_values: list = []
    for v in (dte_filter if dte_filter is not None else [0, 1, 2, 3, 4, 5, 6]):
        try:
            iv = int(v)
            if iv >= 0 and iv not in dte_values:
                dte_values.append(iv)
        except (TypeError, ValueError):
            continue

    sizing_pin = deployment_sizing_from_source(source_type, source_doc)

    doc = {
        "id": str(uuid.uuid4()),
        "name": str(name or f"{strategy_id} deployment"),
        "source_type": source_type,
        "source_id": source_id,
        "source_snapshot": {
            "name": source_doc.get("name") or source_id,
            "saved_at": source_doc.get("saved_at"),
            "created_at": source_doc.get("created_at"),
            "metrics": metrics,
        },
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_hash": strategy_hash,
        "strategy_source_sha": str(strategy_source_sha or ""),
        "params": params,
        "instrument": instrument,
        "timeframe": str(source_config.get("timeframe") or source_doc.get("timeframe") or "1m"),
        "confirmation_mode": confirmation_mode,
        "option_policy": {
            "moneyness": _clean_moneyness(option_moneyness),
            "expiry_policy": "next_available",
            "dte_filter": dte_values,
        },
        "pretrade_profile": str(pretrade_profile or "Balanced"),
        "mode": mode,
        # Approval flow retired 2026-06-12: paper deployments auto-trade clean
        # signals; signal_only deployments journal only. Kept for doc-shape
        # compat with pre-existing readers.
        "manual_approval_required": False,
        "risk": {
            **(risk or {}),
            "allow_overnight": bool(allow_overnight),
            **({"sizing": sizing_pin} if sizing_pin else {}),
        },
        # Present ONLY when configured. Omission is meaningful: a null would look
        # like a configured-but-empty block to every downstream reader.
        **({"premium_trigger": premium_trigger_block} if premium_trigger_block else {}),
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "audit": {
            "created_from": source_type,
            "source_id": source_id,
            "notes": (
                "Created from an immutable Strategy Library snapshot."
                if source_type == "strategy"
                else "Created from saved preset/backtest result."
            ),
        },
    }
    doc["forward_config_hash"] = compute_forward_config_hash(doc)
    return doc


def resolve_deployment_premium_trigger(deployment):
    """Resolve a deployment's premium-trigger config: ``(config, source)``.

    Precedence — **the deployment's ``premium_trigger`` block wins, the
    strategy's own ``params`` are the fallback.** Every premium-trigger
    deployment created before the block existed carries its config in ``params``,
    so absence of a block must resolve exactly as it always did; this is what
    keeps those deployments byte-identical.

    ``source`` is one of ``"premium_trigger"``, ``"params"``, ``"invalid"`` or
    ``None`` (an ordinary strategy). ``"invalid"`` is deliberately distinct from
    ``None``: a malformed config must never be mistaken for "this strategy has
    none", or the deployment silently runs down a different path.

    Reads ``params`` RAW — never through ``strategy.merged_params()``, whose
    allow-list is keyed on the plugin's ``parameter_schema`` and drops 6 of the
    config's 14 fields (``stop_pts``, ``target_pts``, ``trail_x``, ``trail_y``,
    ``lots``, ``cost_config``).
    """
    from app.premium_trigger_dispatch import extract_premium_trigger_config

    dep = deployment or {}
    block = dep.get("premium_trigger")
    if block:
        cfg, reason = extract_premium_trigger_config(block)
        if cfg is not None:
            return cfg, "premium_trigger"
        return None, "invalid"

    cfg, reason = extract_premium_trigger_config(dep.get("params") or {})
    if cfg is not None:
        return cfg, "params"
    return (None, None) if reason == "absent" else (None, "invalid")


def effective_premium_params(deployment):
    """The params a premium-native deployment should actually run.

    The deployment's ``premium_trigger`` block is MERGED OVER ``params``, never
    substituted for them: the session engine reads ``leg_mode``, the five
    ``lazy_*`` fields, ``entry_cutoff``, ``exit_time``, the session P&L caps and
    ``vix_min``/``vix_max`` from params, and none of those exist on
    ``PremiumTriggerConfig`` — replacing would silently erase every 5B setting.

    Reads params RAW (never ``merged_params``), so the plugin allow-list cannot
    drop ``stop_pts``/``target_pts``/``trail_x``/``trail_y``/``lots``/``cost_config``.

    No block ⇒ the params dict unchanged ⇒ byte-identical for every deployment
    created before the block existed.
    """
    params = dict((deployment or {}).get("params") or {})
    cfg, source = resolve_deployment_premium_trigger(deployment)
    if source == "premium_trigger" and cfg is not None:
        params.update(cfg.model_dump(exclude_none=True))
    return params


def is_premium_native_deployment(deployment, strategy=None) -> bool:
    """Is this DEPLOYMENT driven by the premium session engine?

    Used by the POST-EXIT hooks (live guard-close finalize, paper exit marker)
    that finalize a leg and arm a lazy one. Deliberately more permissive than
    :func:`app.premium_trigger_dispatch.is_premium_trigger_strategy`, which gates
    whether ``strategy.evaluate()`` may be BYPASSED and must therefore key
    strictly off the strategy's own declared capability.

    **Entry paths are strict; exit paths are permissive — on purpose.** Refusing
    an exit-side hook because a config is incomplete or unresolvable would leave
    a leg un-finalized and a lazy leg unarmed with money still open. The failure
    modes are not symmetric: a too-strict ENTRY gate declines a trade, while a
    too-strict EXIT gate strands one.

    So this answers "does this deployment carry premium-trigger configuration at
    all", not "does it validate": a deployment's stored ``params`` are frequently
    a PARTIAL config completed by the plugin's schema defaults (``momentum_pct``
    among them), so requiring validity against the raw dict would reject real
    premium deployments. It also tolerates an unresolvable strategy, because the
    registry does not self-populate and the previous code fell back to raw params
    on a failed lookup.
    """
    from app.premium_trigger_dispatch import (
        extract_premium_trigger_config, is_premium_trigger_strategy,
    )

    if strategy is not None and is_premium_trigger_strategy(strategy):
        return True
    for source in (deployment or {}).get("premium_trigger"), (deployment or {}).get("params"):
        if source:
            _cfg, reason = extract_premium_trigger_config(source)
            if reason != "absent":
                return True
    return False
