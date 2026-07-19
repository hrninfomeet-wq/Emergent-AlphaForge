"""Mirror AlphaForge's Flattrade jKey into the Flattrade MCP's session file.

The official Flattrade Trading MCP (closed-source Go binary) caches its login at
``~/.flattrade/session.json``. Under Flattrade's one-API-key policy the MCP can
never complete its own OAuth (the key's redirect is registered to AlphaForge's
callback), so AlphaForge — the single OAuth owner — writes the session file
after each daily token exchange and both consumers share one token, which the
PiConnect docs sanction ("once generated the token can be stored to bypass
authentication for subsequent connects").

Schema note: the binary's exact session struct is unknown (closed source), but
Go's ``json.Unmarshal`` ignores unknown fields, so ``build_session_payload``
emits a SUPERSET of every plausible field alias observed in the binary's string
table (jKey/jkey/token/susertoken, uid/actid/user_id, saved_at). Only a wrong
TYPE could break parsing; ``FLATTRADE_MCP_SESSION_TEMPLATE`` overrides the
payload shape without a code change if first validation reveals a mismatch.

stdlib-only ON PURPOSE: backend/scripts/resync_mcp_session.py imports this from
outside the app (host side), so it must not pull in app config/db modules.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

SESSION_DIR_ENV = "FLATTRADE_MCP_SESSION_DIR"
SESSION_TEMPLATE_ENV = "FLATTRADE_MCP_SESSION_TEMPLATE"
SESSION_FILENAME = "session.json"

# Keys the MCP might read the token from — kept in sync with the alias set in
# build_session_payload so the same-jKey skip check sees any schema variant.
_TOKEN_KEYS = ("jKey", "jkey", "token", "susertoken")


def session_dir() -> Optional[Path]:
    """Target directory, or None when sync is disabled (env unset/empty)."""
    raw = os.environ.get(SESSION_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def build_session_payload(
    *,
    uid: str,
    actid: str,
    jkey: str,
    api_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Superset session payload; unknown fields are ignored by Go's Unmarshal.

    NEVER include the API secret here — the file needs only the session token,
    and the secret must not leave backend env/config.
    """
    now_iso = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    template = os.environ.get(SESSION_TEMPLATE_ENV, "").strip()
    if template:
        subs = {
            "__JKEY__": jkey,
            "__UID__": uid,
            "__ACTID__": actid,
            "__API_KEY__": api_key or "",
            "__NOW_ISO__": now_iso,
        }

        def _sub(value: Any) -> Any:
            if isinstance(value, str):
                for token, real in subs.items():
                    value = value.replace(token, real)
                return value
            if isinstance(value, dict):
                return {k: _sub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_sub(v) for v in value]
            return value

        return _sub(json.loads(template))

    payload: Dict[str, Any] = {
        "uid": uid,
        "actid": actid,
        "user_id": uid,
        "client_id": uid,
        "jKey": jkey,
        "jkey": jkey,
        "token": jkey,
        "susertoken": jkey,
        "saved_at": now_iso,
    }
    if api_key:
        payload["api_key"] = api_key
    return payload


def _existing_token(path: Path) -> Optional[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for key in _TOKEN_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def sync_session_file(
    *,
    uid: str,
    actid: str,
    jkey: str,
    api_key: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Write the MCP session file. Returns True when a file was written.

    No-op (False) when the target dir env is unset or the existing file already
    carries this jKey. Never raises — a sync failure must never break the
    caller's login flow (log-and-continue).
    """
    try:
        target_dir = session_dir()
        if target_dir is None:
            return False
        if not jkey:
            log.warning("mcp session sync: empty jKey, refusing to write")
            return False
        target = target_dir / SESSION_FILENAME
        if _existing_token(target) == jkey:
            return False
        payload = build_session_payload(
            uid=uid, actid=actid, jkey=jkey, api_key=api_key, now=now,
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        # Atomic replace so the MCP never reads a half-written file.
        fd, tmp_path = tempfile.mkstemp(dir=str(target_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp_path, target)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        log.info("mcp session sync: wrote %s", target)
        return True
    except Exception as exc:
        log.warning("mcp session sync failed (non-fatal): %s", exc)
        return False
