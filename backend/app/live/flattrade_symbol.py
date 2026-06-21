"""Upstox -> Noren symbol resolver (Tier-0, fail-closed).

Converts an option_contract dict (Upstox/warehouse shape) into the Noren trading
symbol + token needed to place an order via FlattradeClient.

Design
------
- PURE: no network; ``search_fn`` is injected (sync or async) so the host tests
  can use a fake; the real wiring happens in L0.4 (FlattradeClient).
- FAIL-CLOSED: any ambiguity (no match, multi-match, wrong lot size) raises
  ``SymbolResolutionError`` — we NEVER return a best-guess.

``option_contract`` shape (from live_option_universe.py / option_contracts collection)
---------------------------------------------------------------------------------------
    {
        "underlying":     str,   # "NIFTY", "BANKNIFTY", "SENSEX"
        "strike":         float, # e.g. 25000.0
        "side":           str,   # "CE" or "PE"
        "expiry_date":    str,   # ISO date "YYYY-MM-DD"
        "lot_size":       int,   # e.g. 65
        "trading_symbol": str,   # Upstox symbol (not the Noren tsym)
        "instrument_key": str,   # Upstox instrument key
    }

``search_fn(exch, query) -> list[dict]`` return shape (Noren SearchScrip rows)
-------------------------------------------------------------------------------
The resolver assumes these field names in each returned scrip dict:

    tsym     str    Noren trading symbol          e.g. "NIFTY25JUN2025C25000"
    token    str    Noren instrument token        e.g. "43215"
    ls       str    Lot size                      e.g. "65"
    strprc   str    Strike price                  e.g. "25000.00"
    optt     str    Option type                   "CE" or "PE"
    exd      str    Expiry date in Noren format   "DD-Mon-YYYY" e.g. "26-Jun-2025"

The ``_parse_noren_expiry`` helper normalises ``exd`` → ISO "YYYY-MM-DD" for
comparison with ``contract["expiry_date"]``.  Month abbreviations used by Noren:
Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec.
"""
from __future__ import annotations

import datetime
import math
from typing import Any, Callable, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Explicit allow-list: underlying -> (exchange, expected_lot_size)
# Any underlying NOT in this map is rejected with SymbolResolutionError.
#
# NOTE: BANKNIFTY lot size changed 30->35; verify live before deploying.
# The warehouse records 30; the current NSE spec says 35 for some series.
# Until confirmed, 30 is the expected value — mismatch will raise.
UNDERLYING_SPEC: Dict[str, Tuple[str, int]] = {
    "NIFTY":     ("NFO", 65),
    "BANKNIFTY": ("NFO", 30),
    "SENSEX":    ("BFO", 20),
}

# Derived for backward compatibility with callers that import LOT_SIZE_EXPECTED.
LOT_SIZE_EXPECTED: Dict[str, int] = {u: spec[1] for u, spec in UNDERLYING_SPEC.items()}

_MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class SymbolResolutionError(Exception):
    """Raised when the resolver cannot unambiguously map a contract to a Noren scrip."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_noren_expiry(exd: str) -> str:
    """Convert Noren expiry string ``DD-Mon-YYYY`` to ISO ``YYYY-MM-DD``.

    Raises SymbolResolutionError on any parse failure (fail-closed).
    """
    try:
        parts = exd.strip().split("-")
        if len(parts) != 3:
            raise ValueError(f"expected DD-Mon-YYYY, got {exd!r}")
        day = int(parts[0])
        month = _MONTH_ABBR[parts[1]]
        year = int(parts[2])
        return datetime.date(year, month, day).isoformat()
    except (KeyError, ValueError, TypeError) as exc:
        raise SymbolResolutionError(f"cannot parse Noren expiry {exd!r}: {exc}") from exc


def _normalise_strike(value: Any) -> float:
    """Return float strike; raise SymbolResolutionError on conversion failure or non-finite value."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SymbolResolutionError(f"cannot parse strike {value!r}: {exc}") from exc
    # HOLE-2 fix: reject nan / inf / -inf
    if not math.isfinite(result):
        raise SymbolResolutionError(
            f"strike must be a finite number, got {result!r} (from {value!r})"
        )
    return result


def _normalise_lot_size(value: Any) -> int:
    """Return int lot size from a scrip row's ``ls`` field.

    HOLE-1 fix: require the value to represent an exact integer (no fractional
    lots) AND be strictly positive.  Raises SymbolResolutionError otherwise.
    """
    try:
        fval = float(value)
    except (TypeError, ValueError) as exc:
        raise SymbolResolutionError(f"cannot parse lot size {value!r}: {exc}") from exc
    if not fval.is_integer():
        raise SymbolResolutionError(
            f"lot size {value!r} is not a whole number (got {fval}); "
            "non-integer lot sizes are not allowed"
        )
    ival = int(fval)
    if ival <= 0:
        raise SymbolResolutionError(
            f"lot size must be positive, got {ival} (from {value!r})"
        )
    return ival


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(
    contract: Dict[str, Any],
    *,
    search_fn: Callable[[str, str], List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Resolve an option_contract dict to a Noren trading symbol + metadata.

    Parameters
    ----------
    contract:
        option_contract dict with keys ``underlying``, ``strike``, ``side``,
        ``expiry_date`` (ISO YYYY-MM-DD), ``lot_size``.
    search_fn:
        Callable(exch: str, query: str) -> list[scrip_dict].
        Each scrip_dict must contain: ``tsym``, ``token``, ``ls``, ``strprc``,
        ``optt``, ``exd`` (see module docstring for the field spec).
        May be sync or async — the caller must handle the async case; this
        function calls it synchronously (L0.4 will wrap it accordingly).

    Returns
    -------
    dict with keys: ``tsym``, ``token``, ``exch``, ``lot_size`` (int).

    Raises
    ------
    SymbolResolutionError
        On ANY ambiguity: no match, multiple matches, lot-size mismatch, or
        parse failure.  Never returns a best-guess.
    """
    underlying = str(contract.get("underlying") or "").strip().upper()
    if not underlying:
        raise SymbolResolutionError("contract missing 'underlying'")

    # HOLE-3 fix: explicit allow-list; unknown underlyings are rejected immediately.
    if underlying not in UNDERLYING_SPEC:
        raise SymbolResolutionError(
            f"unknown underlying {underlying!r}; supported underlyings: "
            f"{sorted(UNDERLYING_SPEC)}"
        )
    exch, expected_lot = UNDERLYING_SPEC[underlying]

    # HOLE-2 fix: _normalise_strike now rejects non-finite values.
    contract_strike = _normalise_strike(contract.get("strike"))

    contract_side = str(contract.get("side") or "").strip().upper()
    if contract_side not in ("CE", "PE"):
        raise SymbolResolutionError(f"contract 'side' must be CE or PE, got {contract_side!r}")

    contract_expiry = str(contract.get("expiry_date") or "").strip()
    if not contract_expiry:
        raise SymbolResolutionError("contract missing 'expiry_date'")

    contract_lot = contract.get("lot_size")
    if contract_lot is None:
        raise SymbolResolutionError("contract missing 'lot_size'")
    contract_lot = int(contract_lot)

    # Build a search query: "<UNDERLYING> <strike>"
    query = f"{underlying} {int(contract_strike) if contract_strike == int(contract_strike) else contract_strike}"

    # NOTE (robustness): wrap search_fn call so a raising search_fn never leaks
    # a raw non-SymbolResolutionError to the caller.
    try:
        rows = search_fn(exch, query)
    except SymbolResolutionError:
        raise
    except Exception as exc:
        raise SymbolResolutionError(
            f"search_fn raised an unexpected error for {underlying} {exch}: {exc}"
        ) from exc

    # Filter rows: strike + option type + expiry must all match exactly.
    matches: List[Dict[str, Any]] = []
    for row in rows:
        # NOTE (robustness): non-dict rows must surface as SymbolResolutionError.
        try:
            row_strprc = row.get("strprc")
        except AttributeError as exc:
            raise SymbolResolutionError(
                f"search_fn returned a non-dict row ({type(row).__name__!r}): {row!r}"
            ) from exc

        # Strike match
        try:
            row_strike = _normalise_strike(row_strprc)
        except SymbolResolutionError:
            continue
        if row_strike != contract_strike:
            continue

        # Option type match (CE/PE)
        row_optt = str(row.get("optt") or "").strip().upper()
        if row_optt != contract_side:
            continue

        # Expiry match: parse Noren exd -> ISO and compare
        try:
            row_expiry_iso = _parse_noren_expiry(str(row.get("exd") or ""))
        except SymbolResolutionError:
            continue
        if row_expiry_iso != contract_expiry:
            continue

        matches.append(row)

    if len(matches) == 0:
        raise SymbolResolutionError(
            f"no Noren scrip found for {underlying} {contract_strike} {contract_side} "
            f"expiry={contract_expiry} on {exch}"
        )
    if len(matches) > 1:
        tsyms = [r.get("tsym") for r in matches]
        raise SymbolResolutionError(
            f"ambiguous: {len(matches)} rows matched {underlying} {contract_strike} "
            f"{contract_side} expiry={contract_expiry}: {tsyms}"
        )

    row = matches[0]

    # HOLE-1 fix: _normalise_lot_size now rejects fractional and non-positive values.
    row_lot = _normalise_lot_size(row.get("ls"))

    if row_lot != contract_lot:
        raise SymbolResolutionError(
            f"lot size mismatch: scrip ls={row_lot} vs contract lot_size={contract_lot} "
            f"for {underlying} {contract_strike} {contract_side}"
        )
    # HOLE-3 fix: expected_lot is ALWAYS applied (never skipped — no .get() guard).
    if row_lot != expected_lot:
        raise SymbolResolutionError(
            f"lot size mismatch: scrip ls={row_lot} vs expected {underlying} lot "
            f"{expected_lot} (UNDERLYING_SPEC). Verify live lot size before deploying."
        )

    # HOLE-4 fix: strip tsym/token and reject blank after stripping.
    tsym = str(row.get("tsym") or "").strip()
    token = str(row.get("token") or "").strip()
    if not tsym or not token:
        raise SymbolResolutionError(
            f"matched scrip row is missing or blank tsym/token: {row!r}"
        )

    return {
        "tsym": tsym,
        "token": token,
        "exch": exch,
        "lot_size": row_lot,
    }
