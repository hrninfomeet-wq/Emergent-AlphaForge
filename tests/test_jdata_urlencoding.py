"""A literal '&' in jData truncates the request body.

`_make_body` built a form-urlencoded body by string interpolation:

    f"jData={json.dumps(jdata)}&jKey={self._jKey}"

posted as application/x-www-form-urlencoded. The decoded spec warns twice to
url-encode — endpoints/04-place-order.md and endpoints/21-place-oco-order.md both
say "use url encoding to avoid special char error for symbols like M&M".

Latent rather than today's bug: NIFTY/SENSEX option tsyms and an `oco:<no>` remark
contain no '&' or '+', so the 2026-08-14 payload transmitted intact. It bites the
first time an '&'-bearing symbol, an operator-supplied remark or a strategy name
reaches any jData field — the broker then receives a silently TRUNCATED JSON
object, and the likeliest response is a generic "Invalid Input" that names nothing.
A '+' is worse: it decodes to a space and corrupts silently rather than failing.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.flattrade_client import FlattradeClient  # noqa: E402


class _C(FlattradeClient):
    def __init__(self):
        self._jKey = "TOKEN123"
        self._uid = "FZ00000"


def _roundtrip(jdata):
    body = _C()._make_body(jdata)
    parsed = parse_qs(body, keep_blank_values=True)
    return json.loads(parsed["jData"][0]), parsed["jKey"][0]


def test_an_ampersand_symbol_survives():
    got, key = _roundtrip({"tsym": "M&M-EQ", "qty": 1})
    assert got["tsym"] == "M&M-EQ", "the body was truncated at the literal '&'"
    assert key == "TOKEN123"


def test_a_plus_in_a_remark_is_not_decoded_as_a_space():
    got, _ = _roundtrip({"remarks": "a+b"})
    assert got["remarks"] == "a+b"


def test_an_ordinary_option_payload_is_unchanged():
    """No regression for every payload sent to date."""
    j = {"tsym": "NIFTY18AUG26P24300", "exch": "NFO", "qty": 65,
         "remarks": "oco:26081400076294"}
    got, _ = _roundtrip(j)
    assert got == j


def test_the_token_is_still_delivered_verbatim():
    _, key = _roundtrip({"a": 1})
    assert key == "TOKEN123"


def test_nested_structures_survive():
    """OCO jdata is nested (oivariable + two leg dicts)."""
    j = {"oivariable": [{"d": "21.45", "var_name": "x"}],
         "place_order_params": {"tsym": "M&M", "prc": "21.00"}}
    got, _ = _roundtrip(j)
    assert got["place_order_params"]["tsym"] == "M&M"
