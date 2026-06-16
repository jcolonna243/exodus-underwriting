"""Training Calls — daily role-play coaching, separate from deal calls.

The use case: Jo (or any Manager) runs role-play calls with each agent on
the team and uploads the recording here. The tool transcribes and grades
the call the same way it grades a real seller call, but:

  - Not tied to any deal record
  - Trainee is tracked so we can see progress per-agent over time
  - Doesn't appear on the deal-focused Call Reviews page
  - Same PDF download, same checklist + supplemental analysis layout
  - Coaching note works the same way for capturing 1-on-1 takeaways

Visibility: Admin + Manager only. Agents are hard-redirected away.
"""
import datetime as dt
from typing import Dict, Any, List, Optional

import streamlit as st

from modules.auth import require_login, sidebar_account_widget
from modules import call_analysis as analysis_mod
from modules import transcribe as transcribe_mod
from modules.checklist_pdf import build_checklist_pdf
from modules.db import (save_call_analysis, list_training_calls,
                        get_training_call, save_coaching_note,
                        delete_call_analysis)
from modules.settings import can_review_calls, list_user_roles


st.set_page_config(page_title="Training Calls", page_icon="🎓", layout="wide")
user = require_login()
sidebar_account_widget()

# --- Role gate -----------------------------------------------------------
if not can_review_calls(user["email"]):
    st.error("🎓 Training Calls is for Managers and Admins only.")
    st.write(
        "If you believe you should have access to this page, contact Jo to "
        "have your role updated."
    )
    st.stop()


# =========================================================================
# DETAIL VIEW — clicked into a specific training call
# =========================================================================
if "review_training_id" in st.session_state:
    tid = st.session_state["review_training_id"]
    if st.button("← Back to all training calls"):
        st.session_state.pop("review_training_id", None)
        st.rerun()

    row = get_training_call(tid)
    if not row:
        st.error("That training call was not found.")
        st.stop()

    label = row.get("training_label") or row.get("call_type", "Training Call")
    st.markdown(f"### 🎓 {label}")
    st.caption(
        f"**Call type:** {row.get('call_type', '—')}  ·  "
        f"**Trainee:** {row.get('trainee_email', '—')}  ·  "
        f"**Uploaded by:** {row.get('created_by', '—')}  ·  "
        f"**Date:** {(row.get('created_at') or '')[:10]}  ·  "
        f"**Duration:** {row.get('audio_duration_seconds', 0):.0f}s  ·  "
        f"**File:** `{row.get('audio_filename', '—')}`"
    )

    analysis = row.get("analysis") or {}
    st.markdown(analysis_mod.format_full_analysis(analysis))

    # --- PDF download ---
    try:
        analysis_for_pdf = dict(analysis)
        if row.get("coaching_note"):
            analysis_for_pdf["_coaching_note"] = row["coaching_note"]
        pdf_deal_ctx = {
            "address": row.get("training_label") or "Training Role-Play",
            "city": "",
            "state": "",
            "strategy": "(Training — no real deal)",
        }
        pdf_call_meta = {
            "call_type": row.get("call_type", "—"),
            "uploaded_by": row.get("created_by", "—"),
            "uploaded_at": row.get("created_at", ""),
            "duration_seconds": row.get("audio_duration_seconds", 0) or 0,
        }
        pdf_bytes = build_checklist_pdf(analysis_for_pdf, pdf_deal_ctx, pdf_call_meta)
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_"
                              for c in (row.get("training_label") or "training"))[:60]
        safe_date = (row.get("created_at") or "")[:10]
        st.download_button(
            "📄 Download Coaching PDF (Checklist + Full Analysis)",
            data=pdf_bytes,
            file_name=f"Training_{safe_label}_{safe_date}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(f"Could not generate the PDF: {e}")

    # --- Coaching note ---
    st.markdown("---")
    st.markdown("### 📝 Coaching Note")
    st.caption(
        "Capture what you want to share with the trainee in your next 1-on-1. "
        "This note is NOT visible to the agent in the app."
    )
    note_existing = row.get("coaching_note") or ""
    note_by = row.get("coaching_note_by") or ""
    note_at = (row.get("coaching_note_at") or "")[:10]
    if note_existing:
        st.caption(f"Last edited by {note_by} on {note_at}")

    new_note = st.text_area(
        "Coaching note",
        value=note_existing,
        height=200,
        key=f"training_coaching_{tid}",
        placeholder=(
            "e.g. 'Three things to work on: 1) smile when you say your name, "
            "2) mirror the seller's words back instead of manufacturing emotion, "
            "3) run the full property condition questionnaire next time.'"
        ),
    )
    c_save, c_del = st.columns([1, 1])
    if c_save.button("💾 Save coaching note", type="primary",
                     use_container_width=True):
        if save_coaching_note(tid, new_note, user["email"]):
            st.success("Coaching note saved.")
            st.rerun()
        else:
            st.error("Could not save the coaching note. Try again.")
    if c_del.button("🗑 Delete this training call",
                    use_container_width=True):
        if delete_call_analysis(tid):
            st.toast("Deleted.", icon="🗑")
            st.session_state.pop("review_training_id", None)
            st.rerun()

    transcript = row.get("transcript") or {}
    if transcript.get("labeled_text"):
        with st.expander("📜 Transcript (speaker-labeled)", expanded=False):
            st.markdown(transcript["labeled_text"])

    st.stop()


# =========================================================================
# LIST VIEW (default)
# =========================================================================
st.title("🎓 Training Calls")
st.caption(
    "Upload daily role-play recordings to grade your team's call mechanics. "
    "Same grading engine as real seller calls, scored against the methodology, "
    "but kept separate from the deal pipeline."
)


# --- Upload form -------------------------------------------------------
_missing = []
if not transcribe_mod.is_configured():
    _missing.append("Deepgram API key — `[deepgram]` in Streamlit Secrets.")
if not analysis_mod.is_configured():
    _missing.append("Anthropic API key — `[anthropic]` in Streamlit Secrets.")
if _missing:
    st.warning("Setup not complete:\n\n" + "\n\n".join(f"- {m}" for m in _missing))
    st.stop()

# Build the trainee dropdown from the configured user_roles.
# We surface every email so the manager can also grade themselves practicing.
roles = list_user_roles()
trainee_options = sorted(roles.keys())
trainee_options.append("Other (specify)")

with st.container(border=True):
    st.markdown("### 📥 Upload a Training Call")

    c_upl, c_type = st.columns([3, 2])
    audio_file = c_upl.file_uploader(
        "Audio or video recording (up to 1 GB)",
        type=["mp3", "m4a", "wav", "aac", "mp4", "mov", "m4v", "webm"],
        key="training_audio_uploader",
        help="Audio (mp3/m4a/wav) or video (mp4/mov from Google Meet) — "
             "videos are auto-converted to audio server-side. Faster uploads "
             "if you convert to MP3 first (see tip below).",
    )
    with c_upl.expander("💡 Speed up uploads: convert Google Meet MP4 to MP3 first"):
        st.markdown(
            "Google Meet recordings are mostly video data — the audio you "
            "actually need is 5-10% of the file size. Converting locally "
            "first means **smaller upload, faster transcription**, and the "
            "tool produces the exact same grading.\n\n"
            "**On Mac (no extra software needed):**\n"
            "1. Open the .mp4 file in **QuickTime Player**.\n"
            "2. Click **File → Export As → Audio Only…**\n"
            "3. Save as .m4a (or rename to .mp3 if you prefer).\n"
            "4. Upload the audio file here.\n\n"
            "**Or use [VLC](https://www.videolan.org/vlc/) (free, any OS):**\n"
            "1. Open VLC → **File → Convert/Stream**.\n"
            "2. Drop in the .mp4, pick **Audio - MP3** as the profile.\n"
            "3. Save and upload the resulting MP3.\n\n"
            "If you skip this and upload the MP4 directly, that's fine too — "
            "the server will strip the video for you (just a slower upload)."
        )
    call_type = c_type.selectbox(
        "Call type",
        ["Process Call", "Offer Call", "Renegotiation",
         "Follow-up", "Role-Play", "Other"],
        key="training_call_type",
        help="What scenario was the role-play? Affects how the methodology "
             "evaluates the structure.",
    )

    c_script, _ = st.columns([2, 3])
    script_used = c_script.selectbox(
        "Script used",
        ["Standard Process Script", "Foreclosure Process Script"],
        key="training_script_used",
        help="Which script the rep was practicing. Foreclosure adds the "
             "Educational Pivot (4 routes) and three foreclosure-specific "
             "situation questions to the grading rubric.",
    )

    c_trainee, c_label = st.columns([2, 3])
    trainee_pick = c_trainee.selectbox(
        "Trainee (who was the rep?)",
        trainee_options,
        key="training_trainee_pick",
        help="Pick the agent being trained. Their progress shows up in their "
             "per-trainee filter below.",
    )
    trainee_email_final: Optional[str] = trainee_pick
    if trainee_pick == "Other (specify)":
        trainee_email_final = c_trainee.text_input(
            "Trainee email", key="training_trainee_other",
            placeholder="someone@example.com",
        )
    training_label = c_label.text_input(
        "Training label (optional)",
        key="training_label",
        placeholder="e.g. Mock Process Call - Week 1 - Distressed seller",
    )

    # Speaker labels — diarization separates voices but doesn't know who's who
    c_s0, c_s1 = st.columns(2)
    speaker_0_label = c_s0.selectbox(
        "Speaker A is…", ["Rep", "Seller", "Other"],
        key="training_speaker_0", index=0,
    )
    speaker_1_label = c_s1.selectbox(
        "Speaker B is…", ["Rep", "Seller", "Other"],
        key="training_speaker_1", index=1,
    )
    speaker_labels = {0: speaker_0_label, 1: speaker_1_label}

    do_analyze = st.button(
        "🎯 Transcribe & Grade Training Call",
        type="primary",
        use_container_width=True,
        disabled=(audio_file is None
                   or (trainee_pick == "Other (specify)"
                       and not (trainee_email_final or "").strip())),
    )

    if do_analyze and audio_file is not None:
        # Step 1: transcribe (auto-extracts audio if uploaded a video)
        file_bytes = audio_file.getvalue()
        ext = (audio_file.name.rsplit(".", 1)[-1] or "").lower()
        mime = {
            "mp3": "audio/mpeg", "m4a": "audio/mp4", "m4b": "audio/mp4",
            "wav": "audio/wav", "aac": "audio/aac",
            "mp4": "video/mp4", "mov": "video/quicktime",
            "m4v": "video/mp4", "webm": "video/webm",
        }.get(ext, "audio/mpeg")
        is_video = transcribe_mod.is_video_file(audio_file.name)
        spinner_msg = (
            f"Extracting audio from {ext.upper()} then transcribing "
            "via Deepgram… (1-2 minutes for a video)"
            if is_video
            else "Transcribing via Deepgram… (30-60 sec for a 10-min call)"
        )
        with st.spinner(spinner_msg):
            tr = transcribe_mod.transcribe_audio(
                file_bytes, mime_type=mime,
                source_filename=audio_file.name,
            )

        if not tr.get("found"):
            st.error(f"Transcription failed: {tr.get('error', 'unknown')}")
        else:
            tr = transcribe_mod.relabel_speakers(tr, speaker_labels)

            with st.spinner("Claude is grading the role-play against the methodology…"):
                analysis = analysis_mod.analyze_call(
                    tr.get("labeled_text", ""),
                    deal_context={},  # no deal for training
                    call_type=call_type,
                    is_training=True,
                    training_label=training_label or None,
                    trainee_email=trainee_email_final,
                    script_used=script_used,
                )

            if "error" in analysis:
                st.error(f"Analysis failed: {analysis['error']}")
                if analysis.get("raw_response"):
                    with st.expander("Raw response (debug)"):
                        st.code(analysis["raw_response"])
            else:
                # Persist
                try:
                    new_id = save_call_analysis(
                        deal_id=None,
                        call_type=call_type,
                        audio_filename=audio_file.name,
                        audio_duration_seconds=tr.get("duration_seconds", 0),
                        transcript=tr,
                        analysis=analysis,
                        user_email=user.get("email") if isinstance(user, dict) else None,
                        is_training=True,
                        trainee_email=trainee_email_final,
                        training_label=training_label or None,
                    )
                    if new_id:
                        st.success(
                            f"✅ Training call graded and saved (#{new_id}). "
                            "Scroll down to see it in the list, or click "
                            "**Review →** to open the full analysis + PDF."
                        )
                        # Jump straight to detail view so the manager sees results
                        st.session_state["review_training_id"] = new_id
                        st.rerun()
                    else:
                        st.warning(
                            "Analysis ran but database write returned no row."
                        )
                        st.markdown(analysis_mod.format_full_analysis(analysis))
                except Exception as e:
                    st.error(f"Save failed: {e}")
                    st.markdown(analysis_mod.format_full_analysis(analysis))


# --- Past training calls list ------------------------------------------
st.markdown("---")
st.markdown("### Past Training Calls")

try:
    rows = list_training_calls(limit=500)
except Exception as e:
    st.error(f"Could not load training calls: {e}")
    st.stop()

if not rows:
    st.info(
        "No training calls uploaded yet. Use the form above to upload "
        "your first role-play recording."
    )
    st.stop()

# Filters
trainees = sorted({r.get("trainee_email", "") for r in rows
                    if r.get("trainee_email")})
call_types = sorted({r.get("call_type", "") for r in rows
                      if r.get("call_type")})

f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
trainee_filter = f1.selectbox("Trainee", ["All"] + trainees,
                               key="tc_trainee_filter")
type_filter = f2.selectbox("Call type", ["All"] + call_types,
                           key="tc_type_filter")
grade_filter = f3.selectbox("Grade", ["All", "A", "B", "C", "D", "F"],
                            key="tc_grade_filter")
days_filter = f4.selectbox("Last", ["All", "7d", "30d", "90d"],
                            key="tc_days_filter")


def _row_matches(r: Dict[str, Any]) -> bool:
    if trainee_filter != "All" and r.get("trainee_email") != trainee_filter:
        return False
    if type_filter != "All" and r.get("call_type") != type_filter:
        return False
    if grade_filter != "All":
        g = ((r.get("analysis") or {}).get("overall_grade") or "").upper()
        if not g.startswith(grade_filter):
            return False
    if days_filter != "All":
        n = int(days_filter.rstrip("d"))
        cutoff = dt.datetime.utcnow() - dt.timedelta(days=n)
        try:
            created = dt.datetime.fromisoformat(
                (r.get("created_at") or "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
            if created < cutoff:
                return False
        except Exception:
            pass
    return True


filtered = [r for r in rows if _row_matches(r)]
st.caption(f"Showing **{len(filtered)}** of {len(rows)} training calls.")

for row in filtered:
    analysis = row.get("analysis") or {}
    label = row.get("training_label") or row.get("call_type", "—")
    grade = analysis.get("overall_grade", "—")
    likelihood = analysis.get("contract_likelihood_pct", None)
    summary = analysis.get("summary_one_line", "")
    has_note = bool(row.get("coaching_note"))
    note_indicator = " · 📝 noted" if has_note else ""

    with st.container(border=True):
        c_main, c_meta, c_act = st.columns([5, 2, 1])
        with c_main:
            st.markdown(f"**{label}**")
            st.caption(
                f"{row.get('call_type', '—')}  ·  "
                f"trainee: **{row.get('trainee_email', '—')}**  ·  "
                f"by {row.get('created_by', '—')}  ·  "
                f"{(row.get('created_at') or '')[:10]}"
                f"{note_indicator}"
            )
            if summary:
                st.write(summary)
        with c_meta:
            st.metric("Grade", grade)
            if likelihood is not None:
                st.metric("Likelihood", f"{likelihood}%")
        with c_act:
            if st.button("Review →", key=f"open_training_{row['id']}",
                         use_container_width=True):
                st.session_state["review_training_id"] = row["id"]
                st.rerun()
