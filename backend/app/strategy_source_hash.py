"""Strategy source-file SHA hashing for drift detection (slice 8).

The deployment evaluator already records `strategy_hash` over (id, version, params)
on every signal. That guards against parameter drift but NOT source-code drift -
if the user edits confluence_scalper.py without bumping version, signals before
and after the edit share an identical hash even though the code changed.

This module hashes the strategy plugin's own .py file. Pinned at deployment
creation; checked on every evaluator tick. A mismatch auto-pauses the deployment
with reason `strategy_source_drift`.

Scope (keep it simple, per user spec):
  - Hash ONLY the strategy's own .py file. Not its imports - dependencies can
    reasonably evolve without changing strategy logic, and we don't want to
    false-pause every time a utility module gets a docstring update.
  - When the file path can't be resolved (programmatically registered strategies,
    in-memory definitions in tests), return None and skip drift detection for
    that deployment. We never raise.
  - Hash is SHA-256 of the file's bytes. Truncated to 16 hex chars on display
    to match the existing strategy_hash convention.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


def strategy_file_path(strategy_obj: Any) -> Optional[Path]:
    """Return the absolute path to the strategy class's defining .py file.

    Uses the class's __module__ attribute and sys.modules to find the source
    file. Returns None when the path can't be determined (e.g., in-memory test
    classes or builtin / frozen modules).
    """
    if strategy_obj is None:
        return None
    cls = type(strategy_obj)
    module_name = getattr(cls, "__module__", None)
    if not module_name:
        return None
    mod = sys.modules.get(module_name)
    file = getattr(mod, "__file__", None) if mod else None
    if not file:
        return None
    path = Path(file)
    if not path.is_file():
        return None
    return path


def hash_strategy_source(strategy_obj: Any) -> Optional[str]:
    """Return SHA-256 of the strategy's .py source bytes, or None if unresolvable.

    Truncated to 16 hex characters to match strategy_hash convention. The full
    digest is unnecessary for drift detection - 64 bits of collision resistance
    is more than enough for a single user's plugin folder.
    """
    path = strategy_file_path(strategy_obj)
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError as exc:
        log.warning("strategy_source_hash: failed to read %s: %s", path, exc)
        return None


def detect_drift(*, pinned: Optional[str], current: Optional[str]) -> bool:
    """Return True iff a pinned hash exists, a current hash exists, and they differ.

    Conservative defaults:
      - If either side is missing/None we report no drift (we can't be sure).
      - If both are present and equal, no drift.
      - Only when both are present and unequal do we flag drift.
    """
    if not pinned or not current:
        return False
    return str(pinned) != str(current)


# Drift-audit fields stamped on a deployment when source drift auto-pauses it.
DRIFT_FIELDS = ("drift_detected_at", "drift_pinned_sha", "drift_current_sha", "drift_reason")


def build_repin_update(
    deployment: dict,
    current_sha: Optional[str],
    *,
    at: Optional[str] = None,
) -> dict:
    """Pure: compute the Mongo update to re-pin a deployment's strategy source.

    Re-pins `strategy_source_sha` to the strategy's CURRENT source hash, clears
    every drift audit field, and — only when the deployment was auto-paused for
    `strategy_source_drift` — resumes it (status ACTIVE). A deployment paused for
    any other reason (kill switch, manual) keeps its status; we just acknowledge
    the new source. An audit entry is appended to `repin_history`.

    Returns ``{"set": {...}, "unset": {...}, "audit": {...}, "resumed": bool}``.
    Never touches the database; the caller applies it.
    """
    ts = at or _now_iso()
    prior_sha = deployment.get("strategy_source_sha")
    drift_paused = (
        str(deployment.get("status") or "").upper() == "PAUSED"
        and str(deployment.get("drift_reason") or "") == "strategy_source_drift"
    )
    set_fields: dict = {
        "strategy_source_sha": current_sha,
        "updated_at": ts,
    }
    if drift_paused:
        set_fields["status"] = "ACTIVE"
    audit = {
        "at": ts,
        "prior_sha": prior_sha,
        "new_sha": current_sha,
        "prior_drift_reason": deployment.get("drift_reason"),
        "resumed": drift_paused,
    }
    return {
        "set": set_fields,
        "unset": {f: "" for f in DRIFT_FIELDS},
        "audit": audit,
        "resumed": drift_paused,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
