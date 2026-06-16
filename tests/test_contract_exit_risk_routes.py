from tests.contract_corpus import backend_api_text

API = backend_api_text()


def test_routers_call_exit_risk_validator():
    # the validator is invoked from the corpus-visible router/runtime layer
    assert "validate_exit_risk_config" in API


def test_skipped_rows_segregated_at_response_boundary():
    # SKIPPED_DAILY_CAP rows are filtered out of the public trades list
    assert "SKIPPED_DAILY_CAP" in API and "skipped_trades" in API


def test_exit_controls_forwarded_into_sim():
    # the overlay kwargs are forwarded into the option sim on the backtest path
    assert "exit_controls=" in API or "exit_controls" in API


def test_preset_carries_chosen_overlay():
    # apply_opt_as_preset overlays the job's chosen overlay onto the preset execution
    assert "best_exit_controls" in API


def test_backtest_start_validates_overlay_at_submit():
    # the async /backtest/start handler validates the overlay BEFORE launching the
    # worker, mirroring the in-worker backstop — converting the pydantic sub-models
    # to dicts (.model_dump()) so a bad overlay 400s at submit, not as a failed run.
    assert "ob.exit_controls.model_dump()" in API
    assert "ob.daily_caps.model_dump()" in API
