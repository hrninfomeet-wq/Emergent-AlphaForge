"""Independent exit-only failover process. Run with the supplied Compose service.

Never arms or sends entries. A database lease fences primary/failover work.
Host/power/network failure of both processes still cannot guarantee a flatten.
"""
import asyncio
import logging
import os
import time
import uuid

import httpx
from app.db import get_db
from app.conditional.service import build

log = logging.getLogger(__name__)


class NoEntryFeed:
    async def ensure_subscription(self, contracts):
        pass

    async def snapshot(self, *args, **kwargs):
        return {}

    async def contracts(self, underlying):
        raise RuntimeError('Watchdog cannot select or enter a contract')


async def run():
    engine = build(get_db(), market=NoEntryFeed())
    engine.owner = 'watchdog:' + uuid.uuid4().hex
    engine.recovery_only = True
    await engine.store.initialize()
    url = os.environ.get('CONDITIONAL_BACKEND_URL', 'http://127.0.0.1:8001').rstrip('/')
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            primary_ok = False
            try:
                response = await client.get(url + '/api/conditional-orders/heartbeat')
                response.raise_for_status()
                body = response.json()
                primary_ok = body.get('running') is True and 0 <= time.time() - float(body['last_progress']) < 30
            except Exception:
                pass
            try:
                if primary_ok:
                    await engine.store.drop_lease(engine.owner)
                else:
                    # Claim only after the primary lease expires. Claimed actions
                    # remain indeterminate until read back, never blindly replay.
                    await engine.cycle()
            except Exception:
                log.exception('Conditional watchdog: unresolved outage/exposure')
            await asyncio.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
