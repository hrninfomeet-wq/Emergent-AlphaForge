"""Deployment quality / acknowledgment checks (slice 9).

When a user creates a deployment from a saved preset or backtest_run, evaluate
the source for known red flags. Surface them as warnings - never block.
If any warning is present, the user must explicitly acknowledge before the
deployment is created.

Per user spec (2026-05-29): the app aids the user, never restricts. Even an
overfit-looking strategy can be deployed for paper-trading research as long
as the user makes a conscious choice. The acknowledgment is the conscious choice.

Checks:
  - walk_forward_divergence: avg_oos_win_rate < avg_is_win_rate * 0.7
                             OR divergence_warning flag is True
  - low_trade_count        : metrics.trade_count < 30
  - negative_sharpe        : metrics.sharpe < 0.5
  - missing_walk_forward   : no walkforward result on the source
  - large_drawdown         : abs(max_dd_pts) > 0.15 * abs(total_pnl_pts)
                             (only when total_pnl_pts > 0)

Returns a structured report:
  {
    "source_id": ...,
    "source_type": ...,
    "acknowledged_required": bool,
    "warnings": [{id, severity, label, detail, value}],
    "metrics_snapshot": {...},
    "computed_at": iso,
  }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

WALK_FORWARD_RATIO_THRESHOLD = 0.7   # OOS / IS below this -> overfit warning
MIN_TRADE_COUNT = 30
MIN_SHARPE = 0.5
MAX_DRAWDOWN_RATIO = 0.15            # |max_dd| / total_pnl > this -> warning


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _metrics(source_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the metrics dict whether the source is a preset or a backtest_run."""
    if isinstance(source_doc.get("metrics"), dict):
        return dict(source_doc["metrics"])
    config = source_doc.get("config")
    if isinstance(config, dict) and isinstance(config.get("metrics"), dict):
        return dict(config["metrics"])
    return {}


def _walkforward(source_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve walkforward block from the source if present."""
    wf = source_doc.get("walkforward")
    if isinstance(wf, dict):
        return wf
    return None


def evaluate_source_quality(source_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate a preset or backtest_run for deployment red flags.

    Pure function - no DB, no network. Caller resolves the source doc first.
    """
    metrics = _metrics(source_doc)
    wf = _walkforward(source_doc)
    warnings: List[Dict[str, Any]] = []

    # 1. Walk-forward divergence
    if wf is None:
        warnings.append({
            "id": "missing_walk_forward",
            "severity": SEVERITY_WARNING,
            "label": "No walk-forward validation",
            "detail": "The source backtest does not include in-sample / out-of-sample validation. "
                      "Forward results may diverge significantly from in-sample performance.",
            "value": None,
        })
    else:
        is_vs_oos = wf.get("is_vs_oos") or {}
        avg_is_wr = _safe_float(is_vs_oos.get("avg_is_win_rate"))
        avg_oos_wr = _safe_float(is_vs_oos.get("avg_oos_win_rate"))
        divergence_flag = bool(is_vs_oos.get("divergence_warning"))
        ratio = (avg_oos_wr / avg_is_wr) if avg_is_wr > 0 else None
        if divergence_flag or (ratio is not None and ratio < WALK_FORWARD_RATIO_THRESHOLD):
            ratio_str = f"{ratio:.2f}" if ratio is not None else "n/a"
            warnings.append({
                "id": "walk_forward_divergence",
                "severity": SEVERITY_WARNING,
                "label": "Walk-forward IS/OOS divergence",
                "detail": (
                    f"In-sample win rate {avg_is_wr:.1f}% vs out-of-sample {avg_oos_wr:.1f}% "
                    f"(ratio {ratio_str}). "
                    "Strategy may be overfit to historical data; live results may underperform."
                ),
                "value": {
                    "avg_is_win_rate": avg_is_wr,
                    "avg_oos_win_rate": avg_oos_wr,
                    "ratio": ratio,
                    "divergence_flag": divergence_flag,
                },
            })

    # 2. Low trade count
    trade_count = int(_safe_float(metrics.get("trade_count")))
    if trade_count > 0 and trade_count < MIN_TRADE_COUNT:
        warnings.append({
            "id": "low_trade_count",
            "severity": SEVERITY_WARNING,
            "label": "Low trade count",
            "detail": f"Source backtest has only {trade_count} trades (need >= {MIN_TRADE_COUNT} for "
                      "statistically meaningful conclusions). Win rate and profit factor are unreliable on this sample.",
            "value": {"trade_count": trade_count, "min_recommended": MIN_TRADE_COUNT},
        })
    elif trade_count == 0:
        warnings.append({
            "id": "missing_trade_count",
            "severity": SEVERITY_WARNING,
            "label": "Trade count not available",
            "detail": "Source backtest does not report a trade count. Cannot assess sample-size reliability.",
            "value": None,
        })

    # 3. Negative or weak Sharpe
    sharpe = metrics.get("sharpe")
    if sharpe is not None:
        sharpe_val = _safe_float(sharpe)
        if sharpe_val < MIN_SHARPE:
            warnings.append({
                "id": "weak_sharpe",
                "severity": SEVERITY_WARNING,
                "label": "Weak risk-adjusted return",
                "detail": f"Source backtest Sharpe ratio is {sharpe_val:.2f} (need >= {MIN_SHARPE}). "
                          "Strategy barely beats noise on a risk-adjusted basis.",
                "value": {"sharpe": sharpe_val, "min_recommended": MIN_SHARPE},
            })

    # 4. Large drawdown vs total return
    max_dd = abs(_safe_float(metrics.get("max_dd_pts")))
    total_pnl = _safe_float(metrics.get("total_pnl_pts"))
    if total_pnl > 0 and max_dd > 0:
        dd_ratio = max_dd / total_pnl
        if dd_ratio > MAX_DRAWDOWN_RATIO:
            warnings.append({
                "id": "large_drawdown",
                "severity": SEVERITY_WARNING,
                "label": "Large drawdown vs total return",
                "detail": f"Max drawdown is {max_dd:.1f} pts vs total return of {total_pnl:.1f} pts "
                          f"({dd_ratio*100:.0f}% drawdown ratio). Capital efficiency is poor.",
                "value": {
                    "max_dd_pts": max_dd,
                    "total_pnl_pts": total_pnl,
                    "drawdown_ratio": round(dd_ratio, 3),
                    "max_recommended_ratio": MAX_DRAWDOWN_RATIO,
                },
            })

    snapshot = {
        "trade_count": trade_count,
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "sharpe": metrics.get("sharpe"),
        "max_dd_pts": metrics.get("max_dd_pts"),
        "total_pnl_pts": metrics.get("total_pnl_pts"),
        "has_walkforward": wf is not None,
    }

    return {
        "acknowledgment_required": len(warnings) > 0,
        "warnings": warnings,
        "metrics_snapshot": snapshot,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
