"""Option candle normalization and persistence helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def candles_to_df(
    candles: Iterable[list],
    *,
    instrument_key: str,
    contract: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Normalize Upstox historical candle rows into the options_1m shape."""
    meta = contract or {}
    rows = []
    for candle in candles or []:
        if len(candle) < 6:
            continue
        try:
            dt = pd.to_datetime(candle[0], utc=True)
        except Exception:
            continue
        rows.append({
            "instrument_key": instrument_key,
            "underlying": meta.get("underlying", ""),
            "expiry_date": meta.get("expiry_date", ""),
            "strike": _float_or_zero(meta.get("strike")),
            "side": str(meta.get("side", "")).upper(),
            "trading_symbol": meta.get("trading_symbol", ""),
            "ts": int(dt.value // 10**6),
            "datetime": str(candle[0]),
            "open": _float_or_zero(candle[1]),
            "high": _float_or_zero(candle[2]),
            "low": _float_or_zero(candle[3]),
            "close": _float_or_zero(candle[4]),
            "volume": _float_or_zero(candle[5]),
            "oi": _float_or_zero(candle[6] if len(candle) > 6 else 0),
            "source": "upstox",
        })

    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["instrument_key", "ts"])
        .sort_values("ts")
        .reset_index(drop=True)
    )


async def persist_option_candles_df(db: Any, df: pd.DataFrame) -> Dict[str, int]:
    """Upsert option candles keyed by instrument_key and timestamp."""
    if df is None or df.empty:
        return {"candles_added": 0, "candles_updated": 0}

    inserted = 0
    updated = 0
    for row in df.to_dict(orient="records"):
        doc = {
            "instrument_key": row["instrument_key"],
            "underlying": row.get("underlying", ""),
            "expiry_date": row.get("expiry_date", ""),
            "strike": _float_or_zero(row.get("strike")),
            "side": str(row.get("side", "")).upper(),
            "trading_symbol": row.get("trading_symbol", ""),
            "ts": int(row["ts"]),
            "datetime": str(row.get("datetime", "")),
            "open": _float_or_zero(row.get("open")),
            "high": _float_or_zero(row.get("high")),
            "low": _float_or_zero(row.get("low")),
            "close": _float_or_zero(row.get("close")),
            "volume": _float_or_zero(row.get("volume")),
            "oi": _float_or_zero(row.get("oi")),
            "source": row.get("source", "upstox"),
        }
        result = await db.options_1m.update_one(
            {"instrument_key": doc["instrument_key"], "ts": doc["ts"]},
            {"$set": doc},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    return {"candles_added": inserted, "candles_updated": updated}
