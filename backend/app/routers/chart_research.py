"""Research-only chart routes; POST writes forecasts, never orders."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.chart_research import get_chart_research_service

api = APIRouter(prefix='/analysis', tags=['charts'])


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
