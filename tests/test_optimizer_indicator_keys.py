import pathlib

SRC = pathlib.Path("backend/app/optimizer.py").read_text(encoding="utf-8")
NEW = ["vel_n", "vel_z_window", "vr_q", "vr_lookback", "vr_scale", "bb_len", "bb_mult",
       "kc_len", "kc_atr_mult", "sqz_mom_len", "st_period", "st_mult",
       "cpr_narrow_pctile", "cpr_wide_pctile", "cpr_pctile_window",
       "tod_lookback_sessions", "tod_min_atr_frac"]


def _keys_tuple_text() -> str:
    i = SRC.index("INDICATOR_PARAM_KEYS")
    return SRC[i:SRC.index(")", i)]  # text of the tuple literal


def test_new_period_params_registered_in_keys_tuple():
    block = _keys_tuple_text()
    for k in NEW:
        assert f'"{k}"' in block, f"{k} not registered in INDICATOR_PARAM_KEYS"
