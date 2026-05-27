import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

motor_module = types.ModuleType("motor")
motor_asyncio_module = types.ModuleType("motor.motor_asyncio")


class DummyMotorClient:
    pass


motor_asyncio_module.AsyncIOMotorClient = DummyMotorClient
sys.modules.setdefault("motor", motor_module)
sys.modules.setdefault("motor.motor_asyncio", motor_asyncio_module)

from app import upstox_client  # noqa: E402


def test_sanitize_user_meta_strips_token_like_fields():
    raw = {
        "user_id": "USER123",
        "broker": "UPSTOX",
        "extended_token": "secret",
        "refresh_token": "secret2",
        "access_token": "secret3",
        "token_type": "Bearer",
        "safe_value": "ok",
    }

    sanitized = upstox_client.sanitize_user_meta(raw)

    assert "extended_token" not in sanitized
    assert "refresh_token" not in sanitized
    assert "access_token" not in sanitized
    assert "token_type" not in sanitized
    assert sanitized["safe_value"] == "ok"
