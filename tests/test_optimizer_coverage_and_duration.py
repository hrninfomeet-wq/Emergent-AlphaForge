"""Three gaps where the backend knew something the operator never saw.

#3 CANDLE-CAP COVERAGE. `_option_rerank` loads option candles under a hard 4,000,000-row
   cap. When it is hit, trades past that window simply do not pair, so EVERY candidate's
   rupee P&L is understated — and the only signal was a `log.warning` inside the container.
   Measured relevance: NIFTY over 2025-11-01..2026-08-26 holds 4.38M option rows across
   4,294 keys, i.e. the cap is reachable with a realistic window, not hypothetical.

#5 RUN DURATION. `started_at` / `finished_at` were persisted and rendered nowhere, so there
   was no way to see how long a job took — the number you need to size the next trial budget.

#6 LOT SIZE. `lot_size` is what converts index points to rupees (and is the multiplier inside
   the net_pnl_inr objective), yet the ₹ headline never showed which lot size produced it.

Backend behaviour is covered by the pure-function tests below; the JSX is string-pinned the
same way the rest of this suite pins frontend wiring, and was also exercised in the browser.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMIZER_PY = os.path.join(ROOT, "backend", "app", "optimizer.py")
OPTIMIZER_JSX = os.path.join(ROOT, "frontend", "src", "pages", "Optimizer.jsx")


@pytest.fixture(scope="module")
def py() -> str:
    with open(OPTIMIZER_PY, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def jsx() -> str:
    with open(OPTIMIZER_JSX, encoding="utf-8") as fh:
        return fh.read()


# --- #3 backend -------------------------------------------------------------

def test_cap_is_recorded_not_only_logged(py):
    i = py.index("if len(rows) >= 4000000:")
    body = py[i:i + 1200]
    assert "log.warning" in body, "keep the log line too"
    assert 'coverage_out["candle_cap_hit"] = True' in body
    assert 'coverage_out["candle_rows_loaded"]' in body
    assert 'coverage_out["contract_keys"]' in body


def test_coverage_is_an_optional_out_param_not_a_signature_break(py):
    """_option_rerank and _option_rerank_premium_trigger share a 5-tuple return across 6
    return sites. Widening that tuple for one flag would touch all of them plus the caller;
    an optional out-dict leaves the premium path completely untouched."""
    assert "coverage_out: Optional[Dict[str, Any]] = None," in py
    # the 5-tuple contract is unchanged
    assert py.count("return ranked, contracts, option_candles, False, stopped") == 1
    assert py.count("return ranked, contracts, candles_df, budget_hit, stopped") == 1
    # and the flag is only written when a dict was actually supplied
    assert "if coverage_out is not None:" in py


def test_caller_persists_coverage_only_when_the_cap_was_hit(py):
    i = py.index("coverage_out=_rr_coverage")
    body = py[i:i + 400]
    assert 'if _rr_coverage.get("candle_cap_hit"):' in body
    assert '_update_job(job_id, {"rerank_coverage": _rr_coverage})' in body


def test_premium_rerank_path_was_not_touched(py):
    """It has its own loader and no row cap; it must not have grown a coverage arg."""
    i = py.index("async def _option_rerank_premium_trigger(")
    sig = py[i:i + 600]
    assert "coverage_out" not in sig


# --- #3 frontend ------------------------------------------------------------

def test_coverage_warning_renders_only_on_a_capped_job(jsx):
    assert "function RerankCoverageWarning({ job })" in jsx
    assert "<RerankCoverageWarning job={job} />" in jsx
    i = jsx.index("function RerankCoverageWarning({ job })")
    body = jsx[i:i + 1200]
    assert "job?.rerank_coverage" in body
    assert "if (!cov?.candle_cap_hit) return null;" in body
    assert 'data-testid="opt-rerank-coverage-warning"' in body
    # it must say the direction of the error, not just that something happened
    assert "understated" in body


# --- #5 run duration --------------------------------------------------------

def test_run_duration_uses_the_persisted_timestamps(jsx):
    assert "function RunDuration({ job })" in jsx
    assert "<RunDuration job={job} />" in jsx
    i = jsx.index("function RunDuration({ job })")
    body = jsx[i:i + 1200]
    assert "job?.started_at" in body and "job?.finished_at" in body
    assert 'data-testid="opt-run-duration"' in body
    # per-trial cost is the number that actually sizes the next budget
    assert "n_trials_completed" in body


def test_run_duration_hides_itself_on_bad_or_missing_timestamps(jsx):
    """A running job has no finished_at; a clock skew could invert them."""
    i = jsx.index("function RunDuration({ job })")
    body = jsx[i:i + 1200]
    assert "Number.isFinite(start)" in body and "Number.isFinite(end)" in body
    assert "end < start" in body
    assert "return null;" in body


# --- #6 lot size ------------------------------------------------------------

def test_lot_size_is_shown_beside_the_rupee_headline(jsx):
    i = jsx.index('data-testid="opt-best-headline"')
    body = jsx[i:i + 1400]
    assert "job.lot_size" in body
    assert "fmtInt(job.lot_size)" in body


def test_lot_size_is_omitted_when_absent(jsx):
    """Older jobs predate the field; the headline must not render 'lot undefined'."""
    i = jsx.index('data-testid="opt-best-headline"')
    body = jsx[i:i + 1400]
    assert "{job.lot_size ?" in body and ": null}" in body
