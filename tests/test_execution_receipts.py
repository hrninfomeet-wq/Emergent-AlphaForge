"""Recorded-only receipts: pure evidence checks, no broker client or real DB."""
import ast
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.live.execution_receipts import execution_receipt, receipt_page


def order(**changes):
    return {"client_order_id": "client-a", "norenordno": "entry-a", "mode": "live",
            "state": "SUBMITTED", "intent": {"tsym": "SENSEX-TEST", "exch": "BFO",
            "trantype": "B", "qty": 100, "prc": 104, "prctyp": "LMT", "prd": "I"}, **changes}


def journal(**changes):
    return {"norenordno": "entry-a", "status": "CLOSED", "quantity": 100,
            "entry_price": 100, "exit_price": 110, "realized_pnl": 1000, **changes}


def test_partial_fills_do_not_certify_journal_or_rewrite_history():
    o, j = order(fillshares=60, avgprc=102), journal()
    before = copy.deepcopy([o, j])
    r = execution_receipt(o, [j], [])
    assert r["fills"]["partial"]
    assert r["journal"]["differences"] == ["quantity", "entry_price"]
    # Independent arithmetic: 60 * (110 - 102) = 480, versus 1000 journal gross.
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] == 480
    assert r["journal"]["gross_difference"] == -520
    assert r["journal"]["exit_evidence"] == "not_verified_by_receipt"
    assert [o, j] == before


def test_submitted_without_fills_is_unknown_not_zero_or_complete():
    r = execution_receipt(order(), [journal()], [])
    assert r["fills"]["quantity"] is None
    assert r["fills"]["evidence"] == "unavailable"
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] is None


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", -1, 1.5, {}, []])
def test_malformed_quantities_never_make_a_result(bad):
    r = execution_receipt(order(fillshares=bad, avgprc=102), [journal()], [])
    assert r["fills"]["quantity"] is None
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] is None
    json.dumps(r, allow_nan=False)


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", -1, 0, None])
def test_fills_without_a_valid_average_are_flagged(bad):
    r = execution_receipt(order(fillshares=60, avgprc=bad), [journal()], [])
    assert r["fills"]["evidence"] == "needs_review"
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] is None


@pytest.mark.parametrize("fields", [{"reconcile_required": True}, {"overfill": True},
                                    {"nord_mismatch": True}, {"post_terminal_fillshares": 80},
                                    {"fillshares": 101}])
def test_conflicting_order_evidence_never_used_for_pnl(fields):
    o = order(fillshares=60, avgprc=102)
    o.update(fields)
    r = execution_receipt(o, [journal()], [])
    assert r["fills"]["evidence"] == "needs_review"
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] is None


def test_zero_fills_and_foreign_or_duplicate_journals():
    r = execution_receipt(order(fillshares=0), [journal(norenordno="other")], [])
    assert r["fills"]["quantity"] == 0
    assert r["journal"] is None
    r = execution_receipt(order(fillshares=60, avgprc=102), [journal(), journal()], [])
    assert r["journal"] is None
    assert any("Multiple journal" in w for w in r["warnings"])


def test_no_pnl_for_open_journal_or_sell_instruction():
    o = order(fillshares=60, avgprc=102)
    assert execution_receipt(o, [journal(status="OPEN")], [])["journal"]["gross_at_journal_exit_using_entry_fills"] is None
    o["intent"]["trantype"] = "S"
    assert execution_receipt(o, [journal()], [])["journal"]["gross_at_journal_exit_using_entry_fills"] is None


def test_own_fill_journal_basis_compares_without_promoting_exit_mark():
    r = execution_receipt(order(fillshares="60", avgprc="102"), [journal(
        filled_quantity=60, entry_basis="own_fills", entry_basis_price=102, realized_pnl=480)], [])
    assert r["journal"]["differences"] == []
    assert r["journal"]["gross_difference"] == 0
    assert r["journal"]["exit_evidence"] == "not_verified_by_receipt"


def test_journal_contract_conflict_does_not_produce_pnl_estimate():
    r = execution_receipt(order(fillshares=60, avgprc=102), [journal(noren_tsym="OTHER")], [])
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] is None
    assert any("Journal contract differs" in w for w in r["warnings"])


def test_display_symbol_is_not_compared_to_broker_symbol():
    r = execution_receipt(order(fillshares=60, avgprc=102), [journal(
        trading_symbol="SENSEX TEST DISPLAY", noren_tsym="SENSEX-TEST")], [])
    assert not any("Journal contract differs" in w for w in r["warnings"])
    assert r["journal"]["gross_at_journal_exit_using_entry_fills"] == 480


def test_missing_approval_and_guard_evidence_not_promoted_to_safety():
    o = order(token="SECRET", uid="PRIVATE", payload={"password": "SECRET"})
    o["intent"].update(uid="PRIVATE", actid="PRIVATE", token="SECRET", remarks="SECRET")
    r = execution_receipt(o, [], None)
    assert r["approval_evidence"] == "not_retained_in_order_record"
    assert r["protection"]["state"] == "unavailable"
    assert "SECRET" not in json.dumps(r) and "PRIVATE" not in json.dumps(r)
    assert execution_receipt(o, [], [])["protection"]["state"] == "not_registered"
    g = {"id": "entry-a", "exch": "BFO", "tsym": "SENSEX-TEST", "state": {"stop_level": 80}}
    r = execution_receipt(o, [], [g])
    assert r["protection"]["state"] == "registered"
    assert r["protection"]["broker_exit_verified"] is False
    g["tsym"] = "OTHER"
    assert execution_receipt(o, [], [g])["protection"]["state"] == "identity_mismatch"


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def sort(self, *_):
        return self
    def skip(self, skip):
        self.rows = self.rows[skip:]
        return self
    async def to_list(self, length):
        return self.rows[:length] if length is not None else self.rows


class ReadOnlyCollection:
    def __init__(self, rows):
        self.rows = rows
    def find(self, q, projection):
        rows = self.rows
        if "norenordno" in q:
            rows = [r for r in rows if r.get("norenordno") in q["norenordno"]["$in"]]
        return Cursor(rows)
    async def count_documents(self, q):
        return len(self.rows)


def test_pagination_uses_only_existing_db_reads():
    db = SimpleNamespace(live_orders=ReadOnlyCollection([order(), order(norenordno=None, mode="mock")]),
                         live_trades=ReadOnlyCollection([journal()]))
    r = asyncio.run(receipt_page(db, limit=1, skip=1, guards=[]))
    assert r["total"] == 2 and len(r["items"]) == 1
    assert r["items"][0]["mode"] == "mock"
    assert r["items"][0]["journal"] is None


def test_route_is_get_and_has_no_broker_or_write_calls():
    source = (Path(__file__).parents[1] / "backend/app/routers/live_broker.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "live_execution_receipts")
    assert ast.unparse(fn.decorator_list[0]).startswith("api.get(")
    calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert calls == {"api.get", "Query", "_get_live_registry", "_get_live_registry().snapshot",
                     "get_db", "receipt_page", "datetime.now", "datetime.now(timezone.utc).isoformat"}
