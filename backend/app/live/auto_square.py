"""Auto-square engine — the margin-safe square EXECUTOR + SL-LMT backstop builder.

One job: give the live path a way to exit a filled position it holds, bypassing
every fat-finger/throttle guard, through the cancel-all-then-close path that never
trips the naked-short margin trap.

History: this module once also owned an "L3.3 hard cap (≤10 minutes)" time-square
for manual live-test positions (``SQUARE_HORIZON_SEC`` / ``deadline_iso`` /
``is_due``). That timer was removed (see docs/superpowers/specs/
2026-07-09-remove-manual-livetest-10min-timer-design.md) — deployed strategies
follow their strategy rules + a resting OCO, and the 15:00 IST EOD square is the
manual position's "never left open" backstop. Only the executor and the SL builder
remain here.

Architecture
------------
* ``build_sl_backstop_intent`` creates a protective SL-LMT exit for a LONG option.
  Returns None for any invalid/sub-tick stop_trigger instead of asserting/raising —
  a sub-0.05 premium is real deep-OTM market data, not a programming error.
  The time-square hard cap remains the primary protection; the SL backstop is
  supplementary.

* ``square_position`` is the executor:
  - Parses filled netqty; if 0 (entry never filled) cancels the working
    remainder and reports squared=True via cancel.
  - MARGIN-SAFE EXIT (P1.4): before placing the exit it cancels ALL working
    orders for the scrip — the caller's known ids (``working_norenordno`` or the
    ``working_norenordnos`` list) PLUS any resting order discovered in the order
    book (e.g. a protective SL) — and CONFIRMS they are terminal via a re-fetch.
    A resting SL sell left working while a square-off sell is placed makes the
    broker see a naked short → margin reject (the ₹2.16L failure). If the working
    set cannot be confirmed clear, it returns squared=False, reason
    'cancel_unconfirmed' (NEVER places the doomed exit).
  - Validates lp BEFORE cancelling: an unpriced position keeps its protection
    (we never strip an SL we cannot replace with a priced exit).
  - Builds a marketable-limit exit in the CORRECT direction (long→SELL, short→BUY).
  - If lp is missing/non-finite/≤0 → returns {squared: False, reason: 'unpriced'}.
    The caller (engine) MUST halt on squared=False — it NEVER silently skips.
  - Retries a rejected exit ONCE (same qty/prc, fresh client_order_id). A
    RAISED place is first resolved against the order book (remarks==cid): a
    landed order is ADOPTED, never re-posted; an unreadable book means NO
    retry (fail closed — the lost-ack double-sell guard).
  - On two consecutive rejects → {squared: False, failures: [...]}. NO raise.
  - NEVER applies fat-finger or throttle guards to an exit intent.
  - NEVER raises; always returns a dict.
  - NOT self-idempotent — the caller MUST NOT call it twice on the same position
    (matches panic_squareoff's contract in kill_switch.py).

Key safety properties (mirrors kill_switch.py's language):
- Fat-finger/throttle NEVER applied to exit intents.
- A position whose ref price is invalid is NOT silently dropped → squared=False.
- A rejected exit is retried ONCE, not silently ignored.
- If two consecutive rejects occur the engine is told (squared=False, failures=[...])
  so it can page the operator instead of leaving the position open indefinitely.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from app.live.order_builder import round_to_tick

from app.live.broker_protocol import BrokerReadError, OrderIntent
from app.live.exit_claims import claim_exit
from app.live.idempotency import new_client_order_id
from app.live.kill_switch import (
    TERMINAL,
    _REJECTED_STATUSES,
    _leg_price,
    _normalize_status,
    _order_row,
    _parse_netqty,
    _pos_float,
    _scan_order_by_remarks,
)

log = logging.getLogger(__name__)

#: Max cancel+confirm passes before an exit is placed. A working order that
#: survives this many passes is treated as un-cancellable (margin-unsafe to exit).
_MAX_CANCEL_PASSES: int = 2


# ---------------------------------------------------------------------------
# 2. SL-LMT backstop builder — protective exit for a LONG option leg
# ---------------------------------------------------------------------------

def build_sl_backstop_intent(
    *,
    exch: str,
    tsym: str,
    qty: int,
    stop_trigger: Any,
    client_order_id: str,
    tick: float = 0.05,
) -> Optional[OrderIntent]:
    """Build a protective SL-LMT intent for a LONG option leg, or None if invalid.

    All timestamps are normalized to UTC; callers SHOULD pass UTC (the engine
    uses a UTC-aware now).  Naive is assumed UTC.

    The order sells (trantype='S') qty lots at a trigger of stop_trigger with
    the limit price set slightly below the trigger so the order becomes marketable
    once triggered.

    Parameters
    ----------
    exch:             Exchange, e.g. "NFO" or "BFO".
    tsym:             Trading symbol.
    qty:              Number of units to sell (positive integer).
    stop_trigger:     Trigger price (trgprc) in ₹.  Must be a finite positive
                      number strictly greater than 0.05 (the exchange tick floor).
                      A sub-0.05 value is plausible real deep-OTM market data —
                      the function returns None rather than raising so the caller
                      can fall back to the time-square hard cap.
    client_order_id:  Caller-supplied idempotency key.

    Returns
    -------
    An ``OrderIntent`` with prctyp="SL-LMT", trantype="S" when stop_trigger
    is a finite number > 0.05, otherwise ``None``.

    Returns None (never raises) when:
    - stop_trigger is not numeric (None, str that can't be parsed as float, etc.)
    - stop_trigger is NaN or ±Inf
    - stop_trigger <= 0.05 (at or below the exchange tick floor; a protective
      stop cannot be built because prc would equal or exceed trgprc)

    The caller's primary protection is the time-square hard cap; the SL backstop
    is supplementary and its absence is not an error.

    Price formula
    -------------
    trgprc = stop_trigger
    prc    = max(0.05, round(stop_trigger - 0.05, 2))

    Since stop_trigger > 0.05 is required, prc <= trgprc always holds naturally
    (the protective invariant is structurally guaranteed, not asserted).
    """
    # Validate: coerce to float and check finite + above tick floor.
    try:
        trig = float(stop_trigger)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None  # non-numeric (None, "abc", etc.) — fail-soft

    if not math.isfinite(trig):
        return None  # NaN or ±Inf → fail-soft

    if trig <= 0.05:
        return None  # at or below tick floor → can't build protective stop

    # Round trgprc to nearest tick, prc to a tick multiple below trgprc (down).
    effective_tick = tick if tick > 0 else 0.05
    trgprc = round_to_tick(trig, effective_tick, mode="nearest")
    prc = round_to_tick(max(0.05, round(trgprc - effective_tick, 2)), effective_tick, mode="down")
    prc = max(0.05, prc)
    # Structural guarantee: since trig > 0.05 and trgprc is tick-aligned near trig,
    # prc = trgprc - tick (down), so prc <= trgprc always holds.

    return OrderIntent(
        client_order_id=client_order_id,
        trantype="S",
        prctyp="SL-LMT",
        exch=exch,
        tsym=tsym,
        qty=qty,
        prc=prc,
        prd="I",
        ret="DAY",
        trgprc=trgprc,
        remarks=client_order_id,
    )


# ---------------------------------------------------------------------------
# 3. square_position — executor (MockNoren in tests, FlattradeClient in L3)
# ---------------------------------------------------------------------------

def _marketable_prc(ref: float, trantype: str, band_pct: float, tick: float = 0.05) -> float:
    """Compute a marketable-limit exit price rounded to the exchange tick.

    SELL (long exit): round_to_tick(ref * (1 - eff/100), tick, mode="down")
    BUY  (short exit): round_to_tick(ref * (1 + eff/100), tick, mode="up")
    eff = abs(band_pct)

    Directional rounding keeps the price marketable (SELL stays <= ref,
    BUY stays >= ref after rounding) while satisfying the broker's tick constraint.
    Broker rejects prices that are not exact multiples of the tick size.

    tick defaults to 0.05 (NIFTY/BANKNIFTY/SENSEX index options).
    If tick <= 0 falls back to 0.05.
    """
    eff = abs(band_pct)
    _tick = tick if tick > 0 else 0.05
    if trantype == "S":
        return round_to_tick(ref * (1.0 - eff / 100.0), _tick, mode="down")
    else:  # "B"
        return round_to_tick(ref * (1.0 + eff / 100.0), _tick, mode="up")


async def _fresh_book_state(client: Any, tsym: str) -> tuple:
    """One fresh position-book read for *tsym*, classified tri-state:

      ``("flat", 0)``        — a KNOWN (non-empty) book whose row for tsym is
                               absent or parses to netqty 0: the position is gone.
      ``("live", n)``        — a parsed non-zero netqty n (the current truth).
      ``("unknown", None)``  — the read raised, the book is empty/non-list, or the
                               row is present with an UNPARSEABLE netqty. UNKNOWN
                               must never be coerced to flat (no false
                               already_flat) nor used to resize an exit.
    """
    try:
        book = await client.position_book()
    except Exception:
        return ("unknown", None)
    if not (isinstance(book, list) and book):
        return ("unknown", None)
    for row in book:
        if str(row.get("tsym", "")) == str(tsym):
            n = _parse_netqty(row.get("netqty"))
            if n is None:
                return ("unknown", None)  # present but garbage — per _parse_netqty's contract
            return ("flat", 0) if n == 0 else ("live", n)
    return ("flat", 0)  # absent from a complete non-empty book


async def _cancel_all_working_for_scrip(
    client: Any, tsym: str, seed_ids: List[str]
) -> Dict[str, Any]:
    """Cancel EVERY working order for ``tsym`` and confirm they go terminal.

    This is the margin-safety core (P1.4). A resting SL sell left working while
    we place a square-off sell makes the broker see a naked short → margin reject
    (the observed ₹2.16L failure: "cancel the stop-loss first, then square off").
    So we discover ALL working orders for the scrip — the caller's known ids PLUS
    anything still working in the order book (e.g. a resting SL the caller didn't
    track) — cancel them, and CONFIRM via a re-fetch that none remain non-terminal
    before the exit is placed.

    Up to ``_MAX_CANCEL_PASSES`` cancel+confirm passes absorb a transient
    not-yet-processed cancel. If the client exposes no ``order_book`` (a minimal
    legacy stub), discovery/confirmation are skipped and the seed ids are
    cancelled best-effort (cleared=True is reported — we cannot do better).

    Returns ``{"cleared": bool, "remaining": [norenordno, ...]}``. ``cleared`` is
    True iff, after cancelling, no non-terminal order for the scrip remains.
    """
    ids = {x for x in seed_ids if x}
    has_book = hasattr(client, "order_book")

    # Discover any additional working orders for this scrip from the book.
    if has_book:
        try:
            book = await client.order_book()
            for o in (book or []):
                if _normalize_status(o.get("status")) in TERMINAL:
                    continue
                if str(o.get("tsym", "")) != str(tsym):
                    continue
                non = o.get("norenordno")
                if non:
                    ids.add(non)
        except Exception as exc:
            # FAIL-CLOSED on ANY read failure: an unreadable order book — a typed
            # BrokerReadError (expired token) AND a transport error (httpx timeout,
            # HTTP!=200 RuntimeError) alike — means we cannot confirm there are no
            # UNTRACKED resting orders (e.g. a resting SL) for this scrip. Refuse to
            # report cleared — placing an exit while a resting SL might still be
            # working is a naked-short / margin-reject risk, and transport failures
            # CORRELATE with the cancel itself having silently failed (same degraded
            # broker). This matters most when seed_ids is empty: without it, an
            # errored discovery would fall through to the cleared=True early-return.
            emsg = getattr(exc, "emsg", None) or str(exc)
            return {"cleared": False, "remaining": sorted(ids),
                    "reason": f"cancel-discovery read failed ({str(emsg)[:80]})"}

    if not ids:
        return {"cleared": True, "remaining": []}

    for _ in range(_MAX_CANCEL_PASSES):
        for non in list(ids):
            try:
                await client.cancel_order(non)
            except Exception:
                pass  # a cancel raising is non-fatal; the confirm re-fetch decides

        if not has_book:
            # Cannot confirm against a book — trust the cancels (legacy clients).
            return {"cleared": True, "remaining": []}

        try:
            book = await client.order_book()
        except Exception as exc:
            # FAIL-CLOSED on ANY read failure (BrokerReadError AND transport): an
            # unreadable order book means we CANNOT confirm the resting SL was
            # cancelled. Do NOT trust the cancels — a cancel POST timeout and a
            # confirm-read timeout correlate (same degraded broker), so "trust the
            # cancels" here was exactly the both-timeouts → cleared=True → naked
            # short window. Report cleared=False so square_position refuses to
            # place the exit; the existing SL stays working, so the position is
            # not left unprotected, and the caller retries.
            emsg = getattr(exc, "emsg", None) or str(exc)
            return {
                "cleared": False,
                "remaining": sorted(ids),
                "reason": f"cancel-confirm read failed ({str(emsg)[:80]})",
            }

        remaining = [
            o.get("norenordno")
            for o in (book or [])
            if _normalize_status(o.get("status")) not in TERMINAL
            and str(o.get("tsym", "")) == str(tsym)
            and o.get("norenordno")
        ]
        ids = set(remaining)
        if not ids:
            return {"cleared": True, "remaining": []}

    return {"cleared": False, "remaining": sorted(ids)}


async def square_position(
    client: Any,
    position: Dict[str, Any],
    *,
    reason: str,
    band_pct: float = 1.0,
    uid: str = "",
    actid: str = "",
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Per-tsym-serialized marketable-limit exit (see ``_square_position_impl``).

    The software guard (stop/spot-mirror/time-stop/EOD squares), the deployment
    stop, and the manual square route all funnel through here; this thin wrapper
    claims an exclusive per-tsym exit lock so two of them can't place a second
    SELL on the same scrip and reverse it into a naked short. On contention it
    returns squared=False (the caller keeps retrying / re-reads), never placing
    a competing exit. The kill switch claims tsyms itself and DEFERS any it
    cannot claim (see live_broker._run_kill_switch)."""
    tsym = str(position.get("tsym", "") or "")
    async with claim_exit(tsym, label=reason) as got:
        if not got:
            log.warning("square_position: exit for %s already in flight on another "
                        "path — skipping (reason=%s)", tsym, reason)
            return {
                "squared": False, "via": None, "norenordno": None,
                "reason": "exit_in_flight_elsewhere",
                "note": "another exit path is already flattening this scrip",
                "failures": [],
            }
        return await _square_position_impl(
            client, position, reason=reason, band_pct=band_pct,
            uid=uid, actid=actid, now_iso=now_iso)


async def _square_position_impl(
    client: Any,
    position: Dict[str, Any],
    *,
    reason: str,
    band_pct: float = 1.0,
    uid: str = "",
    actid: str = "",
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Marketable-limit exit of a filled live position.

    This is the executor layer — it calls ``client.cancel_order`` and
    ``client.place_order`` directly, bypassing ALL throttle and fat-finger
    checks.  The engine MUST always be able to exit a position it holds.

    NOT self-idempotent — the caller MUST NOT call it twice on the same position
    (matches panic_squareoff's contract in kill_switch.py).

    NEVER raises.  Returns a dict describing the outcome.

    Parameters
    ----------
    client:
        A BrokerClient instance (MockNoren in tests, FlattradeClient in L3).
    position:
        A dict describing the position to exit.  Expected keys:

        ``tsym``              — trading symbol (str)
        ``exch``              — exchange, e.g. "NFO" (str; defaults to "NFO")
        ``netqty``            — signed FILLED quantity (int or parseable string);
                                negative = short position.
        ``lp``                — last price / ref price (float or parseable string).
                                If missing, non-finite, or ≤ 0 → returns squared=False
                                with reason='unpriced'.  NEVER silently skipped.
        ``working_norenordno`` — (optional) norenordno of an unfilled/partial entry
                                 order to cancel BEFORE placing the exit.

    reason:
        A human-readable string describing why the exit is being triggered
        (e.g. "auto_square_deadline", "manual_override").
    band_pct:
        Marketable-limit cross buffer (%).  Clamped via abs(); default 1.0.
    uid, actid:
        Broker credentials for intent.to_jdata().
    now_iso:
        Injected timestamp for audit/logging (not used in time logic here;
        the caller drives the deadline check).

    Returns
    -------
    dict with keys:

        ``squared``      — True iff the position is considered closed.
        ``via``          — 'exit_order' | 'cancel' | None
        ``norenordno``   — broker order number of the exit order (if placed).
        ``reason``       — echoes the reason parameter (or 'unpriced' on bad lp).
        ``note``         — human-readable note (e.g. 'no position').
        ``failures``     — list of reject reasons from place_order attempts.

    squared=True conditions:
      • An exit order was placed and accepted by the broker (via='exit_order').
      • The entry was never filled (netqty == 0) and any working order was
        cancelled (via='cancel', note='no position').

    squared=False conditions (engine MUST halt / alert):
      • lp is missing/non-finite/≤0  (reason='unpriced').
      • netqty cannot be parsed       (reason='unpriced').
      • Both place_order attempts were rejected (failures=[...]).

    Direction invariant (CRITICAL):
      long position  (netqty > 0) → trantype = 'S' (SELL)
      short position (netqty < 0) → trantype = 'B' (BUY)
    A wrong direction would GROW the position — never acceptable.

    Retry policy:
      A rejected place_order is retried ONCE with the same qty/prc but a fresh
      client_order_id.  If the retry also fails, squared=False is returned with
      both reject reasons in `failures`.  No further retries; no raise.

    Lost-ack guard (double-sell window):
      A place_order that RAISED (transport error) may still have been ACCEPTED
      by the broker.  Before the retry, the order book is scanned for
      remarks == the first attempt's client_order_id (the resume_pending
      adoption pattern): a landed non-REJECTED order is ADOPTED (squared=True,
      its norenordno, note says adopted) instead of re-posted; the retry only
      happens when a READABLE book confirms nothing landed (or it landed
      REJECTED).  An unreadable book → squared=False with
      reason='cancel_unconfirmed' and NO retry — never a blind same-qty
      re-post next to a possible live ghost.
    """
    failures: List[str] = []
    tsym = position.get("tsym", "")
    exch = position.get("exch", "NFO")

    # Seed ids: the legacy single working_norenordno PLUS an optional list of all
    # resting orders for the scrip (entry remainder + any protective SL).
    seed_ids: List[str] = []
    w1 = position.get("working_norenordno")
    if w1:
        seed_ids.append(w1)
    for x in (position.get("working_norenordnos") or []):
        if x:
            seed_ids.append(x)

    # ------------------------------------------------------------------
    # Step 1 — parse filled netqty (cheap, no side effects)
    # ------------------------------------------------------------------
    raw_netqty = position.get("netqty", 0)
    netqty = _parse_netqty(raw_netqty)

    if netqty is None:
        # Unparseable netqty — we cannot safely exit without knowing qty.
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "unpriced",
            "note": f"netqty '{raw_netqty}' could not be parsed",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 2 — entry never filled: cancel the working remainder, no exit needed.
    # ------------------------------------------------------------------
    if netqty == 0:
        state = await _cancel_all_working_for_scrip(client, tsym, seed_ids)
        if not state["cleared"]:
            return {
                "squared": False,
                "via": None,
                "norenordno": None,
                "reason": "cancel_unconfirmed",
                "note": "could not cancel the unfilled entry order(s) for the scrip",
                "failures": state["remaining"],
            }
        return {
            "squared": True,
            "via": "cancel",
            "norenordno": None,
            "reason": reason,
            "note": "no position",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 3 — validate ref price (lp) BEFORE touching working orders.
    # If we cannot price the exit, do NOT cancel a protective SL we can't replace.
    # ------------------------------------------------------------------
    ref_raw = position.get("lp")

    try:
        ref = float(ref_raw)
        ref_ok = math.isfinite(ref) and ref > 0
    except (TypeError, ValueError):
        ref = float("nan")
        ref_ok = False

    if not ref_ok:
        # Bad ref price — NEVER silently skip; the engine must be alerted.
        # No cancel: an unpriced position keeps whatever protection it has.
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "unpriced",
            "note": f"lp={ref_raw!r} is missing, non-finite, or ≤ 0",
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Step 3.5 — FRESH netqty re-confirm (NEVER square a stale netqty).
    # The guard reads the position book once per cycle. If the resting OCO fired
    # between that read and this square, squaring the stale (passed) netqty would
    # place a SECOND sell → naked short. cancel_oco cannot stop an already-
    # triggered OCO. So we re-read the book immediately before placing and abort
    # if THIS tsym is now flat.
    #   * A NON-EMPTY book whose row for this tsym has netqty 0/absent → flat:
    #     return already_flat, place NO order.
    #   * An EMPTY book ([], a broker Not_Ok/hiccup) or a raising book → "unknown":
    #     do NOT treat as flat — fall through to the existing path unchanged
    #     ("unknown" must never trigger a false already-flat).
    # ------------------------------------------------------------------
    state_35, fresh_netqty = await _fresh_book_state(client, tsym)
    if state_35 == "flat":
        return {
            "squared": True,
            "via": "already_flat",
            "norenordno": None,
            "reason": reason,
            "note": "position already flat (no order placed)",
            "failures": [],
        }
    if state_35 == "live" and fresh_netqty != netqty:
        # PARTIAL reduction (e.g. the resting OCO filled PART of the leg between the
        # caller's read and now): the exit MUST sell only what is CURRENTLY held —
        # squaring the caller's larger stale netqty would place a naked short. Clamp
        # to the fresh book truth (also corrects the SELL/BUY direction if the sign
        # flipped). The guard's retry loop makes this hand-off window more
        # reachable, so the clamp is the safety net.
        log.warning(
            "square_position: netqty for %s changed %s → %s between read and "
            "place (partial fill / drift) — squaring the CURRENT qty, not the "
            "stale one", tsym, netqty, fresh_netqty)
        netqty = fresh_netqty
    # state_35 == "unknown" (raising/empty book, or a row with UNPARSEABLE netqty)
    # → fall through UNCHANGED: unknown is never coerced to flat (no false
    # already_flat) and never used to resize (no clamp off garbage).

    # ------------------------------------------------------------------
    # Step 3.6 — DEPTH-AWARE square price (C3): refresh the reference price from
    # a FRESH GetQuotes when a contract token is available.  The marketable limit
    # is priced off `ref`, which defaults to position["lp"] — a possibly-stale
    # mark.  A fresh quote makes the exit clear instead of resting away from the
    # real market.  GATED on position.get("token") so the existing token-less
    # fixtures (and the token-less paper/legacy path) are byte-identical.  Runs
    # AFTER the B4 netqty re-confirm (the position is confirmed still non-flat /
    # unknown) and BEFORE the marketable-limit is computed.  Any failure (raise,
    # empty/Not_Ok payload, or a non-finite/≤0 lp) falls back to position["lp"].
    # ------------------------------------------------------------------
    token = position.get("token")
    if token and hasattr(client, "get_quotes"):
        try:
            q = await client.get_quotes(position.get("exch", "NFO"), token)
        except Exception:
            q = None  # quotes unavailable — keep the position lp
        if isinstance(q, dict):
            try:
                q_lp = float(q.get("lp"))
                if math.isfinite(q_lp) and q_lp > 0:
                    ref = q_lp  # fresh, usable mark → price the exit off it
            except (TypeError, ValueError):
                pass  # non-numeric lp → keep the position lp

    # ------------------------------------------------------------------
    # Step 4 — MARGIN-SAFE cancel: clear ALL working orders for the scrip and
    # confirm they are terminal BEFORE placing the exit.  A resting SL left
    # working would make the exit a naked short → margin reject (₹2.16L bug).
    # ------------------------------------------------------------------
    state = await _cancel_all_working_for_scrip(client, tsym, seed_ids)
    if not state["cleared"]:
        return {
            "squared": False,
            "via": None,
            "norenordno": None,
            "reason": "cancel_unconfirmed",
            "note": (
                "working orders remain for the scrip after cancel; refusing to "
                "place the exit to avoid a naked-short margin reject — operator "
                "must clear them"
            ),
            "failures": state["remaining"],
        }

    # ------------------------------------------------------------------
    # Step 4.5 — POST-CANCEL netqty re-confirm. A working order (a triggered OCO
    # leg, a resting guard exit) can FILL inside the cancel window — the cancel
    # confirm sees it terminal (COMPLETE is terminal!) and reports cleared, but
    # the position it filled against is now smaller or GONE. Placing the exit off
    # the pre-cancel qty would be a full-qty SELL against a flat book → a naked
    # short. So the sizing decision is re-taken from a read AFTER the cancels are
    # confirmed terminal: flat → already_flat (place NOTHING); live → clamp to
    # the current truth; unknown (raise/empty/garbage) → status-quo fall-through
    # with the pre-cancel qty (never coerce unknown to flat; the place path
    # validates, and the guard/caller retries on failure).
    # ------------------------------------------------------------------
    state_45, post_netqty = await _fresh_book_state(client, tsym)
    if state_45 == "flat":
        return {
            "squared": True,
            "via": "already_flat",
            "norenordno": None,
            "reason": reason,
            "note": "position went flat during the cancel window (no order placed)",
            "failures": [],
        }
    if state_45 == "live" and post_netqty != netqty:
        log.warning(
            "square_position: netqty for %s changed %s → %s during the cancel "
            "window (a working order filled) — squaring the CURRENT qty",
            tsym, netqty, post_netqty)
        netqty = post_netqty

    # ------------------------------------------------------------------
    # Step 5 — build and place a marketable-limit exit
    # Direction MUST be correct:
    #   long  (netqty > 0) → SELL
    #   short (netqty < 0) → BUY
    # ------------------------------------------------------------------
    trantype = "S" if netqty > 0 else "B"
    qty = abs(netqty)
    prc = _marketable_prc(ref, trantype, band_pct)

    async def _try_place(cid: str) -> "OrderResult":  # type: ignore[name-defined]  # noqa: F821
        intent = OrderIntent(
            client_order_id=cid,
            trantype=trantype,
            prctyp="LMT",
            exch=exch,
            tsym=tsym,
            qty=qty,
            prc=prc,
            prd=(position.get("prd") or "I"),
            ret="DAY",
            trgprc=None,
            remarks=cid,
        )
        return await client.place_order(intent)

    # First attempt
    cid1 = new_client_order_id()
    raised_first = False
    try:
        result = await _try_place(cid1)
    except Exception as exc:
        failures.append(str(exc))
        result = None  # type: ignore[assignment]
        raised_first = True

    if result is not None and result.ok:
        return {
            "squared": True,
            "via": "exit_order",
            "norenordno": result.norenordno,
            "qty": qty,
            "reason": reason,
            "note": None,
            "failures": [],
        }

    # Record first failure
    if result is not None and not result.ok:
        failures.append(result.rejreason or "place_order returned ok=False")

    if raised_first:
        # LOST-ACK GUARD (double-sell window): the first place RAISED — a
        # transport error (httpx timeout) where the broker may still have
        # ACCEPTED the order. Re-posting blind next to that ghost exit fills
        # twice → naked short. Resolve against the order book first
        # (remarks == cid, the resume_pending adoption pattern):
        #   found & not REJECTED → ADOPT it (working or filled, it IS our
        #     exit; the guard's confirmed-flat finalize owns the rest);
        #   found REJECTED → no ghost can fill (fillshares 0) → retry ok;
        #   absent from a READABLE book → never landed → retry ok;
        #   unreadable → cannot know → NO retry (fail closed). The caller
        #     retries next cycle; its cancel-all discovery absorbs any ghost
        #     once the book is readable again.
        verdict, ghost = await _scan_order_by_remarks(client, cid1)
        if (verdict == "found"
                and _normalize_status(ghost.get("status")) not in _REJECTED_STATUSES):
            return {
                "squared": True,
                "via": "exit_order",
                "norenordno": ghost.get("norenordno"),
                "qty": qty,
                "reason": reason,
                "note": ("adopted in-flight exit (place raised after the "
                         "broker accepted it — lost ack)"),
                "failures": failures,
            }
        if verdict == "unreadable":
            return {
                "squared": False,
                "via": None,
                "norenordno": None,
                "reason": "cancel_unconfirmed",
                "note": ("exit place raised (transport) and the order book is "
                         "unreadable — cannot verify whether the exit landed; "
                         "refusing a blind retry (double-sell risk)"),
                "failures": failures,
            }

    # Retry once with a fresh client_order_id (same qty/prc)
    try:
        result2 = await _try_place(new_client_order_id())
    except Exception as exc2:
        failures.append(str(exc2))
        result2 = None  # type: ignore[assignment]

    if result2 is not None and result2.ok:
        return {
            "squared": True,
            "via": "exit_order",
            "norenordno": result2.norenordno,
            "qty": qty,
            "reason": reason,
            "note": "placed on retry",
            "failures": failures,
        }

    # Retry also failed — record and return squared=False (never silently leave open)
    if result2 is not None and not result2.ok:
        failures.append(result2.rejreason or "retry place_order returned ok=False")

    return {
        "squared": False,
        "via": None,
        "norenordno": None,
        "reason": reason,
        "note": "exit rejected twice; operator intervention required",
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# 4. reprice_exit_leg — Layer 2 OVER-SELL-SAFE widening re-price of ONE resting
#    (unfilled) guard exit. Distinct from square_position: square_position sizes
#    off the PASSED netqty (never re-read post-cancel) and relies on order_book
#    discovery to cancel — both over-sell/double-sell races when re-invoked on a
#    still-open position. This primitive cancels the TRACKED prior order, re-reads
#    ITS fillshares, and places ONLY the confirmed remaining qty at a bid-anchored,
#    circuit-clamped price (kill_switch._leg_price). Mirrors panic_squareoff_verified's
#    per-leg logic for one leg, non-blocking (the guard drives the cadence).
# ---------------------------------------------------------------------------

async def reprice_exit_leg(
    client: Any,
    position: Dict[str, Any],
    *,
    band_pct: float,
    prev_ordno: Optional[str],
    prev_qty: int,
    reason: str,
) -> Dict[str, Any]:
    """Cancel the tracked resting exit and re-place the confirmed remaining qty at a
    wider marketable band. NEVER raises. NEVER over-sells.

    Per-tsym-serialized like ``square_position``: a re-price is an EXIT PATH, so it
    claims the tsym's exit lock first. Without it, a kill switch (which claims every
    open tsym, then cancels + re-places exits) racing a guard re-price could each
    place a SELL for the full remaining qty — a naked short. On contention it
    returns ``{"squared": False, "reason": "exit_in_flight_elsewhere"}`` and the
    guard retries at the SAME band next interval.

    Result dict (the guard's ``_reprice`` classifies on these):
      • ``{"squared": False, "reason": "unpriced"}`` — no usable anchor (no quote AND
        no lp); NOTHING was cancelled or placed (the prior exit still rests).
      • ``{"squared": False, "reason": "exit_in_flight_elsewhere"}`` — another exit
        path holds this tsym's claim; NOTHING was cancelled or placed.
      • ``{"squared": False, "reason": "cancel_unconfirmed"}`` — could not confirm the
        prior exit is terminal, or could not read the fill count to size safely →
        placed NOTHING (over-sell-safe).
      • ``{"squared": True, "via": "already_flat", "remaining": 0}`` — the position
        filled in the cancel window; nothing to place.
      • ``{"squared": True, "via": "exit_order", "norenordno": …, "qty": remaining}`` —
        a fresh marketable LMT was placed for the confirmed remaining qty (a
        ``note`` of "adopted …" means the place RAISED but had landed at the
        broker and was adopted via remarks==cid instead of re-posted).
      • ``{"squared": False, "reason": "cancel_unconfirmed", "failures": [...]}`` —
        the place RAISED and the order book is unreadable: whether it landed is
        unknowable, so NOTHING more was placed (lost-ack double-sell guard).
      • ``{"squared": False, "failures": [...]}`` — the place rejected twice.

    Direction is the position's own sign (long option → SELL). ``prev_qty`` is the qty
    the prior exit was placed for; the true remaining is ``prev_qty − fillshares``,
    additionally floored by the broker position book when it is a KNOWN (non-empty)
    read — never sell more than the account actually holds.
    """
    _tsym_key = str(position.get("tsym", "") or "")
    async with claim_exit(_tsym_key, label=reason) as got:
        if not got:
            log.warning("reprice_exit_leg: exit for %s already in flight on another "
                        "path — skipping (reason=%s)", _tsym_key, reason)
            return {"squared": False, "reason": "exit_in_flight_elsewhere"}
        return await _reprice_exit_leg_impl(
            client, position, band_pct=band_pct, prev_ordno=prev_ordno,
            prev_qty=prev_qty, reason=reason)


async def _reprice_exit_leg_impl(
    client: Any,
    position: Dict[str, Any],
    *,
    band_pct: float,
    prev_ordno: Optional[str],
    prev_qty: int,
    reason: str,
) -> Dict[str, Any]:
    tsym = position.get("tsym", "")
    exch = position.get("exch", "NFO")
    prd = str(position.get("prd") or "I")
    token = str(position.get("token") or "") or None

    pos_netqty = _parse_netqty(position.get("netqty")) or 0
    trantype = "S" if pos_netqty > 0 else "B"
    tick = _pos_float(position.get("ti")) or 0.05
    ref = _pos_float(position.get("lp"))

    # ── Step A — compute the price BEFORE any cancel: never strip a protective exit
    # we cannot replace. A fresh GetQuotes gives the bid/ask anchor + circuit band. ──
    quote: Dict[str, Any] = {}
    if token and hasattr(client, "get_quotes"):
        try:
            quote = (await client.get_quotes(exch, token)) or {}
        except Exception:
            quote = {}
    prc = _leg_price(pos_netqty, ref, band_pct, tick, quote)
    if prc is None:
        return {"squared": False, "reason": "unpriced"}

    # ── Step B — cancel the TRACKED prior exit (+ any other working order for the
    # scrip) and CONFIRM none non-terminal remain. Unconfirmed → place nothing. ──
    seed = [prev_ordno] if prev_ordno else []
    state = await _cancel_all_working_for_scrip(client, tsym, seed)
    if not state["cleared"]:
        return {"squared": False, "reason": "cancel_unconfirmed"}

    # ── Step C — size to the CONFIRMED remaining qty (the over-sell guard). ──
    filled: Optional[int] = None
    if prev_ordno:
        row = await _order_row(client, prev_ordno)
        # The prior exit MUST be readable AND TERMINAL before we place a new one.
        # ``_cancel_all_working_for_scrip`` reports ``cleared`` OPTIMISTICALLY when its
        # confirm re-fetch can't verify (order_book raises / returns [] on a broker
        # Not_Ok blip), so a resting prior exit can survive a "cleared". Trusting only
        # ``cleared`` + fillshares would then STACK a second resting SELL on top of the
        # live prior one → a naked options short. Require terminal status here (mirrors
        # kill_switch.panic_squareoff_verified); else place NOTHING (cancel_unconfirmed).
        if row is None or _normalize_status(row.get("status")) not in TERMINAL:
            return {"squared": False, "reason": "cancel_unconfirmed"}
        filled = _parse_netqty(row.get("fillshares"))

    try:
        pbook = await client.position_book()
    except Exception:
        pbook = None
    book_known = isinstance(pbook, list) and len(pbook) > 0
    book_netqty: Optional[int] = None
    if book_known:
        for p in pbook:
            if str(p.get("tsym", "")) == str(tsym):
                book_netqty = _parse_netqty(p.get("netqty"))
                break
        if book_netqty is None:
            book_netqty = 0  # absent from a complete book → flat

    if prev_ordno:
        if filled is None:
            # Cannot read the prior order's fill count post-cancel → cannot size
            # safely → place NOTHING (over-sell-safe). Retry next interval.
            return {"squared": False, "reason": "cancel_unconfirmed"}
        if book_known:
            remaining = min(int(prev_qty) - int(filled), abs(int(book_netqty)))
        else:
            # FAIL-CLOSED: without a KNOWN book, fillshares bound only the TRACKED
            # order — a fill by any OTHER order in the window (a triggered OCO leg
            # the cancel-all just cancelled after a partial fill) is invisible, so
            # `prev_qty − filled` can exceed what the account still holds → a
            # naked short. Place NOTHING; the guard retries at the same band next
            # interval, by which time the book is readable again.
            return {"squared": False, "reason": "cancel_unconfirmed"}
    else:
        # No tracked prior order → size off the KNOWN book only (else unknown → hold).
        if not book_known:
            return {"squared": False, "reason": "cancel_unconfirmed"}
        remaining = abs(int(book_netqty))

    if remaining <= 0:
        return {"squared": True, "via": "already_flat", "remaining": 0}

    # ── Step D — place the marketable LMT for the confirmed remaining; retry once. ──
    failures: List[str] = []

    async def _try_place(cid: str) -> "OrderResult":  # type: ignore[name-defined]  # noqa: F821
        intent = OrderIntent(
            client_order_id=cid, trantype=trantype, prctyp="LMT",
            exch=exch, tsym=tsym, qty=remaining, prc=prc, prd=prd,
            ret="DAY", trgprc=None, remarks=cid,
        )
        return await client.place_order(intent)

    cid1 = new_client_order_id()
    raised_first = False
    try:
        result = await _try_place(cid1)
    except Exception as exc:
        failures.append(str(exc))
        result = None  # type: ignore[assignment]
        raised_first = True
    if result is not None and result.ok:
        return {"squared": True, "via": "exit_order",
                "norenordno": result.norenordno, "qty": remaining}
    if result is not None and not result.ok:
        failures.append(result.rejreason or "place_order returned ok=False")

    if raised_first:
        # LOST-ACK GUARD (double-sell window): the raised place may have LANDED
        # (httpx timeout where the broker processed the POST). Re-posting the
        # remaining qty next to that live ghost fills twice → naked short.
        # Resolve via remarks==cid (resume_pending adoption pattern): adopt a
        # landed order; retry only when a READABLE book confirms it never
        # landed (or it landed REJECTED — fillshares 0 by definition); place
        # NOTHING while the book is unreadable (the guard retries next
        # interval and this primitive's cancel-all absorbs any ghost then).
        verdict, ghost = await _scan_order_by_remarks(client, cid1)
        if (verdict == "found"
                and _normalize_status(ghost.get("status")) not in _REJECTED_STATUSES):
            return {"squared": True, "via": "exit_order",
                    "norenordno": ghost.get("norenordno"), "qty": remaining,
                    "note": ("adopted in-flight exit (place raised after the "
                             "broker accepted it — lost ack)")}
        if verdict == "unreadable":
            return {"squared": False, "reason": "cancel_unconfirmed",
                    "note": ("exit place raised (transport) and the order book "
                             "is unreadable — cannot verify whether the exit "
                             "landed; refusing a blind retry (double-sell risk)"),
                    "failures": failures}

    try:
        result2 = await _try_place(new_client_order_id())
    except Exception as exc2:
        failures.append(str(exc2))
        result2 = None  # type: ignore[assignment]
    if result2 is not None and result2.ok:
        return {"squared": True, "via": "exit_order",
                "norenordno": result2.norenordno, "qty": remaining, "note": "placed on retry"}
    if result2 is not None and not result2.ok:
        failures.append(result2.rejreason or "retry place_order returned ok=False")

    return {"squared": False, "failures": failures}
