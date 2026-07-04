"""Zero-brokerage statutory friction profile for honest live-test P&L.

Flattrade charges ZERO brokerage on F&O, but statutory charges are real and
matter on thin option premiums. This module gives a deterministic, per-trade
rupee charge breakdown for one BUY→SELL round trip — useful for honest live-test
P&L (more accurate than the brokerage-inclusive backtest friction model).

Segments:
- NFO: NSE F&O (NIFTY, BANKNIFTY index options)
- BFO: BSE F&O (SENSEX index options)

Turnover for index options = premium × quantity (lot_size × lots).

IMPORTANT — RATES ARE STATUTORY AND CHANGE:
All rate constants below are documented with their statutory basis.
Verify against current NSE/BSE/SEBI circulars before relying on them for
anything other than display/estimation. Structure matters more than exactness
here; the P&L order-of-magnitude is correct even if a rate shifts by a few bp.

Reference sources (as of 2026):
- NSE circular CML/2024/57 (exchange txn charges effective 2024-10-01)
- BSE notice 20240930-1 (exchange txn charges effective 2024-10-01)
- Finance Act 2023 (STT on options sell-side)
- SEBI circular SEBI/HO/MRD/MRD-PoD-1/P/CIR/2022/0044 (SEBI fee)
- Stamp Act 2019 (stamp duty on buy-side)
"""
from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Statutory rate constants
# All rates are FRACTIONS (not percentages) unless noted.
# ---------------------------------------------------------------------------

# STT: Securities Transaction Tax — sell-side ONLY on option PREMIUM.
# Rate: 0.1% of premium turnover on the sell leg.
# Statutory basis: Finance Act 2024 / Budget 2024 (effective 2024-10-01).
# option_costs.DEFAULT_STT_SELL_RATE carries the same 0.1% since 2026-07-04.
STT_OPTIONS_SELL = 0.001  # 0.1% of sell turnover

# Exchange transaction charges — charged on both legs (buy + sell premium).
# NSE (NFO) and BSE (BFO) have different rates effective 2024-10-01.
# FLAG: verify current rates — revised under SEBI's cost-reduction directive.
EXCH_TXN_NFO = 0.0003503  # ~0.03503% of total premium turnover (NSE F&O options)
EXCH_TXN_BFO = 0.000325   # ~0.0325% of total premium turnover (BSE F&O options)

# SEBI turnover fee — both sides.
# Rate: ₹10 per crore = 0.00001% = 0.0000001 fraction.
# FLAG: verify — SEBI revises periodically; small but keep for completeness.
SEBI_FEE = 0.000001  # 0.0001% = ₹10 per ₹1 crore of turnover

# GST: on (brokerage + exchange txn + SEBI fee). Brokerage is 0 for Flattrade.
# Rate: 18% (standard rate, unlikely to change but FLAG anyway).
# FLAG: verify current GST rate on financial services.
GST_RATE = 0.18  # 18% on chargeable statutory fees

# Stamp duty — buy-side ONLY on option premium turnover.
# Rate: 0.003% per the Stamp Act 2019 / Finance Act 2020.
# FLAG: verify — some states/SEBI have varying interpretations; 0.003% is the
# standard rate for derivative (options) instruments.
STAMP_OPTIONS = 0.00003  # 0.003% of buy turnover


def live_charges(
    buy_turnover: float,
    sell_turnover: float,
    *,
    segment: str = "NFO",
) -> Dict[str, float]:
    """Compute statutory rupee charges for one option round-trip (zero brokerage).

    Parameters
    ----------
    buy_turnover:
        Entry premium × quantity (₹). Use 0 for a sell-only leg.
    sell_turnover:
        Exit premium × quantity (₹). Use 0 for a buy-only leg.
    segment:
        "NFO" (NSE — NIFTY/BANKNIFTY) or "BFO" (BSE — SENSEX).
        Controls the exchange transaction charge rate.

    Returns
    -------
    dict with keys:
        brokerage, stt, exchange_txn, sebi_fee, gst, stamp_duty, total
        All values in ₹, rounded to paise (2 decimal places).

    Notes
    -----
    - STT is sell-side only (charged on the closing leg premium × qty).
    - Stamp duty is buy-side only (charged on the opening leg premium × qty).
    - Exchange txn + SEBI fee apply to both legs (total turnover).
    - GST applies to (brokerage + exchange_txn + sebi_fee); brokerage is 0.
    - Brokerage is always ₹0 (Flattrade zero-brokerage profile).
    """
    buy_to = max(0.0, float(buy_turnover))
    sell_to = max(0.0, float(sell_turnover))
    total_turnover = buy_to + sell_to

    seg = (segment or "NFO").upper()
    exch_rate = EXCH_TXN_BFO if seg == "BFO" else EXCH_TXN_NFO

    brokerage = 0.0
    stt = sell_to * STT_OPTIONS_SELL                    # sell-side only
    exchange_txn = total_turnover * exch_rate           # both sides
    sebi_fee = total_turnover * SEBI_FEE                # both sides
    gst = (brokerage + exchange_txn + sebi_fee) * GST_RATE  # on chargeable fees
    stamp_duty = buy_to * STAMP_OPTIONS                 # buy-side only

    total = brokerage + stt + exchange_txn + sebi_fee + gst + stamp_duty

    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_txn": round(exchange_txn, 2),
        "sebi_fee": round(sebi_fee, 2),
        "gst": round(gst, 2),
        "stamp_duty": round(stamp_duty, 2),
        "total": round(total, 2),
    }
