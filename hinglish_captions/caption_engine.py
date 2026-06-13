import assemblyai as aai


def transcribe_only(video_path: str, api_key: str, language: str = "en") -> list:
    aai.settings.api_key = api_key
    config = aai.TranscriptionConfig(language_code=language)
    transcript = aai.Transcriber().transcribe(video_path, config=config)
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
