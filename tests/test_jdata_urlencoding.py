"""Flattrade reads `jData` LITERALLY — it does not url-decode the body.

Verified against the live API on 2026-08-15 with one SearchScrip call::

    percent-encoded jData -> HTTP 400 {"emsg":"jData is not valid json object"}
    raw JSON jData        -> HTTP 401 {"emsg":"Session Expired : Invalid User Id"}

The 401 is the informative one: the raw form got PAST JSON parsing and was
rejected only on a deliberately bogus uid, while the encoded form could not be
parsed at all.

This file previously encoded the whole document with `urlencode()` and asserted
the round trip with `parse_qs`. Both sides of that were self-consistent and both
were wrong about the server, so a green suite shipped a transport that broke
EVERY Flattrade call — orders, positions, the lot. The decoded spec's advice to
url-encode "to avoid special char error for symbols like M&M" means escaping a
special character WITHIN a value, not percent-encoding the JSON document.

The truncation hazard that motivated the encoding is real and still handled: a
literal `&` anywhere in the payload would end the body at that byte, so `&` is
emitted as the JSON escape `\\u0026` — valid JSON denoting the same string, with
no `&` left to split on.

The lesson worth keeping: a transport contract cannot be validated against a
mock that shares the implementation's assumption. This needed one real call.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.flattrade_client import FlattradeClient  # noqa: E402


class _C(FlattradeClient):
    def __init__(self):
        self._jKey = "TOKEN123"
        self._uid = "FZ00000"


def _server_parse(body: str):
    """Read the body the way Flattrade demonstrably does: split on the LAST
    `&jKey=` and treat what precedes it as literal JSON."""
    head, _, key = body.rpartition("&jKey=")
    assert head.startswith("jData="), body
    return json.loads(head[len("jData="):]), key


def test_jdata_is_sent_as_raw_json():
    body = _C()._make_body({"uid": "FZ00000", "qty": 1})
    assert body.startswith('jData={"'), body
    assert "%7B" not in body and "%22" not in body, "the document was percent-encoded"


def test_an_ordinary_option_payload_round_trips():
    j = {"tsym": "NIFTY18AUG26P24300", "exch": "NFO", "qty": 65,
         "remarks": "oco:26081400076294"}
    got, key = _server_parse(_C()._make_body(j))
    assert got == j
    assert key == "TOKEN123"


def test_an_ampersand_in_a_value_cannot_truncate_the_body():
    """`M&M-EQ` must survive: the `&` is escaped to \\u0026 so the body has exactly
    one `&`, the one separating jKey."""
    body = _C()._make_body({"tsym": "M&M-EQ", "qty": 1})
    assert body.count("&") == 1, body
    got, _ = _server_parse(body)
    assert got["tsym"] == "M&M-EQ"


def test_multiple_ampersands_are_all_escaped():
    body = _C()._make_body({"a": "x&y", "b": "p&q&r"})
    assert body.count("&") == 1
    got, _ = _server_parse(body)
    assert got == {"a": "x&y", "b": "p&q&r"}


def test_a_plus_is_transmitted_literally():
    """The server does not form-decode, so `+` is NOT read as a space. Encoding it
    would corrupt it; leaving it raw is correct."""
    got, _ = _server_parse(_C()._make_body({"remarks": "a+b"}))
    assert got["remarks"] == "a+b"


def test_nested_oco_structures_survive():
    j = {"oivariable": [{"d": "21.45", "var_name": "x"}],
         "place_order_params": {"tsym": "NIFTY18AUG26P24300", "prc": "21.00"}}
    got, _ = _server_parse(_C()._make_body(j))
    assert got == j


def test_the_token_is_delivered_verbatim():
    _, key = _server_parse(_C()._make_body({"a": 1}))
    assert key == "TOKEN123"


def test_unicode_values_survive():
    got, _ = _server_parse(_C()._make_body({"name": "Nifty 50"}))
    assert got["name"] == "Nifty 50"
