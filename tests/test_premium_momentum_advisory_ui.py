"""Phase 5B B8 — advisory surface + UI states (premium_momentum multi-leg).

Recon correction 5 (docs/superpowers/plans/2026-07-15-premium-momentum-phase5b-execution.md):
`build_arm_advisories`/`deploymentMetrics` had ZERO frontend consumers before this
task — `deploymentMetrics` was defined in `frontend/src/lib/api.js` but never called
anywhere. This file pins:

 1. Backend — a NEW non-blocking `premium_edge_verdict` advisory
    (`app/forward_metrics.py`), attached at BOTH `arm_advisories` sites in
    `app/routers/deployments.py` (GET .../metrics and POST .../live/arm), gated on
    strategy_id == "premium_momentum" AND (leg_mode == "both" OR lazy_enabled). It
    points at docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md (the CLOSED, gate-FAILED
    edge hunt) so an operator arming multi-leg live money sees that context honestly.
 2. The arm/disarm GATING path (`arm_deployment_live`) never reads `arm_advisories`
    — it is assigned to the response AFTER every guard check has already passed and
    AFTER risk.live has already been written to the DB. Advisory only.
 3. Frontend — `DeployToLivePanel.jsx` is the FIRST consumer of
    `api.deploymentMetrics`; it fetches on panel open and renders `arm_advisories`
    (if any) above the ARM button, behind a silent-degrade catch.
 4. Frontend — `LiveDeploymentStrip.jsx` gained 4 new refusal labels in its
    `entryErrorLabel` map: vix_gate, vix_unverifiable, day_stop, and the historical
    (removed in B7, d110a1e) both_mode_live_pending_b6_b7 interim-guard reason.

These are host string-pins over source, matching the repo's standard for JSX/wiring
assertions (see tests/test_premium_native_backtestlab_surfacing.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))


# --- 1. backend: premium_edge_verdict advisory ---------------------------------

def test_forward_metrics_defines_premium_edge_verdict_advisory():
    src = (_ROOT / "backend" / "app" / "forward_metrics.py").read_text(encoding="utf-8")
    assert "def premium_edge_verdict_advisory(" in src
    i = src.index("def premium_edge_verdict_advisory(")
    body = src[i:i + 1800]
    # Gating condition: premium_momentum strategy AND (both-mode OR lazy).
    assert 'strategy_id != "premium_momentum"' in body
    assert 'leg_mode = str(merged_params.get("leg_mode")' in body
    assert 'lazy_enabled = bool(merged_params.get("lazy_enabled"))' in body
    assert 'if leg_mode != "both" and not lazy_enabled:' in body
    assert '"id": "premium_edge_verdict"' in body
    assert '"severity": "warning"' in body
    # Message content pins the verdict's actual numbers + the doc pointer, so a
    # future edit can't silently soften or lose the honest context.
    assert "PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md" in src
    assert "153.8k" in src
    assert "capability build, not a validated edge" in src


def test_deployments_router_attaches_premium_advisory_at_both_arm_advisories_sites():
    src = (_ROOT / "backend" / "app" / "routers" / "deployments.py").read_text(encoding="utf-8")
    assert "premium_edge_verdict_advisory" in src
    assert "def _premium_edge_verdict_advisory_for(" in src
    # The gating helper resolves merged_params via the strategy registry — the
    # same pattern used everywhere else in the codebase (deployment_evaluator.py),
    # so a pre-5B deployment with no stored leg_mode still reads the honest
    # plugin-schema default instead of crashing or silently skipping.
    helper_i = src.index("def _premium_edge_verdict_advisory_for(")
    helper_body = src[helper_i:helper_i + 1200]
    assert 'strategy_obj.merged_params(deployment.get("params") or {})' in helper_body
    assert 'strategy_id != "premium_momentum"' in helper_body

    # Both attachment sites: immediately after `out["arm_advisories"] = build_arm_advisories(fwd)`.
    marker = 'out["arm_advisories"] = build_arm_advisories(fwd)'
    count = src.count(marker)
    assert count == 2, f"expected exactly 2 build_arm_advisories sites (recon), found {count}"
    for i, start in enumerate(_all_indices(src, marker)):
        window = src[start:start + 300]
        assert "_premium_edge_verdict_advisory_for(deployment)" in window, (
            f"site #{i+1} at offset {start} does not append the premium advisory"
        )
        assert 'out["arm_advisories"].append(' in window


def test_premium_advisory_is_informational_only_arm_gate_untouched():
    """The arm/disarm decision (arm_deployment_live) must be provably unaffected:
    every HTTPException guard + the risk.live DB write happen BEFORE the
    arm_advisories line, and arm_advisories is never read as a condition anywhere
    in the router (only ever assigned/appended-to, never branched on)."""
    src = (_ROOT / "backend" / "app" / "routers" / "deployments.py").read_text(encoding="utf-8")
    arm_i = src.index('@api.post("/deployments/{deployment_id}/live/arm")')
    advisories_i = src.index('out["arm_advisories"] = build_arm_advisories(fwd)', arm_i)
    arm_route_body = src[arm_i:advisories_i]
    # All 6 documented arm guards + the risk.live persistence happen before the
    # advisory line in THIS route.
    assert "Deployment must be ACTIVE to arm" in arm_route_body
    assert "is retired" in arm_route_body
    assert "paused for strategy source drift" in arm_route_body
    assert "Flattrade not connected" in arm_route_body
    assert "Live engine cannot trade" in arm_route_body
    assert "Cannot arm after 15:00 IST" in arm_route_body
    assert '"risk": risk, "updated_at": now.isoformat()' in arm_route_body
    # No conditional anywhere in the router keys off arm_advisories (it is only
    # ever: imported, assigned, or appended-to — never read back as a branch
    # condition to gate behavior).
    allowed_line_shapes = (
        "build_arm_advisories,",                                  # import
        'out["arm_advisories"] = build_arm_advisories(fwd)',      # assignment (x2)
        'out["arm_advisories"].append(_pm_advisory)',              # append (x2)
    )
    offending = [
        line for line in src.splitlines()
        if "arm_advisories" in line and line.strip() not in allowed_line_shapes
    ]
    assert not offending, f"unexpected arm_advisories usage (possible gating read): {offending!r}"


def _all_indices(haystack: str, needle: str):
    out = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            return out
        out.append(idx)
        start = idx + 1


# --- 2. frontend: DeployToLivePanel is the first deploymentMetrics consumer ----

def test_deploy_to_live_panel_fetches_metrics_and_renders_advisories():
    src = (_ROOT / "frontend" / "src" / "components" / "live" / "DeployToLivePanel.jsx").read_text(encoding="utf-8")
    assert "api.deploymentMetrics(dep.id)" in src
    assert "armAdvisories" in src
    assert 'data-testid="arm-advisories"' in src
    # Fetch is keyed on formOpen (panel open) and degrades silently on failure —
    # never blocks/breaks the arm flow.
    i = src.index("api.deploymentMetrics(dep.id)")
    window = src[max(0, i - 400):i + 400]
    assert "formOpen" in window
    assert ".catch(() => {" in window
    # Rendered chips sit above the ARM button (data-testid="deploy-to-live-arm-submit").
    advisories_block_i = src.index('data-testid="arm-advisories"')
    arm_button_i = src.index('data-testid="deploy-to-live-arm-submit"')
    assert advisories_block_i < arm_button_i, "advisory chips must render ABOVE the arm button"


def test_api_deployment_metrics_helper_exists():
    src = (_ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    assert "deploymentMetrics: (id) =>" in src
    assert "/deployments/${id}/metrics" in src


# --- 3. frontend: LiveDeploymentStrip refusal labels ----------------------------

def test_strip_entry_error_label_map_has_new_premium_labels():
    src = (_ROOT / "frontend" / "src" / "components" / "live" / "LiveDeploymentStrip.jsx").read_text(encoding="utf-8")
    i = src.index("function entryErrorLabel(reason)")
    body = src[i:i + 1600]
    expected = {
        "vix_gate": "VIX gate blocked the session",
        "vix_unverifiable": "VIX unverifiable - session skipped",
        "day_stop": "session day-stop hit",
        "both_mode_live_pending_b6_b7": "multi-leg live was pending completion",
    }
    for key, label in expected.items():
        assert f"{key}:" in body, f"entryErrorLabel map missing key {key!r}"
        assert label in body, f"entryErrorLabel map missing label {label!r} for {key!r}"
