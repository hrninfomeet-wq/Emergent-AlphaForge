"""Tests for the shared intrabar stop/target exit engine."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.exit_engine import intrabar_exit  # noqa: E402


def test_long_target_hit():
    lvl, reason = intrabar_exit(high=120, low=95, stop=90, target=115, is_long=True)
    assert (lvl, reason) == (115, "TARGET")


def test_long_stop_hit():
    lvl, reason = intrabar_exit(high=105, low=85, stop=90, target=130, is_long=True)
    assert (lvl, reason) == (90, "STOP")


def test_long_stop_first_when_both_in_bar():
    # Bar spans both stop(90) and target(115): pessimistic -> stop fills first.
    lvl, reason = intrabar_exit(high=120, low=85, stop=90, target=115, is_long=True)
    assert (lvl, reason) == (90, "STOP")


def test_long_no_hit():
    lvl, reason = intrabar_exit(high=110, low=95, stop=90, target=120, is_long=True)
    assert (lvl, reason) == (None, None)


def test_short_index_pe_stop_above_target_below():
    # PE = short the index: stop ABOVE entry, target BELOW.
    # high 112 >= stop 110 -> STOP.
    lvl, reason = intrabar_exit(high=112, low=98, stop=110, target=90, is_long=False)
    assert (lvl, reason) == (110, "STOP")
    # low 88 <= target 90, high below stop -> TARGET.
    lvl2, reason2 = intrabar_exit(high=105, low=88, stop=110, target=90, is_long=False)
    assert (lvl2, reason2) == (90, "TARGET")


def test_short_stop_first_when_both():
    lvl, reason = intrabar_exit(high=112, low=88, stop=110, target=90, is_long=False)
    assert (lvl, reason) == (110, "STOP")


def test_target_first_when_stop_first_false():
    lvl, reason = intrabar_exit(high=120, low=85, stop=90, target=115, is_long=True, stop_first=False)
    assert (lvl, reason) == (115, "TARGET")


def test_none_levels_never_hit():
    lvl, reason = intrabar_exit(high=120, low=80, stop=None, target=None, is_long=True)
    assert (lvl, reason) == (None, None)
