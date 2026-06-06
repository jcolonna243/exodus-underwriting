"""Call Reviews — coaching dashboard for Admin and Manager.

Lists every call analysis across every deal. Filterable by uploading agent,
deal/property, call type, date range, and grade. Click into one to see the
full grading + transcript + deal context, and leave a coaching note that
gets stored against the analysis.

Visibility: Admin + Manager only. Agents are hard-redirected away.
"""
import datetime as dt
from typing import Dict, Any, List, Optional

import streamlit as st

from modules.auth import require_login, sidebar_account_widget
from modules import call_analysis as analysis_mod
from modules.checklist_pdf import build_checklist_pdf
from modules.db import (list_all_call_analyses, get_call_analysis,
                        save_coaching_note, delete_call_analysis)
from modules.settings import can_review_calls


st.set_page_config(page_title="Call Reviews", page_icon="📞", layout="wide")
user = require_login()
sidebar_account_widget()

# --- Role gate -----------------------------------------------------------
if not can_review_calls(user["email"]):
    st.error("📞 Call Reviews is for Managers and Admins only.")
    st.write(
        "If you believe you should have access to this page, contact Jo to "
        "have your role updated."
    )
    st.stop()


st.title("📞 Call Reviews")
st.caption(
    "Every uploaded seller call across every deal. Filter by agent, deal, "
    "call type, date, or grade. Click a row to see the full analysis and "
    "leave a coaching note."
)


# --- Detail view --------------------------------------------------------
# When a user clicks 'Review' on a row, store the analysis_id in session
# state and render the detail view below.
if "review_analysis_id" in st.session_state:
    aid = st.session_state["review_analysis_id"]
    if st.button("← Back to all reviews"):
        st.session_state.pop("review_analysis_id", None)
        st.rerun()

    row = get_call_analysis(aid)
    if not row:
        st.error("That call analysis was not found.")
        st.stop()

    # Header — deal address + call meta
    deal = row.get("deals") or {}
    addr = deal.get("address", "(deal removed)")
    city = ", ".join(filter(None, [deal.get("city"), deal.get("state")]))
    st.markdown(f"### {addr} — {city}")
    st.caption(
        f"**Call type:** {row.get('call_type', '—')}  ·  "
        f"**Uploaded by:** {row.get('created_by', '—')}  ·  "
        f"**Date:** {(row.get('created_at') or '')[:10]}  ·  "
        f"**Duration:** {row.get('audio_duration_seconds', 0):.0f}s  ·  "
        f"**File:** `{row.get('audio_filename', '—')}`"
    )

    # The analysis itself
    analysis = row.get("analysis") or {}
    st.markdown(analysis_mod.format_full_analysis(analysis))

    # --- Process Call Checklist PDF download ---------------------------
    # Generates a printable checklist that mirrors the Results Driven sheet
    # — useful as a tangible artifact for 1-on-1 coaching with the agent.
    try:
        # Carry the saved coaching note into the PDF if there is one
        analysis_for_pdf = dict(analysis)
        if row.get("coaching_note"):
            analysis_for_pdf["_coaching_note"] = row["coaching_note"]
        pdf_deal_ctx = {
            "address": deal.get("address", "(no address)"),
            "city": deal.get("city", ""),
            "state": deal.get("state", ""),
            "strategy": deal.get("strategy", ""),
        }
        pdf_call_meta = {
            "call_type": row.get("call_type", "—"),
            "uploaded_by": row.get("created_by", "—"),
            "uploaded_at": row.get("created_at", ""),
            "duration_seconds": row.get("audio_duration_seconds", 0) or 0,
        }
        pdf_bytes = build_checklist_pdf(analysis_for_pdf, pdf_deal_ctx, pdf_call_meta)
        # Filename: Process_Checklist_Address_Date.pdf
        safe_addr = "".join(c if c.isalnum() or c in "-_" else "_"
                            for c in deal.get("address", "deal"))[:60]
        safe_date = (row.get("created_at") or "")[:10]
        st.download_button(
            "📄 Download Process Call Checklist (PDF)",
            data=pdf_bytes,
            file_name=f"Process_Checklist_{safe_addr}_{safe_date}.pdf",
            mime="application/pdf",
            use_container_width=True,
            help="Printable version of the Process Call Checklist for use "
                 "during 1-on-1 coaching with the agent.",
        )
    except Exception as e:
        st.warning(f"Could not generate the checklist PDF: {e}")

    # Coaching note section
    st.markdown("---")
    st.markdown("### 📝 Coaching Note")
    st.caption(
        "Use this to capture your coaching takeaways for this call. The note "
        "is NOT visible to the agent in the app — share it with them through "
        "your normal coaching cadence (1-on-1, Slack, etc.)."
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
        key=f"coaching_note_{aid}",
        placeholder=(
            "e.g. 'On the next call: lead with what they told you about the "
            "mortgage situation. Use 'I couldn't even imagine' before the "
            "price ask.'"
        ),
    )
    c_save, c_del = st.columns([1, 1])
    if c_save.button("💾 Save coaching note", type="primary",
                     use_container_width=True):
        if save_coaching_note(aid, new_note, user["email"]):
            st.success("Coaching note saved.")
            st.rerun()
        else:
            st.error("Could not save the coaching note. Try again.")
    if c_del.button("🗑 Delete this call analysis",
                    use_container_width=True):
        if delete_call_analysis(aid):
            st.toast("Deleted.", icon="🗑")
            st.session_state.pop("review_analysis_id", None)
            st.rerun()

    # Transcript at the bottom
    transcript = row.get("transcript") or {}
    if transcript.get("labeled_text"):
        with st.expander("📜 Transcript (speaker-labeled)", expanded=False):
            st.markdown(transcript["labeled_text"])

    st.stop()


# --- List view (default) -----------------------------------------------
try:
    rows = list_all_call_analyses(limit=500)
except Exception as e:
    st.error(f"Could not load call reviews: {e}")
    st.stop()

if not rows:
    st.info(
        "No call recordings have been uploaded yet. Agents upload calls "
        "from the New Deal page; once they do, every recording will show "
        "up here for you to review."
    )
    st.stop()

# --- Filters ------------------------------------------------------------
agents = sorted({r.get("created_by", "") for r in rows if r.get("created_by")})
call_types = sorted({r.get("call_type", "") for r in rows if r.get("call_type")})

f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 2, 1])
agent_filter = f1.selectbox(
    "Agent", ["All"] + agents, key="cr_agent_filter",
)
type_filter = f2.selectbox(
    "Call type", ["All"] + call_types, key="cr_type_filter",
)
grade_filter = f3.selectbox(
    "Grade", ["All", "A", "B", "C", "D", "F"], key="cr_grade_filter",
)
search = f4.text_input(
    "Search address", placeholder="e.g. Lake Worth, Lutz, 1219",
    key="cr_search",
).strip().lower()
days_filter = f5.selectbox(
    "Last", ["All", "7d", "30d", "90d"], key="cr_days_filter",
)


# --- Apply filters ------------------------------------------------------
def _row_matches(r: Dict[str, Any]) -> bool:
    if agent_filter != "All" and r.get("created_by") != agent_filter:
        return False
    if type_filter != "All" and r.get("call_type") != type_filter:
        return False
    if grade_filter != "All":
        g = ((r.get("analysis") or {}).get("overall_grade") or "").upper()
        # Allow "B+" / "B-" to match "B"
        if not g.startswith(grade_filter):
            return False
    if search:
        deal = r.get("deals") or {}
        haystack = " ".join(filter(None, [
            deal.get("address", ""), deal.get("city", ""), deal.get("state", "")
        ])).lower()
        if search not in haystack:
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

st.caption(
    f"Showing **{len(filtered)}** of {len(rows)} call recordings."
)


# --- Render list as cards -----------------------------------------------
for row in filtered:
    deal = row.get("deals") or {}
    analysis = row.get("analysis") or {}
    addr = deal.get("address", "(deal removed)")
    city = ", ".join(filter(None, [deal.get("city"), deal.get("state")]))

    grade = analysis.get("overall_grade", "—")
    likelihood = analysis.get("contract_likelihood_pct", None)
    summary = analysis.get("summary_one_line", "")
    has_note = bool(row.get("coaching_note"))
    note_indicator = " · 📝 noted" if has_note else ""

    with st.container(border=True):
        c_main, c_meta, c_act = st.columns([5, 2, 1])
        with c_main:
            st.markdown(f"**{addr}** — *{city}*")
            st.caption(
                f"{row.get('call_type', '—')}  ·  "
                f"agent: **{row.get('created_by', '—')}**  ·  "
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
            if st.button("Review →", key=f"open_review_{row['id']}",
                         use_container_width=True):
                st.session_state["review_analysis_id"] = row["id"]
                st.rerun()
