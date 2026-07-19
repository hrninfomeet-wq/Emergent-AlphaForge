"""Strategy Deployment document builder and validation helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_SOURCE_TYPES = {"preset", "backtest_run"}
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
    now: Optional[str] = None,
) -> Dict[str, Any]:
    source_type = str(source_type or "").lower()
    mode = str(mode or "signal_only").lower()
    mode = LEGACY_MODE_MAP.get(mode, mode)
    confirmation_mode = str(confirmation_mode or "1m_close").lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError("Deployment source_type must be preset or backtest_run")
    # Deliberately CREATABLE_MODES, not ALLOWED_MODES: a deployment can never be
    # born live. Live is reached only via the preflighted live/enable route, so a
    # crafted create body can't skip the caps + safety chain into real money.
    if mode not in CREATABLE_MODES:
        raise ValueError("Deployment mode must be signal_only or paper")
    if confirmation_mode not in ALLOWED_CONFIRMATION_MODES:
        raise ValueError("Deployment confirmation_mode must be 1m_close or tick")

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

    return {
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
        "strategy_version": str(source_doc.get("strategy_version") or source_config.get("strategy_version") or ""),
        "strategy_hash": str(source_doc.get("strategy_hash") or source_config.get("strategy_hash") or ""),
        "strategy_source_sha": str(strategy_source_sha or ""),
        "params": _params(source_type, source_doc),
        "instrument": instrument,
        "timeframe": "1m",
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
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "audit": {
            "created_from": source_type,
            "source_id": source_id,
            "notes": "Created from saved preset/backtest result; no direct raw strategy deployment.",
        },
    }
