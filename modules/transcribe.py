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
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
import streamlit as st


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


def transcribe_audio(file_bytes: bytes, mime_type: str = "audio/mpeg") -> Dict[str, Any]:
    """Transcribe an audio file with speaker diarization.

    Args:
        file_bytes: Raw bytes of the audio file (mp3, m4a, wav, mp4, etc.)
        mime_type:  Content type header to send. Common values:
                      "audio/mpeg"  for .mp3
                      "audio/mp4"   for .m4a / .m4b
                      "audio/wav"   for .wav
                      "video/mp4"   for .mp4 (extracts audio track)

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
          - raw: dict          — full Deepgram response for debugging
    """
    if not file_bytes:
        return {"found": False, "error": "No audio data provided."}
    if not is_configured():
        return {"found": False, "error": "Deepgram API key not configured."}

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
