"""Contract pins for the Optimizer "Optimization Setup" form hints (2026-06-30).

Every setup field carries a "?"-hint (what it does + optimum + inter-relations),
authored + adversarially fact-checked against the backend optimizer code. These
are string-asserts on the frontend source (the repo's frontend tests are pytest
text pins; no JS test runner is wired up), mirroring
test_backtest_performance_overview.py::test_backtest_form_fields_have_hints.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FE = ROOT / "frontend" / "src"


def _read(*parts):
    return (FE.joinpath(*parts)).read_text(encoding="utf-8")


def test_optimizer_defaults_to_option_net_evaluation():
    """For an option-buying app the optimizer must default to ranking the winner
    by REAL paired-option net rupee (option_rerank), not the spot proxy — a
    2026-07 audit found spot-optimal configs that lose badly on real options."""
    page = _read("pages", "Optimizer.jsx")
    assert 'evaluation_mode: "option_rerank"' in page
    assert 'evaluation_mode: "spot"' not in page  # spot must not be the default


def test_optimizer_form_fields_have_hints():
    page = _read("pages", "Optimizer.jsx")
    # Hint affordance + the hint-capable Row (label + optional hint).
    assert "const Hint = " in page and "HelpCircle" in page
    assert "function Row({ label, hint, children })" in page
    # The 7 top-level selectors pass a hint via the Row hint prop.
    assert page.count("hint={<>") >= 7
    # Broad coverage: the bulk of fields annotated with an inline <Hint>.
    assert page.count("<Hint") >= 28


def test_optimizer_hint_content_is_present_and_grounded():
    page = _read("pages", "Optimizer.jsx")
    for needle in (
        # core selectors
        "Bayesian (TPE) is the default",
        "Risk-Adjusted is the balanced default",
        "Verify a Single run with walk-forward before trusting it",
            "forward gates decide promotion",
        # trial controls + guards
        "becomes a ceiling, not a target",       # trial budget / early-stop
        "Keep 1 for a deploy decision",           # parallel workers
            "basic screen, not significance",         # min trades
        # option execution
        "Keep ATM unless you have a reason",      # moneyness
        "0DTE",                                   # dte filter
        # survivability
        "Calmar (default)",                       # survival objective
    ):
        assert needle in page, needle


def test_optimizer_hints_keep_the_verifiers_corrections():
    # Lock in the adversarial fact-checks so a future re-author can't silently
    # reintroduce the false claims the verify pass caught.
    page = _read("pages", "Optimizer.jsx")
    # Method: switching method resets n_trials to a fixed per-method default; it
    # does NOT auto-scale by param count, and grid is swapped to bayesian in WFO.
    assert "Grid is auto-swapped to Bayesian in walk-forward" in page
    # Parallel workers: not a guaranteed speedup — experimental/non-deterministic.
    assert "more than 1 is experimental" in page
    # Option costs are NOT a hard gate for survival — the panel "expects" them on.
    assert "expects costs ON" in page
    # BE trigger: a user-typed 0.0 is stripped by parseGrid (n>0), so it can't
    # add a no-breakeven variant — only the blank backend default does.
    assert "a 0 you type is dropped" in page
    # Exit-control search is gated off in walk-forward mode.
    assert "single runs only (not walk-forward)" in page
