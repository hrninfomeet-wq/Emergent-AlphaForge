"""1-second market state for the scalper — deterministic, clock injected by the caller.

Inputs are the app's normalized Upstox ticks (``upstox_stream.normalize_feed_response`` shape):
``instrument_key, last_price, best_bid_price, best_ask_price, best_bid_quantity, best_ask_quantity,
market_depth[{bid_price,bid_quantity,ask_price,ask_quantity}], ts (exchange ltt), received_ts,
ingest_ts`` (local receive clock — the ONLY local clock; HANDOFF T8).

Measured facts this module is built around (docs/scalping/02-measurements.md):
* Upstox ``full`` mode is a ~1 Hz snapshot feed (index inter-arrival p50 1,046 ms), not tick-by-tick.
  So the decision grid is 1 s; nothing finer is observable.
* The options-implied synthetic forward F = mid(CE_K) - mid(PE_K) + K leads the index print by ~1 s
  (lag-1 correlation 0.17 vs 0.08), so it is the preferred reference for NIFTY.

The caller advances the grid with ``on_second(now_ms)``; between calls ``on_tick`` only stores the
latest observation. Same tick sequence + same clock => identical state (replay determinism).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Tuple

STRIKE_STEP = {"NIFTY": 50, "SENSEX": 100}
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "SENSEX": "BSE_INDEX|SENSEX"}
KEY_TO_INDEX = {v: k for k, v in INDEX_KEYS.items()}


@dataclass(frozen=True)
class Contract:
    instrument_key: str
    underlying: str
    strike: float
    side: str            # "CE" | "PE"
    expiry: str          # YYYY-MM-DD
    lot_size: int
    trading_symbol: str = ""


@dataclass
class Quote:
    instrument_key: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    ask_depth5_qty: int
    bid_depth5_qty: int
    ltp: Optional[float]
    ingest_ms: int
    exch_ms: Optional[int]

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def spread_ticks(self, tick: float = 0.05) -> int:
        return int(round(self.spread / tick))

    def spread_pct(self) -> float:
        return 100.0 * self.spread / self.mid if self.mid > 0 else math.inf

    def age_ms(self, now_ms: int) -> int:
        return int(now_ms) - int(self.ingest_ms)

    def ask_ladder(self, depth: List[dict]) -> List[Tuple[float, int]]:
        return [(float(l["ask_price"]), int(l.get("ask_quantity") or 0)) for l in depth
                if l.get("ask_price") and float(l["ask_price"]) > 0]


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def quote_from_tick(tick: dict) -> Optional[Quote]:
    """A two-sided, uncrossed quote or None. One-sided / crossed books are not quotes."""
    bid, ask = _f(tick.get("best_bid_price")), _f(tick.get("best_ask_price"))
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return None
    ingest = tick.get("ingest_ts")
    if ingest is None:
        ingest = tick.get("received_ts")
    if ingest is None:
        return None
    depth = tick.get("market_depth") or []
    ask5 = sum(int(l.get("ask_quantity") or 0) for l in depth) if depth else int(tick.get("best_ask_quantity") or 0)
    bid5 = sum(int(l.get("bid_quantity") or 0) for l in depth) if depth else int(tick.get("best_bid_quantity") or 0)
    return Quote(instrument_key=str(tick["instrument_key"]), bid=bid, ask=ask,
                 bid_qty=int(tick.get("best_bid_quantity") or 0), ask_qty=int(tick.get("best_ask_quantity") or 0),
                 ask_depth5_qty=ask5, bid_depth5_qty=bid5, ltp=_f(tick.get("last_price")),
                 ingest_ms=int(ingest), exch_ms=int(tick["ts"]) if tick.get("ts") is not None else None)


class RollingZ:
    """z of a w-second change, normalised by the std of 1-s changes over a trailing window.

    z_w(t) = (x[t] - x[t-w]) / (std(dx over lookback) * sqrt(w)) — the M3/M4/M7 definition.
    Missing seconds (None) break the change series; std needs ``min_samples`` valid changes."""

    def __init__(self, lookback_s: int, min_samples: int, max_window_s: int):
        self.lookback_s = lookback_s
        self.min_samples = min_samples
        self.values: Deque[Optional[float]] = deque(maxlen=max(lookback_s, max_window_s) + 2)
        self.changes: Deque[Optional[float]] = deque(maxlen=lookback_s)
        self._n = 0
        self._s = 0.0
        self._s2 = 0.0

    def push(self, x: Optional[float]) -> None:
        prev = self.values[-1] if self.values else None
        d = (x - prev) if (x is not None and prev is not None) else None
        if len(self.changes) == self.changes.maxlen:
            old = self.changes[0]
            if old is not None:
                self._n -= 1
                self._s -= old
                self._s2 -= old * old
        self.changes.append(d)
        if d is not None:
            self._n += 1
            self._s += d
            self._s2 += d * d
        self.values.append(x)

    def sigma(self) -> Optional[float]:
        if self._n < max(2, self.min_samples):
            return None
        mean = self._s / self._n
        var = max(0.0, (self._s2 - self._n * mean * mean) / (self._n - 1))
        sd = math.sqrt(var)
        return sd if sd > 0 else None

    def z(self, window_s: int) -> Optional[float]:
        if len(self.values) <= window_s:
            return None
        now, then = self.values[-1], self.values[-1 - window_s]
        sd = self.sigma()
        if now is None or then is None or sd is None:
            return None
        return (now - then) / (sd * math.sqrt(window_s))


@dataclass
class UnderlyingState:
    underlying: str
    spot: Optional[float] = None
    spot_ingest_ms: Optional[int] = None
    synthetic: Optional[float] = None
    synthetic_ms: Optional[int] = None
    z_spot: RollingZ = field(default=None)       # type: ignore[assignment]
    z_syn: RollingZ = field(default=None)        # type: ignore[assignment]


class MarketState:
    """Latest quotes + per-second reference series for NIFTY and SENSEX."""

    def __init__(self, contracts: Iterable[Contract], *, lookback_s: int = 600, min_samples: int = 300,
                 max_window_s: int = 60, max_syn_quote_age_ms: int = 2000):
        self.contracts: Dict[str, Contract] = {c.instrument_key: c for c in contracts}
        self._by_strike: Dict[Tuple[str, str, float, str], str] = {
            (c.underlying, c.expiry, float(c.strike), c.side): c.instrument_key for c in self.contracts.values()}
        self.quotes: Dict[str, Quote] = {}
        self.depth: Dict[str, List[dict]] = {}
        self.max_syn_quote_age_ms = max_syn_quote_age_ms
        self.u: Dict[str, UnderlyingState] = {}
        for und in ("NIFTY", "SENSEX"):
            self.u[und] = UnderlyingState(und, z_spot=RollingZ(lookback_s, min_samples, max_window_s),
                                          z_syn=RollingZ(lookback_s, min_samples, max_window_s))
        self.now_ms: int = 0
        self.seconds_advanced = 0

    # ---- ingestion
    def add_contracts(self, contracts: Iterable[Contract]) -> None:
        for c in contracts:
            self.contracts[c.instrument_key] = c
            self._by_strike[(c.underlying, c.expiry, float(c.strike), c.side)] = c.instrument_key

    def on_tick(self, tick: dict) -> None:
        key = str(tick.get("instrument_key") or "")
        und = KEY_TO_INDEX.get(key)
        if und:
            px = _f(tick.get("last_price"))
            ing = tick.get("ingest_ts")
            if ing is None:
                ing = tick.get("received_ts")
            if px is not None and px > 0 and ing is not None:
                st = self.u[und]
                st.spot, st.spot_ingest_ms = px, int(ing)
            return
        if key in self.contracts:
            q = quote_from_tick(tick)
            if q is not None:
                prev = self.quotes.get(key)
                if prev is None or q.ingest_ms >= prev.ingest_ms:
                    self.quotes[key] = q
                    self.depth[key] = list(tick.get("market_depth") or [])

    # ---- grid
    def expiries(self, underlying: str) -> List[str]:
        return sorted({c.expiry for c in self.contracts.values() if c.underlying == underlying})

    def contract_for(self, underlying: str, side: str, *, expiry_rank: int = 0, offset: int = 0,
                     ref_price: Optional[float] = None) -> Optional[Contract]:
        exps = self.expiries(underlying)
        st = self.u[underlying]
        ref = ref_price if ref_price is not None else st.spot
        if expiry_rank >= len(exps) or ref is None:
            return None
        step = STRIKE_STEP[underlying]
        atm = round(ref / step) * step
        strike = atm - offset * step if side == "CE" else atm + offset * step
        key = self._by_strike.get((underlying, exps[expiry_rank], float(strike), side))
        return self.contracts.get(key) if key else None

    def fresh_quote(self, key: str, max_age_ms: int) -> Optional[Quote]:
        q = self.quotes.get(key)
        if q is None or q.age_ms(self.now_ms) > max_age_ms or q.age_ms(self.now_ms) < -1000:
            return None
        return q

    def _synthetic(self, und: str) -> Tuple[Optional[float], Optional[int]]:
        st = self.u[und]
        if st.spot is None:
            return None, None
        ce = self.contract_for(und, "CE")
        pe = self.contract_for(und, "PE")
        if ce is None or pe is None or ce.strike != pe.strike:
            return None, None
        qc = self.fresh_quote(ce.instrument_key, self.max_syn_quote_age_ms)
        qp = self.fresh_quote(pe.instrument_key, self.max_syn_quote_age_ms)
        if qc is None or qp is None:
            return None, None
        return qc.mid - qp.mid + ce.strike, min(qc.ingest_ms, qp.ingest_ms)

    def on_second(self, now_ms: int) -> None:
        """Advance the 1-s grid. Call once per wall/replay second, AFTER that second's ticks."""
        self.now_ms = int(now_ms)
        for und, st in self.u.items():
            spot_ok = st.spot is not None and st.spot_ingest_ms is not None and \
                self.now_ms - st.spot_ingest_ms <= 3000
            st.z_spot.push(st.spot if spot_ok else None)
            syn, syn_ms = self._synthetic(und)
            st.synthetic, st.synthetic_ms = syn, syn_ms
            st.z_syn.push(syn)
        self.seconds_advanced += 1

    # ---- reads
    def ref_age_ms(self, underlying: str, ref: str) -> Optional[int]:
        st = self.u[underlying]
        ts = st.synthetic_ms if ref == "synthetic" else st.spot_ingest_ms
        return None if ts is None else self.now_ms - ts

    def z(self, underlying: str, ref: str, window_s: int) -> Optional[float]:
        st = self.u[underlying]
        return (st.z_syn if ref == "synthetic" else st.z_spot).z(window_s)
