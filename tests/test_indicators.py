import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.indicators import precompute_all_indicators  # noqa: E402


def test_precompute_all_indicators_assigns_session_vwap_for_single_session():
    start = pd.Timestamp("2026-05-22T09:15:00+05:30")
    rows = []
    for i in range(60):
        dt = start + pd.Timedelta(minutes=i)
        rows.append({
            "ts": int(dt.tz_convert("UTC").value // 10**6),
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 1000 + i,
        })
    df = pd.DataFrame(rows)

    enriched = precompute_all_indicators(df, {})

    assert "vwap" in enriched.columns
    assert len(enriched["vwap"]) == len(df)
    assert enriched["vwap"].notna().any()
