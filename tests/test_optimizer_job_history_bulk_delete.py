"""Job History needs the same multi-select delete the Backtest run journal has.

User request (2026-07-30): "IN the optimizer page, job history pane, add checkbox
for multiple selections so that multiple saved optimization results can be
deleted. That functionality should be similar to Backtest lab page's backtest run
journal pane."

No backend work: `DELETE /api/optimize/jobs/{job_id}` and `api.deleteOptJob`
already exist and single-row delete already uses them. This is a UI affordance
only, mirrored from `BacktestRunJournal.jsx` so the two panes behave identically:
a per-row checkbox in its own leading cell whose click does NOT bubble to the
row's load handler, a `Delete N` button that only appears once something is
selected, a confirm, and a selection reset + refresh afterwards.

The row-click-loads-the-job behaviour is pre-existing and must survive: a
checkbox that also loaded the job would make bulk-selecting several rows fire
several loads.
"""
from __future__ import annotations

import os
import re

_OPT = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "pages",
                    "Optimizer.jsx")
_JOURNAL = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                        "components", "BacktestRunJournal.jsx")


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _history_block():
    """Just the JobHistory component, so a match elsewhere on the page cannot
    make these assertions pass by accident."""
    s = _src(_OPT)
    i = s.index("function JobHistory(")
    return s[i:i + 9000]


def test_rows_carry_a_selection_checkbox():
    body = _history_block()
    assert 'type="checkbox"' in body


def test_a_selection_set_is_tracked():
    body = _history_block()
    assert "selected" in body and "new Set()" in body


def test_a_bulk_delete_action_exists():
    body = _history_block()
    assert "opt-history-bulk-delete" in body, (
        "the pane needs a testable bulk-delete control")


def test_bulk_delete_is_hidden_until_something_is_selected():
    """Mirrors the journal: no destructive button sitting there by default."""
    body = _history_block()
    assert "selected.size > 0" in body


def _bulk_delete_fn():
    """The bulkDelete handler body. Anchored on the function rather than a
    byte-window around the button, so reordering the component cannot silently
    turn these assertions into no-ops."""
    body = _history_block()
    i = body.index("const bulkDelete")
    return body[i:i + 900]


def test_bulk_delete_asks_for_confirmation():
    """Deleting many saved optimizations is not undoable."""
    assert "confirm(" in _bulk_delete_fn()


def test_the_checkbox_click_does_not_also_load_the_job():
    """The row's onClick loads the job into the panel. Selecting several rows
    must not fire several loads."""
    body = _history_block()
    i = body.index('type="checkbox"')
    cell = body[max(0, i - 400):i]
    assert "stopPropagation" in cell


def test_it_reuses_the_existing_delete_endpoint():
    """No new backend surface — single-row delete already uses this."""
    api = _src(os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                            "lib", "api.js"))
    assert "deleteOptJob" in api
    assert "deleteOptJob" in _history_block()


def test_selection_is_cleared_after_a_bulk_delete():
    """Stale ids would make the count lie and re-delete attempts 404."""
    assert re.search(r"setSelected\(new Set\(\)\)", _bulk_delete_fn())


def test_bulk_delete_refreshes_the_list():
    """Otherwise the deleted rows stay on screen until a manual refresh."""
    assert "onRefresh" in _bulk_delete_fn()


def test_the_single_row_delete_still_exists():
    """Additive change — the per-row trash button must not be replaced."""
    body = _history_block()
    assert "opt-delete-" in body


def test_the_header_colspan_still_matches_the_column_count():
    """The empty-state rows span the table; adding a column without updating
    colSpan leaves a visibly short border."""
    body = _history_block()
    ths = len(re.findall(r"<(?:th|SortHeader)\b", body))
    spans = {int(m) for m in re.findall(r'colSpan="(\d+)"', body)}
    assert spans, "empty-state rows should still span the table"
    assert all(s == ths for s in spans), (
        f"colSpan {spans} does not match {ths} header cells")


def test_the_journal_pattern_is_the_reference():
    """Guard the guard: if the journal ever loses these affordances, this test
    file is asserting against a pattern that no longer exists."""
    j = _src(_JOURNAL)
    assert 'type="checkbox"' in j and "journal-bulk-delete" in j
