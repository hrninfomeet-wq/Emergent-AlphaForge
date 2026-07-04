"""Read-only + L3 live-broker routes.

Mirrors the Upstox auth/status patterns in app/routers/broker.py.
READ routes never crash when not connected: return 400/empty, never 500.

Routes
------
GET  /flattrade/status                  — token connection status
GET  /flattrade/auth/start              — return login URL (400 if not configured)
GET  /flattrade/auth/callback?code=...  — exchange code, save token, redirect to frontend
POST /flattrade/disconnect              — delete the stored token

GET  /live-broker/positions             — broker position book (real API)
GET  /live-broker/orders                — broker order book (real API)
GET  /live-broker/trades                — broker trade book (real API)
GET  /live-broker/limits                — broker account limits / margin (real API)
GET  /live-broker/reconcile             — reconcile report (broker vs empty internal state)
GET  /live-broker/symbol/resolve        — preview Noren tsym resolution for a contract

GET  /live-broker/order/dry-run         — build intent + run safety checks WITHOUT placing

GET  /live-broker/safety-config         — get safety guardrails config
PUT  /live-broker/safety-config         — update numeric thresholds
POST /live-broker/safety-config/reset-latch — clear the blocked_until_reset latch

L3 routes (new — order entry, mode, test-session, kill)
---------------------------------------------------------
GET  /live-broker/mode                  — current mode doc
PUT  /live-broker/mode                  — transition mode (PAPER/LIVE_OFFLINE/LIVE_TEST)
POST /live-broker/order/place           — THE ONLY entry route → executor (guarded chokepoint)
POST /live-broker/order/square          — manual square of the test position (exit-only)
GET  /live-broker/test-session          — deadline/remaining/heartbeat/status
POST /live-broker/kill-switch           — EXECUTE panic squareoff + revert mode (L3: transmits)

CHOKEPOINT CLASSIFICATION (grep-verifiable):
  ENTRY place_order  : executor.place_live_test_order ONLY (via POST /order/place)
  EXIT  place_order  : auto_square.square_position (square route + kill + timer)
  EXIT  cancel_order : auto_square.square_position + kill_switch.panic_squareoff
  SL backstop        : build_sl_backstop_intent → client.place_order inside _make_arm (exit-only sell-to-close)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, StrictBool
from typing import Literal as _Literal

from app.live import flattrade_token
from app.live.flattrade_token import (
    DEFAULT_USER_ID,
    build_login_url,
    disconnect,
    exchange_code_for_token,
    get_status,
    get_token,
    is_configured,
    save_token,
)
from app.live.flattrade_client import FlattradeClient
from app.live.reconcile import reconcile
from app.live.flattrade_symbol import (
    SymbolResolutionError,
    resolve,
    _parse_exd,
    _strike_from_dname,
)
from app.live.portfolio_greeks import compute_portfolio_greeks
from app.live.kill_switch import (
    SafetyConfigStore,
    default_store as _default_safety_store,
    plan_squareoff,
    panic_squareoff,
)
from app.live.mode import ModeStore, default_store as _default_mode_store
from app.live.idempotency import IntentStore, default_store as _default_intent_store
from app.live.session_store import SessionStore, default_store as _default_session_store
from app.live import auto_square
from app.live.auto_square import (
    build_sl_backstop_intent,
    deadline_iso,
    square_position,
)
from app.live import executor as _executor_mod
from app.live.engine import LiveEngine
from app.live.option_premium import match_contract, resolve_premium
from app.live.atm_suggest import nearest_expiry, atm_strike as _atm_strike_pure
from app.live.overall_settings_store import (
    OverallSettingsStore,
    default_store as _default_overall_store,
)
from app.live import gtt as _gtt_mod
from app.live.live_position_guard import get_registry as _get_live_registry
from app.live.live_sl_monitor import build_monitor_state

log = logging.getLogger(__name__)

api = APIRouter()

# ---------------------------------------------------------------------------
# Module-level LiveEngine singleton
#
# The singleton matters because LiveEngine.halted is in-memory: a halt set by
# the executor's _abort_protect during one request must survive to the next
# request's can_trade() check.  A fresh engine per request would lose that flag.
#
# The config-store latch (blocked_until_reset) persists in Mongo, but the
# in-memory halted flag does not — hence the singleton.
#
# Fail-CLOSED rule: if a real engine cannot be constructed (e.g. DB down at
# import time), _l3_engine() returns a _ClosedEngine whose can_trade() always
# returns (False, "engine_unavailable"). NEVER fall back to an always-True
# permissive engine in production.
# ---------------------------------------------------------------------------

_live_engine_singleton: Optional["LiveEngine"] = None
_live_engine_init_error: Optional[str] = None


def _build_live_engine_singleton() -> None:
    """Construct and cache the LiveEngine singleton from the real stores.

    Called once at first use.  On any error the singleton remains None and
    _live_engine_init_error is set; subsequent calls to _l3_engine() return
    the fail-closed _ClosedEngine.
    """
    global _live_engine_singleton, _live_engine_init_error
    try:
        from app.db import get_db
        db = get_db()
        _live_engine_singleton = LiveEngine(
            client=_order_client(),         # may be None if not yet connected
            orders_collection=db.live_orders,
            intent_store=_intent_store(),
            config_store=_config_store(),
        )
        log.info("LiveEngine singleton constructed (client=%r)", _order_client())
    except Exception as exc:
        _live_engine_init_error = str(exc)
        log.error("LiveEngine singleton construction FAILED — will fail CLOSED: %s", exc)


class _ClosedEngine:
    """Fail-closed sentinel engine returned when a real engine cannot be built.

    can_trade() always returns (False, "engine_unavailable") so the halt/latch
    gate is never bypassed. This is the ONLY acceptable fallback; the permissive
    (always-True) engine must NOT be used in production.
    """
    halted: bool = True
    halt_reason: str = "engine_unavailable"

    async def can_trade(self):
        return False, "engine_unavailable"

    async def halt(self, reason: str) -> None:
        log.error("_ClosedEngine.halt called (engine unavailable): %s", reason)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRONTEND_POST_AUTH_URL = lambda: os.environ.get("FRONTEND_POST_AUTH_URL", "http://localhost:3000/live-trading")


async def _get_client() -> FlattradeClient:
    """Return a FlattradeClient for the default user's stored token.

    Raises HTTPException(400) if no token is stored.
    """
    doc = await _get_token_doc()
    return FlattradeClient(
        jKey=doc["jKey"],
        uid=doc["uid"],
        actid=doc["actid"],
    )


async def _get_token_doc() -> dict:
    """Return the raw token doc for the default user.

    Raises HTTPException(400) if missing.
    """
    from app.db import get_db
    db = get_db()
    doc = await db.live_broker_tokens.find_one(
        {"user": DEFAULT_USER_ID, "broker": "flattrade"},
    )
    if not doc:
        raise HTTPException(400, "Flattrade not connected. Complete OAuth at /flattrade/auth/start.")
    return doc


# ---------------------------------------------------------------------------
# Wiring getters — monkeypatched by tests to inject fakes
#
# All production instances are created lazily here; tests patch these
# module-level functions so no real DB / client is touched in tests.
# ---------------------------------------------------------------------------

def _order_client() -> Optional[FlattradeClient]:
    """Return the FlattradeClient for ORDER routes (None if not connected).

    Tests monkeypatch this to return a MockNoren. The real version does a
    synchronous-style best-effort check; ORDER routes that need this must call
    it and raise 400 on None.

    NOTE: this is intentionally *not* async because the routes call it
    synchronously before the async broker calls. The actual FlattradeClient
    object is cheap to create; the network cost is in the individual method calls.
    """
    # In production we try to build from the stored token synchronously.
    # We cannot await here; routes that use _order_client() fetch the token
    # via _get_client() if they need the actual async-fetched version.
    # For L3 routes, tests monkeypatch this to a MockNoren so it never runs.
    return None  # prod routes call _get_client() directly; tests patch this


def _l3_engine():
    """Return the LiveEngine singleton (real or fail-closed).

    Production path:
      - Builds the singleton on first call (lazy, so DB must be ready).
      - Subsequent calls return the SAME object (halted flag persists).
      - If construction fails, returns _ClosedEngine (can_trade → False always).

    Tests monkeypatch this function to inject a FakeEngine. The _PermissiveEngine
    (always True) must NEVER appear on the production path — only in test helpers.
    """
    global _live_engine_singleton
    if _live_engine_singleton is None and _live_engine_init_error is None:
        _build_live_engine_singleton()
    if _live_engine_singleton is not None:
        return _live_engine_singleton
    # Construction failed — fail CLOSED
    return _ClosedEngine()


def _mode_store() -> ModeStore:
    """Return the production ModeStore. Tests monkeypatch to a fake."""
    return _default_mode_store()


def _intent_store() -> IntentStore:
    """Return the production IntentStore. Tests monkeypatch to a fake."""
    return _default_intent_store()


def _config_store() -> SafetyConfigStore:
    """Return the production SafetyConfigStore. Tests monkeypatch to a fake."""
    return _default_safety_store()


def _session_store() -> SessionStore:
    """Return the production SessionStore. Tests monkeypatch to a fake."""
    return _default_session_store()


# Process-singleton approval queue for the live order page (P1.6/P1.7). It is
# in-memory by design — a pending approval should NOT survive a restart (a stale
# approval must never fire against a stale price). Tests monkeypatch _approval_store.
_APPROVAL_STORE_SINGLETON: Optional["ApprovalStore"] = None


def _approval_store() -> "ApprovalStore":
    """Return the process-wide ApprovalStore singleton (lazily constructed)."""
    global _APPROVAL_STORE_SINGLETON
    if _APPROVAL_STORE_SINGLETON is None:
        from app.live.approval_store import ApprovalStore
        _APPROVAL_STORE_SINGLETON = ApprovalStore()
    return _APPROVAL_STORE_SINGLETON


# "paper" = the Paper page's basket controls (same store/collection, evaluated
# by paper_overall_controls from the LiveExitMonitor cycle — zero broker calls).
_OVERALL_SCOPES = ("overall", "broker_level", "paper")


def _overall_store(scope: str = "overall") -> "OverallSettingsStore":
    """Return the OverallSettingsStore for a scope. Tests monkeypatch this."""
    return _default_overall_store(scope if scope in _OVERALL_SCOPES else "overall")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Arm factory
#
# CHOKEPOINT: _make_arm only places EXIT-ONLY orders:
#   1. The SL backstop is a sell-to-close (trantype='S') protective SL-LMT.
#   2. The session is recorded without any buy.
# ---------------------------------------------------------------------------

#: Default software-guard stop (% premium below entry) when the order carries no
#: explicit stop. A DEEP disaster stop — manual Square + the 10-min cap are the
#: primary near-term protection; the software guard is the catastrophe net that the
#: resting broker SL could never be (it always margin-rejected for an option buyer).
_GUARD_DEFAULT_STOP_PCT = 50.0


def _make_arm(
    client: Any,
    *,
    ref_ltp: float,
    band_pct: float,
    session_store: SessionStore,
    uid: str,
    actid: str,
    levels: Optional[Dict[str, Any]] = None,
) -> Any:
    """Return an async arm(intent, norenordno) callable for use by the executor.

    The arm callable (software-monitored exits — no resting broker SL):
    1. REGISTERS the position with the live software guard (build_monitor_state from
       the order's stop/target/trailing, or a deep default stop). The guard watches
       the live premium and squares through the margin-safe cancel-all-then-close —
       there is NO resting SELL SL (which always margin-rejected for an option-buyer
       account: a resting sell-stop needs full naked-short SPAN margin).
    2. Computes the deadline (fill_time = now; deadline = now + 600s).
    3. Records the session doc via session_store.arm(...). A failure here RAISES
       (so the executor's _abort_protect path runs).
    4. Schedules the 10-minute auto-square (the ultimate backstop).

    Registration is best-effort (a registry failure is logged, never aborts the arm
    — the time-square cap still protects). The session record is mandatory.
    """
    levels = levels or {}

    async def _arm(intent: Any, norenordno: str) -> None:
        now = _utcnow_iso()
        dl = deadline_iso(now)

        # --- Register with the software guard (replaces the doomed resting SL) ---
        try:
            stop_pct = levels.get("stop_pct")
            if stop_pct is None and levels.get("stop_pts") is None:
                stop_pct = _GUARD_DEFAULT_STOP_PCT
            state = build_monitor_state(
                float(ref_ltp),
                stop_pct=stop_pct,
                stop_pts=levels.get("stop_pts"),
                target_pct=levels.get("target_pct"),
                target_pts=levels.get("target_pts"),
                trail=levels.get("trail"),
            )
            _get_live_registry().register(
                key=norenordno,
                tsym=intent.tsym,
                exch=intent.exch,
                qty=intent.qty,
                prd=intent.prd,
                entry_price=float(ref_ltp),
                state=state,
            )
            log.info("arm: registered %s with software guard (stop_pct=%s)", intent.tsym, stop_pct)
        except Exception as exc:
            log.warning("arm: software-guard registration failed: %s (time-cap still protects)", exc)

        # --- Record session (hard failure raises — executor will abort-protect).
        # sl_norenordno is None: no resting broker SL is placed anymore. ---
        await session_store.arm(
            entry_norenordno=norenordno,
            deadline=dl,
            sl_norenordno=None,
            now_iso=now,
        )

        # --- Start background server-timer task (the 10-min backstop) ---
        _schedule_auto_square(
            client=client,
            deadline=dl,
            band_pct=band_pct,
            session_store=session_store,
            uid=uid,
            actid=actid,
        )

    return _arm


# ---------------------------------------------------------------------------
# Background server-timer
#
# After a successful arm, this thin asyncio task waits until the deadline
# (checking at regular intervals) and then calls square_position + reverts mode.
# The logic is delegated to the tested auto_square functions; this is glue only.
# ---------------------------------------------------------------------------

_TIMER_CHECK_INTERVAL = 15  # seconds between deadline checks


def _schedule_auto_square(
    *,
    client: Any,
    deadline: str,
    band_pct: float,
    session_store: SessionStore,
    uid: str,
    actid: str,
) -> None:
    """Schedule the background auto-square task (fire-and-forget asyncio task)."""
    asyncio.create_task(
        _auto_square_task(
            client=client,
            deadline=deadline,
            band_pct=band_pct,
            session_store=session_store,
            uid=uid,
            actid=actid,
        ),
        name="live_auto_square",
    )


async def _check_and_square_if_due(
    *,
    client: Any,
    deadline: str,
    band_pct: float,
    session_store: SessionStore,
    uid: str,
    actid: str,
    now_iso: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Check if the deadline has passed; if so, square the position and revert mode.

    Returns the square result dict if squaring was triggered, else None.

    This helper is called by the background task AND can be called directly in
    tests with an injected past ``now_iso`` to exercise the timer logic without
    real time passing.
    """
    now = now_iso or _utcnow_iso()
    if not auto_square.is_due(deadline, now):
        return None

    # Deadline reached — load session to get position details
    sess = await session_store.get()
    status = sess.get("status")
    if status not in ("armed",):
        # Already squared or killed — nothing to do
        return None

    entry_norenordno = sess.get("entry_norenordno")
    if not entry_norenordno:
        return None

    # Build a minimal position dict for square_position.
    # We only have the norenordno and need to cancel any working order.
    # The actual position (tsym/exch/netqty/lp) must come from the broker.
    try:
        positions = await client.position_book()
    except Exception as exc:
        log.error("auto_square_task: could not fetch positions: %s", exc)
        return None

    # Find the position associated with the entry norenordno (heuristic: first open)
    position = None
    for pos in positions:
        nq = pos.get("netqty", 0)
        try:
            nq_int = int(float(str(nq).replace(",", "")))
        except (TypeError, ValueError):
            nq_int = 0
        if nq_int != 0:
            position = dict(pos)
            position["working_norenordno"] = entry_norenordno
            break

    if position is None:
        # No open position found — already flat
        await session_store.update_status("squared")
        return {"squared": True, "via": "cancel", "note": "no open position at deadline"}

    result = await square_position(
        client,
        position,
        reason="auto_square_deadline",
        band_pct=band_pct,
        uid=uid,
        actid=actid,
        now_iso=now,
    )

    await session_store.update_status("squared")

    # Revert mode to LIVE_OFFLINE
    try:
        ms = _mode_store()
        await ms.revert_to_offline(now_iso=now)
    except Exception as exc:
        log.warning("auto_square: could not revert mode: %s", exc)

    return result


async def _auto_square_task(
    *,
    client: Any,
    deadline: str,
    band_pct: float,
    session_store: SessionStore,
    uid: str,
    actid: str,
) -> None:
    """Background asyncio task: poll until deadline, then square + revert mode."""
    while True:
        await asyncio.sleep(_TIMER_CHECK_INTERVAL)
        result = await _check_and_square_if_due(
            client=client,
            deadline=deadline,
            band_pct=band_pct,
            session_store=session_store,
            uid=uid,
            actid=actid,
        )
        if result is not None:
            log.info("auto_square_task: deadline reached, square result: %s", result)
            break
        # Check if session was squared by another path
        try:
            sess = await session_store.get()
            if sess.get("status") not in ("armed",):
                break
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth / status routes (mirror /upstox/... pattern from broker.py)
# ---------------------------------------------------------------------------

@api.get("/flattrade/status")
async def flattrade_status():
    """Return Flattrade token connection status (never raises; no-token = connected:False)."""
    try:
        return await get_status(DEFAULT_USER_ID)
    except Exception as exc:
        log.exception("flattrade_status failed")
        return {
            "connected": False,
            "expired": False,
            "regenerate_after_6am": False,
            "uid": None,
            "actid": None,
            "static_ip_primary": "",
            "static_ip_secondary": "",
            "configured": is_configured(),
            "error": str(exc)[:200],
        }


@api.get("/flattrade/auth/start")
async def flattrade_auth_start():
    """Return the Flattrade OAuth login URL.

    Returns 400 if FLATTRADE_API_KEY / FLATTRADE_API_SECRET are not set.
    """
    if not is_configured():
        raise HTTPException(
            400,
            "Flattrade credentials not configured. "
            "Set FLATTRADE_API_KEY and FLATTRADE_API_SECRET in backend/.env",
        )
    url = build_login_url()
    return {"login_url": url}


@api.get("/flattrade/auth/callback")
async def flattrade_auth_callback(
    code: Optional[str] = None,
    client: Optional[str] = None,
    error: Optional[str] = None,
):
    """Browser is redirected here by Flattrade after login.

    Flattrade appends the account id as ``&client=<UID>`` to the redirect; the
    /trade/apitoken response itself carries no uid. Resolve the uid from the
    token payload, then the ``client`` query param, then FLATTRADE_USER_ID — so
    the saved token never has a blank uid (which fails SearchScrip with
    "Invalid User Id").
    """
    frontend_url = _FRONTEND_POST_AUTH_URL()
    if error:
        return RedirectResponse(f"{frontend_url}?flattrade_error={error}")
    if not code:
        return RedirectResponse(f"{frontend_url}?flattrade_error=missing_code")
    try:
        payload = await exchange_code_for_token(code)
        jKey = payload.get("token") or payload.get("jKey")
        uid = payload.get("uid") or client or os.environ.get("FLATTRADE_USER_ID", "")
        actid = payload.get("actid") or uid
        if not jKey:
            return RedirectResponse(f"{frontend_url}?flattrade_error=missing_token_in_response")
        await save_token(DEFAULT_USER_ID, jKey=jKey, uid=uid, actid=actid)
        return RedirectResponse(f"{frontend_url}?flattrade_connected=1")
    except Exception as exc:
        log.exception("flattrade token exchange failed")
        return RedirectResponse(f"{frontend_url}?flattrade_error={str(exc)[:200]}")


@api.post("/flattrade/disconnect")
async def flattrade_disconnect():
    """Delete the stored Flattrade token for the default user."""
    deleted = await disconnect(DEFAULT_USER_ID)
    return {"disconnected": deleted}


# ---------------------------------------------------------------------------
# Live-broker data routes (hit the real Flattrade API; require a stored token)
# ---------------------------------------------------------------------------

@api.get("/live-broker/positions")
async def live_broker_positions():
    """Return the broker net position book. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        positions = await client.position_book()
        return {"positions": positions, "count": len(positions)}
    except Exception as exc:
        log.exception("live_broker_positions failed")
        raise HTTPException(400, f"Flattrade position_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/orders")
async def live_broker_orders():
    """Return the broker order book. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        orders = await client.order_book()
        return {"orders": orders, "count": len(orders)}
    except Exception as exc:
        log.exception("live_broker_orders failed")
        raise HTTPException(400, f"Flattrade order_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/trades")
async def live_broker_trades():
    """Return the broker trade book (filled orders). Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        trades = await client.trade_book()
        return {"trades": trades, "count": len(trades)}
    except Exception as exc:
        log.exception("live_broker_trades failed")
        raise HTTPException(400, f"Flattrade trade_book error: {str(exc)[:300]}") from exc


@api.get("/live-broker/limits")
async def live_broker_limits():
    """Return broker account limits / margin. Returns 400 if not connected."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        lims = await client.limits()
        return lims
    except Exception as exc:
        log.exception("live_broker_limits failed")
        raise HTTPException(400, f"Flattrade limits error: {str(exc)[:300]}") from exc


@api.get("/live-broker/margin-probe")
async def live_broker_margin_probe(exch: str, tsym: str, qty: int, prc: float):
    """Read-only NRML margin readback for a prospective option leg.

    Asks the broker (GetOrderMargin) what NRML (prd="M") margin a 1x BUY LMT of
    this exact contract would block, so the operator can confirm it before the
    live readback.

    NRML ONLY: GetOrderMargin's prd enum is C/M/H — there is no MIS "I", so an
    MIS leg would be rejected and is never probed here. M-vs-MIS parity, if
    wanted, is read from Limits (/live-broker/limits), not from this route.

    Returns 400 if not connected (mirrors the other read routes)."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        resp = await client.order_margin(
            exch=exch, tsym=tsym, qty=qty, prc=prc,
            prd="M", trantype="B", prctyp="LMT",
        )
    except Exception as exc:
        log.exception("live_broker_margin_probe failed")
        raise HTTPException(400, f"Flattrade order_margin error: {str(exc)[:300]}") from exc
    return {
        "prd": "M",
        "cash": resp.get("cash"),
        "marginused": resp.get("marginused"),
        "stat": resp.get("stat"),
        "emsg": resp.get("emsg"),
    }


@api.get("/live-broker/reconcile")
async def live_broker_reconcile():
    """Fetch broker orders+positions and return a reconcile diff report."""
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc
    try:
        broker_orders = await client.order_book()
        broker_positions = await client.position_book()
    except Exception as exc:
        log.exception("live_broker_reconcile: broker fetch failed")
        raise HTTPException(400, f"Flattrade fetch error: {str(exc)[:300]}") from exc

    # Feed the app's REAL internal POSITIONS so the diff can actually detect a
    # divergence. Previously internal_positions was [] → reconcile falsely flagged
    # EVERY live broker position as `unknown_broker_position` the moment one
    # existed (and "Reconciled ✓" was a meaningless green when flat).
    #
    # internal_positions = the SOFTWARE GUARD registry (the watched set). A broker
    # position NOT in the registry surfaces as `unknown_broker_position` — the same
    # "exposed but unwatched" signal as the UNGUARDED banner, so the two agree. A
    # watched position at the expected qty reconciles cleanly.
    #
    # internal_orders stays EMPTY *on purpose*. The `live_orders` store is an
    # idempotency LEDGER, not an order-lifecycle tracker: no om-feed daemon runs in
    # this deployment, so a doc written SUBMITTED never advances to COMPLETE.
    # Feeding those as "working" orders would falsely flag every FILLED order as
    # `internal_order_not_at_broker`. With [], reconcile only flags a NON-TERMINAL
    # broker order that has no internal claim — which in the software-guard model
    # (entries fill to COMPLETE, exits are market squares) is genuinely unexpected
    # and worth surfacing. (A lifecycle-maintained order reconcile lives in
    # LiveEngine.reconcile_tick, which halts the engine — not usable for this
    # read-only dashboard chip.)
    internal_positions: List[Dict[str, Any]] = []
    try:
        internal_positions = [
            {"tsym": e.get("tsym"), "qty": e.get("qty", 0)}
            for e in _get_live_registry().snapshot()
        ]
    except Exception as exc:
        log.debug("reconcile: guard registry read failed: %s", exc)

    report = reconcile(
        internal_orders=[],
        internal_positions=internal_positions,
        broker_orders=broker_orders,
        broker_positions=broker_positions,
    )
    return report


@api.get("/live-broker/blotter")
async def live_broker_blotter(limit: int = Query(100, ge=1, le=500)):
    """Deployment-attributed live blotter.

    Joins the ``live_trades`` journal (attribution: deployment / strategy /
    signal / entry / lots) against the live broker position book (the P&L source
    of truth — Noren ``urmtom``/``rpnl``/``lp``). The raw position/order tables
    show what the broker holds; this adds WHICH deployed strategy opened it and
    how that strategy is doing. Read-only; degrades gracefully (attribution still
    shown with null P&L when the broker is unreachable)."""
    from app.db import get_db
    from app.live.live_blotter import build_live_blotter

    db = get_db()
    trades: List[Dict[str, Any]] = []
    try:
        trades = await (
            db.live_trades.find({}, {"_id": 0}).sort("created_at", -1).to_list(length=limit)
        )
    except Exception as exc:
        log.debug("blotter: live_trades fetch failed: %s", exc)

    # Broker position book = P&L truth. Best-effort: a disconnected broker still
    # yields attributed rows (all FLAT, null P&L) rather than a 4xx.
    broker_positions: List[Dict[str, Any]] = []
    try:
        client = await _get_client()
        broker_positions = await client.position_book()
    except Exception as exc:
        log.debug("blotter: position book unavailable: %s", exc)

    # Resolve deployment display names for attribution.
    dep_ids = sorted({str(t.get("deployment_id") or "") for t in trades if t.get("deployment_id")})
    deployments_by_id: Dict[str, Dict[str, Any]] = {}
    if dep_ids:
        try:
            deps = await db.strategy_deployments.find(
                {"id": {"$in": dep_ids}},
                {"_id": 0, "id": 1, "name": 1, "strategy_id": 1, "instrument": 1},
            ).to_list(length=None)
            deployments_by_id = {str(d.get("id")): d for d in deps}
        except Exception as exc:
            log.debug("blotter: deployment name lookup failed: %s", exc)

    rows = build_live_blotter(trades, broker_positions, deployments_by_id)
    return {"rows": rows, "count": len(rows)}


@api.get("/live-broker/trade-stats")
async def live_trade_stats():
    """Aggregate statistics over the journaled live_trades for analysis:
    lifetime/today/week/month realized P&L, win rate, profit factor, and a
    per-strategy/deployment breakdown. Reuses the proven paper aggregators —
    live_trades docs share the same field contract (status / realized_pnl /
    closed_at / strategy_id / deployment_id). Read-only, no broker calls."""
    from app import paper_analytics
    from app.db import get_db

    db = get_db()
    rows = await db.live_trades.find(
        {}, {"_id": 0, "status": 1, "realized_pnl": 1, "unrealized_pnl": 1,
             "closed_at": 1, "updated_at": 1, "created_at": 1,
             "strategy_id": 1, "deployment_id": 1, "exit_reason": 1,
             "risk_amount": 1, "total_charges": 1},
    ).to_list(length=100000)
    closed = [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]
    stats = paper_analytics.per_strategy_stats(rows)
    dep_ids = sorted({str(s.get("deployment_id")) for s in stats if s.get("deployment_id")})
    if dep_ids:
        deps = await db.strategy_deployments.find(
            {"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1},
        ).to_list(length=len(dep_ids))
        names = {str(d["id"]): str(d.get("name") or "") for d in deps}
        for s in stats:
            s["deployment_name"] = names.get(str(s.get("deployment_id") or ""), "")
    return paper_analytics.json_safe_floats({
        "period_pnl": paper_analytics.period_pnl(closed),
        "per_strategy": stats,
        "trade_count": len(rows),
        "closed_count": len(closed),
    })


@api.get("/live-broker/trade-history")
async def live_trade_history(limit: int = Query(100, ge=1, le=500),
                             skip: int = Query(0, ge=0),
                             status: Optional[str] = Query(None, description="OPEN or CLOSED")):
    """Paginated journaled live-trade history (raw live_trades docs, newest
    first — the same records the Flattrade close-loop finalizes). Unlike the
    blotter this returns the FULL close fields (closed_at, exit_price,
    exit_reason, realized_pnl) for analysis. Read-only, no broker calls.
    exit_price/realized_pnl may be None on CLOSED docs (never fabricated)."""
    from app.db import get_db

    db = get_db()
    q: Dict[str, Any] = {}
    s = str(status or "").strip().upper()
    if s in ("OPEN", "CLOSED"):
        q["status"] = s
    total = await db.live_trades.count_documents(q)
    rows = await (
        db.live_trades.find(q, {"_id": 0})
        .sort("created_at", -1).skip(skip).to_list(length=limit)
    )
    dep_ids = sorted({str(r.get("deployment_id") or "") for r in rows if r.get("deployment_id")})
    names: Dict[str, str] = {}
    if dep_ids:
        deps = await db.strategy_deployments.find(
            {"id": {"$in": dep_ids}}, {"_id": 0, "id": 1, "name": 1},
        ).to_list(length=len(dep_ids))
        names = {str(d["id"]): str(d.get("name") or "") for d in deps}
    for r in rows:
        r["deployment_name"] = names.get(str(r.get("deployment_id") or ""), "")
    return {"items": rows, "count": len(rows), "total": total,
            "skip": skip, "limit": limit}


@api.get("/live-broker/symbol/resolve")
async def live_broker_symbol_resolve(
    underlying: str = Query(..., description="e.g. NIFTY, BANKNIFTY, SENSEX"),
    strike: float = Query(..., description="Strike price, e.g. 25000"),
    side: str = Query(..., description="CE or PE"),
    expiry: str = Query(..., description="ISO date YYYY-MM-DD"),
    lot_size: Optional[int] = Query(None, description="Expected lot size; auto-filled from spec if omitted"),
):
    """Preview Noren symbol resolution for a given option contract."""
    from app.live.flattrade_symbol import UNDERLYING_SPEC

    if lot_size is None:
        spec = UNDERLYING_SPEC.get(underlying.strip().upper())
        if spec is None:
            raise HTTPException(
                400,
                f"Unknown underlying {underlying!r}. Supported: {sorted(UNDERLYING_SPEC)}",
            )
        lot_size = spec[2]

    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc

    contract = {
        "underlying": underlying,
        "strike": strike,
        "side": side,
        "expiry_date": expiry,
        "lot_size": lot_size,
    }

    import asyncio

    try:
        underlying_upper = underlying.strip().upper()
        from app.live.flattrade_symbol import UNDERLYING_SPEC as _SPEC
        if underlying_upper not in _SPEC:
            raise HTTPException(400, f"Unknown underlying {underlying!r}")
        exch = _SPEC[underlying_upper][0]
        strike_val = float(strike)
        query = (
            f"{underlying_upper} {int(strike_val)}"
            if strike_val == int(strike_val)
            else f"{underlying_upper} {strike_val}"
        )
        scrip_rows = await client.search_scrip(exch, query)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"SearchScrip error: {str(exc)[:300]}") from exc

    def _sync_search(exch: str, q: str):
        return scrip_rows

    try:
        result = resolve(contract, search_fn=_sync_search)
        return result
    except SymbolResolutionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("symbol resolve unexpected error")
        raise HTTPException(400, f"Symbol resolution error: {str(exc)[:300]}") from exc


# ---------------------------------------------------------------------------
# Dry-run route (L1.3) — NEVER transmits
# ---------------------------------------------------------------------------

class _DryRunBody(BaseModel):
    contract: Dict[str, Any]
    side: str
    order_kind: str
    lots: int
    ref_ltp: float
    band_pct: float
    fat_finger_cap: int
    levels: Dict[str, Any] = {}
    buffer_pct: Optional[float] = None


@api.post("/live-broker/order/dry-run")
async def live_order_dry_run(body: _DryRunBody):
    """Build an OrderIntent and run all safety checks WITHOUT placing any order."""
    from app.live.idempotency import new_client_order_id
    from app.live.order_builder import build_intent
    from app.live.flattrade_symbol import UNDERLYING_SPEC
    from app.live.margin import margin_verdict

    cid = new_client_order_id()

    underlying = str(body.contract.get("underlying") or "").strip().upper()
    strike = body.contract.get("strike")

    pre_fetched_rows: List[Dict[str, Any]] = []
    fetch_error: Optional[str] = None

    try:
        client = await _get_client()
        spec = UNDERLYING_SPEC.get(underlying)
        if spec is not None and strike is not None:
            exch = spec[0]
            strike_val = float(strike)
            query = (
                f"{underlying} {int(strike_val)}"
                if strike_val == int(strike_val)
                else f"{underlying} {strike_val}"
            )
            pre_fetched_rows = await client.search_scrip(exch, query)
    except HTTPException:
        fetch_error = "Flattrade not connected; symbol resolution will fail"
    except Exception as exc:
        fetch_error = f"SearchScrip error: {str(exc)[:200]}"

    def _sync_search(exch: str, q: str) -> List[Dict[str, Any]]:
        return pre_fetched_rows

    intent, verdicts, resolved_lot = build_intent(
        body.contract,
        side=body.side,
        order_kind=body.order_kind,
        lots=body.lots,
        ref_ltp=body.ref_ltp,
        band_pct=body.band_pct,
        fat_finger_cap=body.fat_finger_cap,
        levels=body.levels,
        client_order_id=cid,
        buffer_pct=body.buffer_pct,
        search_fn=_sync_search,
    )

    if fetch_error and verdicts and verdicts[0]["check"] == "symbol" and not verdicts[0]["ok"]:
        verdicts[0]["detail"] = f"{fetch_error}; {verdicts[0]['detail']}"

    would_send: Optional[Dict[str, Any]] = None
    if intent is not None:
        uid = ""
        actid = ""
        try:
            doc = await _get_token_doc()
            uid = doc.get("uid", "")
            actid = doc.get("actid", uid)
        except HTTPException:
            pass
        would_send = intent.to_jdata(uid=uid, actid=actid)

    return {
        "would_send": would_send,
        "verdicts": verdicts,
        "client_order_id": cid,
        "lot_size": resolved_lot,  # authoritative broker lot size (None if resolution failed)
    }


# ---------------------------------------------------------------------------
# Live order page routes (P1.7) — exchange-aware preview + approval-gated place
#
# These expose the choke-point (order_builder.validate_and_build) and the
# per-trade approval queue (approval_store) to the UI. The ACTUAL real-order
# placement still flows through the SINGLE executor chokepoint (live_order_place
# → executor.place_live_test_order); the one-shot approval token is an added gate
# in FRONT of it, not a second entry path.
# ---------------------------------------------------------------------------

class _OrderTicketBody(BaseModel):
    underlying: str
    strike: float
    option_type: str = "CE"          # CE / PE
    side: str = "B"                  # B / S (Noren trantype)
    expiry_date: str                 # ISO YYYY-MM-DD
    lots: int = 1
    order_type: str = "LIMIT"        # LIMIT / MARKET / SL-LMT
    product: str = "MIS"             # MIS / NRML
    ref_ltp: Optional[float] = None
    band_pct: float = 5.0
    fat_finger_cap: int = 1
    levels: Dict[str, Any] = {}
    buffer_pct: Optional[float] = None


async def _validate_order_ticket(body: "_OrderTicketBody") -> Dict[str, Any]:
    """Pre-fetch scrip rows then run validate_and_build (the choke-point).

    Mirrors the dry-run route's async→sync search adapter. Returns the built
    children (OrderIntent objects or None), the verdicts, the JSON-safe jdata
    preview, and the minted client_order_id.
    """
    from app.live.order_builder import validate_and_build
    from app.live.flattrade_symbol import UNDERLYING_SPEC
    from app.live.idempotency import new_client_order_id

    cid = new_client_order_id()
    underlying = str(body.underlying or "").strip().upper()
    strike = body.strike

    pre_fetched_rows: List[Dict[str, Any]] = []
    fetch_error: Optional[str] = None
    client = None
    try:
        client = await _get_client()
        spec = UNDERLYING_SPEC.get(underlying)
        if spec is not None and strike is not None:
            exch = spec[0]
            sv = float(strike)
            query = f"{underlying} {int(sv)}" if sv == int(sv) else f"{underlying} {sv}"
            pre_fetched_rows = await client.search_scrip(exch, query)
    except HTTPException:
        fetch_error = "Flattrade not connected; symbol resolution will fail"
    except Exception as exc:
        fetch_error = f"SearchScrip error: {str(exc)[:200]}"

    def _sync_search(exch_: str, q: str) -> List[Dict[str, Any]]:
        return pre_fetched_rows

    ticket = {
        "underlying": underlying,
        "strike": strike,
        "option_type": body.option_type,
        "side": body.side,
        "expiry_date": body.expiry_date,
        "lots": body.lots,
        "order_type": body.order_type,
        "product": body.product,
        "ref_ltp": body.ref_ltp,
        "band_pct": body.band_pct,
        "fat_finger_cap": body.fat_finger_cap,
        "levels": body.levels,
        "client_order_id": cid,
        "buffer_pct": body.buffer_pct,
        "search_fn": _sync_search,
    }
    children, verdicts = validate_and_build(ticket)

    if fetch_error and children is None:
        for v in verdicts:
            if v.get("check") == "symbol" and not v.get("ok"):
                v["detail"] = f"{fetch_error}; {v['detail']}"

    # ------------------------------------------------------------------
    # Margin pre-check — runs after validate_and_build so we have the
    # resolved lot_size (total qty / lots).  Skipped if validation already
    # failed (children is None) or if ref_ltp is absent (MARKET orders).
    # Uses the same client that was built for search_scrip above.
    # ------------------------------------------------------------------
    if children is not None and body.ref_ltp is not None and client is not None:
        from app.live.margin import margin_verdict as _margin_verdict
        try:
            total_qty = sum(c.qty for c in children)
            resolved_lot_size = total_qty // (body.lots or 1)
            limits_data = await client.limits()
            mv = _margin_verdict(limits_data, ref_ltp=body.ref_ltp, lot_size=resolved_lot_size)
            verdicts.append(mv)
            if not mv["ok"]:
                children = None
        except Exception as exc:
            verdicts.append({"check": "margin", "ok": False, "detail": f"margin pre-check error: {exc}"})
            children = None

    uid = actid = ""
    try:
        doc = await _get_token_doc()
        uid = doc.get("uid", "")
        actid = doc.get("actid", uid)
    except HTTPException:
        pass
    would_send = [c.to_jdata(uid=uid, actid=actid) for c in (children or [])]

    return {"children": children, "verdicts": verdicts, "would_send": would_send, "cid": cid}


@api.get("/live-broker/order-rules/{underlying}")
async def live_order_rules(underlying: str):
    """Exchange rules for the UI (products/order-types/freeze/tick/lot/expiry)."""
    from app.live.flattrade_symbol import rules_for

    rules = rules_for(underlying)
    if rules is None:
        raise HTTPException(404, f"Unknown underlying {underlying!r}")
    return rules


@api.post("/live-broker/order/preview")
async def live_order_preview(body: _OrderTicketBody):
    """Run the choke-point as a DRY-RUN — exchange/tick/freeze/order-type checks
    with NO placement. Returns the would-send child jdata + full verdicts."""
    res = await _validate_order_ticket(body)
    return {
        "ok": res["children"] is not None,
        "children": res["would_send"],
        "verdicts": res["verdicts"],
        "client_order_id": res["cid"],
    }


@api.post("/live-broker/order/approvals")
async def live_order_create_approval(body: _OrderTicketBody):
    """Validate the ticket and, if it passes, QUEUE it for explicit approval.

    Returns the one-shot token (surfaced once) so the operator can redeem it via
    the approve route. A ticket that fails validation is NOT queued."""
    res = await _validate_order_ticket(body)
    if res["children"] is None:
        return {"ok": False, "verdicts": res["verdicts"]}

    summary = {
        "underlying": str(body.underlying).upper(),
        "strike": body.strike,
        "option_type": body.option_type,
        "side": body.side,
        "order_type": body.order_type,
        "product": body.product,
        "lots": body.lots,
        "ref_ltp": body.ref_ltp,
        "child_count": len(res["would_send"]),
        "would_send": res["would_send"],
    }
    payload = {"ticket": body.dict(), "would_send": res["would_send"]}
    rec = _approval_store().create(payload=payload, summary=summary, now_iso=_utcnow_iso())
    return {
        "ok": True,
        "approval_id": rec["approval_id"],
        "token": rec["token"],
        "summary": rec["summary"],
        "verdicts": res["verdicts"],
    }


@api.get("/live-broker/order/approvals")
async def live_order_list_approvals():
    """List currently-pending approvals (JSON-safe; never exposes the token)."""
    pending = _approval_store().list_pending(_utcnow_iso())
    return {
        "pending": [
            {
                "approval_id": p["approval_id"],
                "status": p["status"],
                "summary": p["summary"],
                "created_at": p["created_at"],
            }
            for p in pending
        ]
    }


class _ApproveBody(BaseModel):
    token: str


@api.post("/live-broker/order/approvals/{approval_id}/approve")
async def live_order_approve(approval_id: str, body: _ApproveBody):
    """Redeem the one-shot token and place the approved entry.

    The token gate sits in FRONT of the existing single executor chokepoint
    (live_order_place → executor.place_live_test_order): a bad/expired/replayed
    token NEVER reaches the executor. On a successful place the approval is marked
    CONSUMED (terminal — it can never be re-placed). On ANY non-placement after the
    token is redeemed (BUY-only, mode not armed, broker reject), the approval is
    REVERTED to pending so it stays in the queue and the operator can fix the cause
    and retry (or reject) with the same token — never a stranded, vanished order."""
    store = _approval_store()
    now = _utcnow_iso()
    res = store.approve(approval_id, body.token, now)
    if not res["ok"]:
        # bad_token / expired / not pending / not_found → record unchanged
        # (bad_token leaves it pending; expired/not-pending are already terminal).
        # The executor is NOT called and nothing is stranded.
        return {"placed": False, "approval_id": approval_id, "reason": res["reason"]}

    # Token is now redeemed (status=approved). EVERY path below that does not end in
    # a confirmed placement MUST revert to pending so the approval is never stranded.
    def _not_placed(reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        store.revert_to_pending(approval_id, now)
        out = {"placed": False, "approval_id": approval_id, "reason": reason, "retryable": True}
        if extra:
            out.update(extra)
        return out

    ticket = (res.get("payload") or {}).get("ticket") or {}
    # The supervised executor places a 1-lot LONG entry; SELL/multi-lot/MARKET are
    # preview-only for now (the approval still validated the full exchange rules).
    if str(ticket.get("side")) != "B":
        return _not_placed(
            "automated placement supports BUY entries only — Reject this order and "
            "place a SELL/exit manually via the square route"
        )

    contract = {
        "underlying": ticket.get("underlying"),
        "strike": ticket.get("strike"),
        "side": ticket.get("option_type"),   # CE/PE leg for resolution
        "expiry_date": ticket.get("expiry_date"),
    }
    place_body = _PlaceBody(
        contract=contract,
        side="B",
        ref_ltp=float(ticket.get("ref_ltp") or 0.0),
        band_pct=float(ticket.get("band_pct") or 5.0),
        levels=ticket.get("levels") or {},
    )
    try:
        result = await live_order_place(place_body)
    except HTTPException as exc:
        return _not_placed(f"placement blocked: {exc.detail}")

    if isinstance(result, dict) and result.get("placed"):
        store.mark_consumed(approval_id, now)
        return {"approval_id": approval_id, **result}
    # Executor returned without placing (halt / margin / broker reject) → revert so
    # the operator can retry; preserve the executor's reason/verdicts for the UI.
    extra = result if isinstance(result, dict) else {"result": result}
    reason = (extra.get("reason") if isinstance(result, dict) else None) or "not placed"
    return _not_placed(reason, extra={k: v for k, v in extra.items() if k != "placed"})


@api.post("/live-broker/order/approvals/{approval_id}/reject")
async def live_order_reject(approval_id: str):
    """Operator declines a pending approval (it can never be placed afterwards)."""
    res = _approval_store().reject(approval_id, _utcnow_iso())
    return {"ok": res["ok"], "approval_id": approval_id, "reason": res.get("reason")}


# ---------------------------------------------------------------------------
# Overall / broker-level controls (Phase 2) — config persistence for the
# basket-level SL / target / trailing / re-entry engine (overall_controls.py).
# scope = "overall" (per-deployment defaults) | "broker_level" (across all live,
# re-armed daily). The evaluation runs in the exit engine; these routes are CRUD.
# ---------------------------------------------------------------------------

def _norm_scope(scope: Optional[str]) -> str:
    s = str(scope or "overall").strip().lower()
    return s if s in _OVERALL_SCOPES else "overall"


@api.get("/live-broker/overall-settings")
async def get_overall_settings(scope: str = Query("overall")):
    """Return the overall-controls config for a scope (merged with defaults)."""
    try:
        return await _overall_store(_norm_scope(scope)).get_config()
    except Exception as exc:
        log.warning("get_overall_settings failed: %s", exc)
        from app.live.overall_settings_store import DEFAULT_OVERALL_CONFIG
        return dict(DEFAULT_OVERALL_CONFIG)


class _OverallSettingsBody(BaseModel):
    config: Dict[str, Any]


@api.put("/live-broker/overall-settings")
async def put_overall_settings(body: _OverallSettingsBody, scope: str = Query("overall")):
    """Persist a validated overall-controls config. Fail-closed: a config that
    the store rejects (bad mode / unknown key / reentry.max>5) returns 400."""
    try:
        return await _overall_store(_norm_scope(scope)).put_config(body.config or {})
    except ValueError as exc:
        raise HTTPException(400, f"invalid overall-controls config: {exc}") from exc
    except Exception as exc:
        log.exception("put_overall_settings failed")
        raise HTTPException(400, f"could not save overall settings: {str(exc)[:200]}") from exc


# ---------------------------------------------------------------------------
# GTT / OCO disaster-backstop (Phase 3). NRML-only PC-died net.
#
# Schema CONFIRMED against the Flattrade PiConnect PDF (ch.1.13–1.20, 2026-06-25)
# and wired through FlattradeClient.{gtt_book,place_gtt,place_oco,cancel_gtt,
# cancel_oco}. Two shapes:
#   kind="oco"  → bracket (SL leg + TP leg); ai_t is the DOCUMENTED "LMT_BOS_O".
#   kind="gtt"  → single leg; ai_t (direction) is REQUIRED from the caller — the
#                 PDF does not document the LTP_A/LTP_B direction suffix, so the
#                 UI must choose it explicitly and confirm by reading one back.
# place is gated behind an explicit transmit=true (preview otherwise); cancel +
# read are always live (they can only remove a resting order / read the book).
# ---------------------------------------------------------------------------

@api.get("/live-broker/gtt")
async def list_gtt():
    """List the broker GTT/OCO book (GetPendingGTTOrder). Best-effort: returns an
    empty list + a human note if not connected."""
    gtts: List[Dict[str, Any]] = []
    note = None
    try:
        client = await _get_client()
        raw = await client.gtt_book()
        gtts = raw if isinstance(raw, list) else []
    except HTTPException:
        note = "not connected"
    except Exception as exc:
        note = f"gtt read error: {str(exc)[:160]}"
    return {"gtt": gtts, "note": note}


class _GttBody(BaseModel):
    kind: str = "oco"                       # "oco" (SL+TP bracket) | "gtt" (single)
    exch: str
    tsym: str
    qty: int
    prd: str = "M"                          # NRML only
    remarks: Optional[str] = None
    transmit: bool = False                  # preview unless explicitly transmitted
    # --- OCO bracket fields ---
    sl_trigger: Optional[float] = None
    sl_limit: Optional[float] = None
    tp_trigger: Optional[float] = None
    tp_limit: Optional[float] = None
    # --- single-GTT fields ---
    trantype: str = "S"
    ai_t: Optional[str] = None              # REQUIRED for kind="gtt" (direction)
    d_trigger: Optional[float] = None
    prc_limit: Optional[float] = None


def _build_gtt_or_oco(body: "_GttBody") -> tuple[str, Optional[Dict[str, Any]]]:
    """Return (kind, intent) from a _GttBody, building via the right gtt.py builder.
    intent is None when the builder rejects (NRML/tick/qty/ai_t validation)."""
    kind = (body.kind or "oco").strip().lower()
    if kind == "oco":
        return kind, _gtt_mod.build_oco_intent(
            exch=body.exch, tsym=body.tsym, qty=body.qty, prd=body.prd,
            sl_trigger=body.sl_trigger, sl_limit=body.sl_limit,
            tp_trigger=body.tp_trigger, tp_limit=body.tp_limit,
            trantype=body.trantype, remarks=body.remarks,
        )
    if kind == "gtt":
        if not (isinstance(body.ai_t, str) and body.ai_t.strip()):
            return kind, None
        return kind, _gtt_mod.build_gtt_intent(
            exch=body.exch, tsym=body.tsym, qty=body.qty, trantype=body.trantype,
            ai_t=body.ai_t, d_trigger=body.d_trigger, prc_limit=body.prc_limit,
            prd=body.prd, remarks=body.remarks,
        )
    return kind, None


@api.post("/live-broker/gtt")
async def place_gtt(body: _GttBody):
    """Build (and, when transmit=true, transmit) a GTT/OCO backstop.

    NRML-only, tick-rounded, fail-closed. With transmit=false (default) it
    returns the built intent for preview WITHOUT any broker call. With
    transmit=true it sends it via the confirmed PiConnect endpoint and returns
    the broker's alert id."""
    kind, intent = _build_gtt_or_oco(body)
    if kind not in ("oco", "gtt"):
        raise HTTPException(400, f"unknown GTT kind {kind!r} (use 'oco' or 'gtt')")
    if intent is None:
        detail = ("invalid GTT (NRML/prd=M only, tick-valid prices, qty>0"
                  + (", ai_t required for single GTT" if kind == "gtt" else "") + ")")
        raise HTTPException(400, detail)

    if not body.transmit:
        return {
            "placed": False,
            "preview": True,
            "kind": kind,
            "intent": intent,
            "note": "preview only — set transmit=true to send this to the broker",
        }

    # Real transmit (explicit). NRML resting backstop blocks no margin.
    try:
        client = await _get_client()
    except HTTPException:
        raise
    res = await (client.place_oco(intent) if kind == "oco" else client.place_gtt(intent))
    return {"placed": bool(res.get("ok")), "kind": kind, "intent": intent, "result": res}


@api.delete("/live-broker/gtt/{al_id}")
async def cancel_gtt(al_id: str, kind: str = "gtt"):
    """Cancel a GTT/OCO by alert id (live — only removes a resting order).
    kind query param routes to CancelGTTOrder ('gtt') or CancelOCOOrder ('oco')."""
    try:
        _gtt_mod.cancel_gtt_jdata(al_id)   # validate id fail-closed before any call
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    client = await _get_client()
    res = await (client.cancel_oco(al_id) if kind.strip().lower() == "oco"
                 else client.cancel_gtt(al_id))
    return {"canceled": bool(res.get("ok")), "kind": kind, "result": res}


# ---------------------------------------------------------------------------
# Software exit-guard status — what the LivePositionGuard is watching + whether
# it's armed (offline-first: dry-run logs intended squares until LIVE_GUARD_ARMED=1).
# ---------------------------------------------------------------------------

@api.get("/live-broker/guard-status")
async def guard_status():
    """Report the software exit guard's state: armed/dry-run + each guarded
    position's entry, stop/target levels, peak, and fill status."""
    import os
    reg = _get_live_registry()
    armed = os.environ.get("LIVE_GUARD_ARMED", "0").strip().lower() in ("1", "true", "yes", "on")
    guarded = []
    for e in reg.snapshot():
        st = e.get("state") or {}
        guarded.append({
            "tsym": e.get("tsym"),
            "qty": e.get("qty"),
            "entry_price": e.get("entry_price"),
            "stop_level": st.get("stop_level"),
            "target_level": st.get("target_level"),
            "peak": st.get("peak"),
            "seen_filled": e.get("seen_filled"),
        })
    return {
        "armed": armed,
        "mode": "ARMED — transmits real squares" if armed else "dry-run — logs intended squares, no transmit",
        "count": len(reg),
        "guarded": guarded,
    }


# ---------------------------------------------------------------------------
# Portfolio Greeks (net Δ / net Θ) — read-only observability
#
# Contract metadata is static per tsym — resolve once via SearchScrip, then reuse.
# ---------------------------------------------------------------------------

_greeks_contract_cache: dict = {}

_GREEKS_EMPTY = {
    "net_delta_rupees_per_point": 0.0, "net_theta_rupees_per_day": 0.0,
    "n_computed": 0, "n_skipped": 0, "positions": [],
}


async def _resolve_greeks_client():
    """Resolve a broker client for the (read-only) Greeks route, fail-soft.

    `_get_client` is async in production (raises HTTPException when not connected)
    but tests monkeypatch it with a sync lambda returning a MockNoren or None. We
    support both: call it, await the result if it is awaitable, and treat any
    error / None as "not connected" (the caller returns zeros — never a 500).
    """
    import inspect
    try:
        client = _get_client()
        if inspect.isawaitable(client):
            client = await client
        return client
    except Exception:
        return None


@api.get("/live-broker/greeks")
async def live_broker_greeks():
    """Portfolio net-Δ (₹/index point) + net-Θ (₹/day) across live positions.

    Fail-soft: not connected / no positions → zeros. General API (40/s); never on
    the guard hot path. IV solved from the GetQuotes premium (no market IV exists).
    """
    from datetime import date as _date

    client = await _resolve_greeks_client()
    if client is None:
        return dict(_GREEKS_EMPTY)
    positions = _get_live_registry().snapshot()
    if not positions:
        return dict(_GREEKS_EMPTY)

    async def _resolve(tsym: str, exch: str):
        if tsym in _greeks_contract_cache:
            return _greeks_contract_cache[tsym]
        m = re.match(r"[A-Za-z]+", tsym)
        underlying = m.group(0) if m else tsym
        # Full tsym first (most specific), then the underlying prefix (broad,
        # matches the proven SearchScrip query idiom) — exact-tsym-filtered in both,
        # so we never accept the wrong contract; this only widens query coverage.
        queries = [tsym] if tsym == underlying else [tsym, underlying]
        for q in queries:
            try:
                rows = await client.search_scrip(exch, q)
            except Exception:
                rows = []
            for r in rows or []:
                if str(r.get("tsym")) == str(tsym):
                    try:
                        strike = _strike_from_dname(str(r.get("dname", "")))
                        expiry_iso = _parse_exd(str(r.get("exd", "")))
                    except SymbolResolutionError:
                        return None
                    token = str(r.get("token") or "")
                    if not token:
                        return None
                    out = (strike, expiry_iso, str(r.get("optt", "")).upper() == "CE", token)
                    _greeks_contract_cache[tsym] = out
                    return out
        log.info("greeks: could not resolve contract for tsym=%s exch=%s (skipped)", tsym, exch)
        return None

    try:
        return await compute_portfolio_greeks(
            positions,
            get_quote_fn=client.get_quotes,
            resolve_contract_fn=_resolve,
            today=_date.today(),
        )
    except Exception:
        return dict(_GREEKS_EMPTY)


# ---------------------------------------------------------------------------
# Safety config routes (L2.2)
# ---------------------------------------------------------------------------

@api.get("/live-broker/safety-config")
async def get_safety_config():
    """Return the current live-trading safety guardrails config."""
    store = _config_store()
    return await store.get_config()


class _SafetyConfigBody(BaseModel):
    daily_loss_limit: Optional[float] = None
    profit_lock_target: Optional[float] = None
    max_open_positions: Optional[int] = None
    # Account-level per-order lot ceiling (default 20 in SafetyConfigStore). The
    # store validates it (positive int, bool rejected); put_config flows it through.
    max_lots_per_order: Optional[int] = None


@api.put("/live-broker/safety-config")
async def put_safety_config(body: _SafetyConfigBody):
    """Update live-trading safety guardrails (numeric thresholds only)."""
    store = _config_store()
    updates: Dict[str, Any] = {
        k: v
        for k, v in body.dict().items()
        if v is not None
    }
    if not updates:
        return await store.get_config()
    try:
        return await store.put_config(updates)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/live-broker/safety-config/reset-latch")
async def reset_safety_latch():
    """Explicitly reset the broker-stop-loss latch."""
    store = _config_store()
    return await store.reset()


# ---------------------------------------------------------------------------
# Unified arm-state — the single "will a signal place a REAL order right now?"
# verdict, collapsing mode / per-deployment arm / the two env gates / connectivity.
# ---------------------------------------------------------------------------

@api.get("/live-broker/arm-state")
async def get_arm_state():
    """Return the one execution-state verdict the UI renders unambiguously.

    Read-only; never raises (every input is best-effort with a safe fallback)."""
    import os
    from app.live.arm_state import compute_arm_state
    from app.live.mode import is_deployment_live_allowed

    # mode singleton
    try:
        mode_doc = await _mode_store().get()
    except Exception:
        mode_doc = None
    # broker connectivity (a token is stored)
    connected = False
    try:
        await _get_token_doc()
        connected = True
    except Exception:
        connected = False
    # offline-first env gates
    def _env(name: str) -> bool:
        return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")
    autoplace_armed = _env("LIVE_AUTOPLACE_ARMED")
    guard_armed = _env("LIVE_GUARD_ARMED")
    # deployments armed-and-in-window for live
    armed_n = 0
    try:
        from app.db import get_db
        now = datetime.now(timezone.utc)
        cur = get_db().strategy_deployments.find({"risk.live.armed": True}, {"_id": 0, "id": 1, "risk": 1})
        for dep in await cur.to_list(length=500):
            ok, _reason = is_deployment_live_allowed(dep, now, connected=connected)
            if ok:
                armed_n += 1
    except Exception as exc:
        log.debug("arm-state: deployment scan failed: %s", exc)
    return compute_arm_state(
        mode_doc=mode_doc, connected=connected,
        autoplace_armed=autoplace_armed, guard_armed=guard_armed,
        armed_deployment_count=armed_n,
    )


# ---------------------------------------------------------------------------
# L3: Mode routes
# ---------------------------------------------------------------------------

class _ModePutBody(BaseModel):
    mode: _Literal["PAPER", "LIVE_OFFLINE", "LIVE_TEST"]
    confirm: StrictBool = False


@api.get("/live-broker/mode")
async def get_mode():
    """Return the current mode doc (mode, single_shot_consumed, since, ...)."""
    ms = _mode_store()
    return await ms.get()


@api.put("/live-broker/mode")
async def put_mode(body: _ModePutBody):
    """Transition the trading mode.

    Guards:
    - LIVE_ARMED → 400 (L4 feature, not available in L3)
    - LIVE_TEST without confirm=True → 400
    - LIVE_TEST without a connected broker token → 400
    - LIVE_TEST with engine.can_trade() == False → 400
    """
    ms = _mode_store()

    # Determine connected status (True if a token is stored)
    connected = False
    try:
        await _get_token_doc()
        connected = True
    except HTTPException:
        connected = False

    # Determine can_trade (True if engine permits and not halted).
    # _l3_engine() always returns an engine (real or fail-closed _ClosedEngine);
    # it never returns None, so no permissive fallback is needed.
    can_trade = False
    try:
        engine = _l3_engine()
        ok, _ = await engine.can_trade()
        can_trade = ok
    except Exception:
        can_trade = False

    try:
        result = await ms.set_mode(
            body.mode,
            confirm=body.confirm,
            connected=connected,
            can_trade=can_trade,
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# L3: Order place route — THE ONLY ENTRY CHOKEPOINT
#
# ENTRY place_order reachable ONLY through executor.place_live_test_order.
# All other place_order / cancel_order calls in this file are EXIT-ONLY.
# ---------------------------------------------------------------------------

class _PlaceBody(BaseModel):
    contract: Dict[str, Any]
    side: _Literal["B"] = "B"
    ref_ltp: float
    band_pct: float = 5.0
    levels: Dict[str, Any] = {}


@api.post("/live-broker/order/place")
async def live_order_place(body: _PlaceBody):
    """Place one real option order through ALL safety gates.

    This is the ONLY route that can cause an ENTRY order to be placed.
    The executor is the single entry chokepoint — no other code path in
    this router reaches client.place_order for a buy entry.

    Requires LIVE_TEST mode with an unconsumed single-shot.
    Returns {placed, protected, norenordno, cid, verdicts} on success.
    """
    from app.live.flattrade_symbol import UNDERLYING_SPEC

    # Validate the underlying is in our allow-list (the executor no longer needs lot_size;
    # it comes from the resolved broker scrip ls via build_intent).
    underlying = str(body.contract.get("underlying") or "").strip().upper()
    spec = UNDERLYING_SPEC.get(underlying)
    if spec is None:
        raise HTTPException(400, f"Unknown underlying {underlying!r}. Supported: {sorted(UNDERLYING_SPEC)}")

    # Get the broker client for order operations
    try:
        client = await _get_client()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not build Flattrade client: {exc}") from exc

    # Get uid/actid for to_jdata calls
    try:
        token_doc = await _get_token_doc()
        uid = token_doc.get("uid", "")
        actid = token_doc.get("actid", uid)
    except HTTPException:
        uid = ""
        actid = ""

    # Build async→sync search_fn adapter (same pattern as /symbol/resolve)
    exch = spec[0]
    strike = body.contract.get("strike")
    pre_fetched_rows: List[Dict[str, Any]] = []
    if strike is not None:
        try:
            strike_val = float(strike)
            query = (
                f"{underlying} {int(strike_val)}"
                if strike_val == int(strike_val)
                else f"{underlying} {strike_val}"
            )
            pre_fetched_rows = await client.search_scrip(exch, query)
        except Exception as exc:
            raise HTTPException(400, f"SearchScrip error: {str(exc)[:300]}") from exc

    def _sync_search(exch_: str, q: str) -> List[Dict[str, Any]]:
        return pre_fetched_rows

    ms = _mode_store()
    is_ = _intent_store()
    engine = _l3_engine()
    ss = _session_store()

    # Build the arm callable (exit-only: SL backstop + session record)
    arm = _make_arm(
        client,
        ref_ltp=body.ref_ltp,
        band_pct=body.band_pct,
        session_store=ss,
        uid=uid,
        actid=actid,
        levels=body.levels,  # software-guard stop/target/trailing (falls back to a deep default)
    )

    # _l3_engine() always returns a real engine or _ClosedEngine (fail-closed).
    # The old _PermissiveEngine fallback (can_trade always True) has been removed —
    # it would bypass the halt/latch gate in production.

    result = await _executor_mod.place_live_test_order(
        body.contract,
        side=body.side,
        ref_ltp=body.ref_ltp,
        band_pct=body.band_pct,
        levels=body.levels,
        client=client,
        mode_store=ms,
        intent_store=is_,
        engine=engine,
        search_fn=_sync_search,
        arm=arm,
        fat_finger_cap=1,
        buffer_pct=0.5,
        uid=uid,
        actid=actid,
    )
    return result


# ---------------------------------------------------------------------------
# L3: Square route — manual exit (EXIT-ONLY)
#
# CHOKEPOINT: calls auto_square.square_position (exit-only, sell-to-close).
# No entry order is placed here.
# ---------------------------------------------------------------------------

@api.post("/live-broker/order/square")
async def live_order_square():
    """Manually square the open test position (exit-only, no new entries).

    Fetches the current position from the broker, calls square_position, and
    reverts the mode to LIVE_OFFLINE.
    """
    try:
        client = await _get_client()
    except HTTPException:
        raise

    try:
        token_doc = await _get_token_doc()
        uid = token_doc.get("uid", "")
        actid = token_doc.get("actid", uid)
    except HTTPException:
        uid = ""
        actid = ""

    ss = _session_store()
    sess = await ss.get()

    # Fetch broker positions to build the position dict
    try:
        positions = await client.position_book()
    except Exception as exc:
        raise HTTPException(400, f"Could not fetch positions: {exc}") from exc

    position: Optional[Dict[str, Any]] = None
    entry_norenordno = sess.get("entry_norenordno")
    for pos in positions:
        nq = pos.get("netqty", 0)
        try:
            nq_int = int(float(str(nq).replace(",", "")))
        except (TypeError, ValueError):
            nq_int = 0
        if nq_int != 0:
            position = dict(pos)
            position["working_norenordno"] = entry_norenordno
            break

    if position is None:
        # No open position — still revert mode
        await _revert_mode()
        await ss.update_status("squared")
        return {"squared": True, "via": "cancel", "note": "no open position found"}

    # EXIT-ONLY: square_position calls place_order with a sell/buy-to-close exit
    result = await square_position(
        client,
        position,
        reason="manual",
        band_pct=5.0,
        uid=uid,
        actid=actid,
    )

    await ss.update_status("squared")
    await _revert_mode()
    return result


async def _revert_mode() -> None:
    """Revert to LIVE_OFFLINE (best-effort; logs but never raises)."""
    try:
        ms = _mode_store()
        await ms.revert_to_offline()
    except Exception as exc:
        log.warning("_revert_mode: failed: %s", exc)


# ---------------------------------------------------------------------------
# L3: Test-session route
# ---------------------------------------------------------------------------

_ACTIVE_SESSION_STATUSES = frozenset({"armed", "filled", "open"})
_TERMINAL_SESSION_STATUSES = frozenset({"squared", "kill_switch", "rejected", "canceled", "none"})

# Broker order statuses that mean the entry was never (or will never be) a real position.
_BROKER_REJECT_STATUSES = frozenset({"REJECTED", "CANCELED"})
# Broker order statuses that confirm a real / working fill — leave session active.
_BROKER_ACTIVE_STATUSES = frozenset({"COMPLETE", "OPEN", "TRIGGER_PENDING", "PARTIAL"})


@api.get("/live-broker/test-session")
async def live_test_session():
    """Return the current test-session state (deadline, remaining_secs, heartbeat, status).

    Auto-detects a rejected/canceled entry order:
    If the session is active (armed/filled/open) AND the broker order book reports the
    entry order as REJECTED or CANCELED, the session is automatically transitioned to
    'rejected', the mode is reverted to LIVE_OFFLINE, and remaining_secs is zeroed.

    Terminal sessions (squared/kill_switch/rejected/canceled/none) always return
    remaining_secs: 0 — never a positive countdown for a closed session.
    """
    ss = _session_store()
    now = _utcnow_iso()

    # Bump heartbeat
    try:
        await ss.bump_heartbeat(now_iso=now)
    except Exception:
        pass

    sess = await ss.get()
    status = sess.get("status", "none")
    deadline = sess.get("deadline")
    entry_norenordno = sess.get("entry_norenordno")

    # --- Auto-detect a rejected/canceled entry order ---
    # Only bother checking when the session appears active and has an entry order.
    # MUST use the async _get_client() (the real broker client) — NOT _order_client(),
    # which returns None in production (only tests patch it), so the detection was
    # silently dead live: a broker-rejected entry kept the session 'armed' with a
    # phantom countdown. _get_client() raises HTTPException when not connected.
    if status in _ACTIVE_SESSION_STATUSES and entry_norenordno:
        client = None
        try:
            client = await _get_client()
        except HTTPException:
            client = None
        except Exception as exc:
            log.debug("test_session: could not build client: %s", exc)
            client = None
        if client is not None:
            try:
                orders = await client.order_book()
                for order in orders:
                    nordno = order.get("norenordno") or order.get("norenordno") or ""
                    if nordno == entry_norenordno:
                        broker_status = str(order.get("status") or "").upper()
                        if broker_status in _BROKER_REJECT_STATUSES:
                            # Entry was rejected/canceled — auto-resolve the session
                            rejreason = order.get("rejreason") or f"broker_status:{broker_status}"
                            try:
                                await ss.update_status("rejected", reject_reason=rejreason)
                            except Exception as exc:
                                log.warning("test_session: could not update_status to rejected: %s", exc)
                            try:
                                ms = _mode_store()
                                await ms.revert_to_offline(now_iso=now)
                            except Exception as exc:
                                log.warning("test_session: could not revert mode: %s", exc)
                            # Reload session after update
                            sess = await ss.get()
                            status = sess.get("status", "rejected")
                        break
            except Exception as exc:
                # Not connected or order_book failed — leave session unchanged, never 500
                log.debug("test_session: could not read order_book: %s", exc)

    # Re-read status after potential auto-update
    status = sess.get("status", "none")

    # Terminal sessions always return remaining_secs: 0 (never a phantom countdown)
    if status in _TERMINAL_SESSION_STATUSES:
        remaining: Optional[float] = 0
    else:
        remaining = ss.remaining_secs(deadline, now)

    return {
        "position": sess.get("entry_norenordno"),
        "deadline": deadline,
        "remaining_secs": remaining,
        "heartbeat": now,
        "sl_norenordno": sess.get("sl_norenordno"),
        "status": status,
        "reject_reason": sess.get("reject_reason"),
    }


# ---------------------------------------------------------------------------
# L3: Kill-switch route — EXECUTING (L3 wires real client; exits test position)
#
# CHOKEPOINT: calls panic_squareoff (exit-only: cancel + flatten).
# No entry order is placed here.
# ---------------------------------------------------------------------------

@api.post("/live-broker/kill-switch")
async def live_kill_switch():
    """Execute the squareoff of all open orders and positions (L3: transmits).

    In L3 the kill-switch route EXECUTES the squareoff via panic_squareoff
    (which calls client.cancel_order and client.place_order for exits only).
    It then reverts mode to LIVE_OFFLINE and records the session as 'kill_switch'.

    Returns the panic report + config + transmitted=True.
    """
    store = _config_store()
    config = await store.get_config()

    open_orders: List[Dict[str, Any]] = []
    open_positions: List[Dict[str, Any]] = []
    connected = False
    transmitted = False
    panic_result: Dict[str, Any] = {}

    try:
        client = await _get_client()
        open_orders = await client.order_book()
        open_positions = await client.position_book()
        connected = True
    except HTTPException:
        pass
    except Exception as exc:
        log.warning("kill_switch: broker fetch failed: %s", exc)

    # Also get uid/actid for exit intents — best-effort, don't crash on missing token
    uid = ""
    actid = ""
    if connected:
        try:
            token_doc = await _get_token_doc()
            uid = token_doc.get("uid", "")
            actid = token_doc.get("actid", uid)
        except Exception:
            pass

    if connected:
        # EXIT-ONLY: panic_squareoff calls cancel_order + place_order (sell/buy exits)
        panic_result = await panic_squareoff(
            client,
            open_orders,
            open_positions,
            band_pct=1.0,
            uid=uid,
            actid=actid,
        )
        transmitted = True

        # Sweep ALL resting GTT/OCO alerts. panic_squareoff cancels working
        # ORDERS + flattens positions but does NOT touch resting GTT/OCO — those
        # would survive the panic and could fire later. Best-effort: a sweep
        # failure must NEVER block the panic flatten.
        try:
            for row in (await client.gtt_book() or []):
                al_id = row.get("al_id") or row.get("Al_id")
                if not al_id:
                    continue
                # Pick cancel_oco vs cancel_gtt from the row's ai_t: an OCO
                # bracket reads back as the documented "LMT_BOS_O" (see gtt.py
                # AI_T_OCO); anything else is a single-leg GTT. If ai_t is
                # missing/ambiguous, try cancel_oco then cancel_gtt (best-effort).
                ai_t = str(row.get("ai_t") or "").strip().upper()
                try:
                    if ai_t == "LMT_BOS_O":
                        await client.cancel_oco(al_id)
                    elif ai_t:
                        await client.cancel_gtt(al_id)
                    else:
                        try:
                            await client.cancel_oco(al_id)
                        except Exception:
                            await client.cancel_gtt(al_id)
                except Exception as exc:
                    log.warning("kill_switch: gtt/oco cancel failed for %s: %s",
                                al_id, exc)
        except Exception as exc:
            log.warning("kill_switch: gtt/oco sweep failed: %s", exc)

        # Revert mode
        await _revert_mode()

        # Update session status
        ss = _session_store()
        try:
            await ss.update_status("kill_switch")
        except Exception:
            pass
    else:
        # Not connected — return a plan only (pre-L3 degraded behaviour)
        panic_result = plan_squareoff(open_orders, open_positions)

    return {
        "plan": plan_squareoff(open_orders, open_positions),
        "panic": panic_result,
        "config": config,
        "transmitted": transmitted,
        "armed": True,
        "connected": connected,
    }


# ---------------------------------------------------------------------------
# Option-premium resolver — read-only, no order placement
# ---------------------------------------------------------------------------
#
# These three module-level getters are the only I/O seams for this feature.
# Tests monkeypatch them to inject fakes; production uses the real singletons.
# ---------------------------------------------------------------------------

def _get_db_for_option_premium():
    """Return the motor DB handle. Tests monkeypatch this."""
    from app.db import get_db
    return get_db()


def _get_tick_map_for_option_premium() -> dict:
    """Return the live tick map {instrument_key: tick_dict}.

    Wrapped so tests can monkeypatch without touching upstox_stream globally.
    Returns an empty dict if the stream manager is unavailable.
    """
    try:
        from app.upstox_stream import upstox_stream_manager
        return upstox_stream_manager.latest_tick_map()
    except Exception:
        return {}


def _now_ts_for_option_premium() -> float:
    """Return current UTC epoch seconds. Tests monkeypatch this for determinism."""
    import time
    return time.time()


class _OptionPremiumRequest(BaseModel):
    underlying: str
    strike: float
    expiry_date: str
    side: str


@api.post("/live-broker/option-premium")
async def get_option_premium(body: _OptionPremiumRequest):
    """Return the current premium for the requested option contract.

    Resolution order:
      1. Fresh live Upstox WS tick (within MARK_TICK_MAX_AGE_SECONDS = 120s).
      2. Last options_1m candle close (no age restriction — best-effort when
         market is closed).
      3. No data available → premium None.

    Always returns 200; never 500.  Read-only — no order is placed.

    Response::

        {
            "instrument_key": str | None,
            "premium": float | None,
            "source": "live_tick" | "last_candle" | "none",
            "fresh": bool,
            "ts": float | None,
            # only when contract not found:
            "reason": "contract_not_found"
        }
    """
    db = _get_db_for_option_premium()
    tick_map = _get_tick_map_for_option_premium()
    now_ts = _now_ts_for_option_premium()

    # 1. Resolve contract → instrument_key. Filter to ACTIVE expiries (>= today) and
    #    drop the row cap. option_contracts holds ~20k rows per underlying (mostly
    #    EXPIRED); the old unfiltered `.to_list(5000)` returned a natural-order slice
    #    that contained ZERO active contracts → match_contract → contract_not_found
    #    for every real strike. Mirrors deployment_evaluator._resolve_option_contract.
    today_iso = _today_utc_iso()
    try:
        contracts = await db.option_contracts.find(
            {"underlying": body.underlying, "expiry_date": {"$gte": today_iso}}
        ).to_list(length=None)
    except Exception as exc:
        log.warning("get_option_premium: option_contracts fetch failed: %s", exc)
        contracts = []

    contract = match_contract(
        contracts,
        strike=body.strike,
        side=body.side,
        expiry_date=body.expiry_date,
    )

    if contract is None:
        return {
            "instrument_key": None,
            "premium": None,
            "source": "none",
            "fresh": False,
            "ts": None,
            "reason": "contract_not_found",
        }

    instrument_key: str = contract["instrument_key"]

    # 2. Live tick (may be None if not subscribed / stream not running)
    tick = tick_map.get(instrument_key) if tick_map else None

    # 3. Last options_1m candle close
    candle_close = None
    try:
        candle = await db.options_1m.find_one(
            {"instrument_key": instrument_key},
            sort=[("ts", -1)],
        )
        if candle:
            candle_close = candle.get("close")
    except Exception as exc:
        log.warning("get_option_premium: options_1m fetch failed for %s: %s", instrument_key, exc)

    # 4. Resolve
    result = resolve_premium(
        instrument_key=instrument_key,
        tick=tick,
        candle_close=candle_close,
        now_ts=now_ts,
    )
    result["instrument_key"] = instrument_key
    return result


# ---------------------------------------------------------------------------
# ATM-strike suggester — read-only, no order placement
# ---------------------------------------------------------------------------
#
# Same I/O getter pattern as the option-premium route above so that tests can
# monkeypatch the same three functions.
#
# Getters reuse _get_db_for_option_premium, _get_tick_map_for_option_premium,
# and _now_ts_for_option_premium defined above — no new seams needed.
#
# Spot resolution: live Upstox tick → candle_1m fallback (same as
# live_option_universe._spot_from_latest_tick / _spot_from_latest_candle).
# ---------------------------------------------------------------------------

def _get_db_for_atm_suggest():
    """Return the motor DB handle. Tests monkeypatch this."""
    from app.db import get_db
    return get_db()


def _get_tick_map_for_atm_suggest() -> dict:
    """Return the live tick map {instrument_key: tick_dict}.

    Wrapped so tests can monkeypatch without touching upstox_stream globally.
    Returns an empty dict if the stream manager is unavailable.
    """
    try:
        from app.upstox_stream import upstox_stream_manager
        return upstox_stream_manager.latest_tick_map()
    except Exception:
        return {}


def _now_ts_for_atm_suggest() -> float:
    """Return current UTC epoch seconds. Tests monkeypatch this for determinism."""
    import time
    return time.time()


def _today_utc_iso() -> str:
    """Return today's date as ISO string (UTC). Tests monkeypatch this."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _to_float_or_none(value: Any) -> Optional[float]:
    """Convert value to float; return None on any failure or non-finite result."""
    import math as _math
    try:
        f = float(value)
        return f if _math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


@api.get("/live-broker/atm-suggest")
async def get_atm_suggest(
    underlying: str = Query(..., description="Index underlying, e.g. NIFTY, BANKNIFTY, SENSEX"),
    side: str = Query("CE", description="CE (call) or PE (put)"),
):
    """Return the nearest-ATM option strike, front expiry, and its premium.

    Resolution steps:
      1. Load all option_contracts for *underlying* from Mongo.
      2. Resolve SPOT: live Upstox WS tick → fallback last candles_1m close.
         If spot unavailable → return with reason="no_spot".
      3. Pick nearest expiry >= today (front weekly/monthly).
         If none found → return with reason="no_expiry".
      4. Pick the contract whose strike is nearest to spot for the given side.
         If none found → return with reason="no_atm".
      5. Fetch premium via live tick → last options_1m close (same as
         /live-broker/option-premium).

    Always returns 200; never 500.  Read-only — no order is placed.

    Response::

        {
            "underlying": str,
            "spot": float | None,
            "spot_source": "stream_tick" | "candles_1m" | None,
            "expiry": str | None,
            "atm_strike": float | None,
            "side": str,
            "instrument_key": str | None,
            "premium": float | None,
            "premium_source": "live_tick" | "last_candle" | "none",
            "fresh": bool,
            "reason": str | None,   # present only when something is missing
        }
    """
    from app.instruments import INSTRUMENT_KEYS

    db = _get_db_for_atm_suggest()
    tick_map = _get_tick_map_for_atm_suggest()
    now_ts = _now_ts_for_atm_suggest()
    today_iso = _today_utc_iso()
    side_upper = str(side).upper()

    underlying_upper = str(underlying).strip().upper()

    # 1. Load contracts — ACTIVE expiries only (>= today), no row cap. Without the
    #    expiry filter an unfiltered `.to_list(5000)` over ~20k rows returned only
    #    EXPIRED contracts → nearest_expiry found none → "no_expiry". Same root cause
    #    + fix as the option-premium route above.
    try:
        contracts = await db.option_contracts.find(
            {"underlying": underlying_upper, "expiry_date": {"$gte": today_iso}}
        ).to_list(length=None)
    except Exception as exc:
        log.warning("get_atm_suggest: option_contracts fetch failed: %s", exc)
        contracts = []

    # 2. Resolve SPOT
    # a) Live Upstox tick for the index instrument key
    spot: Optional[float] = None
    spot_source: Optional[str] = None

    index_key = INSTRUMENT_KEYS.get(underlying_upper)
    if index_key and tick_map:
        tick_data = tick_map.get(index_key)
        if tick_data:
            raw_price = tick_data.get("last_price") or tick_data.get("ltp")
            spot = _to_float_or_none(raw_price)
            if spot is not None:
                spot_source = "stream_tick"

    # b) Candle fallback
    if spot is None:
        try:
            cursor = db.candles_1m.find(
                {"instrument": underlying_upper},
                {"_id": 0, "close": 1},
            ).sort("ts", -1).limit(1)
            rows = await cursor.to_list(length=1)
            if rows:
                spot = _to_float_or_none(rows[0].get("close"))
                if spot is not None:
                    spot_source = "candles_1m"
        except Exception as exc:
            log.warning("get_atm_suggest: candles_1m fallback failed: %s", exc)

    if spot is None:
        return {
            "underlying": underlying_upper,
            "spot": None,
            "spot_source": None,
            "expiry": None,
            "atm_strike": None,
            "side": side_upper,
            "instrument_key": None,
            "premium": None,
            "premium_source": "none",
            "fresh": False,
            "reason": "no_spot",
        }

    # 3. Nearest expiry
    expiry = nearest_expiry(contracts, today_iso=today_iso)
    if expiry is None:
        return {
            "underlying": underlying_upper,
            "spot": spot,
            "spot_source": spot_source,
            "expiry": None,
            "atm_strike": None,
            "side": side_upper,
            "instrument_key": None,
            "premium": None,
            "premium_source": "none",
            "fresh": False,
            "reason": "no_expiry",
        }

    # 4. Nearest strike to spot
    atm_row = _atm_strike_pure(contracts, spot=spot, expiry_date=expiry, side=side_upper)
    if atm_row is None:
        return {
            "underlying": underlying_upper,
            "spot": spot,
            "spot_source": spot_source,
            "expiry": expiry,
            "atm_strike": None,
            "side": side_upper,
            "instrument_key": None,
            "premium": None,
            "premium_source": "none",
            "fresh": False,
            "reason": "no_atm",
        }

    instrument_key: str = str(atm_row.get("instrument_key") or "")
    atm_strike_val = _to_float_or_none(atm_row.get("strike"))

    # 5. Premium: live tick → last options_1m candle
    option_tick = tick_map.get(instrument_key) if (tick_map and instrument_key) else None
    candle_close = None
    if instrument_key:
        try:
            candle = await db.options_1m.find_one(
                {"instrument_key": instrument_key},
                sort=[("ts", -1)],
            )
            if candle:
                candle_close = candle.get("close")
        except Exception as exc:
            log.warning("get_atm_suggest: options_1m fetch failed for %s: %s", instrument_key, exc)

    prem_result = resolve_premium(
        instrument_key=instrument_key,
        tick=option_tick,
        candle_close=candle_close,
        now_ts=now_ts,
    )

    return {
        "underlying": underlying_upper,
        "spot": spot,
        "spot_source": spot_source,
        "expiry": expiry,
        "atm_strike": atm_strike_val,
        "side": side_upper,
        "instrument_key": instrument_key or None,
        "premium": prem_result["premium"],
        "premium_source": prem_result["source"],
        "fresh": prem_result["fresh"],
        "reason": None,
    }
