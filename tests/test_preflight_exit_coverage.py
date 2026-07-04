"""Option preflight must require BOTH entry- and exit-side candles.

Pre-2026-07-05 the preflight counted a trade as 'would_pair' when only the
ENTRY candle existed, so coverage_pct overstated real pairing: an illiquid
strike with an entry print but an exit-side gap showed as covered, then
dropped as MISSING_EXIT_CANDLE in the actual run. These pins the two-gate
contract shared with simulate_paired_option_trades.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.option_backtest import (  # noqa: E402
    _has_candle_at_or_before,
    preflight_trade_pairs,
)

ENTRY_AGE = 120_000  # 120s
EXIT_AGE = 180_000   # 180s


def test_both_sides_present_pairs():
    ts = [1_000_000, 1_000_060_000, 1_000_120_000, 1_000_180_000]
    assert preflight_trade_pairs(ts, 1_000_000, 1_000_180_000, ENTRY_AGE, EXIT_AGE) is True


def test_entry_present_but_exit_gap_does_not_pair():
    # entry candle exists; the exit is 5 min after the last candle -> gap.
    ts = [1_000_000, 1_000_060_000]
    exit_ts = 1_000_060_000 + 300_000  # 5 min past the last candle
    assert _has_candle_at_or_before(ts, 1_000_000, ENTRY_AGE) is True
    assert preflight_trade_pairs(ts, 1_000_000, exit_ts, ENTRY_AGE, EXIT_AGE) is False


def test_exit_present_but_entry_gap_does_not_pair():
    ts = [1_000_180_000]  # only a candle at exit time, none near entry
    assert preflight_trade_pairs(ts, 1_000_000, 1_000_180_000, ENTRY_AGE, EXIT_AGE) is False


def test_no_candles_does_not_pair():
    assert preflight_trade_pairs([], 1_000_000, 1_000_180_000, ENTRY_AGE, EXIT_AGE) is False


def test_at_or_before_respects_max_age_and_direction():
    ts = [1_000_000]
    # exactly at boundary passes
    assert _has_candle_at_or_before(ts, 1_000_000 + ENTRY_AGE, ENTRY_AGE) is True
    # one ms past the age window fails
    assert _has_candle_at_or_before(ts, 1_000_000 + ENTRY_AGE + 1, ENTRY_AGE) is False
    # a candle strictly AFTER the target is not eligible (at-or-before only)
    assert _has_candle_at_or_before(ts, 999_999, ENTRY_AGE) is False


def test_intraday_hold_with_full_coverage_pairs():
    ts = list(range(1_000_000, 1_000_000 + 60_000 * 30, 60_000))  # 30 one-min candles
    assert preflight_trade_pairs(ts, ts[2], ts[27], ENTRY_AGE, EXIT_AGE) is True
