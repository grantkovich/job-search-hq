"""Real speech-to-text with faster-whisper and ffmpeg. Skipped unless JOBHQ_LIVE_VOICE points at an audio clip
of someone saying roughly: "I led the migration from Iterable to HubSpot for seventy five thousand contacts,
with a full Salesforce sync and zero downtime." (macOS: `say -o clip.m4a --data-format=aac "..."`)."""
import os
from pathlib import Path

import pytest

from jobhq import practice
from jobhq.config import load

from ..conftest import make_env

CLIP = os.environ.get("JOBHQ_LIVE_VOICE", "")
pytestmark = [pytest.mark.live, pytest.mark.skipif(not Path(CLIP).is_file(), reason="set JOBHQ_LIVE_VOICE to an audio clip")]


def test_transcribe_and_clean_up(ctx, tmp_path):
    ctx.config = load({**make_env(tmp_path), "JOBHQ_VOICE": "on"}, scrub=False)
    text, seconds = practice.transcribe(ctx, Path(CLIP).read_bytes(), "audio/mp4", "HubSpot, Salesforce, Iterable")
    words = text.lower()
    assert "hubspot" in words and "salesforce" in words and ("75,000" in words or "seventy" in words or "75000" in words), text
    assert 4 < seconds < 20
    assert not list(ctx.config.work_dir.glob("voice-*")), "temporary audio was left behind"
    s = practice.stats(text, seconds)
    assert s["words"] > 10 and s["wpm"] and 80 < s["wpm"] < 260


def test_rejects_non_audio(ctx, tmp_path):
    ctx.config = load({**make_env(tmp_path), "JOBHQ_VOICE": "on"}, scrub=False)
    with pytest.raises(practice.VoiceError, match="isn't an audio"):
        practice.transcribe(ctx, b"x" * 5000, "application/pdf")
    with pytest.raises(practice.VoiceError, match="couldn't be read"):
        practice.transcribe(ctx, b"not really audio" * 500, "audio/webm")
