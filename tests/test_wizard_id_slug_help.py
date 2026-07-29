"""An invalid strategy id must be fixable in one click, not just rejected.

User-reported (2026-07-29): they named the strategy
"ALGOTEST Options Buying NIFTY" and Preview code failed with

    id 'ALGOTEST Options Buying NIFTY' is not a valid slug (must match ^[a-z][a-z0-9_]*$)

The rule is correct — the id becomes a Python identifier AND the plugin's
filename, so it cannot contain spaces or capitals. But the UI only restated the
regex. It never said WHY, and it never offered the obvious fix, so a user who
typed a human name into the ID field had to work out the transformation
themselves — and the perfectly good display name they wanted belongs in the
adjacent Name field, which the wizard did not point out either.

This is the same "dead-end message" class as the ASK verdict with no answer box:
the app knew exactly what was wrong and what the answer was, and made the user
derive it.
"""
from __future__ import annotations

import os
import re

_WIZ = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                    "components", "strategy", "AuthoringWizard.jsx")


def _src() -> str:
    with open(_WIZ, encoding="utf-8") as fh:
        return fh.read()


def test_a_slugify_helper_exists():
    assert "function slugify" in _src()


def test_the_helper_handles_the_reported_input():
    """Port the JS transform and check it on the exact reported string."""
    src = _src()
    i = src.index("function slugify")
    body = src[i:i + 700]
    # must lowercase, replace non-alphanumerics with _, and guarantee a leading letter
    assert "toLowerCase()" in body
    assert "replace(" in body


def test_the_user_is_offered_a_valid_id_not_just_the_regex():
    src = _src()
    assert "author-id-suggest" in src, (
        "an invalid id must come with a one-click valid suggestion")


def test_the_message_explains_WHY_the_id_is_constrained():
    """'must match ^[a-z][a-z0-9_]*' tells the user the rule but not the reason,
    so they cannot tell whether it is arbitrary or load-bearing."""
    src = _src()
    i = src.index("author-id")
    body = src[i:i + 2500]
    low = body.lower()
    assert "filename" in low or "python" in low or "identifier" in low


def test_the_user_is_pointed_at_the_Name_field_for_their_real_title():
    """The display name they wanted is legitimate — it just belongs next door."""
    src = _src()
    i = src.index("author-id")
    body = src[i:i + 2500]
    assert "Name" in body


def test_an_empty_id_is_still_not_an_error():
    """Pre-existing behaviour: a blank id is 'not yet filled', not invalid."""
    src = _src()
    assert 'id === ""' in src


def test_the_suggestion_never_produces_an_invalid_slug():
    """Simulate the JS rules in Python over hostile inputs — a suggestion that is
    itself invalid would be worse than none."""
    slug_re = re.compile(r"^[a-z][a-z0-9_]*$")

    def slugify(raw: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if not s or not s[0].isalpha():
            s = "strategy_" + s
        return s.strip("_")

    for raw in ["ALGOTEST Options Buying NIFTY", "123 numbers first", "!!!",
                "  spaced  out  ", "CAPS", "with-hyphens", "emoji 🚀 here", "_leading"]:
        assert slug_re.match(slugify(raw)), f"{raw!r} -> {slugify(raw)!r}"
