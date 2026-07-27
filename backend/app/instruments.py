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

#: Static metadata. ``strike_step`` is authoritative here.
#:
#: ``lot_size`` is a LAST-RESORT FALLBACK ONLY — exchanges revise lot sizes and
#: this map goes stale. Resolve lots from contract data via
#: :func:`resolve_lot_size`; do not read ``lot_size`` from here directly.
#: (Measured 2026-07-27: every stored BANKNIFTY expiry from 2026-01 through
#: 2027-06 carries lot 30, while this map still said 35 — a 16.7% error in every
#: quantity and rupee figure produced by the paths that trusted it.)
UNDERLYING_META = {
    "NIFTY": {"strike_step": 50, "lot_size": 65},
    "BANKNIFTY": {"strike_step": 100, "lot_size": 35},
    "SENSEX": {"strike_step": 100, "lot_size": 20},
}


def resolve_lot_size(contracts, instrument):
    """Resolve the option lot size from CONTRACT DATA, with a noisy fallback.

    The project's stated design is that expiries and lots come from
    ``option_contracts``/the broker and are never hardcoded, precisely because
    exchanges revise them. ``option_backtest`` already honours this (it uses the
    selected contract's own ``lot_size``); this helper lets the other paths do
    the same instead of reading the static :data:`UNDERLYING_META` map.

    Args:
        contracts:  option-contract dicts for the run (each may carry ``lot_size``
                    and ``expiry_date``).
        instrument: underlying name, used only for the fallback.

    Returns:
        ``(lot_size, warnings)``. *warnings* is a list of short codes; it is
        EMPTY on the clean path. Callers should surface any warnings rather than
        drop them — each one means the rupee figures are only approximately right.

    A lot change inside the window is reported, not hidden: BANKNIFTY really did
    carry 35 for the Jul–Dec 2025 expiries and 30 afterwards, so a run spanning
    that boundary cannot be sized by a single number. We take the most recent
    expiry's lot (matching the live convention) and say so.
    """
    warnings = []

    by_expiry = {}
    for c in contracts or []:
        raw = (c or {}).get("lot_size")
        try:
            lot = int(raw)
        except (TypeError, ValueError):
            continue
        if lot <= 0:
            continue
        by_expiry[str((c or {}).get("expiry_date") or "")] = lot

    if not by_expiry:
        meta = UNDERLYING_META.get(str(instrument).upper(), {})
        fallback = int(meta.get("lot_size") or 0)
        if fallback > 0:
            warnings.append("lot_size_fallback_hardcoded")
            return fallback, warnings
        warnings.append("lot_size_unknown_instrument")
        return 1, warnings

    distinct = set(by_expiry.values())
    # Most recent expiry wins — ISO dates sort lexicographically.
    latest = max(by_expiry.items(), key=lambda kv: kv[0])[1]
    if len(distinct) > 1:
        warnings.append(
            "lot_size_changed_in_window:" + ",".join(str(x) for x in sorted(distinct)))
    return latest, warnings


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
