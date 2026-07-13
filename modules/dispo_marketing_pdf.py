"""Dispo Marketing PDF — internal deal sheet + ready-to-copy marketing copy.

Generated after Exodus signs a contract with a seller, to help move the deal
to a cash buyer / rehabber. The PDF has two halves:

  1. THE DEAL SHEET  — property snapshot, deal math, rehab breakdown with
     LOW-HIGH range, and the top 3-5 highest defensible comps that justify
     the ARV we're asking against.

  2. READY-TO-COPY MARKETING COPY — pre-written email, SMS, and Facebook
     post drafts filled with THIS deal's numbers. Jo copies and adapts.

Always sort comps by sold price DESC and take the highest 5 available (the
higher the comp, the stronger the ARV justification). Rehab items are
displayed with a +/- 15% range around the underwriting estimate so the
buyer sees the honest uncertainty band.
"""
from __future__ import annotations
from io import BytesIO
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_REHAB_RANGE_PCT = 0.15  # ±15% default, tunable per-deal on the page


def _rehab_range(
    items: List[Tuple[str, float]],
    range_pct: float = DEFAULT_REHAB_RANGE_PCT,
) -> Tuple[float, float, list]:
    """Return (total_low, total_high, per_item_ranges).

    per_item_ranges is a list of (label, low, mid, high) tuples.
    range_pct is the +/- band around the underwriting estimate (0.15 = ±15%).
    """
    low_mult = 1.0 - float(range_pct)
    high_mult = 1.0 + float(range_pct)
    ranged = []
    for lbl, amt in items:
        low = round(float(amt) * low_mult / 50) * 50   # round to nearest $50
        high = round(float(amt) * high_mult / 50) * 50
        ranged.append((lbl, low, float(amt), high))
    total_low = sum(r[1] for r in ranged)
    total_high = sum(r[3] for r in ranged)
    return total_low, total_high, ranged


def _top_comps(comps: List[Dict], n: int = 5) -> List[Dict]:
    """Return the top-N comps by sold_price DESC. Skips comps with no price."""
    if not comps:
        return []
    priced = [c for c in comps if c.get("sold_price")]
    priced.sort(key=lambda c: float(c.get("sold_price") or 0), reverse=True)
    return priced[:n]


def _fmt_addr_short(prop: Dict) -> str:
    """Short property line for headlines. Address + city."""
    parts = [prop.get("address", ""), prop.get("city", "")]
    return ", ".join(p for p in parts if p)


def _fmt_money(v) -> str:
    if v is None:
        return "TBD"
    try:
        return f"${float(v):,.0f}"
    except Exception:
        return str(v)


def _email_copy(prop: Dict, rec: Dict, asking: float, arv: float,
                rehab_low: float, rehab_high: float, spread: float) -> str:
    """Pre-written email body with THIS deal's numbers baked in."""
    addr = _fmt_addr_short(prop)
    beds = prop.get("beds", "?")
    baths = prop.get("baths", "?")
    sqft = prop.get("sqft", 0) or 0
    yr = prop.get("year", "?")
    return (
        f"Subject: Cash Deal — {addr} — ARV {_fmt_money(arv)}, "
        f"Asking {_fmt_money(asking)}\n\n"
        f"Hi [Buyer First Name],\n\n"
        f"I've got a live deal I want to run past you before it hits the wider list:\n\n"
        f"  Property: {addr}\n"
        f"  Beds / Baths: {beds} / {baths}   Sqft: {int(sqft):,}   Built: {yr}\n"
        f"  ARV (comps in the {_fmt_money(arv * 0.97)}-{_fmt_money(arv * 1.03)} range): "
        f"{_fmt_money(arv)}\n"
        f"  Estimated rehab: {_fmt_money(rehab_low)}-{_fmt_money(rehab_high)}\n"
        f"  Asking: {_fmt_money(asking)}   →   Est. spread: {_fmt_money(spread)}\n\n"
        f"I've attached the deal sheet with the rehab line items and my 5 highest comps.\n"
        f"House is available for walk-through this week. First one to say YES on price "
        f"gets it — no assignments to piggyback wholesalers.\n\n"
        f"Reply back or text me if you want to run it.\n\n"
        f"Jo Colonna\n"
        f"Exodus Property Solutions\n"
        f"(954) 684-1368"
    )


def _sms_copy(prop: Dict, asking: float, arv: float,
              rehab_low: float, rehab_high: float) -> str:
    """SMS-length pitch (under ~320 chars for a 2-segment message)."""
    addr = _fmt_addr_short(prop)
    return (
        f"[FIRST NAME] — Cash deal in {prop.get('city', 'FL')}: "
        f"{addr}. "
        f"Asking {_fmt_money(asking)}, ARV {_fmt_money(arv)}, "
        f"rehab {_fmt_money(rehab_low)}-{_fmt_money(rehab_high)}. "
        f"Walk-through this week. Interested? — Jo, Exodus"
    )


def _facebook_copy(prop: Dict, rec: Dict, asking: float, arv: float,
                   rehab_low: float, rehab_high: float,
                   spread: float) -> str:
    """Facebook post — investor group friendly. Hook, numbers, CTA."""
    addr = _fmt_addr_short(prop)
    beds = prop.get("beds", "?")
    baths = prop.get("baths", "?")
    sqft = prop.get("sqft", 0) or 0
    return (
        f"🏠 CASH DEAL — {addr}\n\n"
        f"{beds}bed / {baths}bath, {int(sqft):,} sqft. "
        f"Motivated seller under contract.\n\n"
        f"💰 Asking: {_fmt_money(asking)}\n"
        f"📈 ARV: {_fmt_money(arv)}\n"
        f"🔨 Rehab: {_fmt_money(rehab_low)}-{_fmt_money(rehab_high)}\n"
        f"💵 Spread: ~{_fmt_money(spread)}\n\n"
        f"Full comps + rehab breakdown in the deal sheet — DM me and "
        f"I'll send it over. Walk-through open this week to serious "
        f"cash buyers only. No daisy chains, no assignments.\n\n"
        f"— Jo Colonna | Exodus Property Solutions"
    )


def _clean_rehab_label(lbl) -> str:
    """Strip parenthesized unit-cost detail from a rehab item label so we
    don't leak our per-sqft / per-ton / per-bathroom cost structure to cash
    buyers. "Roof (Shingle, 1-story, 1,796 sf x $10.50/sf)" -> "Roof"."""
    if not lbl:
        return "" if lbl is None else str(lbl)
    return str(lbl).split(" (")[0].strip()


def build_dispo_marketing_pdf(
    prop: Dict[str, Any],
    rec: Dict[str, Any],
    inputs: Dict[str, Any],
    rehab_items: Optional[List[Tuple[str, float]]] = None,
    asking_price_override: Optional[float] = None,
    rehab_range_pct: float = DEFAULT_REHAB_RANGE_PCT,
    max_comps: int = 5,
) -> bytes:
    """Build the Dispo Marketing PDF. Returns PDF bytes ready for st.download_button().

    Args:
        prop: property dict (address, city, state, zip, county, beds, baths,
              sqft, year, pool, stories, etc.)
        rec: recommendation dict from compute_recommendation. Used for ARV,
             cash_mao / wholesale_mao, rehab_total, and profit calcs.
        inputs: full deal inputs dict — used to pull "comps" list.
        rehab_items: optional pre-computed itemized rehab list. If omitted,
                     we don't render the itemized breakdown (only the total).
        asking_price_override: what we ask the cash buyer to pay. If None or
                     0, the PDF renders "TBD" in the asking box and skips the
                     spread. Pass a real number to compute spread.
        rehab_range_pct: +/- band around the underwriting estimate. Default
                     0.15 (±15%). Tunable per-deal from the Dispo page.
        max_comps: how many top comps to show (default 5).
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, PageBreak,
                                     KeepTogether)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    # ---- Extract numbers -------------------------------------------------
    arv = float(rec.get("arv", 0) or 0)
    rehab_est = float(rec.get("rehab_total", 0) or 0)
    # Asking can be blank on purpose — the page starts with no value and
    # Jo types it in. If None/0, render "TBD" and skip the spread math.
    asking = None
    if asking_price_override:
        try:
            v = float(asking_price_override)
            if v > 0:
                asking = v
        except Exception:
            asking = None

    # Range around the underwriting rehab estimate
    if rehab_items:
        total_low, total_high, ranged_items = _rehab_range(
            rehab_items, range_pct=rehab_range_pct,
        )
    else:
        low_mult = 1.0 - float(rehab_range_pct)
        high_mult = 1.0 + float(rehab_range_pct)
        total_low = round(rehab_est * low_mult / 100) * 100
        total_high = round(rehab_est * high_mult / 100) * 100
        ranged_items = []

    # Spread = ARV - asking - midpoint of rehab range (only if asking set)
    rehab_mid = (total_low + total_high) / 2
    spread = (arv - asking - rehab_mid) if asking is not None else None

    # Comps
    comps = _top_comps(inputs.get("comps") or [], n=max_comps)

    # ---- Doc setup -------------------------------------------------------
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    brand_blue = colors.HexColor("#1F4E78")
    brand_blue_dark = colors.HexColor("#163A5A")
    accent_green = colors.HexColor("#1E7B3B")
    accent_orange = colors.HexColor("#C77700")
    subtle_gray = colors.HexColor("#666666")
    section_bg = colors.HexColor("#F2F8FF")
    grid_gray = colors.HexColor("#D0D7DE")

    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=18,
        textColor=brand_blue_dark, alignment=TA_CENTER,
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"], fontSize=10,
        textColor=subtle_gray, alignment=TA_CENTER, spaceAfter=10,
        fontName="Helvetica-Oblique",
    )
    banner_style = ParagraphStyle(
        "Banner", parent=styles["Heading2"], fontSize=11,
        textColor=colors.white, alignment=TA_LEFT, fontName="Helvetica-Bold",
        leftIndent=6, backColor=brand_blue,
        borderPadding=6, spaceBefore=12, spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=9.5,
        textColor=colors.HexColor("#222222"), leading=13,
    )
    body_small = ParagraphStyle(
        "BodySmall", parent=styles["Normal"], fontSize=8.5,
        textColor=colors.HexColor("#222222"), leading=11,
    )
    mono_style = ParagraphStyle(
        "Mono", parent=styles["Code"], fontSize=8,
        textColor=colors.HexColor("#222222"), leading=10,
        backColor=colors.HexColor("#F6F8FA"),
        borderPadding=6, borderColor=grid_gray, borderWidth=0.5,
    )

    story = []

    # ---- Title -----------------------------------------------------------
    story.append(Paragraph("DISPO MARKETING SHEET", title_style))
    story.append(Paragraph(
        "Cash-buyer deal sheet + ready-to-copy marketing drafts",
        subtitle_style,
    ))

    # ---- Property snapshot ----------------------------------------------
    addr = prop.get("address", "") or ""
    csz = (
        f"{prop.get('city', '')}, {prop.get('state', '')} "
        f"{prop.get('zip', '')}"
    ).strip().strip(",").strip()

    snap_data = [
        ["Property:", addr, "Beds / Baths:",
         f"{prop.get('beds', '?')} / {prop.get('baths', '?')}"],
        ["", csz, "Sqft:",
         f"{int(prop.get('sqft', 0) or 0):,}"],
        ["County:", prop.get("county", "-") or "-", "Year built:",
         str(prop.get("year", "-") or "-")],
        ["Pool:", prop.get("pool", "No") or "No", "Stories:",
         str(prop.get("stories", 1) or 1)],
        ["Prepared:", datetime.now().strftime("%B %d, %Y"), "", ""],
    ]
    snap_tbl = Table(
        snap_data,
        colWidths=[0.85 * inch, 3.4 * inch, 1.0 * inch, 2.25 * inch],
        style=TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
            ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
            ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9.5),
            ("TEXTCOLOR", (0, 0), (0, -1), brand_blue_dark),
            ("TEXTCOLOR", (2, 0), (2, -1), brand_blue_dark),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ]),
    )
    story.append(snap_tbl)
    story.append(Spacer(1, 0.15 * inch))

    # ---- Deal Math (big numbers) ----------------------------------------
    asking_display = _fmt_money(asking) if asking is not None else "TBD"
    spread_display = _fmt_money(spread) if spread is not None else "TBD"
    range_pct_label = int(round(rehab_range_pct * 100))
    math_data = [[
        Paragraph(
            '<font size="10" color="#666666"><b>ASKING</b></font><br/>'
            f'<font size="20" color="#1F4E78"><b>{asking_display}</b></font><br/>'
            '<font size="8" color="#666666">Your cash-buyer price</font>',
            body_style,
        ),
        Paragraph(
            '<font size="10" color="#666666"><b>ARV</b></font><br/>'
            f'<font size="20" color="#1E7B3B"><b>{_fmt_money(arv)}</b></font><br/>'
            '<font size="8" color="#666666">Highest defensible comps</font>',
            body_style,
        ),
        Paragraph(
            '<font size="10" color="#666666"><b>REHAB (LOW-HIGH)</b></font><br/>'
            f'<font size="14" color="#C77700"><b>{_fmt_money(total_low)}</b></font>'
            '<font size="14" color="#666666"> - </font>'
            f'<font size="14" color="#C77700"><b>{_fmt_money(total_high)}</b></font><br/>'
            f'<font size="8" color="#666666">±{range_pct_label}% band on itemized estimate</font>',
            body_style,
        ),
        Paragraph(
            '<font size="10" color="#666666"><b>SPREAD (mid)</b></font><br/>'
            f'<font size="20" color="#1E7B3B"><b>{spread_display}</b></font><br/>'
            '<font size="8" color="#666666">ARV − Asking − Rehab (mid)</font>',
            body_style,
        ),
    ]]
    math_tbl = Table(
        math_data,
        colWidths=[1.9 * inch, 1.9 * inch, 2.0 * inch, 1.7 * inch],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), section_bg),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LINEBEFORE", (0, 0), (0, -1), 4, brand_blue),
            ("BOX", (0, 0), (-1, -1), 0.5, grid_gray),
        ]),
    )
    story.append(math_tbl)

    # ---- Rehab Breakdown (with LOW-HIGH range) --------------------------
    story.append(Paragraph("REHAB BREAKDOWN (LOW-HIGH RANGE)", banner_style))
    if ranged_items:
        rows = [["Item", "Low", "Est.", "High"]]
        for lbl, low, mid, high in ranged_items:
            rows.append([
                _clean_rehab_label(lbl),
                _fmt_money(low),
                _fmt_money(mid),
                _fmt_money(high),
            ])
        rows.append([
            "TOTAL REHAB (LOW - HIGH)",
            _fmt_money(total_low),
            _fmt_money(rehab_est),
            _fmt_money(total_high),
        ])
        rehab_tbl = Table(
            rows,
            colWidths=[3.6 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch],
            style=TableStyle([
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                # Header row
                ("BACKGROUND", (0, 0), (-1, 0), brand_blue),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("ALIGN", (1, 0), (-1, 0), "RIGHT"),
                # Body
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                # Alternating rows
                ("ROWBACKGROUNDS", (0, 1), (-1, -2),
                 [colors.white, colors.HexColor("#F6F8FA")]),
                # Total row
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF8E1")),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, brand_blue),
                ("TEXTCOLOR", (0, -1), (-1, -1), brand_blue_dark),
            ]),
        )
        story.append(rehab_tbl)
    else:
        story.append(Paragraph(
            f"<i>Underwriting rehab estimate: {_fmt_money(rehab_est)} "
            f"(range {_fmt_money(total_low)}-{_fmt_money(total_high)})</i>",
            body_style,
        ))

    # ---- Top Comps -------------------------------------------------------
    story.append(Paragraph(
        f"TOP {len(comps)} COMPS (highest defensible — supports {_fmt_money(arv)} ARV)",
        banner_style,
    ))
    if comps:
        comp_rows = [["Address", "Beds", "Baths", "Sqft", "Sold For", "Sold Date"]]
        for c in comps:
            comp_rows.append([
                (c.get("address") or "") + (
                    f", {c.get('city', '')}" if c.get("city") else ""
                ),
                str(c.get("beds", "-") or "-"),
                str(c.get("baths", "-") or "-"),
                f"{int(c.get('sqft', 0) or 0):,}" if c.get("sqft") else "-",
                _fmt_money(c.get("sold_price", 0)),
                str(c.get("sold_date", "-") or "-"),
            ])
        comp_tbl = Table(
            comp_rows,
            colWidths=[3.1 * inch, 0.5 * inch, 0.6 * inch, 0.8 * inch,
                       1.0 * inch, 1.0 * inch],
            style=TableStyle([
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("BACKGROUND", (0, 0), (-1, 0), brand_blue),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (1, 1), (3, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#F6F8FA")]),
                ("BOX", (0, 0), (-1, -1), 0.5, grid_gray),
            ]),
        )
        story.append(comp_tbl)
    else:
        story.append(Paragraph(
            "<i>No comps stored on this deal — add comps in New Deal → Comps "
            "section before generating this sheet for a cash buyer.</i>",
            body_style,
        ))

    # ---- Page 2: Marketing Copy -----------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("READY-TO-COPY MARKETING DRAFTS", title_style))
    story.append(Paragraph(
        "Copy any block, paste into email / SMS / Facebook, edit as needed.",
        subtitle_style,
    ))

    story.append(Paragraph("📧 EMAIL DRAFT", banner_style))
    email_body = _email_copy(prop, rec, asking, arv, total_low, total_high, spread)
    email_html = email_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    story.append(Paragraph(email_html, mono_style))

    story.append(Paragraph("📱 SMS DRAFT (~2 segments)", banner_style))
    sms_body = _sms_copy(prop, asking, arv, total_low, total_high)
    story.append(Paragraph(
        sms_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
        mono_style,
    ))
    story.append(Paragraph(
        f"<i>Character count: {len(sms_body)} chars</i>",
        body_small,
    ))

    story.append(Paragraph("💬 FACEBOOK POST DRAFT", banner_style))
    fb_body = _facebook_copy(prop, rec, asking, arv, total_low, total_high, spread)
    fb_html = fb_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    story.append(Paragraph(fb_html, mono_style))

    # ---- Footer ---------------------------------------------------------
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "<i><b>Notes:</b> ARV is based on the highest defensible comps in our "
        "underwriting file. Rehab range is +/-15% around the itemized "
        "underwriting estimate — use the LOW figure for premium buyers, HIGH "
        "for skittish ones. Never share this sheet with a seller; it's "
        "internal + cash-buyer facing only.</i>",
        body_small,
    ))
    story.append(Paragraph(
        f"<i>Prepared by <b>Exodus Property Solutions</b> - "
        f"{datetime.now().strftime('%B %d, %Y at %I:%M %p')}.</i>",
        body_small,
    ))

    doc.build(story)
    return buf.getvalue()
