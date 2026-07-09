"""Pure validation for an optimizer request with survival_config enabled.
Returns an error string (for HTTP 400) or None when valid. Host-testable."""
from __future__ import annotations
from typing import Optional


def validate_survival_request(
    *, enabled: bool, evaluation_mode: str,
    costs_enabled: bool, capital: float, ruin_floor: float,
    max_drawdown_pct: float, max_ror_pct: float,
) -> Optional[str]:
    if not enabled:
        return None
    # In the OPTIMIZER, option execution IS "option_rerank" mode — there is no
    # separate option_config.enabled flag like the backtest has (the re-rank always
    # pairs options). So requiring option_rerank mode is the option-execution gate.
    if evaluation_mode != "option_rerank":
        return ("Survival mode requires evaluation_mode='option_rerank' — option execution and "
                "the rupee gate both live in the re-rank (rupee equity is impossible spot-only).")
    if not costs_enabled:
        return ("Survival mode requires option costs enabled "
                "(option_config.cost_config.enabled=true) — else risk-of-ruin/Calmar "
                "run on GROSS option P&L (no spread/brokerage/STT), and index-option "
                "spread alone flips marginal survivors.")
    if not (0.0 <= float(ruin_floor) < float(capital)):
        return f"ruin_floor must be 0 <= ruin_floor < capital ({capital})."
    if not (0 < float(max_drawdown_pct) <= 100):
        return "max_drawdown_pct must be in (0, 100]."
    if not (0 < float(max_ror_pct) <= 100):
        return "max_ror_pct must be in (0, 100]."
    return None
