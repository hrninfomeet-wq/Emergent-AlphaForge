"""Normalize the user's authoring input to transcript text.
v1 supports: pasted text, and YouTube links (transcript fetched via youtube-transcript-api).
Non-YouTube URLs are rejected with a clear message (paste the text instead)."""
from __future__ import annotations
import re
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}


def is_url(s: str) -> bool:
    s = s.strip()
    return s.lower().startswith(("http://", "https://"))


def extract_youtube_id(url: str) -> Optional[str]:
    """Return the 11-char video id from common YouTube URL forms, else None.
    Handles youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID, /embed/ID."""
    try:
        u = urlparse(url.strip())
    except Exception:
        return None
    host = (u.netloc or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    if host in ("youtu.be", "www.youtu.be"):
        vid = u.path.lstrip("/").split("/")[0]
        return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) else None
    # youtube.com/watch?v=ID
    q = parse_qs(u.query or "")
    if "v" in q and re.fullmatch(r"[A-Za-z0-9_-]{11}", q["v"][0]):
        return q["v"][0]
    # /shorts/ID or /embed/ID
    m = re.match(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})", u.path or "")
    return m.group(1) if m else None


def fetch_youtube_transcript(video_id: str) -> str:
    """Fetch + join a YouTube transcript. Lazy-imports youtube_transcript_api.
    Raises RuntimeError with a readable message when no transcript is available."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # lazy
    except Exception as e:  # pragma: no cover - import guard
        raise RuntimeError(f"transcript library unavailable: {e}")
    try:
        # youtube-transcript-api 1.x: instance .fetch() -> iterable of snippet objects
        # (each with a `.text`). The 0.x classmethod `get_transcript` is blocked by
        # YouTube now (returns empty -> "no element found"), so we require >=1.1.
        fetched = YouTubeTranscriptApi().fetch(video_id)
    except Exception as e:
        raise RuntimeError(f"could not fetch transcript for this video ({e}). Try pasting the text instead.")
    text = " ".join(getattr(seg, "text", "") for seg in fetched).strip()
    if not text:
        raise RuntimeError("the video has no usable transcript — paste the strategy text instead.")
    return text


def ingest_source(raw: str) -> Dict[str, object]:
    """Return {text, kind, url}. kind in {text, youtube}. Raises ValueError for an
    unsupported/empty input, RuntimeError for a transcript-fetch failure."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("source is empty")
    if is_url(raw):
        vid = extract_youtube_id(raw)
        if vid is None:
            raise ValueError("Only YouTube links and pasted text are supported — paste the strategy rules as text.")
        return {"text": fetch_youtube_transcript(vid), "kind": "youtube", "url": raw}
    return {"text": raw, "kind": "text", "url": None}
