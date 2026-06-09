"""Deepgram transcription for seller-call recordings.

The Exodus call-analysis feature feeds the resulting transcript into Claude
along with the sales methodology doc to grade the call. Speaker diarization
is critical here — we need to know what the REP said vs what the SELLER said
or the grader can't evaluate whether the rep asked the right questions.

API docs: https://developers.deepgram.com/reference/listen-file
Pricing:  https://deepgram.com/pricing  (~$0.0043/min on nova-2)
Setup:    Add the API key to Streamlit Secrets as:
            [deepgram]
            api_key = "YOUR_KEY_HERE"
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
import streamlit as st


# Video file extensions we'll strip the video track from before sending to
# Deepgram. The audio container we extract is MP3 at 16 kHz mono — small,
# fast, Deepgram-friendly. Google Meet recordings come down as MP4.
VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm", "avi", "mkv"}


def is_video_file(filename: str) -> bool:
    """Return True if the filename's extension matches a video container
    we know how to strip with ffmpeg."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext in VIDEO_EXTENSIONS


def _ffmpeg_available() -> bool:
    """Return True if ffmpeg is available on PATH (Streamlit Cloud installs
    it from packages.txt at deploy time)."""
    try:
        subprocess.run(["ffmpeg", "-version"],
                       capture_output=True, timeout=5, check=True)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def extract_audio_from_video(video_bytes: bytes, source_ext: str = "mp4") -> bytes:
    """Extract audio track from a video file using ffmpeg.

    Returns mp3 bytes (mono, 16 kHz, ~64 kbps) — a 500 MB MP4 becomes a
    ~25 MB MP3. Raises RuntimeError on failure (file corrupt, no audio
    track, ffmpeg crashed, ffmpeg not installed).

    Args:
        video_bytes: raw bytes of the uploaded video file
        source_ext:  file extension without dot (e.g. "mp4", "mov") — only
            used to give the temp file a sensible suffix so ffmpeg can
            detect the container format from the filename hint.
    """
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg is not installed on this server. Add 'ffmpeg' to "
            "packages.txt in the project root and redeploy."
        )

    suffix_in = "." + source_ext.lower().lstrip(".")
    in_path: Optional[str] = None
    out_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix_in, delete=False) as f_in:
            f_in.write(video_bytes)
            in_path = f_in.name
        out_path = in_path.rsplit(".", 1)[0] + "_audio.mp3"

        # -i input  -vn no-video  -ar 16000 sample rate (Deepgram-friendly)
        # -ac 1 mono  -b:a 64k bitrate  -y overwrite output  -loglevel error
        cmd = ["ffmpeg", "-i", in_path, "-vn",
               "-ar", "16000", "-ac", "1", "-b:a", "64k",
               "-y", "-loglevel", "error", out_path]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "ffmpeg timed out extracting audio (>10 minutes). The video "
                "may be very long or corrupt. Try converting to MP3 locally "
                "first."
            )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg failed to extract audio: {stderr[:400]}"
            )
        with open(out_path, "rb") as f_out:
            return f_out.read()
    finally:
        for p in (in_path, out_path):
            if p:
                try:
                    os.unlink(p)
                except (FileNotFoundError, OSError):
                    pass


DEEPGRAM_URL = (
    "https://api.deepgram.com/v1/listen"
    "?model=nova-2"
    "&diarize=true"
    "&punctuate=true"
    "&smart_format=true"
    "&utterances=true"
    "&language=en"
)
TIMEOUT_SECONDS = 300  # transcription can take ~30-60s per 10min audio


def is_configured() -> bool:
    """True if a Deepgram API key is configured in Streamlit Secrets."""
    try:
        return bool(st.secrets["deepgram"]["api_key"])
    except Exception:
        return False


def _api_key() -> str:
    return st.secrets["deepgram"]["api_key"]


def transcribe_audio(file_bytes: bytes, mime_type: str = "audio/mpeg",
                     source_filename: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe an audio file with speaker diarization.

    For video files (MP4, MOV, M4V, WEBM), the audio track is extracted
    server-side via ffmpeg BEFORE the bytes are sent to Deepgram — that
    way we ship 25 MB of audio instead of 500 MB of video over the wire,
    and the transcription cost stays the same (Deepgram bills per minute
    of audio, not per byte).

    Args:
        file_bytes: Raw bytes of the uploaded file (audio OR video).
        mime_type:  Content type header that would be sent if it were
            already audio. Used as a fallback for the Deepgram call when
            the file is audio. For video files, this is overridden with
            audio/mpeg after extraction.
        source_filename: Original filename — used to detect video formats
            so we know whether to run ffmpeg first.

    Returns:
        dict with keys:
          - found: bool        — True if transcription succeeded
          - error: str | None  — non-None if it failed
          - utterances: list   — [{speaker: 0|1|..., text: str, start: float,
                                  end: float}] in chronological order
          - full_text: str     — flat transcript without speaker labels
          - labeled_text: str  — speaker-labeled markdown: "**Speaker A:** ..."
          - duration_seconds: float
          - language: str
          - extracted_from_video: bool — True if we stripped video first
          - raw: dict          — full Deepgram response for debugging
    """
    if not file_bytes:
        return {"found": False, "error": "No audio data provided."}
    if not is_configured():
        return {"found": False, "error": "Deepgram API key not configured."}

    # If this is a video file, strip the video track first so we only
    # send audio to Deepgram. Big upload to us, small upload to them.
    extracted_from_video = False
    if source_filename and is_video_file(source_filename):
        try:
            source_ext = source_filename.rsplit(".", 1)[-1].lower()
            file_bytes = extract_audio_from_video(file_bytes, source_ext=source_ext)
            mime_type = "audio/mpeg"
            extracted_from_video = True
        except Exception as e:
            return {"found": False,
                    "error": f"Could not extract audio from video file: {e}"}

    try:
        req = urllib.request.Request(
            DEEPGRAM_URL,
            data=file_bytes,
            method="POST",
            headers={
                "Authorization": f"Token {_api_key()}",
                "Content-Type": mime_type or "audio/mpeg",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = f"Deepgram API error {e.code}"
        try:
            body = e.read().decode("utf-8")
            msg += f": {body[:300]}"
        except Exception:
            pass
        return {"found": False, "error": msg}
    except urllib.error.URLError as e:
        return {"found": False, "error": f"Network error reaching Deepgram: {e}"}
    except Exception as e:
        return {"found": False, "error": f"Transcription failed: {e}"}

    # Extract structured utterances from the response
    try:
        results = data.get("results", {}) or {}
        utterances = results.get("utterances", []) or []

        # Normalize: just the bits we need downstream
        norm: List[Dict[str, Any]] = []
        for u in utterances:
            norm.append({
                "speaker": int(u.get("speaker", 0) or 0),
                "text": (u.get("transcript") or "").strip(),
                "start": float(u.get("start", 0) or 0),
                "end": float(u.get("end", 0) or 0),
                "confidence": float(u.get("confidence", 0) or 0),
            })

        # Fallback: if utterances are empty but we have a flat transcript
        # (some shorter audio cases), build a single-speaker placeholder.
        full_text = ""
        try:
            channels = results.get("channels", []) or []
            if channels:
                alts = channels[0].get("alternatives", []) or []
                if alts:
                    full_text = (alts[0].get("transcript") or "").strip()
        except Exception:
            pass
        if not norm and full_text:
            norm.append({"speaker": 0, "text": full_text, "start": 0.0,
                         "end": 0.0, "confidence": 0.0})
        if not full_text:
            full_text = " ".join(u["text"] for u in norm)

        # Speaker-labeled markdown for reading
        labeled_parts: List[str] = []
        speaker_names = {0: "Speaker A", 1: "Speaker B", 2: "Speaker C", 3: "Speaker D"}
        last_speaker = -1
        current_buf: List[str] = []
        for u in norm:
            sp = u["speaker"]
            if sp != last_speaker and current_buf:
                labeled_parts.append(
                    f"**{speaker_names.get(last_speaker, f'Speaker {last_speaker}')}:** "
                    + " ".join(current_buf)
                )
                current_buf = []
            current_buf.append(u["text"])
            last_speaker = sp
        if current_buf:
            labeled_parts.append(
                f"**{speaker_names.get(last_speaker, f'Speaker {last_speaker}')}:** "
                + " ".join(current_buf)
            )
        labeled_text = "\n\n".join(labeled_parts)

        # Duration from metadata
        duration = float((data.get("metadata") or {}).get("duration", 0) or 0)
        language = ((results.get("channels") or [{}])[0]
                    .get("detected_language", "en") or "en")

        return {
            "found": True,
            "error": None,
            "utterances": norm,
            "full_text": full_text,
            "labeled_text": labeled_text,
            "duration_seconds": duration,
            "language": language,
            "extracted_from_video": extracted_from_video,
            "raw": data,
        }
    except Exception as e:
        return {"found": False, "error": f"Failed to parse Deepgram response: {e}",
                "raw": data}


def relabel_speakers(transcription: Dict[str, Any], speaker_labels: Dict[int, str]
                     ) -> Dict[str, Any]:
    """Replace generic speaker labels ('Speaker A') with named labels
    ('Rep', 'Seller'). Returns a new dict; doesn't mutate the original.

    Args:
        transcription: result from transcribe_audio()
        speaker_labels: {0: "Rep", 1: "Seller", ...}
    """
    if not transcription.get("found"):
        return transcription
    new_parts: List[str] = []
    last_speaker = -1
    current_buf: List[str] = []
    for u in transcription["utterances"]:
        sp = u["speaker"]
        if sp != last_speaker and current_buf:
            label = speaker_labels.get(last_speaker, f"Speaker {last_speaker}")
            new_parts.append(f"**{label}:** " + " ".join(current_buf))
            current_buf = []
        current_buf.append(u["text"])
        last_speaker = sp
    if current_buf:
        label = speaker_labels.get(last_speaker, f"Speaker {last_speaker}")
        new_parts.append(f"**{label}:** " + " ".join(current_buf))

    out = dict(transcription)
    out["labeled_text"] = "\n\n".join(new_parts)
    out["speaker_labels"] = speaker_labels
    return out
