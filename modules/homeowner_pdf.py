"""Generate a polished, leave-behind PDF of the Homeowner Presentation.

Mirrors pages/6_Homeowner_Presentation.py — five sections that walk the
seller from ARV → renovation cost → our deal costs → our minimum profit →
the cash offer — all rendered with print-quality typography and color-coded
section banners so the document feels like a real artifact, not a screenshot
of a web app.

Designed to print on standard Letter and reconcile back to ARV exactly so
when the homeowner asks "where did the rest go?" every dollar is on the page.
"""
from __future__ import annotations
from io import BytesIO
from datetime import datetime
from typing import Any, Dict, List


def build_homeowner_pdf(
    prop: Dict[str, Any],
    rec: Dict[str, Any],
    inputs: Dict[str, Any],
    rehab_items: List[tuple],
) -> bytes:
    """Build the homeowner-facing PDF and return its bytes.

    Args:
        prop: property dict (address, beds, baths, sqft, year, etc.)
        rec: recommendation dict — MUST be from compute_recommendation with
            force_strategy="Rehab" so the rehab-math fields are present.
        inputs: full saved deal inputs (used for comps round-trip).
        rehab_items: pre-computed list of (label, dollar_amount) tuples
            from rehab_breakdown() — pass these in so the PDF reflects
            exactly what the rep saw on the New Deal page.
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
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.55*inch, rightMargin=0.55*inch,
        topMargin=0.45*inch, bottomMargin=0.45*inch,
    )

    # --- Pull the numbers (Rehab strategy ceilings + clamped offer) -----
    arv = float(rec.get("arv", 0) or 0)
    rehab_total = float(rec.get("rehab_total", 0) or 0)
    purchase_closing = float(rec.get("purchase_closing_costs", 0) or 0)
    sale_closing = float(rec.get("sale_closing_costs", 0) or 0)
    holding = float(rec.get("total_holding", 0) or 0)
    cost_of_money = float(rec.get("cost_of_money", 0) or 0)
    our_costs = purchase_closing + sale_closing + holding + cost_of_money

    cash_offer = float(
        rec.get("cash_offer_to_seller")
        or rec.get("cash_offer", 0)
        or 0
    )
    # Min profit reconciles to ARV exactly: ARV − rehab − our_costs − offer
    min_profit = arv - rehab_total - our_costs - cash_offer
    if min_profit < 0:
        min_profit = float(
            rec.get("net_profit_at_mao") or rec.get("net_profit", 0) or 0
        )

    rehab_sub = float(rec.get("rehab_subtotal", 0) or 0)
    contingency = rehab_total - rehab_sub
    contingency_label = "Contingency (10%)" if rehab_sub > 50_000 else "Contingency ($5,000)"

    # --- Styles ---------------------------------------------------------
    styles = getSampleStyleSheet()

    brand_blue = colors.HexColor("#1F4E78")
    brand_blue_dark = colors.HexColor("#163A5A")
    blue_bg = colors.HexColor("#F2F8FF")
    blue_border = colors.HexColor("#1F4E78")
    orange_bg = colors.HexColor("#FFF8F0")
    orange_border = colors.HexColor("#C77")
    orange_text = colors.HexColor("#8B4513")
    gold_bg = colors.HexColor("#FFF9E6")
    gold_border = colors.HexColor("#C9A227")
    gold_text = colors.HexColor("#9A7B1A")
    green_bg = colors.HexColor("#F0F8F2")
    green_border = colors.HexColor("#2E7D32")
    green_text = colors.HexColor("#2E7D32")
    text_grey = colors.HexColor("#444444")
    light_grey = colors.HexColor("#999999")

    brand_kicker = ParagraphStyle(
        "BrandKicker", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=10, textColor=colors.HexColor("#666666"),
        alignment=TA_CENTER, spaceAfter=2,
    )
    page_title = ParagraphStyle(
        "PageTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=22, textColor=brand_blue, alignment=TA_CENTER, spaceAfter=4,
    )
    page_sub = ParagraphStyle(
        "PageSub", parent=styles["Normal"], fontSize=11,
        textColor=colors.HexColor("#555555"), alignment=TA_CENTER, spaceAfter=10,
    )
    intro = ParagraphStyle(
        "Intro", parent=styles["Normal"], fontSize=10,
        textColor=text_grey, alignment=TA_LEFT, spaceAfter=8, leading=13,
        italic=True,
    )
    section_h = ParagraphStyle(
        "SectionH", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, textColor=brand_blue, spaceBefore=8, spaceAfter=4,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10,
        textColor=colors.HexColor("#222222"), spaceAfter=4, leading=13,
    )
    caption = ParagraphStyle(
        "Caption", parent=styles["Normal"], fontSize=9,
        textColor=colors.HexColor("#666666"), spaceAfter=4, leading=12,
    )
    table_header = ParagraphStyle(
        "TblH", parent=body, fontName="Helvetica-Bold", textColor=colors.white,
    )
    table_cell = ParagraphStyle(
        "TblC", parent=body, fontSize=9, leading=11,
    )

    # ------------------------------------------------------------------
    # Helper — render a colored section block: [Big number tile][explanation]
    # ------------------------------------------------------------------
    def section_block(
        kicker: str,
        amount: float,
        amount_color,
        explanation_html: str,
        bg_color,
        border_color,
    ):
        """Return a 2-column Table: colored tile (left) + explanation (right)."""
        kicker_p = Paragraph(
            f"<font color='#666666' size=8><b>{kicker}</b></font>",
            ParagraphStyle("Kk", parent=body, alignment=TA_CENTER),
        )
        amount_p = Paragraph(
            f"<font color='{amount_color.hexval()}' size=28><b>${amount:,.0f}</b></font>",
            ParagraphStyle("Amt", parent=body, alignment=TA_CENTER, leading=32),
        )
        # Stack kicker + amount in a mini-table so they share the colored block
        tile_inner = Table(
            [[kicker_p], [amount_p]],
            colWidths=[2.4*inch],
        )
        tile_inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), bg_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBEFORE", (0, 0), (0, -1), 5, border_color),
        ]))
        # Outer two-column table: tile + explanation
        explanation_p = Paragraph(explanation_html, body)
        outer = Table(
            [[tile_inner, explanation_p]],
            colWidths=[2.6*inch, 4.6*inch],
        )
        outer.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (1, 0), (1, 0), 14),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return outer

    # ------------------------------------------------------------------
    # Build the document
    # ------------------------------------------------------------------
    story: List[Any] = []

    # --- Header ---
    addr = prop.get("address", "your property")
    city_state = ", ".join(filter(None, [prop.get("city", ""),
                                          prop.get("state", "")]))
    loc_line = f"{addr}" + (f" — {city_state}" if city_state else "")
    today = datetime.now().strftime("%B %d, %Y")

    story.append(Paragraph("EXODUS PROPERTY SOLUTIONS", brand_kicker))
    story.append(Paragraph("🏠 Your Cash Offer — How We Got Here", page_title))
    story.append(Paragraph(f"{loc_line}  ·  {today}", page_sub))
    story.append(Paragraph(
        "We want to show you our exact math — from start to finish. No black "
        "box, no surprises. Every dollar that goes into our offer is on this "
        "page.",
        intro,
    ))
    story.append(Spacer(1, 6))

    # --- Section 1: After-Repair Value ---
    story.append(Paragraph(
        "1. What your house will be worth after improvements", section_h,
    ))
    story.append(section_block(
        "AFTER-REPAIR VALUE",
        arv,
        brand_blue,
        f"This is what we expect a fully renovated "
        f"<b>{prop.get('beds', '—')} bed / {prop.get('baths', '—')} bath</b> "
        f"home of your size ({prop.get('sqft', 0):,} sqft, built "
        f"{prop.get('year', '—')}) to sell for in your neighborhood, based on "
        "the most recent comparable sales we could find. The actual sale-price "
        "evidence is below.",
        blue_bg, blue_border,
    ))
    story.append(Spacer(1, 6))

    # Comps table
    comps_data = inputs.get("comps") or []
    if comps_data:
        # Only "use" = True comps
        used_comps = [c for c in comps_data
                      if c.get("use", True) in (True, "True", "true", 1)]
        if used_comps:
            header_row = [
                Paragraph("<b>Address</b>", table_header),
                Paragraph("<b>City</b>", table_header),
                Paragraph("<b>Sqft</b>", table_header),
                Paragraph("<b>Beds/Baths</b>", table_header),
                Paragraph("<b>Year</b>", table_header),
                Paragraph("<b>Sold For</b>", table_header),
                Paragraph("<b>Sold Date</b>", table_header),
            ]
            rows = [header_row]
            for c in used_comps[:8]:  # cap at 8 to keep the page tidy
                rows.append([
                    Paragraph(str(c.get("address", "—") or "—"), table_cell),
                    Paragraph(str(c.get("city", "") or ""), table_cell),
                    Paragraph(
                        f"{int(c['sqft']):,}" if c.get("sqft") else "—",
                        table_cell,
                    ),
                    Paragraph(
                        f"{c.get('beds', '—')}/{c.get('baths', '—')}",
                        table_cell,
                    ),
                    Paragraph(str(c.get("year", "—") or "—"), table_cell),
                    Paragraph(
                        f"${float(c['sold_price']):,.0f}"
                        if c.get("sold_price") else "—",
                        table_cell,
                    ),
                    Paragraph(
                        str(c.get("sold_date", "") or "—")[:10],
                        table_cell,
                    ),
                ])
            comps_table = Table(
                rows,
                colWidths=[1.8*inch, 1.0*inch, 0.55*inch, 0.65*inch,
                           0.55*inch, 0.85*inch, 0.85*inch],
            )
            comps_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), brand_blue),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, light_grey),
                ("BOX", (0, 0), (-1, -1), 0.6, brand_blue_dark),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(Paragraph(
                "<b>Recent comparable sales we used to estimate your value:</b>",
                caption,
            ))
            story.append(comps_table)
    story.append(Spacer(1, 10))

    # --- Section 2: Renovation Cost ---
    story.append(Paragraph(
        "2. What it'll cost to bring it to top condition", section_h,
    ))
    story.append(section_block(
        "ESTIMATED RENOVATION COST",
        rehab_total,
        orange_text,
        "This is what we estimate it will cost to bring your home to the "
        "condition of the homes you just saw. Every line item is below — "
        "nothing inflated, nothing hidden.",
        orange_bg, orange_border,
    ))
    story.append(Spacer(1, 6))

    # Rehab items table
    if rehab_items:
        header_row = [
            Paragraph("<b>Item</b>", table_header),
            Paragraph("<b>Cost</b>",
                       ParagraphStyle("TblHr", parent=table_header,
                                       alignment=TA_LEFT)),
        ]
        rows = [header_row]
        for label, amount in rehab_items:
            rows.append([
                Paragraph(str(label), table_cell),
                Paragraph(f"${float(amount):,.0f}",
                          ParagraphStyle("RC", parent=table_cell,
                                          alignment=TA_LEFT)),
            ])
        # Subtotal / contingency / total footer
        bold_cell = ParagraphStyle("Bld", parent=table_cell,
                                    fontName="Helvetica-Bold")
        rows.append([
            Paragraph("<b>Subtotal</b>", bold_cell),
            Paragraph(f"<b>${rehab_sub:,.0f}</b>", bold_cell),
        ])
        rows.append([
            Paragraph(contingency_label, table_cell),
            Paragraph(f"${contingency:,.0f}", table_cell),
        ])
        rows.append([
            Paragraph("<b>TOTAL RENOVATION</b>", bold_cell),
            Paragraph(f"<b>${rehab_total:,.0f}</b>", bold_cell),
        ])
        rehab_table = Table(
            rows,
            colWidths=[5.2*inch, 1.85*inch],
        )
        rehab_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), orange_text),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, -3), (-1, -3), colors.HexColor("#F8F8F8")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F2F2F2")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, light_grey),
            ("BOX", (0, 0), (-1, -1), 0.6, orange_text),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(rehab_table)
    story.append(Spacer(1, 10))

    # --- Section 3: Our Cost of Doing the Deal ---
    story.append(Paragraph("3. Our cost of doing the deal", section_h))
    story.append(section_block(
        "OUR DEAL COSTS",
        our_costs,
        gold_text,
        "These are the costs of being the buyer: closing costs when we buy "
        "from you, holding the property while we renovate (insurance, taxes, "
        "utilities, financing), and then closing again when we sell.",
        gold_bg, gold_border,
    ))
    story.append(Spacer(1, 6))

    # Breakdown table
    cost_rows = [
        [Paragraph("<b>What it covers</b>", table_header),
         Paragraph("<b>Amount</b>", table_header)]
    ]
    if purchase_closing > 0:
        cost_rows.append([
            Paragraph("Closing costs when we buy from you", table_cell),
            Paragraph(f"${purchase_closing:,.0f}", table_cell),
        ])
    if holding > 0:
        cost_rows.append([
            Paragraph(
                "Holding the property (~6 months — insurance, taxes, utilities)",
                table_cell,
            ),
            Paragraph(f"${holding:,.0f}", table_cell),
        ])
    if cost_of_money > 0:
        cost_rows.append([
            Paragraph("Financing costs (loan interest + fees)", table_cell),
            Paragraph(f"${cost_of_money:,.0f}", table_cell),
        ])
    if sale_closing > 0:
        cost_rows.append([
            Paragraph(
                "Closing costs + agent commissions when we sell",
                table_cell,
            ),
            Paragraph(f"${sale_closing:,.0f}", table_cell),
        ])
    if len(cost_rows) > 1:
        cost_table = Table(cost_rows, colWidths=[5.2*inch, 1.85*inch])
        cost_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), gold_text),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, light_grey),
            ("BOX", (0, 0), (-1, -1), 0.6, gold_text),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(cost_table)
    story.append(Spacer(1, 10))

    # --- Section 4: Our Minimum Profit (Jo's exact wording) ---
    story.append(Paragraph("4. Our minimum profit", section_h))
    story.append(section_block(
        "MINIMUM PROFIT",
        min_profit,
        green_text,
        "We want to create a win-win — those are the best deals. You walk "
        "away knowing exactly what you're going to make and that we've been "
        "transparent with you, with the understanding that we have to make "
        "a profit to keep this business running. <b>This is the minimum "
        "profit we require to make this deal happen.</b>",
        green_bg, green_border,
    ))
    story.append(Spacer(1, 14))

    # --- Section 5: Your Cash Offer (hero banner) ---
    story.append(Paragraph("5. Your cash offer", section_h))
    hero_top = Paragraph(
        f"<font color='#B8D8EB' size=10><b>YOUR HIGHEST CASH OFFER</b></font>",
        ParagraphStyle("HT", parent=body, alignment=TA_CENTER, spaceAfter=4),
    )
    hero_amt = Paragraph(
        f"<font color='white' size=46><b>${cash_offer:,.0f}</b></font>",
        ParagraphStyle("HA", parent=body, alignment=TA_CENTER, leading=52,
                        spaceAfter=4),
    )
    hero_sub = Paragraph(
        f"<font color='#B8D8EB' size=10>Cash. As-is. No repairs. No "
        f"commissions. Close on your timeline.</font>",
        ParagraphStyle("HS", parent=body, alignment=TA_CENTER, spaceAfter=0),
    )
    hero_table = Table(
        [[hero_top], [hero_amt], [hero_sub]],
        colWidths=[7.0*inch],
    )
    hero_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), brand_blue),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 18),
        ("RIGHTPADDING", (0, 0), (-1, -1), 18),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("TOPPADDING", (0, 1), (0, 1), 4),
        ("TOPPADDING", (0, 2), (0, 2), 4),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
    ]))
    story.append(hero_table)
    story.append(Spacer(1, 8))

    # Math reconciliation line — every dollar accounted for
    total_check = cash_offer + rehab_total + our_costs + min_profit
    delta = arv - total_check
    math_line = (
        f"<b>The math:</b> ${arv:,.0f} (after-repair value) "
        f"− ${rehab_total:,.0f} (renovation) "
        f"− ${our_costs:,.0f} (our deal costs) "
        f"− ${min_profit:,.0f} (our minimum profit) "
        f"= <b>${cash_offer:,.0f}</b> (your cash offer)."
    )
    if abs(delta) > 50:
        math_line += f"  <i>(Rounding: ${delta:,.0f})</i>"
    story.append(Paragraph(math_line, caption))

    # --- Footer ---
    story.append(Spacer(1, 12))
    footer_line = Paragraph(
        "<b>What you get:</b> cash, certainty, and a closing date you "
        "choose. We pay the closing costs. You sell as-is — no cleaning, "
        "no repairs, no showings, no realtor commissions.",
        body,
    )
    story.append(footer_line)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<font color='#666666' size=9><b>Exodus Property Solutions</b></font>",
        ParagraphStyle("Brand", parent=body, alignment=TA_CENTER, spaceAfter=0),
    ))

    doc.build(story)
    return buf.getvalue()
