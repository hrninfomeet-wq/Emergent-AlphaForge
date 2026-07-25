"""Tests for backend/app/market_analysis.py — pure market-analysis primitives."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.market_analysis import (  # noqa: E402
    classify_trend,
    classify_structure,
    put_call_ratio,
    max_pain,
    atm_straddle,
    iv_rank,
    levels_from_sr,
)


CHAIN = [
    {"strike": 24100, "ce_oi": 120000, "pe_oi": 290000, "ce_ltp": 168.4, "pe_ltp": 41.0},
    {"strike": 24150, "ce_oi": 150000, "pe_oi": 180000, "ce_ltp": 120.0, "pe_ltp": 60.0},
    {"strike": 24200, "ce_oi": 200000, "pe_oi": 150000, "ce_ltp": 78.2, "pe_ltp": 92.4},
    {"strike": 24250, "ce_oi": 90000, "pe_oi": 80000, "ce_ltp": 45.0, "pe_ltp": 140.0},
]


# ---------------------------------------------------------------------------
# classify_trend
# ---------------------------------------------------------------------------

def test_classify_trend_rising():
    closes = [100, 101, 102, 103, 104, 106, 108]
    assert classify_trend(closes) == "up"


def test_classify_trend_falling():
    closes = [108, 106, 104, 103, 102, 101, 100]
    assert classify_trend(closes) == "down"


def test_classify_trend_noisy_flat():
    closes = [100, 100.1, 99.9, 100.05, 99.95, 100.02, 99.98]
    assert classify_trend(closes) == "flat"


def test_classify_trend_single_point():
    assert classify_trend([100]) == "flat"


def test_classify_trend_empty():
    assert classify_trend([]) == "flat"


# ---------------------------------------------------------------------------
# classify_structure
# ---------------------------------------------------------------------------

def test_classify_structure_strong_uptrend():
    closes = [100, 102, 104, 106, 108, 110, 113]
    r = classify_structure(closes=closes, adx=28)
    assert r["kind"] == "trending"
    assert r["regime_bucket"] == 4
    assert r["label"] == "STRONG UPTREND"
    assert r["buckets"] == 5
    assert 0.0 <= r["confidence"] <= 1.0
    assert isinstance(r["why"], str) and r["why"]


def test_classify_structure_noisy_flat_low_adx():
    closes = [100, 100.1, 99.9, 100.05, 99.95, 100.02, 99.98]
    r = classify_structure(closes=closes, adx=12)
    assert r["kind"] in ("range", "choppy")
    assert r["regime_bucket"] == 2
    assert r["buckets"] == 5


def test_classify_structure_falling_trending():
    closes = [113, 110, 108, 106, 104, 102, 100]
    r = classify_structure(closes=closes, adx=30)
    assert r["kind"] == "trending"
    assert r["regime_bucket"] == 0
    assert r["label"] == "BEARISH"


def test_classify_structure_adx_none_no_exception():
    closes = [100, 102, 104, 106, 108]
    r = classify_structure(closes=closes, adx=None)
    assert r["kind"] == "transitional"
    assert set(r.keys()) == {"label", "kind", "regime_bucket", "buckets", "confidence", "why"}


def test_classify_structure_empty_closes_no_exception():
    r = classify_structure(closes=[], adx=None)
    assert r["kind"] == "transitional"
    assert r["buckets"] == 5
    assert 0.0 <= r["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# put_call_ratio
# ---------------------------------------------------------------------------

def test_put_call_ratio_basic():
    total_pe = 290000 + 180000 + 150000 + 80000
    total_ce = 120000 + 150000 + 200000 + 90000
    expected = total_pe / total_ce
    assert put_call_ratio(CHAIN) == expected


def test_put_call_ratio_empty_chain():
    assert put_call_ratio([]) is None


def test_put_call_ratio_zero_ce_oi():
    chain = [{"strike": 100, "ce_oi": 0, "pe_oi": 500, "ce_ltp": 1.0, "pe_ltp": 1.0}]
    assert put_call_ratio(chain) is None


def test_put_call_ratio_missing_oi_treated_as_zero():
    chain = [{"strike": 100, "ce_oi": 100, "pe_oi": None, "ce_ltp": 1.0, "pe_ltp": 1.0}]
    assert put_call_ratio(chain) == 0.0


# ---------------------------------------------------------------------------
# max_pain
# ---------------------------------------------------------------------------

def test_max_pain_basic():
    # Brute-force expectation computed directly from the CHAIN payoff formula.
    strikes = [row["strike"] for row in CHAIN]
    best_strike = None
    best_payout = None
    for k in strikes:
        payout = 0.0
        for row in CHAIN:
            payout += row["ce_oi"] * max(0, k - row["strike"])
            payout += row["pe_oi"] * max(0, row["strike"] - k)
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = k
    assert max_pain(CHAIN) == best_strike


def test_max_pain_empty_chain():
    assert max_pain([]) is None


def test_max_pain_ties_pick_lowest_strike():
    chain = [
        {"strike": 100, "ce_oi": 0, "pe_oi": 0, "ce_ltp": 1.0, "pe_ltp": 1.0},
        {"strike": 200, "ce_oi": 0, "pe_oi": 0, "ce_ltp": 1.0, "pe_ltp": 1.0},
    ]
    # All-zero OI -> every strike has payout 0 -> tie -> lowest strike wins.
    assert max_pain(chain) == 100


# ---------------------------------------------------------------------------
# atm_straddle
# ---------------------------------------------------------------------------

def test_atm_straddle_nearest_strike():
    r = atm_straddle(CHAIN, spot=24187.7)
    assert r["strike"] == 24200
    assert round(r["straddle"], 2) == 170.6
    assert r["implied_move_pct"] == round(170.6 / 24187.7 * 100, 2)


def test_atm_straddle_empty_chain():
    assert atm_straddle([], spot=24000) is None


def test_atm_straddle_missing_ltp():
    chain = [{"strike": 100, "ce_oi": 1, "pe_oi": 1, "ce_ltp": None, "pe_ltp": 5.0}]
    assert atm_straddle(chain, spot=100) is None


# ---------------------------------------------------------------------------
# iv_rank
# ---------------------------------------------------------------------------

def test_iv_rank_atm_iv_source():
    history = [10, 12, 14, 16, 18, 20, 22, 24]
    r = iv_rank(current_iv=18, iv_history=history)
    below_or_eq = sum(1 for v in history if v <= 18)
    assert r["rank"] == round(below_or_eq / len(history), 3)
    assert r["source"] == "atm_iv"
    assert r["warning"] is None


def test_iv_rank_vix_proxy_when_iv_history_thin():
    vix_hist = [12, 13, 14, 15, 16, 17, 18, 19]
    r = iv_rank(current_iv=None, iv_history=[10, 11], vix=15, vix_history=vix_hist)
    assert r["source"] == "vix_proxy"
    assert r["warning"] == "iv_history_thin"
    below_or_eq = sum(1 for v in vix_hist if v <= 15)
    assert r["rank"] == round(below_or_eq / len(vix_hist), 3)


def test_iv_rank_unavailable():
    r = iv_rank(current_iv=None, iv_history=[], vix=None, vix_history=None)
    assert r["rank"] is None
    assert r["source"] == "unavailable"
    assert r["warning"] == "iv_unavailable"


def test_iv_rank_thin_iv_history_and_no_vix_is_unavailable():
    r = iv_rank(current_iv=15, iv_history=[10, 11, 12], vix=None, vix_history=None)
    assert r["rank"] is None
    assert r["source"] == "unavailable"
    assert r["warning"] == "iv_unavailable"


# ---------------------------------------------------------------------------
# levels_from_sr
# ---------------------------------------------------------------------------

def test_levels_from_sr_partition_and_ordering():
    sr = {"resistance": [24300, 24500, 24250], "support": [24000, 23900, 24100]}
    spot = 24187.7
    r = levels_from_sr(sr, spot=spot, pivot=24180.0)
    assert r["spot"] == spot
    assert r["pivot"] == 24180.0
    # supports strictly below spot, descending (nearest first), max 3
    assert r["supports"] == [24100, 24000, 23900]
    # resistances strictly above spot, ascending (nearest first), max 3
    assert r["resistances"] == [24250, 24300, 24500]
    assert r["nearest_wall"] == {"side": "R", "price": 24250}
    s1, r1 = 24100, 24250
    expected_pos = round(max(0.0, min(1.0, (spot - s1) / (r1 - s1))), 3)
    assert r["position_in_range"] == expected_pos
    assert 0.0 <= r["position_in_range"] <= 1.0


def test_levels_from_sr_empty_no_exception():
    r = levels_from_sr({"resistance": [], "support": []}, spot=24000)
    assert r["supports"] == []
    assert r["resistances"] == []
    assert r["nearest_wall"] is None
    assert r["position_in_range"] is None


def test_levels_from_sr_malformed_input_no_exception():
    r = levels_from_sr({}, spot=24000)
    assert r["supports"] == []
    assert r["resistances"] == []
    assert r["nearest_wall"] is None


# ---------------------------------------------------------------------------
# Malformed-input hardening (orchestrator review finding, 2026-07-22)
#
# These functions run inside a LIVE endpoint that renders a risk surface from
# broker/warehouse chain rows. A single malformed numeric field (an OI of "bad",
# a blank LTP, a string strike) must degrade to the documented sentinel, never
# raise out of the request. put_call_ratio originally raised ValueError here.
# ---------------------------------------------------------------------------

MALFORMED_CHAIN = [
    {"strike": "24100", "ce_oi": None, "pe_oi": "bad", "ce_ltp": "", "pe_ltp": 41.0},
    {"strike": 24200, "ce_oi": "n/a", "pe_oi": 240000, "ce_ltp": 78.2, "pe_ltp": 92.4},
]


def test_put_call_ratio_never_raises_on_malformed_oi():
    # "bad"/None coerce to 0 → total CE OI is 0 → documented None, not ValueError
    assert put_call_ratio(MALFORMED_CHAIN) is None
    assert put_call_ratio([{"ce_oi": "x", "pe_oi": 10}]) is None
    assert put_call_ratio([{"ce_oi": 10, "pe_oi": "x"}]) == 0.0


def test_max_pain_never_raises_on_malformed_rows():
    assert max_pain(MALFORMED_CHAIN) in (24100.0, 24200.0)
    assert max_pain([{"strike": "junk", "ce_oi": 1, "pe_oi": 1}]) == 0.0


def test_atm_straddle_never_raises_on_malformed_rows():
    out = atm_straddle(MALFORMED_CHAIN, spot="24187.7")
    assert out["strike"] == 24200.0 and out["straddle"] == 78.2 + 92.4


def test_levels_from_sr_never_raises_on_malformed_levels():
    out = levels_from_sr({"support": ["24135", None], "resistance": ["bad", 24240]},
                         spot="24187.7")
    assert 24135.0 in out["supports"]
    assert 24240.0 in out["resistances"]
