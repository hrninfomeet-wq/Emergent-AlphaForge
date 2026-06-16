from tests.contract_corpus import backend_api_text

API = backend_api_text()


def test_run_paired_option_backtest_has_validate_param():
    # the optimizer replays a grid overlay through the runtime with validation off
    assert "validate: bool = True" in API


def test_get_backtest_run_attaches_quality():
    # Fix-C: the run-detail read computes the trust verdict
    assert "evaluate_source_quality" in API


def test_deploy_evidence_reads_promoted_net():
    # Fix-D: the deploy evidence gatherer reads the promoted full-window net
    assert "best_option_pnl_value" in API
