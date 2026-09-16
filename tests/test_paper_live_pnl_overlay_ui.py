"""The /paper Trades panel must mark OPEN rows on the tick, not on the poll.

Reported 2026-09-16: P&L%, Net P&L and the P&L curve on /paper felt frozen next
to a real terminal. Measured, the backend was not the problem — the
`/paper/open-positions/stream` SSE feed was already delivering ~7.4 events/s with
live positions on it. The blotter simply never received it: PaperTrading rendered
`<TradeBlotter rows={data.items} …>` with no live marks, so all three columns
inherited the row poll, and that poll was bundled with `/paper/strategy-stats`
(measured ~1.55s) which forced it to 30s.

Two invariants matter more than the speed:
  * a CLOSED trade's realized P&L is final and must never be re-marked;
  * a stale mark must not read as a live one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / "frontend" / "src" / rel).read_text(encoding="utf-8")


BLOTTER = "components/paper/TradeBlotter.jsx"
PAGE = "pages/PaperTrading.jsx"


def test_the_page_hands_the_blotter_its_live_marks():
    page = _src(PAGE)
    assert "liveById" in page, "the page never builds a live-mark map"
    assert re.search(r"<TradeBlotter[^>]*liveById=\{liveById\}", page, re.S), (
        "TradeBlotter is still rendered without liveById — the tick feed cannot "
        "reach P&L%, Net P&L or the P&L curve"
    )


def test_the_map_is_keyed_by_trade_id():
    """The stream's item `id` IS the paper-trade id; anything else silently
    matches nothing and the overlay becomes a no-op that still looks wired."""
    page = _src(PAGE)
    block = page[page.index("const liveById"):]
    block = block[:block.index("}, [livePos])") + 14]
    assert "m.set(p.id, p)" in block, block[:400]


def test_open_rows_prefer_the_live_mark_and_closed_rows_never_do():
    blotter = _src(BLOTTER)
    assert "const lm = isOpen && liveById ? liveById.get(t.id) : null;" in blotter, (
        "the live mark is not gated on isOpen — a closed trade's realized P&L is "
        "final and must never be re-marked"
    )
    # net falls back to the polled running_pnl when there is no live mark.
    assert "liveNet != null ? liveNet : a.running_pnl" in blotter


def test_the_percentage_and_the_curve_derive_from_the_same_number():
    """If P&L% or the sparkline kept reading the polled value, the row would
    disagree with itself — a live Net P&L beside a stale percentage."""
    blotter = _src(BLOTTER)
    # pct is computed from `net`, which is the live-preferring value above.
    assert "const pct = notional ? (Number(net || 0) / notional) * 100 : null;" in blotter
    # the curve renders ctx.spark (live-extended), not the raw analytics series.
    assert "points={ctx.spark}" in blotter, "the sparkline still reads the polled series"
    assert "{ t: Date.now(), pnl: liveNet }" in blotter, "no live point is appended"


def test_a_stale_mark_is_shown_but_visibly_degraded():
    """`live_stale` means the feed went quiet for that contract. The last known
    price is still the best information available, so it is shown — but a number
    that has stopped moving must not look like a number that is flat."""
    blotter = _src(BLOTTER)
    assert "const liveStale = Boolean(lm && lm.live_stale);" in blotter
    assert "ctx.liveStale ?" in blotter


def test_the_expensive_drift_call_is_off_the_p_and_l_clock():
    """/paper/strategy-stats is per-deployment drift attribution and measured
    ~1.55s against 85ms for the rows. Bundled into the same Promise.all it set the
    floor for the whole page refresh."""
    page = _src(PAGE)
    fetch_rows = page[page.index("const fetchRows"):page.index("// Per-strategy drift attribution")]
    assert "paperStrategyStats" not in fetch_rows, (
        "strategy-stats is back inside fetchRows — it gates the money numbers again"
    )
    assert "const fetchStrategyStats" in page, "drift attribution has no slow clock of its own"


def test_the_row_poll_is_a_fallback_floor_not_the_primary_path():
    page = _src(PAGE)
    assert "window.setInterval(fetchRows, 5000)" in page, (
        "the row poll is not on a fast fallback cadence"
    )
    assert "window.setInterval(fetchStrategyStats, 60000)" in page


def test_liveposition_fallback_is_memoised():
    """`liveStream.data || {…}` allocates a new object every render, so every memo
    downstream of it recomputes forever. CRA's lint caught this once the overlay
    added more consumers; keep it pinned."""
    page = _src(PAGE)
    assert "const livePos = useMemo(" in page, "livePos is an unmemoised object literal again"
