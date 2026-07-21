"""Shared instrument metadata for supported index underlyings."""

from datetime import date, datetime

INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX",
}

# Auxiliary (non-tradable-as-options) instruments we ingest for CONTEXT only,
# e.g. India VIX for the volatility regime. Kept separate from INSTRUMENT_KEYS
# so option-contract/expiry code never treats them as an option underlying.
AUX_INSTRUMENT_KEYS = {
    "INDIAVIX": "NSE_INDEX|India VIX",
}

UNDERLYING_META = {
    "NIFTY": {"strike_step": 50, "lot_size": 65},
    "BANKNIFTY": {"strike_step": 100, "lot_size": 35},
    "SENSEX": {"strike_step": 100, "lot_size": 20},
}


def canonical_instrument_key(instrument_key) -> str:
    """Canonical broker key for an option instrument: the plain 2-part
    `SEGMENT|TOKEN` form.

    The expired-contract backfill stores dated 3-part keys
    (`NSE_FO|72171|26-05-2026`) while the current-contract sync stores the
    plain key for the SAME contract — fragmenting candle storage and breaking
    exact-key lookups (root cause #3, found 2026-06-12: 702 duplicated NIFTY
    contract identities). All candle persistence and candle lookups normalize
    through this helper; the dated form remains only inside the
    expired-endpoint URL builder (`upstox_client._expired_endpoint_key`).
    """
    key = str(instrument_key or "")
    parts = key.split("|")
    if len(parts) >= 3:
        tail = parts[-1]
        if len(tail) == 10:
            for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
                try:
                    datetime.strptime(tail, fmt)
                    return "|".join(parts[:-1])
                except ValueError:
                    continue
    return key


def contract_identity_key(instrument_key, expiry_date=None) -> str:
    """Immutable option-contract identity used by research storage/lookups.

    Exchange tokens are reusable across expiries, so ``SEGMENT|TOKEN`` is a
    streaming-address key, not a historical contract identity.  Research must
    pair it with the dated expiry.  Dated broker keys use ``DD-MM-YYYY`` while
    AlphaForge metadata uses ``YYYY-MM-DD``; both normalize to the latter.

    If no expiry is available we deliberately fall back to the canonical token
    for backward-compatible non-option fixtures.  Such rows remain ineligible
    for promotion under the option-data integrity gate.
    """
    raw = str(instrument_key or "")
    parts = raw.split("|")
    expiry = expiry_date
    if not expiry and len(parts) >= 3:
        tail = parts[-1]
        if len(tail) == 10 and tail[2] == "-" and tail[5] == "-":
            expiry = tail

    normalized = ""
    if isinstance(expiry, (datetime, date)):
        normalized = expiry.date().isoformat() if isinstance(expiry, datetime) else expiry.isoformat()
    elif expiry:
        text = str(expiry).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                normalized = datetime.strptime(text[:10], fmt).date().isoformat()
                break
            except ValueError:
                continue
    base = canonical_instrument_key(raw)
    return f"{base}|{normalized}" if normalized else base
