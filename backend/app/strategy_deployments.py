"""Strategy Deployment document builder and validation helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, time as _dtime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# IST is UTC+5:30; live arming and the EOD square pivot on India market time.
_IST_OFFSET = timedelta(hours=5, minutes=30)
# Hard daily backstop for a live arm (== the EOD square cutoff, §4.3 of the
# strategy-deploy-to-live design spec). New live entries are already blocked by
# the 14:50 IST signal-window guard; armed_until is the additional hard stop.
LIVE_ARM_EOD_IST = _dtime(15, 0)
# Account-absolute fat-finger ceiling default (decision #4): a per-deployment
# lots count is always clamped to at most this many lots at place time.
DEFAULT_ACCOUNT_MAX_LOTS_PER_ORDER = 20


ALLOWED_SOURCE_TYPES = {"preset", "backtest_run"}
# Two modes (user decision 2026-06-12): "signal_only" journals signals without
# trading; "paper" auto-trades clean signals. Legacy values map on create:
# "shadow" -> signal_only; "recommendation" was retired (treated as signal_only
# on old stored docs — the evaluator only ever trades when mode == "paper").
ALLOWED_MODES = {"signal_only", "paper"}
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
    if mode not in ALLOWED_MODES:
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


# ---------------------------------------------------------------------------
# Live-arm model (strategy-deploy-to-live, §4) — per-deployment `risk.live`.
#
# These are PURE helpers: they build/clear the `risk.live` sub-document and
# evaluate the live-allow predicate. No DB / env / broker I/O — the endpoints in
# routers/deployments.py do the persistence and the connected/can_trade reads,
# and the env master gate (LIVE_AUTOPLACE_ARMED) is checked separately at the
# executor transmit boundary so this stays unit-testable without env.
# ---------------------------------------------------------------------------

# disarmed_reason vocabulary (§4.1). "eod"/"token_expiry"/"daily_loss"/"halt"
# are set automatically by the evaluator/governor; "manual" by the disarm/stop
# endpoints.
LIVE_DISARM_REASONS = {"eod", "token_expiry", "manual", "daily_loss", "halt"}


def _ist_now(now_utc: Optional[datetime] = None) -> datetime:
    """Return the current IST wall-clock as a naive datetime (offset applied)."""
    base = now_utc or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base.astimezone(timezone.utc) + _IST_OFFSET).replace(tzinfo=None)


def live_arm_until(now_utc: Optional[datetime] = None) -> str:
    """Return the UTC ISO `armed_until` for an arm performed at *now_utc*.

    armed_until = today 15:00 IST, expressed back in UTC. If it is already past
    15:00 IST the deadline is in the past and the arm is effectively expired
    (is_deployment_live_allowed will reject it) — the endpoint surfaces that.
    """
    ist = _ist_now(now_utc)
    eod_ist_naive = datetime.combine(ist.date(), LIVE_ARM_EOD_IST)
    eod_utc = eod_ist_naive - _IST_OFFSET
    return eod_utc.replace(tzinfo=timezone.utc).isoformat()


def build_live_arm(
    *,
    lots: int,
    max_lots_per_day: Optional[int] = None,
    max_concurrent: int = 1,
    daily_loss_cap: Optional[float] = None,
    account_max_lots_per_order: int = DEFAULT_ACCOUNT_MAX_LOTS_PER_ORDER,
    armed_by: str = "user",
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the `risk.live` armed sub-document (§4.1).

    `lots` is the user's per-signal lot COUNT (overrides strategy sizing). It is
    clamped to [1, account_max_lots_per_order] here so a stored arm can never
    exceed the account ceiling. `max_lots_per_day` defaults to `lots` (a single
    signal's worth) when omitted; `max_concurrent` defaults to 1; `daily_loss_cap`
    None means no per-deployment loss pause (only account-level + manual).

    Raises ValueError on a non-positive ceiling or a non-numeric lots value.
    """
    try:
        ceiling = int(account_max_lots_per_order)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"account_max_lots_per_order must be an integer: {account_max_lots_per_order!r}") from exc
    if ceiling < 1:
        raise ValueError("account_max_lots_per_order must be >= 1")
    try:
        lots_n = int(lots)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"lots must be an integer: {lots!r}") from exc
    if lots_n < 1:
        raise ValueError("lots must be >= 1")
    capped_lots = min(lots_n, ceiling)

    if max_concurrent is None:
        max_concurrent_n = 1
    else:
        try:
            max_concurrent_n = int(max_concurrent)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_concurrent must be an integer: {max_concurrent!r}") from exc
        if max_concurrent_n < 1:
            raise ValueError("max_concurrent must be >= 1")

    if max_lots_per_day is None:
        max_lots_per_day_n = capped_lots
    else:
        try:
            max_lots_per_day_n = int(max_lots_per_day)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"max_lots_per_day must be an integer: {max_lots_per_day!r}") from exc
        if max_lots_per_day_n < 1:
            raise ValueError("max_lots_per_day must be >= 1")

    loss_cap: Optional[float] = None
    if daily_loss_cap is not None:
        try:
            loss_cap = float(daily_loss_cap)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"daily_loss_cap must be numeric: {daily_loss_cap!r}") from exc
        if loss_cap <= 0:
            raise ValueError("daily_loss_cap must be > 0 (omit it for no per-deployment loss cap)")

    now_iso = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat() \
        if (now_utc and now_utc.tzinfo) else (now_utc.replace(tzinfo=timezone.utc).isoformat() if now_utc else _now_iso())
    return {
        "armed": True,
        "armed_at": now_iso,
        "armed_until": live_arm_until(now_utc),
        "lots": capped_lots,
        "max_lots_per_day": max_lots_per_day_n,
        "max_concurrent": max_concurrent_n,
        "daily_loss_cap": loss_cap,
        "armed_by": str(armed_by or "user"),
        "disarmed_reason": None,
    }


def build_live_disarm(existing_live: Optional[Dict[str, Any]], *, reason: str = "manual") -> Dict[str, Any]:
    """Return a disarmed `risk.live` sub-document derived from *existing_live*.

    Preserves the audit fields (lots/caps/armed_at) so the UI can still show the
    last-armed envelope; sets `armed=False` and `disarmed_reason`. A disarm of an
    already-disarmed / missing arm is idempotent.
    """
    r = str(reason or "manual")
    if r not in LIVE_DISARM_REASONS:
        r = "manual"
    base = dict(existing_live or {})
    base["armed"] = False
    base["disarmed_reason"] = r
    return base


def is_deployment_live_allowed(
    deployment: Dict[str, Any],
    now_utc: Optional[datetime] = None,
    *,
    connected: bool,
) -> Tuple[bool, str]:
    """Return (ok, reason) for whether *deployment* may auto-place live now (§4.2).

    True iff ALL hold:
      - `risk.live.armed is True` (literal),
      - `now_utc < armed_until`,
      - the deployment is ACTIVE (not paused/archived),
      - `connected is True` (a broker token is stored).

    Fail-closed: missing/malformed `risk.live`, expired arm, not connected, or a
    non-ACTIVE deployment → (False, reason). The env master gate
    `LIVE_AUTOPLACE_ARMED` is checked SEPARATELY at the transmit boundary, so this
    predicate is unit-testable without env. Market-hours gating is applied by the
    evaluator's signal window (09:25–14:50) and the guard/EOD (15:00), so it is
    not re-checked here.
    """
    if not connected:
        return False, "not_connected"
    if str((deployment or {}).get("status") or "").upper() != "ACTIVE":
        return False, "deployment_not_active"
    live = ((deployment or {}).get("risk") or {}).get("live")
    if not isinstance(live, dict):
        return False, "not_armed"
    if live.get("armed") is not True:
        return False, "not_armed"
    armed_until = live.get("armed_until")
    if not armed_until:
        return False, "no_armed_until"
    try:
        dl = datetime.fromisoformat(str(armed_until))
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "bad_armed_until"
    now = (now_utc or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now >= dl:
        return False, "arm_expired"
    return True, "ok"
