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
