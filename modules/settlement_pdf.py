"""Preliminary Settlement Statement generator — seller-facing net sheet.

Slim v24.10 build: only the Settlement Statement PDF. The Compare-vs-Realtor
PDF will be added separately after this one is verified working end-to-end.

Generates a professionally-formatted PDF that mirrors what a Realtor's
"net sheet" looks like, so the homeowner sees a familiar document format
and knows exactly what they'll walk away with at closing.

The document shows ONLY the seller's side of closing (no buyer/Exodus
costs). It's meant to be printed and left with the homeowner as a tangible
artifact of transparency.
"""
from __future__ import annotations
from io import BytesIO
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


# Which FL counties customarily have the BUYER pay the owner's title
# policy at closing. Everywhere else, the seller pays it.
BUYER_PAYS_TITLE_COUNTIES = {"Broward", "Miami-Dade", "Sarasota", "Collier"}


def _seller_pays_owner_title(county: str) -> bool:
    """True if the seller customarily pays the owner's title policy."""
    county_norm = (county or "").strip()
    if not county_norm:
        return True  # conservative default: assume seller pays
    return county_norm not in BUYER_PAYS_TITLE_COUNTIES


def compute_seller_net_sheet(
    sale_price: float,
    county: str,
    has_hoa: bool,
    mortgage1_payoff: float = 0,
    mortgage2_payoff: float = 0,
    other_liens: float = 0,
    annual_property_taxes: float = 0,
    hoa_monthly: float = 0,
    closing_date: Optional[datetime] = None,
    buyer_pays_seller_closings: bool = False,
    utility_owed_estimate: float = 0,
    seller_uses_agent: bool = False,
    commission_pct: float = 0.06,
) -> Dict[str, Any]:
    """Compute every line item on the seller's side of closing."""
    if closing_date is None:
        closing_date = datetime.now() + timedelta(days=30)

    # Section 1: Seller's Closing Costs
    closing_costs = []
    if buyer_pays_seller_closings:
        closing_costs.append(("Deed Documentary Stamps (Exodus covers)", 0))
        if _seller_pays_owner_title(county):
            closing_costs.append(("Owner's Title Insurance (Exodus covers)", 0))
        closing_costs.append(("Title Search Fee (Exodus covers)", 0))
        closing_costs.append(("Settlement / Escrow Fee (Exodus covers)", 0))
        closing_costs.append(("Municipal Lien Search (Exodus covers)", 0))
        if has_hoa:
            closing_costs.append(("HOA Estoppel Fee (Exodus covers)", 0))
        closing_costs.append(("Recording Fees (Exodus covers)", 0))
    else:
        doc_stamps = sale_price * 0.007
        closing_costs.append(
            (f"Deed Documentary Stamps (0.70% of ${sale_price:,.0f})",
             doc_stamps)
        )
        if _seller_pays_owner_title(county):
            owner_title = sale_price * 0.004
            closing_costs.append(
                (f"Owner's Title Insurance Policy (~0.40% of ${sale_price:,.0f})",
                 owner_title)
            )
        else:
            closing_costs.append(
                (f"Owner's Title Insurance (buyer pays in {county})", 0)
            )
        closing_costs.append(("Title Search Fee", 200))
        closing_costs.append(("Settlement / Escrow Fee", 600))
        closing_costs.append(("Municipal Lien Search", 500))
        if has_hoa:
            closing_costs.append(("HOA Estoppel Fee", 500))
        closing_costs.append(("Recording Fees", 150))

    commission_amt = 0
    if seller_uses_agent:
        commission_amt = sale_price * commission_pct
        closing_costs.append(
            (f"Realtor Commission ({commission_pct*100:.1f}% of ${sale_price:,.0f})",
             commission_amt)
        )

    closing_costs_total = sum(a for _, a in closing_costs)

    # Section 2: Payoffs
    payoffs = []
    if mortgage1_payoff and mortgage1_payoff > 0:
        payoffs.append(("1st Mortgage Payoff (est.)", mortgage1_payoff))
    if mortgage2_payoff and mortgage2_payoff > 0:
        payoffs.append(("2nd Mortgage / HELOC Payoff (est.)", mortgage2_payoff))
    if other_liens and other_liens > 0:
        payoffs.append(("Other Liens (tax, code, mechanic — est.)", other_liens))
    payoffs_total = sum(a for _, a in payoffs)

    # Section 3: Prorations
    prorations = []
    if annual_property_taxes and annual_property_taxes > 0:
        year_start = datetime(closing_date.year, 1, 1)
        days_owned = max(0, (closing_date - year_start).days + 1)
        tax_proration = annual_property_taxes * (days_owned / 365.0)
        prorations.append(
            (f"Property Taxes (Jan 1 to closing, {days_owned} days of "
             f"${annual_property_taxes:,.0f}/yr)", tax_proration)
        )
    if hoa_monthly and hoa_monthly > 0:
        day_of_month = closing_date.day
        hoa_proration = hoa_monthly * (day_of_month / 30.0)
        prorations.append(
            (f"HOA Dues (partial month, {day_of_month} days of "
             f"${hoa_monthly:,.0f}/mo)", hoa_proration)
        )
    if utility_owed_estimate and utility_owed_estimate > 0:
        prorations.append(
            ("Utilities / Water Owed (est.)", utility_owed_estimate)
        )
    prorations_total = sum(a for _, a in prorations)

    total_deductions = closing_costs_total + payoffs_total + prorations_total
    estimated_net = sale_price - total_deductions

    return {
        "sale_price": sale_price,
        "closing_costs": closing_costs,
        "closing_costs_total": closing_costs_total,
        "payoffs": payoffs,
        "payoffs_total": payoffs_total,
        "prorations": prorations,
        "prorations_total": prorations_total,
        "total_deductions": total_deductions,
        "estimated_net_to_seller": estimated_net,
        "closing_date": closing_date,
        "buyer_covers_costs": buyer_pays_seller_closings,
        "county": county,
        "commission_amount": commission_amt,
    }


def build_settlement_pdf(
    prop: Dict[str, Any],
    rec: Dict[str, Any],
    seller: Dict[str, Any],
    buyer_entity_name: str = "Exodus Property Solutions",
    seller_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    sale_price_override: Optional[float] = None,
    closing_date: Optional[datetime] = None,
    seller_uses_agent: bool = False,
    commission_pct: float = 0.06,
) -> bytes:
    """Build the seller-facing preliminary settlement statement PDF."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.5*inch, bottomMargin=0.5*inch,
    )

    # Determine sale price
    if sale_price_override is not None:
        sale_price = float(sale_price_override)
    else:
        sale_price = float(
            rec.get("cash_offer_to_seller")
            or rec.get("cash_offer", 0)
            or 0
        )

    # Determine closing date
    if closing_date is None:
        timeline_days = int(seller.get("timeline_days", 30) or 30)
        closing_date = datetime.now() + timedelta(days=timeline_days)

    # Compute the numbers
    net_sheet = compute_seller_net_sheet(
        sale_price=sale_price,
        county=prop.get("county", "") or "",
        has_hoa=bool(prop.get("hoa", 0) and prop.get("hoa", 0) > 0),
        mortgage1_payoff=float(seller.get("mtg1", 0) or 0),
        mortgage2_payoff=float(seller.get("mtg2", 0) or 0),
        other_liens=float(seller.get("other_liens", 0) or 0),
        annual_property_taxes=float(prop.get("annual_taxes", 0) or 0),
        hoa_monthly=float(prop.get("hoa", 0) or 0),
        closing_date=closing_date,
        buyer_pays_seller_closings=bool(prop.get("buyer_pays_seller_closings")),
        utility_owed_estimate=0,
        seller_uses_agent=seller_uses_agent,
        commission_pct=commission_pct,
    )

    # Styles
    styles = getSampleStyleSheet()
    brand_blue = colors.HexColor("#1F4E78")
    brand_blue_dark = colors.HexColor("#163A5A")
    section_bg = colors.HexColor("#F2F8FF")
    grid_gray = colors.HexColor("#B7C7D9")
    positive_green = colors.HexColor("#1E7B3B")
    subtle_gray = colors.HexColor("#666666")

    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=17,
        textColor=brand_blue_dark, alignment=TA_CENTER,
        spaceBefore=0, spaceAfter=2, fontName="Helvetica-Bold",
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"], fontSize=10,
        textColor=subtle_gray, alignment=TA_CENTER, spaceAfter=14,
        fontName="Helvetica-Oblique",
    )
    header_style = ParagraphStyle(
        "Header", parent=styles["Heading2"], fontSize=11,
        textColor=colors.white, alignment=TA_LEFT,
        fontName="Helvetica-Bold",
        leftIndent=6, backColor=brand_blue,
        borderPadding=6, spaceBefore=8, spaceAfter=4,
    )
    body_normal = ParagraphStyle(
        "BodyNormal", parent=styles["Normal"], fontSize=9.5,
        textColor=colors.HexColor("#222222"), leading=13,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer", parent=styles["Normal"], fontSize=8,
        textColor=subtle_gray, leading=11, alignment=TA_LEFT,
        spaceBefore=8, fontName="Helvetica-Oblique",
    )

    story = []

    # Title
    story.append(Paragraph("PRELIMINARY SETTLEMENT STATEMENT", title_style))
    story.append(Paragraph(
        "Seller's Estimated Net at Closing", subtitle_style
    ))

    # Property / Party Header
    _seller_name = (
        seller_name
        or seller.get("name")
        or seller.get("seller_party_name")
        or "Homeowner"
    )
    addr_full = prop.get("address", "") or ""
    city_state_zip = (
        f"{prop.get('city', '')}, {prop.get('state', '')} "
        f"{prop.get('zip', '')}"
    ).strip().strip(",").strip()

    header_data = [
        ["Property:", addr_full],
        ["", city_state_zip],
        ["County:", prop.get("county", "-") or "-"],
        ["Seller:", _seller_name],
        ["Buyer:", buyer_entity_name],
        ["Prepared:", datetime.now().strftime("%B %d, %Y")],
        ["Est. Closing:", closing_date.strftime("%B %d, %Y")],
    ]
    header_tbl = Table(
        header_data, colWidths=[1.4*inch, 5.5*inch],
        style=TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
            ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
            ("TEXTCOLOR", (0, 0), (0, -1), brand_blue_dark),
            ("TEXTCOLOR", (1, 0), (-1, -1), colors.HexColor("#222222")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ]),
    )
    story.append(header_tbl)
    story.append(Spacer(1, 0.15*inch))

    # Sale Price Banner
    sale_data = [[
        Paragraph(
            '<font size="10" color="#666666"><b>SALE PRICE</b></font><br/>'
            '<font size="9" color="#666666">'
            'Amount paid to seller by buyer at closing</font>',
            body_normal,
        ),
        Paragraph(
            f'<font size="22" color="#1F4E78"><b>'
            f'${net_sheet["sale_price"]:,.0f}</b></font>',
            body_normal,
        ),
    ]]
    sale_tbl = Table(
        sale_data, colWidths=[4.5*inch, 2.4*inch],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), section_bg),
            ("LINEBEFORE", (0, 0), (0, -1), 4, brand_blue),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]),
    )
    story.append(sale_tbl)
    story.append(Spacer(1, 0.15*inch))

    # Helper to render a section
    def render_section(banner_label, items, subtotal_amount, subtotal_label,
                       banner_note=""):
        flows = []
        banner_html = f"<b>{banner_label}</b>"
        if banner_note:
            banner_html += (
                f'&nbsp;&nbsp;<font size="8">- {banner_note}</font>'
            )
        flows.append(Paragraph(banner_html, header_style))
        if not items:
            flows.append(Paragraph("<i>(none applicable)</i>", body_normal))
        else:
            rows = []
            for label, amt in items:
                rows.append([label, f"(${amt:,.2f})"])
            rows.append(["", ""])
            rows.append([
                f"Subtotal - {subtotal_label}",
                f"(${subtotal_amount:,.2f})"
            ])
            tbl = Table(
                rows, colWidths=[5.0*inch, 1.9*inch],
                style=TableStyle([
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
                    ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222222")),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.5, grid_gray),
                ]),
            )
            flows.append(tbl)
        return flows

    # Section 1: Closing Costs
    banner_note = ""
    if net_sheet["buyer_covers_costs"]:
        banner_note = "Exodus is covering the seller's closing costs"
    for f in render_section(
        "SELLER'S CLOSING COSTS",
        net_sheet["closing_costs"],
        net_sheet["closing_costs_total"],
        "Closing Costs",
        banner_note=banner_note,
    ):
        story.append(f)

    # Section 2: Payoffs
    for f in render_section(
        "SELLER'S PAYOFFS (mortgages, liens)",
        net_sheet["payoffs"],
        net_sheet["payoffs_total"],
        "Payoffs",
    ):
        story.append(f)

    # Section 3: Prorations
    for f in render_section(
        f"PRORATIONS AS OF {closing_date.strftime('%B %d, %Y')}",
        net_sheet["prorations"],
        net_sheet["prorations_total"],
        "Prorations",
    ):
        story.append(f)

    # Bottom Line
    story.append(Spacer(1, 0.15*inch))

    net_amt = net_sheet["estimated_net_to_seller"]
    net_color = positive_green if net_amt >= 0 else colors.HexColor("#B91C1C")
    net_data = [[
        Paragraph(
            '<font size="10" color="#666666"><b>ESTIMATED NET TO SELLER</b></font><br/>'
            '<font size="9" color="#666666">'
            'Sale Price minus Closing Costs minus Payoffs minus Prorations</font>',
            body_normal,
        ),
        Paragraph(
            f'<font size="22" color="{net_color.hexval()}"><b>'
            f'${net_amt:,.0f}</b></font>',
            body_normal,
        ),
    ]]
    net_tbl = Table(
        net_data, colWidths=[4.5*inch, 2.4*inch],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), section_bg),
            ("LINEBEFORE", (0, 0), (0, -1), 4, net_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]),
    )
    story.append(net_tbl)

    # Reconciliation
    story.append(Spacer(1, 0.10*inch))
    recon_txt = (
        f"<b>Reconciliation:</b> "
        f"Sale Price ${net_sheet['sale_price']:,.0f} "
        f"minus Closing Costs ${net_sheet['closing_costs_total']:,.0f} "
        f"minus Payoffs ${net_sheet['payoffs_total']:,.0f} "
        f"minus Prorations ${net_sheet['prorations_total']:,.0f} "
        f"= <b>${net_amt:,.0f}</b>"
    )
    story.append(Paragraph(recon_txt, body_normal))

    # Disclaimer
    story.append(Spacer(1, 0.20*inch))
    disclaimer = (
        "<b>DISCLAIMER:</b> This is a <b>good-faith estimate</b> based on "
        "information provided by the seller and typical Florida closing "
        "customs. Mortgage payoff figures are estimates and will be "
        "finalized by the lender at closing. Final settlement figures will "
        "be determined by the title company and reflected in the official "
        "ALTA Settlement Statement provided at closing."
    )
    story.append(Paragraph(disclaimer, disclaimer_style))

    # Footer
    footer_prep = prepared_by or "Exodus Property Solutions"
    footer_txt = (
        f'<font color="#666666" size="8">'
        f'Prepared by <b>{footer_prep}</b> - '
        f'{datetime.now().strftime("%B %d, %Y at %I:%M %p")}'
        f'</font>'
    )
    story.append(Spacer(1, 0.12*inch))
    story.append(Paragraph(footer_txt, disclaimer_style))

    doc.build(story)
    return buf.getvalue()
