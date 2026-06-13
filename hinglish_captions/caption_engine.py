import os
import subprocess
import uuid

import assemblyai as aai
from imageio_ffmpeg import get_ffmpeg_exe

_FFMPEG = get_ffmpeg_exe()

# Common Hinglish words that ASR tends to mishear — boosted for accuracy
_WORD_BOOST = [
    "yaar", "bhai", "kya", "nahi", "matlab", "achha", "haan", "theek",
    "toh", "aur", "lekin", "kyunki", "bohot", "bilkul", "pagal", "dost",
    "pyaar", "zindagi", "dil", "sach", "iska", "uska", "mera", "tera",
    "humara", "tumhara", "kar", "karo", "karta", "karti", "raha", "rahi",
    "dekho", "suno", "bolo", "samjhe", "chal", "arre", "oye", "woh",
    "abhi", "phir", "sirf", "bas", "zyada", "kam", "accha", "thoda",
]


def extract_audio(video_path: str) -> str:
    """Extract mono 16 kHz WAV from video — typically under 10 MB even for long clips."""
    out = f"/tmp/audio_{uuid.uuid4().hex[:8]}.wav"
    result = subprocess.run(
        [_FFMPEG, "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", out],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg audio extraction failed:\n"
            + result.stderr.decode(errors="replace")[-500:]
        )
    return out


def transcribe_only(video_path: str, api_key: str, language: str = "hi") -> list:
    audio_path = extract_audio(video_path)
    try:
        aai.settings.api_key = api_key
        config = aai.TranscriptionConfig(
            language_code=language,
            word_boost=_WORD_BOOST,
        )
        transcript = aai.Transcriber().transcribe(audio_path, config=config)
    finally:
        # Delete audio immediately — AssemblyAI has it now, no reason to keep
        try:
            os.unlink(audio_path)
        except OSError:
            pass

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")
    words = transcript.words or []
    if not words:
        raise RuntimeError("No speech detected in the video.")
    return [{"text": w.text, "start": w.start, "end": w.end} for w in words]


def _ms_to_srt_time(ms: int) -> str:
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(word_dicts: list) -> str:
    lines = []
    for i, w in enumerate(word_dicts, start=1):
        start = _ms_to_srt_time(w["start"])
        end = _ms_to_srt_time(w["end"])
        lines.append(f"{i}\n{start} --> {end}\n{w['text']}\n")
    return "\n".join(lines)
