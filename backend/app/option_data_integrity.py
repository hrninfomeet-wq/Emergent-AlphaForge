"""Promotion gate for historical option evidence.

Exploration may continue with clearly-labelled legacy data, but option results
must not be represented as deployment evidence until identity, retrieval, and
decision-time provenance are complete.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List

from app.instruments import canonical_instrument_key, contract_identity_key


def _records(rows: Any) -> List[Dict[str, Any]]:
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        try:
            return list(rows.to_dict(orient="records"))
        except TypeError:
            pass
    return [dict(r) for r in rows]


def assess_option_research_integrity(
    contracts: Iterable[Dict[str, Any]], option_candles: Any
) -> Dict[str, Any]:
    """Return a machine-readable research/promotion verdict for a loaded slice."""
    contract_rows = _records(contracts)
    candle_rows = _records(option_candles)

    metadata_by_token: Dict[str, set] = defaultdict(set)
    for row in contract_rows:
        token = canonical_instrument_key(row.get("instrument_key"))
        identity = contract_identity_key(row.get("instrument_key"), row.get("expiry_date"))
        if token:
            metadata_by_token[token].add(identity)

    candles_by_token: Dict[str, set] = defaultdict(set)
    for row in candle_rows:
        token = canonical_instrument_key(row.get("instrument_key"))
        identity = row.get("contract_key") or contract_identity_key(
            row.get("instrument_key"), row.get("expiry_date"))
        if token:
            candles_by_token[token].add(str(identity))

    metadata_collision_tokens = sum(1 for ids in metadata_by_token.values() if len(ids) > 1)
    mixed_candle_tokens = sum(1 for ids in candles_by_token.values() if len(ids) > 1)
    missing_contract_key = sum(1 for r in candle_rows if not r.get("contract_key"))
    missing_first_ingested = sum(1 for r in candle_rows if not r.get("first_ingested_at"))
    missing_retrieval_run = sum(1 for r in candle_rows if not r.get("retrieval_run_id"))
    missing_bar_end = sum(1 for r in candle_rows if not r.get("bar_end_ts"))
    missing_listing_evidence = sum(
        1 for r in contract_rows if not (r.get("listed_at") or r.get("master_snapshot_at"))
    )

    blockers = []
    if metadata_collision_tokens or mixed_candle_tokens:
        blockers.append({
            "code": "reused_exchange_token",
            "label": "Reusable broker tokens are not unique contract identities",
            "detail": (
                f"{metadata_collision_tokens} loaded metadata token(s) span multiple contracts; "
                f"{mixed_candle_tokens} loaded candle token(s) span multiple identities."
            ),
        })
    if missing_contract_key:
        blockers.append({
            "code": "legacy_contract_identity",
            "label": "Legacy candles have no immutable token-plus-expiry key",
            "detail": f"{missing_contract_key}/{len(candle_rows)} loaded candle rows require rebuild/migration.",
        })
    if missing_first_ingested or missing_retrieval_run:
        blockers.append({
            "code": "missing_retrieval_provenance",
            "label": "Historical rows cannot prove what was known when",
            "detail": (
                f"Missing first-ingest timestamps on {missing_first_ingested} row(s) and "
                f"retrieval run IDs on {missing_retrieval_run} row(s)."
            ),
        })
    if missing_listing_evidence:
        blockers.append({
            "code": "retrospective_instrument_master",
            "label": "Contract availability is retrospective",
            "detail": f"{missing_listing_evidence}/{len(contract_rows)} contract rows lack a dated listing/master snapshot.",
        })
    if missing_bar_end:
        blockers.append({
            "code": "implicit_bar_decision_time",
            "label": "Bar start and decision time are not explicitly separated",
            "detail": f"{missing_bar_end}/{len(candle_rows)} candle rows lack bar_end_ts.",
        })
    blockers.append({
        "code": "no_point_in_time_execution_surface",
        "label": "No historical point-in-time bid/ask/depth/IV surface",
        "detail": "Forward full-feed chain capture is required to calibrate executable spread and liquidity.",
    })

    return {
        "status": "research_only" if blockers else "certified",
        "certified": not blockers,
        "exploration_allowed": True,
        "promotion_allowed": not blockers,
        "blockers": blockers,
        "counts": {
            "contracts_loaded": len(contract_rows),
            "candles_loaded": len(candle_rows),
            "metadata_collision_tokens": metadata_collision_tokens,
            "mixed_candle_tokens": mixed_candle_tokens,
            "legacy_candles_without_contract_key": missing_contract_key,
            "candles_without_first_ingested_at": missing_first_ingested,
            "candles_without_retrieval_run_id": missing_retrieval_run,
        },
    }
