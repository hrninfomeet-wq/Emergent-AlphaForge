"""Mongo-backed watchlist with immutable saved instrument identities."""
from __future__ import annotations
import asyncio
import time
import uuid
from pymongo.errors import DuplicateKeyError
from app.chart_stream import stream_state
from app.tick_stream import sse_frame

CAPACITY = 200
IDENTITY_FIELDS = ("instrument_key", "symbol", "exchange", "segment", "kind", "exchange_token", "underlying", "expiry", "strike", "option_type")

class RevisionConflict(Exception):
    pass

def same_identity(saved, current):
    return all(saved.get(k) == current.get(k) for k in IDENTITY_FIELDS)

def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    return value.strip()

class WatchlistStore:
    def __init__(self, collection, resolver):
        self.collection, self.resolver = collection, resolver

    async def get(self):
        doc = await self.collection.find_one({"_id": "default"})
        if doc is None:
            doc = {"_id": "default", "name": "Watchlist", "revision": 0, "items": []}
            try:
                await self.collection.insert_one(doc.copy())
            except DuplicateKeyError:
                doc = await self.collection.find_one({"_id": "default"})
        return {"id": "default", "name": doc["name"], "revision": doc["revision"],
                "items": doc["items"], "capacity": CAPACITY}

    async def put(self, revision, name, items):
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("Invalid revision")
        name = _text(name, "Name", 100)
        if not isinstance(items, list) or len(items) > CAPACITY:
            raise ValueError("Watchlist exceeds capacity")
        old = await self.get()
        if revision != old["revision"]:
            raise RevisionConflict()
        existing = {item["id"]: item for item in old["items"]}
        result, ids, keys = [], set(), set()
        for item in items:
            if not isinstance(item, dict) or set(item) - {"id", "instrument_key", "label"}:
                raise ValueError("Invalid watchlist item")
            key = _text(item.get("instrument_key"), "Instrument key", 200)
            item_id = item.get("id")
            if item_id is not None:
                item_id = _text(item_id, "Item id", 100)
                prior = existing.get(item_id)
                if not prior or prior["instrument"]["instrument_key"] != key:
                    raise ValueError("Unknown item or changed instrument identity")
                instrument = prior["instrument"]
            else:
                if any(prior["instrument"]["instrument_key"] == key for prior in existing.values()):
                    raise ValueError("Existing instruments require their saved item id")
                instrument = await self.resolver(key)
                if any(prior["instrument"]["instrument_key"] == instrument["instrument_key"] for prior in existing.values()):
                    raise ValueError("Existing instruments require their saved item id")
                item_id = str(uuid.uuid4())
            canonical_key = instrument["instrument_key"]
            if item_id in ids or canonical_key in keys:
                raise ValueError("Duplicate watchlist item")
            ids.add(item_id); keys.add(canonical_key)
            raw_label = item.get("label")
            label = _text(raw_label, "Label", 100) if raw_label is not None and (not isinstance(raw_label, str) or raw_label.strip()) else instrument["symbol"]
            result.append({"id": item_id, "instrument": instrument, "label": label})
        doc = {"_id": "default", "name": name, "revision": revision + 1, "items": result}
        outcome = await self.collection.replace_one({"_id": "default", "revision": revision}, doc, upsert=False)
        if outcome.matched_count != 1:
            raise RevisionConflict()
        return {"id": "default", "name": name, "revision": revision + 1, "items": result, "capacity": CAPACITY}

async def watchlist_events(*, store, manager, revision, is_disconnected, interval=1.0, now_ms=lambda: int(time.time() * 1000), monotonic=time.monotonic):
    lease = None
    try:
        saved = await store.get()
        if saved["revision"] != revision:
            yield sse_frame("watchlist_changed", {"revision": saved["revision"]})
            return
        valid, unavailable = [], {}
        for item in saved["items"]:
            old = item["instrument"]
            key = old["instrument_key"]
            try:
                current = await store.resolver(key)
                if not same_identity(old, current):
                    raise ValueError("Identity changed")
                valid.append(key)
            except Exception:
                unavailable[key] = {"instrument_key": key, "tick": {}, "connected": False,
                                    "last_tick_age_ms": None, "status": "unavailable",
                                    "warnings": ["Saved instrument is expired, unavailable, or its identity changed."]}
        if valid:
            lease = await manager.acquire_quote_batch(valid)
        identity_check_at = monotonic() + 60
        while not await is_disconnected():
            if monotonic() >= identity_check_at:
                for item in saved["items"]:
                    old = item["instrument"]
                    key = old["instrument_key"]
                    try:
                        currently_valid = same_identity(old, await store.resolver(key))
                    except Exception:
                        currently_valid = False
                    if currently_valid != (key in valid):
                        if lease is not None:
                            await asyncio.shield(manager.release_quote_batch(lease))
                            lease = None
                        yield sse_frame("stream_error", {"warnings": ["Instrument availability changed; reconnecting quotes."], "connected": False})
                        return
                identity_check_at = monotonic() + 60
            latest = await store.get()
            if latest["revision"] != revision:
                yield sse_frame("watchlist_changed", {"revision": latest["revision"]})
                return
            now = now_ms()
            quotes = dict(unavailable)
            # Snapshot the map once; stream_state otherwise copies the full feed per item.
            class SnapshotManager:
                def status(self): return status
                def latest_tick_map(self): return ticks
            status, ticks = manager.status(), manager.latest_tick_map()
            snapshot = SnapshotManager()
            quotes.update({key: stream_state(snapshot, key, None, now) for key in valid})
            yield sse_frame("snapshot", {"revision": revision, "quotes": quotes, "as_of_ms": now, "warnings": []})
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        yield sse_frame("stream_error", {"warnings": ["Watchlist quotes are unavailable."], "connected": False})
    finally:
        if lease is not None:
            await asyncio.shield(manager.release_quote_batch(lease))
