"""SP-2 seed structural features (ICT vocabulary) for capability-aware authoring.

Each feature is causal (trailing-window / shift / forward-pass only), reuses the
pure helpers in app.indicators where possible, and is registered into the SP-1
FeatureGroup registry at import time via app.features.catalog.register_feature.

Host-importable: imports only pandas / numpy / app.indicators / app.features.* --
no motor, no I/O (same discipline as indicator_groups.py).

Live feasibility (see feature_live_feasible): swing_levels / premium_discount /
displacement are vectorized + bounded -> live-correct. fvg_zones / choch /
order_block are stateful_unbounded -> backtest-only in v1.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from app.features.registry import FeatureGroup
from app.features.catalog import register_feature

# ---- compute fns + registrations are appended by the following tasks ----


# ---------------------------------------------------------------------------
# FEATURE 1 — swing_levels
# ---------------------------------------------------------------------------

def compute_swing_levels(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    high, low = df["high"], df["low"]
    is_sh, is_sl = df["is_swing_high"], df["is_swing_low"]
    last_high = high.where(is_sh).ffill().shift(1)
    last_low = low.where(is_sl).ffill().shift(1)
    swept_high = (high > last_high).fillna(False)
    swept_low = (low < last_low).fillna(False)
    return {
        "last_swing_high_level": last_high,
        "last_swing_low_level": last_low,
        "swing_high_swept": swept_high,
        "swing_low_swept": swept_low,
    }


register_feature(
    FeatureGroup(
        name="swing_levels",
        columns=("last_swing_high_level", "last_swing_low_level",
                 "swing_high_swept", "swing_low_swept"),
        param_keys=(),
        requires=(),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_swing_levels,
    ),
    description="Most-recent confirmed swing high/low price levels (causal, shifted) "
                "plus liquidity-sweep flags. Foundation for premium/discount, BOS, "
                "and order blocks.",
    data_requirements=["ohlcv_1m"],
)


# ---------------------------------------------------------------------------
# FEATURE 2 — premium_discount
# ---------------------------------------------------------------------------

def compute_premium_discount(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    hi = df["last_swing_high_level"]
    lo = df["last_swing_low_level"]
    rng = (hi - lo)
    pct = 100.0 * (df["close"] - lo) / rng.where(rng > 0, np.nan)
    state = np.where(pct.isna(), None,
             np.where(pct > 55.0, "premium",
              np.where(pct < 45.0, "discount", "equilibrium")))
    return {
        "premium_discount_pct": pct,
        "range_state": pd.Series(state, index=df.index, dtype=object),
    }


register_feature(
    FeatureGroup(
        name="premium_discount",
        columns=("premium_discount_pct", "range_state"),
        param_keys=(),
        requires=("swing_levels",),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_premium_discount,
    ),
    description="Position of price within the last swing range as a 0-100 percent "
                "(premium >55, discount <45, equilibrium between). Requires swing_levels.",
    data_requirements=["ohlcv_1m"],
)


# ---------------------------------------------------------------------------
# FEATURE 3 — displacement + BOS
# ---------------------------------------------------------------------------

def compute_displacement(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    atr_mult = float(params.get("disp_atr_mult", 1.5))
    body_min = float(params.get("disp_body_frac_min", 0.5))
    o, c, h, lo = df["open"], df["close"], df["high"], df["low"]
    atr = df["atr"]
    body = (c - o).abs()
    rng = (h - lo)
    body_frac = body / rng.where(rng > 0, np.nan)
    disp = ((body >= atr_mult * atr) & (body_frac >= body_min)).fillna(False)
    bos_up = (c > df["last_swing_high_level"]).fillna(False)
    bos_down = (c < df["last_swing_low_level"]).fillna(False)
    return {"displacement": disp, "bos_up": bos_up, "bos_down": bos_down}


register_feature(
    FeatureGroup(
        name="displacement",
        columns=("displacement", "bos_up", "bos_down"),
        param_keys=("disp_atr_mult", "disp_body_frac_min"),
        requires=("swing_levels",),
        cost_class="vectorized",
        session_anchored=False,
        stateful_unbounded=False,
        min_history_bars=2,
        compute=compute_displacement,
    ),
    description="Displacement (large impulsive body vs ATR) and break-of-structure "
                "flags (close beyond the last swing level). Requires swing_levels.",
    data_requirements=["ohlcv_1m"],
)


# ---------------------------------------------------------------------------
# FEATURE 4 — choch (change-of-character)
# ---------------------------------------------------------------------------

def compute_choch(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    bu = df["bos_up"].fillna(False).to_numpy(dtype=bool)
    bd = df["bos_down"].fillna(False).to_numpy(dtype=bool)
    n = len(df)
    up = np.zeros(n, dtype=bool)
    down = np.zeros(n, dtype=bool)
    direction = 0
    for i in range(n):
        new = direction
        if bu[i]:
            new = 1
        elif bd[i]:
            new = -1
        if new == 1 and direction == -1:
            up[i] = True
        elif new == -1 and direction == 1:
            down[i] = True
        direction = new
    return {
        "choch_up": pd.Series(up, index=df.index),
        "choch_down": pd.Series(down, index=df.index),
    }


register_feature(
    FeatureGroup(
        name="choch",
        columns=("choch_up", "choch_down"),
        param_keys=(),
        requires=("displacement",),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=2,
        compute=compute_choch,
    ),
    description="Change-of-character: the running market-structure direction flips "
                "(bullish<->bearish) on a counter break of structure. Stateful "
                "(depends on history before the rolling window) -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)


# ---------------------------------------------------------------------------
# FEATURE 5 — fvg_zones
# ---------------------------------------------------------------------------

def compute_fvg_zones(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    from app.indicators import detect_fvg
    fdir = df["fvg"] if "fvg" in df.columns else detect_fvg(df)
    fdir = fdir.to_numpy(dtype=object)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    ce = np.full(n, np.nan)
    state = np.empty(n, dtype=object)
    direction = np.empty(n, dtype=object)
    cur_top = cur_bot = np.nan
    cur_dir = None
    cur_state = "none"
    for i in range(n):
        d = fdir[i]
        if d == "UP" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i - 2], low[i], "UP", "active"
        elif d == "DOWN" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i], low[i - 2], "DOWN", "active"
        elif cur_state == "active":
            if cur_dir == "UP" and low[i] <= cur_bot:
                cur_state = "filled"
            elif cur_dir == "DOWN" and high[i] >= cur_top:
                cur_state = "filled"
        top[i] = cur_top
        bot[i] = cur_bot
        ce[i] = (cur_top + cur_bot) / 2.0 if cur_dir is not None else np.nan
        direction[i] = cur_dir
        state[i] = cur_state
    idx = df.index
    return {
        "fvg_top": pd.Series(top, index=idx),
        "fvg_bottom": pd.Series(bot, index=idx),
        "fvg_ce": pd.Series(ce, index=idx),
        "fvg_dir": pd.Series(direction, index=idx, dtype=object),
        "fvg_state": pd.Series(state, index=idx, dtype=object),
    }


register_feature(
    FeatureGroup(
        name="fvg_zones",
        columns=("fvg_top", "fvg_bottom", "fvg_ce", "fvg_dir", "fvg_state"),
        param_keys=(),
        requires=(),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=3,
        compute=compute_fvg_zones,
    ),
    description="Fair Value Gap zones: the active 3-candle imbalance boundaries "
                "(top/bottom/midpoint), direction, and fill state. The active gap may "
                "predate the rolling window -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)


# ---------------------------------------------------------------------------
# FEATURE 6 — order_block
# ---------------------------------------------------------------------------

def compute_order_block(df: pd.DataFrame, params: dict) -> Dict[str, pd.Series]:
    lb = min(int(params.get("ob_lookback", 10)), 20)
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    disp = df["displacement"].to_numpy(dtype=bool)
    n = len(df)
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    direction = np.empty(n, dtype=object)
    active = np.zeros(n, dtype=bool)
    cur_top = cur_bot = np.nan
    cur_dir = None
    cur_active = False
    for i in range(n):
        if disp[i] and c[i] > o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] < o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bull", True
                    break
        elif disp[i] and c[i] < o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] > o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bear", True
                    break
        elif cur_active:
            if cur_dir == "bull" and l[i] <= cur_bot:
                cur_active = False
            elif cur_dir == "bear" and h[i] >= cur_top:
                cur_active = False
        top[i] = cur_top
        bot[i] = cur_bot
        direction[i] = cur_dir
        active[i] = cur_active
    idx = df.index
    return {
        "ob_top": pd.Series(top, index=idx),
        "ob_bottom": pd.Series(bot, index=idx),
        "ob_dir": pd.Series(direction, index=idx, dtype=object),
        "ob_active": pd.Series(active, index=idx),
    }


register_feature(
    FeatureGroup(
        name="order_block",
        columns=("ob_top", "ob_bottom", "ob_dir", "ob_active"),
        param_keys=("ob_lookback",),
        requires=("displacement",),
        cost_class="session_loop",
        session_anchored=False,
        stateful_unbounded=True,
        min_history_bars=2,
        compute=compute_order_block,
    ),
    description="Order block: the last opposing candle before a displacement (bounded "
                "lookback <=20), carried until mitigated. Requires displacement; "
                "stateful -> backtest-only in v1.",
    data_requirements=["ohlcv_1m"],
)
