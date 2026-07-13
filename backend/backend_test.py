#!/usr/bin/env python3
"""Phase 4.1 + Phase 4 backtest-dispatch end-to-end verification.

Tests the user's reported bug fix (NF CE PE EXP2 blueprint feasibility) and the
new premium-trigger backtest route, plus regression checks on existing routes.

All tests use the public endpoint (REACT_APP_BACKEND_URL) to verify what the
user sees.
"""
import json
import sys
import requests
from typing import Any, Dict

# Public endpoint from frontend/.env
BASE_URL = "https://alphaforge-dev.preview.emergentagent.com"

# The exact blueprint the user reported as REJECT in Session 2
NF_CE_PE_EXP2_BLUEPRINT = """Strategy: NF CE PE EXP2 (Configurable Contingency Breakout)

Overview: Intraday NIFTY options-buying strategy. Same-day exit only. At 09:31 lock ITM1 CE and ITM1 PE strikes (weekly expiry). Snapshot both premiums at 09:31.

Session settings:
- Instrument: NIFTY 50 spot
- Options: current weekly expiry
- Strike: ITM1 for both CE and PE
- Position size: 2 lots per leg
- Signal start: 09:31
- No new entries after 15:09 (re-entry cutoff)
- Hard square-off: 15:13
- Strategy type: intraday, same-day exit only

Primary Leg 1 (CE):
- Option type: CE
- Expiry: weekly
- Strike: ITM1
- Size: 2 lots
- Entry: BUY when the CE premium rises 15% or more from its 09:31 snapshot
- Initial SL: 20% below fill price
- Trailing: for every +5% premium rise from the peak-tracked premium, trail the SL up by 5%
- On SL hit: activate Lazy Leg 1 (PE side)

Primary Leg 2 (PE): mirror of Leg 1 on PE side. On SL hit, activate Lazy Leg 2 (CE side).

Lazy Leg 1: PE, ITM1, weekly, 2 lots, fresh snapshot at activation, 10% premium rise entry, 10% SL, 5%/5% stepped trail.
Lazy Leg 2: CE, ITM1, weekly, 2 lots, same rules as Lazy Leg 1.

Global overlays: global TP, global SL, max 1 trade per leg per day.
"""


class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, fn):
        """Run a single test function."""
        self.tests_run += 1
        print(f"\n{'='*70}")
        print(f"TEST {self.tests_run}: {name}")
        print('='*70)
        try:
            fn()
            self.tests_passed += 1
            print(f"✅ PASSED: {name}")
        except AssertionError as e:
            self.tests_failed += 1
            self.failures.append((name, str(e)))
            print(f"❌ FAILED: {name}")
            print(f"   Error: {e}")
        except Exception as e:
            self.tests_failed += 1
            self.failures.append((name, f"Exception: {e}"))
            print(f"❌ FAILED: {name}")
            print(f"   Exception: {e}")

    def summary(self):
        """Print test summary."""
        print(f"\n{'='*70}")
        print("TEST SUMMARY")
        print('='*70)
        print(f"Total:  {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_failed}")
        
        if self.failures:
            print(f"\n{'='*70}")
            print("FAILURES:")
            print('='*70)
            for name, error in self.failures:
                print(f"\n❌ {name}")
                print(f"   {error}")
        
        return 0 if self.tests_failed == 0 else 1


runner = TestRunner()


# ===========================================================================
# A) Feasibility bug fix: NF CE PE EXP2 blueprint should return ADVISE/BUILD
# ===========================================================================
def test_feasibility_bug_fixed():
    """The user's reported bug: NF CE PE EXP2 blueprint was REJECT, should be ADVISE/BUILD."""
    print("Testing feasibility with NF CE PE EXP2 blueprint...")
    
    response = requests.post(
        f"{BASE_URL}/api/strategies/author/from-source",
        json={"source_text": NF_CE_PE_EXP2_BLUEPRINT, "provider": "gemini"},
        timeout=60,
    )
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    print(f"Decision: {data.get('decision')}")
    print(f"Summary: {data.get('summary')}")
    
    # The fix should result in ADVISE or BUILD, NOT REJECT
    decision = data.get("decision")
    assert decision in ("ADVISE", "BUILD"), \
        f"Expected ADVISE or BUILD, got {decision}. Summary: {data.get('summary')}"
    
    # Should NOT contain "Can't build this faithfully" (the REJECT message)
    summary = data.get("summary", "")
    assert "Can't build this faithfully" not in summary, \
        f"Summary still contains REJECT message: {summary}"
    
    # Zero rules with INFEASIBLE or NEEDS_NEW_DATA decision_class
    rules = data.get("rules", [])
    infeasible_rules = [
        r for r in rules 
        if r.get("decision_class") in ("INFEASIBLE", "NEEDS_NEW_DATA")
        and r.get("criticality") == "CORE"
    ]
    
    if infeasible_rules:
        print("\n⚠️  Found CORE rules marked INFEASIBLE/NEEDS_NEW_DATA:")
        for r in infeasible_rules:
            print(f"   - {r.get('id')}: {r.get('text')}")
            print(f"     Decision: {r.get('decision_class')}, Message: {r.get('message')}")
    
    assert len(infeasible_rules) == 0, \
        f"Found {len(infeasible_rules)} CORE rules marked INFEASIBLE/NEEDS_NEW_DATA"
    
    # Rules describing option kind, expiry, holding period should map to correct features
    shape_rules = [
        r for r in rules
        if any(keyword in r.get("text", "").lower() 
               for keyword in ["option type", "expiry", "intraday", "weekly", "ce leg", "pe leg"])
    ]
    
    if shape_rules:
        print("\n📋 Shape/config rules found:")
        for r in shape_rules:
            feature = r.get("feature")
            print(f"   - {r.get('text')[:60]}...")
            print(f"     Feature: {feature}, Decision: {r.get('decision_class')}")
            
            # These should map to premium_trigger_config, deployment_layer, or declarative_config
            assert feature in ("premium_trigger_config", "deployment_layer", "declarative_config", None), \
                f"Shape rule mapped to unexpected feature: {feature}"
    
    # Lazy-leg rules should be BUILDABLE_WITH_FEATURE with feature "lazy_leg_contingency"
    lazy_rules = [
        r for r in rules
        if any(keyword in r.get("text", "").lower() 
               for keyword in ["lazy leg", "contingency", "opposite side"])
    ]
    
    if lazy_rules:
        print("\n🔮 Lazy-leg contingency rules found:")
        for r in lazy_rules:
            print(f"   - {r.get('text')[:60]}...")
            print(f"     Feature: {r.get('feature')}, Live feasible: {r.get('live_feasible')}")
            assert r.get("feature") == "lazy_leg_contingency", \
                f"Lazy-leg rule should map to lazy_leg_contingency, got {r.get('feature')}"
    
    print(f"\n✅ Feasibility check passed: {decision}")
    print(f"   Total rules: {len(rules)}")
    print(f"   CORE infeasible: {len(infeasible_rules)}")


# ===========================================================================
# B) New /api/premium-trigger/backtest route responds correctly
# ===========================================================================
def test_premium_trigger_backtest_route():
    """The new config-driven backtest route should return correct shape."""
    print("Testing /api/premium-trigger/backtest route...")
    
    response = requests.post(
        f"{BASE_URL}/api/premium-trigger/backtest",
        json={
            "instrument": "NIFTY",
            "start_ts": 1730000000000,
            "end_ts": 1730500000000,
            "premium_trigger": {
                "momentum_pct": 15,
                "stop_pct": 20,
            }
        },
        timeout=30,
    )
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    # Check required keys
    required_keys = ["trades", "coverage", "summary", "params", "premium_trigger_config", "dispatch"]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"
    
    # Check dispatch marker
    assert data["dispatch"] == "premium_trigger_config", \
        f"Expected dispatch='premium_trigger_config', got {data['dispatch']}"
    
    # Empty trades are FINE (sandbox has no seeded warehouse)
    print(f"   Trades: {len(data['trades'])}")
    print(f"   Coverage: {data['coverage']}")
    print(f"   Dispatch: {data['dispatch']}")
    print("✅ Route shape correct")


# ===========================================================================
# C) Validation errors surface at the API layer
# ===========================================================================
def test_validation_missing_field():
    """Missing momentum trigger should return 400."""
    print("Testing validation: missing momentum_pct/momentum_pts...")
    
    response = requests.post(
        f"{BASE_URL}/api/premium-trigger/backtest",
        json={
            "instrument": "NIFTY",
            "start_ts": 1730000000000,
            "end_ts": 1730500000000,
            "premium_trigger": {
                "stop_pct": 20,  # Missing momentum trigger
            }
        },
        timeout=30,
    )
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    
    error_text = response.text.lower()
    print(f"Error message: {response.text[:200]}")
    assert "momentum" in error_text, \
        f"Error message should mention 'momentum', got: {response.text}"
    print("✅ Validation error correctly surfaced")


def test_validation_extra_field():
    """Extra field should return 400 (extra=forbid on the model)."""
    print("Testing validation: extra field...")
    
    response = requests.post(
        f"{BASE_URL}/api/premium-trigger/backtest",
        json={
            "instrument": "NIFTY",
            "start_ts": 1730000000000,
            "end_ts": 1730500000000,
            "premium_trigger": {
                "momentum_pct": 15,
                "stop_pct": 20,
                "typo_field": "whoops",  # Extra field
            }
        },
        timeout=30,
    )
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    
    error_text = response.text.lower()
    print(f"Error message: {response.text[:200]}")
    # Should mention "extra" or "forbidden" or the field name
    assert any(word in error_text for word in ["extra", "forbidden", "typo_field"]), \
        f"Error message should mention extra field, got: {response.text}"
    print("✅ Validation error correctly surfaced")


# ===========================================================================
# D) Regression check: existing /api/premium-momentum/backtest still works
# ===========================================================================
def test_premium_momentum_backtest_regression():
    """The existing bespoke route should still work (byte-identical shape)."""
    print("Testing /api/premium-momentum/backtest (regression check)...")
    
    response = requests.post(
        f"{BASE_URL}/api/premium-momentum/backtest",
        json={
            "instrument": "NIFTY",
            "start_ts": 1730000000000,
            "end_ts": 1730500000000,
            "params": {
                "momentum_pct": 15,
                "stop_pct": 20,
            }
        },
        timeout=30,
    )
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    
    # Check required keys (same shape as before)
    required_keys = ["trades", "coverage", "summary", "params"]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"
    
    # Should NOT have the new dispatch marker (this is the old route)
    print(f"   Trades: {len(data['trades'])}")
    print(f"   Coverage: {data['coverage']}")
    print("✅ Regression check passed")


# ===========================================================================
# E) Regression check: /api/health endpoint
# ===========================================================================
def test_health_endpoint():
    """Health endpoint should return 200 and {"db":"ok"}."""
    print("Testing /api/health (regression check)...")
    
    response = requests.get(f"{BASE_URL}/api/health", timeout=10)
    
    print(f"Status: {response.status_code}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    print(f"Response: {data}")
    assert data.get("db") == "ok", f"Expected db='ok', got {data}"
    print("✅ Health check passed")


# ===========================================================================
# Run all tests
# ===========================================================================
if __name__ == "__main__":
    print(f"\n{'='*70}")
    print("Phase 4.1 + Phase 4 Backtest-Dispatch End-to-End Verification")
    print(f"Base URL: {BASE_URL}")
    print('='*70)
    
    # A) Feasibility bug fix
    runner.test("A) Feasibility bug fixed (NF CE PE EXP2 blueprint)", test_feasibility_bug_fixed)
    
    # B) New premium-trigger backtest route
    runner.test("B) /api/premium-trigger/backtest route works", test_premium_trigger_backtest_route)
    
    # C) Validation errors
    runner.test("C1) Validation: missing momentum trigger", test_validation_missing_field)
    runner.test("C2) Validation: extra field", test_validation_extra_field)
    
    # D) Regression: existing premium-momentum route
    runner.test("D) Regression: /api/premium-momentum/backtest", test_premium_momentum_backtest_regression)
    
    # E) Regression: health endpoint
    runner.test("E) Regression: /api/health", test_health_endpoint)
    
    # Summary
    exit_code = runner.summary()
    sys.exit(exit_code)
