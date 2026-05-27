"""Upstox V3 client — OAuth + REST historical candles + expired options scaffold.

Designed to extend the Data Warehouse beyond yfinance's 30-day cap.
Reference: integration_playbook_expert_v2 playbook (in docs/).
"""
from __future__ import annotations
import base64
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx
import pandas as pd

from app.db import get_db
from app.encryption import decrypt_str, encrypt_str
from app.instruments import INSTRUMENT_KEYS, UNDERLYING_META
from app.option_candles import candles_to_df

log = logging.getLogger(__name__)

# Default user_id for single-user deployments. Multi-user can extend later.
DEFAULT_USER_ID = "default"

_META_SENSITIVE_TOKENS = (
    "token",
    "secret",
    "access",
    "refresh",
    "auth",
    "jwt",
    "bearer",
)


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


def _jwt_expiry(access_token: Optional[str]) -> Optional[datetime]:
    if not access_token:
        return None
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        if exp:
            return datetime.fromtimestamp(int(exp), timezone.utc)
    except Exception:
        return None
    return None


def resolve_token_expiry(token_payload: Dict[str, Any], now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    expires_in = token_payload.get("expires_in")
    if expires_in:
        return now + timedelta(seconds=int(expires_in))
    jwt_expiry = _jwt_expiry(token_payload.get("access_token"))
    if jwt_expiry:
        return jwt_expiry
    # Upstox standard OAuth tokens expire around the next early-morning cycle.
    # Use 24h as a final fallback only when neither API metadata nor JWT claims exist.
    return now + timedelta(hours=24)


def sanitize_user_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Remove token-like fields before returning user metadata to clients."""
    safe: Dict[str, Any] = {}
    for key, value in (meta or {}).items():
        key_l = str(key).lower()
        if any(marker in key_l for marker in _META_SENSITIVE_TOKENS):
            continue
        safe[key] = value
    return safe


async def save_token(user_id: str, token_payload: Dict[str, Any]) -> None:
    """Persist encrypted access token + metadata to MongoDB."""
    db = get_db()
    access_token = token_payload.get("access_token")
    if not access_token:
        raise ValueError("Token payload missing access_token")
    expires_at = resolve_token_expiry(token_payload)
    safe_payload = sanitize_user_meta(
        {k: v for k, v in token_payload.items() if k not in ("access_token", "refresh_token")}
    )
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
        "user_meta": sanitize_user_meta(doc.get("user_meta", {})),
    }


async def disconnect(user_id: str = DEFAULT_USER_ID) -> bool:
    db = get_db()
    res = await db.upstox_tokens.delete_many({"user_id": user_id, "provider": "upstox"})
    return res.deleted_count > 0


# ---------------------------------------------------------------------------
# REST: authenticated GET helper
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


# ---------------------------------------------------------------------------
# REST: live market quote
# ---------------------------------------------------------------------------

def _float_value(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_market_quote(payload: Dict[str, Any], instrument: str) -> Dict[str, Any]:
    """Normalize Upstox full market quote into a safe app-level snapshot."""
    underlying = str(instrument or "").upper()

    data = payload.get("data") or {}
    if not data:
        raise RuntimeError(f"No market quote returned for {underlying}")

    raw_key, quote = next(iter(data.items()))
    quote = quote or {}
    ohlc = quote.get("ohlc") or {}
    fallback_key = INSTRUMENT_KEYS.get(underlying, str(instrument or raw_key))
    return {
        "underlying": underlying,
        "instrument_key": str(quote.get("instrument_token") or fallback_key),
        "raw_key": str(raw_key),
        "symbol": str(quote.get("symbol") or ""),
        "last_price": _float_value(quote.get("last_price")),
        "timestamp": str(quote.get("timestamp") or ""),
        "last_trade_time": str(quote.get("last_trade_time") or ""),
        "volume": _int_value(quote.get("volume")),
        "oi": _int_value(quote.get("oi")),
        "net_change": _float_value(quote.get("net_change")),
        "ohlc": {
            "open": _float_value(ohlc.get("open")),
            "high": _float_value(ohlc.get("high")),
            "low": _float_value(ohlc.get("low")),
            "close": _float_value(ohlc.get("close")),
        },
        "source": "upstox_market_quote",
    }


async def fetch_market_quote(instrument: str, user_id: str = DEFAULT_USER_ID) -> Dict[str, Any]:
    """Fetch a live full market quote snapshot for a supported index."""
    underlying = str(instrument or "").upper()
    if underlying not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported instrument: {instrument}")
    return await fetch_market_quote_by_key(INSTRUMENT_KEYS[underlying], user_id=user_id, instrument=underlying)


async def fetch_market_quote_by_key(
    instrument_key: str,
    user_id: str = DEFAULT_USER_ID,
    instrument: str = "",
) -> Dict[str, Any]:
    """Fetch a full market quote snapshot for a concrete Upstox instrument key."""
    if not instrument_key:
        raise ValueError("instrument_key is required")
    encoded = quote(instrument_key, safe="")
    url = f"{_base_url()}/v2/market-quote/quotes?instrument_key={encoded}"
    data = await _authenticated_get(url, user_id)
    return normalize_market_quote(data, instrument or instrument_key)


async def fetch_market_data_feed_authorize_url(user_id: str = DEFAULT_USER_ID) -> str:
    """Return a one-time Upstox V3 market-data WebSocket URL."""
    url = f"{_base_url()}/v3/feed/market-data-feed/authorize"
    data = await _authenticated_get(url, user_id)
    redirect_uri = ((data.get("data") or {}).get("authorized_redirect_uri") or "").strip()
    if not redirect_uri.startswith("wss://"):
        raise RuntimeError("Upstox did not return a valid market-data WebSocket URL")
    return redirect_uri


# ---------------------------------------------------------------------------
# REST: historical candles
# ---------------------------------------------------------------------------


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


async def fetch_historical_1m_for_key(
    instrument_key: str,
    from_date: str,
    to_date: str,
    user_id: str = DEFAULT_USER_ID,
    contract: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Fetch 1-minute candles for a concrete Upstox instrument key."""
    if _is_expired_instrument_key(instrument_key, contract):
        return await fetch_expired_historical_1m_for_key(
            instrument_key,
            from_date,
            to_date,
            user_id=user_id,
            contract=contract,
        )
    encoded = quote(instrument_key, safe="")
    url = f"{_base_url()}/v3/historical-candle/{encoded}/minutes/1/{to_date}/{from_date}"
    log.info(f"Upstox fetch 1m {instrument_key} {from_date}→{to_date}")
    data = await _authenticated_get(url, user_id)
    candles = (data.get("data") or {}).get("candles") or []
    return candles_to_df(candles, instrument_key=instrument_key, contract=contract)


def _is_expired_instrument_key(instrument_key: str, contract: Optional[Dict[str, Any]] = None) -> bool:
    if str((contract or {}).get("source") or "") == "expired_option_contract":
        return True
    parts = str(instrument_key or "").split("|")
    if len(parts) < 3:
        return False
    try:
        datetime.strptime(parts[-1], "%d-%m-%Y")
        return True
    except ValueError:
        return False


async def fetch_expired_historical_1m_for_key(
    instrument_key: str,
    from_date: str,
    to_date: str,
    user_id: str = DEFAULT_USER_ID,
    contract: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Fetch 1-minute candles for an expired Upstox instrument key."""
    encoded = quote(instrument_key, safe="")
    url = f"{_base_url()}/v2/expired-instruments/historical-candle/{encoded}/1minute/{to_date}/{from_date}"
    log.info(f"Upstox fetch expired 1m {instrument_key} {from_date}→{to_date}")
    data = await _authenticated_get(url, user_id)
    candles = (data.get("data") or {}).get("candles") or []
    return candles_to_df(candles, instrument_key=instrument_key, contract=contract)


async def fetch_historical_1m_for_key_chunked(
    instrument_key: str,
    from_date: str,
    to_date: str,
    max_days_per_call: int = 7,
    user_id: str = DEFAULT_USER_ID,
    contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch option/index candles by concrete instrument key and report partial failures."""
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    if start > end:
        raise ValueError("from_date > to_date")

    frames: List[pd.DataFrame] = []
    failed_chunks: List[Dict[str, str]] = []
    chunk_days = max(1, int(max_days_per_call or 1))
    cur = start
    import asyncio
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days), end)
        try:
            df = await fetch_historical_1m_for_key(
                instrument_key,
                cur.isoformat(),
                chunk_end.isoformat(),
                user_id=user_id,
                contract=contract,
            )
            if not df.empty:
                frames.append(df)
        except Exception as e:
            log.warning(f"option chunk {instrument_key} {cur}→{chunk_end} failed: {e}")
            failed_chunks.append({"from_date": cur.isoformat(), "to_date": chunk_end.isoformat(), "error": str(e)[:300]})
        await asyncio.sleep(0.15)
        cur = chunk_end + timedelta(days=1)

    if frames:
        df = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["instrument_key", "ts"])
            .sort_values("ts")
            .reset_index(drop=True)
        )
    else:
        df = pd.DataFrame()
    return {"df": df, "failed_chunks": failed_chunks}


# ---------------------------------------------------------------------------
# REST: option contracts
# ---------------------------------------------------------------------------

def _first_value(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    number = _float_or_none(value)
    return int(number) if number is not None else None


def normalize_option_contract(
    contract: Dict[str, Any],
    underlying: str,
    source: str = "option_contract",
) -> Optional[Dict[str, Any]]:
    underlying = underlying.upper()
    if underlying not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported underlying: {underlying}")

    side = str(_first_value(contract, "instrument_type", "option_type", "side") or "").upper()
    instrument_key = _first_value(contract, "instrument_key", "instrumentKey", "expired_instrument_key", "expiredInstrumentKey")
    strike = _float_or_none(_first_value(contract, "strike_price", "strike", "strikePrice"))
    if side not in ("CE", "PE") or not instrument_key or strike is None:
        return None

    return {
        "underlying": underlying,
        "underlying_key": _first_value(contract, "underlying_key", "underlyingKey") or INSTRUMENT_KEYS[underlying],
        "instrument_key": str(instrument_key),
        "exchange_token": str(_first_value(contract, "exchange_token", "exchangeToken") or ""),
        "trading_symbol": str(_first_value(contract, "trading_symbol", "tradingSymbol", "symbol") or ""),
        "expiry_date": str(_first_value(contract, "expiry", "expiry_date", "expiryDate") or ""),
        "side": side,
        "strike": strike,
        "lot_size": _int_or_none(_first_value(contract, "lot_size", "lotSize", "minimum_lot")) or UNDERLYING_META[underlying]["lot_size"],
        "exchange": str(_first_value(contract, "exchange", "exchange_name", "exchangeName") or ""),
        "segment": str(_first_value(contract, "segment") or ""),
        "weekly": bool(_first_value(contract, "weekly")),
        "source": source,
    }


def normalize_option_contracts(
    contracts: List[Dict[str, Any]],
    underlying: str,
    source: str = "option_contract",
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for contract in contracts or []:
        item = normalize_option_contract(contract, underlying, source=source)
        if item:
            normalized.append(item)
    return sorted(normalized, key=lambda c: (c["expiry_date"], c["strike"], c["side"], c["instrument_key"]))


async def fetch_option_contracts(
    underlying: str,
    expiry: Optional[str] = None,
    user_id: str = DEFAULT_USER_ID,
) -> List[Dict[str, Any]]:
    underlying = underlying.upper()
    if underlying not in INSTRUMENT_KEYS:
        raise ValueError(f"Unsupported underlying: {underlying}")
    encoded = quote(INSTRUMENT_KEYS[underlying], safe="")
    url = f"{_base_url()}/v2/option/contract?instrument_key={encoded}"
    if expiry:
        url += f"&expiry_date={quote(expiry, safe='')}"
    data = await _authenticated_get(url, user_id)
    return normalize_option_contracts(data.get("data") or [], underlying, source="current_option_contract")


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
    return normalize_option_contracts(data.get("data") or [], underlying, source="expired_option_contract")
