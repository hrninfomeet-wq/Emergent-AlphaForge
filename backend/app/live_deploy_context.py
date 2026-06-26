"""Live-deploy "context": the real broker collaborators + the guard-registering
``arm_for`` factory consumed by the continuous live path (``auto_live``).

The deployment evaluator's tee (``evaluate_active_deployments``) needs, per ARMED
deployment signal, a bundle of collaborators to hand to
``auto_live.auto_live_trade_for_signal``: the Flattrade client, the intent store,
the live engine, an async→sync ``search_fn`` for symbol resolution, a shared rate
throttle, the account lot ceiling, the broker uid/actid, and an ``arm_for`` factory
that registers the filled position with the software exit guard.

``build_live_deploy_context(db)`` assembles that bundle from ``routers.live_broker``'s
existing getters (no broker logic is duplicated — they are imported and called).
It returns ``None`` when the broker is NOT connected (no valid Flattrade token) or
not configured, so the evaluator treats live as disabled and falls through to the
unchanged ``auto_paper`` path. It NEVER raises on an unconfigured/erroring broker.

``arm_for`` builds a MULTI-POSITION guard arm: it registers the position with the
process-singleton ``LiveMonitorRegistry`` (``source="auto_live"``, carrying the
spot-exit / time-stop / deployment_id so the deployed position gets full exit
parity with the paper/backtest path). Unlike the manual single-shot
``live_broker._make_arm`` it does NOT create a ``SessionStore`` arm and does NOT
schedule the 10-minute auto-square — for a deployed (multi-position) book the
software guard + the 15:00 IST EOD square handle exits. Registration is
best-effort (a registry failure is logged, never crashes the fill).
"""
from __future__ import annotations

import functools
import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.live.flattrade_token import DEFAULT_USER_ID, get_status
from app.live.gtt import build_oco_intent
from app.live.live_position_guard import get_registry
from app.live.live_sl_monitor import build_monitor_state
from app.live.oco_levels import compute_catastrophe_band

log = logging.getLogger(__name__)

#: Default price-band (% of ref_ltp) for the live LMT entry (mirrors the manual
#: place route's default of 5.0).
_DEFAULT_BAND_PCT = 5.0

#: Deep-default catastrophe premium stop (% of entry premium) — never leave a
#: deployed position unprotected. Mirrors ``_make_arm._GUARD_DEFAULT_STOP_PCT``;
#: the exit plan already seeds this in ``auto_live.resolve_live_exit_plan``, but we
#: defend in depth here so a plan with no stop still produces a monitorable state.
_GUARD_DEFAULT_STOP_PCT = 50.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# arm_for — multi-position guard-registering arm factory
# --------------------------------------------------------------------------- #

def arm_for(
    plan: Dict[str, Any],
    signal_doc: Dict[str, Any],
    ref_ltp: float,
    *,
    client: Any = None,
    uid: str = "",
    actid: str = "",
    catastrophe_stop_pct: Optional[float] = None,
    catastrophe_target_pct: Optional[float] = None,
) -> Callable[[Any, str], Any]:
    """Return an async ``arm(intent, norenordno)`` callable for the executor.

    On a successful fill the executor calls ``arm(intent, norenordno)``. This arm
    does TWO things:

    1. REGISTERS the filled position with the software exit guard (MANDATORY),
       carrying the full exit plan so the deployed position gets paper/backtest
       exit parity:
         - premium SL/TP/trailing via ``build_monitor_state`` from ``plan["levels"]``
           (with a deep-default 50% stop floor if the plan somehow carries none);
         - ``spot_exit`` (the live ``spot_exit`` mode — close when the underlying hits
           a level);
         - ``time_stop_minutes`` (close after N minutes from entry);
         - ``source="auto_live"`` (so the guard's 15:00 IST EOD square applies — manual
           single-shots are EOD-exempt) and ``deployment_id`` for audit.

    2. BEST-EFFORT places a resting broker OCO (stop+target) so a PC-down position
       still has a broker-side catastrophe net. The OCO levels come from
       ``compute_catastrophe_band`` (derived strictly wider than the software guard
       stop). This is best-effort ONLY: a transient OCO reject / exception must NEVER
       unwind an already-filled+guarded entry, so it runs in a separate try/except
       that never re-raises. When ``client is None`` no OCO is placed.

    It does NOT build a SessionStore arm and does NOT schedule a 10-minute
    auto-square — those are the manual single-shot's concern. Registration is
    MANDATORY: if ``build_monitor_state`` or ``register`` fails, the exception
    PROPAGATES so the executor's ``_abort_protect`` squares the fill and halts — a
    deployed position is never left live-and-unguarded. The OCO block sits AFTER
    register, so a register failure short-circuits before any OCO is attempted.

    ``_arm`` returns the broker OCO alert id (``oco_al_id``) on a successful OCO
    placement, else ``None`` (no client / band unavailable / reject / exception).
    """
    levels = plan.get("levels") or {}
    deployment_id = signal_doc.get("deployment_id")

    async def _arm(intent: Any, norenordno: str) -> Optional[str]:
        # Registration is MANDATORY (no 10-min backstop for a deployed position): any
        # failure here propagates to the executor, whose _abort_protect squares + halts
        # rather than leaving an unguarded live position. Do NOT swallow.
        stop_pct = levels.get("stop_pct")
        # Defense-in-depth: a position with no premium stop is never unmonitorable —
        # seed the 50% catastrophe floor INDEPENDENT of any spot stop (auto_live's
        # resolve_live_exit_plan already does this; this is belt-and-suspenders so a
        # hand-built plan can never produce an all-None monitor state that fails to
        # register). The spot-mirror exit remains additive.
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
        # The catastrophe band must sit strictly WIDER than the guard's ACTUAL
        # resolved stop level (so the resting OCO never races the in-process software
        # guard). build_monitor_state["stop_level"] is the ABSOLUTE premium stop the
        # guard will use — derived from pct OR pts OR the deep-default. Derive
        # guard_stop_pct as a PERCENT of entry FROM that resolved level so a POINTS
        # stop (where stop_pct is None) is handled correctly: a None pct would
        # collapse compute_catastrophe_band to its 50% default, which for a stop
        # deeper than 50% would land the OCO SL ABOVE the real guard stop and fire
        # first (the BLOCKER). Fall back to the pct (incl. the deep-default) only when
        # the level is unusable.
        sl = state.get("stop_level")
        try:
            ref = float(ref_ltp)
        except (TypeError, ValueError):
            ref = 0.0
        guard_stop_pct = stop_pct
        if sl is not None and math.isfinite(float(sl)) and ref > 0:
            guard_stop_pct = (1.0 - float(sl) / ref) * 100.0
        get_registry().register(
            key=norenordno,
            tsym=intent.tsym,
            exch=intent.exch,
            qty=intent.qty,
            prd=intent.prd,
            entry_price=float(ref_ltp),
            state=state,
            spot_exit=plan.get("spot_exit"),
            time_stop_minutes=plan.get("time_stop_minutes"),
            entry_ts=_now_iso(),
            source="auto_live",
            deployment_id=deployment_id,
        )
        log.info("auto_live arm: registered %s with software guard (deployment=%s, stop_pct=%s)",
                 getattr(intent, "tsym", "?"), deployment_id, stop_pct)

        # --- BEST-EFFORT resting broker OCO (PC-down catastrophe net) --------------
        # NEVER re-raises: an already-filled+guarded entry must not be unwound by a
        # transient OCO reject. Failure → oco_al_id stays None (auto_live journals
        # "no_broker_backstop"); the software guard remains the live protection.
        oco_al_id: Optional[str] = None
        if client is not None:
            try:
                band = compute_catastrophe_band(
                    float(ref_ltp),
                    guard_stop_pct=guard_stop_pct,
                    stop_pct=catastrophe_stop_pct,
                    target_pct=catastrophe_target_pct,
                )
                if band:
                    sl_t, sl_l, tp_t, tp_l = band
                    oco = build_oco_intent(
                        exch=intent.exch,
                        tsym=intent.tsym,
                        qty=intent.qty,
                        prd="M",                # OCO is NRML-only (carry-forward backstop)
                        sl_trigger=sl_t,
                        sl_limit=sl_l,
                        tp_trigger=tp_t,
                        tp_limit=tp_l,
                        remarks=f"oco:{norenordno}",
                    )
                    if oco:
                        res = await client.place_oco(oco)
                        if res.get("ok"):
                            oco_al_id = res.get("al_id")
                            ent = get_registry().get(norenordno)
                            if ent is not None:
                                ent["oco_al_id"] = oco_al_id
                            log.info("auto_live arm: resting OCO placed for %s (al_id=%s)",
                                     getattr(intent, "tsym", "?"), oco_al_id)
                        else:
                            log.warning("auto_live arm: OCO rejected for %s: %s",
                                        getattr(intent, "tsym", "?"), res)
                    else:
                        log.warning("auto_live arm: build_oco_intent returned None for %s "
                                    "(NRML-only / bad levels) — no broker backstop",
                                    getattr(intent, "tsym", "?"))
                else:
                    log.warning("auto_live arm: compute_catastrophe_band returned None for %s "
                                "— no broker backstop", getattr(intent, "tsym", "?"))
            except Exception as exc:
                # A filled+guarded entry must NEVER be unwound by an OCO failure.
                log.warning("auto_live arm: resting OCO place failed for %s (%s) — "
                            "no broker backstop (software guard still protects)",
                            getattr(intent, "tsym", "?"), exc)
                oco_al_id = None

        return oco_al_id

    return _arm


# --------------------------------------------------------------------------- #
# build_live_deploy_context — assemble the real broker collaborators
# --------------------------------------------------------------------------- #

async def build_live_deploy_context(db: Any) -> Optional[Dict[str, Any]]:
    """Build the live-deploy collaborator bundle, or ``None`` when not connected.

    Returns ``None`` (live disabled — the evaluator falls through to auto_paper)
    when there is no valid, unexpired Flattrade token, when the broker is not
    configured, or on ANY error while probing the connection. NEVER raises.

    On success returns a dict consumed by the evaluator tee as ``**live_kwargs``
    for ``auto_live.auto_live_trade_for_signal``::

        {client, intent_store, engine, search_fn, throttle, account_max,
         connected=True, uid, actid, band_pct, arm_for}
    """
    # 1. Connection probe — fail-soft to None.
    try:
        status = await get_status(DEFAULT_USER_ID)
    except Exception as exc:
        log.warning("build_live_deploy_context: status probe failed (%s) — live disabled", exc)
        return None
    if not (isinstance(status, dict) and status.get("connected") and not status.get("expired")):
        return None

    # 2. Build the real collaborators via live_broker's getters (no duplication).
    #    Import lazily so this module stays host-importable without the router.
    try:
        from app.routers import live_broker as lb

        client = await lb._get_client()              # async; raises if no token
        token_doc = await lb._get_token_doc()
        uid = token_doc.get("uid", "") or ""
        actid = token_doc.get("actid", uid) or ""

        intent_store = lb._intent_store()
        engine = lb._l3_engine()

        # account ceiling = the safety-config store's max_lots_per_order
        account_max = 20
        try:
            cfg = await lb._config_store().get_config()
            account_max = int(cfg.get("max_lots_per_order") or 20)
        except Exception as exc:
            log.warning("build_live_deploy_context: could not read max_lots_per_order (%s); default 20", exc)

        # A shared module-level throttle so all deployed orders this cadence share
        # one token bucket (keeps the per-second SEBI cap honest across deployments).
        throttle = _shared_throttle()

        search_fn = await _build_search_fn(client)
    except Exception as exc:
        # _get_client raises HTTPException(400) when not connected — already handled
        # by the status probe, but defend here so a transient build error degrades
        # to live-disabled rather than crashing the whole evaluator pass.
        log.warning("build_live_deploy_context: collaborator build failed (%s) — live disabled", exc)
        return None

    # Bind ONLY the deployment-INDEPENDENT collaborators (client/uid/actid) into the
    # arm_for factory here. The per-deployment catastrophe pct is NOT bound — the
    # context has no deployment; it reaches arm_for via the PER-SIGNAL call in
    # auto_live (which reads deployment.risk.live). The catastrophe band falls to its
    # own defaults when the per-signal call passes None.
    bound_arm_for = functools.partial(arm_for, client=client, uid=uid, actid=actid)

    return {
        "client": client,
        "intent_store": intent_store,
        "engine": engine,
        "search_fn": search_fn,
        "throttle": throttle,
        "account_max": account_max,
        "connected": True,
        "uid": uid,
        "actid": actid,
        "band_pct": _DEFAULT_BAND_PCT,
        "arm_for": bound_arm_for,
    }


# --------------------------------------------------------------------------- #
# search_fn adapter — async→sync SearchScrip (mirrors live_order_place)
# --------------------------------------------------------------------------- #

async def _build_search_fn(client: Any) -> Callable[[str, str], List[Dict[str, Any]]]:
    """Build the async→sync ``search_fn(exch, query)`` adapter the executor's
    ``build_intent`` calls for symbol resolution.

    ``build_intent`` invokes ``search_fn`` SYNCHRONOUSLY, but the broker's
    ``search_scrip`` is async. Mirroring ``live_broker.live_order_place``, we
    pre-fetch scrip rows for the queried (exch, query) on first call and cache by
    key; the sync adapter then returns the cached rows. Each unique contract's
    underlying/strike maps to one ``search_scrip`` call, prefetched on demand via a
    tiny event-loop bridge so the sync adapter stays pure.

    NOTE: the executor's deployed path calls ``search_fn`` once per order from
    within the same async pass; here we expose a sync adapter that lazily pre-fetches
    per (exch, query) using a cache so resolution works for any contract the tee
    hands it without the caller pre-knowing the strike.
    """
    import asyncio

    cache: Dict[str, List[Dict[str, Any]]] = {}

    def _sync_search(exch: str, query: str) -> List[Dict[str, Any]]:
        key = f"{exch}|{query}"
        if key in cache:
            return cache[key]
        # build_intent calls this synchronously from inside the running loop; we
        # cannot await here. Schedule the fetch on the loop and block briefly.
        try:
            loop = asyncio.get_event_loop()
            rows = loop.run_until_complete(client.search_scrip(exch, query))
        except RuntimeError:
            # Already inside a running loop (the normal case): fall back to a fresh
            # loop in a worker thread so we never re-enter the running loop.
            import concurrent.futures

            def _runner() -> List[Dict[str, Any]]:
                return asyncio.run(client.search_scrip(exch, query))

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                rows = ex.submit(_runner).result()
        except Exception as exc:
            log.warning("auto_live search_fn: SearchScrip failed for %s %s: %s", exch, query, exc)
            rows = []
        cache[key] = list(rows or [])
        return cache[key]

    return _sync_search


# --------------------------------------------------------------------------- #
# Shared rate throttle singleton
# --------------------------------------------------------------------------- #

_THROTTLE_SINGLETON: Any = None


def _shared_throttle() -> Any:
    """Return the process-wide RateThrottle shared by all deployed live orders."""
    global _THROTTLE_SINGLETON
    if _THROTTLE_SINGLETON is None:
        from app.live.safety import RateThrottle
        _THROTTLE_SINGLETON = RateThrottle()
    return _THROTTLE_SINGLETON
