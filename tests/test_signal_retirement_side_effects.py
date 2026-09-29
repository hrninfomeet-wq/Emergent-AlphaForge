"""Retiring ~1366 clean, untraded CONFIRMED signals to AUDITED must change their
STATE and nothing else an operator relies on.

A three-angle audit of every consumer of signal state / updated_at (2026-09-29)
found what the retirement would otherwise have changed:

  * transition_signal stamps updated_at, which is read as the REFUSAL time (the Live
    strip's `last_entry.at`, the timeline's "Entry refused" row) and as the AGE for
    "purge older than N days" — one sweep would have moved every refusal to the sweep
    instant and re-dated June's signals to today;
  * the Signal Journal's opt-in retention deletes AUDITED signals, built when AUDITED
    meant "blocked" — it would have started deleting clean trade-recommendation history;
  * the CSV export had no `blocked` column, so a retired clean row became
    indistinguishable from a blocked one;
  * the journal chip would have lost its EXPIRED label and read plain "AUDITED".
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.signal_lifecycle import (  # noqa: E402
    UNACTIONED_REASON, expire_unactioned_signals)
from tests.test_signal_bar_field import _DB, _enriched, _match, _real_signal  # noqa: E402

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)         # the next morning's boot
REFUSED_AT = "2026-09-29T08:30:10+00:00"                         # the real refusal instant


class _Sigs:
    """replace_one / delete_many over the bar-field fake, honouring the filter."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def find(self, q=None, proj=None):
        from tests.test_signal_bar_field import _Cur
        return _Cur([r for r in self.rows if _match_or(r, q or {})])

    async def replace_one(self, q, doc, upsert=False):
        for i, r in enumerate(self.rows):
            if _match_or(r, q):
                self.rows[i] = dict(doc)
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

    async def delete_many(self, q):
        keep = [r for r in self.rows if not _match_or(r, q)]
        n = len(self.rows) - len(keep)
        self.rows = keep
        return type("R", (), {"deleted_count": n})()


def _match_or(row, q):
    q = dict(q)
    ors = q.pop("$or", None)
    for k, v in list(q.items()):
        if isinstance(v, dict) and "$exists" in v:
            if (k in row) != bool(v["$exists"]):
                return False
            q.pop(k)
    if ors is not None and not any(_match(row, sub) for sub in ors):
        return False
    return _match(row, q)


class _Db:
    def __init__(self, rows):
        self.signals = _Sigs(rows)


def _refused(sig_id="r1", **kw):
    s = _real_signal(sig_id, **kw)
    s.update(live_trade_error="stale_authorization:caps:max_concurrent",
             updated_at=REFUSED_AT)
    return s


# --------------------------------------------------------------------------- #
# updated_at is the signal's own, never the sweep's
# --------------------------------------------------------------------------- #

def test_the_retirement_keeps_the_signals_own_updated_at():
    db = _Db([_refused()])
    assert asyncio.run(expire_unactioned_signals(db, now_utc=NOW)) == 1
    s = db.signals.rows[0]
    assert s["state"] == "AUDITED"
    assert s["updated_at"] == REFUSED_AT, "the sweep re-dated the signal"
    assert s["audited_at"] == NOW.isoformat()
    assert s["events"][-1]["at"] == NOW.isoformat()
    assert s["events"][-1]["reason"] == UNACTIONED_REASON


def test_the_timeline_keeps_the_refusal_at_the_refusal_minute():
    from app.live_timeline import _Events, _add_signal
    db = _Db([_refused()])
    asyncio.run(expire_unactioned_signals(db, now_utc=NOW))
    ev = _Events(0, 2 ** 62)
    _add_signal(ev, db.signals.rows[0])
    refused = [dt for dt, e in ev.items if e["kind"] == "refused"]
    assert refused == [datetime.fromisoformat(REFUSED_AT)], \
        f"the refusal moved to the sweep: {refused}"


# --------------------------------------------------------------------------- #
# retention deletes blocked noise, never clean history
# --------------------------------------------------------------------------- #

def _purge(monkeypatch, rows, **req):
    import app.routers.journals as j
    from app.schemas import SignalsPurgeReq
    db = _Db(rows)
    monkeypatch.setattr(j, "get_db", lambda: db)
    out = asyncio.run(j.purge_signals(SignalsPurgeReq(**req)))
    return out, db


def _audited(sig_id, blocked, days_old=90):
    s = _real_signal(sig_id)
    s.update(state="AUDITED", blocked=blocked,
             updated_at=(NOW - timedelta(days=days_old)).isoformat())
    return s


def test_retention_deletes_only_blocked_audited_signals(monkeypatch):
    rows = [_audited("blocked-old", True), _audited("clean-old", False),
            _audited("blocked-new", True, days_old=1)]
    out, db = _purge(monkeypatch, rows, older_than_days=30, states=["AUDITED"], blocked=True)
    assert out["deleted"] == 1
    assert {r["id"] for r in db.signals.rows} == {"clean-old", "blocked-new"}


def test_blocked_false_selects_only_clean_ones(monkeypatch):
    rows = [_audited("b", True), _audited("c", False), {**_audited("legacy", None)}]
    out, db = _purge(monkeypatch, rows, older_than_days=30, blocked=False)
    assert {r["id"] for r in db.signals.rows} == {"b"}, "blocked=False must mean 'not blocked'"


def test_no_blocked_criterion_keeps_the_old_behaviour(monkeypatch):
    rows = [_audited("b", True), _audited("c", False)]
    out, db = _purge(monkeypatch, rows, older_than_days=30, states=["AUDITED"])
    assert out["deleted"] == 2


def test_the_journal_retention_asks_for_blocked_only():
    """Wiring pin: the one-line call site the retention behaviour hangs on."""
    src = (ROOT / "frontend" / "src" / "pages" / "SignalJournal.jsx").read_text(encoding="utf-8")
    assert 'api.purgeSignals({ older_than_days: n, states: ["AUDITED"], blocked: true })' in src


# --------------------------------------------------------------------------- #
# the CSV says which rows were blocked
# --------------------------------------------------------------------------- #

def test_the_csv_carries_the_blocked_flag(monkeypatch):
    clean = _audited("clean", False)
    blocked = _audited("blk", True)
    blocked["blockers"] = ["regime"]
    db = _DB([clean, blocked])
    resp = _enriched(monkeypatch, db, format="csv")
    body = resp.body.decode("utf-8") if hasattr(resp, "body") else str(resp)
    rows = list(csv.DictReader(io.StringIO(body)))
    assert "blocked" in rows[0], f"no blocked column: {list(rows[0])}"
    got = {r["state"] + "/" + r["blocked"] for r in rows}
    assert got == {"AUDITED/False", "AUDITED/True"}, got


# --------------------------------------------------------------------------- #
# the journal chip keeps telling the truth after retirement
# --------------------------------------------------------------------------- #

node = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _view(sig: dict):
    from tests.test_signal_display import _run_js
    return _run_js(f"return M.signalDisplay({json.dumps(sig)}, Date.parse('2026-09-30T04:00:00Z'));")


@node
def test_a_retired_clean_signal_reads_expired_not_audited():
    v = _view({"state": "AUDITED", "blocked": False})
    assert v["label"] == "EXPIRED" and v["tone"] == "expired" and v["state"] == "AUDITED"


@node
def test_a_retired_refused_signal_reads_not_acted_on_with_its_reason():
    v = _view({"state": "AUDITED", "blocked": False, "live_trade_error": "caps:max_concurrent"})
    assert v["label"] == "NOT ACTED ON" and "caps:max_concurrent" in v["note"]


@node
@pytest.mark.parametrize("sig", [
    {"state": "AUDITED", "blocked": True},                       # blocked by the filter
    {"state": "AUDITED"},                                        # blocked flag unknown
    {"state": "AUDITED", "blocked": False, "paper_trade_id": "t1"},  # traded (or a dangling link)
    {"state": "AUDITED", "blocked": False, "live_trade_id": "t2"},
    {"state": "AUDITED", "blocked": False, "paper_trade_claim": {"source": "auto_paper"}},
])
def test_other_audited_signals_stay_plain_audited(sig):
    v = _view(sig)
    assert v["label"] == "AUDITED" and v["tone"] == "normal"


@node
def test_a_signal_whose_linked_trade_is_gone_reads_retired():
    """The 646 signals retired from ACTIVE on 2026-09-29 point at paper trades that no
    longer exist. /signals/enriched finds no trade (trade_status null) — say so."""
    v = _view({"state": "AUDITED", "blocked": False, "paper_trade_id": "gone",
               "trade_status": None})
    assert v["label"] == "RETIRED" and "no longer exists" in v["note"]


@node
@pytest.mark.parametrize("sig", [
    {"state": "AUDITED", "blocked": False, "paper_trade_id": "t1", "trade_status": "CLOSED"},
    {"state": "AUDITED", "blocked": False, "paper_trade_id": "t1"},      # status unknown
    {"state": "AUDITED", "blocked": True, "paper_trade_id": "t1", "trade_status": None},
])
def test_a_trade_that_exists_or_is_unknown_is_not_called_retired(sig):
    assert _view(sig)["label"] == "AUDITED"


@node
def test_the_expired_note_does_not_claim_the_session_ended():
    """The 15:00 sweep retires bars from earlier TODAY, while the session is open."""
    note = _view({"state": "AUDITED", "blocked": False})["note"]
    assert "session" not in note.lower() and "bar had passed" in note


def test_the_csv_says_whether_a_row_was_traded_and_whether_the_trade_exists(monkeypatch):
    s = _audited("gone", False)
    s["paper_trade_id"] = "missing-trade"
    resp = _enriched(monkeypatch, _DB([s]), format="csv")
    row = next(csv.DictReader(io.StringIO(resp.body.decode("utf-8"))))
    assert row["paper_trade_id"] == "missing-trade" and row["trade_status"] == ""
