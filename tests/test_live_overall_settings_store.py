"""TDD tests for backend/app/live/overall_settings_store.py.

The overall-controls config is the single shared contract (backend + frontend
panel use EXACTLY this object).  Shape::

    {
      "sl":      { "enabled": bool, "mode": "mtm"|"premium_pct", "value": number },
      "target":  { "enabled": bool, "mode": "mtm"|"premium_pct", "value": number },
      "trailing": {
        "mode": "none"|"lock"|"lock_trail"|"overall_trail",
        "unit": "mtm"|"premium_pct",
        "lock_at": number, "lock_floor": number,
        "trail_per": number, "trail_by": number, "base_sl": number
      },
      "reentry": { "enabled": bool, "max": int<=5, "type": "asap"|"momentum",
                   "reverse": bool, "momentum_pct": number }
    }

Store mirrors kill_switch.SafetyConfigStore: async store over an injectable
collection (find_one / update_one upsert), _SINGLETON_ID, get/put with a
whitelist, tests inject a FakeAsyncCollection.  An optional ``scope`` param lets
the SAME class back both the 'overall' and 'broker_level' singletons.

Coverage
--------
- get_config returns full defaults when collection empty (never None)
- put_config persists + merges (round-trip)
- put_config rejects unknown TOP-LEVEL keys (ValueError)
- put_config rejects bad sl/target mode + bad trailing.mode (ValueError)
- reentry.max clamped to <= 5
- type coercion (numbers, bools)
- scope isolation: 'overall' and 'broker_level' don't collide
- default_store() wiring (deferred import — smoke only)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.overall_settings_store import (
    DEFAULT_OVERALL_CONFIG,
    OverallSettingsStore,
)


# ---------------------------------------------------------------------------
# FakeAsyncCollection (copied from test_live_kill_switch.py)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeAsyncCollection:
    """In-memory async collection suitable for OverallSettingsStore tests."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def find_one(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: Dict[str, Any]) -> Any:
        self.docs.append(dict(doc))

    async def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> _UpdateResult:
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _UpdateResult(matched_count=1)
        if upsert:
            new_doc = dict(query)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=1)
        return _UpdateResult(matched_count=0)

    def find(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> _FakeCursor:
        results = [dict(d) for d in self.docs if _matches(d, query)]
        return _FakeCursor(results)

    async def create_index(self, field: str, unique: bool = False) -> str:
        return f"{field}_1"


def _matches(doc: dict, query: dict) -> bool:
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


def _fake_store(scope: str = "overall") -> "tuple[OverallSettingsStore, FakeAsyncCollection]":
    col = FakeAsyncCollection()
    return OverallSettingsStore(col, scope=scope), col


# ===========================================================================
# DEFAULT_OVERALL_CONFIG shape
# ===========================================================================

class TestDefaultShape:
    def test_default_top_level_keys(self):
        assert set(DEFAULT_OVERALL_CONFIG) == {"sl", "target", "trailing", "reentry"}

    def test_default_all_disabled(self):
        assert DEFAULT_OVERALL_CONFIG["sl"]["enabled"] is False
        assert DEFAULT_OVERALL_CONFIG["target"]["enabled"] is False
        assert DEFAULT_OVERALL_CONFIG["trailing"]["mode"] == "none"
        assert DEFAULT_OVERALL_CONFIG["reentry"]["enabled"] is False

    def test_default_modes_sensible(self):
        assert DEFAULT_OVERALL_CONFIG["sl"]["mode"] in {"mtm", "premium_pct"}
        assert DEFAULT_OVERALL_CONFIG["target"]["mode"] in {"mtm", "premium_pct"}
        assert DEFAULT_OVERALL_CONFIG["trailing"]["unit"] in {"mtm", "premium_pct"}
        assert DEFAULT_OVERALL_CONFIG["reentry"]["type"] in {"asap", "momentum"}

    def test_default_trailing_numeric_fields_present(self):
        t = DEFAULT_OVERALL_CONFIG["trailing"]
        for k in ("lock_at", "lock_floor", "trail_per", "trail_by", "base_sl"):
            assert k in t
            assert isinstance(t[k], (int, float))

    def test_default_reentry_max_within_cap(self):
        assert DEFAULT_OVERALL_CONFIG["reentry"]["max"] <= 5


# ===========================================================================
# get_config
# ===========================================================================

class TestGetConfig:
    def test_returns_defaults_when_empty(self):
        store, _ = _fake_store()
        cfg = asyncio.run(store.get_config())
        assert cfg == DEFAULT_OVERALL_CONFIG
        assert cfg is not DEFAULT_OVERALL_CONFIG  # must be a copy, not the constant

    def test_never_returns_none(self):
        store, _ = _fake_store()
        cfg = asyncio.run(store.get_config())
        assert cfg is not None

    def test_get_does_not_mutate_default_constant(self):
        store, _ = _fake_store()
        cfg = asyncio.run(store.get_config())
        cfg["sl"]["enabled"] = True
        # The module-level constant must remain pristine.
        assert DEFAULT_OVERALL_CONFIG["sl"]["enabled"] is False

    def test_merges_stored_partial_with_defaults(self):
        store, _ = _fake_store()
        asyncio.run(store.put_config({"sl": {"enabled": True, "mode": "mtm", "value": 5000}}))
        cfg = asyncio.run(store.get_config())
        assert cfg["sl"]["enabled"] is True
        assert cfg["sl"]["value"] == 5000
        # untouched sections still defaulted
        assert cfg["target"]["enabled"] is False
        assert cfg["trailing"]["mode"] == "none"


# ===========================================================================
# put_config — persist + merge + round-trip
# ===========================================================================

class TestPutConfig:
    def test_persists_and_merges_sl(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config(
            {"sl": {"enabled": True, "mode": "premium_pct", "value": 30}}
        ))
        assert result["sl"]["enabled"] is True
        assert result["sl"]["mode"] == "premium_pct"
        assert result["sl"]["value"] == 30

    def test_round_trip(self):
        store, _ = _fake_store()
        payload = {
            "sl": {"enabled": True, "mode": "mtm", "value": 5000},
            "target": {"enabled": True, "mode": "premium_pct", "value": 50},
            "trailing": {
                "mode": "lock_trail", "unit": "mtm",
                "lock_at": 2000, "lock_floor": 1000,
                "trail_per": 500, "trail_by": 250, "base_sl": 3000,
            },
            "reentry": {"enabled": True, "max": 3, "type": "momentum",
                        "reverse": True, "momentum_pct": 10},
        }
        asyncio.run(store.put_config(payload))
        cfg = asyncio.run(store.get_config())
        assert cfg["sl"] == {"enabled": True, "mode": "mtm", "value": 5000}
        assert cfg["target"] == {"enabled": True, "mode": "premium_pct", "value": 50}
        assert cfg["trailing"]["mode"] == "lock_trail"
        assert cfg["trailing"]["trail_by"] == 250
        assert cfg["reentry"]["max"] == 3
        assert cfg["reentry"]["reverse"] is True

    def test_partial_section_merges_with_section_defaults(self):
        """put_config({'sl': {'enabled': True}}) keeps default mode/value."""
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"sl": {"enabled": True}}))
        assert result["sl"]["enabled"] is True
        assert result["sl"]["mode"] == DEFAULT_OVERALL_CONFIG["sl"]["mode"]
        assert result["sl"]["value"] == DEFAULT_OVERALL_CONFIG["sl"]["value"]

    def test_returns_full_merged_config(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"target": {"enabled": True}}))
        assert set(result) == {"sl", "target", "trailing", "reentry"}

    def test_second_put_merges_over_first(self):
        store, _ = _fake_store()
        asyncio.run(store.put_config({"sl": {"enabled": True, "mode": "mtm", "value": 1000}}))
        asyncio.run(store.put_config({"target": {"enabled": True, "mode": "mtm", "value": 2000}}))
        cfg = asyncio.run(store.get_config())
        assert cfg["sl"]["value"] == 1000   # first put survives
        assert cfg["target"]["value"] == 2000


# ===========================================================================
# put_config — validation / fail-closed
# ===========================================================================

class TestPutConfigValidation:
    def test_rejects_unknown_top_level_key(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"bogus": 1}))

    def test_rejects_bad_sl_mode(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"sl": {"enabled": True, "mode": "lol", "value": 1}}))

    def test_rejects_bad_target_mode(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"target": {"mode": "nope"}}))

    def test_rejects_bad_trailing_mode(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"trailing": {"mode": "rocket"}}))

    def test_rejects_bad_trailing_unit(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"trailing": {"unit": "bananas"}}))

    def test_rejects_bad_reentry_type(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"reentry": {"type": "telepathy"}}))

    def test_accepts_all_valid_trailing_modes(self):
        for m in ("none", "lock", "lock_trail", "overall_trail"):
            store, _ = _fake_store()
            result = asyncio.run(store.put_config({"trailing": {"mode": m}}))
            assert result["trailing"]["mode"] == m

    def test_rejects_unknown_section_subkey(self):
        """An unknown key inside a known section must be rejected (fail-closed)."""
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"sl": {"enabled": True, "bogus_sub": 9}}))

    def test_rejects_non_dict_section(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"sl": 123}))


# ===========================================================================
# reentry.max clamping
# ===========================================================================

class TestReentryMaxClamp:
    def test_max_above_5_clamped_to_5(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"reentry": {"enabled": True, "max": 99}}))
        assert result["reentry"]["max"] == 5

    def test_max_at_5_preserved(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"reentry": {"max": 5}}))
        assert result["reentry"]["max"] == 5

    def test_max_below_5_preserved(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"reentry": {"max": 2}}))
        assert result["reentry"]["max"] == 2

    def test_negative_max_clamped_to_zero(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"reentry": {"max": -3}}))
        assert result["reentry"]["max"] == 0


# ===========================================================================
# Type coercion
# ===========================================================================

class TestCoercion:
    def test_string_number_coerced_for_sl_value(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"sl": {"value": "5000"}}))
        assert result["sl"]["value"] == 5000

    def test_max_coerced_to_int(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"reentry": {"max": "3"}}))
        assert result["reentry"]["max"] == 3
        assert isinstance(result["reentry"]["max"], int)

    def test_enabled_coerced_to_bool(self):
        store, _ = _fake_store()
        result = asyncio.run(store.put_config({"sl": {"enabled": 1}}))
        assert result["sl"]["enabled"] is True
        assert isinstance(result["sl"]["enabled"], bool)

    def test_bad_number_raises(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"sl": {"value": "not-a-number"}}))


# ===========================================================================
# scope isolation — one class, two singletons
# ===========================================================================

class TestScope:
    def test_default_scope_is_overall(self):
        col = FakeAsyncCollection()
        store = OverallSettingsStore(col)
        asyncio.run(store.put_config({"sl": {"enabled": True}}))
        assert col.docs[0]["_id"] == "overall"

    def test_broker_level_scope_distinct_id(self):
        col = FakeAsyncCollection()
        store = OverallSettingsStore(col, scope="broker_level")
        asyncio.run(store.put_config({"sl": {"enabled": True}}))
        assert col.docs[0]["_id"] == "broker_level"

    def test_two_scopes_same_collection_dont_collide(self):
        col = FakeAsyncCollection()
        overall = OverallSettingsStore(col, scope="overall")
        broker = OverallSettingsStore(col, scope="broker_level")
        asyncio.run(overall.put_config({"sl": {"enabled": True, "mode": "mtm", "value": 1}}))
        asyncio.run(broker.put_config({"sl": {"enabled": True, "mode": "mtm", "value": 999}}))
        ov = asyncio.run(overall.get_config())
        br = asyncio.run(broker.get_config())
        assert ov["sl"]["value"] == 1
        assert br["sl"]["value"] == 999


# ===========================================================================
# default_store wiring (deferred import smoke)
# ===========================================================================

class TestDefaultStore:
    def test_default_store_importable_with_scope(self):
        """default_store(scope=...) constructs without a running Mongo (deferred import)."""
        import app.live.overall_settings_store as mod
        assert hasattr(mod, "default_store")
