"""Unit tests for the Paper-blotter redesign backend filters (spec 2026-06-22).

Pure-function tests only (no live DB): the exit_price sort allowlist, the
exit-reason bucketing precedence, the Mongo bucket-query builder, and the
query-composition helper. A tiny Mongo-condition evaluator (`_match`) proves the
bucket queries select exactly the right docs without needing MongoDB.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.runtime import _TRADES_SORT_FIELDS  # noqa: E402
from app.paper_analytics import (  # noqa: E402
    normalize_exit_reason,
    exit_reason_query,
    merge_conditions,
)


# --- Task 1: exit_price sortable -------------------------------------------------

def test_exit_price_is_sortable():
    assert "exit_price" in _TRADES_SORT_FIELDS
    for f in ("created_at", "closed_at", "entry_price", "realized_pnl", "mfe_value", "mae_value"):
        assert f in _TRADES_SORT_FIELDS


# --- Task 2: normalize_exit_reason precedence ------------------------------------

# Every raw exit_reason the backend can write (spec §2), mapped to its bucket.
RAW_BY_BUCKET = {
    "target": ["target_hit", "spot_target_hit"],
    "manual": ["manual_square_off", "manual_close_at_market"],
    "eod": ["auto_square_off_15_00_IST"],
    "stop": ["stop_hit", "spot_stop_hit"],
    "other": ["time_stop"],
}
ALL_RAW = [(raw, bucket) for bucket, raws in RAW_BY_BUCKET.items() for raw in raws]
BUCKETS = ("target", "manual", "eod", "stop", "other")


def test_normalize_exit_reason_buckets_every_raw_value():
    for raw, bucket in ALL_RAW:
        assert normalize_exit_reason(raw) == bucket, f"{raw} -> {normalize_exit_reason(raw)} (want {bucket})"


def test_normalize_exit_reason_manual_squareoff_is_manual_not_eod():
    # contains BOTH "manual" and "square"; manual must win
    assert normalize_exit_reason("manual_square_off") == "manual"


def test_normalize_exit_reason_time_stop_is_other_not_stop():
    assert normalize_exit_reason("time_stop") == "other"


# --- Task 3: exit_reason_query + merge_conditions --------------------------------

def _match_field(doc, field, spec):
    present = field in doc
    value = doc.get(field)
    if not isinstance(spec, dict):
        return value == spec
    for op, operand in spec.items():
        if op == "$options":
            continue
        if op == "$regex":
            flags = re.I if "i" in spec.get("$options", "") else 0
            if value is None or re.search(operand, str(value), flags) is None:
                return False
        elif op == "$ne":
            if value == operand:
                return False
        elif op == "$exists":
            if bool(operand) != present:
                return False
        elif op == "$not":
            if _match_field(doc, field, operand):
                return False
        else:
            raise AssertionError(f"unsupported field op {op}")
    return True


def _match(doc, cond):
    """Minimal MongoDB-condition evaluator: $and/$or/$nor + field operators."""
    for key, val in cond.items():
        if key == "$and":
            if not all(_match(doc, c) for c in val):
                return False
        elif key == "$or":
            if not any(_match(doc, c) for c in val):
                return False
        elif key == "$nor":
            if any(_match(doc, c) for c in val):
                return False
        elif not _match_field(doc, key, val):
            return False
    return True


def test_exit_reason_query_selects_only_its_own_bucket():
    for raw, bucket in ALL_RAW:
        doc = {"exit_reason": raw}
        assert _match(doc, exit_reason_query(bucket)), f"{raw} should match {bucket}"
        for other in BUCKETS:
            if other == bucket:
                continue
            assert not _match(doc, exit_reason_query(other)), f"{raw} wrongly matched {other}"


def test_exit_reason_query_agrees_with_normalizer():
    for raw, _bucket in ALL_RAW:
        doc = {"exit_reason": raw}
        matched = [b for b in BUCKETS if _match(doc, exit_reason_query(b))]
        assert matched == [normalize_exit_reason(raw)], (raw, matched)


def test_exit_reason_query_unknown_bucket_is_none():
    assert exit_reason_query("bogus") is None
    assert exit_reason_query("") is None


def test_open_trade_matches_no_exit_reason_bucket():
    doc = {"exit_reason": None}  # OPEN trade
    for b in BUCKETS:
        assert not _match(doc, exit_reason_query(b)), b


def test_merge_conditions_appends_without_clobbering_keys():
    q = {"status": "CLOSED", "deployment_id": "d1"}
    out = merge_conditions(q, [{"direction": "CE"}])
    assert out["status"] == "CLOSED" and out["deployment_id"] == "d1"
    assert out["$and"] == [{"direction": "CE"}]


def test_merge_conditions_extends_existing_and():
    q = {"$and": [{"a": 1}]}
    out = merge_conditions(q, [{"b": 2}])
    assert out["$and"] == [{"a": 1}, {"b": 2}]


def test_merge_conditions_empty_extra_is_noop():
    assert merge_conditions({"status": "OPEN"}, []) == {"status": "OPEN"}
