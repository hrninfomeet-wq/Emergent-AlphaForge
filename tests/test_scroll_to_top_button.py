"""The floating "back to top" control must attach to the element that actually scrolls.

THE TRAP: this app does not scroll the window. `Layout` renders every page inside

    <div className="flex-1 min-w-0 overflow-y-auto p-4" data-testid="page-content">

so that div is the scroller, and a conventional `window.scrollTo(0, 0)` implementation
would compile, render, click — and do nothing at all, on every page. These lock the
wiring to the real scroll container.

Rendering is verified separately by scrolling the live app in a browser.
"""
from __future__ import annotations

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BTN = os.path.join(ROOT, "frontend", "src", "components", "common", "ScrollToTopButton.jsx")
LAYOUT = os.path.join(ROOT, "frontend", "src", "components", "Layout.jsx")


@pytest.fixture(scope="module")
def btn() -> str:
    with open(BTN, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def btn_code(btn) -> str:
    """`btn` with // comments stripped.

    The component's own docstring names `window.scrollTo(0, 0)` as the thing NOT to
    do, so a raw substring check on the file would flag the explanation rather than
    an actual call.
    """
    out = []
    for line in btn.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue
        out.append(line.split("//")[0] if "//" in line else line)
    return chr(10).join(out)


@pytest.fixture(scope="module")
def layout() -> str:
    with open(LAYOUT, encoding="utf-8") as fh:
        return fh.read()


def test_it_resolves_the_real_scroller_at_runtime(btn_code):
    """MEASURED on /backtest at 1280x800: `page-content` has overflow-y-auto but the
    flex chain is not height-constrained, so it GROWS — scrollHeight 5690 ==
    clientHeight 5690, zero scrollable — while documentElement had 5239px of scroll.
    Binding to either one alone gives a button that renders, clicks and does nothing
    (container-only today; document-only if the layout is ever height-constrained)."""
    assert "resolveScroller" in btn_code
    assert "scrollRef?.current" in btn_code
    assert "document.scrollingElement" in btn_code
    # container wins only when it actually scrolls
    assert "el.scrollHeight - el.clientHeight > 1" in btn_code


def test_it_listens_on_both_candidates(btn_code):
    assert 'window.addEventListener("scroll"' in btn_code
    assert 'container.addEventListener("scroll"' in btn_code
    assert 'window.removeEventListener("scroll"' in btn_code
    assert 'container.removeEventListener("scroll"' in btn_code
    assert "passive: true" in btn_code


def test_it_stays_hidden_until_the_page_is_actually_scrolled(btn):
    assert "el.scrollTop > threshold" in btn
    # and never appears on a page that barely overflows
    assert "el.scrollHeight - el.clientHeight > threshold" in btn


def test_it_reacts_to_content_height_changes(btn):
    """Route changes and expanding panels alter scrollHeight without firing scroll."""
    assert "ResizeObserver" in btn
    assert "ro.disconnect()" in btn


def test_it_respects_reduced_motion(btn_code):
    assert "prefers-reduced-motion" in btn_code
    assert 'const behavior = reduce ? "auto" : "smooth";' in btn_code
    assert "el.scrollTo({ top: 0, behavior })" in btn_code


def test_it_is_reachable_and_labelled_for_assistive_tech(btn):
    assert 'aria-label="Scroll back to top"' in btn
    assert "type=\"button\"" in btn
    # hidden state must leave the tab order, not just fade out
    assert "tabIndex={visible ? 0 : -1}" in btn
    assert "aria-hidden={!visible}" in btn
    assert "pointer-events-none" in btn


def test_it_is_pinned_bottom_right_and_above_page_content(btn):
    assert "fixed bottom-5 right-5" in btn
    assert "z-40" in btn


def test_layout_mounts_it_once_against_the_real_scroller(layout):
    """One button in Layout covers /backtest, /optimizer, /warehouse, /paper, ..."""
    assert "const scrollRef = useRef(null);" in layout
    assert '<div ref={scrollRef} className="flex-1 min-w-0 overflow-y-auto p-4" data-testid="page-content">' in layout
    assert layout.count("<ScrollToTopButton") == 1
    assert "scrollRef={scrollRef}" in layout


def test_the_toast_corner_is_not_reused(layout):
    """Sonner is configured top-right; the button must not fight it. If toasts are
    ever moved to bottom-right this test should fail and force a rethink."""
    app = os.path.join(ROOT, "frontend", "src", "App.js")
    with open(app, encoding="utf-8") as fh:
        src = fh.read()
    assert 'position="top-right"' in src
