"""
Caption engine — CapCut-style word-group karaoke captions via FFmpeg ASS subtitles.

Flow:
  video → extract audio → AssemblyAI transcribe → group words →
  build .ass file → FFmpeg burn → output .mp4
"""

import json
import os
import subprocess
import tempfile
import uuid

import assemblyai as aai
from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()

# Words shown per caption block (CapCut default is ~3)
WORDS_PER_GROUP = 3

# ASS colors are &HAABBGGRR& (alpha, blue, green, red)
STYLES = {
    "yellow": {
        "primary":   "&H0000E6FF&",  # yellow  — active / already-spoken word
        "secondary": "&H00FFFFFF&",  # white   — upcoming words
        "outline":   "&H00000000&",  # black stroke
    },
    "white": {
        "primary":   "&H00FFFFFF&",
        "secondary": "&H00AAAAAA&",  # grey for upcoming
        "outline":   "&H00000000&",
    },
    "red": {
        "primary":   "&H003333FF&",  # red
        "secondary": "&H00FFFFFF&",
        "outline":   "&H00000000&",
    },
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ms_to_ass(ms: int) -> str:
    """1234 ms → '0:00:01.23'"""
    cs = ms // 10
    h  = cs // 360000;  cs %= 360000
    m  = cs // 6000;    cs %= 6000
    s  = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"




def _video_size(video_path: str) -> tuple:
    """Return (width, height) of the video."""
    try:
        from moviepy import VideoFileClip
    except ImportError:
        from moviepy.editor import VideoFileClip
    clip = VideoFileClip(video_path)
    w, h = int(clip.w), int(clip.h)
    clip.close()
    return w, h


# ─── Step 1 — Extract audio ──────────────────────────────────────────────────

def extract_audio(video_path: str) -> str:
    """Pull mono 16 kHz WAV from the video — much smaller to upload than the full video."""
    out = os.path.join(tempfile.gettempdir(), f"aai_{uuid.uuid4().hex[:8]}.wav")
    result = subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-ac", "1", "-ar", "16000", "-vn", out],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg audio extraction failed:\n" + result.stderr.decode(errors="replace")
        )
    return out


# ─── Step 2 — Transcribe ─────────────────────────────────────────────────────

def transcribe(audio_path: str, api_key: str, language: str = "en") -> list:
    aai.settings.api_key = api_key
    # "en" → Roman-script output (yaar, kya, bhai) — correct for Hinglish creators
    # "hi" → Devanagari output (यार, क्या, भाई) — needs a Devanagari-capable font
    config = aai.TranscriptionConfig(language_code=language)
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")
    return transcript.words or []


# ─── Step 3 — Group words ────────────────────────────────────────────────────

def _group_words(words: list) -> list:
    """Split flat word list into N-word groups for display."""
    groups = []
    for i in range(0, len(words), WORDS_PER_GROUP):
        chunk = words[i : i + WORDS_PER_GROUP]
        groups.append({
            "words": chunk,
            "start": chunk[0].start,
            "end":   chunk[-1].end,
        })
    return groups


# ─── Step 4 — Build ASS file ─────────────────────────────────────────────────

def _build_ass(groups: list, width: int, height: int, style: str = "yellow") -> str:
    """
    Generate ASS subtitle content with karaoke word-highlighting.

    How the karaoke works:
      • SecondaryColour = upcoming word colour (white/grey)
      • PrimaryColour   = active / already-spoken colour (yellow/red/white)
      • \\k{cs} tag      = this word stays active for {cs} centiseconds
      At any moment the word currently being spoken is the one that just
      turned Primary — all future words are still Secondary. This is exactly
      the CapCut "pop word-by-word" effect.
    """
    cfg       = STYLES.get(style, STYLES["yellow"])
    font_size = max(40, int(height * 0.065))   # ~124 px on 1920-tall video
    outline   = max(5,  font_size // 12)
    margin_v  = int(height * 0.08)             # 8 % from bottom edge

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n\n"

        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"

        # BorderStyle 1 = outline + drop-shadow (crisp on any background)
        f"Style: Default,"
        f"Arial Black,{font_size},"
        f"{cfg['primary']},{cfg['secondary']},{cfg['outline']},&H00000000&,"
        f"-1,0,0,0,100,100,2,0,1,{outline},2,2,10,10,{margin_v},1\n\n"

        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = []
    for g in groups:
        s_ass = _ms_to_ass(g["start"])
        e_ass = _ms_to_ass(g["end"])
        ws    = g["words"]

        parts = []
        for i, w in enumerate(ws):
            # Duration = time until next word starts (bridges natural gaps cleanly)
            if i < len(ws) - 1:
                dur_ms = ws[i + 1].start - w.start
            else:
                dur_ms = w.end - w.start
            dur_cs = max(1, dur_ms // 10)
            parts.append(f"{{\\k{dur_cs}}}{w.text.upper()}")

        text = " ".join(parts)
        # \fad(in_ms, out_ms) — subtle fade-in/out on each word group
        lines.append(
            f"Dialogue: 0,{s_ass},{e_ass},Default,,0,0,0,,{{\\fad(80,60)}}{text}"
        )

    return header + "\n".join(lines)


# ─── Step 5 — Burn subtitles ─────────────────────────────────────────────────

def burn_captions(video_path: str, ass_path: str, output_path: str) -> None:
    """
    Burn ASS subtitles into the video.

    Windows FFmpeg bug: drive-letter colons inside the -vf filtergraph string
    (e.g. C:/path/file.ass) are misinterpreted as option separators, breaking
    the ass= filter.  Fix: run FFmpeg with cwd = the directory that holds the
    .ass file and pass only the bare filename — no path, no colon.
    """
    ass_dir      = os.path.dirname(os.path.abspath(ass_path))
    ass_filename = os.path.basename(ass_path)

    result = subprocess.run(
        [
            FFMPEG, "-y",
            "-i", os.path.abspath(video_path),
            "-vf", f"ass={ass_filename}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "copy",
            os.path.abspath(output_path),
        ],
        cwd=ass_dir,          # FFmpeg looks for ass_filename here
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg caption burn failed:\n" + result.stderr.decode(errors="replace")[-1000:]
        )


# ─── Public API ──────────────────────────────────────────────────────────────

def render(
    video_path: str,
    word_dicts: list,
    style: str = "yellow",
    words_per_group: int = 3,
    output_dir: str = "outputs",
) -> str:
    """
    Burn captions from already-transcribed (and optionally edited) word dicts.
    Each dict: {"text": str, "start": int_ms, "end": int_ms}
    Returns output_path.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Wrap plain dicts in a tiny object so _group_words / _build_ass can use .text/.start/.end
    class W:
        __slots__ = ("text", "start", "end")
        def __init__(self, d):
            self.text  = d["text"]
            self.start = d["start"]
            self.end   = d["end"]

    words         = [W(d) for d in word_dicts]
    width, height = _video_size(video_path)

    # Temporarily override global WORDS_PER_GROUP with caller's value
    original = globals().get("WORDS_PER_GROUP", 3)
    globals()["WORDS_PER_GROUP"] = words_per_group
    groups = _group_words(words)
    globals()["WORDS_PER_GROUP"] = original

    ass_content = _build_ass(groups, width, height, style)
    ass_path    = os.path.join(
        tempfile.gettempdir(), f"captions_{uuid.uuid4().hex[:8]}.ass"
    )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    stem        = os.path.splitext(os.path.basename(video_path))[0]
    output_path = os.path.join(output_dir, f"{stem}_captioned.mp4")
    try:
        burn_captions(video_path, ass_path, output_path)
    finally:
        try:
            os.unlink(ass_path)
        except OSError:
            pass

    return output_path


def transcribe_only(
    video_path: str,
    api_key: str,
    language: str = "en",
) -> list:
    """
    Step 1 of the two-step flow: extract audio → transcribe → return word dicts.
    The caller can let the user edit the dicts, then pass them to render().
    """
    audio_path = extract_audio(video_path)
    try:
        words = transcribe(audio_path, api_key, language=language)
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass

    if not words:
        raise RuntimeError("No speech detected in the video.")

    return [{"text": w.text, "start": w.start, "end": w.end} for w in words]
