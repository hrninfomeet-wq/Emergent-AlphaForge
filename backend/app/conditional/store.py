"""Mongo durability, revision fences and mode-specific controls.

An action marked SUBMITTING is never blindly retransmitted. Unique action IDs
live in the parent order; revisions fence concurrent workers/API edits.
"""
from __future__ import annotations

import copy
import time
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

TERMINAL = ('CLOSED', 'CANCELED', 'EXPIRED', 'REJECTED')


class Conflict(RuntimeError):
    pass


class MongoStore:
    def __init__(self, db):
        self.orders = db.conditional_orders
        self.controls = db.conditional_controls
        self.reservations = db.conditional_reservations

    async def initialize(self):
        await self.orders.create_index([('mode', 1), ('status', 1)])
        await self.orders.create_index('created_at')
        for mode in ('paper', 'live'):
            await self.controls.update_one({'_id': mode}, {'$setOnInsert': {
                'blocked': False, 'generation': 0}}, upsert=True)

    async def insert(self, order):
        await self.orders.insert_one(copy.deepcopy(order))
        return order

    async def get(self, oid):
        return await self.orders.find_one({'_id': oid})

    async def save(self, order):
        expected = order['version']
        updated = copy.deepcopy(order)
        updated['version'] = expected + 1
        result = await self.orders.replace_one({'_id': order['_id'], 'version': expected}, updated)
        if result.matched_count != 1:
            raise Conflict('Order changed concurrently; re-read before acting')
        order['version'] = expected + 1
        return order

    async def list(self, *, active=False, limit=200):
        query = {'status': {'$nin': list(TERMINAL) + ['DRAFT']}} if active else {'status': {'$ne': 'DRAFT'}}
        # Never bound active monitoring by the UI's display limit.
        cursor = self.orders.find(query).sort('created_at', -1)
        if not active:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=None)

    async def control(self, mode):
        row = await self.controls.find_one({'_id': mode})
        if not row:
            raise RuntimeError('Conditional controls unavailable; entries denied')
        return row

    async def latch(self, mode, blocked):
        if mode not in ('paper', 'live'):
            raise ValueError('invalid mode')
        return await self.controls.find_one_and_update({'_id': mode}, {
            '$set': {'blocked': bool(blocked), 'updated_at': time.time()},
            '$inc': {'generation': 1}}, return_document=ReturnDocument.AFTER)

    async def request_cancel(self, oid):
        return await self.orders.find_one_and_update(
            {'_id': oid, 'status': {'$nin': list(TERMINAL)}},
            {'$set': {'cancel_requested': True}, '$inc': {'version': 1}},
            return_document=ReturnDocument.AFTER)

    async def reserve(self, mode, contract_key, oid):
        key = mode + ':' + contract_key
        try:
            await self.reservations.insert_one({'_id': key, 'order_id': oid})
            return True
        except DuplicateKeyError:
            row = await self.reservations.find_one({'_id': key})
            if row is not None and row.get('order_id') == oid:
                return True
            # Terminal-save and reservation-release are separate writes. Recover
            # the narrow crash window without releasing any active ownership.
            prior = await self.get(row['order_id']) if row else None
            if prior and prior.get('status') in TERMINAL:
                removed = await self.reservations.delete_one({'_id': key, 'order_id': row['order_id']})
                if removed.deleted_count:
                    try:
                        await self.reservations.insert_one({'_id': key, 'order_id': oid})
                        return True
                    except DuplicateKeyError:
                        pass
            return False

    async def release(self, order):
        await self.reservations.delete_many({'order_id': order['_id']})

    async def lease(self, owner, now):
        try:
            result = await self.controls.find_one_and_update(
                {'_id': 'worker', '$or': [{'owner': owner}, {'until': {'$lt': now}}, {'until': {'$exists': False}}]},
                {'$set': {'owner': owner, 'until': now + 90, 'heartbeat': now}},
                upsert=True, return_document=ReturnDocument.AFTER)
            return result and result.get('owner') == owner
        except DuplicateKeyError:
            return False

    async def heartbeat(self):
        return await self.controls.find_one({'_id': 'worker'}) or {}

    async def drop_lease(self, owner):
        await self.controls.update_one({'_id': 'worker', 'owner': owner}, {'$set': {'until': 0}})
