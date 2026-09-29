"""Transient-safe reboot reconciliation for the live execution path.

When the PC / backend is down and a resting OCO fires (or a position is closed
externally), the in-memory state is lost and two holes open up:

  1. the ``live_trades`` doc for that position stays ``status="OPEN"`` forever —
     so realized P&L is never journaled, ``max_concurrent`` over-counts, and the
     ``daily_loss_cap`` is blind to the realized loss;
  2. the resting OCO that fired (one leg) may leave its *other* leg dangling, or
     a still-resting OCO for an already-closed position is orphaned at the broker.

``reconcile_on_startup`` closes both, run once on boot.

CRITICAL SAFETY — the empty-book false-close hole
--------------------------------------------------
An UNREADABLE ``position_book`` must be treated as **UNKNOWN**, never "flat". If we
treated it as flat, EVERY open position would look closed and get false-closed, and
EVERY resting OCO would get false-cancelled — catastrophically removing the
broker-side backstop while the position is in fact still live.

An EMPTY book is narrower. ``FlattradeClient._parse_book`` returns ``[]`` ONLY for
Noren's "no data" and RAISES on any real failure, so an empty book is evidence of
flat — but still not enough on its own. It must be CONFIRMED by a second
authenticated empty read (the guard's ``flat_confirm_reads = 2``), and even then it
closes only docs entered on an EARLIER IST day (a same-day doc's own entry fill
would be in the book, so an empty book beside it is a contradiction → UNKNOWN). No
resting OCO is ever cancelled on an empty book. Without this, a position squared
while the PC was down stayed OPEN forever on a flat account (2026-09-16 → 09-27).

When the book cannot settle the question at all, only calendar proof is used: an
OPEN doc on a contract whose expiry date has PASSED is closed, without a price.

CRITICAL SAFETY — match the exit fill to THE entry
--------------------------------------------------
We never pick "the newest SELL by tsym": a same-strike re-entry would leave stale
fills that point at the wrong exit price. We match the OCO's ``remarks`` tag
(``oco:<entry_norenordno>``) first; otherwise only SELLs PROVEN to follow this
entry are used (``_proven_exit_sells`` — own fills present, account flat on the
contract at entry, no other order interleaved), summed to exactly the position
size. Ambiguous → we close the doc but never fabricate a price.

The function NEVER raises — every phase is wrapped, and a failure in one phase
does not abort the other.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.live.close_loop import close_live_trade, realized_fields
from app.live.kill_switch import _parse_netqty
from app.live_marks import expired_before

log = logging.getLogger(__name__)

_OCO_REMARKS_PREFIX = "oco:"


def _finite(v: Any) -> Optional[float]:
    """float(v) if finite, else None (guards None / '' / NaN / inf)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _open_tsyms(book: List[Dict[str, Any]]) -> Dict[str, bool]:
    """{ tsym: True } for every book row whose parsed netqty != 0 (still held).

    A row with netqty 0 / absent / unparseable is NOT counted as held; the caller
    treats a tsym missing from this map as flat — which is safe ONLY because the
    book was already confirmed to be a non-empty list before we got here.
    """
    held: Dict[str, bool] = {}
    for row in book:
        if not isinstance(row, dict):
            continue
        tsym = row.get("tsym")
        if not tsym:
            continue
        nq = _parse_netqty(row.get("netqty", 0))
        if nq:  # non-zero (and not None) → held
            held[str(tsym)] = True
    return held


def _parse_oco_norenordno(remarks: Any) -> Optional[str]:
    """Extract ``<norenordno>`` from a ``oco:<norenordno>`` remarks tag."""
    if not isinstance(remarks, str):
        return None
    s = remarks.strip()
    # The broker DECORATES our tag rather than echoing it. A real fired leg reads
    #   "LMT_BOS_O: oco:26081400076294: Ltp 61.95 is above 21.45: oco:26081400076294"
    # i.e. "<ai_t>: <our remarks>: <trigger desc>" (2026-08-14 order book; the June
    # readback in gtt.py shows the same shape). Requiring startswith() meant NO real
    # fired leg ever parsed, so an OCO exit was never attributed to its entry and
    # realized_pnl stayed null. Search anywhere, and take the FIRST occurrence so a
    # trailing repeat cannot change the answer.
    idx = s.find(_OCO_REMARKS_PREFIX)
    if idx < 0:
        return None
    tail = s[idx + len(_OCO_REMARKS_PREFIX):].strip()
    # The id ends at the broker's next delimiter — it is not the rest of the string.
    for sep in (":", " ", ","):
        cut = tail.find(sep)
        if cut > 0:
            tail = tail[:cut]
    return tail.strip() or None


# Noren fill-timestamp fields, most to least authoritative, each with the layout
# the decoded API docs show for it: TradeBook `fltm` is DATE-first; `exch_tm` is
# TIME-first in the TradeBook sample but DATE-first in the WebSocket order update
# and the postback (endpoints 13, 49, 55). The layouts are mutually exclusive, so
# no string can parse as the wrong one.
#
# `norentm` is deliberately ABSENT. It is the ORDER-ENTRY time, not a fill time:
# every partial fill of one order shares it, and a resting SELL placed before a
# foreign BUY but filled after it would sort the wrong way round — exactly the
# interleave the proof below exists to see.
_FILL_TIME_FIELDS = (
    ("fltm", "%d-%m-%Y %H:%M:%S"),
    ("exch_tm", "%H:%M:%S %d-%m-%Y"),
    ("exch_tm", "%d-%m-%Y %H:%M:%S"),
)


def _fill_time(row: Dict[str, Any]) -> Optional[datetime]:
    """Parse a Noren fill timestamp to a naive (IST) datetime, or None.

    Used to ORDER fills on one contract within one day — every row comes from the
    same broker clock — and to date a proven exit.

    Noren fills an unset time with a placeholder: the vendor's own TradeBook sample
    carries ``fltm "01-01-1980 00:00:00"`` and ``exch_tm "00:00:00 01-01-1980"``.
    That parses cleanly, and a fill dated 1980 sorts before every real fill — an
    exit would appear to precede its own entry. Any year before 2000 is therefore
    treated as ABSENT and the next field is tried.
    """
    for field, fmt in _FILL_TIME_FIELDS:
        raw = str(row.get(field) or "").strip()
        if not raw:
            continue
        try:
            t = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if t.year < 2000:
            continue
        return t
    return None


def _fill_qty(row: Dict[str, Any]) -> Optional[float]:
    """FILLED quantity of one trade-book row (``flqty``), or None.

    Never the ORDER quantity (``qty``): a partially-filled order's row would then be
    counted at full size, and one partial would appear to complete an exit.
    """
    q = _finite(row.get("flqty"))
    return q if q is not None and q > 0 else None


def _proven_round_trip(
    trade_book: List[Dict[str, Any]], *, norenordno: str, tsym: str,
) -> Optional[Dict[str, Any]]:
    """THIS entry's own fills and the SELLs PROVEN to close it — or None.

    Returns ``{"entry_qty", "entry_px", "sells": [{"t", "qty", "px"}, ...]}``.

    The broker nets a contract's fills into one position, so a SELL carries no
    record of which BUY it closed. It can only be attributed by showing that no
    other position on the contract existed to claim it. All must hold:

      1. this entry's OWN fills are in the book (they carry its ``norenordno``),
         are all BUYs, and are all priced. The book is DAY-SCOPED, so this is also
         the proof the entry was made TODAY — a doc from an earlier day can never
         borrow today's fills;
      2. every fill on this contract is orderable (parseable fill time, filled
         qty, side) and all share ONE product: an MIS long and an NRML short on one
         symbol net to zero while two positions are open;
      3. the account was FLAT on this contract when the entry began — the net of
         every earlier fill on it is zero — so no older position was open to own a
         later SELL (two deployments, or an MCP-placed position, on one strike);
      4. nothing else touched the contract while the entry was filling, and the
         candidates STOP at the first BUY from any other order: a SELL after that
         could belong to the newcomer.

    A SELL tagged ``oco:<another entry>`` in the window is positive proof of a
    second position, so the whole answer is refused, not just that row.

    What this CANNOT see is a position CARRIED FORWARD from an earlier day — the
    book is day-scoped. Callers establish that from the position book
    (``_carry_free``) before trusting the answer.

    The ENTRY basis is returned too, and callers use it in preference to the doc's
    ``entry_fill_price``: that field is the broker's ``daybuyavgprc``, which BLENDS
    every entry on the contract that day, so a second same-day entry on a strike
    would otherwise be measured from the wrong price.
    """
    own = str(norenordno)
    same: List[tuple] = []
    for row in trade_book:
        if not isinstance(row, dict) or row.get("tsym") != tsym:
            continue
        t, q, side = _fill_time(row), _fill_qty(row), row.get("trantype")
        if t is None or q is None or side not in ("B", "S"):
            return None                                        # (2)
        same.append((t, row, q, side))
    if len({str(row.get("prd") or "") for _t, row, _q, _s in same}) > 1:
        return None                                            # (2) products
    mine = [(t, row, q, side) for t, row, q, side in same
            if str(row.get("norenordno") or "") == own]
    if not mine:
        return None                                            # (1)
    if any(side != "B" or _finite(row.get("flprc")) is None
           for _t, row, _q, side in mine):
        return None                                            # (1)
    entry_qty = sum(q for _t, _row, q, _s in mine)
    entry_px = sum(q * _finite(row.get("flprc")) for _t, row, q, _s in mine) / entry_qty
    first = min(t for t, _row, _q, _s in mine)
    last = max(t for t, _row, _q, _s in mine)
    foreign = [(t, row, q, side) for t, row, q, side in same
               if str(row.get("norenordno") or "") != own]
    pre_net = sum(q if side == "B" else -q
                  for t, _row, q, side in foreign if t < first)
    if pre_net != 0:
        return None                                            # (3)
    if any(first <= t < last or (t == last and side == "B")
           for t, _row, _q, side in foreign):
        return None                                            # (4) mid-entry
    sells: List[Dict[str, Any]] = []
    # At an equal timestamp a BUY sorts first, so a same-second newcomer stops the
    # pool BEFORE a SELL it might own — the conservative reading of a tie.
    for t, row, q, side in sorted((f for f in foreign if f[0] >= last),
                                  key=lambda f: (f[0], f[3] != "B")):
        if side == "B":
            break                                              # (4) newcomer
        if _parse_oco_norenordno(row.get("remarks")) not in (None, own):
            return None
        sells.append({"t": t, "qty": q, "px": _finite(row.get("flprc"))})
    return {"entry_qty": entry_qty, "entry_px": entry_px, "sells": sells}


def _weighted_exit(sells: List[Dict[str, Any]],
                   qty_target: float) -> Optional[Tuple[float, Optional[datetime]]]:
    """(quantity-weighted price, time of the last fill used) of the leading SELLs
    that sum to EXACTLY ``qty_target`` — or None.

    Short means the exit is incomplete; over means the SELL that crossed the target
    was only partly this trade's. Neither is provably this trade's exit.

    The price is NOT rounded here: rounding a weighted average and then
    multiplying by the quantity drifts from the broker's own ``rpnl`` by up to
    qty × 0.005. Only the journalled money is rounded (``realized_fields``).
    """
    total = 0.0
    weighted = 0.0
    last_t: Optional[datetime] = None
    for s in sells:
        if s["px"] is None:
            return None  # an unpriced row refuses the whole aggregate
        total += s["qty"]
        weighted += s["qty"] * s["px"]
        last_t = s.get("t")
        if total >= qty_target:
            break
    if total == qty_target and total > 0:
        return weighted / total, last_t
    return None


def _match_close(
    trade_book: List[Dict[str, Any]], *, norenordno: str, tsym: str,
    carry_proven: bool, quantity: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Everything provable about how THIS entry was closed, from today's book.

    ``carry_proven`` — REQUIRED, no default — says whether the caller has shown
    that no position on the contract was carried forward from an earlier day
    (``_carry_free`` on the live position book, or an operator attestation for
    recorded fills). Without it only a TAGGED exit is accepted: the tag, and the
    entry's own order number, prove ownership whatever else was open; an untagged
    SELL could have closed the carried position instead.

    Returns ``{"exit_px", "entry_px", "qty", "exit_at"}`` or None. ``entry_px`` /
    ``qty`` are None when only the exit is provable (the tagged-OCO path without
    this entry's own fills), and the caller then falls back to the doc's own.

    1. TAGGED — SELLs tagged ``oco:<norenordno>``. The tag is proof of ownership.
       One tagged row: its price. Several (an OCO leg filled in parts): their
       quantity-weighted average, summing exactly to the proven entry quantity (or,
       without own fills, the doc's ``quantity``). Effectively dormant while
       ``LIVE_BROKER_OCO_ENABLED`` is off (2026-09-03) — no OCO leg exists to tag.
    2. PROVEN — the SELLs ``_proven_round_trip`` attributes to this entry, summing
       exactly to the entry's OWN filled quantity (never the ordered quantity: an
       entry that filled 60 of 100 is a 60-lot position).

    Anything else returns None and the caller closes without a price — we never
    fabricate one.

    WHY (2) EXISTS. 2026-09-16, a real 5-lot SENSEX 74300 PE exited as two fills,
    60 @ 361.00 and 40 @ 361.00, one second apart. The old single-SELL rule found
    two SELLs and returned None — so the trade closed with ``realized_pnl = null``
    and the Live Deployments pane showed ₹0 for a round trip the broker had booked
    at ₹822. Partial fills are ordinary, not an edge case: any order large enough
    to sweep more than one price level produces them.

    The old lone-SELL fallback is gone: it accepted any single same-symbol SELL
    with no evidence the entry traded today, so a doc left OPEN from an earlier
    day would have borrowed an unrelated round trip's exit. Every case it resolved
    correctly is a case (2) resolves with proof.
    """
    own = str(norenordno)
    rt = _proven_round_trip(trade_book, norenordno=own, tsym=tsym)
    tagged = [row for row in trade_book
              if isinstance(row, dict) and row.get("tsym") == tsym
              and row.get("trantype") == "S"
              and _parse_oco_norenordno(row.get("remarks")) == own]
    if tagged:
        if len(tagged) == 1:
            exit_px = _finite(tagged[0].get("flprc"))
            exit_at = _fill_time(tagged[0])
        else:
            target = rt["entry_qty"] if rt else _finite(quantity)
            parts = [{"t": _fill_time(r), "qty": _fill_qty(r),
                      "px": _finite(r.get("flprc"))} for r in tagged]
            if target is None or any(p["qty"] is None for p in parts):
                return None
            got = _weighted_exit(parts, target)
            exit_px, exit_at = got if got else (None, None)
        if exit_px is None:
            return None
        return {"exit_px": exit_px, "exit_at": exit_at,
                "entry_px": rt["entry_px"] if rt else None,
                "qty": rt["entry_qty"] if rt else None}
    if not carry_proven or rt is None or not rt["sells"]:
        return None
    got = _weighted_exit(rt["sells"], rt["entry_qty"])
    if got is None:
        return None
    return {"exit_px": got[0], "exit_at": got[1],
            "entry_px": rt["entry_px"], "qty": rt["entry_qty"]}


def _match_exit_fill_price(
    trade_book: List[Dict[str, Any]], *, norenordno: str, tsym: str,
    quantity: Optional[float] = None, carry_proven: bool = False,
) -> Optional[float]:
    """The proven exit price for THIS entry, or None (see ``_match_close``)."""
    m = _match_close(trade_book, norenordno=norenordno, tsym=tsym,
                     carry_proven=carry_proven, quantity=quantity)
    return None if m is None else m["exit_px"]


def _carry_free(position_book: Any, tsym: str) -> bool:
    """True iff the position book PROVES nothing on ``tsym`` was carried forward.

    The trade book is day-scoped, so ``_proven_round_trip`` cannot see a position
    bought on an EARLIER day and still open this morning — and a SELL that closed
    it would be attributed to today's entry. The position book can: every row for
    the contract (one per product) must show zero carry-forward buy AND sell.
    No row, or an unreadable field, proves nothing → False.
    """
    if not isinstance(position_book, list):
        return False
    rows = [r for r in position_book if isinstance(r, dict) and r.get("tsym") == tsym]
    if not rows:
        return False
    for r in rows:
        for field in ("cfbuyqty", "cfsellqty"):
            if _finite(r.get(field)) != 0:
                return False
    return True


#: Order-book statuses that end an order with nothing filled (spellings per
#: ``kill_switch._TERMINAL_STATUSES``). Reached only when the trade book holds no
#: fill for the order, so a CANCELED status there means cancelled unfilled.
_TERMINAL_UNFILLED = frozenset({"REJECTED", "REJECT", "CANCELED", "CANCELLED"})


async def _entry_order_status(client: Any, norenordno: str,
                              cache: Dict[str, Any]) -> Optional[str]:
    """Upper-cased broker status of one order, from ONE order-book read per run.

    None when the book is unreadable or the order is not in it — never a guess."""
    if "book" not in cache:
        try:
            ob = await client.order_book()
            cache["book"] = ob if isinstance(ob, list) else None
        except Exception as exc:
            log.warning("reboot reconcile: order_book fetch failed: %s", exc)
            cache["book"] = None
    for row in cache["book"] or []:
        if isinstance(row, dict) and str(row.get("norenordno") or "") == norenordno:
            return str(row.get("status") or "").strip().upper() or None
    return None


async def _reconcile_open_flat(
    db: Any, client: Any, open_tsyms: Dict[str, bool],
    trade_book: Optional[List[Dict[str, Any]]] = None, *,
    position_book: Optional[List[Dict[str, Any]]] = None,
    today_ist: Optional[date] = None,
) -> Dict[str, int]:
    """Phase 2 — close OPEN docs whose position is flat at the broker.

    ``trade_book`` may be passed in so a reconcile run reads the book once;
    ``position_book`` (the non-empty book already read) is what proves a price is
    not contaminated by a carried-forward position (``_carry_free``).

    A doc entered TODAY with no fill in today's trade book is NOT flat — its entry
    order may still be working (the doc is written on broker ACCEPTANCE), and it can
    fill a second after this runs. It is left OPEN unless the order book shows it
    ended unfilled, in which case it is closed ``never_filled``.

    A doc closed here without a proven price whose entry day is BEFORE today gets
    ``exit_day_unknown``: its exit happened on some earlier, unrecorded day, and
    stamping today's ``closed_at`` on a later backfill would charge that P&L to
    TODAY's day-stop.
    """
    summary = {"closed": 0, "skipped_held": 0, "skipped_no_norenordno": 0,
               "skipped_working": 0, "never_filled": 0}
    if trade_book is None:
        try:
            trade_book = await client.trade_book()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("reboot reconcile: trade_book fetch failed: %s", exc)
            trade_book = []
    if not isinstance(trade_book, list):
        trade_book = []
    try:
        cursor = db.live_trades.find({"status": {"$ne": "CLOSED"}})
        docs = await cursor.to_list(length=None)
    except Exception as exc:
        log.warning("reboot reconcile: open-doc query failed: %s", exc)
        return summary
    order_cache: Dict[str, Any] = {}
    for doc in docs:
        try:
            norenordno = doc.get("norenordno")
            if not norenordno:
                # rehydrated / manual doc with no broker order id → cannot match.
                summary["skipped_no_norenordno"] += 1
                continue
            norenordno = str(norenordno)
            # The position book is NOREN-keyed. `trading_symbol` is the UPSTOX
            # symbol ("NIFTY 24300 PE 18 AUG 26" vs "NIFTY18AUG26P24300"), so
            # comparing it here could NEVER match: every open live trade looked
            # flat and was closed the instant the book was non-empty. On
            # 2026-08-14 that journalled a REAL open position as closed with a
            # null P&L, blinding the caps and the day-stop to it for ~30 minutes.
            # The documented empty-book guard was working perfectly and masking
            # this the entire time.
            tsym = str(doc.get("noren_tsym") or "").strip()
            if not tsym:
                # Identity unresolvable in the broker's own symbol space. That is
                # UNKNOWN, and unknown must NEVER close — the same rule the empty
                # book already follows. Falling back to `trading_symbol` here
                # would reproduce the original defect exactly.
                log.warning("reboot reconcile: %s has no noren_tsym — cannot prove "
                            "flatness in the broker's symbol space; leaving OPEN",
                            norenordno)
                summary["skipped_no_norenordno"] += 1
                continue
            if tsym in open_tsyms:
                # still held at the broker → leave OPEN.
                summary["skipped_held"] += 1
                continue
            entered = _entry_ist_date(doc)
            own_filled = any(isinstance(r, dict)
                             and str(r.get("norenordno") or "") == norenordno
                             for r in trade_book)
            if (not own_filled and today_ist is not None
                    and entered is not None and entered >= today_ist):
                status = await _entry_order_status(client, norenordno, order_cache)
                if status in _TERMINAL_UNFILLED:
                    if await close_live_trade(db, norenordno=norenordno,
                                              exit_price=None, fill_price=None,
                                              exit_reason="never_filled"):
                        summary["never_filled"] += 1
                else:
                    summary["skipped_working"] += 1
                    log.info("reboot reconcile: %s entered today has no fill yet "
                             "(order status %s) — leaving OPEN", norenordno, status)
                continue
            # Flat at the broker → close. Match the exit fill to THIS entry.
            match = _match_close(trade_book, norenordno=norenordno, tsym=tsym,
                                 carry_proven=_carry_free(position_book, tsym),
                                 quantity=doc.get("quantity"))
            exit_day_unknown = match is None and (
                entered is None or today_ist is None or entered < today_ist)
            closed = await close_live_trade(
                db,
                norenordno=norenordno,
                exit_price=None,
                fill_price=match["exit_px"] if match else None,
                entry_px=match["entry_px"] if match else None,
                quantity=match["qty"] if match else None,
                exit_reason="reconciled_closed",
                extra_fields={"exit_day_unknown": True} if exit_day_unknown else None,
            )
            if closed:
                summary["closed"] += 1
        except Exception as exc:  # one bad doc never aborts the rest
            log.warning("reboot reconcile: close of %s failed: %s",
                        doc.get("norenordno"), exc)
    return summary


def _relink_held_ocos(
    gtt_book: List[Dict[str, Any]], open_tsyms: Dict[str, bool]
) -> Dict[str, int]:
    """Phase 2.5 — re-link a still-resting OCO to a HELD rehydrated position.

    After a backend restart, ``LivePositionGuard.rehydrate_from_broker`` re-registers
    open broker positions keyed by ``tsym`` with ``oco_al_id=None`` — but the OCO
    placed at the ORIGINAL arm SURVIVES at the broker. Without a handle the guard
    cannot cancel that OCO when it later squares the rehydrated position → the OCO is
    orphaned and could misfire (an unintended SELL) on a same-strike re-entry.

    This walks the process-singleton guard registry and, for every entry whose tsym is
    HELD (in the non-empty position_book) and whose ``oco_al_id`` is falsy, looks for a
    resting OCO in ``gtt_book`` with the SAME tsym; if found it stores the al_id back
    onto the LIVE registry entry (``get_registry().get(key)["oco_al_id"] = al_id``).
    Matching is by tsym because rehydrated entries carry no norenordno and one open
    position per tsym is the norm.

    Held entries that still have NO oco_al_id after the attempt are counted as
    ``no_backstop`` (software-guard-only) and each gets a loud WARNING. An entry that
    already has an oco_al_id is left untouched. Re-link / flag are HELD-only — a flat
    tsym's entry is left to the orphan-sweep / guard drop.

    NEVER raises. Returns ``{"relinked": int, "no_backstop": int}``.
    """
    summary = {"relinked": 0, "no_backstop": 0}
    try:
        from app.live.live_position_guard import get_registry
        registry = get_registry()
        # tsym -> al_id of the first resting OCO row for that tsym.
        oco_by_tsym: Dict[str, Any] = {}
        for row in gtt_book:
            if not isinstance(row, dict):
                continue
            al_id = row.get("al_id") or row.get("Al_id")
            tsym = row.get("tsym")
            if al_id and tsym:
                oco_by_tsym.setdefault(str(tsym), al_id)
        for entry in registry.snapshot():
            try:
                tsym = str(entry.get("tsym") or "")
                if not tsym or tsym not in open_tsyms:
                    continue  # HELD-only — flat entries are not re-linked / flagged
                if entry.get("oco_al_id"):
                    continue  # already linked — never overwrite
                al_id = oco_by_tsym.get(tsym)
                if al_id:
                    # Live dict — store the handle so the guard can cancel on square.
                    live = registry.get(entry.get("id"))
                    target = live if live is not None else entry
                    target["oco_al_id"] = al_id
                    summary["relinked"] += 1
                    log.info("reboot reconcile: re-linked resting OCO %s to held "
                             "rehydrated position %s", al_id, tsym)
                else:
                    summary["no_backstop"] += 1
                    log.warning(
                        "reboot reconcile: HELD position %s has NO broker backstop "
                        "(no resting OCO) — it is SOFTWARE-GUARD-ONLY", tsym)
            except Exception as exc:  # one bad entry never aborts the rest
                log.warning("reboot reconcile: re-link of %s failed: %s",
                            entry.get("tsym"), exc)
    except Exception as exc:  # registry import / snapshot failure is non-fatal
        log.warning("reboot reconcile: re-link phase failed: %s", exc)
    return summary


async def _sweep_orphan_ocos(
    db: Any, client: Any, open_tsyms: Dict[str, bool],
    gtt_book: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Phase 3 — cancel resting OCOs whose entry is CONFIRMED gone.

    Conservative: cancel ONLY a confirmed orphan —
      * remarks-linked: the entry's ``live_trades`` doc is now CLOSED; OR
      * unlinked (no remarks / no doc): the row's tsym is flat in the NON-EMPTY book
        AND there is no OPEN ``live_trades`` doc for that tsym.
    Never cancel an OCO whose entry is still OPEN / position still held — so a HELD
    tsym's OCO (incl. one just re-linked in phase 2.5) is never swept.
    """
    summary = {"cancelled": 0, "kept": 0}
    for row in gtt_book:
        try:
            if not isinstance(row, dict):
                continue
            al_id = row.get("al_id") or row.get("Al_id")
            if not al_id:
                continue
            entry_no = _parse_oco_norenordno(row.get("remarks"))
            should_cancel = False
            if entry_no:
                # remarks-linked: orphan iff its entry doc is CLOSED.
                doc = await db.live_trades.find_one({"norenordno": entry_no})
                if doc is not None and doc.get("status") == "CLOSED":
                    should_cancel = True
            else:
                # unlinked: orphan iff tsym is flat AND no OPEN live_trade for it.
                tsym = str(row.get("tsym") or "")
                if tsym and tsym not in open_tsyms:
                    # The gtt book's tsym is NOREN-space, so the journal must be
                    # queried by `noren_tsym`. It queried the UPSTOX
                    # `trading_symbol`, which can never equal a Noren symbol — the
                    # same symbol-space confusion as the 2026-08-14 false close —
                    # so this "is anything still open on it?" check always said no.
                    open_doc = await db.live_trades.find_one(
                        {"noren_tsym": tsym, "status": {"$ne": "CLOSED"}}
                    )
                    if open_doc is None:
                        should_cancel = True
            if should_cancel:
                try:
                    await client.cancel_oco(al_id)
                    summary["cancelled"] += 1
                except Exception as exc:  # best-effort
                    log.warning("reboot reconcile: cancel_oco(%s) failed: %s",
                                al_id, exc)
            else:
                summary["kept"] += 1
        except Exception as exc:
            log.warning("reboot reconcile: OCO sweep row failed: %s", exc)
    return summary


def _write_landed(res: Any) -> bool:
    """True iff an ``update_one`` actually changed a document."""
    modified = getattr(res, "modified_count", None)
    if modified is None:  # FakeDB / drivers without modified_count
        modified = getattr(res, "matched_count", 0)
    return bool(modified)


async def _repair_missing_realized(
    db: Any, client: Any, trade_book: Optional[List[Dict[str, Any]]] = None, *,
    position_book: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Phase 2b — backfill ``realized_pnl`` on CLOSED docs that never got one.

    Fixing the matcher only helps trades closed from now on. Six of twelve live
    trades on 2026-09-16 were already CLOSED with ``realized_pnl = null``, which is
    not merely a display gap: this module exists precisely because an unjournalled
    close leaves ``daily_loss_cap`` and the day-stop blind to a realized loss.

    Deliberately NARROW. It only ever ADDS a number that was missing:

      * ``status == "CLOSED"`` and ``realized_pnl`` is null — an existing value is
        never overwritten, and the WRITE re-asserts the null so a concurrent close
        that journalled a real number wins;
      * FULL proof only: ``_match_close`` must prove both the exit AND the entry
        basis from this entry's own fills (the tagged-OCO path without own fills
        proves only the exit, and is refused here), and the position book must
        show nothing carried forward on the contract (``_carry_free``). A
        ``never_filled`` doc has no own fills, so it can never be handed an
        unrelated SELL;
      * the broker trade book is DAY-SCOPED, so only same-day trades can be
        repaired. Older rows are unreachable — their fills are gone from the
        broker and no honest price can be recovered here (see
        ``backend/scripts/backfill_realized_from_recorded_fills.py``).

    ``trade_book`` may be passed in so a reconcile run reads the book once.
    """
    out = {"repaired": 0, "unresolved": 0, "raced": 0}
    if trade_book is None:
        try:
            trade_book = await client.trade_book()
        except Exception as exc:
            log.warning("reboot reconcile: trade_book fetch failed in repair: %s", exc)
            return out
    if not isinstance(trade_book, list) or not trade_book:
        return out
    try:
        docs = await db.live_trades.find(
            {"status": "CLOSED", "realized_pnl": None}
        ).to_list(length=None)
    except Exception as exc:
        log.warning("reboot reconcile: repair query failed: %s", exc)
        return out

    for doc in docs:
        try:
            norenordno = str(doc.get("norenordno") or "")
            tsym = str(doc.get("noren_tsym") or "").strip()
            if not norenordno or not tsym:
                out["unresolved"] += 1
                continue
            match = _match_close(trade_book, norenordno=norenordno, tsym=tsym,
                                 carry_proven=_carry_free(position_book, tsym),
                                 quantity=doc.get("quantity"))
            if match is None or match["entry_px"] is None or match["qty"] is None:
                out["unresolved"] += 1
                continue
            # The SAME computation close_live_trade uses — one formula, not two.
            fields = realized_fields(doc, match["exit_px"],
                                     entry_px=match["entry_px"], quantity=match["qty"])
            if "realized_pnl" not in fields:
                out["unresolved"] += 1
                continue
            fields["realized_pnl_backfilled"] = True
            res = await db.live_trades.update_one(
                {"norenordno": norenordno, "status": "CLOSED", "realized_pnl": None},
                {"$set": fields},
            )
            if not _write_landed(res):
                out["raced"] += 1  # someone journalled it first — theirs stands
                continue
            out["repaired"] += 1
            log.info("reboot reconcile: backfilled realized_pnl=%.2f on %s (%s) "
                     "from exit fill %.4f", fields["realized_pnl"], norenordno, tsym,
                     match["exit_px"])
        except Exception as exc:  # one bad doc never aborts the rest
            log.warning("repair of %s failed: %s", doc.get("norenordno"), exc)
            out["unresolved"] += 1
    return out


# ---------------------------------------------------------------------------
# Stale OPEN docs — a position the broker closed while AlphaForge was not looking.
#
# 2026-09-16: the PC went down at 13:21 IST holding a 3-lot SENSEX 17 SEP 74400 PE;
# the operator squared it the same day. Every later restart then met either an
# expired daily token (unreadable book → UNKNOWN, correctly) or a FLAT account,
# whose position book Noren answers with "no data" → []. An empty book was treated
# as UNKNOWN as well, so the doc could never close: it sat OPEN for eleven days,
# counting toward max_concurrent on its deployment and reporting a position that
# did not exist.
#
# The empty-book rule predates the client contract that now makes the difference
# provable: `FlattradeClient._parse_book` returns [] ONLY for Noren's "no data"
# and RAISES `BrokerReadError` on any real failure (23a61c1). So a confirmed-empty
# book is evidence, and two of them in a row — the guard's own flat_confirm_reads
# rule — is proof enough to close what could not have survived to today.
#
# Every close made here is stamped `exit_day_unknown`: the exit happened on some
# earlier day nobody recorded, so the row must not count toward TODAY's realized
# P&L (`daily_realized_summary`) — least of all if a price is backfilled later.
# ---------------------------------------------------------------------------

_IST_OFFSET = timedelta(hours=5, minutes=30)

#: Seconds between the two position-book reads that confirm an empty book. The
#: guard's `flat_confirm_reads` are one guard cycle apart; two reads 100 ms apart
#: would both see the same transient (e.g. a session still warming up after
#: OAuth). Tests set it to 0.
_CONFIRM_READ_DELAY_S = 1.5

_STALE_CLOSE_FIELDS = {"exit_day_unknown": True}


def _ist_date(dt: datetime) -> date:
    """Calendar date in IST of an aware datetime (naive is taken as UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt.astimezone(timezone.utc) + _IST_OFFSET).date()


def _entry_ist_date(doc: Dict[str, Any]) -> Optional[date]:
    """IST calendar date the doc was entered, or None when it cannot be placed.

    ``created_at`` is written as an aware ISO string. A driver-returned datetime is
    naive UTC (pymongo's convention) and is accepted as such; a naive STRING has no
    stated zone and is refused rather than guessed.
    """
    raw = doc.get("created_at")
    if isinstance(raw, datetime):
        return _ist_date(raw)
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return _ist_date(dt)


async def _open_docs(db: Any) -> List[Dict[str, Any]]:
    return await db.live_trades.find({"status": {"$ne": "CLOSED"}}).to_list(length=None)


async def _close_expired_open_docs(db: Any, *, today_ist: date) -> Dict[str, int]:
    """Close OPEN docs whose CONTRACT HAS EXPIRED. Calendar proof — needs no broker.

    Runs whenever the position book is not a readable, non-empty book (that one
    is the authority: if it says HELD, nothing here overrides it). An option past
    its expiry date cannot be held, so the doc is closed — but with NO price: the
    exit fill is not knowable from here, and it is never guessed.

    The expiry comes from the broker's own symbol (``noren_tsym``) through
    ``live_marks.expired_before`` — the same rule the guard's ownership boundary
    uses. Unparseable → left alone; expiring TODAY → left alone (the contract
    still trades until the close).
    """
    out = {"closed": 0, "not_proven_expired": 0}
    for doc in await _open_docs(db):
        try:
            tsym = str(doc.get("noren_tsym") or "").strip()
            if not expired_before(tsym, today_ist.isoformat()):
                out["not_proven_expired"] += 1
                continue
            if await close_live_trade(db, norenordno=str(doc.get("norenordno") or ""),
                                      exit_price=None, fill_price=None,
                                      exit_reason="reconciled_expired",
                                      extra_fields=_STALE_CLOSE_FIELDS):
                out["closed"] += 1
                log.warning("reboot reconcile: closed %s (%s) — contract expired "
                            "before %s; exit price unknowable, realized_pnl left null",
                            doc.get("norenordno"), tsym, today_ist)
        except Exception as exc:  # one bad doc never aborts the rest
            log.warning("reboot reconcile: expiry close of %s failed: %s",
                        doc.get("norenordno"), exc)
    return out


async def _close_prior_day_open_docs(db: Any, *, today_ist: date) -> Dict[str, int]:
    """On a CONFIRMED-flat account, close OPEN docs entered on an EARLIER IST day.

    A doc entered TODAY while the book is empty is a contradiction — its entry
    order may still be WORKING (the doc is written on broker acceptance) and can
    fill a second later — so it is left OPEN and counted in ``same_day``; an
    undatable doc likewise in ``undated``. The caller must treat either count as
    UNKNOWN, not as a completed recovery.
    """
    out = {"closed": 0, "same_day": 0, "undated": 0}
    for doc in await _open_docs(db):
        try:
            entered = _entry_ist_date(doc)
            if entered is None:
                out["undated"] += 1
                continue
            if entered >= today_ist:
                out["same_day"] += 1
                log.warning("reboot reconcile: %s entered today but the position book "
                            "is empty — contradiction, leaving OPEN",
                            doc.get("norenordno"))
                continue
            if await close_live_trade(db, norenordno=str(doc.get("norenordno") or ""),
                                      exit_price=None, fill_price=None,
                                      exit_reason="reconciled_flat_prior_day",
                                      extra_fields=_STALE_CLOSE_FIELDS):
                out["closed"] += 1
                log.warning("reboot reconcile: closed %s (%s) — entered %s, account "
                            "confirmed flat; exit fill is gone from the day-scoped "
                            "trade book, realized_pnl left null",
                            doc.get("norenordno"), doc.get("noren_tsym"), entered)
        except Exception as exc:  # one bad doc never aborts the rest
            log.warning("reboot reconcile: prior-day close of %s failed: %s",
                        doc.get("norenordno"), exc)
    return out


async def _confirming_read(client: Any) -> Optional[List[Dict[str, Any]]]:
    """A SECOND position-book read after an empty one, or None if unreadable.

    The first empty read is only evidence; the guard concludes "flat" from two
    authenticated reads a guard cycle apart (``flat_confirm_reads = 2``), and so
    does this (``_CONFIRM_READ_DELAY_S``). A NON-empty second read is newer than
    the empty first one, so the caller uses it as the book — it is never discarded
    in favour of a weaker proof.
    """
    if _CONFIRM_READ_DELAY_S > 0:
        await asyncio.sleep(_CONFIRM_READ_DELAY_S)
    try:
        again = await client.position_book()
    except Exception as exc:
        log.warning("reboot reconcile: confirming read of an empty position book "
                    "failed (%s) — UNKNOWN", exc)
        return None
    return again if isinstance(again, list) else None


async def reconcile_on_startup(
    db: Any, client: Any, *, now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Transient-safe one-shot startup reconciliation. NEVER raises.

    Returns a small summary dict. ``status`` is one of:

      * ``ok`` — a non-empty book was read and every phase ran;
      * ``flat_confirmed`` — two confirmed-empty reads and nothing left that
        contradicts them: the account holds nothing, which is a COMPLETE answer
        (it used to be reported as UNKNOWN, so the supervisor retried recovery every
        tick, all day, on the shared broker rate budget);
      * ``unknown_position_book`` — the book could not be read or confirmed, OR it
        was confirmed empty while a same-day / undatable OPEN doc remains. Such a
        doc's entry order may still be working; recovery must stay INCOMPLETE so
        the supervisor re-runs it — and re-attaches the guard — once it fills.

    See the module docstring for the safety invariants.
    """
    summary: Dict[str, Any] = {"closed": 0, "cancelled": 0, "status": "ok"}
    today_ist = _ist_date(now_utc or datetime.now(timezone.utc))

    async def _calendar() -> None:
        # An EXPIRED contract needs no broker, whatever the book says or fails to.
        try:
            expired = await _close_expired_open_docs(db, today_ist=today_ist)
            summary["closed"] += expired["closed"]
            summary["expired_detail"] = expired
        except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
            log.warning("reboot reconcile: expiry phase failed: %s", exc)

    async def _unknown() -> Dict[str, Any]:
        summary["status"] = "unknown_position_book"
        await _calendar()
        return summary

    try:
        book = await client.position_book()
    except Exception as exc:
        log.warning("reboot reconcile: position_book fetch failed (treating as "
                    "UNKNOWN — no action): %s", exc)
        return await _unknown()
    if not isinstance(book, list):
        log.info("reboot reconcile: position_book not a list — treating as UNKNOWN; "
                 "no close, no cancel")
        return await _unknown()
    if len(book) == 0:
        again = await _confirming_read(client)
        if again is None:
            log.info("reboot reconcile: position_book empty but not confirmed — "
                     "treating as UNKNOWN; no close, no cancel")
            return await _unknown()
        if again:
            # The newer read is NOT empty: the first was not the truth. Proceed on
            # the newer book exactly as if it had been the first read.
            log.info("reboot reconcile: empty position_book was followed by a "
                     "non-empty one — using the newer read")
            book = again
    if len(book) == 0:
        # Confirmed FLAT. Close only what could not have survived to today. The
        # OCO re-link / orphan sweep stay skipped on an empty book: cancelling a
        # resting broker order is a heavier act than closing a journal row, and
        # the OCO is off by default anyway (LIVE_BROKER_OCO_ENABLED).
        await _calendar()
        try:
            prior = await _close_prior_day_open_docs(db, today_ist=today_ist)
            summary["closed"] += prior["closed"]
            summary["prior_day_detail"] = prior
            contradicted = prior["same_day"] > 0 or prior["undated"] > 0
        except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
            log.warning("reboot reconcile: prior-day phase failed: %s", exc)
            contradicted = True
        summary["status"] = "unknown_position_book" if contradicted else "flat_confirmed"
        return summary

    open_tsyms = _open_tsyms(book)

    # Read the trade book ONCE for both phases that need it (shared rate budget).
    try:
        trade_book = await client.trade_book()
        if not isinstance(trade_book, list):
            trade_book = []
    except Exception as exc:
        log.warning("reboot reconcile: trade_book fetch failed: %s", exc)
        trade_book = []

    # Phase 2 — close OPEN-but-flat docs.
    try:
        close_summary = await _reconcile_open_flat(
            db, client, open_tsyms, trade_book=trade_book,
            position_book=book, today_ist=today_ist)
        summary["closed"] += close_summary.get("closed", 0)
        summary["close_detail"] = close_summary
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: open-flat phase failed: %s", exc)

    # Phase 2b — backfill realized P&L that an EARLIER close never journalled.
    try:
        repair = await _repair_missing_realized(db, client, trade_book=trade_book,
                                                position_book=book)
        summary["repaired"] = repair.get("repaired", 0)
        summary["repair_detail"] = repair
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: realized-P&L repair phase failed: %s", exc)

    # Fetch the resting-OCO (gtt) book ONCE — shared by the re-link phase and the
    # orphan sweep. A failed fetch ⇒ empty list ⇒ both phases no-op safely.
    try:
        gtt_book = await client.gtt_book()
        if not isinstance(gtt_book, list):
            gtt_book = []
    except Exception as exc:
        log.warning("reboot reconcile: gtt_book fetch failed: %s", exc)
        gtt_book = []

    # Phase 2.5 — RE-LINK a still-resting OCO to its HELD rehydrated position
    # (so the guard can cancel it on a later square → no orphan misfire) + flag
    # HELD positions with no broker backstop. Runs BEFORE the orphan-sweep so a
    # re-linked HELD OCO is never a sweep candidate (the sweep is flat/closed-only).
    try:
        relink_summary = _relink_held_ocos(gtt_book, open_tsyms)
        summary["relinked"] = relink_summary.get("relinked", 0)
        summary["no_backstop"] = relink_summary.get("no_backstop", 0)
        summary["relink_detail"] = relink_summary
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: re-link phase failed: %s", exc)

    # Phase 3 — orphan-OCO sweep (independent of the phases above; flat/closed-only,
    # so a HELD tsym's OCO — incl. one just re-linked — is never cancelled).
    try:
        sweep_summary = await _sweep_orphan_ocos(db, client, open_tsyms, gtt_book)
        summary["cancelled"] = sweep_summary.get("cancelled", 0)
        summary["sweep_detail"] = sweep_summary
    except Exception as exc:  # pragma: no cover - defensive (inner already wraps)
        log.warning("reboot reconcile: orphan-OCO sweep phase failed: %s", exc)

    return summary
