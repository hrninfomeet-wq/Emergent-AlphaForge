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


def _fake_yt_api(monkeypatch, *, snippets=None, raises=None):
    """Inject a fake youtube_transcript_api (1.x shape) so the real lib/network isn't needed."""
    import sys, types
    mod = types.ModuleType("youtube_transcript_api")

    class _Snip:
        def __init__(self, text): self.text = text

    class YouTubeTranscriptApi:
        def fetch(self, video_id):
            if raises is not None:
                raise raises
            return [_Snip(t) for t in (snippets or [])]

    mod.YouTubeTranscriptApi = YouTubeTranscriptApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", mod)


def test_fetch_youtube_transcript_joins_1x_snippets(monkeypatch):
    _fake_yt_api(monkeypatch, snippets=["enter long", "when rsi > 60"])
    assert si.fetch_youtube_transcript("dQw4w9WgXcQ") == "enter long when rsi > 60"


def test_fetch_youtube_transcript_wraps_fetch_errors(monkeypatch):
    _fake_yt_api(monkeypatch, raises=Exception("blocked"))
    with pytest.raises(RuntimeError) as ei:
        si.fetch_youtube_transcript("dQw4w9WgXcQ")
    assert "could not fetch transcript" in str(ei.value)


def test_fetch_youtube_transcript_empty_raises(monkeypatch):
    _fake_yt_api(monkeypatch, snippets=["   "])
    with pytest.raises(RuntimeError) as ei:
        si.fetch_youtube_transcript("dQw4w9WgXcQ")
    assert "no usable transcript" in str(ei.value)
