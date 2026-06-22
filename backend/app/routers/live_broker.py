"""Read-only Flattrade broker routes + Live-broker data endpoints.

Mirrors the Upstox auth/status patterns in app/routers/broker.py.
All routes are READ-ONLY or AUTH-ONLY — no order-placing path exists here.
Routes must not crash when not connected: return 400/empty, never 500-by-exception.

Routes
------
GET  /flattrade/status                  — token connection status
GET  /flattrade/auth/start              — return login URL (400 if not configured)
GET  /flattrade/auth/callback?code=...  — exchange code, save token, redirect to frontend
POST /flattrade/disconnect              — delete the stored token

GET  /live-broker/positions             — broker position book (real API)
GET  /live-broker/orders                — broker order book (real API)
GET  /live-broker/trades                — broker trade book (real API)
GET  /live-broker/limits                — broker account limits / margin (real API)
GET  /live-broker/reconcile             — reconcile report (broker vs empty internal state)
GET  /live-broker/symbol/resolve        — preview Noren tsym resolution for a contract
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.live import flattrade_token
from app.live.flattrade_token import (
    DEFAULT_USER_ID,
    build_login_url,
    disconnect,
    exchange_code_for_token,
    get_status,
    get_token,
    is_configured,
    save_token,
)
from app.live.flattrade_client import FlattradeClient
from app.live.reconcile import reconcile
from app.live.flattrade_symbol import SymbolResolutionError, resolve

log = logging.getLogger(__name__)

api = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRONTEND_POST_AUTH_URL = lambda: os.environ.get("FRONTEND_POST_AUTH_URL", "/warehouse")


async def _get_client() -> FlattradeClient:
    """Return a FlattradeClient for the default user's stored token.

    Raises HTTPException(400) if no token is stored — never raises 500 from
    missing auth state.
    """
    doc = await _get_token_doc()
    return FlattradeClient(
        jKey=doc["jKey"],
        uid=doc["uid"],
        actid=doc["actid"],
    )


async def _get_token_doc() -> dict:
    """Return the raw token doc for the default user.

    Raises HTTPException(400) if missing.
    """
    from app.db import get_db
    db = get_db()
    doc = await db.live_broker_tokens.find_one(
        {"user": DEFAULT_USER_ID, "broker": "flattrade"},
    )
    if not doc:
        raise HTTPException(400, "Flattrade not connected. Complete OAuth at /flattrade/auth/start.")
    return doc


# ---------------------------------------------------------------------------
# Auth / status routes (mirror /upstox/... pattern from broker.py)
# ---------------------------------------------------------------------------

@api.get("/flattrade/status")
async def flattrade_status():
    """Return Flattrade token connection status (never raises; no-token = connected:False)."""
    try:
        return await get_status(DEFAULT_USER_ID)
    except Exception as exc:
        log.exception("flattrade_status failed")
        # Return a safe degraded response rather than a 500
        return {
            "connected": False,
            "expired": False,
            "regenerate_after_6am": False,
            "uid": None,
            "actid": None,
            "static_ip_primary": "",
            "static_ip_secondary": "",
            "configured": is_configured(),
            "error": str(exc)[:200],
        }


@api.get("/flattrade/auth/start")
async def flattrade_auth_start():
    """Return the Flattrade OAuth login URL.

    Returns 400 if FLATTRADE_API_KEY / FLATTRADE_API_SECRET are not set.
    """
    if not is_configured():
        raise HTTPException(
            400,
            "Flattrade credentials not configured. "
            "Set FLATTRADE_API_KEY and FLATTRADE_API_SECRET in backend/.env",
        )
    url = build_login_url()
    return {"login_url": url}


@api.get("/flattrade/auth/callback")
async def flattrade_auth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
):
    """Browser is redirected here by Flattrade after login.

    Exchange code for token, save it, then redirect to the frontend.
    Uses ?flattrade_connected=1 / ?flattrade_error=<reason> query params to
    signal outcome to the frontend (mirrors Upstox's ?upstox_connected=1 pattern).
    """
    frontend_url = _FRONTEND_POST_AUTH_URL()
    if error:
        return RedirectResponse(f"{frontend_url}?flattrade_error={error}")
    if not code:
        return RedirectResponse(f"{frontend_url}?flattrade_error=missing_code")
    try:
        payload = await exchange_code_for_token(code)
        # payload shape: {stat: "Ok", token: <jKey>, uid: <uid>, actid: <actid>, ...}
        jKey = payload.get("token") or payload.get("jKey")
        uid = payload.get("uid", "")
        actid = payload.get("actid", uid)  # actid defaults to uid for single-account users
        if not jKey:
            return RedirectResponse(f"{frontend_url}?flattrade_error=missing_token_in_response")
        await save_token(DEFAULT_USER_ID, jKey=jKey, uid=uid, actid=actid)
        return RedirectResponse(f"{frontend_url}?flattrade_connected=1")
    except Exception as exc:
        log.exception("flattrade token exchange failed")
        return RedirectResponse(f"{frontend_url}?flattrade_error={str(exc)[:200]}")


@api.post("/flattrade/disconnect")
async def flattrade_disconnect():
    """Delete the stored Flattrade token for the default user."""
    deleted = await disconnect(DEFAULT_USER_ID)
    return {"disconnected": deleted}


# ---------------------------------------------------------------------------
# Live-broker data routes (hit the real Flattrade API; require a stored token)
# ---------------------------------------------------------------------------

@api.get("/live-broker/positions")
async def live_broker_positions():
    """Return the broker net position book. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        positions = await client.position_book()
        return {"positions": positions, "count": len(positions)}
    except Exception as exc:
        log.exception("live_broker_positions failed")
        raise HTTPException(400, f"Flattrade position_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/orders")
async def live_broker_orders():
    """Return the broker order book. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        orders = await client.order_book()
        return {"orders": orders, "count": len(orders)}
    except Exception as exc:
        log.exception("live_broker_orders failed")
        raise HTTPException(400, f"Flattrade order_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/trades")
async def live_broker_trades():
    """Return the broker trade book (filled orders). Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        trades = await client.trade_book()
        return {"trades": trades, "count": len(trades)}
    except Exception as exc:
        log.exception("live_broker_trades failed")
        raise HTTPException(400, f"Flattrade trade_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/limits")
async def live_broker_limits():
    """Return broker account limits / margin. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        lims = await client.limits()
        return lims
    except Exception as exc:
        log.exception("live_broker_limits failed")
        raise HTTPException(400, f"Flattrade limits error: {str(exc)[:300]}") from exc


@api.get("/live-broker/reconcile")
async def live_broker_reconcile():
    """Fetch broker orders+positions and return a reconcile diff report.

    Internal state is empty for now (L0 — no live_orders/live_positions store yet).
    The report will flag any broker-side open orders/positions as unknown_broker_*
    mismatches, which is the correct fail-closed behaviour until L2.3 wires the
    full engine state.

    Returns 400 if not connected.
    """
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        broker_orders = await client.order_book()
        broker_positions = await client.position_book()
    except Exception as exc:
        log.exception("live_broker_reconcile: broker fetch failed")
        raise HTTPException(400, f"Flattrade fetch error: {str(exc)[:300]}") from exc

    # L0: internal state is empty — no live_orders / live_positions yet.
    report = reconcile(
        internal_orders=[],
        internal_positions=[],
        broker_orders=broker_orders,
        broker_positions=broker_positions,
    )
    return report


@api.get("/live-broker/symbol/resolve")
async def live_broker_symbol_resolve(
    underlying: str = Query(..., description="e.g. NIFTY, BANKNIFTY, SENSEX"),
    strike: float = Query(..., description="Strike price, e.g. 25000"),
    side: str = Query(..., description="CE or PE"),
    expiry: str = Query(..., description="ISO date YYYY-MM-DD"),
    lot_size: Optional[int] = Query(None, description="Expected lot size; auto-filled from spec if omitted"),
):
    """Preview Noren symbol resolution for a given option contract.

    Calls SearchScrip on the real Flattrade API via the stored token and returns
    {tsym, token, exch, lot_size} or a 400 with the SymbolResolutionError message.
    Returns 400 if not connected or if the symbol cannot be resolved unambiguously.
    """
    from app.live.flattrade_symbol import UNDERLYING_SPEC

    # Auto-fill lot_size from the known spec if not provided
    if lot_size is None:
        spec = UNDERLYING_SPEC.get(underlying.strip().upper())
        if spec is None:
            raise HTTPException(
                400,
                f"Unknown underlying {underlying!r}. Supported: {sorted(UNDERLYING_SPEC)}",
            )
        lot_size = spec[2]  # UNDERLYING_SPEC is (exch, symname, lot_size)

    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc

    # Build a contract dict matching the flattrade_symbol.resolve() expected shape
    contract = {
        "underlying": underlying,
        "strike": strike,
        "side": side,
        "expiry_date": expiry,
        "lot_size": lot_size,
    }

    # search_scrip is async; resolve() calls search_fn synchronously.
    # Wrap it so the async call is awaited before returning to the sync resolver.
    # We pre-fetch the results and provide a sync wrapper over the cached list.
    import asyncio

    async def _async_search(exch: str, query: str):
        return await client.search_scrip(exch, query)

    # Pre-run the search so resolve() can call a sync wrapper
    try:
        underlying_upper = underlying.strip().upper()
        from app.live.flattrade_symbol import UNDERLYING_SPEC as _SPEC
        if underlying_upper not in _SPEC:
            raise HTTPException(400, f"Unknown underlying {underlying!r}")
        exch = _SPEC[underlying_upper][0]
        strike_val = float(strike)
        query = (
            f"{underlying_upper} {int(strike_val)}"
            if strike_val == int(strike_val)
            else f"{underlying_upper} {strike_val}"
        )
        scrip_rows = await client.search_scrip(exch, query)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"SearchScrip error: {str(exc)[:300]}") from exc

    # Now call resolve() with a sync wrapper over the pre-fetched rows
    def _sync_search(exch: str, q: str):
        # Returns the already-fetched rows (same query)
        return scrip_rows

    try:
        result = resolve(contract, search_fn=_sync_search)
        return result
    except SymbolResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("symbol resolve unexpected error")
        raise HTTPException(400, f"Symbol resolution error: {str(exc)[:300]}") from exc


# ---------------------------------------------------------------------------
# Dry-run route (L1.3) — NEVER transmits; no place_order/cancel_order call
# ---------------------------------------------------------------------------

class _DryRunBody(BaseModel):
    contract: Dict[str, Any]
    side: str                          # "B" or "S"
    order_kind: str                    # "entry" | "exit" | "stop"
    lots: int
    ref_ltp: float
    band_pct: float
    fat_finger_cap: int
    levels: Dict[str, Any] = {}
    buffer_pct: Optional[float] = None


@api.post("/live-broker/order/dry-run")
async def live_order_dry_run(body: _DryRunBody):
    """Build an OrderIntent and run all safety checks WITHOUT placing any order.

    Returns the jdata dict that WOULD be sent, the per-check verdicts list, and
    the generated client_order_id.  This route NEVER calls place_order or
    cancel_order — it is strictly read-and-compute.

    Works even when the broker is not connected: symbol resolution will produce a
    failed 'symbol' verdict rather than a 500 when no token is stored.
    """
    from app.live.idempotency import new_client_order_id
    from app.live.order_builder import build_intent
    from app.live.flattrade_symbol import UNDERLYING_SPEC

    cid = new_client_order_id()

    # Build a sync search_fn adapter that calls the live client's async search_scrip.
    # Mirror the existing /live-broker/symbol/resolve pattern: pre-fetch results via
    # await, then wrap in a sync closure so order_builder's sync search_fn contract
    # is satisfied.  If the client cannot be obtained (not connected), we fall back
    # to a sync fn that always returns [] so that the symbol verdict reports the
    # failure gracefully rather than raising a 500.
    underlying = str(body.contract.get("underlying") or "").strip().upper()
    strike = body.contract.get("strike")

    pre_fetched_rows: List[Dict[str, Any]] = []
    fetch_error: Optional[str] = None

    try:
        client = await _get_client()
        spec = UNDERLYING_SPEC.get(underlying)
        if spec is not None and strike is not None:
            exch = spec[0]
            strike_val = float(strike)
            query = (
                f"{underlying} {int(strike_val)}"
                if strike_val == int(strike_val)
                else f"{underlying} {strike_val}"
            )
            pre_fetched_rows = await client.search_scrip(exch, query)
    except HTTPException:
        # Not connected — let the symbol verdict report this
        fetch_error = "Flattrade not connected; symbol resolution will fail"
    except Exception as exc:
        fetch_error = f"SearchScrip error: {str(exc)[:200]}"

    def _sync_search(exch: str, q: str) -> List[Dict[str, Any]]:
        # Returns the already-fetched rows (same single-query pattern as symbol/resolve)
        return pre_fetched_rows

    intent, verdicts = build_intent(
        body.contract,
        side=body.side,
        order_kind=body.order_kind,
        lots=body.lots,
        ref_ltp=body.ref_ltp,
        band_pct=body.band_pct,
        fat_finger_cap=body.fat_finger_cap,
        levels=body.levels,
        client_order_id=cid,
        buffer_pct=body.buffer_pct,
        search_fn=_sync_search,
    )

    # If the broker isn't connected, surface it as a note on the first verdict
    if fetch_error and verdicts and verdicts[0]["check"] == "symbol" and not verdicts[0]["ok"]:
        verdicts[0]["detail"] = f"{fetch_error}; {verdicts[0]['detail']}"

    # Build jdata only when an intent was produced; otherwise would_send is None.
    would_send: Optional[Dict[str, Any]] = None
    if intent is not None:
        # We need uid/actid for to_jdata; try to get them from the stored token.
        # Fall back to empty strings — the dry-run is inspection-only, not submission.
        uid = ""
        actid = ""
        try:
            doc = await _get_token_doc()
            uid = doc.get("uid", "")
            actid = doc.get("actid", uid)
        except HTTPException:
            pass
        would_send = intent.to_jdata(uid=uid, actid=actid)

    return {
        "would_send": would_send,
        "verdicts": verdicts,
        "client_order_id": cid,
    }
