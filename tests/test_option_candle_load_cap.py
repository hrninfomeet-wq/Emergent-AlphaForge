"""Regression: the paired-option backtest must never SILENTLY drop the newest option candles.

`_run_paired_option_backtest` (runtime.py) loads option candles oldest-first
(`.sort("ts", 1)`) and previously capped the load at 1,000,000 rows. On a long
date range whose option-candle set exceeds the cap, the oldest-first sort means
the rows that get dropped are the NEWEST ones — so every trade in the most recent
period reports `missing_entry_candle` (0% pairing) while the response still looks
plausible, with no warning. (Verified 2026-06-16 against the running stack: a
19-month NIFTY confluence_scalper backtest paired 0% in 2026-05/06, while a
12-month run over the same end paired 99.8% including those exact months — the
data was fully present; the 1M cap dropped it.)

runtime.py imports motor and is not host-importable, so — like
test_broker_empty_ledger.py — this pins the fix as a source contract rather than
a behavioural test. The sibling option-candle loaders
(`optimizer._option_rerank`, `wfo`) already use a 4,000,000-row cap AND log a
warning when the cap is hit; this brings the runtime loader in line and, in
addition, surfaces a `candles_capped` flag in the response so a truncated load
is never silent to the caller/UI.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _paired_backtest_fn() -> str:
    """The source text of `_run_paired_option_backtest` only (up to the next def)."""
    src = (ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    start = src.index("async def _run_paired_option_backtest")
    end = src.index("\nasync def ", start + 1)
    return src[start:end]


def _option_candle_load_block() -> str:
    """The option-candle load region (`if selected_keys:` -> simulate call).

    Scoped deliberately so it EXCLUDES the earlier India-VIX `candles_1m` load,
    which queries a single instrument and cannot realistically hit the cap — only
    the multi-strike `options_1m` load is the silent-drop hazard.
    """
    fn = _paired_backtest_fn()
    start = fn.index("if selected_keys:")
    end = fn.index("result = simulate_paired_option_trades")
    return fn[start:end]


def test_option_candle_load_is_not_capped_at_one_million():
    block = _option_candle_load_block()
    assert "to_list(length=1000000)" not in block, (
        "the options_1m candle load still uses the 1,000,000-row cap; combined with "
        "the oldest-first sort this silently drops the NEWEST candles on long ranges "
        "(0% pairing in the most recent period, no warning)"
    )


def test_option_candle_load_cap_is_raised_to_four_million():
    src = (ROOT / "backend" / "app" / "runtime.py").read_text(encoding="utf-8")
    block = _option_candle_load_block()
    # The options_1m load must request a 4,000,000-row cap — matching the
    # optimizer/_option_rerank and wfo loaders — either inline or via a named
    # constant that resolves to 4,000,000. A named constant is preferred: it keeps
    # the to_list(length=...) and the cap-hit `>=` check provably the same value.
    if "OPTION_CANDLE_LOAD_CAP" in block:
        assert ("OPTION_CANDLE_LOAD_CAP = 4_000_000" in src
                or "OPTION_CANDLE_LOAD_CAP = 4000000" in src), (
            "the option-candle load uses OPTION_CANDLE_LOAD_CAP but it is not "
            "defined as 4,000,000"
        )
    else:
        assert ("4_000_000" in block or "4000000" in block), (
            "the options_1m candle load cap must be raised to 4,000,000 to match the "
            "optimizer/_option_rerank and wfo loaders"
        )


def test_capped_option_candle_load_emits_a_warning():
    block = _option_candle_load_block()
    assert "log.warning" in block, (
        "hitting the option-candle load cap must emit log.warning so the truncation "
        "is never silent (mirrors optimizer._option_rerank / wfo)"
    )


def test_capped_option_candle_load_is_surfaced_in_response():
    fn = _paired_backtest_fn()
    assert '"candles_loaded"' in fn  # anchor: the response data block exists
    assert '"candles_capped"' in fn, (
        "a capped option-candle load must be surfaced in the response data block "
        "(e.g. candles_capped: true) so a silently-truncated load is detectable"
    )


# ---- frontend: the capped load must be VISIBLE in the backtest journal ----------
# The response flag alone is invisible to a normal user reading the journal, so the
# Option Execution card must render a banner when data.candles_capped is true.


def test_backtest_journal_banners_a_capped_option_candle_load():
    lab = (ROOT / "frontend" / "src" / "pages" / "BacktestLab.jsx").read_text(encoding="utf-8")
    assert "candles_capped" in lab, (
        "the backtest journal must read data.candles_capped to warn on a truncated "
        "option-candle load"
    )
    assert "option-candles-capped-warning" in lab, (
        "a capped option-candle load must render a dedicated banner in the Option "
        "Execution card, so the silent-drop is visible in the UI and not only in the "
        "response JSON / server log"
    )
