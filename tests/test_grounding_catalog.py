import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.grounding import build_grounding_catalog


def test_catalog_has_core_and_adaptive_columns():
    cat = build_grounding_catalog()
    cols = set(cat["indicator_columns"])
    for c in ["ema9", "ema21", "ema50", "rsi", "vwap", "atr", "adx", "chop", "fvg", "regime"]:
        assert c in cols, f"missing core column {c}"
    for c in ["vel_z", "accel_z", "regime_score", "squeeze_on", "squeeze_fire",
              "supertrend", "vwap_sigma", "nr7", "cpr_p", "day_type", "tod_tradeable"]:
        assert c in cols, f"missing adaptive column {c}"


def test_catalog_signal_fields_complete():
    cat = build_grounding_catalog()
    names = {f["name"] for f in cat["signal_fields"]}
    for f in ["direction", "score", "reasons", "blockers", "target_pct", "stop_pct",
              "time_stop_minutes", "spot_target_pts", "spot_stop_pts",
              "scenario", "spot_target_level", "exit_mode"]:
        assert f in names, f"missing Signal field {f}"


def test_catalog_lists_strategies_with_param_schema():
    cat = build_grounding_catalog()
    ids = {s["id"] for s in cat["strategies"]}
    assert "confluence_scalper" in ids
    cs = next(s for s in cat["strategies"] if s["id"] == "confluence_scalper")
    assert isinstance(cs["parameter_schema"], dict) and cs["parameter_schema"]
