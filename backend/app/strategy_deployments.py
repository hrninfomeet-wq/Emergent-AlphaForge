"""Strategy Deployment document builder and validation helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_SOURCE_TYPES = {"preset", "backtest_run"}
ALLOWED_MODES = {"shadow", "paper", "recommendation"}
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


def build_deployment_doc(
    *,
    source_type: str,
    source_doc: Dict[str, Any],
    name: str,
    mode: str = "shadow",
    confirmation_mode: str = "1m_close",
    option_moneyness: Optional[Iterable[str]] = None,
    pretrade_profile: str = "Balanced",
    risk: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    source_type = str(source_type or "").lower()
    mode = str(mode or "shadow").lower()
    confirmation_mode = str(confirmation_mode or "1m_close").lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError("Deployment source_type must be preset or backtest_run")
    if mode not in ALLOWED_MODES:
        raise ValueError("Deployment mode must be shadow, paper, or recommendation")
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
        "params": _params(source_type, source_doc),
        "instrument": instrument,
        "timeframe": "1m",
        "confirmation_mode": confirmation_mode,
        "option_policy": {
            "moneyness": _clean_moneyness(option_moneyness),
            "expiry_policy": "next_available",
            "manual_approval_required": True,
        },
        "pretrade_profile": str(pretrade_profile or "Balanced"),
        "mode": mode,
        "manual_approval_required": True,
        "risk": risk or {},
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "audit": {
            "created_from": source_type,
            "source_id": source_id,
            "notes": "Created from saved preset/backtest result; no direct raw strategy deployment.",
        },
    }
