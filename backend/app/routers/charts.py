"""Private chart terminal: history, market stream and read-only overlays."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.chart_data import get_chart_data_service
from app.chart_positions import chart_positions
from app.chart_stream import chart_event_stream, chart_multi_event_stream, parse_chart_channels
from app.tick_stream import SSE_HEADERS

api = APIRouter(prefix="/charts", tags=["charts"])


async def _instrument(key):
    try:
        return await get_chart_data_service().resolve(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Instrument catalogue is unavailable; try a saved index or retry.") from exc


@api.get("/instruments")
async def instruments(q: str = Query("", max_length=100), limit: int = Query(30, ge=1, le=100)):
    try:
        return await get_chart_data_service().search(q, limit=limit)
    except Exception as exc:
        raise HTTPException(503, "Instrument search is temporarily unavailable.") from exc


@api.get("/history")
async def history(instrument_key: str = Query(..., max_length=200),
                  timeframe: str = Query("1m", pattern="^(1m|3m|5m|15m|30m|1h|1d|1w)$"),
                  limit: int = Query(600, ge=2, le=2000), before: int | None = Query(None, gt=0)):
    try:
        return await get_chart_data_service().history(instrument_key, timeframe=timeframe, limit=limit, before=before)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Chart history is temporarily unavailable.") from exc


@api.get("/stream")
async def stream(request: Request, instrument_key: str = Query(..., max_length=200),
                 timeframe: str = Query("1m", pattern="^(1m|3m|5m|15m|30m|1h|1d|1w)$")):
    from app.runtime import upstox_stream_manager
    instrument = await _instrument(instrument_key)
    return StreamingResponse(chart_event_stream(
        manager=upstox_stream_manager, instrument=instrument, timeframe=timeframe,
        history_loader=get_chart_data_service().history, is_disconnected=request.is_disconnected,
        history_peek=get_chart_data_service().peek_history,
    ), media_type="text/event-stream", headers=SSE_HEADERS)


@api.get("/positions")
async def positions(instrument_key: str = Query(..., max_length=200)):
    return await chart_positions(await _instrument(instrument_key))


@api.get("/streams")
async def streams(request: Request, channels: str = Query(..., max_length=12000)):
    try:
        selected = parse_chart_channels(channels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    resolved, errors = [], []
    for channel in selected:
        try:
            resolved.append((await _instrument(channel["instrument_key"]), channel["timeframe"]))
        except HTTPException as exc:
            # A saved/expired instrument cannot disable the other visible charts.
            errors.append({**channel, "warnings": [str(exc.detail)]})
    manager = None
    if resolved:
        from app.runtime import upstox_stream_manager
        manager = upstox_stream_manager
    service = get_chart_data_service()
    return StreamingResponse(chart_multi_event_stream(
        manager=manager, channels=resolved, history_loader=service.history,
        is_disconnected=request.is_disconnected, history_peek=service.peek_history,
        initial_errors=errors,
    ), media_type="text/event-stream", headers=SSE_HEADERS)


# Watchlist preferences and quotes share this chart-only API namespace.
from app.routers.chart_watchlist import api as watchlist_api
api.include_router(watchlist_api)

from app.routers.chart_options import api as options_api
api.include_router(options_api)
