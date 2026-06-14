"""Pure validation for an optimizer request with survival_config enabled.
Returns an error string (for HTTP 400) or None when valid. Host-testable."""
from __future__ import annotations
from typing import Any, Dict, Optional


def validate_survival_request(
    *, enabled: bool, evaluation_mode: str, option_config: Optional[Dict[str, Any]],
    costs_enabled: bool, capital: float, ruin_floor: float,
    max_drawdown_pct: float, max_ror_pct: float,
) -> Optional[str]:
    if not enabled:
        return None
    if evaluation_mode != "option_rerank":
        return "Survival mode requires evaluation_mode='option_rerank' (the rupee gate lives in the re-rank)."
    if not (option_config and option_config.get("enabled")):
        return "Survival mode requires option execution enabled (rupee equity is impossible spot-only)."
    if not costs_enabled:
        return "Survival mode requires costs_enabled=true (else risk-of-ruin/Calmar run on gross P&L)."
    if not (0.0 <= float(ruin_floor) < float(capital)):
        return f"ruin_floor must be 0 <= ruin_floor < capital ({capital})."
    if not (0 < float(max_drawdown_pct) <= 100):
        return "max_drawdown_pct must be in (0, 100]."
    if not (0 < float(max_ror_pct) <= 100):
        return "max_ror_pct must be in (0, 100]."
    return None
