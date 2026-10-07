"""Entry-signal functions — one per pre-registered hypothesis. Pure: (market, cfg) -> decision.

Neither hypothesis has positive evidence (docs/scalping/02-measurements.md). They are implemented so
that the forward paper test is exactly the rule that was pre-registered — not a re-tuned variant.

A signal says WHICH contract to buy and WHY; liquidity/freshness/time filters are applied by the
engine (``engine.ScalperEngine._entry_filters``) so every hypothesis faces the same gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from app.scalping.config import ScalperConfig
from app.scalping.market import Contract, MarketState


@dataclass(frozen=True)
class EntrySignal:
    side: str                    # "CE" | "PE"
    contract: Contract
    reason: str
    metrics: Dict[str, float] = field(default_factory=dict)


def _pick(m: MarketState, cfg: ScalperConfig, side: str) -> Optional[Contract]:
    st = m.u[cfg.underlying]
    # strike chosen on the reference that triggered (synthetic forward when available)
    ref = st.synthetic if (cfg.signal_kind == "synthetic_impulse" and st.synthetic is not None) else st.spot
    return m.contract_for(cfg.underlying, side, expiry_rank=cfg.expiry_rank, offset=cfg.strike_offset,
                          ref_price=ref)


def synthetic_impulse(m: MarketState, cfg: ScalperConfig) -> Optional[EntrySignal]:
    """N1: |z_w| of the options-implied synthetic forward >= threshold -> buy in the move's direction.

    Falls back to NOTHING (not to the index) when the synthetic is unavailable: the hypothesis is
    about the synthetic, and silently switching reference would test a different rule."""
    z = m.z(cfg.underlying, "synthetic", cfg.window_s)
    age = m.ref_age_ms(cfg.underlying, "synthetic")
    if z is None or age is None or age > cfg.max_ref_age_ms:
        return None
    if z >= cfg.z_threshold:
        side = "CE"
    elif z <= -cfg.z_threshold:
        side = "PE"
    else:
        return None
    c = _pick(m, cfg, side)
    if c is None:
        return None
    return EntrySignal(side, c, f"synthetic z{cfg.window_s}={z:+.2f}", {"z": z, "ref_age_ms": float(age)})


def joint_impulse(m: MarketState, cfg: ScalperConfig) -> Optional[EntrySignal]:
    """S1-E: |z_w| of BOTH the traded index and the confirming index >= thresholds, same sign."""
    other = cfg.confirm_underlying
    if not other:
        return None
    z1 = m.z(cfg.underlying, "spot", cfg.window_s)
    z2 = m.z(other, "spot", cfg.window_s)
    a1 = m.ref_age_ms(cfg.underlying, "spot")
    a2 = m.ref_age_ms(other, "spot")
    if None in (z1, z2, a1, a2) or a1 > cfg.max_ref_age_ms or a2 > cfg.max_ref_age_ms:
        return None
    if z1 >= cfg.z_threshold and z2 >= cfg.confirm_z:
        side = "CE"
    elif z1 <= -cfg.z_threshold and z2 <= -cfg.confirm_z:
        side = "PE"
    else:
        return None
    c = _pick(m, cfg, side)
    if c is None:
        return None
    return EntrySignal(side, c, f"joint z{cfg.window_s} {cfg.underlying}={z1:+.2f} {other}={z2:+.2f}",
                       {"z": z1, "z_confirm": z2, "ref_age_ms": float(max(a1, a2))})


SIGNALS = {"synthetic_impulse": synthetic_impulse, "joint_impulse": joint_impulse}


def evaluate(m: MarketState, cfg: ScalperConfig) -> Optional[EntrySignal]:
    return SIGNALS[cfg.signal_kind](m, cfg)
