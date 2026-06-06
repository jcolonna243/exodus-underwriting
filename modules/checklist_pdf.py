"""Generate a downloadable PDF version of the Process Call Checklist.

The PDF mirrors the layout of the Results Driven checklist Jo uses for live
coaching: a three-column table (checkbox / STEP / FEEDBACK) grouped into
sections (Intro, Set Expectations, Motivation, Property Condition, Road Blocks,
Set Expectations close), with a Notes block at the end. The checkboxes are
populated from analysis['process_call_checklist'] — ✓ for covered, blank for
missed — so managers can use the printout (or PDF on screen) the same way they
would the Results Driven original.

If the analysis has process_call_checklist.applicable = False (because the
call was not a Process Call), the PDF instead shows a one-page note explaining
that no checklist applies to this call type and suggesting the manager use the
full analysis from the Call Reviews page directly.
"""
from __future__ import annotations
from io import BytesIO
from datetime import datetime
from typing import Dict, Any, List, Tuple


def _build_section_rows(
    section_title: str,
    items: List[Tuple[str, Dict[str, Any]]],
) -> List[List[Any]]:
    """Render one labelled section of the checklist as table rows.

    Args:
        section_title: e.g. "Set Expectations", "Motivation"
        items: list of (label, sub_dict) where sub_dict has 'covered' + 'feedback'

    Returns a list of [check_cell, step_cell, feedback_cell] rows. The first
    row is the section heading (no checkbox, no feedback column populated).
    """
    rows: List[List[Any]] = []
    # Section header row — visually distinct
    rows.append([None, section_title, None])
    for label, sub in items:
        sub = sub or {}
        covered = bool(sub.get("covered", False))
        feedback = sub.get("feedback", "") or ""
        rows.append([covered, label, feedback])
    return rows


def build_checklist_pdf(
    analysis: Dict[str, Any],
    deal_context: Dict[str, Any],
    call_meta: Dict[str, Any],
) -> bytes:
    """Return PDF bytes for the Process Call Checklist of one analyzed call.

    Args:
        analysis: the full call_analysis dict (with process_call_checklist)
        deal_context: {"address": ..., "city": ..., "state": ..., "strategy": ...}
        call_meta: {"call_type": ..., "uploaded_by": ..., "uploaded_at": ...,
                    "duration_seconds": ...}
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, KeepTogether,
                                     PageBreak)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER,
                            leftMargin=0.6*inch, rightMargin=0.6*inch,
                            topMargin=0.6*inch, bottomMargin=0.6*inch)

    styles = getSampleStyleSheet()
    title_s = ParagraphStyle("Title", parent=styles["Heading1"],
                             fontName="Helvetica-Bold", fontSize=18,
                             textColor=colors.HexColor("#1F4E78"),
                             alignment=TA_CENTER, spaceAfter=4)
    sub_s = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10,
                           textColor=colors.HexColor("#666666"),
                           alignment=TA_CENTER, spaceAfter=4)
    meta_s = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9,
                            textColor=colors.HexColor("#333333"),
                            alignment=TA_LEFT, spaceAfter=10)
    cell_s = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9,
                            textColor=colors.HexColor("#000000"),
                            leading=11)
    cell_bold = ParagraphStyle("CellB", parent=cell_s,
                                fontName="Helvetica-Bold")
    feedback_s = ParagraphStyle("Feedback", parent=cell_s, fontSize=8,
                                 textColor=colors.HexColor("#444444"))
    notes_label_s = ParagraphStyle("NotesLabel", parent=styles["Heading2"],
                                    fontName="Helvetica-Bold", fontSize=12,
                                    textColor=colors.HexColor("#1F4E78"),
                                    spaceBefore=12, spaceAfter=4)
    notes_body_s = ParagraphStyle("NotesBody", parent=styles["Normal"], fontSize=10,
                                   textColor=colors.HexColor("#222222"))

    story: List[Any] = []

    # --- Header --------------------------------------------------------
    story.append(Paragraph("The Process Call Checklist", title_s))
    story.append(Paragraph(
        f"Exodus Property Solutions &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"{datetime.now().strftime('%B %d, %Y')}",
        sub_s,
    ))
    story.append(Spacer(1, 6))

    # Deal + call metadata block
    addr = deal_context.get("address", "—")
    city_state = ", ".join(filter(None, [
        deal_context.get("city", ""), deal_context.get("state", "")
    ]))
    meta_lines = [
        f"<b>Property:</b> {addr}{(', ' + city_state) if city_state else ''}",
        f"<b>Recommended strategy:</b> {deal_context.get('strategy', '—')}",
        f"<b>Call type:</b> {call_meta.get('call_type', '—')}",
        f"<b>Uploaded by:</b> {call_meta.get('uploaded_by', '—')} "
        f"&nbsp;&nbsp; <b>Date:</b> {(call_meta.get('uploaded_at') or '')[:10]}"
        f" &nbsp;&nbsp; <b>Duration:</b> {call_meta.get('duration_seconds', 0):.0f}s",
        f"<b>Overall grade:</b> {analysis.get('overall_grade', '—')} "
        f"&nbsp;&nbsp; <b>Contract likelihood:</b> "
        f"{analysis.get('contract_likelihood_pct', '—')}%",
    ]
    for line in meta_lines:
        story.append(Paragraph(line, meta_s))

    # --- Not applicable? Short page and done ---------------------------
    checklist = analysis.get("process_call_checklist", {}) or {}
    if not checklist.get("applicable", False):
        note = checklist.get("applicable_note", "")
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "<b>The Process Call Checklist does not apply to this call type.</b>",
            notes_body_s,
        ))
        if note:
            story.append(Spacer(1, 6))
            story.append(Paragraph(note, notes_body_s))
        story.append(Spacer(1, 12))
        story.append(Paragraph(
            "Use the full analysis on the Call Reviews page for grading this "
            "call type — the structured analysis (UMBC, pain points, what the "
            "rep did well, what the rep missed, next-call recommendations) "
            "remains the source of truth for non-Process calls.",
            notes_body_s,
        ))
        doc.build(story)
        return buf.getvalue()

    # --- Build the checklist table -------------------------------------
    rows: List[List[Any]] = []
    section_header_rows: List[int] = []  # to style differently
    item_rows: List[int] = []  # to style differently

    def add_section(title: str, items: List[Tuple[str, Dict[str, Any]]]) -> None:
        rows.append([None, title, None])
        section_header_rows.append(len(rows) - 1)
        for label, sub in items:
            sub = sub or {}
            covered = bool(sub.get("covered", False))
            feedback = sub.get("feedback", "") or ""
            rows.append([covered, label, feedback])
            item_rows.append(len(rows) - 1)

    # Intro is a single line, not in a section
    intro = checklist.get("intro") or {}
    rows.append([
        bool(intro.get("covered", False)),
        "Intro",
        intro.get("feedback", "") or "",
    ])
    item_rows.append(len(rows) - 1)

    se_open = checklist.get("set_expectations_open", {}) or {}
    add_section("Set Expectations (open)", [
        ("a. Time",                se_open.get("time")),
        ("b. Agenda",              se_open.get("agenda")),
        ("c. Result",              se_open.get("result")),
        ("d. Permission to say No", se_open.get("permission_to_say_no")),
        ("e. Urgency",             se_open.get("urgency")),
    ])

    mot = checklist.get("motivation", {}) or {}
    add_section("Motivation", [
        ("a. Situation",            mot.get("situation")),
        ("b. Impact 1",             mot.get("impact_1")),
        ("c. Impact 2",             mot.get("impact_2")),
        ("d. Impact 3",             mot.get("impact_3")),
        ("e. Perfect Picture / Goal", mot.get("perfect_picture")),
    ])

    pc = checklist.get("property_condition") or {}
    rows.append([
        bool(pc.get("covered", False)),
        "Property Condition",
        pc.get("feedback", "") or "",
    ])
    item_rows.append(len(rows) - 1)

    rb = checklist.get("road_blocks", {}) or {}
    add_section("Road Blocks", [
        ("a. Time",        rb.get("time")),
        ("b. Influencers", rb.get("influencers")),
        ("c. Discomfort",  rb.get("discomfort")),
        ("d. Money",       rb.get("money")),
    ])

    se_close = checklist.get("set_expectations_close", {}) or {}
    add_section("Set Expectations (close)", [
        ("a. Time",                 se_close.get("time")),
        ("b. Agenda",               se_close.get("agenda")),
        ("c. Result",               se_close.get("result")),
        ("d. Permission to say No", se_close.get("permission_to_say_no")),
    ])

    # Build the actual Table data — render Paragraphs for wrapping
    table_data: List[List[Any]] = [[
        Paragraph("<b>&nbsp;</b>", cell_bold),
        Paragraph("<b>STEP</b>", cell_bold),
        Paragraph("<b>FEEDBACK</b>", cell_bold),
    ]]
    for r_idx, (chk, step, feedback) in enumerate(rows, start=1):
        # Section header rows have chk=None and feedback=None
        if chk is None:
            cell_chk = Paragraph("", cell_s)
            cell_step = Paragraph(f"<b>{step}</b>", cell_bold)
            cell_feedback = Paragraph("", cell_s)
        else:
            mark = "✔" if chk else ""
            cell_chk = Paragraph(
                f"<font size=14 color='#1F4E78'><b>{mark}</b></font>"
                if chk else "<font size=14>&nbsp;</font>",
                cell_s,
            )
            cell_step = Paragraph(step, cell_s)
            cell_feedback = Paragraph(feedback, feedback_s)
        table_data.append([cell_chk, cell_step, cell_feedback])

    table = Table(
        table_data,
        colWidths=[0.4*inch, 1.6*inch, 5.0*inch],
        repeatRows=1,
    )
    # Base style
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#B8D8EB")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#1F4E78")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    # Section header rows get a soft tint
    for hr in section_header_rows:
        # +1 because table_data[0] is the header row
        style_cmds.append((
            "BACKGROUND", (0, hr + 1), (-1, hr + 1),
            colors.HexColor("#EAF1F8"),
        ))
        style_cmds.append((
            "SPAN", (0, hr + 1), (0, hr + 1),
        ))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    # --- Notes ---------------------------------------------------------
    notes_text = checklist.get("notes", "")
    coaching_note = analysis.get("_coaching_note")  # if caller injects it
    story.append(Paragraph("Notes", notes_label_s))
    if notes_text:
        story.append(Paragraph(notes_text, notes_body_s))
    else:
        story.append(Paragraph("(No synthesis notes from grading.)", notes_body_s))

    if coaching_note:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Manager's Coaching Note:</b>", notes_body_s))
        story.append(Paragraph(coaching_note, notes_body_s))

    # ======================================================================
    # PAGE 2+ — SUPPLEMENTAL ANALYSIS
    # Everything the AI generated that isn't already in the checklist —
    # so the printed/exported PDF is the COMPLETE coaching artifact and
    # nothing is hidden behind the Call Reviews UI.
    # ======================================================================
    story.append(PageBreak())

    # Reuse some styles, define a couple more for this section
    h2_s = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13,
        textColor=colors.HexColor("#1F4E78"), spaceBefore=10, spaceAfter=4,
    )
    h3_s = ParagraphStyle(
        "H3", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=11,
        textColor=colors.HexColor("#222222"), spaceBefore=6, spaceAfter=2,
    )
    body_s = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#222222"), spaceAfter=4, leading=13,
    )
    quote_s = ParagraphStyle(
        "Quote", parent=body_s, fontSize=9, textColor=colors.HexColor("#555555"),
        leftIndent=14, italic=True, spaceAfter=4,
    )
    bullet_s = ParagraphStyle(
        "Bullet", parent=body_s, leftIndent=14, bulletIndent=0,
    )
    caption_grey_s = ParagraphStyle(
        "Cap", parent=body_s, fontSize=9,
        textColor=colors.HexColor("#888888"),
    )

    def _safe(text: Any) -> str:
        """Escape characters that confuse reportlab's mini-HTML parser."""
        s = "" if text is None else str(text)
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))

    # --- Page header --------------------------------------------------
    story.append(Paragraph("Supplemental Analysis", title_s))
    story.append(Paragraph(
        "Everything the AI flagged about this call, beyond the checklist.",
        sub_s,
    ))
    summary_line = analysis.get("summary_one_line", "")
    if summary_line:
        story.append(Paragraph(f"<i>{_safe(summary_line)}</i>", body_s))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"<b>Overall grade:</b> {_safe(analysis.get('overall_grade', '—'))} "
        f"&nbsp;&nbsp; "
        f"<b>Contract likelihood:</b> "
        f"{_safe(analysis.get('contract_likelihood_pct', '—'))}%",
        body_s,
    ))

    # --- 1. UMBC Qualification ---------------------------------------
    umbc = analysis.get("umbc", {}) or {}
    if umbc:
        story.append(Paragraph("UMBC Qualification", h2_s))
        umbc_labels = {
            "urgency": "U — Urgency",
            "motivation": "M — Motivation",
            "ballpark": "B — Ballpark",
            "condition": "C — Condition",
        }
        for key, label in umbc_labels.items():
            sect = umbc.get(key, {}) or {}
            score = _safe(sect.get("score", "—"))
            note = _safe(sect.get("note", ""))
            story.append(Paragraph(f"<b>{label}</b> — {score}", h3_s))
            if note:
                story.append(Paragraph(note, body_s))
            evidence = sect.get("evidence") or sect.get("seller_quoted_price")
            if evidence and evidence not in ("not asked", ""):
                story.append(Paragraph(f"Evidence: \"{_safe(evidence)}\"", quote_s))
            if key == "motivation":
                cats = sect.get("categories_found") or []
                if cats:
                    story.append(Paragraph(
                        f"Categories surfaced: {_safe(', '.join(cats))}",
                        caption_grey_s,
                    ))
            if key == "condition":
                ic = sect.get("items_covered_count")
                if ic is not None:
                    story.append(Paragraph(
                        f"Property condition items covered: {_safe(ic)} / 16",
                        caption_grey_s,
                    ))

    # --- 2. Pain Points Identified -----------------------------------
    pains = analysis.get("pain_points_identified", []) or []
    if pains:
        story.append(Paragraph("Pain Points Revealed", h2_s))
        for p in pains:
            cat = _safe(p.get("category", "—"))
            followed = "✓ rep followed up" if p.get("rep_followed_up") else "✗ rep did NOT follow up"
            quality = _safe(p.get("rep_response_quality", ""))
            story.append(Paragraph(
                f"<b>{cat}</b> — {followed} ({quality})", body_s,
            ))
            q = p.get("seller_quote") or ""
            if q:
                story.append(Paragraph(f"\"{_safe(q)}\"", quote_s))

    # --- 3. What the Rep Did Well ------------------------------------
    well = analysis.get("what_rep_did_well", []) or []
    if well:
        story.append(Paragraph("What the Rep Did Well", h2_s))
        for w in well:
            moment = _safe(w.get("moment", "—"))
            why = _safe(w.get("why_good", ""))
            story.append(Paragraph(f"<b>{moment}</b>", h3_s))
            if why:
                story.append(Paragraph(why, body_s))
            q = w.get("quote_or_timestamp") or ""
            if q:
                story.append(Paragraph(f"\"{_safe(q)}\"", quote_s))

    # --- 4. What the Rep Missed --------------------------------------
    missed = analysis.get("what_rep_missed", []) or []
    if missed:
        story.append(Paragraph("What the Rep Missed", h2_s))
        for m in missed:
            moment = _safe(m.get("moment", "—"))
            story.append(Paragraph(f"<b>{moment}</b>", h3_s))
            ctx = m.get("quote_or_context") or ""
            if ctx:
                story.append(Paragraph(f"Context: \"{_safe(ctx)}\"", quote_s))
            fit = m.get("what_should_have_fit") or ""
            if fit:
                story.append(Paragraph(
                    f"<b>Should have:</b> {_safe(fit)}", body_s,
                ))

    # --- 5. Objections Encountered -----------------------------------
    objs = analysis.get("objections_encountered", []) or []
    if objs:
        story.append(Paragraph("Objections Encountered", h2_s))
        for o in objs:
            otype = _safe(o.get("objection_type", "—"))
            framework = _safe(o.get("framework_used", "—"))
            ok = "✓ handled well" if o.get("handled_well") else "✗ mishandled"
            story.append(Paragraph(
                f"<b>{otype}</b> — {ok} (framework: <i>{framework}</i>)",
                body_s,
            ))
            q = o.get("seller_quote") or ""
            if q:
                story.append(Paragraph(f"\"{_safe(q)}\"", quote_s))
            note = o.get("note") or ""
            if note:
                story.append(Paragraph(_safe(note), body_s))

    # --- 6. Non-Committal Language Detected --------------------------
    ncl = analysis.get("non_committal_language_detected", []) or []
    if ncl:
        story.append(Paragraph("Non-Committal Language Detected", h2_s))
        for n in ncl:
            phrase = _safe(n.get("seller_phrase", ""))
            story.append(Paragraph(
                f"Seller said: \"{phrase}\"", body_s,
            ))
            rep_resp = n.get("rep_response") or ""
            if rep_resp:
                story.append(Paragraph(
                    f"Rep response: {_safe(rep_resp)}", body_s,
                ))
            should = n.get("should_have") or ""
            if should:
                story.append(Paragraph(
                    f"<b>Should have:</b> {_safe(should)}", body_s,
                ))

    # --- 7. Next-Call Tactical Recommendations -----------------------
    nexts = analysis.get("next_call_recommendations", []) or []
    if nexts:
        story.append(Paragraph("Next-Call Tactical Recommendations", h2_s))
        for i, rec_item in enumerate(nexts, 1):
            play = _safe(rec_item.get("play", "—"))
            story.append(Paragraph(f"<b>{i}. {play}</b>", h3_s))
            phrasing = rec_item.get("specific_phrasing") or ""
            if phrasing:
                story.append(Paragraph(
                    f"Specific phrasing: \"{_safe(phrasing)}\"", quote_s,
                ))
            outcome = rec_item.get("expected_outcome") or ""
            if outcome:
                story.append(Paragraph(
                    f"Expected outcome: {_safe(outcome)}", body_s,
                ))

    # --- 8. Qualification Verdict ------------------------------------
    qv = analysis.get("qualification_verdict", {}) or {}
    if qv:
        story.append(Paragraph("Qualification Verdict", h2_s))
        story.append(Paragraph(
            f"<b>Qualified:</b> {'Yes' if qv.get('qualified') else 'No'}  "
            f"&nbsp; <b>Path taken:</b> {_safe(qv.get('path_taken', '—'))}  "
            f"&nbsp; <b>Routing correct?</b> "
            f"{'Yes' if qv.get('was_routing_correct') else 'No'}",
            body_s,
        ))
        note = qv.get("note") or ""
        if note:
            story.append(Paragraph(_safe(note), body_s))

    # --- 9. Tonality Assessment --------------------------------------
    tone = analysis.get("tonality_assessment", "")
    if tone:
        story.append(Paragraph("Tonality Assessment", h2_s))
        story.append(Paragraph(_safe(tone), body_s))

    doc.build(story)
    return buf.getvalue()
