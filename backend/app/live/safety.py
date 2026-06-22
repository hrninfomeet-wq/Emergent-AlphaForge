"""Pre-trade safety engine — pure, stateless (except RateThrottle), fail-closed.

All public functions return (allowed: bool, reason: str | None).
- allowed=True  → reason is None
- allowed=False → reason is a non-empty string

NEVER call time.time() internally; callers inject `now` for determinism.
NEVER import DB, network, or I/O modules here.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from app.live.broker_protocol import ALLOWED_PRCTYP, ALLOWED_PRD, ALLOWED_RET, OrderIntent

# ---------------------------------------------------------------------------
# Type alias for the return shape used by every check
# ---------------------------------------------------------------------------
CheckResult = Tuple[bool, Optional[str]]

_ALLOWED: CheckResult = (True, None)


def _block(reason: str) -> CheckResult:
    return (False, reason)


def _finite_num(x: object) -> bool:
    """Return True iff x is a real finite number (int or float, not bool, not NaN, not inf).

    bool is excluded because True==1 and False==0 are footguns in numeric checks —
    a caller passing `True` as a lot count or price is almost certainly a bug.
    """
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ---------------------------------------------------------------------------
# 1. Fat-finger cap
# ---------------------------------------------------------------------------

def check_fat_finger(lots: int | float, cap: Optional[int | float]) -> CheckResult:
    """Block if cap is unconfigured, lots is not a finite positive number, or lots > cap.

    DEFAULT-DENY: a missing cap blocks rather than permits. This ensures that
    a misconfigured deployment cannot accidentally send outsized orders.

    Guards (in order):
    - cap is None                          → block (default-deny)
    - cap is not a finite number           → block (NaN/inf/string/bool cap)
    - lots is not a finite number          → block (NaN/inf/string/bool lots)
    - lots <= 0                            → block
    - lots > cap                           → block
    """
    if cap is None:
        return _block("no fat-finger cap configured")
    if not _finite_num(cap):
        return _block(f"fat-finger cap must be a finite positive number, got {cap!r}")
    if not _finite_num(lots):
        return _block(f"lots must be a finite positive number, got {lots!r}")
    if lots <= 0:
        return _block(f"lots must be positive, got {lots}")
    if lots > cap:
        return _block(f"lots {lots} exceeds fat-finger cap {cap}")
    return _ALLOWED


# ---------------------------------------------------------------------------
# 2. Price-band check
# ---------------------------------------------------------------------------

def check_price_band(
    price: float,
    ref_ltp: Optional[float],
    pct: float,
) -> CheckResult:
    """Block if the reference is absent/stale/zero/non-finite, price is
    non-positive/non-finite, pct is non-finite/negative, or the price
    deviates more than `pct` percent from ref_ltp.

    Guards (in order):
    - ref_ltp is None                      → block (no reference)
    - ref_ltp is not finite, or <= 0       → block (NaN/inf/string/stale)
    - price is not finite, or <= 0         → block (NaN/inf/string/negative)
    - pct is not finite, or < 0            → block (NaN/inf/string/negative)
    - abs deviation > pct                  → block (out of band)
    """
    if ref_ltp is None:
        return _block("no/stale price reference")
    if not _finite_num(ref_ltp) or ref_ltp <= 0:
        return _block(f"no/stale price reference (got {ref_ltp!r})")
    if not _finite_num(price) or price <= 0:
        return _block(f"price must be a finite positive number, got {price!r}")
    if not _finite_num(pct) or pct < 0:
        return _block(f"pct band must be a finite non-negative number, got {pct!r}")
    deviation = abs(price - ref_ltp) / ref_ltp * 100
    if deviation > pct:
        return _block(
            f"price {price:.4f} is {deviation:.2f}% off reference {ref_ltp:.4f} "
            f"(limit {pct}%)"
        )
    return _ALLOWED


# ---------------------------------------------------------------------------
# 3. jData / OrderIntent validation
# ---------------------------------------------------------------------------

def validate_jdata(intent: OrderIntent, *, lot_size: int) -> CheckResult:
    """Validate an OrderIntent before it becomes a broker API call.

    Checks (in order, first failure wins):
    - lot_size must be a positive int (not bool, not zero)  — checked FIRST to
      prevent ZeroDivisionError when computing qty % lot_size
    - prctyp in ALLOWED_PRCTYP  (blocks market / IOC / CO / BO)
    - prd   in ALLOWED_PRD
    - ret   in ALLOWED_RET
    - SL-LMT must carry a finite positive trgprc (not just non-None)
    - qty must be an int > 0 and an exact multiple of lot_size (not string/bool)
    - prc must be a finite positive number (not NaN/inf/string)
    """
    # Guard lot_size before any % operation to prevent ZeroDivisionError.
    if not (isinstance(lot_size, int) and not isinstance(lot_size, bool) and lot_size > 0):
        return _block(f"invalid lot_size {lot_size!r}: must be a positive integer")

    if intent.prctyp not in ALLOWED_PRCTYP:
        return _block(
            f"prctyp '{intent.prctyp}' not allowed; permitted: {ALLOWED_PRCTYP}"
        )
    if intent.prd not in ALLOWED_PRD:
        return _block(
            f"prd '{intent.prd}' not allowed; permitted: {ALLOWED_PRD}"
        )
    if intent.ret not in ALLOWED_RET:
        return _block(
            f"ret '{intent.ret}' not allowed; permitted: {ALLOWED_RET}"
        )
    # SL-LMT trigger price: must exist AND be a finite positive number.
    if intent.prctyp == "SL-LMT":
        if intent.trgprc is None or not _finite_num(intent.trgprc) or intent.trgprc <= 0:
            return _block(
                f"SL-LMT order requires a finite positive trgprc (trigger price), "
                f"got {intent.trgprc!r}"
            )
    # qty: must be a plain positive int, an exact lot multiple — not a string/bool/float.
    if not (isinstance(intent.qty, int) and not isinstance(intent.qty, bool)
            and intent.qty > 0 and intent.qty % lot_size == 0):
        return _block(
            f"qty {intent.qty!r} must be a positive integer multiple of lot_size {lot_size}"
        )
    # prc: must be a finite positive number — NaN/inf/string all fail-open without this.
    if not _finite_num(intent.prc) or intent.prc <= 0:
        return _block(f"prc must be a finite positive number, got {intent.prc!r}")
    return _ALLOWED


# ---------------------------------------------------------------------------
# 4. Rate throttle (token bucket)
# ---------------------------------------------------------------------------

# SEBI limit: <10 orders/second.  We cap at 9 to stay safely under.
_DEFAULT_MAX_PER_SEC = 9


class RateThrottle:
    """Token-bucket rate limiter for order placement.

    Cancels/exits (is_cancel=True) are ALWAYS allowed — throttling an exit
    would trap a losing position open (FIA / broker fair-use rule).

    `allow()` takes an explicit `now` float (seconds since epoch) so that
    unit tests are fully deterministic without patching time.time().

    Args:
        max_per_sec: maximum order submissions per second (default 9 to stay
                     under the SEBI hard limit of 10).  Must be >= 1.

    NOTE: This is a leaky-bucket (token-bucket) implementation.  It permits a
    burst of up to `max_per_sec` orders at the start of a new second window.
    That means up to ~2×max_per_sec orders could arrive at a broker within a
    strict 1-second rolling window (last token of second N + first tokens of
    second N+1).  If the broker enforces a strict rolling-per-second ORL, switch
    to a sliding-window deque counter rather than a token bucket here.
    """

    def __init__(self, max_per_sec: int = _DEFAULT_MAX_PER_SEC) -> None:
        if max_per_sec < 1:
            raise ValueError(f"max_per_sec must be >= 1, got {max_per_sec}")
        self._max: int = max_per_sec
        # Token-bucket state: tokens available + timestamp of last refill.
        # _last_refill=None means "never called" — distinct from t=0.0.
        self._tokens: float = float(max_per_sec)
        self._last_refill: Optional[float] = None

    def allow(self, *, is_cancel: bool, now: float) -> bool:
        """Return True if the order may proceed.

        Cancels always return True.  For entries/modifications, tokens are
        consumed (1 per call).  Tokens refill at `max_per_sec` tokens per
        second based on elapsed wall-clock time (injected via `now`).

        Safety guards:
        - is_cancel is bool-coerced (1/0/truthy accepted) — callers may pass
          non-bool truthy values; we normalise rather than crash.
        - Non-finite `now` (NaN/inf) → fail-closed for entries (return False).
          Cancels bypass this check because throttling an exit is worse than
          accepting one with an unreliable clock reading.
        """
        # Normalise is_cancel so truthy ints (1) work identically to True.
        is_cancel = bool(is_cancel)

        if is_cancel:
            # Cancels/exits bypass the bucket entirely — never throttled.
            return True

        # Fail-closed on a bad clock: a NaN/inf 'now' would corrupt bucket
        # arithmetic, potentially allowing an unlimited burst.
        if not _finite_num(now):
            return False

        if self._last_refill is None:
            # First non-cancel call — seed the clock; tokens are already full.
            self._last_refill = now
        else:
            # Refill tokens proportional to elapsed time since last check.
            elapsed = max(0.0, now - self._last_refill)
            self._tokens = min(float(self._max), self._tokens + elapsed * self._max)
            self._last_refill = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
