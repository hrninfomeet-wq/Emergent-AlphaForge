"""Broker routes: Upstox auth/status/quotes, market header, WS stream, live candles.

Moved verbatim from backend/server.py (quality-hardening Slice C).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

import secrets
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, RedirectResponse

from app.db import get_db, serialize_doc
from app.market_header import build_market_header_snapshot
from app.live_option_universe import build_live_option_universe
from app import upstox_client

from app.runtime import (
    _OAUTH_STATES,
    _default_stream_instrument_keys,
    _parse_underlyings_query,
    _trigger_autoupdate,
    feed_supervisor_state,
    live_candle_roller,
    live_exit_monitor,
    log,
    upstox_stream_manager,
)

from app.schemas import UpstoxOptionStreamRestartReq, UpstoxStreamStartReq

api = APIRouter()


@api.get("/market/header")
async def market_header_snapshot():
    """Read the persistent terminal header quote snapshot."""
    return await build_market_header_snapshot(latest_ticks=upstox_stream_manager.latest_tick_map())


@api.get("/market/header/stream")
async def market_header_sse(request: Request):
    """Server-Sent Events feed of market header snapshots.

    Pushes a snapshot:
      - Immediately on connect
      - Whenever any subscribed Upstox WS tick arrives (debounced to ~10/s max per client)
      - As a heartbeat every 15s if no tick fires (so proxies do not close the connection)
    Falls back to client-side polling if SSE is unsupported or the WS stream is offline.
    """

    async def event_source():
        queue = upstox_stream_manager.subscribe(max_queue=128)
        try:
            # Initial snapshot so the UI paints immediately.
            snapshot = await build_market_header_snapshot(
                latest_ticks=upstox_stream_manager.latest_tick_map()
            )
            yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"

            # Debounce: at most one snapshot per `min_interval_s` to avoid hammering the client
            # when 10+ instruments tick simultaneously.
            min_interval_s = 0.1
            last_emit = asyncio.get_event_loop().time()

            while True:
                if await request.is_disconnected():
                    break
                # Wait for a tick or heartbeat timeout (15s).
                try:
                    await asyncio.wait_for(queue.get(), timeout=15.0)
                    # Drain any other ticks queued during the same instant.
                    drained = 0
                    while drained < 32:
                        try:
                            queue.get_nowait()
                            drained += 1
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies/load-balancers from closing the connection.
                    yield ": heartbeat\n\n"
                    continue

                now = asyncio.get_event_loop().time()
                wait = (last_emit + min_interval_s) - now
                if wait > 0:
                    await asyncio.sleep(wait)
                snapshot = await build_market_header_snapshot(
                    latest_ticks=upstox_stream_manager.latest_tick_map()
                )
                yield f"event: snapshot\ndata: {json.dumps(snapshot, default=str)}\n\n"
                last_emit = asyncio.get_event_loop().time()
        finally:
            upstox_stream_manager.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for instant push
            "Connection": "keep-alive",
        },
    )


@api.get("/live-candles/status")
async def live_candle_roller_status():
    """Return the live tick-to-OHLC roller status: tick counts, active buckets, last error."""
    return serialize_doc(live_candle_roller.status())


@api.get("/live-feed/health")
async def live_feed_health_endpoint():
    """Truthful live-feed health: is the pipeline (token -> stream -> roller ->
    fresh candles_1m) actually delivering, or what's blocking it?"""
    from datetime import datetime, timezone, timedelta
    from app.live_feed_health import compute_feed_health
    from app.nse_calendar import is_trading_day
    db = get_db()
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    token = await upstox_client.get_connection_status()
    roller_status = live_candle_roller.status()
    roller_started_ms = None
    started_at = roller_status.get("started_at")
    if started_at:
        try:
            roller_started_ms = int(datetime.fromisoformat(
                str(started_at).replace("Z", "+00:00")).timestamp() * 1000)
        except (ValueError, TypeError):
            roller_started_ms = None
    latest = await db.candles_1m.find_one({"instrument": "NIFTY"}, {"_id": 0, "ts": 1},
                                          sort=[("ts", -1)])
    last_candle_ts = int(latest["ts"]) if latest and latest.get("ts") else None
    sup = feed_supervisor_state()
    health = compute_feed_health(
        now_ist=ist_now, now_ms=now_ms,
        is_trading_day=is_trading_day(ist_now.strftime("%Y-%m-%d")),
        token=token,
        stream_running=bool((upstox_stream_manager.status() or {}).get("running")),
        roller_running=bool(roller_status.get("running")),
        roller_started_ms=roller_started_ms, last_candle_ts=last_candle_ts,
        supervisor_backoff_active=bool(sup.get("backoff_active")),
        supervisor_last_error=sup.get("last_error"),
    )
    return serialize_doc(health)


@api.get("/live-exit-monitor/status")
async def live_exit_monitor_status():
    return serialize_doc(live_exit_monitor.status())


@api.post("/live-candles/start")
async def live_candle_roller_start():
    """Manually start the live tick-to-OHLC roller. No-op if already running."""
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = False
    await live_candle_roller.start()
    return serialize_doc(live_candle_roller.status())


@api.post("/live-candles/stop")
async def live_candle_roller_stop():
    """Stop the roller and flush any in-progress buckets."""
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = True
    await live_candle_roller.stop()
    return serialize_doc(live_candle_roller.status())


@api.get("/upstox/status")
async def upstox_status():
    return await upstox_client.get_connection_status()


@api.get("/upstox/auth/start")
async def upstox_auth_start():
    if not upstox_client.is_configured():
        raise HTTPException(500, "Upstox credentials not configured. Set UPSTOX_CLIENT_ID / UPSTOX_CLIENT_SECRET / UPSTOX_REDIRECT_URI in backend/.env")
    state = secrets.token_urlsafe(24)
    _OAUTH_STATES[state] = datetime.now(timezone.utc).timestamp()
    # Prune old states (>15 min)
    now = datetime.now(timezone.utc).timestamp()
    for s, t in list(_OAUTH_STATES.items()):
        if now - t > 900:
            _OAUTH_STATES.pop(s, None)
    url = upstox_client.build_login_url(state)
    return {"login_url": url, "state": state}


@api.get("/upstox/auth/callback")
async def upstox_auth_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Browser is redirected here by Upstox after login. Exchange code for token, then redirect to frontend."""
    frontend_url = os.environ.get("FRONTEND_POST_AUTH_URL", "/warehouse")
    if error:
        return RedirectResponse(f"{frontend_url}?upstox_error={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend_url}?upstox_error=missing_code_or_state")
    if state not in _OAUTH_STATES:
        return RedirectResponse(f"{frontend_url}?upstox_error=invalid_state")
    _OAUTH_STATES.pop(state, None)
    try:
        payload = await upstox_client.exchange_code_for_token(code)
        await upstox_client.save_token(upstox_client.DEFAULT_USER_ID, payload)
        # Fresh token: kick off a warehouse catch-up in the background so the
        # redirect is not delayed. Best-effort; failures are captured in state.
        asyncio.create_task(_trigger_autoupdate("oauth_connect"), name="warehouse-autoupdate-oauth")
        return RedirectResponse(f"{frontend_url}?upstox_connected=1")
    except Exception as e:
        log.exception("upstox token exchange failed")
        return RedirectResponse(f"{frontend_url}?upstox_error={str(e)[:200]}")


@api.post("/upstox/disconnect")
async def upstox_disconnect():
    deleted = await upstox_client.disconnect()
    return {"disconnected": deleted}


@api.get("/upstox/market-quote/{instrument}")
async def upstox_market_quote(instrument: str):
    """Read a live Upstox market quote snapshot for a supported index."""
    try:
        return await upstox_client.fetch_market_quote(instrument)
    except Exception as e:
        raise HTTPException(400, str(e)[:300])


@api.post("/upstox/stream/start")
async def upstox_stream_start(req: UpstoxStreamStartReq):
    """Start the read-only Upstox V3 market-data WebSocket stream."""
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = False
    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before starting the stream.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before starting the stream.")
    instrument_keys = req.instrument_keys or _default_stream_instrument_keys()
    if not instrument_keys:
        raise HTTPException(400, "No stream instrument keys configured")
    try:
        return serialize_doc(await upstox_stream_manager.start(
            instrument_keys=instrument_keys,
            mode=req.mode,
            persist=req.persist_ticks,
        ))
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.post("/upstox/stream/stop")
async def upstox_stream_stop():
    """Stop the local read-only Upstox WebSocket stream."""
    from app.runtime import _feed_supervisor
    _feed_supervisor["suppressed"] = True
    return serialize_doc(await upstox_stream_manager.stop())


@api.get("/upstox/stream/status")
async def upstox_stream_status():
    """Return sanitized local WebSocket stream status."""
    return serialize_doc(upstox_stream_manager.status())


@api.get("/upstox/stream/options/universe")
async def upstox_stream_options_universe(
    underlyings: Optional[str] = Query(None, description="Comma-separated index underlyings, e.g. NIFTY,BANKNIFTY"),
    radius: int = Query(1, ge=0, le=5),
    max_option_keys: int = Query(60, ge=2, le=200),
):
    """Preview the nearest-expiry ATM option keys suitable for the live WS stream."""
    result = await build_live_option_universe(
        get_db(),
        latest_ticks=upstox_stream_manager.latest_tick_map(),
        underlyings=_parse_underlyings_query(underlyings),
        radius=radius,
        max_option_keys=max_option_keys,
    )
    return serialize_doc(result)


@api.post("/upstox/stream/options/restart")
async def upstox_stream_options_restart(req: UpstoxOptionStreamRestartReq):
    """Restart the read-only stream with market-header keys plus live ATM option keys."""
    status = await upstox_client.get_connection_status()
    if not status.get("connected"):
        raise HTTPException(400, "Upstox is not connected. Complete OAuth before starting the stream.")
    if status.get("expired"):
        raise HTTPException(400, "Upstox token expired. Reconnect Upstox before starting the stream.")

    universe = await build_live_option_universe(
        get_db(),
        latest_ticks=upstox_stream_manager.latest_tick_map(),
        underlyings=req.underlyings,
        radius=req.radius,
        max_option_keys=req.max_option_keys,
    )
    option_keys = universe.get("instrument_keys") or []
    if not option_keys:
        raise HTTPException(400, "No live option keys available. Sync current option contracts and ensure spot data exists.")

    stream_keys = list(dict.fromkeys([*_default_stream_instrument_keys(), *option_keys]))
    await upstox_stream_manager.stop()
    stream_status = await upstox_stream_manager.start(
        instrument_keys=stream_keys,
        mode=req.mode,
        persist=req.persist_ticks,
    )
    return serialize_doc({
        "status": "ok",
        "stream": stream_status,
        "universe": universe,
        "stream_instrument_count": len(stream_keys),
    })


@api.get("/upstox/stream/ticks/latest")
async def upstox_stream_latest_ticks(limit: int = Query(50, le=500)):
    """Return latest sanitized ticks from memory, falling back to stored Mongo ticks."""
    items = upstox_stream_manager.latest_ticks(limit=limit)
    if len(items) < limit:
        seen = {(item.get("instrument_key"), item.get("ts")) for item in items}
        rows = await get_db().ticks.find({}, {"_id": 0}).sort("received_ts", -1).limit(limit).to_list(length=limit)
        for row in rows:
            key = (row.get("instrument_key"), row.get("ts"))
            if key in seen:
                continue
            items.append(row)
            seen.add(key)
            if len(items) >= limit:
                break
    return {"items": serialize_doc(items[:limit]), "count": len(items[:limit]), "source": "upstox_ws_v3"}
