"""Deployment quality / acknowledgment checks (slice 9; gate-rigor pass 2026-06-13).

When a user creates a deployment from a saved preset or backtest_run, evaluate
the source for known red flags. Surface them as warnings - never block.
If any warning is present, the user must explicitly acknowledge before the
deployment is created.

Per user spec (2026-05-29): the app aids the user, never restricts. Even an
overfit-looking strategy can be deployed for paper-trading research as long
as the user makes a conscious choice. The acknowledgment is the conscious choice.

In-sample checks (always run, from the source's own backtest):
  - walk_forward_divergence: avg_oos_win_rate < avg_is_win_rate * 0.7 OR flag
  - low_trade_count        : metrics.trade_count < 30
  - weak_sharpe            : metrics.sharpe < 0.5
  - missing_walk_forward   : no walkforward result on the source
  - large_drawdown         : abs(max_dd_pts) > 0.15 * abs(total_pnl_pts)

Evidence-driven checks (gate-rigor pass — run ONLY when the caller supplies
`evidence`, gathered from the optimizer/WFO jobs; the gate used to be blind to
both of these, so an overfit or premium-bleeding strategy passed on in-sample
spot stability alone):
  - selection_bias    : the reported "best" is the MAX over N optimizer trials,
                        which inflates the in-sample Sharpe. We compute a
                        selection-bias-adjusted (deflated) Sharpe — observed
                        Sharpe minus the expected maximum of N noise trials
                        (Bailey & López de Prado, "false strategy" expected max)
                        — and warn when it's at/below 0 AND no out-of-sample run
                        confirms the edge.
  - option_oos_negative / missing_option_oos : the spot edge must survive real
                        premium decay, spread and charges. We consume the honest
                        option-rupee OOS (option-aware walk-forward) or the
                        option backtest, and warn when it's <= 0 or absent.

Every threshold is tunable via `QualityThresholds` so the operator can preview
the gate at stricter/looser settings rather than inheriting fixed constants.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Defaults (importable constants kept stable — UI copy + tests depend on them).
WALK_FORWARD_RATIO_THRESHOLD = 0.7   # OOS / IS below this -> overfit warning
MIN_TRADE_COUNT = 30
MIN_SHARPE = 0.5
MAX_DRAWDOWN_RATIO = 0.15            # |max_dd| / total_pnl > this -> warning
# Selection-bias (evidence-driven) defaults.
SELECTION_BIAS_MIN_TRIALS = 50       # only assess once the search is this wide
MIN_DEFLATED_SHARPE = 0.0            # deflated SR at/below this -> warn
WF_EFFICIENCY_MIN = 0.5              # honest-WFO efficiency >= this = strong OOS

ANNUALIZATION = 252                  # matches backtest.py Sharpe annualization
EULER_GAMMA = 0.5772156649015329     # Euler-Mascheroni, for the expected-max term


@dataclass
class QualityThresholds:
    """Tunable gate knobs. Defaults reproduce the historical (locked) behavior
    plus the new selection-bias / option-OOS checks."""
    min_trade_count: int = MIN_TRADE_COUNT
    min_sharpe: float = MIN_SHARPE
    walk_forward_ratio: float = WALK_FORWARD_RATIO_THRESHOLD
    max_drawdown_ratio: float = MAX_DRAWDOWN_RATIO
    selection_bias_min_trials: int = SELECTION_BIAS_MIN_TRIALS
    min_deflated_sharpe: float = MIN_DEFLATED_SHARPE
    wf_efficiency_min: float = WF_EFFICIENCY_MIN
    ruin_floor: float = 0.0            # equity-floor for ruin breach (rupees)
    min_coverage_ratio: float = 0.70   # paired / addressable below this -> coverage warning

    @classmethod
    def from_overrides(cls, **kw: Any) -> "QualityThresholds":
        base = cls()
        for key, value in kw.items():
            if value is not None and hasattr(base, key):
                setattr(base, key, value)
        return base


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _inv_norm_cdf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, |err|<1.2e-9).
    Used by expected_max_sharpe; avoids a scipy dependency on the test host."""
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)


def expected_max_sharpe(n_trials: Any, n_obs: Any, *, annualization: int = ANNUALIZATION) -> float:
    """Expected MAXIMUM annualized Sharpe over N independent zero-edge trials on
    n_obs observations — the "false strategy" benchmark (Bailey & López de Prado).
    A real best-of-N Sharpe must clear this to be distinguishable from luck.

    sd is the standard error of the annualized per-trade Sharpe estimate
    (~sqrt(annualization / n_obs), matching backtest.py's mean/std*sqrt(252)).
    Approximation: assumes iid returns (ignores skew/kurtosis); advisory only.
    """
    n = int(n_trials or 0)
    obs = int(n_obs or 0)
    if n < 2 or obs <= 1:
        return 0.0
    sd = math.sqrt(annualization / obs)
    z1 = _inv_norm_cdf(1.0 - 1.0 / n)
    z2 = _inv_norm_cdf(1.0 - 1.0 / (n * math.e))
    return sd * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(sharpe: Any, n_trials: Any, n_obs: Any, *, annualization: int = ANNUALIZATION) -> float:
    """Selection-bias-adjusted Sharpe = observed - expected_max_sharpe(N, n_obs).
    <= 0 means the observed Sharpe is within what searching N configs could
    produce on this sample by chance alone."""
    return round(_safe_float(sharpe) - expected_max_sharpe(n_trials, n_obs, annualization=annualization), 3)


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


def evaluate_source_quality(
    source_doc: Dict[str, Any],
    *,
    evidence: Optional[Dict[str, Any]] = None,
    thresholds: Optional[QualityThresholds] = None,
) -> Dict[str, Any]:
    """Evaluate a preset or backtest_run for deployment red flags.

    Pure function - no DB, no network. The caller resolves the source doc first,
    and optionally supplies `evidence` (n_trials + honest-WFO + option-rupee OOS,
    gathered from the optimizer jobs) to enable the selection-bias and option-OOS
    checks. With `evidence=None` the behavior is exactly the historical in-sample
    gate (so existing callers/tests are unchanged).
    """
    th = thresholds or QualityThresholds()
    metrics = _metrics(source_doc)
    wf = _walkforward(source_doc)
    om = source_doc.get("option_backtest")   # self-contained option result (Fix-B); also drives the dedup
    warnings: List[Dict[str, Any]] = []

    sharpe = metrics.get("sharpe")
    sharpe_val = _safe_float(sharpe) if sharpe is not None else None
    trade_count = int(_safe_float(metrics.get("trade_count")))

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
        if divergence_flag or (ratio is not None and ratio < th.walk_forward_ratio):
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
    if trade_count > 0 and trade_count < th.min_trade_count:
        warnings.append({
            "id": "low_trade_count",
            "severity": SEVERITY_WARNING,
            "label": "Low trade count",
            "detail": f"Source backtest has only {trade_count} trades (need >= {th.min_trade_count} for "
                      "statistically meaningful conclusions). Win rate and profit factor are unreliable on this sample.",
            "value": {"trade_count": trade_count, "min_recommended": th.min_trade_count},
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
    if sharpe_val is not None and sharpe_val < th.min_sharpe:
        warnings.append({
            "id": "weak_sharpe",
            "severity": SEVERITY_WARNING,
            "label": "Weak risk-adjusted return",
            "detail": f"Source backtest Sharpe ratio is {sharpe_val:.2f} (need >= {th.min_sharpe}). "
                      "Strategy barely beats noise on a risk-adjusted basis.",
            "value": {"sharpe": sharpe_val, "min_recommended": th.min_sharpe},
        })

    # 4. Large drawdown vs total return
    max_dd = abs(_safe_float(metrics.get("max_dd_pts")))
    total_pnl = _safe_float(metrics.get("total_pnl_pts"))
    if total_pnl > 0 and max_dd > 0:
        dd_ratio = max_dd / total_pnl
        if dd_ratio > th.max_drawdown_ratio:
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
                    "max_recommended_ratio": th.max_drawdown_ratio,
                },
            })

    # --- Evidence-driven checks (only when the caller gathered evidence) ------
    n_trials: Optional[int] = None
    dsr: Optional[float] = None
    option_oos_net: Optional[float] = None
    option_oos_source: Optional[str] = None
    if isinstance(evidence, dict):
        wfo_ev = evidence.get("wfo") or {}
        opt_ev = evidence.get("option_evidence") or {}
        raw_trials = evidence.get("n_trials")
        n_trials = int(raw_trials) if raw_trials else None

        # 5. Selection bias over the optimizer search (deflated Sharpe).
        if n_trials and sharpe_val is not None and trade_count > 0:
            dsr = deflated_sharpe(sharpe_val, n_trials, trade_count)
            eff = wfo_ev.get("efficiency")
            strong_oos = (
                eff is not None and _safe_float(eff) >= th.wf_efficiency_min
                and bool(wfo_ev.get("params_match"))
            )
            if n_trials >= th.selection_bias_min_trials and dsr <= th.min_deflated_sharpe and not strong_oos:
                warnings.append({
                    "id": "selection_bias",
                    "severity": SEVERITY_WARNING,
                    "label": "Selection bias from the optimizer search",
                    "detail": (
                        f"This result was the best of {n_trials} optimizer trials over {trade_count} trades. "
                        f"Its selection-bias-adjusted Sharpe is {dsr:.2f} (observed {sharpe_val:.2f} minus the "
                        f"expected best of {n_trials} zero-edge trials) — at/below {th.min_deflated_sharpe} it is "
                        "within what searching that many configurations could produce by luck, and no out-of-sample "
                        "run confirms it. Validate with an honest walk-forward before trusting the in-sample numbers."
                    ),
                    "value": {
                        "observed_sharpe": round(sharpe_val, 3),
                        "deflated_sharpe": dsr,
                        "expected_max_sharpe": round(expected_max_sharpe(n_trials, trade_count), 3),
                        "n_trials": n_trials,
                        "n_obs": trade_count,
                        "min_deflated_sharpe": th.min_deflated_sharpe,
                    },
                })

        # 6. Option-rupee OOS — does the spot edge survive premium/spread/costs?
        if wfo_ev.get("option_oos_net") is not None:
            option_oos_net = _safe_float(wfo_ev.get("option_oos_net"))
            option_oos_source = "option-aware walk-forward (OOS)"
        elif opt_ev.get("net_pnl_value") is not None:
            option_oos_net = _safe_float(opt_ev.get("net_pnl_value"))
            option_oos_source = f"option backtest ({opt_ev.get('kind') or 'run'})"

        if option_oos_net is not None and option_oos_net <= 0:
            warnings.append({
                "id": "option_oos_negative",
                "severity": SEVERITY_WARNING,
                "label": "Negative option-rupee result",
                "detail": (
                    f"The option-rupee evaluation ({option_oos_source}) nets ₹{option_oos_net:,.0f} — the spot "
                    "edge does NOT survive premium decay, bid-ask spread and charges. A spot-positive strategy "
                    "that loses on real option premium should not be deployed on the spot number alone."
                ),
                "value": {"net_pnl_value": option_oos_net, "source": option_oos_source,
                          "params_match": bool(wfo_ev.get("params_match") or opt_ev.get("params_match"))},
            })
        elif option_oos_net is None:
            warnings.append({
                "id": "missing_option_oos",
                "severity": SEVERITY_WARNING,
                "label": "No option-rupee validation",
                "detail": (
                    "No option-aware walk-forward or option backtest was found for these exact params, so the "
                    "spot backtest's edge is unproven on real premium (theta, spread, charges). Run an option "
                    "backtest or an option-aware walk-forward before relying on it."
                ),
                "value": None,
            })

    # --- Fix-B dedup: when a self-contained option result is present, the legacy
    # evidence-driven option-OOS warnings are superseded by option_full_window_negative.
    if om:
        warnings = [w for w in warnings if w["id"] not in ("option_oos_negative", "missing_option_oos")]

    # --- Option-rupee checks (Fix-B): self-contained from the source's option result ---
    om_net = None
    om_min_equity = None
    om_ratio = None
    if om:
        port = om.get("portfolio") or {}
        cov = om.get("coverage") or {}
        paired = cov.get("paired_trade_count") or 0
        spot = cov.get("spot_trade_count") or 0
        skipped = cov.get("skipped_by_cap") or 0
        om_net = port.get("net_pnl_value")
        oos_rp = (evidence or {}).get("oos_return_pct")

        # 1. Full-window fragility (gate on paired>0, strict <0; zero-pair routes to coverage)
        if paired > 0 and om_net is not None and om_net < 0:
            oos_positive = (oos_rp is not None and oos_rp > 0)
            wf_ok = wf is not None and not (((wf or {}).get("is_vs_oos") or {}).get("divergence_warning"))
            fragile = oos_positive or wf_ok
            if fragile:
                label = "Fragile: positive out-of-sample, negative full-window"
                detail = (f"Option result is ₹{om_net:,.0f} over the full window even though it looked "
                          "positive out-of-sample. The recent slice carried it - do not deploy on the OOS number alone.")
            else:
                label = "Negative full-window option result"
                detail = (f"Option result is ₹{om_net:,.0f} over the full window after premium decay, "
                          "bid-ask spread and charges.")
            warnings.append({
                "id": "option_full_window_negative", "severity": SEVERITY_WARNING,
                "label": label, "detail": detail,
                "value": {"net_pnl_value": om_net, "total_return_pct": port.get("total_return_pct"),
                          "oos_signal": oos_rp},
            })

        # 2. Ruin / equity-floor breach (empty-curve guarded)
        eqs = [c.get("equity_value") for c in (port.get("curve") or []) if c.get("equity_value") is not None]
        om_min_equity = min(eqs) if eqs else None
        ending = port.get("ending_equity")
        max_dd = port.get("max_drawdown_pct")
        if ((om_min_equity is not None and om_min_equity <= th.ruin_floor)
                or (ending is not None and ending < 0)
                or (abs(max_dd or 0) >= 100)):
            shown = om_min_equity if om_min_equity is not None else (ending if ending is not None else 0.0)
            warnings.append({
                "id": "ruin_floor_breach", "severity": SEVERITY_WARNING,
                "label": "Account ruin / equity-floor breach",
                "detail": (f"Equity reached ₹{shown:,.0f} (floor ₹{th.ruin_floor:,.0f}). The account would "
                           "be wiped, yet the backtest keeps trading past ruin - the rupee result is fiction."),
                "value": {"min_equity": om_min_equity, "ending_equity": ending,
                          "max_drawdown_pct": max_dd, "ruin_floor": th.ruin_floor},
            })

        # 3. Coverage attrition (DATA only; intentional cap-skips excluded)
        addressable = spot - skipped
        if addressable > 0 and (paired / addressable) < th.min_coverage_ratio:
            om_ratio = round(paired / addressable, 3)
            missing = (cov.get("missing_contract") or 0) + (cov.get("missing_entry_candle") or 0)
            warnings.append({
                "id": "coverage_attrition", "severity": SEVERITY_WARNING,
                "label": "Low option-data coverage",
                "detail": (f"Only {paired}/{addressable} non-capped signals ({round(100 * paired / addressable, 1)}%) "
                           f"paired with option data - {missing} missing option data "
                           f"({skipped} additionally skipped by daily caps). Result may not be representative."),
                "value": {"paired": paired, "spot": spot, "addressable": addressable, "ratio": om_ratio,
                          "skipped_by_cap": skipped, "missing_contract": cov.get("missing_contract"),
                          "missing_entry_candle": cov.get("missing_entry_candle")},
            })

    snapshot = {
        "trade_count": trade_count,
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "sharpe": metrics.get("sharpe"),
        "max_dd_pts": metrics.get("max_dd_pts"),
        "total_pnl_pts": metrics.get("total_pnl_pts"),
        "has_walkforward": wf is not None,
        # Evidence-driven (None when no evidence was supplied).
        "deflated_sharpe": dsr,
        "n_trials": n_trials,
        "option_oos_net": option_oos_net,
        "option_oos_source": option_oos_source,
        # Fix-B self-contained option-rupee snapshot (None for spot-only sources).
        "option_net_pnl_value": om_net,
        "option_min_equity": om_min_equity,
        "option_coverage_ratio": om_ratio,
    }

    return {
        "acknowledgment_required": len(warnings) > 0,
        "warnings": warnings,
        "metrics_snapshot": snapshot,
        "thresholds": {
            "min_trade_count": th.min_trade_count,
            "min_sharpe": th.min_sharpe,
            "walk_forward_ratio": th.walk_forward_ratio,
            "max_drawdown_ratio": th.max_drawdown_ratio,
            "selection_bias_min_trials": th.selection_bias_min_trials,
            "min_deflated_sharpe": th.min_deflated_sharpe,
            "wf_efficiency_min": th.wf_efficiency_min,
            "ruin_floor": th.ruin_floor,
            "min_coverage_ratio": th.min_coverage_ratio,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
