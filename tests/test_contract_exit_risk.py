# tests/test_contract_exit_risk.py  (repo-root tests/; run pytest from REPO ROOT)
from tests.contract_corpus import backend_api_text

API = backend_api_text()


def test_exit_controls_schema_pinned():
    assert "class ExitControlsReq" in API
    assert "class DailyCapsReq" in API
    assert "exit_controls" in API and "daily_caps" in API


def test_attribution_reason_constants_pinned():
    for name in ("OPTION_TRAIL_STOP", "OPTION_BREAKEVEN_STOP", "DAILY_LOSS_HALT",
                 "DAILY_TARGET_HALT", "MAX_TRADES_HALT"):
        assert name in API


def test_attribution_metric_key_constants_pinned():
    # V5: the response-side metric KEY names are pinned in a corpus-visible module
    for name in ("option_trail_exits", "option_breakeven_exits", "skipped_by_cap"):
        assert name in API


def test_search_exit_controls_flag_pinned():
    assert "search_exit_controls" in API
