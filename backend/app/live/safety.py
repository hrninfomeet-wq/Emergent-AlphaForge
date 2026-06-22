"""Pre-trade safety engine — pure, stateless (except RateThrottle), fail-closed.

All public functions return (allowed: bool, reason: str | None).
- allowed=True  → reason is None
- allowed=False → reason is a non-empty string

NEVER call time.time() internally; callers inject `now` for determinism.
NEVER import DB, network, or I/O modules here.
"""
from __future__ import annotations

from typing import Optional, Tuple

from app.live.broker_protocol import ALLOWED_PRCTYP, ALLOWED_PRD, ALLOWED_RET, OrderIntent

# ---------------------------------------------------------------------------
# Type alias for the return shape used by every check
# ---------------------------------------------------------------------------
CheckResult = Tuple[bool, Optional[str]]

_ALLOWED: CheckResult = (True, None)


def _block(reason: str) -> CheckResult:
    return (False, reason)


# ---------------------------------------------------------------------------
# 1. Fat-finger cap
# ---------------------------------------------------------------------------

def check_fat_finger(lots: int | float, cap: Optional[int | float]) -> CheckResult:
    """Block if cap is unconfigured, lots <= 0, or lots > cap.

    DEFAULT-DENY: a missing cap blocks rather than permits. This ensures that
    a misconfigured deployment cannot accidentally send outsized orders.
    """
    if cap is None:
        return _block("no fat-finger cap configured")
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
    """Block if the reference is absent/stale/zero, price is non-positive,
    or the price deviates more than `pct` percent from ref_ltp.
    """
    if ref_ltp is None or ref_ltp <= 0:
        return _block("no/stale price reference")
    if price <= 0:
        return _block(f"price must be positive, got {price}")
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
    - prctyp in ALLOWED_PRCTYP  (blocks market / IOC / CO / BO)
    - prd   in ALLOWED_PRD
    - ret   in ALLOWED_RET
    - SL-LMT must carry trgprc
    - qty > 0 and an exact multiple of lot_size
    - prc > 0
    """
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
    if intent.prctyp == "SL-LMT" and intent.trgprc is None:
        return _block("SL-LMT order requires trgprc (trigger price)")
    if intent.qty <= 0 or intent.qty % lot_size != 0:
        return _block(
            f"qty {intent.qty} must be a positive multiple of lot_size {lot_size}"
        )
    if intent.prc <= 0:
        return _block(f"prc must be positive, got {intent.prc}")
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
        """
        if is_cancel:
            # Cancels/exits bypass the bucket entirely — never throttled.
            return True

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
