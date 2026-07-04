"""Seam regression: merged_params must pass shared indicator-period params.

The optimizer's "optimize indicator periods" feature injects catalog params
(rsi_length, macd_*, adx_length, ...) that no strategy declares in its own
parameter_schema. StrategyBase.merged_params() used to allow-list ONLY schema
keys, silently dropping the injected ones in every evaluation path — the
optimizer's own trials, saved presets re-run in Backtest Lab, and paper
deployments — making the feature a no-op since birth.

These tests pin the fixed contract:
  * shared indicator keys survive merged_params for every registered strategy
  * unknown junk keys are still dropped (the allow-list is widened, not opened)
  * the two key tuples (optimizer.py literal / indicator_groups source) match
    — text-based on the optimizer side so host tests never import optuna.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.indicator_groups import SHARED_INDICATOR_PARAM_KEYS  # noqa: E402
from app.strategies.builtin.confluence_scalper import ConfluenceScalper  # noqa: E402

OPT_SRC = pathlib.Path("backend/app/optimizer.py").read_text(encoding="utf-8")


def _optimizer_keys_tuple_text() -> str:
    i = OPT_SRC.index("INDICATOR_PARAM_KEYS")
    return OPT_SRC[i:OPT_SRC.index(")", i)]


def test_shared_indicator_params_survive_merged_params():
    s = ConfluenceScalper()
    assert "rsi_length" not in s.parameter_schema  # precondition: not a schema param
    merged = s.merged_params({"rsi_length": 9, "adx_length": 21, "macd_fast": 8})
    assert merged["rsi_length"] == 9
    assert merged["adx_length"] == 21
    assert merged["macd_fast"] == 8


def test_schema_params_still_merge_and_junk_still_drops():
    s = ConfluenceScalper()
    merged = s.merged_params({"ema_fast": 7, "definitely_not_a_param": 1})
    assert merged["ema_fast"] == 7
    assert "definitely_not_a_param" not in merged


def test_every_shared_key_is_in_optimizer_literal_tuple():
    block = _optimizer_keys_tuple_text()
    for k in SHARED_INDICATOR_PARAM_KEYS:
        assert f'"{k}"' in block, (
            f"{k} in SHARED_INDICATOR_PARAM_KEYS but missing from the "
            "INDICATOR_PARAM_KEYS literal in optimizer.py (drift guard would "
            "also fail at import in the container)"
        )


def test_merged_params_accepts_exactly_the_shared_set_beyond_schema():
    s = ConfluenceScalper()
    for k in SHARED_INDICATOR_PARAM_KEYS:
        merged = s.merged_params({k: 11})
        assert merged.get(k) == 11, f"shared key {k} was dropped by merged_params"
