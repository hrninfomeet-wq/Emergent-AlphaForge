"""Regression contracts for the Stage-1 operator-guidance checkpoint.

These intentionally assert rendered-source contracts because the project does
not use a JavaScript component-test runner. They protect the operator-visible
copy and do not make any broker or live-state call.
"""
from __future__ import annotations

import os
import sys

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_execution_navigation_uses_operator_terms_not_internal_phase_codes():
    layout = _read("frontend", "src", "components", "Layout.jsx")

    assert 'to: "/live", label: "Deploy Strategies"' in layout
    assert 'to: "/live-trading", label: "Live Broker"' in layout
    assert 'testid: "nav-live"' in layout
    assert 'testid: "nav-live-trading"' in layout
    assert 'badge: "P4"' not in layout
    assert 'badge: "L0"' not in layout
    assert "P4a prep" not in layout


def test_live_banner_accepts_either_configured_static_ip_and_explains_absence():
    banner = _read("frontend", "src", "components", "live", "LiveBanner.jsx")

    assert "Boolean(status?.static_ip_primary || status?.static_ip_secondary)" in banner
    assert 'data-testid="live-static-ip-guidance"' in banner
    assert "FLATTRADE_PRIMARY_IP" in banner
    assert "FLATTRADE_SECONDARY_IP" in banner
    assert "registered with Flattrade" in banner
    assert "LIVE_AUTOPLACE_ARMED=0" in banner


def test_fernet_unset_has_a_safe_boot_warning(monkeypatch):
    from app import encryption

    monkeypatch.delenv("FERNET_KEY", raising=False)
    assert encryption.fernet_startup_warning() == (
        "FERNET_KEY is not configured; stored Upstox OAuth tokens written by this "
        "process cannot be decrypted after restart. Set FERNET_KEY in backend/.env."
    )


def test_fernet_configured_has_no_boot_warning(monkeypatch):
    from cryptography.fernet import Fernet
    from app import encryption

    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode("utf-8"))
    assert encryption.fernet_startup_warning() is None


def test_fernet_malformed_warning_does_not_disclose_the_key(monkeypatch):
    from app import encryption

    malformed = "not-a-valid-secret-key"
    monkeypatch.setenv("FERNET_KEY", malformed)
    warning = encryption.fernet_startup_warning()

    assert warning is not None
    assert "FERNET_KEY is invalid" in warning
    assert malformed not in warning


def test_server_startup_calls_the_fernet_warning_helper():
    server = _read("backend", "server.py")

    assert "fernet_startup_warning" in server
    assert "log.warning(fernet_warning)" in server
