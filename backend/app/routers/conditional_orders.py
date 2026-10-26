"""Conditional order API: no arbitrary broker payloads accepted from clients."""
from typing import Any, Dict, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool
from app.db import serialize_doc
from app.conditional import service
from app.conditional.store import Conflict

api = APIRouter(prefix='/conditional-orders', tags=['conditional-orders'])


class ArmBody(BaseModel):
    preview_id: str
    confirm_live: StrictBool = False


class ModeBody(BaseModel):
    mode: Literal['paper', 'live']


def engine():
    try:
        return service.get_service()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


async def response(operation):
    try:
        return serialize_doc(await operation)
    except (ValueError, Conflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f'Conditional service unavailable: {exc}') from exc


@api.get('')
async def list_orders():
    engine()
    return await response(service.status())


@api.get('/heartbeat')
async def heartbeat():
    """Process-local progress for the independent watchdog; no broker reads."""
    e = engine()
    return {'running': bool(e.task and not e.task.done()), 'last_progress': e.last_progress}


@api.get('/metadata')
async def metadata(underlying: Literal['NIFTY', 'BANKNIFTY', 'SENSEX'] = 'NIFTY'):
    e = engine()
    async def load():
        contracts = await e.market.contracts(underlying)
        available, reason = await e.live_ready()
        return {'contracts': contracts, 'expiries': sorted({c['expiry'] for c in contracts}),
                'live_available': available, 'live_reason': reason,
                'max_lots': e.max_lots,
                'warning': 'Live market validation pending. ' + (
                    'Broker stop capability configured; each stop still requires broker readback.'
                    if e.gateways['live'].protection_supported else
                    'Software-only exits: backend, feed or broker failure can leave exposure open.')}
    return await response(load())


@api.post('/preview')
async def preview(body: Dict[str, Any]):
    return await response(engine().preview(body))


@api.post('/arm')
async def arm(body: ArmBody):
    async def create():
        return {'order': await engine().arm(body.preview_id, body.confirm_live)}
    return await response(create())


@api.post('/kill')
async def kill(body: ModeBody):
    return await response(engine().kill(body.mode))


@api.post('/reset')
async def reset(body: ModeBody):
    return await response(engine().reset(body.mode))


@api.post('/{order_id}/cancel')
async def cancel(order_id: str):
    e = engine()
    async def request():
        doc = await e.store.get(order_id)
        if doc is None:
            raise HTTPException(404, 'Conditional order not found')
        await e.store.request_cancel(order_id)
        return {'order': await e.store.get(order_id),
                'message': 'Cancel/close requested; exposure remains open until reconciled'}
    return await response(request())
