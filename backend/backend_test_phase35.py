"""
AlphaForge Phase 3.5 Quick Backend Tests
Focus on the 3 user-reported fixes:
1. best_backtest_run_id creation + verification
2. Cancel API (running, non-existent, already-done)
3. Preset creation from optimizer
"""
import time
import requests

BASE_URL = "https://algo-trading-lab-7.preview.emergentagent.com/api"

def log(msg):
    print(f"[TEST] {msg}")

def test_phase35():
    log("=" * 70)
    log("Phase 3.5 Backend Tests — User-Reported Fixes")
    log("=" * 70)
    
    # Test 1: Start optimization with n_trials=15 (fast)
    log("\n1️⃣  Starting optimization with n_trials=15...")
    payload = {
        "instrument": "NIFTY",
        "strategy_id": "confluence_scalper",
        "method": "bayesian",
        "objective": "risk_adjusted",
        "n_trials": 15,
        "name": "Phase 3.5 Test"
    }
    r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
    assert r.status_code == 200, f"Start failed: {r.status_code} {r.text}"
    job_id = r.json()["job_id"]
    log(f"✅ Job started: {job_id[:8]}")
    
    # Test 2: Poll until done
    log("\n2️⃣  Polling job until done...")
    start = time.time()
    while time.time() - start < 60:
        r = requests.get(f"{BASE_URL}/optimize/jobs/{job_id}")
        job = r.json()
        status = job.get("status")
        trials = job.get("n_trials_completed", 0)
        log(f"   Status: {status}, Trials: {trials}/15")
        if status == "done":
            break
        if status == "failed":
            log(f"❌ Job failed: {job.get('error')}")
            return False
        time.sleep(3)
    
    if job["status"] != "done":
        log(f"❌ Job did not complete in 60s")
        return False
    
    log(f"✅ Job completed: best_value={job.get('best_value', 'N/A')}")
    
    # Test 3: Verify best_backtest_run_id exists
    log("\n3️⃣  Verifying best_backtest_run_id...")
    assert "best_backtest_run_id" in job, "❌ Missing best_backtest_run_id"
    run_id = job["best_backtest_run_id"]
    assert run_id is not None, "❌ best_backtest_run_id is None"
    log(f"✅ best_backtest_run_id: {run_id[:8]}")
    
    # Test 4: Verify backtest run exists in /api/backtest/runs
    log("\n4️⃣  Verifying backtest run in /api/backtest/runs...")
    r = requests.get(f"{BASE_URL}/backtest/runs")
    runs = r.json()["items"]
    run = next((r for r in runs if r["id"] == run_id), None)
    assert run is not None, f"❌ Run {run_id} not found in /api/backtest/runs"
    assert run["name"].startswith("Optimized · "), f"❌ Run name should start with 'Optimized · ', got {run['name']}"
    assert run["config"].get("optimization_job_id") == job_id, "❌ optimization_job_id mismatch"
    log(f"✅ Found run: {run['name']}")
    
    # Test 5: Verify full backtest data
    log("\n5️⃣  Verifying full backtest data via GET /api/backtest/runs/{run_id}...")
    r = requests.get(f"{BASE_URL}/backtest/runs/{run_id}")
    assert r.status_code == 200, f"❌ Failed to get run: {r.status_code}"
    full_run = r.json()
    assert "trades" in full_run, "❌ Missing trades"
    assert "equity_curve" in full_run, "❌ Missing equity_curve"
    assert "walkforward" in full_run, "❌ Missing walkforward"
    assert "significance" in full_run, "❌ Missing significance"
    log(f"✅ Full data: {len(full_run['trades'])} trades, {len(full_run['equity_curve'])} equity points")
    
    # Test 6: Cancel non-existent job (should return 404)
    log("\n6️⃣  Testing cancel on non-existent job...")
    fake_id = "00000000-0000-0000-0000-000000000000"
    r = requests.post(f"{BASE_URL}/optimize/jobs/{fake_id}/cancel")
    assert r.status_code == 404, f"❌ Expected 404, got {r.status_code}"
    log(f"✅ Correctly returned 404 for non-existent job")
    
    # Test 7: Cancel already-done job (should return already_finished=true)
    log("\n7️⃣  Testing cancel on already-done job...")
    r = requests.post(f"{BASE_URL}/optimize/jobs/{job_id}/cancel")
    assert r.status_code == 200, f"❌ Expected 200, got {r.status_code}"
    data = r.json()
    assert data.get("already_finished") == True, f"❌ Expected already_finished=True, got {data}"
    log(f"✅ Correctly returned already_finished=True")
    
    # Test 8: Start a longer job and cancel it mid-run
    log("\n8️⃣  Testing cancel on running job...")
    payload2 = {
        "instrument": "NIFTY",
        "strategy_id": "confluence_scalper",
        "method": "bayesian",
        "objective": "sharpe",
        "n_trials": 50,
        "name": "Cancel Test"
    }
    r = requests.post(f"{BASE_URL}/optimize/start", json=payload2)
    job_id2 = r.json()["job_id"]
    log(f"   Started job {job_id2[:8]} with 50 trials")
    
    # Wait for it to start
    time.sleep(5)
    
    # Cancel it
    r = requests.post(f"{BASE_URL}/optimize/jobs/{job_id2}/cancel")
    assert r.status_code == 200, f"❌ Cancel failed: {r.status_code}"
    log(f"✅ Cancel request sent")
    
    # Wait for status to update (poll until done or cancelled)
    for _ in range(15):
        time.sleep(2)
        r = requests.get(f"{BASE_URL}/optimize/jobs/{job_id2}")
        job2 = r.json()
        status = job2.get("status")
        trials = job2.get('n_trials_completed', 0)
        log(f"   Job status: {status}, trials: {trials}/50")
        if status in ("done", "cancelled", "failed"):
            break
    
    # Verify best_so_far and best_backtest_run_id are preserved
    assert "best_so_far" in job2 or "best_params" in job2, "❌ best_so_far missing after cancel"
    # best_backtest_run_id should be present if job reached analyzing phase
    if job2.get("status") in ("done", "cancelled"):
        assert "best_backtest_run_id" in job2, "❌ best_backtest_run_id missing after cancel"
        log(f"✅ best_so_far preserved, best_backtest_run_id present")
    else:
        log(f"⚠️  Job status: {job2.get('status')}, best_backtest_run_id may not be created yet")
    
    # Test 9: Apply as preset
    log("\n9️⃣  Testing apply-as-preset...")
    preset_name = f"TestPreset_{int(time.time())}"
    r = requests.post(f"{BASE_URL}/optimize/apply-as-preset/{job_id}?name={preset_name}")
    assert r.status_code == 200, f"❌ Apply failed: {r.status_code} {r.text}"
    log(f"✅ Created preset: {preset_name}")
    
    # Verify preset
    r = requests.get(f"{BASE_URL}/presets")
    presets = r.json()["items"]
    preset = next((p for p in presets if p["name"] == preset_name), None)
    assert preset is not None, f"❌ Preset not found"
    assert preset["config"].get("source_optimization_job") == job_id, "❌ source_optimization_job mismatch"
    log(f"✅ Verified preset with source_optimization_job={job_id[:8]}")
    
    log("\n" + "=" * 70)
    log("🎉 ALL PHASE 3.5 TESTS PASSED")
    log("=" * 70)
    return True

if __name__ == "__main__":
    try:
        success = test_phase35()
        exit(0 if success else 1)
    except Exception as e:
        log(f"❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
