"""Option candle normalization and persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from app.instruments import canonical_instrument_key, contract_identity_key


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
        ts = int(dt.value // 10**6)
        rows.append({
            "instrument_key": canonical_instrument_key(instrument_key),
            "contract_key": contract_identity_key(instrument_key, meta.get("expiry_date")),
            "underlying": meta.get("underlying", ""),
            "expiry_date": meta.get("expiry_date", ""),
            "strike": _float_or_zero(meta.get("strike")),
            "side": str(meta.get("side", "")).upper(),
            "trading_symbol": meta.get("trading_symbol", ""),
            "ts": ts,
            "bar_end_ts": ts + 60_000,
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
        .drop_duplicates(subset=["contract_key", "ts"])
        .sort_values("ts")
        .reset_index(drop=True)
    )


async def persist_option_candles_df(
    db: Any,
    df: pd.DataFrame,
    *,
    retrieval_run_id: Optional[str] = None,
) -> Dict[str, int]:
    """Upsert candles by immutable contract identity and timestamp.

    The legacy token key remains for live lookup compatibility.  ``contract_key``
    includes expiry so a reused exchange token cannot merge two contracts.
    Retrieval timestamps make future rows auditable; legacy rows require the
    controlled rebuild documented in ``docs/option-data-provenance.md``.
    """
    if df is None or df.empty:
        return {"candles_added": 0, "candles_updated": 0}

    inserted = 0
    updated = 0
    refreshed_at = datetime.now(timezone.utc)
    for row in df.to_dict(orient="records"):
        expiry = row.get("expiry_date", "")
        doc = {
            "instrument_key": canonical_instrument_key(row["instrument_key"]),
            "contract_key": row.get("contract_key") or contract_identity_key(
                row["instrument_key"], expiry),
            "underlying": row.get("underlying", ""),
            "expiry_date": row.get("expiry_date", ""),
            "strike": _float_or_zero(row.get("strike")),
            "side": str(row.get("side", "")).upper(),
            "trading_symbol": row.get("trading_symbol", ""),
            "ts": int(row["ts"]),
            "bar_end_ts": int(row.get("bar_end_ts") or int(row["ts"]) + 60_000),
            "datetime": str(row.get("datetime", "")),
            "open": _float_or_zero(row.get("open")),
            "high": _float_or_zero(row.get("high")),
            "low": _float_or_zero(row.get("low")),
            "close": _float_or_zero(row.get("close")),
            "volume": _float_or_zero(row.get("volume")),
            "oi": _float_or_zero(row.get("oi")),
            "source": row.get("source", "upstox"),
            "source_endpoint": row.get("source_endpoint", "historical-candle-v3"),
            "last_retrieved_at": refreshed_at,
            "retrieval_run_id": retrieval_run_id or row.get("retrieval_run_id"),
        }
        result = await db.options_1m.update_one(
            {"instrument_key": doc["instrument_key"],
             "expiry_date": doc["expiry_date"], "ts": doc["ts"]},
            {"$set": doc, "$setOnInsert": {"first_ingested_at": refreshed_at}},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1

    return {"candles_added": inserted, "candles_updated": updated}
