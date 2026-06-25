"""Unified live execution arm-state — the single source of "will a signal place a
REAL order right now?".

Today that answer is smeared across the Mode tile, the Guard tile, a banner pill,
and a hardcoded "L3 enabled" chip that doesn't reflect reality. This collapses the
five real inputs into ONE verdict:

  inputs : mode (LIVE_TEST single-shot), per-deployment risk.live arm, the two
           offline-first env gates (LIVE_AUTOPLACE_ARMED entries / LIVE_GUARD_ARMED
           auto-squares), and broker connectivity.
  output : would_transmit_entry / would_transmit_exit booleans + a single verdict
           label the UI renders unambiguously.

`compute_arm_state` is PURE (no I/O) so it is fully host-testable; the route in
live_broker.py assembles the inputs and calls it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def compute_arm_state(
    *,
    mode_doc: Optional[Dict[str, Any]],
    connected: bool,
    autoplace_armed: bool,
    guard_armed: bool,
    armed_deployment_count: int,
) -> Dict[str, Any]:
    """Collapse the five live-execution inputs into one verdict.

    Parameters
    ----------
    mode_doc:               the ModeStore singleton doc ({mode, single_shot_consumed}).
    connected:              a Flattrade token is stored (broker reachable).
    autoplace_armed:        LIVE_AUTOPLACE_ARMED env — the AUTO-entry transmit gate.
    guard_armed:            LIVE_GUARD_ARMED env — the AUTO-square transmit gate.
    armed_deployment_count: deployments currently armed-and-in-window for live.

    Returns a dict with the booleans + a single `verdict`/`label`.
    """
    mode = str((mode_doc or {}).get("mode") or "PAPER")
    single_shot_consumed = (mode_doc or {}).get("single_shot_consumed") is True

    # Manual single-shot ticket is armed when in LIVE_TEST with an unconsumed shot.
    manual_armed = mode == "LIVE_TEST" and not single_shot_consumed
    # Auto (deployment) path is armed when any deployment is armed-and-in-window.
    auto_armed = armed_deployment_count > 0

    # Would a NEW entry actually reach the broker right now?
    #   manual: LIVE_TEST single-shot (the manual Place transmits regardless of the
    #           AUTO env gate — it's a user-initiated click)
    #   auto:   a deployment is armed AND the auto-entry env gate is on
    would_transmit_entry = bool(connected and (manual_armed or (auto_armed and autoplace_armed)))
    # Would an AUTOMATIC guard square reach the broker? (user-clicked squares always
    # transmit; this is specifically the unattended software-guard exit.)
    would_transmit_exit = bool(connected and guard_armed)

    reasons: List[str] = []
    if not connected:
        reasons.append("broker not connected")
    if manual_armed:
        reasons.append("manual LIVE_TEST single-shot armed")
    if auto_armed and autoplace_armed:
        reasons.append(f"{armed_deployment_count} deployment(s) armed + LIVE_AUTOPLACE_ARMED on")
    elif auto_armed and not autoplace_armed:
        reasons.append(f"{armed_deployment_count} deployment(s) armed but LIVE_AUTOPLACE_ARMED off (dry-run)")
    reasons.append("guard transmits squares" if guard_armed else "guard dry-run (no real squares)")

    if would_transmit_entry:
        verdict = "LIVE"
        label = "LIVE — entries transmit real orders"
    elif manual_armed or auto_armed:
        verdict = "DRY_RUN"
        label = "Armed (dry-run) — no real entries until LIVE_AUTOPLACE_ARMED=1"
    else:
        verdict = "SAFE"
        label = "Safe — no live entries armed"

    return {
        "mode": mode,
        "single_shot_consumed": single_shot_consumed,
        "connected": bool(connected),
        "autoplace_armed": bool(autoplace_armed),
        "guard_armed": bool(guard_armed),
        "armed_deployments": int(armed_deployment_count),
        "would_transmit_entry": would_transmit_entry,
        "would_transmit_exit": would_transmit_exit,
        "verdict": verdict,
        "label": label,
        "reasons": reasons,
    }
