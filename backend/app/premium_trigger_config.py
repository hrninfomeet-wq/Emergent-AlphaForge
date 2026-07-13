"""Phase 4 engine dispatch — Declarative config block for premium-trigger strategies.

The user-facing goal: turn "premium_trigger_config is buildable" (which the AI
feasibility classifier now confidently returns after Phase 4.1) into an actual
executable runtime path so a strategy declared via config, NOT hard-coded strategy
id, can be run through the general Optimizer/Backtest Lab.

Session-2 scope is the BACKTEST PATH ONLY:

    caller  ->  PremiumTriggerConfig (validated, clamped)
            ->  to_backtest_params()  (translate to the dict shape the shipped
                                       run_premium_momentum_backtest expects)
            ->  premium_momentum_backtest.run_premium_momentum_backtest()
                (unchanged; the pure sim is untouched — byte-identical output
                 to the existing /api/premium-momentum/backtest bespoke route,
                 which is exactly what the Phase 4-5 spec §3.5 requires)

Deferred to a follow-up session (see docs/AGENT_HANDOFF_PROMPT.md):

    - deployment_evaluator dispatch (live path — needs a live-parity test)
    - Optimizer wiring (tune path — needs the tuner to accept the config block)
    - Frontend config builder (deployment creation UI)

Host-safe / pure: no motor, no LLM, no network. Existing routes/plugin behavior
are UNCHANGED — this is an ADDITIVE lift, not a rewrite.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------------------
# The declarative config block
# ---------------------------------------------------------------------------
class PremiumTriggerConfig(BaseModel):
    """Declarative, validated config for a premium-trigger deployment.

    Every field maps 1:1 to a knob in the shipped `premium_momentum_backtest.py`
    sim; nothing here invents new behavior. The point of this model is to give
    the app a NAMED SCHEMA (for the deployment UI, the Optimizer, the API, and
    the AI authoring output) instead of a bare Dict[str, Any].

    Kept strict (`extra="forbid"`) so a typo in a field name is caught at
    validation time instead of silently doing nothing at sim time.
    """
    model_config = ConfigDict(extra="forbid", frozen=False)

    # --- Reference / lock ----------------------------------------------------
    reference_time: str = Field(
        default="09:31",
        description="IST HH:MM at which the reference strike is locked + snapshot "
                    "premium recorded. Matches the shipped 09:31 default.",
    )
    moneyness: Literal["atm", "itm1", "itm2", "otm1", "otm2"] = Field(
        default="itm1",
        description="Strike-selection band relative to spot at reference time. "
                    "ITM1 = one strike in the money.",
    )
    side: Literal["ce", "pe", "first_to_trigger"] = Field(
        default="first_to_trigger",
        description="Which side(s) to arm. `first_to_trigger` locks BOTH CE and "
                    "PE and enters whichever premium crosses momentum first "
                    "(shipped premium_momentum behavior). CE/PE = single side only.",
    )

    # --- Entry trigger -------------------------------------------------------
    momentum_pct: Optional[float] = Field(
        default=None, ge=0.0, le=100.0,
        description="Entry when premium rises this % from the reference snapshot. "
                    "Exclusive with momentum_pts.",
    )
    momentum_pts: Optional[float] = Field(
        default=None, ge=0.0,
        description="Entry when premium rises this many points from the reference "
                    "snapshot. Exclusive with momentum_pct.",
    )

    # --- Exit knobs (all optional; sim treats None as disabled) -------------
    stop_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    stop_pts: Optional[float] = Field(default=None, ge=0.0)
    target_pct: Optional[float] = Field(default=None, ge=0.0, le=1000.0)
    target_pts: Optional[float] = Field(default=None, ge=0.0)

    # --- Stepped premium trail (X-Y ratchet) --------------------------------
    trail_x: Optional[float] = Field(
        default=None, ge=0.0, le=100.0,
        description="Premium-rise % that triggers a trail step (blueprint default 5).",
    )
    trail_y: Optional[float] = Field(
        default=None, ge=0.0, le=100.0,
        description="Amount the SL is trailed up by, per trail step, as % of "
                    "entry premium (blueprint default 5). Requires trail_x.",
    )

    # --- Sizing / lot count (per-leg) ---------------------------------------
    lots: int = Field(default=1, ge=1, le=100)

    # --- Optional: late-lock cutoff -----------------------------------------
    late_lock_cutoff: Optional[str] = Field(
        default=None,
        description="Optional HH:MM after which the reference-time lock is skipped "
                    "for a session (leaving it flat that day). None = no cutoff.",
    )

    # --- Optional: cost model passthrough -----------------------------------
    cost_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Passthrough to app.option_costs.CostConfig.from_dict. When "
                    "None, costs are disabled and net_pnl == gross_pnl (fields "
                    "always present so results are shape-stable).",
    )

    # --- Validators ---------------------------------------------------------
    @field_validator("reference_time", "late_lock_cutoff")
    @classmethod
    def _hhmm(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None if v == "" else v
        if not _HHMM_RE.match(v):
            raise ValueError(f"expected HH:MM (24-hour); got {v!r}")
        return v

    @model_validator(mode="after")
    def _entry_trigger_present(self) -> "PremiumTriggerConfig":
        """A premium-trigger strategy without any entry threshold is nonsensical."""
        if self.momentum_pct is None and self.momentum_pts is None:
            raise ValueError(
                "PremiumTriggerConfig requires one of momentum_pct or momentum_pts "
                "(the entry trigger). Set one; leave the other None."
            )
        if self.momentum_pct is not None and self.momentum_pts is not None:
            raise ValueError(
                "PremiumTriggerConfig accepts EITHER momentum_pct OR momentum_pts, "
                "not both. The sim would silently use momentum_pct and ignore "
                "momentum_pts, causing a bad-parity surprise."
            )
        return self

    @model_validator(mode="after")
    def _trail_pair_or_neither(self) -> "PremiumTriggerConfig":
        if (self.trail_x is None) != (self.trail_y is None):
            raise ValueError(
                "trail_x and trail_y must be set together (X-Y stepped trail) "
                "or both left as None (no trail). The shipped sim ignores a lone "
                "trail_x/trail_y."
            )
        return self

    # ---------------------------------------------------------------------
    # Serialization to the shipped-sim's dict shape.
    # ---------------------------------------------------------------------
    def to_backtest_params(self) -> Dict[str, Any]:
        """Translate to the exact `params` dict shape `run_premium_momentum_backtest`
        expects (see backend/app/premium_momentum_backtest.py::run_premium_momentum_backtest).

        This is the ONE place we couple to the shipped sim's parameter names. If
        the sim ever renames a knob, THIS function is the only site to update —
        the config schema (public API) stays stable.
        """
        params: Dict[str, Any] = {
            "reference_time": self.reference_time,
            "moneyness": self.moneyness,
            "side": self.side,
            "lots": self.lots,
        }
        # Entry trigger (one of the two is guaranteed present by the validator).
        if self.momentum_pct is not None:
            params["momentum_pct"] = self.momentum_pct
        if self.momentum_pts is not None:
            params["momentum_pts"] = self.momentum_pts
        # Exit knobs — omit None so the sim's "disabled" branch stays clean.
        for k in ("stop_pct", "stop_pts", "target_pct", "target_pts",
                  "trail_x", "trail_y"):
            v = getattr(self, k)
            if v is not None:
                params[k] = v
        if self.late_lock_cutoff:
            params["late_lock_cutoff"] = self.late_lock_cutoff
        if self.cost_config is not None:
            params["cost_config"] = self.cost_config
        return params


# ---------------------------------------------------------------------------
# Reverse: build a config from a plain dict. Used by the API route (which
# accepts a dict from the wire) and by future callers that want to reuse an
# existing bespoke-route param dict via the config-driven path.
# ---------------------------------------------------------------------------
def config_from_dict(d: Dict[str, Any]) -> PremiumTriggerConfig:
    """Validate a raw dict into a PremiumTriggerConfig. Raises ValidationError
    (Pydantic) on any typo / out-of-range value — this is the API layer's
    boundary check. Case-insensitive on side/moneyness for wire ergonomics."""
    d = dict(d or {})
    for key in ("side", "moneyness"):
        if isinstance(d.get(key), str):
            d[key] = d[key].strip().lower()
    return PremiumTriggerConfig(**d)


__all__: List[str] = ["PremiumTriggerConfig", "config_from_dict"]
