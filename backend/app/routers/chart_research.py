"""Research-only chart routes; POST writes forecasts, never orders."""
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.chart_research import get_chart_research_service

api = APIRouter(prefix='/analysis', tags=['charts'])
_worker = None


@api.on_event('startup')
async def start_research_scoring():
    global _worker
    async def work():
        while True:
            await asyncio.sleep(30)
            try:
                await get_chart_research_service().settle_due()
            except Exception as exc:
                logging.getLogger(__name__).warning('Research settlement unavailable (%s)', type(exc).__name__)
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(work())


@api.on_event('shutdown')
async def stop_research_scoring():
    global _worker
    if _worker:
        _worker.cancel()
        await asyncio.gather(_worker, return_exceptions=True)
        _worker = None


class ObserveRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    instrument_key: str = Field(min_length=1, max_length=200)
    timeframe: str = Field(max_length=4)
    strategy_id: str | None = Field(default=None, max_length=100)
    forecast: bool = False


@api.get('/catalog')
async def catalog(instrument_key: str, timeframe: str = '1m'):
    try:
        return await get_chart_research_service().catalog(instrument_key, timeframe)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, 'Chart analysis catalogue unavailable') from exc


@api.post('/observe')
async def observe(body: ObserveRequest):
    try:
        return await get_chart_research_service().observe(body.instrument_key, body.timeframe, body.strategy_id, body.forecast)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, 'Chart analysis unavailable') from exc
