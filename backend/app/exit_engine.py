"""Shared intrabar stop/target exit resolution.

Before this module, the "did a stop or target fill inside this bar, and which
filled first?" decision was reimplemented in three places (the spot backtest
loop, the option spot-mirror exit, and the option premium-level walk). Subtle
drift between them is exactly the kind of bug that makes a backtest lie. This
centralizes the rule so spot and option engines share one audited decision.

Convention (the pessimistic, no-self-flattery rule used across the app):
  - For a LONG position (spot CE, or a long option premium): stop is BELOW
    entry, target is ABOVE. A stop hits when low <= stop; target when high >= target.
  - For a SHORT-on-index position (spot PE): stop is ABOVE entry, target is BELOW.
  - If BOTH levels are touched within the same bar, the STOP is assumed to fill
    first (stop_first=True) so the backtest never assumes the lucky fill.

Returns the fill LEVEL (not the bar close) at the stop/target, matching how a
resting stop/limit order would fill, and the reason tag.
"""
from __future__ import annotations

from typing import Optional, Tuple


def intrabar_exit(
    *,
    high: float,
    low: float,
    stop: Optional[float],
    target: Optional[float],
    is_long: bool,
    stop_first: bool = True,
) -> Tuple[Optional[float], Optional[str]]:
    """Resolve a stop/target hit within one bar.

    Returns (exit_level, reason) where reason is "STOP" or "TARGET", or
    (None, None) if neither level was touched in the bar.
    """
    if is_long:
        hit_stop = stop is not None and low <= stop
        hit_target = target is not None and high >= target
    else:
        hit_stop = stop is not None and high >= stop
        hit_target = target is not None and low <= target

    checks = [("STOP", hit_stop, stop), ("TARGET", hit_target, target)]
    if not stop_first:
        checks.reverse()
    for reason, hit, level in checks:
        if hit:
            return level, reason
    return None, None
