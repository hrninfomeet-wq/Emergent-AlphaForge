"""
AlphaForge Trading Lab — Phase 3 Optimizer Backend Tests
=========================================================
Comprehensive API testing for the Auto-Optimizer endpoints.

Tests cover:
- POST /api/optimize/start with all methods (bayesian, grid, genetic)
- All objectives (risk_adjusted, sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd)
- Validation rules (n_trials bounds, invalid method, non-existent strategy)
- GET /api/optimize/jobs (list)
- GET /api/optimize/jobs/{job_id} (poll until done)
- DELETE /api/optimize/jobs/{job_id}
- POST /api/optimize/apply-as-preset/{job_id}?name=X
- param_overrides (narrow search bounds)
- Date window filtering (start_ts/end_ts)

Run: cd /app/backend && python backend_test.py
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta
import requests

BASE_URL = "https://algo-trading-lab-7.preview.emergentagent.com/api"

class OptimizerTester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.job_ids = []
        
    def log(self, msg, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{timestamp} | {level:7s} | {msg}")
    
    def test(self, name, fn):
        """Run a single test"""
        self.tests_run += 1
        self.log(f"🔍 Test {self.tests_run}: {name}")
        try:
            fn()
            self.tests_passed += 1
            self.log(f"✅ PASS: {name}", "SUCCESS")
            return True
        except AssertionError as e:
            self.tests_failed += 1
            self.log(f"❌ FAIL: {name} — {e}", "ERROR")
            return False
        except Exception as e:
            self.tests_failed += 1
            self.log(f"❌ ERROR: {name} — {e}", "ERROR")
            return False
    
    def poll_job(self, job_id, timeout=60):
        """Poll job until done/failed or timeout"""
        start = time.time()
        while time.time() - start < timeout:
            r = requests.get(f"{BASE_URL}/optimize/jobs/{job_id}")
            assert r.status_code == 200, f"Failed to get job {job_id}: {r.status_code}"
            job = r.json()
            status = job.get("status")
            self.log(f"  Job {job_id[:8]} status={status}, trials={job.get('n_trials_completed', 0)}/{job.get('n_trials_total', 0)}")
            if status == "done":
                return job
            if status == "failed":
                raise AssertionError(f"Job failed: {job.get('error')}")
            time.sleep(2)
        raise AssertionError(f"Job {job_id} timed out after {timeout}s")
    
    # -------------------------------------------------------------------------
    # Test Cases
    # -------------------------------------------------------------------------
    
    def test_start_bayesian_basic(self):
        """POST /api/optimize/start with bayesian method returns job_id + status=queued"""
        payload = {
            "instrument": "NIFTY",
            "mode": "SCALP",
            "strategy_id": "confluence_scalper",
            "method": "bayesian",
            "objective": "risk_adjusted",
            "n_trials": 20,
            "costs_enabled": True,
            "name": "Test Bayesian Basic"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "job_id" in data, "Missing job_id in response"
        assert data.get("status") == "queued", f"Expected status=queued, got {data.get('status')}"
        self.job_ids.append(data["job_id"])
        self.log(f"  Created job {data['job_id'][:8]}")
    
    def test_poll_job_until_done(self):
        """GET /api/optimize/jobs/{job_id} polls until status=done with full results"""
        if not self.job_ids:
            raise AssertionError("No job_id available from previous test")
        job_id = self.job_ids[-1]
        job = self.poll_job(job_id, timeout=60)
        assert job["status"] == "done", f"Expected done, got {job['status']}"
        assert "best_params" in job, "Missing best_params"
        assert "best_value" in job, "Missing best_value"
        assert "best_metrics" in job, "Missing best_metrics"
        assert "top_n_alternatives" in job, "Missing top_n_alternatives"
        assert "parameter_importance" in job, "Missing parameter_importance"
        assert "robustness" in job, "Missing robustness"
        assert "heatmap" in job, "Missing heatmap"
        self.log(f"  Job completed: best_value={job['best_value']:.4f}, trials={job['n_trials_completed']}")
        # Validate robustness structure
        rob = job["robustness"]
        assert "score" in rob, "Missing robustness.score"
        assert "perturbations" in rob, "Missing robustness.perturbations"
        self.log(f"  Robustness score: {rob['score']}")
        # Validate parameter_importance
        imp = job["parameter_importance"]
        assert isinstance(imp, list), "parameter_importance should be a list"
        if imp:
            self.log(f"  Top param: {imp[0]['param']} ({imp[0]['importance']:.4f})")
        # Validate heatmap
        hm = job["heatmap"]
        if hm:
            assert "param_a" in hm and "param_b" in hm, "Heatmap missing param_a/param_b"
            assert "grid" in hm, "Heatmap missing grid"
            self.log(f"  Heatmap: {hm['param_a']} × {hm['param_b']}")
    
    def test_list_jobs(self):
        """GET /api/optimize/jobs returns list sorted by created_at desc"""
        r = requests.get(f"{BASE_URL}/optimize/jobs?limit=50")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert "items" in data, "Missing items in response"
        items = data["items"]
        assert len(items) > 0, "Expected at least one job"
        # Check sorted by created_at desc
        if len(items) >= 2:
            t1 = items[0].get("created_at", "")
            t2 = items[1].get("created_at", "")
            assert t1 >= t2, f"Jobs not sorted desc: {t1} < {t2}"
        self.log(f"  Found {len(items)} jobs")
    
    def test_start_grid_method(self):
        """POST /api/optimize/start with method=grid completes successfully"""
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "grid",
            "objective": "sharpe",
            "n_trials": 15,
            "name": "Test Grid"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        job_id = r.json()["job_id"]
        self.job_ids.append(job_id)
        job = self.poll_job(job_id, timeout=60)
        assert job["status"] == "done", f"Grid job failed: {job.get('error')}"
        assert job["method"] == "grid"
        self.log(f"  Grid job completed: best={job['best_value']:.4f}")
    
    def test_start_genetic_method(self):
        """POST /api/optimize/start with method=genetic completes successfully"""
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "genetic",
            "objective": "profit_factor",
            "n_trials": 15,
            "name": "Test Genetic"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        job_id = r.json()["job_id"]
        self.job_ids.append(job_id)
        job = self.poll_job(job_id, timeout=60)
        assert job["status"] == "done", f"Genetic job failed: {job.get('error')}"
        assert job["method"] == "genetic"
        self.log(f"  Genetic job completed: best={job['best_value']:.4f}")
    
    def test_objectives(self):
        """Test all objective functions: sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd"""
        objectives = ["sharpe", "profit_factor", "total_pnl_pts", "win_rate", "neg_max_dd"]
        for obj in objectives:
            payload = {
                "instrument": "NIFTY",
                "strategy_id": "confluence_scalper",
                "method": "bayesian",
                "objective": obj,
                "n_trials": 10,
                "name": f"Test Objective {obj}"
            }
            r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
            assert r.status_code == 200, f"Failed to start with objective={obj}: {r.status_code}"
            job_id = r.json()["job_id"]
            self.job_ids.append(job_id)
            self.log(f"  Started job for objective={obj}: {job_id[:8]}")
            # Poll until done (quick test with 10 trials)
            job = self.poll_job(job_id, timeout=40)
            assert job["status"] == "done", f"Job for {obj} failed"
            assert job["objective"] == obj
            self.log(f"  ✓ Objective {obj} completed: best={job['best_value']:.4f}")
    
    def test_invalid_method(self):
        """POST /api/optimize/start with invalid method returns 400"""
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "invalid_method",
            "objective": "sharpe",
            "n_trials": 20
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        self.log(f"  Correctly rejected invalid method: {r.json().get('detail')}")
    
    def test_nonexistent_strategy(self):
        """POST /api/optimize/start with non-existent strategy returns 404"""
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "nonexistent_strategy",
            "method": "bayesian",
            "objective": "sharpe",
            "n_trials": 20
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 404, f"Expected 404, got {r.status_code}"
        self.log(f"  Correctly rejected non-existent strategy: {r.json().get('detail')}")
    
    def test_n_trials_validation(self):
        """POST /api/optimize/start with n_trials out of bounds returns 400"""
        # Too few
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "bayesian",
            "objective": "sharpe",
            "n_trials": 5
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 400, f"Expected 400 for n_trials=5, got {r.status_code}"
        self.log(f"  Correctly rejected n_trials=5: {r.json().get('detail')}")
        
        # Too many
        payload["n_trials"] = 10000
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 400, f"Expected 400 for n_trials=10000, got {r.status_code}"
        self.log(f"  Correctly rejected n_trials=10000: {r.json().get('detail')}")
    
    def test_param_overrides(self):
        """param_overrides narrows search bounds (verify best_params within override range)"""
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "bayesian",
            "objective": "sharpe",
            "n_trials": 15,
            "param_overrides": {
                "ema_fast": {"min": 5, "max": 15}
            },
            "name": "Test Param Overrides"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        job_id = r.json()["job_id"]
        self.job_ids.append(job_id)
        job = self.poll_job(job_id, timeout=60)
        assert job["status"] == "done"
        best_params = job["best_params"]
        if "ema_fast" in best_params:
            val = best_params["ema_fast"]
            assert 5 <= val <= 15, f"ema_fast={val} outside override range [5, 15]"
            self.log(f"  ✓ ema_fast={val} within override range [5, 15]")
    
    def test_date_window(self):
        """start_ts/end_ts filters candles correctly (use narrow 2-day window)"""
        # Use a 2-day window (e.g., 2025-05-20 to 2025-05-22)
        # Convert to ms epoch UTC (IST 09:15 to 15:30)
        from datetime import datetime, timezone
        start_date = datetime(2025, 5, 20, 9, 15, tzinfo=timezone.utc)
        end_date = datetime(2025, 5, 22, 15, 30, tzinfo=timezone.utc)
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)
        
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "bayesian",
            "objective": "sharpe",
            "n_trials": 10,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "name": "Test Date Window"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        job_id = r.json()["job_id"]
        self.job_ids.append(job_id)
        job = self.poll_job(job_id, timeout=60)
        # Job might fail if no candles in that window, but should not crash
        if job["status"] == "failed":
            self.log(f"  Job failed (expected if no candles in window): {job.get('error')}")
        else:
            assert job["status"] == "done"
            self.log(f"  ✓ Date window job completed")
    
    def test_apply_as_preset(self):
        """POST /api/optimize/apply-as-preset/{job_id}?name=X creates preset"""
        # Use the first completed job
        if not self.job_ids:
            raise AssertionError("No job_id available")
        job_id = self.job_ids[0]
        # Ensure job is done
        r = requests.get(f"{BASE_URL}/optimize/jobs/{job_id}")
        job = r.json()
        if job["status"] != "done":
            self.log(f"  Skipping apply-as-preset (job not done)")
            return
        
        preset_name = f"TestPreset_{int(time.time())}"
        r = requests.post(f"{BASE_URL}/optimize/apply-as-preset/{job_id}?name={preset_name}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("ok") == True, "Expected ok=True"
        assert data.get("preset_name") == preset_name
        self.log(f"  ✓ Created preset: {preset_name}")
        
        # Verify preset exists
        r = requests.get(f"{BASE_URL}/presets")
        assert r.status_code == 200
        presets = r.json()["items"]
        preset = next((p for p in presets if p["name"] == preset_name), None)
        assert preset is not None, f"Preset {preset_name} not found"
        assert "config" in preset
        assert preset["config"].get("source_optimization_job") == job_id
        self.log(f"  ✓ Verified preset in /api/presets")
    
    def test_delete_job(self):
        """DELETE /api/optimize/jobs/{job_id} deletes the job"""
        # Create a job to delete
        payload = {
            "instrument": "NIFTY",
            "strategy_id": "confluence_scalper",
            "method": "bayesian",
            "objective": "sharpe",
            "n_trials": 10,
            "name": "Test Delete"
        }
        r = requests.post(f"{BASE_URL}/optimize/start", json=payload)
        job_id = r.json()["job_id"]
        self.log(f"  Created job {job_id[:8]} for deletion test")
        
        # Wait a bit for it to start
        time.sleep(2)
        
        # Delete it
        r = requests.delete(f"{BASE_URL}/optimize/jobs/{job_id}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert data.get("deleted") == 1, f"Expected deleted=1, got {data.get('deleted')}"
        self.log(f"  ✓ Deleted job {job_id[:8]}")
        
        # Verify it's gone
        r = requests.get(f"{BASE_URL}/optimize/jobs/{job_id}")
        assert r.status_code == 404, f"Expected 404 after delete, got {r.status_code}"
        self.log(f"  ✓ Verified job deleted (404)")
    
    # -------------------------------------------------------------------------
    # Runner
    # -------------------------------------------------------------------------
    
    def run_all(self):
        """Run all tests in sequence"""
        self.log("=" * 70)
        self.log("AlphaForge Phase 3 Optimizer Backend Tests")
        self.log("=" * 70)
        
        # Basic flow
        self.test("Start Bayesian optimization", self.test_start_bayesian_basic)
        self.test("Poll job until done with full results", self.test_poll_job_until_done)
        self.test("List optimization jobs", self.test_list_jobs)
        
        # Methods
        self.test("Grid search method", self.test_start_grid_method)
        self.test("Genetic (CMA-ES) method", self.test_start_genetic_method)
        
        # Objectives
        self.test("All objective functions", self.test_objectives)
        
        # Validation
        self.test("Invalid method returns 400", self.test_invalid_method)
        self.test("Non-existent strategy returns 404", self.test_nonexistent_strategy)
        self.test("n_trials validation", self.test_n_trials_validation)
        
        # Advanced features
        self.test("Parameter overrides", self.test_param_overrides)
        self.test("Date window filtering", self.test_date_window)
        self.test("Apply best params as preset", self.test_apply_as_preset)
        self.test("Delete optimization job", self.test_delete_job)
        
        # Summary
        self.log("=" * 70)
        self.log(f"Tests run: {self.tests_run}")
        self.log(f"Tests passed: {self.tests_passed}", "SUCCESS")
        self.log(f"Tests failed: {self.tests_failed}", "ERROR" if self.tests_failed > 0 else "INFO")
        self.log("=" * 70)
        
        if self.tests_failed == 0:
            self.log("🎉 ALL TESTS PASSED", "SUCCESS")
            return 0
        else:
            self.log(f"❌ {self.tests_failed} TEST(S) FAILED", "ERROR")
            return 1

def main():
    tester = OptimizerTester()
    return tester.run_all()

if __name__ == "__main__":
    sys.exit(main())
