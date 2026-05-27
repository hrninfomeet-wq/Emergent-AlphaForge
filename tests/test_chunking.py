import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.chunking import chunk_guidance_for_index, chunk_guidance_for_options  # noqa: E402


def test_index_auto_chunk_guidance_uses_safe_broker_windows():
    guidance = chunk_guidance_for_index("2026-05-01", "2026-05-26")

    assert guidance["mode"] == "auto"
    assert guidance["chunk_days"] == 7
    assert guidance["contracts"] == 1
    assert guidance["estimated_api_calls"] == 4


def test_index_manual_chunk_guidance_clamps_to_allowed_range():
    guidance = chunk_guidance_for_index("2026-05-01", "2026-05-26", requested_chunk_days=99)

    assert guidance["mode"] == "manual"
    assert guidance["chunk_days"] == 30
    assert guidance["estimated_api_calls"] == 1


def test_option_auto_chunk_guidance_gets_safer_as_contract_count_grows():
    guidance = chunk_guidance_for_options("2026-05-01", "2026-05-26", contract_count=58)

    assert guidance["mode"] == "auto"
    assert guidance["chunk_days"] == 2
    assert guidance["contracts"] == 58
    assert guidance["estimated_api_calls"] == 754
