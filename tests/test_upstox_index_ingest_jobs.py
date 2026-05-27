from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_exposes_background_upstox_index_ingest_job_routes():
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    db = (ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")

    assert '@api.post("/upstox/warehouse/ingest/jobs")' in server
    assert '@api.get("/upstox/warehouse/ingest/jobs/{run_id}")' in server
    assert "BackgroundTasks" in server
    assert "run_upstox_index_ingest_job" in server
    assert "warehouse_runs.create_index([(\"status\", 1), (\"updated_at\", -1)])" in db


def test_upstox_index_ingest_module_tracks_progress_and_bulk_persists():
    module = (ROOT / "backend" / "app" / "upstox_index_ingest.py").read_text(encoding="utf-8")

    assert "bulk_write" in module
    assert "db or get_db()" not in module
    assert "db if db is not None else get_db()" in module
    assert "total_chunks" in module
    assert "completed_chunks" in module
    assert "failed_chunks" in module
    assert "progress_pct" in module
    assert "persist_index_candles_bulk" in module


def test_frontend_uses_background_ingest_job_and_large_import_hint():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    warehouse = (ROOT / "frontend" / "src" / "pages" / "DataWarehouse.jsx").read_text(encoding="utf-8")

    assert "startUpstoxIngestJob" in api
    assert "getUpstoxIngestJob" in api
    assert "upstox-large-import-help" in warehouse
    assert "upstox-ingest-progress" in warehouse
    assert "background" in warehouse.lower()
