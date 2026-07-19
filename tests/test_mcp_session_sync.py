"""Flattrade MCP session sync: superset payload + atomic write + callback wiring.

Single-key coexistence (2026-07-18 design): AlphaForge is the sole OAuth owner
and mirrors its jKey into the MCP's session.json after each login. The payload
is a SUPERSET of plausible field aliases because Go's json.Unmarshal ignores
unknown fields — see docs/superpowers/specs/2026-07-18-flattrade-mcp-token-share-design.md.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mcp_session_sync import (  # noqa: E402
    SESSION_DIR_ENV,
    SESSION_TEMPLATE_ENV,
    build_session_payload,
    sync_session_file,
)

NOW = datetime(2026, 7, 18, 3, 0, 0, tzinfo=timezone.utc)


def test_payload_superset_aliases_share_one_token(monkeypatch):
    monkeypatch.delenv(SESSION_TEMPLATE_ENV, raising=False)
    p = build_session_payload(uid="FT1234", actid="FT1234", jkey="JK", api_key="AK", now=NOW)
    for alias in ("jKey", "jkey", "token", "susertoken"):
        assert p[alias] == "JK"
    for alias in ("uid", "actid", "user_id", "client_id"):
        assert p[alias] == "FT1234"
    assert p["api_key"] == "AK"
    assert p["saved_at"] == "2026-07-18T03:00:00Z"  # RFC3339 (Go time.Time default)
    # The API SECRET must never appear in any form.
    assert not any("secret" in k.lower() for k in p)


def test_template_override_substitutes_tokens(monkeypatch):
    monkeypatch.setenv(SESSION_TEMPLATE_ENV,
                       '{"tok": "__JKEY__", "who": {"id": "__UID__"}, "at": "__NOW_ISO__"}')
    p = build_session_payload(uid="U1", actid="A1", jkey="JK2", now=NOW)
    assert p == {"tok": "JK2", "who": {"id": "U1"}, "at": "2026-07-18T03:00:00Z"}


def test_sync_writes_then_skips_same_token(tmp_path, monkeypatch):
    monkeypatch.delenv(SESSION_TEMPLATE_ENV, raising=False)
    monkeypatch.setenv(SESSION_DIR_ENV, str(tmp_path))
    assert sync_session_file(uid="U", actid="U", jkey="JK") is True
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["jKey"] == "JK"
    # Same token again -> skip (no churn on every callback hit).
    assert sync_session_file(uid="U", actid="U", jkey="JK") is False
    # New token -> rewrite.
    assert sync_session_file(uid="U", actid="U", jkey="JK-NEW") is True
    assert json.loads((tmp_path / "session.json").read_text())["jKey"] == "JK-NEW"
    # No leftover temp files from the atomic write.
    assert [p.name for p in tmp_path.iterdir()] == ["session.json"]


def test_sync_noop_when_env_unset_or_empty_jkey(tmp_path, monkeypatch):
    monkeypatch.delenv(SESSION_DIR_ENV, raising=False)
    assert sync_session_file(uid="U", actid="U", jkey="JK") is False
    monkeypatch.setenv(SESSION_DIR_ENV, str(tmp_path))
    assert sync_session_file(uid="U", actid="U", jkey="") is False
    assert not (tmp_path / "session.json").exists()


def test_sync_never_raises_on_bad_dir(monkeypatch):
    # Point at a path that cannot be a directory (a file) — must swallow, not raise.
    monkeypatch.setenv(SESSION_DIR_ENV, str(ROOT / "README.md"))
    assert sync_session_file(uid="U", actid="U", jkey="JK") is False


def test_auth_callback_syncs_after_save_token():
    """Contract: the callback mirrors the token AFTER persisting it, before the
    redirect — so a successful login always leaves the MCP session fresh."""
    src = (ROOT / "backend" / "app" / "routers" / "live_broker.py").read_text(encoding="utf-8")
    cb = src[src.index("async def flattrade_auth_callback"):]
    cb = cb[:cb.index("\n@api.", 10)]
    i_save = cb.index("await save_token(")
    i_sync = cb.index("sync_session_file(")
    assert i_save < i_sync, "session sync must run after save_token"
    assert "never break the login flow" in cb or "non-fatal" in cb
