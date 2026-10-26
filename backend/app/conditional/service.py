"""Lifecycle wiring kept separate from existing strategy/manual execution."""
from __future__ import annotations

import os
import time
from .broker import FlattradeGateway, PaperGateway
from .engine import ConditionalEngine
from .market import ProductionMarket
from .store import MongoStore

_service = None


def build(db, *, market=None):
    # Only set this after account/product-specific trigger-pending readback.
    broker_stop = os.environ.get('CONDITIONAL_BROKER_STOP_VERIFIED', '0').lower() in ('1', 'true', 'yes')
    max_lots = int(os.environ.get('CONDITIONAL_MAX_LOTS', '5'))
    if not 1 <= max_lots <= 100:
        raise ValueError('CONDITIONAL_MAX_LOTS must be between 1 and 100')
    return ConditionalEngine(MongoStore(db), market or ProductionMarket(),
                             {'paper': PaperGateway(), 'live': FlattradeGateway(protection_supported=broker_stop)},
                             max_lots=max_lots)


def get_service():
    if _service is None:
        raise RuntimeError('Conditional-order service is not running')
    return _service


async def start(db):
    global _service
    if _service is not None:
        return _service
    service = build(db)
    await service.start()
    _service = service
    return service


async def stop():
    global _service
    if _service is not None:
        await _service.stop()
    _service = None


async def block_mode(mode):
    """Used by existing kill controls even if worker startup failed."""
    if _service is not None:
        return await _service.kill(mode)
    from app.db import get_db
    fallback = build(get_db())
    await fallback.store.initialize()
    return await fallback.kill(mode)


async def status():
    service = get_service()
    now = time.time()
    heartbeat = await service.store.heartbeat()
    at = heartbeat.get('heartbeat')
    controls = {mode: await service.store.control(mode) for mode in ('paper', 'live')}
    live_available, live_reason = await service.live_ready()
    return {'orders': [o for o in await service.store.list() if o['status'] != 'DRAFT'],
            'controls': controls, 'live_available': live_available, 'live_reason': live_reason,
            'health': {'running': bool(service.task and not service.task.done()),
                       'heartbeat': at, 'stale': not at or now - at > 30,
                       'local_progress': service.last_progress,
                       'last_error': service.last_error,
                       'watchdog': heartbeat.get('owner', '').startswith('watchdog:')},
            'max_lots': service.max_lots}
