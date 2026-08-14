"""What exits will ACTUALLY be in force — computed before a rupee is committed.

Audit finding [17]. On 2026-08-14 a deployment went live carrying a premium stop of
50% (the deep-default catastrophe floor, not anything anybody chose), no premium
target at all, and a trail overlay the live path then silently discarded. All three
were computable from the deployment doc plus the strategy's ``risk_hints`` before
arming. Nothing computed them, and no surface showed them: the enable dialog lists
caps, the enable route validates caps, and ``stop_level``/``target_level`` first
appear only after a real fill.

This module is a **pure description**. It reports; it does not gate. The standing
project rule is that live-arming gates are removed, not added, so every notice here
is advisory and the enable route is not permitted to consult it. Its only job is to
make "you are arming a 50% stop with no target" visible at the confirm step instead
of discoverable in the blotter.

**Anti-drift is the load-bearing design property.** Every number comes from
``resolve_live_exit_plan`` — the exact function the arm path calls — never from a
second copy of the precedence rules. A preview that drifts is worse than no preview:
it would have shown a confident, wrong answer on precisely the trade that motivated
it. The one thing this module derives independently is the *label* saying which
input won, and even that is cross-checked against the resolved value; on any
disagreement it reports ``"unknown"`` rather than asserting something false.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from app.auto_live import _GUARD_DEFAULT_STOP_PCT, resolve_live_exit_plan
from app.exit_controls import ExitControlsConfig

#: The ultimate "never left open" backstop every live position inherits
#: (live_position_guard: "BOTH are EOD-squared at 15:00 IST").
EOD_SQUARE_IST = "15:00"


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(value: Any) -> Optional[float]:
    f = _num(value)
    return f if f is not None and f > 0 else None


def _leg_source(hint: Any, dep_pts: Any, dep_pct: Any) -> str:
    """Which input wins for one leg, mirroring resolve_live_exit_plan's precedence.

    Label only — the VALUES always come from the resolver. Cross-checked by the
    caller, which downgrades to "unknown" if this disagrees with what was resolved.
    """
    if _num(hint) is not None:
        return "strategy_hint"
    if _num(dep_pts) is not None:
        return "deployment_pts"
    if _num(dep_pct) is not None:
        return "deployment_pct"
    return "none"


def _fmt(value: float) -> str:
    """Trim a trailing .0 so '+10' reads as intended rather than '+10.0'."""
    return f"{value:g}"


def _describe_trail(raw: Any) -> Dict[str, Any]:
    """The exit_controls overlay in human terms, or ``present: False``.

    ``unit="pct"`` is a FRACTION in this schema (``entry * (1 + trigger)``), so 0.2
    means +20%. Rendering it as "0.2%" would understate the trigger by 100x, and the
    SENSEX deployments ship exactly that shape.
    """
    absent = {"present": False, "unit": None, "breakeven": None,
              "trailing": None, "summary": "none"}
    if not isinstance(raw, dict):
        return absent
    cfg = ExitControlsConfig.from_dict(raw)
    if not cfg.enabled:
        return absent

    be_on = bool(cfg.be_trigger and cfg.be_trigger > 0)
    tr_on = bool(cfg.trail_distance and cfg.trail_distance > 0)
    if not (be_on or tr_on):
        # enabled:true but every overlay zeroed — effective_premium_stop arms
        # nothing, so reporting it as "present" would be a lie.
        return absent

    pts = cfg.unit == "pts"

    def q(v: float) -> str:
        return f"+{_fmt(v)}" if pts else f"+{_fmt(v * 100.0)}%"

    parts: List[str] = []
    breakeven = trailing = None
    if be_on:
        breakeven = {"trigger": float(cfg.be_trigger), "lock": float(cfg.be_lock)}
        parts.append(f"breakeven at {q(cfg.be_trigger)} locks {q(cfg.be_lock)}")
    if tr_on:
        trailing = {"activation": float(cfg.trail_activation),
                    "distance": float(cfg.trail_distance)}
        gap = (f"{_fmt(cfg.trail_distance)} pts"
               if pts else f"{_fmt(cfg.trail_distance * 100.0)}%")
        parts.append(f"trail {gap} below peak from {q(cfg.trail_activation)}")
    return {"present": True, "unit": cfg.unit, "breakeven": breakeven,
            "trailing": trailing, "summary": "; ".join(parts)}


def describe_live_exits(
    deployment: Optional[Dict[str, Any]],
    risk_hints: Optional[Dict[str, Any]] = None,
    *,
    ref_premium: Any = None,
) -> Dict[str, Any]:
    """Describe the exits a live entry on ``deployment`` would carry.

    ``risk_hints`` should be the hints from a REAL recent signal for this
    deployment — they outrank every deployment fallback, so a preview built without
    them is provisional and says so (``hints_unobserved``). Pass ``None`` when no
    signal has been observed; pass ``{}`` to mean "observed, and it carried none".

    ``ref_premium`` is optional. At confirm time there is no live premium yet, so
    absolute levels are reported as ``None`` rather than guessed; supply one and the
    stop/target levels are resolved through the guard's own arithmetic.

    Never raises: this renders inside the arm dialog, and a preview that 500s on a
    malformed risk block would be worse than the blindness it replaces.
    """
    dep = deployment if isinstance(deployment, dict) else {}
    risk = dep.get("risk") if isinstance(dep.get("risk"), dict) else {}
    hints_observed = risk_hints is not None
    hints = risk_hints if isinstance(risk_hints, dict) else {}

    # THE seam: the arm path's own resolver, never a second copy of the rules.
    levels = resolve_live_exit_plan({"risk_hints": hints}, {"risk": risk})["levels"]

    stop_pct, stop_pts = levels.get("stop_pct"), levels.get("stop_pts")
    tgt_pct, tgt_pts = levels.get("target_pct"), levels.get("target_pts")

    # --- source attribution, cross-checked against what was actually resolved ---
    stop_src = _leg_source(hints.get("stop_pct"), risk.get("auto_paper_stop_pts"),
                           risk.get("auto_paper_stop_pct"))
    is_deep_default = stop_src == "none"
    if is_deep_default:
        stop_src = "deep_default"
        # The floor is seeded only as a pct; if that is not what came back, the
        # precedence has changed underneath us — say "unknown", never guess.
        if stop_pct != _GUARD_DEFAULT_STOP_PCT or stop_pts is not None:
            stop_src, is_deep_default = "unknown", False
    tgt_src = _leg_source(hints.get("target_pct"), risk.get("auto_paper_target_pts"),
                          risk.get("auto_paper_target_pct"))
    if tgt_src == "none" and (tgt_pct is not None or tgt_pts is not None):
        tgt_src = "unknown"

    # --- absolute levels, only when a real reference premium is supplied --------
    stop_level = target_level = None
    entry = _pos(ref_premium)
    if entry is not None:
        from app.live.live_sl_monitor import build_monitor_state
        try:
            st = build_monitor_state(entry, stop_pct=stop_pct, stop_pts=stop_pts,
                                     target_pct=tgt_pct, target_pts=tgt_pts)
            stop_level, target_level = st["initial_stop"], st["target_level"]
        except (ValueError, TypeError):
            pass

    trail = _describe_trail(risk.get("exit_controls"))
    spot_t = _num(hints.get("spot_target_pts"))
    spot_s = _num(hints.get("spot_stop_pts"))
    time_stop = _num(hints.get("time_stop_minutes"))

    notices: List[Dict[str, str]] = []

    def note(nid: str, severity: str, message: str) -> None:
        notices.append({"id": nid, "severity": severity, "message": message})

    if is_deep_default:
        note("deep_default_stop", "warn",
             f"No premium stop is configured, so the {_fmt(_GUARD_DEFAULT_STOP_PCT)}% "
             "catastrophe floor applies. That is a last-resort backstop, not a "
             "chosen exit — the position gives up half its premium before it fires.")
    if tgt_pct is None and tgt_pts is None:
        note("no_premium_target", "warn",
             "No premium target. The position can only be closed by a stop, the "
             "trail, a spot-mirror level, or the 15:00 EOD square.")
    if not trail["present"]:
        note("no_trail", "info",
             "No breakeven or trailing overlay, so the stop never ratchets: an "
             "open profit can round-trip in full.")
    if spot_t is None and spot_s is None:
        note("no_spot_exit", "info",
             "This strategy emits no spot-mirror levels, so the underlying moving "
             "against the position does not by itself close it.")
    if not hints_observed:
        note("hints_unobserved", "info",
             "No recent signal was available, so this is the deployment's own "
             "configuration. A strategy's per-signal risk hints outrank it and "
             "could change the stop or target actually used.")

    return {
        "premium_stop": {"pct": stop_pct, "pts": stop_pts, "level": stop_level,
                         "source": stop_src, "is_deep_default": is_deep_default},
        "premium_target": {"pct": tgt_pct, "pts": tgt_pts, "level": target_level,
                           "source": tgt_src,
                           "present": tgt_pct is not None or tgt_pts is not None},
        "trail": trail,
        "spot_exit": {"present": spot_t is not None or spot_s is not None,
                      "target_pts": spot_t, "stop_pts": spot_s},
        "time_stop_minutes": time_stop,
        "eod_square_ist": EOD_SQUARE_IST,
        "reference_premium": entry,
        "hints_observed": hints_observed,
        "notices": notices,
    }
