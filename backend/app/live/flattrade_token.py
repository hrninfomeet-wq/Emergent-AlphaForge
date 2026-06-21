"""Flattrade daily-token OAuth store — mirrors upstox_client.py patterns.

OAuth flow (redirect-URL, NOT QuickAuth/TOTP):
  1. Browser → https://auth.flattrade.in/?app_key=<API_KEY>
  2. Flattrade redirects to FLATTRADE_REDIRECT_URI?code=<request_code>
  3. Token exchange: POST https://authapi.flattrade.in/trade/apitoken
       JSON body: {api_key, request_code, api_secret: sha256(api_key+code+api_secret_raw)}
       Response:  {stat: "Ok", token: <jKey>, ...}

Token lifetime: Flattrade clears tokens ~5-6 AM IST daily.
Treat any token issued before today 06:00 IST as needing regen (expired for our purposes).

Verified against:
  - computeraidedautomation.com forum sample program (sha256 formula, URL)
  - openalgo/broker/flattrade/api/auth_api.py (authoritative open-source impl)
  - Indian-Algorithmic-Trading-Community/Flattrade/login/flattrade_get_api_token.py
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

from app.db import get_db

log = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"

# ---------------------------------------------------------------------------
# Env config
# ---------------------------------------------------------------------------

def _api_key() -> str:
    return os.environ.get("FLATTRADE_API_KEY", "")


def _api_secret() -> str:
    return os.environ.get("FLATTRADE_API_SECRET", "")


def _redirect_uri() -> str:
    return os.environ.get(
        "FLATTRADE_REDIRECT_URI",
        "http://127.0.0.1:8001/api/flattrade/auth/callback",
    )


def _primary_ip() -> str:
    return os.environ.get("FLATTRADE_PRIMARY_IP", "")


def _secondary_ip() -> str:
    return os.environ.get("FLATTRADE_SECONDARY_IP", "")


def is_configured() -> bool:
    """True iff FLATTRADE_API_KEY and FLATTRADE_API_SECRET are set."""
    return bool(_api_key() and _api_secret())


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def build_login_url() -> str:
    """Return the Flattrade OAuth authorize URL.

    Verified URL: https://auth.flattrade.in/?app_key=<API_KEY>
    (parameter name is ``app_key``, not ``client_id``).
    """
    return f"https://auth.flattrade.in/?app_key={_api_key()}"


def _compute_hash(api_key: str, request_code: str, api_secret_raw: str) -> str:
    """SHA-256 of (api_key + request_code + api_secret_raw), hex-encoded.

    Formula verified against openalgo auth_api.py and community sample code:
        hash_input = api_key + request_code + api_secret_raw
        api_secret_param = sha256(hash_input.encode()).hexdigest()
    """
    return hashlib.sha256(f"{api_key}{request_code}{api_secret_raw}".encode("utf-8")).hexdigest()


async def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """Exchange OAuth request_code for a Flattrade jKey token.

    Endpoint: POST https://authapi.flattrade.in/trade/apitoken
    Body (JSON): {api_key, request_code, api_secret: sha256_hash}
    Success response: {stat: "Ok", token: <jKey>, ...}
    Error response:   {stat: <other>, emsg: <reason>}

    Raises RuntimeError on HTTP error or stat != "Ok".
    """
    api_key = _api_key()
    api_secret_raw = _api_secret()
    if not api_key or not api_secret_raw:
        raise RuntimeError("Flattrade not configured: set FLATTRADE_API_KEY and FLATTRADE_API_SECRET")

    security_hash = _compute_hash(api_key, code, api_secret_raw)
    payload = {
        "api_key": api_key,
        "request_code": code,
        "api_secret": security_hash,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://authapi.flattrade.in/trade/apitoken",
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Flattrade token exchange failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    if data.get("stat") != "Ok":
        emsg = data.get("emsg", "unknown error")
        raise RuntimeError(f"Flattrade token exchange rejected: {emsg}")

    return data


# ---------------------------------------------------------------------------
# Token store — MongoDB collection: live_broker_tokens
# ---------------------------------------------------------------------------

_BROKER = "flattrade"

# Flattrade regenerates tokens ~5-6 AM IST each day.
# We treat anything issued before today 06:00 IST as needing regen.
_REGEN_HOUR_IST = 6       # 06:00 IST
_IST_OFFSET_HOURS = 5.5   # UTC+5:30


def _today_regen_cutoff_utc(now: Optional[datetime] = None) -> datetime:
    """Return today's 06:00 IST expressed in UTC.

    Any token whose ``issued_at`` is earlier than this cutoff must be
    regenerated — Flattrade has already cleared it server-side.
    """
    now_utc = now or datetime.now(timezone.utc)
    # Convert now to IST to find today's date in IST
    ist_offset = timedelta(hours=_IST_OFFSET_HOURS)
    now_ist = now_utc + ist_offset
    # Build today's 06:00 IST as a naive datetime, then convert back to UTC
    regen_ist = datetime(
        now_ist.year, now_ist.month, now_ist.day,
        _REGEN_HOUR_IST, 0, 0,
    )
    regen_utc = regen_ist - ist_offset
    return regen_utc.replace(tzinfo=timezone.utc)


async def save_token(
    user_id: str,
    jKey: str,
    uid: str,
    actid: str,
    *,
    now: Optional[datetime] = None,
) -> None:
    """Persist the Flattrade session token to MongoDB.

    Doc shape: {user, jKey, uid, actid, issued_at, expires_at}
    expires_at is set to tomorrow's 06:00 IST (when Flattrade will clear it).
    """
    db = get_db()
    now_utc = now or datetime.now(timezone.utc)

    # Token expires at NEXT 06:00 IST after it is issued.
    # Find today's cutoff; if we're already past it, add a day.
    cutoff_utc = _today_regen_cutoff_utc(now_utc)
    if now_utc >= cutoff_utc:
        # Token was obtained after today's 06:00 IST cutoff → valid until tomorrow 06:00 IST
        expires_at = cutoff_utc + timedelta(days=1)
    else:
        # Token obtained before today's cutoff (unusual — means we logged in before 6am)
        expires_at = cutoff_utc

    doc = {
        "user": user_id,
        "broker": _BROKER,
        "jKey": jKey,
        "uid": uid,
        "actid": actid,
        "issued_at": now_utc.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    await db.live_broker_tokens.update_one(
        {"user": user_id, "broker": _BROKER},
        {"$set": doc},
        upsert=True,
    )
    log.info(f"Flattrade token saved for user={user_id} uid={uid} expires={expires_at.isoformat()}")


async def get_token(user_id: str = DEFAULT_USER_ID) -> Optional[str]:
    """Return the stored jKey or None if not found."""
    db = get_db()
    doc = await db.live_broker_tokens.find_one(
        {"user": user_id, "broker": _BROKER}
    )
    if not doc:
        return None
    return doc.get("jKey")


async def get_status(
    user_id: str = DEFAULT_USER_ID,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return connection status for the Flattrade token.

    Returns
    -------
    dict with keys:
        connected            bool    True if a token doc exists
        expired              bool    True if past expires_at
        regenerate_after_6am bool    True if token was issued before today's 06:00 IST regen cutoff
        uid                  str|None
        actid                str|None
        static_ip_primary    str     from FLATTRADE_PRIMARY_IP env
        static_ip_secondary  str     from FLATTRADE_SECONDARY_IP env
        configured           bool
    """
    db = get_db()
    doc = await db.live_broker_tokens.find_one(
        {"user": user_id, "broker": _BROKER},
        {"_id": 0, "jKey": 0},
    )
    if not doc:
        return {
            "connected": False,
            "expired": False,
            "regenerate_after_6am": False,
            "uid": None,
            "actid": None,
            "static_ip_primary": _primary_ip(),
            "static_ip_secondary": _secondary_ip(),
            "configured": is_configured(),
        }

    now_utc = now or datetime.now(timezone.utc)

    # expired: past the stored expires_at
    expired = False
    expires_at_str = doc.get("expires_at")
    if expires_at_str:
        try:
            exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if not exp_dt.tzinfo:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            expired = now_utc >= exp_dt
        except Exception:
            expired = True  # fail-closed: treat unparseable as expired

    # regenerate_after_6am: token was issued before today's 06:00 IST regen cutoff
    regenerate_after_6am = False
    issued_at_str = doc.get("issued_at")
    if issued_at_str:
        try:
            issued_dt = datetime.fromisoformat(issued_at_str.replace("Z", "+00:00"))
            if not issued_dt.tzinfo:
                issued_dt = issued_dt.replace(tzinfo=timezone.utc)
            cutoff_utc = _today_regen_cutoff_utc(now_utc)
            # Token issued before today's 06:00 IST cutoff → server has already cleared it
            regenerate_after_6am = issued_dt < cutoff_utc
        except Exception:
            regenerate_after_6am = True  # fail-closed

    return {
        "connected": True,
        "expired": expired,
        "regenerate_after_6am": regenerate_after_6am,
        "uid": doc.get("uid"),
        "actid": doc.get("actid"),
        "static_ip_primary": _primary_ip(),
        "static_ip_secondary": _secondary_ip(),
        "configured": is_configured(),
    }


async def disconnect(user_id: str = DEFAULT_USER_ID) -> bool:
    """Delete the stored token for this user. Returns True if a doc was deleted."""
    db = get_db()
    res = await db.live_broker_tokens.delete_many({"user": user_id, "broker": _BROKER})
    return res.deleted_count > 0
