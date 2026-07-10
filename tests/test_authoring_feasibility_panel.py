"""Phase 0: the feasibility (ruleSet) panel must render in BOTH authoring modes,
not just spec — it lives OUTSIDE the `mode === "spec"` block. HOST test (reads JSX)."""
from pathlib import Path

_FE = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _src(rel):
    return (_FE / rel).read_text(encoding="utf-8")


def test_feasibility_panel_is_outside_the_spec_mode_block():
    src = _src("components/strategy/AuthoringWizard.jsx")
    spec_gate = src.index('{mode === "spec" && (')
    # the ruleSet feasibility panel's decision chip must appear BEFORE the spec gate
    rule_panel = src.index("{ruleSet && (")
    assert rule_panel < spec_gate, (
        "the {ruleSet && (...)} feasibility panel is still nested inside the "
        "mode===spec block; Full-Python mode will render a blank verdict"
    )
