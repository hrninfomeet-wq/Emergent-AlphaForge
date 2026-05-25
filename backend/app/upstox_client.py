"""Upstox V3 client — OAuth + REST historical candles + expired options scaffold.

Designed to extend the Data Warehouse beyond yfinance's 30-day cap.
Reference: integration_playbook_expert_v2 playbook (in docs/).
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx
import pandas as pd

from app.db import get_db
from app.encryption import decrypt_str, encrypt_str

log = logging.getLogger(__name__)

# Default user_id for single-user deployments. Multi-user can extend later.
DEFAULT_USER_ID = "default"

# Upstox instrument keys for the indices we track
INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX",
}


def _base_url() -> str:
    return os.environ.get("UPSTOX_BASE_URL", "https://api.upstox.com")


def _client_id() -> str:
    return os.environ.get("UPSTOX_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("UPSTOX_CLIENT_SECRET", "")


def _redirect_uri() -> str:
    return os.environ.get("UPSTOX_REDIRECT_URI", "")


def is_configured() -> bool:
    return bool(_client_id() and _client_secret() and _redirect_uri())


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def build_login_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "state": state,
    }
    return f"{_base_url()}/v2/login/authorization/dialog?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """Exchange OAuth authorization code for access token."""
    data = {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{_base_url()}/v2/login/authorization/token",
            data=data,
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Upstox token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


async def save_token(user_id: str, token_payload: Dict[str, Any]) -> None:
    """Persist encrypted access token + metadata to MongoDB."""
    db = get_db()
    access_token = token_payload.get("access_token")
    if not access_token:
        raise ValueError("Token payload missing access_token")
    expires_in = token_payload.get("expires_in")
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    else:
        # Upstox tokens valid ~24h
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    safe_payload = {k: v for k, v in token_payload.items() if k not in ("access_token", "refresh_token")}
    doc = {
        "user_id": user_id,
        "provider": "upstox",
        "encrypted_access_token": encrypt_str(access_token),
        "expires_at": expires_at.isoformat(),
        "user_meta": safe_payload,  # email, broker, exchanges, products, order_types, user_type
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    refresh = token_payload.get("refresh_token")
    if refresh:
        doc["encrypted_refresh_token"] = encrypt_str(refresh)
    await db.upstox_tokens.update_one(
        {"user_id": user_id, "provider": "upstox"},
        {"$set": doc},
        upsert=True,
    )


async def get_token(user_id: str = DEFAULT_USER_ID) -> Optional[str]:
    """Return decrypted access token or None."""
    db = get_db()
    doc = await db.upstox_tokens.find_one({"user_id": user_id, "provider": "upstox"})
    if not doc:
        return None
    return decrypt_str(doc["encrypted_access_token"])


async def get_connection_status(user_id: str = DEFAULT_USER_ID) -> Dict[str, Any]:
    db = get_db()
    doc = await db.upstox_tokens.find_one(
        {"user_id": user_id, "provider": "upstox"},
        {"_id": 0, "encrypted_access_token": 0, "encrypted_refresh_token": 0},
    )
    if not doc:
        return {"connected": False, "configured": is_configured()}
    expires_at = doc.get("expires_at")
    expired = False
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            expired = exp_dt <= datetime.now(timezone.utc)
        except Exception:
            pass
    return {
        "connected": True,
        "configured": is_configured(),
        "expired": expired,
        "expires_at": expires_at,
        "connected_at": doc.get("connected_at"),
        "user_meta": doc.get("user_meta", {}),
    }


async def disconnect(user_id: str = DEFAULT_USER_ID) -> bool:
    db = get_db()
    res = await db.upstox_tokens.delete_many({"user_id": user_id, "provider": "upstox"})
    return res.deleted_count > 0


# ---------------------------------------------------------------------------
# REST: historical candles
# ---------------------------------------------------------------------------

async def _authenticated_get(url: str, user_id: str = DEFAULT_USER_ID) -> Dict[str, Any]:
    token = await get_token(user_id)
    if not token:
        raise RuntimeError("Upstox not connected. Please complete OAuth first.")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
    if resp.status_code == 401:
        raise RuntimeError("Upstox token expired or invalid. Please reconnect.")
    if resp.status_code == 429:
        raise RuntimeError("Upstox rate limit exceeded. Retry later.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Upstox API error ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


async def fetch_historical_1m(
    instrument: str,
    from_date: str,  # YYYY-MM-DD
    to_date: str,    # YYYY-MM-DD
    user_id: str = DEFAULT_USER_ID,
) -> pd.DataFrame:
    """Fetch 1-minute candles from Upstox V3 for an instrument over a date range.
    Upstox V3 endpoint: /v3/historical-candle/{instrument_key}/minutes/1/{to}/{from}
    Response: data.candles = [[ts_iso, open, high, low, close, volume, oi], ...]
    """
    instrument = instrument.upper()
    if instrument not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {instrument}")
    instrument_key = INSTRUMENT_KEYS[instrument]
    encoded = quote(instrument_key, safe="")
    url = f"{_base_url()}/v3/historical-candle/{encoded}/minutes/1/{to_date}/{from_date}"
    log.info(f"Upstox fetch 1m {instrument} {from_date}→{to_date}")
    data = await _authenticated_get(url, user_id)
    candles = (data.get("data") or {}).get("candles") or []
    if not candles:
        return pd.DataFrame()
    # Build DataFrame in the SAME shape as yfinance source returns
    rows = []
    for c in candles:
        # Upstox returns: [ts_iso, open, high, low, close, volume, oi]
        ts_iso = c[0]
        try:
            dt = pd.to_datetime(ts_iso, utc=True)
        except Exception:
            continue
        rows.append({
            "instrument": instrument,
            "ts": int(dt.value // 10**6),  # ms epoch UTC
            "datetime": ts_iso,
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5] or 0),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).drop_duplicates(subset=["instrument", "ts"]).sort_values("ts").reset_index(drop=True)
    log.info(f"  fetched {len(df)} candles")
    return df


async def fetch_historical_1m_chunked(
    instrument: str,
    from_date: str,
    to_date: str,
    max_days_per_call: int = 7,
    user_id: str = DEFAULT_USER_ID,
) -> pd.DataFrame:
    """Fetch a long range by chunking into smaller windows (Upstox limits per call)."""
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    if start > end:
        raise ValueError("from_date > to_date")
    frames: List[pd.DataFrame] = []
    cur = start
    import asyncio
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days_per_call), end)
        try:
            df = await fetch_historical_1m(instrument, cur.isoformat(), chunk_end.isoformat(), user_id)
            if not df.empty:
                frames.append(df)
        except Exception as e:
            log.warning(f"chunk {cur}→{chunk_end} failed: {e}")
        # rate limit cushion (50 req/sec is the Upstox limit; we're well under)
        await asyncio.sleep(0.15)
        cur = chunk_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["instrument", "ts"]).sort_values("ts").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Expired options (scaffold — requires Upstox Plus)
# ---------------------------------------------------------------------------

async def fetch_expiries(underlying: str, user_id: str = DEFAULT_USER_ID) -> List[str]:
    """Get expiry dates for an underlying. Used to build the expired-options chain in Phase 4c."""
    underlying = underlying.upper()
    if underlying not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported underlying: {underlying}")
    encoded = quote(INSTRUMENT_KEYS[underlying], safe="")
    url = f"{_base_url()}/v2/expired-instruments/expiries?instrument_key={encoded}"
    data = await _authenticated_get(url, user_id)
    return (data.get("data") or [])


async def fetch_expired_option_contracts(
    underlying: str,
    expiry: str,
    user_id: str = DEFAULT_USER_ID,
) -> List[Dict[str, Any]]:
    underlying = underlying.upper()
    if underlying not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported underlying: {underlying}")
    encoded = quote(INSTRUMENT_KEYS[underlying], safe="")
    url = f"{_base_url()}/v2/expired-instruments/option/contract?instrument_key={encoded}&expiry_date={expiry}"
    data = await _authenticated_get(url, user_id)
    return (data.get("data") or [])
