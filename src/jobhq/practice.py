"""Interview practice: questions, typed or spoken answers, delivery stats.

Voice answers are recorded in the browser, sent to this copy, transcribed on the VM with
faster-whisper (when JOBHQ_VOICE=on), and the audio is deleted right away, whatever happens.
Only the transcript is kept, and only the transcript goes to Claude.
"""
from __future__ import annotations

import logging
import re
import secrets
import subprocess  # noqa: S404 - ffmpeg is called with a fixed argument list, never a shell
import tempfile
import time
from pathlib import Path

from .context import Ctx

log = logging.getLogger("jobhq.practice")
FILLERS = re.compile(r"\b(um+|uh+|erm|like|you know|i mean|sort of|kind of|basically|actually|literally)\b", re.I)
# Each accepted recording type and the one ffmpeg reader allowed for it, so a crafted file can't pick
# another demuxer (playlists, concat lists and the like can open other files or URLs).
AUDIO_FORMATS = {"audio/webm": "matroska", "video/webm": "matroska", "audio/ogg": "ogg", "audio/mp4": "mov", "video/mp4": "mov",
                 "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac"}
AUDIO_TYPES = set(AUDIO_FORMATS)
MAX_SECONDS = 300
_model = None


class VoiceError(Exception):
    pass


def stats(text: str, seconds: float | None) -> dict:
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    fillers = len(FILLERS.findall(text))
    wpm = round(words / (seconds / 60), 1) if seconds and seconds > 3 else None
    return {"words": words, "fillers": fillers, "wpm": wpm}


def _whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # optional dependency, installed with the voice extra

        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _model


def _read_wav(path: Path):
    """16 kHz mono 16-bit WAV (what ffmpeg was told to write) as float32 samples in -1..1."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != 16000:
            raise VoiceError("The recording couldn't be read.")
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(ctx: Ctx, data: bytes, content_type: str, hint: str = "") -> tuple[str, float]:
    """Audio bytes to (transcript, seconds). The audio never touches disk outside a private temp dir that is removed."""
    if not ctx.config.voice:
        raise VoiceError("Voice answers are turned off on this copy. Type your answer, or ask the admin to set JOBHQ_VOICE=on.")
    mime = content_type.split(";")[0].strip().lower()
    if mime not in AUDIO_TYPES:
        raise VoiceError("That isn't an audio recording.")
    if not data or len(data) < 1000:
        raise VoiceError("The recording was empty.")
    ctx.config.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ctx.config.work_dir, prefix="voice-") as tmp:
        src = Path(tmp) / f"in-{secrets.token_hex(6)}"
        wav = Path(tmp) / "audio.wav"
        src.write_bytes(data)
        try:
            # Local files only (no network or other protocols), and only the reader for the declared type.
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",  # noqa: S603, S607
                            "-f", AUDIO_FORMATS[mime], "-i", str(src), "-t", str(MAX_SECONDS),
                            "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)], check=True, timeout=120, capture_output=True)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise VoiceError("The recording couldn't be read.") from exc
        samples = _read_wav(wav)  # decode here, so faster-whisper never touches PyAV (whose API changes break it)
        segments, info = _whisper().transcribe(samples, beam_size=1, language="en", initial_prompt=hint[:200] or None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        seconds = float(getattr(info, "duration", 0) or 0)
    if not text:
        raise VoiceError("I couldn't hear any words in that recording.")
    return text, seconds


def new_question(ctx: Ctx, role_id: int | None, question: str, focus: str) -> int:
    return ctx.db.run("INSERT INTO practice_questions (role_id, question, focus, created) VALUES (?, ?, ?, ?)",
                      (role_id, question.strip()[:600], focus.strip()[:100], time.time())).lastrowid


def add_attempt(ctx: Ctx, question_id: int, answer: str, via: str, seconds: float | None) -> int:
    answer = answer.strip()[:8000]
    if len(answer.split()) < 5:
        raise VoiceError("That answer is too short to grade.")
    s = stats(answer, seconds)
    return ctx.db.run("INSERT INTO practice_attempts (question_id, at, answer, via, seconds, words, wpm, fillers) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (question_id, time.time(), answer, via, seconds, s["words"], s["wpm"], s["fillers"])).lastrowid


def history(ctx: Ctx, limit: int = 50) -> list[dict]:
    return ctx.db.all("SELECT a.id, a.at, a.total, a.via, a.seconds, a.wpm, a.fillers, a.status, q.question, q.role_id, r.company "
                      "FROM practice_attempts a JOIN practice_questions q ON q.id = a.question_id LEFT JOIN roles r ON r.id = q.role_id "
                      "ORDER BY a.id DESC LIMIT ?", (limit,))
