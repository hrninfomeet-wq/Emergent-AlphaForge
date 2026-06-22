"""Shared network helpers for Flattrade API calls.

Flattrade whitelists a static IPv4 address (e.g. 103.76.102.148).  On a
dual-stack host the OS may prefer IPv6 for outbound connections, which causes
Flattrade to reject the request with "Invalid Input : INVALID_IP".

Binding the outbound socket to 0.0.0.0 (the IPv4 any-address) forces the OS
to route via IPv4 regardless of the system preference.

Set FLATTRADE_FORCE_IPV4=0 to disable (e.g. in a test environment where the
outbound address is already IPv4-only and you want default httpx behaviour).
"""
from __future__ import annotations

import os
from typing import Optional

import httpx


def force_ipv4() -> bool:
    """Return True (default) unless FLATTRADE_FORCE_IPV4 is explicitly '0'."""
    return os.environ.get("FLATTRADE_FORCE_IPV4", "1") != "0"


def ipv4_transport() -> Optional[httpx.AsyncHTTPTransport]:
    """Return an httpx transport that binds to IPv4, or None when forcing is off.

    Passing the returned value as ``transport=ipv4_transport()`` to
    ``httpx.AsyncClient`` is safe when the value is None — httpx treats
    ``transport=None`` as "use the default transport".
    """
    # Bind the outbound socket to the IPv4 any-address so the request egresses
    # over IPv4.  Flattrade whitelists a static IPv4; a dual-stack host
    # otherwise egresses IPv6 → 'Invalid Input : INVALID_IP'.
    return httpx.AsyncHTTPTransport(local_address="0.0.0.0") if force_ipv4() else None
