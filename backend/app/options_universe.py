"""Option universe helpers for historical and live signal selection."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from app.instruments import UNDERLYING_META


OptionContract = Dict[str, Any]


def strike_step_for(underlying: str) -> int:
    """Return the configured strike interval for a supported index."""
    key = str(underlying or "").upper()
    if key not in UNDERLYING_META:
        raise ValueError(f"Unsupported underlying: {underlying}")
    return int(UNDERLYING_META[key]["strike_step"])


def round_to_step(value: float, step: int) -> int:
    """Round a spot value to the nearest tradable strike step."""
    strike_step = int(step)
    if strike_step <= 0:
        raise ValueError("step must be greater than zero")
    return int(round(float(value) / strike_step) * strike_step)


def strike_offset_for_moneyness(moneyness: str = "otm1", direction: str = "CE") -> int:
    """Map ATM/OTM/ITM labels to strike offsets from ATM.

    CE OTM strikes sit above ATM, PE OTM strikes sit below ATM. ITM is the
    inverse. This matches the reference project's strategy selection semantics.
    """
    side = str(direction or "CE").upper()
    if side not in ("CE", "PE"):
        raise ValueError("direction must be CE or PE")

    label = str(moneyness or "otm1").lower()
    if label == "atm":
        return 0

    match = re.match(r"^(otm|itm)(\d+)$", label)
    if not match:
        return 1 if side == "CE" else -1

    distance = int(match.group(2))
    is_otm = match.group(1) == "otm"
    if side == "CE":
        return distance if is_otm else -distance
    return -distance if is_otm else distance


def _normalized_contract(contract: OptionContract) -> Optional[OptionContract]:
    try:
        side = str(contract.get("side") or contract.get("instrument_type") or "").upper()
        strike = float(contract.get("strike"))
        instrument_key = contract.get("instrument_key")
    except (TypeError, ValueError):
        return None

    if side not in ("CE", "PE") or not instrument_key:
        return None
    return {**contract, "side": side, "strike": strike}


def _rank_for_contract(contract: OptionContract, atm: int, underlying: str) -> int:
    offset = round((float(contract["strike"]) - atm) / strike_step_for(underlying))
    if contract["side"] == "CE" and offset > 0:
        return int(offset)
    if contract["side"] == "PE" and offset < 0:
        return int(abs(offset))
    return 0


def select_contract_for_signal(
    *,
    contracts: Iterable[OptionContract],
    underlying: str,
    spot_price: float,
    direction: str,
    moneyness: str = "otm1",
) -> Optional[OptionContract]:
    """Select the exact option contract for a spot-driven signal."""
    key = str(underlying or "").upper()
    side = str(direction or "").upper()
    step = strike_step_for(key)
    atm = round_to_step(spot_price, step)
    target_strike = atm + strike_offset_for_moneyness(moneyness, side) * step

    for raw_contract in contracts:
        contract = _normalized_contract(raw_contract)
        if not contract:
            continue
        if contract["side"] == side and int(contract["strike"]) == int(target_strike):
            return {**contract, "atm": atm, "moneyness": moneyness}
    return None


def select_atm_band(
    *,
    contracts: Iterable[OptionContract],
    underlying: str,
    spot_price: float,
    radius: int = 5,
) -> List[OptionContract]:
    """Return CE/PE contracts inside an ATM-centered strike band."""
    key = str(underlying or "").upper()
    step = strike_step_for(key)
    atm = round_to_step(spot_price, step)
    bounded_radius = max(0, min(int(radius or 0), 20))
    low = atm - bounded_radius * step
    high = atm + bounded_radius * step

    selected: List[OptionContract] = []
    for raw_contract in contracts:
        contract = _normalized_contract(raw_contract)
        if not contract:
            continue
        if not (low <= contract["strike"] <= high):
            continue
        rank = _rank_for_contract(contract, atm, key)
        moneyness = "ATM" if int(contract["strike"]) == int(atm) else (f"OTM{rank}" if rank > 0 else "ITM_OR_ATM_BAND")
        selected.append({**contract, "atm": atm, "rank": rank, "moneyness": moneyness})

    return sorted(selected, key=lambda contract: (abs(contract["strike"] - atm), contract["strike"], contract["side"]))
