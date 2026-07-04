"""Basket-level overall controls for PAPER trading (parity with the Live page).

Reuses the PURE live evaluator (app.live.overall_controls: build_overall_state
+ evaluate_overall) against the paper account's open-basket MTM. The config is
the SAME object shape the Live page's Overall Controls panel emits, persisted
in the live_overall_settings collection under scope="paper" — the Paper page
renders the same OverallSettingsPanel with scope="paper".

Runs from the LiveExitMonitor cycle (injected as overall_fn), so it inherits
the monitor's supervisor reconcile — a boot-before-OAuth gap cannot leave it
silently dead (the LiveExitMonitor/candle-roller lesson).

Fail-closed on bad readings: if ANY open leg lacks a live mark, the basket MTM
is treated as stale (None) and the pure evaluator returns no-exit unchanged —
the whole basket is never squared on a partial mark. Ratcheting state (floors)
is in-memory and resets whenever the open-basket composition changes, every
rule is disabled, or the basket empties.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

# In-memory ratchet state: sig = sorted open-trade ids the state was built for.
_state: Dict[str, Any] = {"sig": None, "state": None, "last": None}


def rules_enabled(cfg: Dict[str, Any]) -> bool:
    sl = cfg.get("sl") or {}
    target = cfg.get("target") or {}
    trailing = cfg.get("trailing") or {}
    return bool(sl.get("enabled")) or bool(target.get("enabled")) \
        or str(trailing.get("mode") or "none") != "none"


def _reset() -> None:
    _state["sig"] = None
    _state["state"] = None


def overall_status() -> Dict[str, Any]:
    """Snapshot for health/status surfaces."""
    return {"armed": _state["state"] is not None, "last": _state["last"]}


async def check_paper_overall_controls(
    db: Any,
    *,
    latest_tick_lookup: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    store_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """One evaluation pass. Returns {exit, reason, ...} — never raises."""
    from app.live.overall_controls import build_overall_state, evaluate_overall
    from app.paper_open_positions import build_open_positions

    try:
        if store_factory is not None:
            store = store_factory()
        else:
            from app.live.overall_settings_store import default_store
            store = default_store("paper")
        cfg = await store.get_config()
    except Exception as exc:  # noqa: BLE001 — config trouble must not kill the monitor
        return {"exit": False, "reason": f"config_unavailable: {str(exc)[:120]}"}

    if not rules_enabled(cfg):
        _reset()
        return {"exit": False, "reason": "disabled"}

    open_rows = await db.paper_trades.find(
        {"status": "OPEN"}, {"_id": 0, "events": 0}
    ).to_list(length=500)
    if not open_rows:
        _reset()
        return {"exit": False, "reason": "no_open_trades"}

    live = build_open_positions(open_rows, latest_tick_lookup=latest_tick_lookup)
    items = {p.get("id"): p for p in (live.get("items") or [])}

    # Fail-closed: a leg without a live tick (live_stale) makes the whole
    # basket reading stale — mtm=None and the pure evaluator no-ops. The
    # stored-unrealized fallback is NOT trusted for a whole-basket square.
    mtm: Optional[float] = 0.0
    for r in open_rows:
        leg = items.get(r.get("id"))
        if leg is None or leg.get("live_stale"):
            mtm = None
            break
        mtm += float(leg.get("unrealized_pnl") or 0.0)

    basket_premium = sum(
        float(r.get("entry_price") or 0) * float(r.get("quantity") or 0)
        for r in open_rows
    )
    sig = tuple(sorted(str(r.get("id") or "") for r in open_rows))
    if _state["sig"] != sig or _state["state"] is None:
        _state["sig"] = sig
        _state["state"] = build_overall_state(cfg, basket_premium)

    res = evaluate_overall(_state["state"], mtm)
    _state["state"] = res["state"]
    _state["last"] = {"mtm": mtm, "exit": res["exit"], "reason": res.get("reason"),
                      "basket_premium": round(basket_premium, 2), "legs": len(open_rows)}

    if not res["exit"]:
        return {"exit": False, "reason": None, "mtm": mtm}

    from app.paper_squareoff import square_off_open_paper_trades
    summaries = await square_off_open_paper_trades(
        db, latest_tick_lookup=latest_tick_lookup, reason=str(res.get("reason") or "overall"),
    )
    log.info("paper overall controls squared the basket: reason=%s legs=%d mtm=%s",
             res.get("reason"), len(summaries or []), mtm)
    _reset()
    return {"exit": True, "reason": res.get("reason"),
            "squared": len(summaries or []), "mtm": mtm}
