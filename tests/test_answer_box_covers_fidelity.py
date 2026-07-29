"""The answer box must appear whenever there is an open question — from EITHER source.

User-reported (2026-07-29). I gated the answers box on the FEASIBILITY verdict
being `ASK`. But a strategy can pass feasibility cleanly (BUILD, no ambiguous
rules) and still come back from GENERATION with `fidelity.ambiguous` items — a
second, independent set of questions the model wants answered.

That is exactly what happened: verdict BUILD, then

    "ambiguous: Global target profit and global stop loss were unspecified..."

...with no box anywhere, because `ruleSet.decision` was not `ASK`. The same
dead-end the ASK channel was built to remove, arriving through the other door.

The bug was keying the UI on a VERDICT rather than on "is there something to
answer". The box now renders when either source has an open question, and the
answers already flow to both the re-check and the generator.
"""
from __future__ import annotations

import os
import re

_WIZ = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                    "components", "strategy", "AuthoringWizard.jsx")


def _src() -> str:
    with open(_WIZ, encoding="utf-8") as fh:
        return fh.read()


def _panel_condition() -> str:
    """What actually decides whether the answers panel renders.

    The panel is guarded by a NAMED signal rather than an inline expression, so
    the decision lives at that signal's definition — checking the JSX alone would
    pass vacuously.
    """
    src = _src()
    i = src.index("const hasOpenQuestions")
    definition = src[i:src.index(";", i) + 1]
    j = src.index("author-answers-panel")
    guard = src[max(0, j - 300):j]
    return definition + "\n---GUARD---\n" + guard


def test_the_box_is_not_gated_on_the_ASK_verdict_alone():
    """THE regression: BUILD + a generate-time ambiguity left no channel."""
    cond = _panel_condition()
    assert 'ruleSet.decision === "ASK" && (\n' not in cond, (
        "the panel must not be gated on the feasibility verdict alone — a BUILD "
        "verdict can still produce fidelity.ambiguous items")


def test_a_generate_time_ambiguity_opens_the_box():
    cond = _panel_condition()
    assert "ambiguous" in cond, (
        "the panel must also open when the generation fidelity readback reports "
        "ambiguous items")


def test_the_box_still_opens_on_an_ASK_verdict():
    """The original path must keep working."""
    cond = _panel_condition()
    assert "ASK" in cond
    assert "hasOpenQuestions" in cond, "the panel must be guarded by the shared signal"


def test_there_is_a_single_shared_open_question_signal():
    """Keying two renders off two different booleans is how these drift apart —
    one derived flag, used by the panel and by the footer note."""
    src = _src()
    assert "hasOpenQuestions" in src, (
        "expected one derived signal for 'something needs answering'")


def test_the_answers_reach_generation_not_only_the_recheck():
    """An answer channel that only feeds the feasibility re-check would not fix a
    GENERATE-time ambiguity at all."""
    src = _src()
    i = src.index("api.authorFromSource")
    assert "answers" in src[i:i + 200], (
        "generation must receive the answers, or answering changes nothing")


def test_the_footer_note_no_longer_says_only_recheck():
    """'then re-check' is wrong advice for a generate-time ambiguity — the user
    needs to re-GENERATE for it to take effect."""
    src = _src()
    i = src.index("install-gate-note")
    body = src[i:i + 700]
    assert "generate" in body.lower()
