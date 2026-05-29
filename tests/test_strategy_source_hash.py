"""Tests for the strategy source-file hash drift detection (slice 8)."""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.strategy_source_hash import (  # noqa: E402
    detect_drift,
    hash_strategy_source,
    strategy_file_path,
)
from app.strategies.base import StrategyBase  # noqa: E402


# ---- detect_drift pure logic ------------------------------------------------


def test_detect_drift_returns_false_when_pinned_missing():
    assert detect_drift(pinned=None, current="abc123") is False
    assert detect_drift(pinned="", current="abc123") is False


def test_detect_drift_returns_false_when_current_missing():
    assert detect_drift(pinned="abc123", current=None) is False
    assert detect_drift(pinned="abc123", current="") is False


def test_detect_drift_returns_false_when_hashes_match():
    assert detect_drift(pinned="abc123", current="abc123") is False


def test_detect_drift_returns_true_only_when_both_present_and_differ():
    assert detect_drift(pinned="abc123", current="def456") is True


# ---- hash_strategy_source against a real plugin file -----------------------


def test_hash_strategy_source_returns_16_hex_chars_for_real_plugin():
    """A registered builtin strategy has a real .py file - hash should resolve."""
    from app.strategies.builtin.confluence_scalper import ConfluenceScalper

    h = hash_strategy_source(ConfluenceScalper())
    assert h is not None
    assert len(h) == 16
    # SHA hex must be 0-9 and a-f only
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_strategy_source_is_deterministic():
    """Same file -> same hash on repeated calls."""
    from app.strategies.builtin.confluence_scalper import ConfluenceScalper

    h1 = hash_strategy_source(ConfluenceScalper())
    h2 = hash_strategy_source(ConfluenceScalper())
    assert h1 == h2


def test_hash_strategy_source_different_files_give_different_hashes():
    from app.strategies.builtin.confluence_scalper import ConfluenceScalper
    from app.strategies.builtin.fibonacci_pullback import FibonacciPullback

    h1 = hash_strategy_source(ConfluenceScalper())
    h2 = hash_strategy_source(FibonacciPullback())
    assert h1 != h2


def test_hash_strategy_source_returns_none_for_unresolvable_class():
    """A class whose module can't be located should not raise; return None."""

    class InMemoryStrategy(StrategyBase):
        id = "in_memory_test"
        name = "test"
        version = "1.0.0"

    # Strip the module marker so the loader can't find a file. We mutate a copy of
    # the class spec via a dynamic class so we don't pollute the test module.
    obj = InMemoryStrategy()
    type(obj).__module__ = "non_existent_module"
    h = hash_strategy_source(obj)
    assert h is None


def test_hash_strategy_source_returns_none_for_none_input():
    assert hash_strategy_source(None) is None


# ---- strategy_file_path helper ---------------------------------------------


def test_strategy_file_path_returns_existing_file_for_real_plugin():
    from app.strategies.builtin.confluence_scalper import ConfluenceScalper

    path = strategy_file_path(ConfluenceScalper())
    assert path is not None
    assert path.is_file()
    assert path.suffix == ".py"
    assert "confluence_scalper" in path.name


def test_strategy_file_path_returns_none_for_none_input():
    assert strategy_file_path(None) is None
