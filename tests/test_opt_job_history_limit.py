"""Regression: Optimizer Job History must show ALL saved runs, not a 30-row cap.

The frontend hardcoded api.listOptJobs(30), so only the 30 most-recent optimization
jobs were ever fetched even though every run is persisted in db.optimization_jobs.
The backend list endpoint also capped limit at le=200. The fix requests the full
history from the frontend and raises the backend cap so "all" is a real all.
"""
import re
from pathlib import Path

from tests.contract_corpus import backend_api_text

ROOT = Path(__file__).resolve().parents[1]


def _optimizer_page_text() -> str:
    return (ROOT / "frontend" / "src" / "pages" / "Optimizer.jsx").read_text(encoding="utf-8")


def test_frontend_does_not_cap_job_history_at_30():
    src = _optimizer_page_text()
    calls = re.findall(r"listOptJobs\((\d+)\)", src)
    assert calls, "expected Optimizer to call api.listOptJobs(<limit>)"
    # Every fetch must request the full history, not the old 30-row cap.
    assert all(int(n) >= 500 for n in calls), f"listOptJobs limits too small: {calls}"


def test_backend_job_list_cap_allows_full_history():
    src = backend_api_text()
    i = src.index("async def list_opt_jobs")
    sig = src[i:i + 200]
    m = re.search(r"le=(\d+)", sig)
    assert m, "list_opt_jobs limit Query must keep an le= cap"
    assert int(m.group(1)) >= 1000, f"cap too small to show all saved jobs: {sig}"
