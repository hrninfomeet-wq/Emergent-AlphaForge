import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_data_audit import summarize_option_audit  # noqa: E402
from tests.contract_corpus import backend_api_text
from tests.contract_corpus import warehouse_page_text


def test_summarize_option_audit_flags_missing_and_incomplete_contract_days():
    contracts = [
        {
            "instrument_key": "NSE_FO|100",
            "underlying": "NIFTY",
            "expiry_date": "2026-05-28",
            "strike": 24000,
            "side": "CE",
            "trading_symbol": "NIFTY26MAY24000CE",
        },
        {
            "instrument_key": "NSE_FO|200",
            "underlying": "NIFTY",
            "expiry_date": "2026-05-28",
            "strike": 24000,
            "side": "PE",
            "trading_symbol": "NIFTY26MAY24000PE",
        },
    ]
    expected_counts = {"2026-05-26": 375, "2026-05-27": 375}
    option_counts = {
        ("NSE_FO|100", "2026-05-26"): 375,
        ("NSE_FO|100", "2026-05-27"): 120,
    }

    result = summarize_option_audit(
        underlying="NIFTY",
        contracts=contracts,
        expected_date_counts=expected_counts,
        option_counts=option_counts,
    )

    assert result["summary"]["contracts_checked"] == 2
    assert result["summary"]["complete_contracts"] == 0
    assert result["summary"]["contracts_with_missing_days"] == 1
    assert result["summary"]["contracts_with_incomplete_days"] == 1
    assert result["summary"]["stored_candles"] == 495
    assert result["summary"]["expected_candles"] == 1500

    by_key = {item["instrument_key"]: item for item in result["items"]}
    assert by_key["NSE_FO|100"]["status"] == "incomplete"
    assert by_key["NSE_FO|100"]["coverage_pct"] == 66.0
    assert by_key["NSE_FO|200"]["status"] == "missing"
    assert by_key["NSE_FO|200"]["missing_days"] == 2


def test_backend_exposes_option_audit_routes():
    server = backend_api_text()

    assert '@api.get("/options/audit/{instrument}")' in server
    assert '@api.delete("/options/data/{instrument}")' in server
    assert "audit_option_data" in server
    assert "clear_option_data" in server


def test_frontend_option_clear_retained_audit_panel_removed():
    """The redundant Raw Option Universe Audit panel was removed; its unique
    'clear option candles' maintenance action was relocated into the Data Trust
    Audit panel and must still be present."""
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    page = warehouse_page_text()

    # API helpers remain (route still exists for programmatic use).
    assert "clearOptionData" in api
    # The clear-options control is retained in the Data Trust Audit panel.
    assert "option-clear-button" in page
    # The redundant raw audit panel and its widgets are gone.
    for removed in (
        "option-audit-panel",
        "option-audit-button",
        "option-audit-summary",
        "option-audit-table",
        "Raw Option Universe Audit",
    ):
        assert removed not in page
