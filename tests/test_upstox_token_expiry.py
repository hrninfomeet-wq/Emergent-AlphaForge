import base64
import json
import sys
import types
from datetime import datetime, timezone
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


def _jwt_with_payload(payload):
    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}.signature"


def test_token_expiry_prefers_jwt_exp_claim_when_expires_in_missing():
    token = _jwt_with_payload({"exp": 1779746400})

    expires_at = upstox_client.resolve_token_expiry({"access_token": token})

    assert expires_at == datetime.fromtimestamp(1779746400, timezone.utc)


def test_token_expiry_uses_expires_in_when_provided():
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)

    expires_at = upstox_client.resolve_token_expiry({"expires_in": 60}, now=now)

    assert expires_at == datetime(2026, 5, 25, 12, 1, tzinfo=timezone.utc)
