"""Tests for flattrade_token.py — host-only, no real network calls.

Covers:
  - is_configured() with/without env vars
  - build_login_url() correct app_key param
  - _compute_hash() SHA-256 formula correctness for known inputs
  - get_status() logic with injected token docs + injected "now":
      * no doc   → connected=False
      * valid doc (issued after 06:00 IST, not yet expired) → connected, not expired, not needs-regen
      * expired by time (past expires_at)
      * needs-regen (issued before today's 06:00 IST cutoff)
  - exchange_code_for_token() request-building + response-parsing via stubbed httpx
  - save_token() / get_token() / disconnect() via an in-memory async fake DB
"""
import asyncio
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake DB for save/get/disconnect tests
# ---------------------------------------------------------------------------

class _FakeCollection:
    """Minimal async motor-like collection backed by an in-memory list."""

    def __init__(self):
        self._docs: List[Dict[str, Any]] = []

    def _match(self, filter_: dict, doc: dict) -> bool:
        return all(doc.get(k) == v for k, v in filter_.items())

    async def find_one(self, filter_: dict, projection=None) -> Optional[dict]:
        for doc in self._docs:
            if self._match(filter_, doc):
                result = dict(doc)
                if projection:
                    # Remove keys explicitly set to 0
                    for k, v in projection.items():
                        if v == 0 and k in result:
                            del result[k]
                return result
        return None

    async def update_one(self, filter_: dict, update: dict, upsert: bool = False):
        set_vals = update.get("$set", {})
        for doc in self._docs:
            if self._match(filter_, doc):
                doc.update(set_vals)
                return MagicMock(modified_count=1, upserted_id=None)
        if upsert:
            new_doc = dict(set_vals)
            self._docs.append(new_doc)
        return MagicMock(modified_count=0)

    async def delete_many(self, filter_: dict):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not self._match(filter_, d)]
        return MagicMock(deleted_count=before - len(self._docs))


class _FakeDB:
    def __init__(self):
        self.live_broker_tokens = _FakeCollection()


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

def test_is_configured_false_without_env(monkeypatch):
    monkeypatch.delenv("FLATTRADE_API_KEY", raising=False)
    monkeypatch.delenv("FLATTRADE_API_SECRET", raising=False)
    from app.live import flattrade_token
    # Force re-read by calling the private env helpers (they read os.environ directly)
    assert flattrade_token.is_configured() is False


def test_is_configured_true_with_both_env(monkeypatch):
    monkeypatch.setenv("FLATTRADE_API_KEY", "TESTKEY")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "TESTSECRET")
    from app.live import flattrade_token
    assert flattrade_token.is_configured() is True


def test_is_configured_false_if_only_key(monkeypatch):
    monkeypatch.setenv("FLATTRADE_API_KEY", "TESTKEY")
    monkeypatch.delenv("FLATTRADE_API_SECRET", raising=False)
    from app.live import flattrade_token
    assert flattrade_token.is_configured() is False


def test_is_configured_false_if_only_secret(monkeypatch):
    monkeypatch.delenv("FLATTRADE_API_KEY", raising=False)
    monkeypatch.setenv("FLATTRADE_API_SECRET", "TESTSECRET")
    from app.live import flattrade_token
    assert flattrade_token.is_configured() is False


# ---------------------------------------------------------------------------
# build_login_url
# ---------------------------------------------------------------------------

def test_build_login_url_contains_app_key(monkeypatch):
    monkeypatch.setenv("FLATTRADE_API_KEY", "MYAPIKEY123")
    from app.live import flattrade_token
    url = flattrade_token.build_login_url()
    assert "auth.flattrade.in" in url
    assert "app_key=MYAPIKEY123" in url


def test_build_login_url_base_host(monkeypatch):
    monkeypatch.setenv("FLATTRADE_API_KEY", "K1")
    from app.live import flattrade_token
    url = flattrade_token.build_login_url()
    assert url.startswith("https://auth.flattrade.in/")


# ---------------------------------------------------------------------------
# _compute_hash — SHA-256 formula verification
# ---------------------------------------------------------------------------

def test_compute_hash_known_inputs():
    """Verify sha256(api_key + request_code + api_secret) hex matches stdlib."""
    from app.live.flattrade_token import _compute_hash

    api_key = "TESTKEY"
    request_code = "CODE123"
    api_secret_raw = "SECRET456"

    expected = hashlib.sha256(f"{api_key}{request_code}{api_secret_raw}".encode("utf-8")).hexdigest()
    assert _compute_hash(api_key, request_code, api_secret_raw) == expected


def test_compute_hash_is_hex_string():
    from app.live.flattrade_token import _compute_hash
    h = _compute_hash("a", "b", "c")
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_hash_order_matters():
    """sha256(A+B+C) != sha256(C+B+A) — order is api_key, code, secret."""
    from app.live.flattrade_token import _compute_hash
    h1 = _compute_hash("KEY", "CODE", "SECRET")
    h2 = _compute_hash("SECRET", "CODE", "KEY")
    assert h1 != h2


def test_compute_hash_empty_inputs():
    """Empty strings are valid inputs; result must still be a 64-char hex."""
    from app.live.flattrade_token import _compute_hash
    h = _compute_hash("", "", "")
    assert len(h) == 64


# ---------------------------------------------------------------------------
# exchange_code_for_token — stubbed httpx
# ---------------------------------------------------------------------------

def _make_fake_response(status_code: int, json_data: dict):
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def test_exchange_code_for_token_success(monkeypatch):
    """Happy path: stat=Ok, token returned."""
    monkeypatch.setenv("FLATTRADE_API_KEY", "KEY1")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "SECRET1")

    fake_resp = _make_fake_response(200, {"stat": "Ok", "token": "JKEY_ABC123"})

    async def fake_post(url, *, json=None, **kw):
        # Verify request shape
        assert url == "https://authapi.flattrade.in/trade/apitoken"
        assert json["api_key"] == "KEY1"
        assert json["request_code"] == "MYCODE"
        # api_secret in body must be sha256(KEY1+MYCODE+SECRET1)
        expected_hash = hashlib.sha256(b"KEY1MYCODESECRET1").hexdigest()
        assert json["api_secret"] == expected_hash
        return fake_resp

    # Patch httpx.AsyncClient so we never hit the network
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=fake_post)

    with patch("app.live.flattrade_token.httpx.AsyncClient", return_value=mock_client):
        from app.live import flattrade_token
        result = run(flattrade_token.exchange_code_for_token("MYCODE"))

    assert result["stat"] == "Ok"
    assert result["token"] == "JKEY_ABC123"


def test_exchange_code_for_token_rejection(monkeypatch):
    """stat != Ok → RuntimeError."""
    monkeypatch.setenv("FLATTRADE_API_KEY", "KEY1")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "SECRET1")

    fake_resp = _make_fake_response(200, {"stat": "Not_Ok", "emsg": "Invalid code"})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("app.live.flattrade_token.httpx.AsyncClient", return_value=mock_client):
        from app.live import flattrade_token
        import pytest
        with pytest.raises(RuntimeError, match="Invalid code"):
            run(flattrade_token.exchange_code_for_token("BADCODE"))


def test_exchange_code_for_token_http_error(monkeypatch):
    """Non-200 status → RuntimeError."""
    monkeypatch.setenv("FLATTRADE_API_KEY", "KEY1")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "SECRET1")

    fake_resp = _make_fake_response(500, {})
    fake_resp.text = "Internal Server Error"

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_resp)

    with patch("app.live.flattrade_token.httpx.AsyncClient", return_value=mock_client):
        from app.live import flattrade_token
        import pytest
        with pytest.raises(RuntimeError, match="500"):
            run(flattrade_token.exchange_code_for_token("CODE"))


def test_exchange_code_for_token_not_configured(monkeypatch):
    """Missing env → RuntimeError before making any HTTP call."""
    monkeypatch.delenv("FLATTRADE_API_KEY", raising=False)
    monkeypatch.delenv("FLATTRADE_API_SECRET", raising=False)
    from app.live import flattrade_token
    import pytest
    with pytest.raises(RuntimeError, match="not configured"):
        run(flattrade_token.exchange_code_for_token("CODE"))


# ---------------------------------------------------------------------------
# _today_regen_cutoff_utc helper
# ---------------------------------------------------------------------------

def test_regen_cutoff_is_6am_ist():
    """Cutoff at an IST noon → 06:00 IST that day = 00:30 UTC."""
    from app.live.flattrade_token import _today_regen_cutoff_utc
    # noon IST on 2026-06-22 = 06:30 UTC on 2026-06-22
    now_utc = datetime(2026, 6, 22, 6, 30, 0, tzinfo=timezone.utc)  # 12:00 IST
    cutoff = _today_regen_cutoff_utc(now_utc)
    # 06:00 IST = 00:30 UTC
    expected = datetime(2026, 6, 22, 0, 30, 0, tzinfo=timezone.utc)
    assert cutoff == expected


def test_regen_cutoff_before_6am_ist():
    """At 03:00 IST (before cutoff), today's cutoff is still 06:00 IST same day."""
    from app.live.flattrade_token import _today_regen_cutoff_utc
    # 03:00 IST = 21:30 UTC previous day
    now_utc = datetime(2026, 6, 21, 21, 30, 0, tzinfo=timezone.utc)  # 03:00 IST 2026-06-22
    cutoff = _today_regen_cutoff_utc(now_utc)
    # IST date is 2026-06-22 at 03:00 IST; today's 06:00 IST cutoff = 00:30 UTC on 2026-06-22
    expected = datetime(2026, 6, 22, 0, 30, 0, tzinfo=timezone.utc)
    assert cutoff == expected


# ---------------------------------------------------------------------------
# get_status — injected token docs + injected "now"
# ---------------------------------------------------------------------------

def _make_token_doc(
    issued_at: datetime,
    expires_at: datetime,
    uid: str = "U1",
    actid: str = "A1",
) -> dict:
    return {
        "user": "default",
        "broker": "flattrade",
        "jKey": "JKEY_TEST",
        "uid": uid,
        "actid": actid,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def _status_with_doc(doc, now):
    """Run get_status with an injected fake DB and injected 'now'."""
    fake_db = _FakeDB()
    if doc:
        fake_db.live_broker_tokens._docs.append(doc)

    async def run_status():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            from app.live import flattrade_token
            return await flattrade_token.get_status("default", now=now)

    return run(run_status())


def test_get_status_no_doc():
    """No token doc → connected=False."""
    now = datetime(2026, 6, 22, 10, 0, 0, tzinfo=timezone.utc)
    status = _status_with_doc(None, now)
    assert status["connected"] is False
    assert status["expired"] is False
    assert status["regenerate_after_6am"] is False
    assert status["uid"] is None
    assert status["actid"] is None


def test_get_status_connected_valid():
    """Token issued after 06:00 IST, not yet expired → connected, not expired, not needs-regen."""
    # 06:00 IST = 00:30 UTC on 2026-06-22
    # Issue token at 09:00 IST = 03:30 UTC
    issued_at = datetime(2026, 6, 22, 3, 30, 0, tzinfo=timezone.utc)  # 09:00 IST
    # expires tomorrow 06:00 IST = 2026-06-23 00:30 UTC
    expires_at = datetime(2026, 6, 23, 0, 30, 0, tzinfo=timezone.utc)
    # "now" is 10:00 IST = 04:30 UTC
    now = datetime(2026, 6, 22, 4, 30, 0, tzinfo=timezone.utc)  # 10:00 IST

    doc = _make_token_doc(issued_at, expires_at, uid="USER1", actid="ACC1")
    status = _status_with_doc(doc, now)

    assert status["connected"] is True
    assert status["expired"] is False
    assert status["regenerate_after_6am"] is False
    assert status["uid"] == "USER1"
    assert status["actid"] == "ACC1"


def test_get_status_expired_by_time():
    """past expires_at → expired=True."""
    issued_at = datetime(2026, 6, 21, 3, 30, 0, tzinfo=timezone.utc)
    # expires yesterday's cutoff
    expires_at = datetime(2026, 6, 22, 0, 30, 0, tzinfo=timezone.utc)  # 06:00 IST 2026-06-22
    # "now" is AFTER expires_at
    now = datetime(2026, 6, 22, 1, 0, 0, tzinfo=timezone.utc)  # 06:30 IST 2026-06-22

    doc = _make_token_doc(issued_at, expires_at)
    status = _status_with_doc(doc, now)

    assert status["connected"] is True
    assert status["expired"] is True


def test_get_status_needs_6am_regen():
    """Token issued before today's 06:00 IST → regenerate_after_6am=True."""
    # Token issued at 01:00 IST = 19:30 UTC on 2026-06-21 (YESTERDAY before cutoff)
    # Actually: issued before TODAY's cutoff, meaning it was issued before 06:00 IST today.
    # today is 2026-06-22; 06:00 IST today = 00:30 UTC 2026-06-22
    # Issue token at 23:00 UTC 2026-06-21 = 04:30 IST 2026-06-22 (before cutoff)
    issued_at = datetime(2026, 6, 21, 23, 0, 0, tzinfo=timezone.utc)  # 04:30 IST 2026-06-22
    expires_at = datetime(2026, 6, 23, 0, 30, 0, tzinfo=timezone.utc)  # not expired yet
    # now = 07:00 IST 2026-06-22 = 01:30 UTC 2026-06-22 (after the cutoff)
    now = datetime(2026, 6, 22, 1, 30, 0, tzinfo=timezone.utc)  # 07:00 IST 2026-06-22

    doc = _make_token_doc(issued_at, expires_at)
    status = _status_with_doc(doc, now)

    assert status["connected"] is True
    assert status["regenerate_after_6am"] is True


def test_get_status_not_needs_regen_if_issued_after_cutoff():
    """Token issued at 07:00 IST today → issued AFTER today's 06:00 IST cutoff → no regen needed."""
    # 07:00 IST 2026-06-22 = 01:30 UTC 2026-06-22
    issued_at = datetime(2026, 6, 22, 1, 30, 0, tzinfo=timezone.utc)  # 07:00 IST
    expires_at = datetime(2026, 6, 23, 0, 30, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 22, 5, 0, 0, tzinfo=timezone.utc)  # 10:30 IST

    doc = _make_token_doc(issued_at, expires_at)
    status = _status_with_doc(doc, now)

    assert status["regenerate_after_6am"] is False


def test_get_status_static_ip_fields(monkeypatch):
    """static_ip_primary/secondary come from env vars."""
    monkeypatch.setenv("FLATTRADE_PRIMARY_IP", "1.2.3.4")
    monkeypatch.setenv("FLATTRADE_SECONDARY_IP", "5.6.7.8")
    now = datetime(2026, 6, 22, 5, 0, 0, tzinfo=timezone.utc)
    status = _status_with_doc(None, now)
    assert status["static_ip_primary"] == "1.2.3.4"
    assert status["static_ip_secondary"] == "5.6.7.8"


# ---------------------------------------------------------------------------
# save_token / get_token / disconnect — fake DB
# ---------------------------------------------------------------------------

def _run_with_fake_db(coro_factory):
    """Run an async function with a shared fake_db injected via patch."""
    fake_db = _FakeDB()

    async def runner():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            return await coro_factory(fake_db)

    return run(runner())


def test_save_and_get_token():
    from app.live import flattrade_token

    async def coro(fake_db):
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            now = datetime(2026, 6, 22, 4, 0, 0, tzinfo=timezone.utc)  # 09:30 IST
            await flattrade_token.save_token("default", "JKEY1", "USER1", "ACC1", now=now)
            token = await flattrade_token.get_token("default")
        return token

    fake_db = _FakeDB()

    async def runner():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            now = datetime(2026, 6, 22, 4, 0, 0, tzinfo=timezone.utc)
            await flattrade_token.save_token("default", "JKEY1", "USER1", "ACC1", now=now)
            return await flattrade_token.get_token("default")

    token = run(runner())
    assert token == "JKEY1"


def test_save_token_sets_expires_at_tomorrow_cutoff():
    """expires_at should be next day's 06:00 IST when token saved after today's 06:00 IST."""
    from app.live import flattrade_token

    fake_db = _FakeDB()
    # Save at 09:00 IST 2026-06-22 = 03:30 UTC 2026-06-22
    now = datetime(2026, 6, 22, 3, 30, 0, tzinfo=timezone.utc)

    async def runner():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            await flattrade_token.save_token("default", "J", "U", "A", now=now)
        return fake_db.live_broker_tokens._docs[0]

    doc = run(runner())
    # expires_at should be 2026-06-23 00:30 UTC (06:00 IST tomorrow)
    exp = datetime.fromisoformat(doc["expires_at"].replace("Z", "+00:00"))
    if not exp.tzinfo:
        exp = exp.replace(tzinfo=timezone.utc)
    expected = datetime(2026, 6, 23, 0, 30, 0, tzinfo=timezone.utc)
    assert exp == expected


def test_disconnect_removes_token():
    from app.live import flattrade_token

    fake_db = _FakeDB()

    async def runner():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            now = datetime(2026, 6, 22, 4, 0, 0, tzinfo=timezone.utc)
            await flattrade_token.save_token("default", "JKEY1", "U", "A", now=now)
            removed = await flattrade_token.disconnect("default")
            token = await flattrade_token.get_token("default")
        return removed, token

    removed, token = run(runner())
    assert removed is True
    assert token is None


def test_disconnect_returns_false_if_no_doc():
    from app.live import flattrade_token
    fake_db = _FakeDB()

    async def runner():
        with patch("app.live.flattrade_token.get_db", return_value=fake_db):
            return await flattrade_token.disconnect("default")

    assert run(runner()) is False
