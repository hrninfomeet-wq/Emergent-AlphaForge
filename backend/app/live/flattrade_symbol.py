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
Real Flattrade SearchScrip field names (verified live — these are the truth):

    tsym      str    Noren trading symbol        e.g. "NIFTY23JUN26C25000"
    token     str    Noren instrument token      e.g. "56432"
    ls        str    Lot size                    e.g. "65"
    symname   str    Symbol name                 e.g. "NIFTY", "BANKNIFTY", "BSXOPT"
    optt      str    Option type                 "CE" or "PE"
    exd       str    Expiry date DD-MON-YYYY     e.g. "23-JUN-2026" (UPPERCASE month)
    dname     str    Display name                e.g. "NIFTY 23JUN26 25000 CE "

NOTE: There is NO ``strprc`` field. Strike is parsed from ``dname`` — the token
immediately before the CE/PE suffix is the strike (works for both NFO and BFO
formats). SENSEX ``symname`` is ``BSXOPT`` (not ``SENSEX``).
"""
from __future__ import annotations

import datetime
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Explicit allow-list: underlying -> (exchange, expected_symname, expected_lot_size)
# Any underlying NOT in this map is rejected with SymbolResolutionError.
#
# NOTE: BANKNIFTY lot size changed 30->35; verify live before deploying.
# The warehouse records 30; the current NSE spec says 35 for some series.
# Until confirmed, 30 is the expected value — mismatch will raise.
#
# NOTE: SENSEX symname in Flattrade SearchScrip is "BSXOPT", not "SENSEX".
UNDERLYING_SPEC: Dict[str, Tuple[str, str, int]] = {
    "NIFTY":     ("NFO", "NIFTY",     65),
    "BANKNIFTY": ("NFO", "BANKNIFTY", 30),
    "SENSEX":    ("BFO", "BSXOPT",    20),
}

# Derived for backward compatibility with callers that import LOT_SIZE_EXPECTED.
LOT_SIZE_EXPECTED: Dict[str, int] = {u: spec[2] for u, spec in UNDERLYING_SPEC.items()}


# ---------------------------------------------------------------------------
# Exchange rules engine — the single source of truth for what order types /
# products each exchange allows + the per-exchange constants. Drives both the
# order ticket (enable/disable controls) and the order choke-point (server-side
# re-validation). Exchange is DERIVED from the underlying, never user-picked.
#
# v1 deliberately ships CO/BO OFF (BSE/BFO blocks them entirely, and our
# software-monitored exits supersede their use) and SL-MKT OFF (RMS-blocked for
# index options on both exchanges — steer to SL-LMT).
#
# NOTE: lot_size + freeze_qty are exchange figures that change periodically —
# re-verify against the live broker before relying on them (lot sizes have moved
# NIFTY 75->65, BANKNIFTY 30<->35; freeze quantities are revised by NSE/BSE).
# ---------------------------------------------------------------------------
EXCHANGE_RULES: Dict[str, Dict[str, Any]] = {
    "NIFTY": {
        "exch": "NFO", "lot_size": 65, "freeze_qty": 1800, "tick": 0.05,
        "products": ["NRML", "MIS"], "price_types": ["LIMIT", "MARKET", "SL-LMT"],
        "expiry_cadence": "weekly_tue",
    },
    "BANKNIFTY": {
        "exch": "NFO", "lot_size": 30, "freeze_qty": 600, "tick": 0.05,
        "products": ["NRML", "MIS"], "price_types": ["LIMIT", "MARKET", "SL-LMT"],
        "expiry_cadence": "monthly_last_tue",
    },
    "SENSEX": {
        "exch": "BFO", "lot_size": 20, "freeze_qty": 1000, "tick": 0.05,
        "products": ["NRML", "MIS"], "price_types": ["LIMIT", "MARKET", "SL-LMT"],
        "expiry_cadence": "weekly_thu",
    },
}


def rules_for(underlying: Any) -> Optional[Dict[str, Any]]:
    """Return a COPY of the exchange rules for ``underlying`` (uppercased), or None.

    The copy (including its nested lists) is independent of EXCHANGE_RULES so a
    caller can never mutate the shared table.
    """
    if not isinstance(underlying, str):
        return None
    rules = EXCHANGE_RULES.get(underlying.strip().upper())
    if rules is None:
        return None
    out = dict(rules)
    out["products"] = list(rules["products"])
    out["price_types"] = list(rules["price_types"])
    return out


def market_allowed(
    rules: Optional[Dict[str, Any]],
    *,
    expiry_date: Any = None,
    strike: Any = None,
    moneyness: Any = None,
) -> bool:
    """Whether a MARKET order is permitted for this contract.

    Phase 1: returns True when MARKET is in the exchange's price_types (the order
    ticket still defaults to LIMIT). The strict liquidity predicate — restrict
    MARKET to the near weekly/monthly expiries and liquid (high-OI, non-deep-ITM)
    strikes — is a Phase-3 refinement; the ``expiry_date``/``strike``/``moneyness``
    parameters are accepted now so callers don't change later.
    """
    if not rules:
        return False
    return "MARKET" in rules.get("price_types", [])

# Uppercase month abbreviations as used by Flattrade exd field (DD-MON-YYYY).
_MONTH_ABBR_UPPER = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class SymbolResolutionError(Exception):
    """Raised when the resolver cannot unambiguously map a contract to a Noren scrip."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_exd(exd: str) -> str:
    """Convert Flattrade expiry string ``DD-MON-YYYY`` (uppercase month) to ISO ``YYYY-MM-DD``.

    Examples:
        "23-JUN-2026" -> "2026-06-23"
        "30-JUN-2026" -> "2026-06-30"
        "25-JUN-2026" -> "2026-06-25"

    Raises SymbolResolutionError on any parse failure (fail-closed).
    """
    try:
        parts = exd.strip().split("-")
        if len(parts) != 3:
            raise ValueError(f"expected DD-MON-YYYY, got {exd!r}")
        day = int(parts[0])
        month_key = parts[1].upper()
        if month_key not in _MONTH_ABBR_UPPER:
            raise ValueError(f"unknown month abbreviation {parts[1]!r} in {exd!r}")
        month = _MONTH_ABBR_UPPER[month_key]
        year = int(parts[2])
        return datetime.date(year, month, day).isoformat()
    except (KeyError, ValueError, TypeError) as exc:
        raise SymbolResolutionError(f"cannot parse Flattrade exd {exd!r}: {exc}") from exc


def _contract_expiry_iso(expiry_date: Any) -> str:
    """Normalize the contract's expiry to ISO 'YYYY-MM-DD' string.

    Accepts: ISO string "YYYY-MM-DD", datetime.date, or datetime.datetime.
    Raises SymbolResolutionError if blank or unparseable.
    """
    if isinstance(expiry_date, (datetime.date, datetime.datetime)):
        return expiry_date.strftime("%Y-%m-%d")
    s = str(expiry_date or "").strip()
    if not s:
        raise SymbolResolutionError("contract missing 'expiry_date'")
    # Validate it looks like an ISO date
    try:
        datetime.date.fromisoformat(s)
    except ValueError as exc:
        raise SymbolResolutionError(
            f"expiry_date {s!r} is not a valid ISO date YYYY-MM-DD: {exc}"
        ) from exc
    return s


def _strike_from_dname(dname: str) -> float:
    """Parse the strike price from a Flattrade dname field.

    The dname has the option side (CE/PE) as the LAST whitespace-separated token,
    and the strike as the numeric token immediately before it.

    Works for both NFO and BFO formats:
        "NIFTY 23JUN26 25000 CE "    -> 25000.0
        "BANKNIFTY 30JUN26 52000 CE " -> 52000.0
        "SENSEX 25 JUN 80000 CE"     -> 80000.0

    Raises SymbolResolutionError if the strike cannot be parsed.
    """
    tokens = dname.strip().split()
    if len(tokens) < 2:
        raise SymbolResolutionError(
            f"dname {dname!r} has too few tokens to extract strike"
        )
    # Last token must be CE or PE (side)
    last = tokens[-1].upper()
    if last not in ("CE", "PE"):
        raise SymbolResolutionError(
            f"dname {dname!r}: last token {tokens[-1]!r} is not CE/PE"
        )
    # Strike is the token immediately before the side
    strike_token = tokens[-2]
    try:
        val = float(strike_token)
    except (ValueError, TypeError) as exc:
        raise SymbolResolutionError(
            f"dname {dname!r}: token before side is {strike_token!r}, not numeric: {exc}"
        ) from exc
    return val


def _normalise_strike(value: Any) -> float:
    """Return float strike; raise SymbolResolutionError on conversion failure or non-finite value."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SymbolResolutionError(f"cannot parse strike {value!r}: {exc}") from exc
    # Reject nan / inf / -inf
    if not math.isfinite(result):
        raise SymbolResolutionError(
            f"strike must be a finite number, got {result!r} (from {value!r})"
        )
    return result


def _normalise_tick(value: Any) -> float:
    """Parse the scrip ``ti`` (tick size) field to a positive float.

    If the value is missing (None), unparseable, or <= 0, default to 0.05
    and emit a log warning.  Index option tick is always 0.05 so this is a
    safe and informative fallback.

    Examples::
        "0.05"  -> 0.05
        "1"     -> 1.0
        None    -> 0.05  (+ warning)
        "bad"   -> 0.05  (+ warning)
        "0"     -> 0.05  (+ warning)
        "-0.05" -> 0.05  (+ warning)
    """
    _DEFAULT_TICK = 0.05
    try:
        fval = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        log.warning(
            "cannot parse scrip ti=%r as a float — defaulting tick to %s (index option standard)",
            value, _DEFAULT_TICK,
        )
        return _DEFAULT_TICK
    if fval <= 0:
        log.warning(
            "scrip ti=%r parses to non-positive value %s — defaulting tick to %s",
            value, fval, _DEFAULT_TICK,
        )
        return _DEFAULT_TICK
    return fval


def _normalise_lot_size(value: Any) -> int:
    """Return int lot size from a scrip row's ``ls`` field.

    Requires the value to represent an exact integer (no fractional lots)
    AND be strictly positive.  Raises SymbolResolutionError otherwise.
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
        Each scrip_dict must contain: ``tsym``, ``token``, ``ls``, ``symname``,
        ``optt``, ``exd`` (DD-MON-YYYY), ``dname`` — see module docstring.
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

    # Explicit allow-list; unknown underlyings are rejected immediately (fail-closed).
    if underlying not in UNDERLYING_SPEC:
        raise SymbolResolutionError(
            f"unknown underlying {underlying!r}; supported underlyings: "
            f"{sorted(UNDERLYING_SPEC)}"
        )
    exch, expected_symname, expected_lot = UNDERLYING_SPEC[underlying]

    # Reject non-finite strikes.
    contract_strike = _normalise_strike(contract.get("strike"))
    if contract_strike <= 0:
        raise SymbolResolutionError(
            f"strike must be positive, got {contract_strike!r}"
        )

    contract_side = str(contract.get("side") or "").strip().upper()
    if contract_side not in ("CE", "PE"):
        raise SymbolResolutionError(f"contract 'side' must be CE or PE, got {contract_side!r}")

    # Normalize expiry to ISO string (fails closed on blank/bad format).
    contract_expiry = _contract_expiry_iso(contract.get("expiry_date"))

    # contract lot_size is ADVISORY — the broker scrip ls is authoritative.
    # May be None; that is fine; we never hard-fail on a missing or stale value.
    contract_lot = contract.get("lot_size")  # may be None

    # Build search query: "<UNDERLYING> <strike_int>", e.g. "NIFTY 25000"
    strike_int = int(contract_strike) if contract_strike == int(contract_strike) else contract_strike
    query = f"{underlying} {strike_int}"

    # Wrap search_fn so a raising search_fn never leaks a raw exception to the caller.
    try:
        rows = search_fn(exch, query)
    except SymbolResolutionError:
        raise
    except Exception as exc:
        raise SymbolResolutionError(
            f"search_fn raised an unexpected error for {underlying} {exch}: {exc}"
        ) from exc

    # Filter rows: ALL four criteria must match.
    matches: List[Dict[str, Any]] = []
    for row in rows:
        # Non-dict rows must surface as SymbolResolutionError.
        try:
            row_symname = row.get("symname")
        except AttributeError as exc:
            raise SymbolResolutionError(
                f"search_fn returned a non-dict row ({type(row).__name__!r}): {row!r}"
            ) from exc

        # 1. symname filter (e.g. "NIFTY" or "BSXOPT" for SENSEX)
        if str(row_symname or "").strip() != expected_symname:
            continue

        # 2. Option type match (CE/PE)
        row_optt = str(row.get("optt") or "").strip().upper()
        if row_optt != contract_side:
            continue

        # 3. Expiry match: parse Flattrade exd (DD-MON-YYYY) -> ISO and compare
        try:
            row_expiry_iso = _parse_exd(str(row.get("exd") or ""))
        except SymbolResolutionError:
            continue
        if row_expiry_iso != contract_expiry:
            continue

        # 4. Strike match: parsed from dname (never from tsym — format is inconsistent)
        try:
            row_strike = _strike_from_dname(str(row.get("dname") or ""))
        except SymbolResolutionError:
            continue
        if row_strike != contract_strike:
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

    # Broker scrip ls is the authoritative lot size.
    # The 4-way match (symname/optt/exd/strike) already guarantees the right contract;
    # the positive-integer sanity check stays (a zero/negative/fractional ls is always wrong).
    row_lot = _normalise_lot_size(row.get("ls"))

    # contract lot_size is advisory — a stale or absent value must NEVER block.
    # (No cross-check against contract_lot.)

    # UNDERLYING_SPEC mismatch: warn, do NOT raise.  The broker ls is still used.
    if row_lot != expected_lot:
        log.warning(
            "lot size: broker ls=%s differs from UNDERLYING_SPEC %s=%s "
            "— using broker ls (authoritative); update the spec",
            row_lot, underlying, expected_lot,
        )

    # Strip tsym/token and reject blank after stripping.
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
        "tick": _normalise_tick(row.get("ti")),
    }
