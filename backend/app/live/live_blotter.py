"""Deployment-attributed live blotter — pure join, no DB/network.

The raw broker position/order tables on the Live dashboard answer "what does the
broker hold?" but not "which of MY deployed strategies opened it, and how is that
strategy doing?". This module joins the ``live_trades`` journal (attribution:
deployment / strategy / signal / entry / lots) against the live broker position
book (the P&L source of truth — Noren's own ``urmtom``/``rpnl``/``lp``), so the
operator sees per-deployment live trades with real P&L.

P&L source by row state:
  * OPEN  → the BROKER position book (Noren ``urmtom``/``rpnl``/``lp``) — the
    live truth, matching the dashboard's day-P&L tile. (Reading a live position's
    P&L from the journal would be wrong; the journal only knows the entry price.)
  * CLOSED → the journal's ``realized_pnl`` + ``exit_price``, written by the
    close-loop (``app.live.close_loop``) when the guard / a user stop squares the
    position. The broker drops a flat position from its book, so the journal is
    the only lasting record of a squared trade's result.

Multiple OPEN journal rows can share one trading symbol (a tsym traded, squared,
and re-entered), but the broker aggregates to a single position row per tsym. To
keep the live-P&L column sum-correct, the live broker P&L for a tsym is attributed
to AT MOST ONE journal row — the most recent by ``created_at``. Older same-tsym
rows are marked not-at-broker (CLOSED if the close-loop journaled them, else
FLAT). For max_concurrent>1 on the SAME tsym this is an attribution heuristic (the
broker can't split aggregated MTM per entry); the TOTAL is always exact.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _to_float(value: Any) -> Optional[float]:
    """Parse a Noren numeric (often a string) to float; None if unparseable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    """Parse a Noren netqty (string/float) to int; 0 if unparseable."""
    f = _to_float(value)
    return int(f) if f is not None else 0


def _position_pnl(pos: Dict[str, Any]) -> Optional[float]:
    """Broker P&L for a position = unrealized MTM (urmtom) + realized (rpnl).

    Mirrors the dashboard's deriveDayPnl: a held position carries urmtom; a
    same-day squared one carries rpnl. None only when neither field parses.
    """
    u = _to_float(pos.get("urmtom"))
    r = _to_float(pos.get("rpnl"))
    parts = [x for x in (u, r) if x is not None]
    return sum(parts) if parts else None


def _deployment_label(dep: Dict[str, Any], dep_id: str, strategy_id: str) -> str:
    """Human label: deployment name → 'strategy · instrument' → strategy → id."""
    name = str(dep.get("name") or "").strip()
    if name:
        return name
    sid = str(dep.get("strategy_id") or strategy_id or "").strip()
    inst = str(dep.get("instrument") or "").strip()
    if sid and inst:
        return f"{sid} · {inst}"
    return sid or dep_id or "—"


def build_live_blotter(
    trades: List[Dict[str, Any]],
    broker_positions: List[Dict[str, Any]],
    deployments_by_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join live_trades with the broker position book into attributed blotter rows.

    Parameters
    ----------
    trades
        ``live_trades`` docs (any order; sorted newest-first in the output).
    broker_positions
        Rows from ``BrokerClient.position_book()`` (tsym / netqty / lp / urmtom /
        rpnl). May be empty (broker not connected) — rows still carry attribution
        with null P&L.
    deployments_by_id
        ``{deployment_id: deployment_doc}`` for name resolution.

    Returns
    -------
    list of blotter rows, newest first.
    """
    # Broker positions indexed by tsym (one aggregated row per symbol).
    pos_by_tsym: Dict[str, Dict[str, Any]] = {
        str(p.get("tsym") or ""): p for p in broker_positions if p.get("tsym")
    }

    # Sort newest-first ONCE; this also fixes which same-tsym row "owns" the live
    # broker position (the first one seen per tsym, i.e. the most recent).
    ordered = sorted(trades, key=lambda t: str(t.get("created_at") or ""), reverse=True)

    claimed_tsyms: set = set()
    rows: List[Dict[str, Any]] = []
    for t in ordered:
        tsym = str(t.get("trading_symbol") or "")
        dep_id = str(t.get("deployment_id") or "")
        strategy_id = str(t.get("strategy_id") or "")
        dep = deployments_by_id.get(dep_id) or {}

        pos = pos_by_tsym.get(tsym)
        netqty = _to_int(pos.get("netqty")) if pos else 0
        # Live at broker only if the position exists, is non-flat, AND this is the
        # first (newest) journal row to claim the symbol.
        held = pos is not None and netqty != 0 and tsym not in claimed_tsyms
        ltp: Optional[float] = None
        pnl: Optional[float] = None
        if held:
            # LIVE: still open at the broker → show its live MTM (the truth).
            claimed_tsyms.add(tsym)
            ltp = _to_float(pos.get("lp"))
            pnl = _position_pnl(pos)
            status = "LIVE"
        elif str(t.get("status") or "").upper() == "CLOSED":
            # CLOSED: the close-loop journaled this squared trade — surface its
            # persisted realized P&L + exit mark (the broker book drops a flat
            # position, so the journal is the only lasting record of the result).
            status = "CLOSED"
            pnl = _to_float(t.get("realized_pnl"))
            ltp = _to_float(t.get("exit_price"))
        else:
            # FLAT: unfilled, superseded same-tsym, or externally closed with no
            # close-loop data — never claim a realized P&L we don't have.
            status = "FLAT"

        rows.append({
            "id": t.get("id"),
            "created_at": t.get("created_at"),
            "deployment_id": dep_id,
            "deployment_name": _deployment_label(dep, dep_id, strategy_id),
            "strategy_id": strategy_id,
            "instrument": t.get("instrument"),
            "trading_symbol": tsym,
            "direction": t.get("direction"),
            "lots": t.get("lots"),
            "quantity": t.get("quantity"),
            "entry_price": _to_float(t.get("entry_price")),
            "ltp": ltp,
            "pnl": pnl,
            "at_broker": held,
            # Honest status: LIVE = held at broker now (live MTM); CLOSED = the
            # close-loop journaled a realized P&L for a squared trade; FLAT = no
            # live position and no close-loop data (unfilled / superseded / closed
            # externally). We never claim a realized P&L we don't have.
            "status": status,
            "norenordno": t.get("norenordno"),
            # Resting broker OCO backstop state. When the entry filled but its OCO
            # failed to place, the journal doc carries oco_error="no_broker_backstop"
            # → surface it so the operator knows this OPEN position has no PC-down
            # broker net (only the software guard while the app is alive).
            "oco_error": t.get("oco_error"),
            # Resting OCO handle (the live_trades doc carries it when the backstop
            # was placed). The blotter UI matches this against the GTT/OCO book to
            # show a positive "OCO ✓" chip with the resting SL/TP band.
            "oco_al_id": t.get("oco_al_id"),
        })

    return rows
