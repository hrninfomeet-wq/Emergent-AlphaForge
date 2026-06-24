import sys; from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "backend"))
from unittest.mock import patch
import pytest
from app.ai import source_ingest as si


def test_text_passthrough():
    out = si.ingest_source("buy calls when close > ema9")
    assert out["kind"] == "text" and out["url"] is None and "close" in out["text"]


def test_extract_youtube_id_watch():
    assert si.extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_short():
    assert si.extract_youtube_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"


def test_extract_youtube_id_shorts():
    assert si.extract_youtube_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_non_youtube_url_rejected():
    with pytest.raises(ValueError):
        si.ingest_source("https://example.com/some-blog-post")


def test_empty_rejected():
    with pytest.raises(ValueError):
        si.ingest_source("   ")


def test_youtube_ingest_fetches_transcript():
    with patch.object(si, "fetch_youtube_transcript", return_value="enter long when rsi above 60") as m:
        out = si.ingest_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    m.assert_called_once_with("dQw4w9WgXcQ")
    assert out["kind"] == "youtube" and out["text"] == "enter long when rsi above 60"
