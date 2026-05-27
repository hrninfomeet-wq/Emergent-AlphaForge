"""Shared instrument metadata for supported index underlyings."""

INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX",
}

UNDERLYING_META = {
    "NIFTY": {"strike_step": 50, "lot_size": 65},
    "BANKNIFTY": {"strike_step": 100, "lot_size": 35},
    "SENSEX": {"strike_step": 100, "lot_size": 20},
}
