"""Watchlist endpoints, included under the chart router."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from app.chart_data import get_chart_data_service
from app.chart_watchlist import RevisionConflict, WatchlistStore, watchlist_events
from app.db import get_db
from app.tick_stream import SSE_HEADERS

api = APIRouter(prefix="/watchlist", tags=["charts"])

def get_watchlist_store():
    return WatchlistStore(get_db().chart_watchlists, get_chart_data_service().resolve)

def get_quote_manager():
    from app.runtime import upstox_stream_manager
    return upstox_stream_manager

class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    instrument_key: str
    label: str | None = None

class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    name: str
    items: list[Item]

@api.get("")
async def get_watchlist():
    return await get_watchlist_store().get()

@api.put("")
async def put_watchlist(body: Update):
    try:
        return await get_watchlist_store().put(body.revision, body.name, [x.model_dump() for x in body.items])
    except RevisionConflict as exc:
        raise HTTPException(409, "Watchlist changed; reload and try again") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@api.get("/stream")
async def stream_watchlist(request: Request, revision: int = Query(..., ge=0)):
    return StreamingResponse(watchlist_events(store=get_watchlist_store(), manager=get_quote_manager(),
        revision=revision, is_disconnected=request.is_disconnected), media_type="text/event-stream", headers=SSE_HEADERS)
