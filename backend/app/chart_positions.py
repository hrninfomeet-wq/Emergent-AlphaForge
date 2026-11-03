"""Read-only chart overlays. Broker quantities and paper lots never get added."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

from app.chart_stream import finite, tick_age_ms


def _positive(value):
    number = finite(value)
    return number if number is not None and number > 0 else None


def _exchange(instrument):
    return {"NSE_FO": "NFO", "BSE_FO": "BFO", "NSE_EQ": "NSE", "BSE_EQ": "BSE"}.get(instrument.get("segment"))


def broker_matches(row, instrument):
    if instrument.get("kind") == "index":
        return False
    if str(row.get("exch") or "").upper() != _exchange(instrument):
        return False
    if instrument.get("expiry"):
        from app.live_marks import parse_position_identity
        identity = parse_position_identity(row)
        actual_expiry = identity[1] if identity else None
        if not actual_expiry and instrument.get("kind") == "future":
            match = re.search(r"(\d{2}[A-Z]{3}\d{2})F(?:UT)?$", str(row.get("tsym") or "").upper())
            if match:
                try:
                    actual_expiry = datetime.strptime(match.group(1), "%d%b%y").date().isoformat()
                except ValueError:
                    return False
        if actual_expiry and actual_expiry != str(instrument["expiry"])[:10]:
            return False
        if identity and instrument.get("kind") == "option":
            if finite(instrument.get("strike")) is not None and finite(instrument["strike"]) != identity[2]:
                return False
            if instrument.get("option_type") and str(instrument["option_type"]).upper() != identity[3]:
                return False
        if not actual_expiry and str(row.get("tsym") or "") != str(instrument.get("symbol") or ""):
            # A bare reusable exchange token cannot prove historical expiry.
            return False
    # Both fields are exchange-issued tokens in the CURRENT instrument master.
    token = instrument.get("exchange_token")
    if token is not None and str(row.get("token") or "") == str(token):
        return True
    if row.get("tick_key") == instrument["instrument_key"]:
        return True
    if instrument.get("kind") == "equity":
        actual = str(row.get("tsym") or "").upper()
        expected = str(instrument.get("symbol") or "").upper()
        return bool(expected and (actual == expected or actual == expected + "-EQ"))
    return False


def _contract_matches(row, instrument):
    key = str(row.get("instrument_key") or "")
    parts = key.split("|")
    if "|".join(parts[:2]) != instrument["instrument_key"]:
        return False
    wanted = str(instrument.get("expiry") or "")[:10]
    for field in ("strike", "option_type"):
        if row.get(field) is not None and instrument.get(field) is not None and str(row[field]) != str(instrument[field]):
            if field != "strike" or finite(row[field]) != finite(instrument[field]):
                return False
    if wanted:
        expiry = str(row.get("expiry") or row.get("expiry_date") or "")[:10]
        if not expiry and len(parts) == 3:
            try:
                expiry = datetime.strptime(parts[2], "%d-%m-%Y").date().isoformat()
            except ValueError:
                expiry = parts[2]
        if not expiry:
            symbol = str(row.get("trading_symbol") or "")
            if symbol and symbol == instrument.get("symbol"):
                return True  # Exact dated current-master trading symbol.
            from app.live_marks import parse_position_identity
            identity = parse_position_identity({"tsym": symbol, "dname": symbol})
            expiry = identity[1] if identity else ""
        if expiry != wanted:
            return False
    return True


def _levels(item_id, stop, target, label, quantity, source):
    result = []
    for kind, value in (("stop", stop), ("target", target)):
        if _positive(value) is not None:
            result.append({"id": f"{item_id}:{kind}", "kind": kind, "price": float(value),
                           "label": label, "quantity": quantity, "source": source})
    return result


def _apply_levels(row, levels):
    row["protection_levels"] = levels
    for kind in ("stop", "target"):
        prices = {level["price"] for level in levels if level["kind"] == kind}
        row[f"{kind}_price"] = next(iter(prices)) if len(prices) == 1 else None
    row["protection_source"] = "; ".join(sorted({level["source"] for level in levels})) or "No matched protection metadata"


def build_chart_positions(instrument, *, broker_book=None, paper_rows=(), conditional_rows=(),
                          guards=(), ticks=None, now_ms=None, paper_stale=False, guard_health="unknown"):
    """Pure adapter; unknown prices/P&L stay null, not plausible-looking zeroes."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    key = instrument["instrument_key"]
    tick = (ticks or {}).get(key) or {}
    age = tick_age_ms(tick, now_ms)
    tick_fresh = age is not None and age <= 10_000 and _positive(tick.get("last_price")) is not None
    positions, warnings = [], []
    book = broker_book or {}
    book_age = finite(book.get("age_ms"))
    book_fresh = bool(broker_book is not None and not book.get("stale") and book_age is not None and book_age <= 30_000)
    if broker_book is None:
        warnings.append("Broker positions unavailable. This does not confirm a flat account; paper charts remain available.")
    elif not book_fresh:
        warnings.append("Broker position snapshot is stale; live quantities and P&L are not confirmed.")
    if paper_stale:
        warnings.append("Paper position snapshot is stale.")

    for raw in book.get("rows") or []:
        qty = finite(raw.get("netqty"))
        if not qty or not broker_matches(raw, instrument):
            if qty and str(raw.get("exch") or "") == _exchange(instrument) and str(raw.get("token") or "") == str(instrument.get("exchange_token") or "missing"):
                warnings.append("A broker position shares this exchange token but its contract identity could not be confirmed; it was omitted.")
            continue
        entry, broker_price = _positive(raw.get("netavgprc")), _positive(raw.get("lp"))
        mark, pnl = broker_price, finite(raw.get("urmtom"))
        fresh = book_fresh and tick_fresh
        if fresh:
            mark = float(tick["last_price"])
            factor = _positive(raw.get("prcftr")) or 1.0
            multiplier = _positive(raw.get("mult")) or 1.0
            if pnl is not None and broker_price is not None:
                pnl += (mark - broker_price) * qty * factor * multiplier
            # Missing broker MTM is unknown: do not guess its carry-forward basis.
        identity = f"broker:{raw.get('exch')}:{raw.get('token') or raw.get('tsym')}:{raw.get('prd')}"
        row = {"id": identity, "origin": "broker", "instrument_key": key,
               "symbol": instrument.get("symbol"), "quantity": qty, "entry_price": entry,
               "mark_price": mark, "pnl": round(pnl, 2) if pnl is not None else None,
               "fresh": fresh, "mark_age_ms": age if fresh else None, "broker_age_ms": book_age,
               "label": f"Broker · {raw.get('prd') or 'position'}", "pnl_basis": "Unrealized, before charges"}
        levels = []
        for guard in guards:
            if not guard.get("seen_filled"):
                continue
            if any(str(guard.get(field) or "") != str(raw.get(field) or "") for field in ("exch", "tsym", "prd")):
                continue
            state = guard.get("state") or {}
            label = str(guard.get("deployment_id") or guard.get("source") or "Guard")
            source = f"AlphaForge software guard ({guard_health}); not a broker order"
            levels += _levels(guard.get("id"), state.get("stop_level"), state.get("target_level"), label, guard.get("qty"), source)
        for order in conditional_rows:
            # Conditional Flattrade execution is intraday-only. The same
            # contract held as carry-forward is a separate broker position.
            if str(raw.get("prd") or "") != "I" or qty <= 0:
                continue
            if order.get("mode") != "live" or not _contract_matches(order.get("contract") or {}, instrument):
                continue
            residual = (finite(order.get("filled_quantity")) or 0) - (finite(order.get("closed_quantity")) or 0)
            if residual <= 0 or order.get("status") in {"CLOSED", "CANCELED", "EXPIRED", "REJECTED", "DRAFT"}:
                continue
            state = order.get("exit_state") or {}
            source = f"Conditional plan · {order.get('protection') or 'protection unconfirmed'}"
            levels += _levels(order.get("id"), state.get("stop"), state.get("target"), "Conditional", residual, source)
        _apply_levels(row, levels)
        positions.append(row)

    for trade in paper_rows:
        if str(trade.get("status")) != "OPEN" or not _contract_matches(trade, instrument):
            if str(trade.get("status")) == "OPEN" and (
                    str(trade.get("instrument_key") or "").startswith(key + "|") or trade.get("instrument_key") == key):
                warnings.append("A paper position could not be matched to the current contract expiry and was omitted.")
            continue
        qty, entry = finite(trade.get("quantity")), _positive(trade.get("entry_price"))
        if not qty:
            continue
        fresh = tick_fresh and not paper_stale
        mark = float(tick["last_price"]) if fresh else _positive(trade.get("last_price"))
        pnl = (mark - entry) * qty if fresh and mark is not None and entry is not None else finite(trade.get("unrealized_pnl"))
        risk = trade.get("risk") or {}
        identity = f"paper:{trade.get('id')}"
        row = {"id": identity, "origin": "paper", "instrument_key": key, "symbol": instrument.get("symbol"),
               "quantity": qty, "entry_price": entry, "mark_price": mark,
               "pnl": round(pnl, 2) if pnl is not None else None, "fresh": fresh,
               "mark_age_ms": age, "label": trade.get("deployment_name") or "Paper deployment",
               "pnl_basis": "Paper unrealized, before exit costs"}
        _apply_levels(row, _levels(identity, risk.get("stop_price"), risk.get("target_price"), row["label"], qty, "Paper risk plan"))
        positions.append(row)

    # Conditional paper trades have their own ledger; they are not broker rows.
    for order in conditional_rows:
        if order.get("mode") != "paper" or not _contract_matches(order.get("contract") or {}, instrument):
            continue
        qty = (finite(order.get("filled_quantity")) or 0) - (finite(order.get("closed_quantity")) or 0)
        if qty <= 0 or order.get("status") in {"CLOSED", "CANCELED", "EXPIRED", "REJECTED", "DRAFT"}:
            continue
        entry = _positive(order.get("entry_fill_price"))
        mark = float(tick["last_price"]) if tick_fresh else _positive(order.get("last_premium"))
        state = order.get("exit_state") or {}
        identity = f"paper:conditional:{order.get('id')}"
        row = {"id": identity, "origin": "paper", "instrument_key": key, "symbol": instrument.get("symbol"),
               "quantity": qty, "entry_price": entry, "mark_price": mark,
               "pnl": round((mark - entry) * qty, 2) if mark is not None and entry is not None else None,
               "fresh": tick_fresh, "mark_age_ms": age, "label": "Conditional paper",
               "pnl_basis": "Paper unrealized, before exit costs"}
        _apply_levels(row, _levels(identity, state.get("stop"), state.get("target"), row["label"], qty, "Conditional paper plan"))
        positions.append(row)
    return {"instrument_key": key, "positions": positions, "warnings": list(dict.fromkeys(warnings)), "as_of_ms": now_ms}


async def chart_positions(instrument):
    from app.db import get_db
    from app.runtime import upstox_stream_manager, live_position_guard
    from app.live.live_position_guard import get_registry, guard_health
    from app.routers.live_broker import _marks_service
    from app.routers.journals import _open_rows_source

    source = _open_rows_source()
    async def conditional_rows():
        return await get_db().conditional_orders.find({
            "contract.instrument_key": instrument["instrument_key"],
            "status": {"$nin": ["CLOSED", "CANCELED", "EXPIRED", "REJECTED", "DRAFT"]},
        }, {"_id": 0, "events": 0, "actions": 0}).limit(100).to_list(length=100)

    results = await asyncio.gather(_marks_service().book(), source.get(), conditional_rows(), return_exceptions=True)
    book, paper, conditional = [None if isinstance(result, BaseException) else result for result in results]
    status = guard_health(live_position_guard.status(), now_utc=datetime.now(timezone.utc))
    result = build_chart_positions(instrument, broker_book=book, paper_rows=paper or [],
                                  conditional_rows=conditional or [], guards=get_registry().snapshot(),
                                  ticks=upstox_stream_manager.latest_tick_map(),
                                  paper_stale=bool(source.stale), guard_health=str(status.get("state") or "unknown"))
    if paper is None:
        result["warnings"].append("Paper positions unavailable; this does not confirm an empty paper ledger.")
    if conditional is None:
        result["warnings"].append("Conditional protection metadata unavailable.")
    return result
