"""Shared instrument metadata for supported index underlyings."""

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
        if len(tail) == 10 and tail[2] == "-" and tail[5] == "-":
            return "|".join(parts[:-1])
    return key
