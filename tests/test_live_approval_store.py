"""Tests for ApprovalStore — per-trade approval queue + one-shot token (P1.6).

The safety-critical properties: an approval is redeemable EXACTLY once (no
replay / double-execution), the token is never leaked except at creation, and a
stale approval expires (cannot fire against a stale price).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.approval_store import (  # noqa: E402
    STATUS_APPROVED,
    STATUS_CONSUMED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    ApprovalStore,
)


def _counter_factory():
    """Deterministic id factory: id-0, id-1, ... (so tests are reproducible)."""
    n = {"i": -1}

    def _next():
        n["i"] += 1
        return f"id-{n['i']}"

    return _next


def _store(ttl_sec=120):
    return ApprovalStore(ttl_sec=ttl_sec, id_factory=_counter_factory())


_PAYLOAD = [{"tsym": "NIFTY26JUN26C25000", "qty": "65", "prctyp": "LMT"}]
_SUMMARY = {"underlying": "NIFTY", "lots": 1, "side": "B"}
_T0 = "2026-06-23T10:00:00+00:00"


class TestCreate:
    def test_create_returns_pending_with_token(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        assert rec["status"] == STATUS_PENDING
        assert rec["approval_id"] == "id-0"
        assert rec["token"] == "id-1"
        assert rec["payload"] == _PAYLOAD
        assert rec["summary"]["underlying"] == "NIFTY"

    def test_get_does_not_leak_token(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        fetched = s.get(rec["approval_id"])
        assert "token" not in fetched

    def test_list_pending_does_not_leak_token(self):
        s = _store()
        s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        pend = s.list_pending(_T0)
        assert len(pend) == 1
        assert "token" not in pend[0]


class TestApproveOneShot:
    def test_approve_with_token_returns_payload(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        res = s.approve(rec["approval_id"], rec["token"], _T0)
        assert res["ok"] is True
        assert res["payload"] == _PAYLOAD
        assert s.get(rec["approval_id"])["status"] == STATUS_APPROVED

    def test_replay_same_token_fails(self):
        """One-shot: the SAME token can never be redeemed twice (no double-exec)."""
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        first = s.approve(rec["approval_id"], rec["token"], _T0)
        assert first["ok"] is True
        second = s.approve(rec["approval_id"], rec["token"], _T0)
        assert second["ok"] is False
        assert "not pending" in second["reason"]

    def test_wrong_token_fails_and_keeps_pending(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        res = s.approve(rec["approval_id"], "WRONG", _T0)
        assert res["ok"] is False
        assert res["reason"] == "bad_token"
        # still pending — the legit operator can still approve
        assert s.get(rec["approval_id"])["status"] == STATUS_PENDING
        ok = s.approve(rec["approval_id"], rec["token"], _T0)
        assert ok["ok"] is True

    def test_empty_token_fails(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        assert s.approve(rec["approval_id"], "", _T0)["ok"] is False
        assert s.approve(rec["approval_id"], None, _T0)["ok"] is False

    def test_approve_unknown_id(self):
        s = _store()
        assert s.approve("nope", "tok", _T0)["ok"] is False
        assert s.approve("nope", "tok", _T0)["reason"] == "not_found"


class TestReject:
    def test_reject_pending(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        res = s.reject(rec["approval_id"], _T0)
        assert res["ok"] is True
        assert s.get(rec["approval_id"])["status"] == STATUS_REJECTED

    def test_cannot_approve_after_reject(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.reject(rec["approval_id"], _T0)
        res = s.approve(rec["approval_id"], rec["token"], _T0)
        assert res["ok"] is False

    def test_cannot_reject_approved(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        assert s.reject(rec["approval_id"], _T0)["ok"] is False


class TestExpiry:
    def test_pending_expires_after_ttl(self):
        s = _store(ttl_sec=120)
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        # 121s later
        late = "2026-06-23T10:02:01+00:00"
        res = s.approve(rec["approval_id"], rec["token"], late)
        assert res["ok"] is False
        assert "expired" in res["reason"]
        assert s.get(rec["approval_id"])["status"] == STATUS_EXPIRED

    def test_within_ttl_still_approvable(self):
        s = _store(ttl_sec=120)
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        within = "2026-06-23T10:01:59+00:00"  # 119s
        assert s.approve(rec["approval_id"], rec["token"], within)["ok"] is True

    def test_list_pending_excludes_expired(self):
        s = _store(ttl_sec=120)
        s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        late = "2026-06-23T10:05:00+00:00"
        assert s.list_pending(late) == []


class TestConsume:
    def test_mark_consumed_after_approve(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        res = s.mark_consumed(rec["approval_id"], _T0)
        assert res["ok"] is True
        assert s.get(rec["approval_id"])["status"] == STATUS_CONSUMED

    def test_cannot_consume_pending(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        assert s.mark_consumed(rec["approval_id"], _T0)["ok"] is False

    def test_cannot_consume_twice(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        assert s.mark_consumed(rec["approval_id"], _T0)["ok"] is True
        assert s.mark_consumed(rec["approval_id"], _T0)["ok"] is False


class TestListPendingScoping:
    def test_only_pending_listed(self):
        s = _store()
        a = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        b = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        c = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(a["approval_id"], a["token"], _T0)
        s.reject(b["approval_id"], _T0)
        pend = s.list_pending(_T0)
        ids = [p["approval_id"] for p in pend]
        assert ids == [c["approval_id"]]


class TestRevertToPending:
    """revert_to_pending un-strands an approved-but-not-placed approval so it can
    be retried/rejected — without weakening the no-double-place guarantee."""

    def test_revert_returns_to_pending(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        res = s.revert_to_pending(rec["approval_id"], _T0)
        assert res["ok"] is True
        assert s.get(rec["approval_id"])["status"] == STATUS_PENDING
        # re-appears in the pending list
        assert s.list_pending(_T0)[0]["approval_id"] == rec["approval_id"]

    def test_original_token_still_works_after_revert(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        s.revert_to_pending(rec["approval_id"], _T0)
        # the SAME token re-approves (no token rotation needed on the client)
        again = s.approve(rec["approval_id"], rec["token"], _T0)
        assert again["ok"] is True
        assert again["payload"] == _PAYLOAD

    def test_reverted_can_be_rejected(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        s.revert_to_pending(rec["approval_id"], _T0)
        assert s.reject(rec["approval_id"], _T0)["ok"] is True

    def test_cannot_revert_consumed(self):
        """A PLACED (consumed) order must never be reverted/re-placed."""
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        s.approve(rec["approval_id"], rec["token"], _T0)
        s.mark_consumed(rec["approval_id"], _T0)
        res = s.revert_to_pending(rec["approval_id"], _T0)
        assert res["ok"] is False
        assert s.get(rec["approval_id"])["status"] == STATUS_CONSUMED

    def test_cannot_revert_pending_or_rejected(self):
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        assert s.revert_to_pending(rec["approval_id"], _T0)["ok"] is False  # still pending
        s.reject(rec["approval_id"], _T0)
        assert s.revert_to_pending(rec["approval_id"], _T0)["ok"] is False  # rejected

    def test_revert_unknown(self):
        s = _store()
        assert s.revert_to_pending("nope", _T0)["ok"] is False

    def test_no_double_place_across_revert_cycle(self):
        """approve→revert→approve→consume must allow EXACTLY one consume."""
        s = _store()
        rec = s.create(payload=_PAYLOAD, summary=_SUMMARY, now_iso=_T0)
        aid, tok = rec["approval_id"], rec["token"]
        s.approve(aid, tok, _T0)
        s.revert_to_pending(aid, _T0)          # first placement failed
        s.approve(aid, tok, _T0)               # retry
        assert s.mark_consumed(aid, _T0)["ok"] is True   # placed
        # any further attempt is blocked (consumed is terminal)
        assert s.approve(aid, tok, _T0)["ok"] is False
        assert s.revert_to_pending(aid, _T0)["ok"] is False
        assert s.mark_consumed(aid, _T0)["ok"] is False
