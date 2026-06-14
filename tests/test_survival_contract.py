from tests.contract_corpus import backend_api_text


def test_survival_config_in_schema_corpus():
    src = backend_api_text()
    assert "class SurvivalConfigReq" in src
    assert "survival_config" in src
    assert "min_equity" in src and "max_drawdown_pct" in src and "max_ror_pct" in src


def test_optimize_start_validates_survival():
    src = backend_api_text()
    assert "survival_config" in src
    assert "costs_enabled" in src
    assert "option_rerank" in src
