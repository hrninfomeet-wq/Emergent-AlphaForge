"""The single source of truth for *when a signal may become a trade*.

Before this module the answer lived in two places that disagreed.
``deployment_evaluator`` hardcoded 09:25-14:50 as module constants;
``backtest.run_backtest`` defaulted to 09:25-15:00. Three consequences, recorded
as register item #6 and deliverable §7.2:

1. Every backtest run at defaults scored signals from **14:50-15:00 that live
   would refuse** — ten minutes of trades that cannot exist, in every result the
   app has ever produced at defaults.
2. A strategy needing the opening ten minutes was **undeployable at any price**,
   however well it backtested. ``dte_opening_shock_breakout``'s docstring had to
   tell operators to override the window by hand — a documented workaround for a
   missing feature.
3. The live blocker string hardcoded ``09:25`` into its text, so a configurable
   window would have made the *reason* a bar was refused into a lie.

Both sides now resolve through :func:`resolve_entry_window`, and a test asserts
the two constants are equal so neither can be edited alone again.

**Bounds, and why they are not a preference.** A deployment may narrow the window
freely and may widen it — that is the point of item #6 — but only within
``HARD_EARLIEST``..``HARD_LATEST``. The upper bound is
``deployment_evaluator.SQUARE_OFF_AT`` (15:00): an entry at or after square-off is
closed on the bar it opens, so admitting one is arithmetic nonsense rather than a
risk appetite. The lower bound is the session open, before which no bar exists.

**Everything unusable fails to the DEFAULT, never to a wider window.** The window
is applied by lexicographic string comparison everywhere in this codebase
(``backtest._in_window`` is literally ``start <= ist < end``), so a plausible-
looking ``"9:25"`` without its leading zero compares GREATER than ``"14:50"`` and
would silently admit nothing at all. Guessing at a malformed bound is how a
window quietly stops working; falling back is visible.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

#: The live-effective window every deployment has enforced since 2026-05-27:
#: block the opening ten minutes and the last forty. These are the defaults for
#: the backtest, the optimizer and the live evaluator alike.
DEFAULT_ENTRY_START = "09:25"
DEFAULT_ENTRY_END = "14:50"

#: Session open. Nothing before this exists to trade.
HARD_EARLIEST = "09:15"

#: `deployment_evaluator.SQUARE_OFF_AT`. An entry at or after square-off is
#: squared off on the same bar, so no configuration may reach past it.
HARD_LATEST = "15:00"

#: Config keys, matching `BacktestReq` / `OptimizeStartReq` so a saved preset's
#: window carries into a deployment without translation.
START_KEY = "trade_window_start"
END_KEY = "trade_window_end"


def _hhmm(value: Any) -> Optional[str]:
    """A validated ``HH:MM`` string, or ``None``.

    Strict on purpose — see the module docstring. ``bool`` is rejected before the
    string check because ``str(True)`` is a perfectly parseable-looking value that
    means nothing here.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) != 5 or text[2] != ":":
        return None
    hh, mm = text[:2], text[3:]
    if not (hh.isdigit() and mm.isdigit()):
        return None
    if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
        return None
    return text


def _clamp(value: str, low: str, high: str) -> str:
    return max(low, min(value, high))


def resolve_entry_window(config: Any) -> Tuple[str, str]:
    """Resolve ``(start, end)`` IST ``HH:MM`` entry bounds from a config mapping.

    ``config`` is any mapping that may carry ``trade_window_start`` /
    ``trade_window_end`` — in practice a deployment's ``risk`` dict or a backtest
    request. Anything else (None, a string, a list) is ignored and the defaults
    are returned, because a caller handing this the wrong shape must not silently
    get a different trading window than it asked for.

    A window that admits nothing (start >= end after clamping) falls back to the
    defaults rather than disabling the deployment: the intent of a nonsensical
    window is unknowable, and refusing every bar for the rest of the session is a
    worse failure than trading the documented default.
    """
    start = DEFAULT_ENTRY_START
    end = DEFAULT_ENTRY_END

    if isinstance(config, Mapping):
        raw_start = _hhmm(config.get(START_KEY))
        raw_end = _hhmm(config.get(END_KEY))
        if raw_start is not None:
            start = _clamp(raw_start, HARD_EARLIEST, HARD_LATEST)
        if raw_end is not None:
            end = _clamp(raw_end, HARD_EARLIEST, HARD_LATEST)

    if start >= end:
        return DEFAULT_ENTRY_START, DEFAULT_ENTRY_END
    return start, end
