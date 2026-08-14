"""Only a DOCUMENTED success stat is a success.

`_parse_alert_response` decided success with a NEGATIVE allowlist:

    ok = al_id is not None and str(stat).strip().lower() != "not_ok"

so anything carrying an id and a stat that merely is not the literal "Not_Ok"
counted as a placed GTT/OCO — including `stat="Rejected"`, an absent stat, and
`stat=None` (note `str(None).lower()` is "none", not "not_ok"). The function's own
docstring already names the real success values — "Oi created" / "OI created" /
"Oi delete success" — and the decoded spec pins them too
(docs/Resources/flattrade-pi-api/endpoints/21-place-oco-order.md: "stat | OI created / Not_Ok").

This is the same failure shape that produced a phantom backstop on 2026-08-14: a
response that is not success being read as success, so the journal records
`oco_al_id` and the operator is told a protective order exists. That day the OCO
genuinely WAS accepted and died later (fixed separately in ac1c03a); this is the
second, latent route to the same outcome — and it needs no broker misbehaviour,
only a stat string nobody anticipated.

A negative allowlist fails OPEN on the unknown. For a protective order the unknown
must fail CLOSED: report no backstop and let the software guard be the stated
protection.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.flattrade_client import _parse_alert_response as parse  # noqa: E402


# --- documented successes still succeed ------------------------------------

def test_place_success_values_are_accepted():
    for stat in ("Oi created", "OI created", "oi created"):
        assert parse({"stat": stat, "al_id": "AL1"})["ok"] is True, stat


def test_cancel_success_value_is_accepted():
    assert parse({"stat": "Oi delete success", "al_id": "AL1"})["ok"] is True


def test_a_success_in_a_single_element_list_is_accepted():
    """Documented quirk: success may be a LIST or a DICT."""
    assert parse([{"stat": "Oi created", "al_id": "AL1"}])["ok"] is True


def test_the_alternate_al_id_casings_still_work():
    for key in ("al_id", "Al_id", "AL_id"):
        assert parse({"stat": "Oi created", key: "AL1"})["ok"] is True, key


# --- everything else must fail CLOSED --------------------------------------

def test_a_rejected_stat_is_not_success():
    r = parse({"stat": "Rejected", "al_id": "AL1"})
    assert r["ok"] is False, (
        "a REJECTED alert carrying an id was reported as a placed backstop")


def test_an_absent_stat_is_not_success():
    assert parse({"al_id": "AL1"})["ok"] is False


def test_a_none_stat_is_not_success():
    """`str(None).lower()` is 'none', which slipped past the != 'not_ok' test."""
    assert parse({"stat": None, "al_id": "AL1"})["ok"] is False


def test_an_unknown_future_stat_is_not_success():
    """The whole point: fail CLOSED on what nobody anticipated."""
    assert parse({"stat": "Pending Risk Review", "al_id": "AL1"})["ok"] is False


def test_not_ok_remains_a_failure():
    assert parse({"stat": "Not_Ok", "al_id": "AL1"})["ok"] is False


def test_a_success_stat_without_an_id_is_not_success():
    """An id is the handle needed to CANCEL it later; no id, no usable backstop."""
    assert parse({"stat": "Oi created"})["ok"] is False


# --- diagnostics must survive so the operator can see WHY ------------------

def test_the_stat_and_emsg_are_preserved_on_failure():
    r = parse({"stat": "Rejected", "al_id": "AL1", "emsg": "margin shortfall"})
    assert r["stat"] == "Rejected"
    assert r["emsg"] == "margin shortfall"
    assert r["al_id"] == "AL1", "keep the id for diagnostics even when not ok"
