import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas import WfoStartReq


def test_wfo_opt_workers_defaults_to_one():
    req = WfoStartReq(strategy_id="confluence_scalper")
    assert req.opt_workers == 1


def test_wfo_opt_workers_roundtrips_through_model_dump():
    req = WfoStartReq(strategy_id="confluence_scalper", opt_workers=4)
    dumped = req.model_dump()
    assert dumped["opt_workers"] == 4  # flows into create_wfo_job(payload) -> run_wfo
