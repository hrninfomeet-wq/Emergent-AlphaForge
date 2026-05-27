"""Preview-first planner for option candle warehouse downloads."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from app.options_universe import round_to_step, strike_offset_for_moneyness, strike_step_for


DEFAULT_MONEYNESS = ["atm", "itm1", "itm2", "otm1", "otm2", "otm3"]
DEFAULT_LEGS = ["CE", "PE"]


def _normalize_moneyness(values: Optional[Sequence[str]]) -> List[str]:
    cleaned: List[str] = []
    for value in values or DEFAULT_MONEYNESS:
        label = str(value or "").strip().lower()
        if not label or label in cleaned:
            continue
        cleaned.append(label)
    return cleaned or ["atm"]


def _normalize_legs(values: Optional[Sequence[str]]) -> List[str]:
    cleaned: List[str] = []
    for value in values or DEFAULT_LEGS:
        label = str(value or "").strip().upper()
        if label not in ("CE", "PE") or label in cleaned:
            continue
        cleaned.append(label)
    return cleaned or ["CE", "PE"]


def _ts_to_ist_date(ts_ms: int) -> str:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").date().isoformat()


def _ts_to_ist_datetime(ts_ms: int) -> str:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata").strftime("%Y-%m-%d %H:%M")


def _sample_spot_candles(spot_candles: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    if spot_candles is None or spot_candles.empty:
        return pd.DataFrame()

    interval_ms = max(1, int(interval_minutes or 1)) * 60 * 1000
    normalized = spot_candles.copy()
    normalized["ts"] = normalized["ts"].astype(int)
    normalized = normalized.sort_values("ts").reset_index(drop=True)

    rows: List[Dict[str, Any]] = []
    last_ts: Optional[int] = None
    for row in normalized.to_dict(orient="records"):
        ts = int(row["ts"])
        if last_ts is None or ts - last_ts >= interval_ms:
            rows.append(row)
            last_ts = ts

    return pd.DataFrame(rows)


def _resolve_expiry_for_date(date_str: str, expiries: Sequence[str], fixed_expiry_date: Optional[str]) -> Optional[str]:
    if fixed_expiry_date:
        return str(fixed_expiry_date)
    for expiry in expiries:
        if expiry >= date_str:
            return expiry
    return None


def _contracts_by_expiry_side_strike(contracts: Iterable[Dict[str, Any]]) -> Dict[str, Dict[tuple[str, int], Dict[str, Any]]]:
    lookup: Dict[str, Dict[tuple[str, int], Dict[str, Any]]] = {}
    for raw_contract in contracts or []:
        expiry = str(raw_contract.get("expiry_date") or "")
        side = str(raw_contract.get("side") or raw_contract.get("instrument_type") or "").upper()
        instrument_key = raw_contract.get("instrument_key")
        if not expiry or side not in ("CE", "PE") or not instrument_key:
            continue
        try:
            strike = int(float(raw_contract.get("strike")))
        except (TypeError, ValueError):
            continue
        lookup.setdefault(expiry, {})[(side, strike)] = {
            **raw_contract,
            "side": side,
            "strike": float(raw_contract.get("strike") or strike),
        }
    return lookup


def build_option_warehouse_plan(
    *,
    spot_candles: pd.DataFrame,
    contracts: Iterable[Dict[str, Any]],
    underlying: str,
    moneyness: Optional[Sequence[str]] = None,
    legs: Optional[Sequence[str]] = None,
    sample_interval_minutes: int = 15,
    fixed_expiry_date: Optional[str] = None,
    existing_counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Select the option contracts needed to cover a spot history window.

    The planner samples stored spot/index candles, resolves the next available
    expiry from contract metadata for each sampled timestamp, then selects the
    requested CE/PE and moneyness contracts. Results are deduplicated by
    instrument key so large downloads can be previewed before any broker calls.
    """
    underlying_key = str(underlying or "").upper()
    labels = _normalize_moneyness(moneyness)
    sides = _normalize_legs(legs)
    contract_list = [
        {**contract, "side": str(contract.get("side", "")).upper()}
        for contract in contracts or []
        if str(contract.get("underlying", underlying_key)).upper() == underlying_key
    ]
    expiries = sorted({str(contract.get("expiry_date")) for contract in contract_list if contract.get("expiry_date")})
    contract_lookup = _contracts_by_expiry_side_strike(contract_list)
    sampled = _sample_spot_candles(spot_candles, sample_interval_minutes)
    step = strike_step_for(underlying_key)
    counts = existing_counts or {}

    items_by_key: Dict[str, Dict[str, Any]] = {}
    missing: List[Dict[str, Any]] = []
    selection_count = 0

    for spot in sampled.to_dict(orient="records") if not sampled.empty else []:
        ts = int(spot["ts"])
        spot_date = _ts_to_ist_date(ts)
        expiry = _resolve_expiry_for_date(spot_date, expiries, fixed_expiry_date)
        spot_price = float(spot.get("close") or spot.get("price") or 0)
        atm = round_to_step(spot_price, step)
        expiry_contracts = contract_lookup.get(str(expiry or ""), {})

        for label in labels:
            for side in sides:
                selection_count += 1
                target_strike = int(atm + strike_offset_for_moneyness(label, side) * step)
                selected_raw = expiry_contracts.get((side, target_strike))
                selected = {**selected_raw, "atm": atm, "moneyness": label} if selected_raw else None
                if not selected:
                    missing.append({
                        "spot_ts": ts,
                        "spot_datetime": _ts_to_ist_datetime(ts),
                        "spot_price": round(spot_price, 3),
                        "atm": atm,
                        "underlying": underlying_key,
                        "expiry_date": expiry,
                        "moneyness": label,
                        "side": side,
                    })
                    continue

                instrument_key = selected["instrument_key"]
                selected_as = f"{label} {side}"
                existing = int(counts.get(instrument_key, 0) or 0)
                if instrument_key not in items_by_key:
                    items_by_key[instrument_key] = {
                        "instrument_key": instrument_key,
                        "underlying": underlying_key,
                        "expiry_date": selected.get("expiry_date", expiry),
                        "side": side,
                        "strike": float(selected.get("strike")),
                        "trading_symbol": selected.get("trading_symbol", ""),
                        "lot_size": int(selected.get("lot_size") or 0),
                        "selected_as": selected_as,
                        "moneyness": label,
                        "spot_ts": ts,
                        "spot_datetime": _ts_to_ist_datetime(ts),
                        "spot_price": round(spot_price, 3),
                        "atm": selected.get("atm", atm),
                        "selected_dates": [spot_date],
                        "selection_count": 1,
                        "stored_candles": existing,
                        "needs_fetch": existing <= 0,
                    }
                else:
                    item = items_by_key[instrument_key]
                    item["selection_count"] += 1
                    selected_dates = item.setdefault("selected_dates", [])
                    if spot_date not in selected_dates:
                        selected_dates.append(spot_date)
                    aliases = set(str(item.get("selected_as", "")).split(", "))
                    aliases.add(selected_as)
                    item["selected_as"] = ", ".join(sorted(aliases))

    for item in items_by_key.values():
        item["selected_dates"] = sorted(item.get("selected_dates", []))

    items = sorted(
        items_by_key.values(),
        key=lambda item: (
            str(item.get("expiry_date", "")),
            float(item.get("strike") or 0),
            str(item.get("side", "")),
            str(item.get("instrument_key", "")),
        ),
    )
    expiries_used = sorted({str(item.get("expiry_date")) for item in items if item.get("expiry_date")})

    return {
        "summary": {
            "underlying": underlying_key,
            "spot_candles_seen": int(0 if spot_candles is None else len(spot_candles)),
            "spot_candles_used": int(0 if sampled is None else len(sampled)),
            "sample_interval_minutes": max(1, int(sample_interval_minutes or 1)),
            "moneyness": labels,
            "legs": sides,
            "selection_count": int(selection_count),
            "planned_contracts": len(items),
            "stored_contracts": sum(1 for item in items if not item.get("needs_fetch")),
            "missing_data_contracts": sum(1 for item in items if item.get("needs_fetch")),
            "missing_contract_count": len(missing),
            "expiries_used": expiries_used,
            "expiry_mode": "fixed" if fixed_expiry_date else "next_available",
        },
        "items": items,
        "missing": missing,
    }
