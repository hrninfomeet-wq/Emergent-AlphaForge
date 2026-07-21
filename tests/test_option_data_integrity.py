import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_data_integrity import assess_option_research_integrity  # noqa: E402


def test_legacy_reused_token_is_research_only_and_machine_blocked():
    contracts = [
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2025-01-02"},
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2026-03-30"},
    ]
    candles = pd.DataFrame([
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2025-01-02", "ts": 1},
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2026-03-30", "ts": 2},
    ])
    verdict = assess_option_research_integrity(contracts, candles)
    assert verdict["status"] == "research_only"
    assert verdict["promotion_allowed"] is False
    assert verdict["counts"]["metadata_collision_tokens"] == 1
    assert verdict["counts"]["mixed_candle_tokens"] == 1
    assert "reused_exchange_token" in {b["code"] for b in verdict["blockers"]}


def test_new_identity_and_retrieval_fields_remove_legacy_blockers_only():
    contracts = [{
        "instrument_key": "NSE_FO|1", "expiry_date": "2026-07-30",
        "master_snapshot_at": "2026-07-01T00:00:00Z",
    }]
    candles = [{
        "instrument_key": "NSE_FO|1", "contract_key": "NSE_FO|1|2026-07-30",
        "expiry_date": "2026-07-30", "ts": 1, "bar_end_ts": 60_001,
        "first_ingested_at": "2026-07-01T00:00:01Z", "retrieval_run_id": "run-1",
    }]
    verdict = assess_option_research_integrity(contracts, candles)
    codes = {b["code"] for b in verdict["blockers"]}
    assert "legacy_contract_identity" not in codes
    assert "missing_retrieval_provenance" not in codes
    assert codes == {"no_point_in_time_execution_surface"}
