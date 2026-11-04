"""Read-only option-chain chart endpoints."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from app.chart_options import get_chart_options_service, option_events
from app.tick_stream import SSE_HEADERS

api = APIRouter(prefix='/options', tags=['charts'])

def get_quote_manager():
    from app.runtime import upstox_stream_manager
    return upstox_stream_manager

@api.get('/expiries')
async def expiries(instrument_key: str):
    try: return await get_chart_options_service().expiries(instrument_key)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    except Exception as exc: raise HTTPException(503, 'Option contracts unavailable') from exc

@api.get('/chain')
async def chain(instrument_key: str, expiry: str | None = None, center: float | None = None, radius: int = Query(20, ge=5, le=40)):
    try: return await get_chart_options_service().snapshot(instrument_key, expiry, center, radius)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    except Exception as exc: raise HTTPException(503, 'Option chain unavailable') from exc

@api.get('/stream')
async def stream(request: Request, instrument_key: str, expiry: str | None = None, center: float | None = None, radius: int = Query(20, ge=5, le=40)):
    service = get_chart_options_service()
    try: initial = await service.snapshot(instrument_key, expiry, center, radius)
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    except Exception as exc: raise HTTPException(503, 'Option chain unavailable') from exc
    return StreamingResponse(option_events(service=service, manager=get_quote_manager(), initial=initial,
        key=instrument_key, expiry=initial['expiry'], center=center, radius=radius,
        is_disconnected=request.is_disconnected), media_type='text/event-stream', headers=SSE_HEADERS)
