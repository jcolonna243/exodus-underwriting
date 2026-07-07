"""Preliminary Settlement Statement generator — seller-facing net sheet.

Generates a professionally-formatted PDF that mirrors what a Realtor's
"net sheet" looks like, so the homeowner sees a familiar document format
and knows exactly what they'll walk away with at closing.

The document shows ONLY the seller's side of closing (no buyer/Exodus
costs). It's meant to be printed and left with the homeowner as a tangible
artifact of transparency, and to build trust by pre-empting the "hidden
fees" objection.

Numbers are computed off the same v24 closing cost model used in the
underwriting engine (modules/strategy.compute_bc_closing / county rules),
plus prorations based on the estimated closing date.
"""
from __future__ import annotations
from io import BytesIO
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


# Which FL counties customarily have the BUYER pay the owner's title
# policy at closing. Everywhere else, the seller pays it. Matches the
# same list used in modules/strategy._seller_pays_owner_title().
BUYER_PAYS_TITLE_COUNTIES = {"Broward", "Miami-Dade", "Sarasota", "Collier"}


def _seller_pays_owner_title(county: str) -> bool:
    """True if the seller (homeowner) customarily pays the owner's title
    policy in this county. Same rule as strategy._seller_pays_owner_title
    but duplicated here so this module has no cross-dependency."""
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
    """Compute every line item on the seller's side of closing.

    Returns a dict with:
        sale_price: gross sale price (what buyer is paying seller)
        closing_costs: list of (label, amount) tuples for closing fees
        payoffs: list of (label, amount) for mortgage/lien payoffs
        prorations: list of (label, amount) for prorated items
        closing_costs_total / payoffs_total / prorations_total: subtotals
        estimated_net_to_seller: bottom line
        closing_date: date used for prorations (either passed in or +30 days)
        buyer_covers_costs: True if this seller pays nothing at closing
    """
    if closing_date is None:
        closing_date = datetime.now() + timedelta(days=30)

    # ---- Section 1: Seller's Closing Costs ----------------------------
    closing_costs = []
    if buyer_pays_seller_closings:
        # Exodus is picking up the entire tab — show $0 with a note.
        closing_costs.append(
            ("Deed Documentary Stamps (0.70% — Exodus covers)", 0)
        )
        if _seller_pays_owner_title(county):
            closing_costs.append(
                ("Owner's Title Insurance (Exodus covers)", 0)
            )
        closing_costs.append(("Title Search Fee (Exodus covers)", 0))
        closing_costs.append(("Settlement / Escrow Fee (Exodus covers)", 0))
        closing_costs.append(("Municipal Lien Search (Exodus covers)", 0))
        if has_hoa:
            closing_costs.append(("HOA Estoppel Fee (Exodus covers)", 0))
        closing_costs.append(("Recording Fees (Exodus covers)", 0))
    else:
        # Standard: seller pays their side
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
            # Buyer-pays county — mention that seller isn't charged
            closing_costs.append(
                (f"Owner's Title Insurance (buyer pays in {county})", 0)
            )

        closing_costs.append(("Title Search Fee", 200))
        closing_costs.append(("Settlement / Escrow Fee", 600))
        closing_costs.append(("Municipal Lien Search", 500))
        if has_hoa:
            closing_costs.append(("HOA Estoppel Fee", 500))
        closing_costs.append(("Recording Fees", 150))

    # If seller is represented by an agent, add commission
    commission_amt = 0
    if seller_uses_agent:
        commission_amt = sale_price * commission_pct
        closing_costs.append(
            (f"Realtor Commission ({commission_pct*100:.1f}% of ${sale_price:,.0f})",
             commission_amt)
        )

    closing_costs_total = sum(a for _, a in closing_costs)

    # ---- Section 2: Payoffs -------------------------------------------
    payoffs = []
    if mortgage1_payoff and mortgage1_payoff > 0:
        payoffs.append(("1st Mortgage Payoff (est.)", mortgage1_payoff))
    if mortgage2_payoff and mortgage2_payoff > 0:
        payoffs.append(("2nd Mortgage / HELOC Payoff (est.)", mortgage2_payoff))
    if other_liens and other_liens > 0:
        payoffs.append(
            ("Other Liens (tax, code, mechanic — est.)", other_liens)
        )
    payoffs_total = sum(a for _, a in payoffs)

    # ---- Section 3: Prorations ----------------------------------------
    # Property tax proration: seller owes for the portion of the year they
    # owned the property (Jan 1 through closing date).
    prorations = []
    if annual_property_taxes and annual_property_taxes > 0:
        # Days from Jan 1 to closing (inclusive)
        year_start = datetime(closing_date.year, 1, 1)
        days_owned = max(0, (closing_date - year_start).days + 1)
        # Prorated share of the annual tax bill
        tax_proration = annual_property_taxes * (days_owned / 365.0)
        prorations.append(
            (f"Property Taxes (Jan 1 → closing, {days_owned} days of "
             f"${annual_property_taxes:,.0f}/yr)", tax_proration)
        )

    # HOA proration: partial-month for the days in the month before closing
    if hoa_monthly and hoa_monthly > 0:
        # Prorate for days of the closing month the seller still owned
        day_of_month = closing_date.day
        # Assume a 30-day month for simplicity
        hoa_proration = hoa_monthly * (day_of_month / 30.0)
        prorations.append(
            (f"HOA Dues (partial month — {day_of_month} days of "
             f"${hoa_monthly:,.0f}/mo)", hoa_proration)
        )

    # Estimated utilities owed (if any)
    if utility_owed_estimate and utility_owed_estimate > 0:
        prorations.append(
            ("Utilities / Water Owed (est.)", utility_owed_estimate)
        )

    prorations_total = sum(a for _, a in prorations)

    # ---- Bottom Line ---------------------------------------------------
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
    """Build the seller-facing preliminary settlement statement PDF.

    Args:
        prop: property dict — needs address, city, state, zip, county,
              hoa, annual_taxes, buyer_pays_seller_closings
        rec: recommendation dict — used to pull sale_price from
              cash_offer_to_seller (or override)
        seller: seller dict — mtg1, mtg2, other_liens, timeline_days
        buyer_entity_name: what to display as the buyer (default Exodus)
        seller_name: what to display as the seller (default: read from
              seller.name, else "Homeowner")
        prepared_by: name/entity to show at bottom (default Exodus)
        sale_price_override: use this instead of cash_offer_to_seller
        closing_date: estimated closing date. If None, uses today +
              seller.timeline_days or +30 days.
        seller_uses_agent: True to include realtor commission line
        commission_pct: commission % if seller_uses_agent

    Returns PDF bytes ready to hand to st.download_button().
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, KeepTogether)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.5*inch, bottomMargin=0.5*inch,
    )

    # ---- Determine sale price ---------------------------------------
    if sale_price_override is not None:
        sale_price = float(sale_price_override)
    else:
        sale_price = float(
            rec.get("cash_offer_to_seller")
            or rec.get("cash_offer", 0)
            or 0
        )

    # ---- Determine closing date -------------------------------------
    if closing_date is None:
        timeline_days = int(seller.get("timeline_days", 30) or 30)
        closing_date = datetime.now() + timedelta(days=timeline_days)

    # ---- Compute the numbers ----------------------------------------
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
        utility_owed_estimate=0,  # user-editable in future revs
        seller_uses_agent=seller_uses_agent,
        commission_pct=commission_pct,
    )

    # ---- Styles -----------------------------------------------------
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

    # ---- Title Block ------------------------------------------------
    story.append(Paragraph("PRELIMINARY SETTLEMENT STATEMENT", title_style))
    story.append(Paragraph(
        "Seller's Estimated Net at Closing", subtitle_style
    ))

    # ---- Property / Party Header (2-column table) -------------------
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
        ["County:", prop.get("county", "—") or "—"],
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

    # ---- Sale Price Banner ------------------------------------------
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

    # ---- Helper to render a section (banner + line items + subtotal)
    def section(banner_label: str, items: list, subtotal_amount: float,
                subtotal_label: str, banner_note: str = "") -> list:
        """Return a list of flowables for one section."""
        flows = []
        # Colored banner
        banner_html = f"<b>{banner_label}</b>"
        if banner_note:
            banner_html += (
                f'&nbsp;&nbsp;<font size="8">— {banner_note}</font>'
            )
        flows.append(Paragraph(banner_html, header_style))
        # Line items — 2-column table (label / amount)
        if not items:
            flows.append(Paragraph(
                "<i>(none applicable)</i>", body_normal
            ))
        else:
            rows = []
            for label, amt in items:
                rows.append([label, f"(${amt:,.2f})"])
            # Subtotal row
            rows.append(["", ""])
            rows.append([
                f"Subtotal — {subtotal_label}",
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

    # ---- Section 1: Seller's Closing Costs ---------------------------
    banner_note = ""
    if net_sheet["buyer_covers_costs"]:
        banner_note = "Exodus is covering the seller's closing costs"
    for f in section(
        "SELLER'S CLOSING COSTS",
        net_sheet["closing_costs"],
        net_sheet["closing_costs_total"],
        "Closing Costs",
        banner_note=banner_note,
    ):
        story.append(f)

    # ---- Section 2: Payoffs ------------------------------------------
    for f in section(
        "SELLER'S PAYOFFS (mortgages, liens)",
        net_sheet["payoffs"],
        net_sheet["payoffs_total"],
        "Payoffs",
    ):
        story.append(f)

    # ---- Section 3: Prorations ---------------------------------------
    for f in section(
        f"PRORATIONS AS OF {closing_date.strftime('%B %d, %Y')}",
        net_sheet["prorations"],
        net_sheet["prorations_total"],
        "Prorations",
    ):
        story.append(f)

    # ---- Bottom Line: Estimated Net to Seller -----------------------
    story.append(Spacer(1, 0.15*inch))

    net_amt = net_sheet["estimated_net_to_seller"]
    net_color = positive_green if net_amt >= 0 else colors.HexColor("#B91C1C")
    net_data = [[
        Paragraph(
            '<font size="10" color="#666666"><b>ESTIMATED NET TO SELLER</b></font><br/>'
            '<font size="9" color="#666666">'
            'Sale Price − Closing Costs − Payoffs − Prorations</font>',
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
            ("LINEBEFORE", (0, 0), (0, -1),
             4, positive_green if net_amt >= 0 else colors.HexColor("#B91C1C")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]),
    )
    story.append(net_tbl)

    # ---- Reconciliation summary --------------------------------------
    story.append(Spacer(1, 0.10*inch))
    recon_txt = (
        f"<b>Reconciliation:</b> "
        f"Sale Price ${net_sheet['sale_price']:,.0f} "
        f"− Closing Costs ${net_sheet['closing_costs_total']:,.0f} "
        f"− Payoffs ${net_sheet['payoffs_total']:,.0f} "
        f"− Prorations ${net_sheet['prorations_total']:,.0f} "
        f"= <b>${net_amt:,.0f}</b>"
    )
    story.append(Paragraph(recon_txt, body_normal))

    # ---- Disclaimer -------------------------------------------------
    story.append(Spacer(1, 0.20*inch))
    disclaimer = (
        "<b>DISCLAIMER:</b> This is a <b>good-faith estimate</b> based on "
        "information provided by the seller and typical Florida closing "
        "customs. Mortgage payoff figures are estimates and will be "
        "finalized by the lender at closing. Prorated taxes assume the "
        "current annual tax bill; the final proration will be based on "
        "the actual bill received. Final settlement figures will be "
        "determined by the title company and reflected in the official "
        "ALTA Settlement Statement provided at closing."
    )
    story.append(Paragraph(disclaimer, disclaimer_style))

    # ---- Footer -----------------------------------------------------
    footer_prep = prepared_by or "Exodus Property Solutions"
    footer_txt = (
        f'<font color="#666666" size="8">'
        f'Prepared by <b>{footer_prep}</b> · '
        f'{datetime.now().strftime("%B %d, %Y at %I:%M %p")}'
        f'</font>'
    )
    story.append(Spacer(1, 0.12*inch))
    story.append(Paragraph(footer_txt, disclaimer_style))

    doc.build(story)
    return buf.getvalue()


# ============================================================================
# COMPARISON PDF — "Sell to Us" vs. "List with a Realtor"
# ============================================================================
# The persuasion tool. Sellers overestimate what they'd net at retail because
# they only think about the higher sticker price. This document lays out the
# whole picture: retail sticker price MINUS commission, MINUS inspection
# concession for the property's actual condition, MINUS full seller-side
# closing costs — vs. Exodus's clean, fast cash offer.
#
# Numbers used:
#   - Exodus column: same net sheet as build_settlement_pdf()
#   - Realtor column: retail price (ARV) minus 6% commission, minus estimated
#     inspection concession (default 100% of rehab estimate — buyers see the
#     deferred maintenance and demand credit or price reduction covering it),
#     minus seller-side closing costs at the retail price (higher because
#     the sale price is higher → higher deed doc stamps), minus same payoffs
#     and prorations extended for the longer holding period.
#
# The realtor route ALSO drags out time. That has a real cost:
#   - Days on Market before offer (SoFL avg ~45 days for as-is condition)
#   - Days to close after under contract (~30-45 for financed buyer)
#   - Total: 75-120 days vs. Exodus's 14-21 days
#   - During that time seller keeps paying mortgage, taxes, insurance,
#     HOA, utilities on a property they've already emotionally checked out
# ============================================================================


def compute_realtor_route_net(
    arv: float,
    rehab_estimate: float,
    county: str,
    has_hoa: bool,
    mortgage1_payoff: float,
    mortgage2_payoff: float,
    other_liens: float,
    annual_property_taxes: float,
    hoa_monthly: float,
    utility_owed_estimate: float,
    closing_date: Optional[datetime] = None,
    commission_pct: float = 0.06,
    inspection_concession_pct_of_rehab: float = 1.00,
) -> Dict[str, Any]:
    """Model the realtor listing route so the seller sees the honest picture.

    Retail sale at ARV — but the seller pays for a full realtor commission
    (both sides, 6% typical), an inspection concession that covers the
    property's deferred maintenance (default 100% of rehab estimate —
    a retail buyer sees the same repair needs Exodus sees and demands
    credit or price reduction covering them), plus full seller-side
    closing costs computed at the higher retail price.

    Args:
        arv: after-repair value / retail listing price
        rehab_estimate: Exodus's rehab estimate. Drives the inspection
            concession — retail buyer will demand credit ~100% of this.
        commission_pct: total commission (listing + selling combined).
            0.06 is FL default. Some markets 0.05.
        inspection_concession_pct_of_rehab: multiplier on rehab estimate.
            1.0 = buyer demands full rehab as credit. 0.75 = negotiated down.

    Returns dict with the SAME shape as compute_seller_net_sheet.
    """
    if closing_date is None:
        # Realtor route: 45 days on market + 30 days to close = 75 days
        closing_date = datetime.now() + timedelta(days=75)

    # Retail closing costs (seller pays full slate at ARV)
    closing_costs = []
    doc_stamps = arv * 0.007
    closing_costs.append(
        (f"Deed Documentary Stamps (0.70% of ${arv:,.0f})", doc_stamps)
    )
    if _seller_pays_owner_title(county):
        owner_title = arv * 0.004
        closing_costs.append(
            (f"Owner's Title Insurance (~0.40% of ${arv:,.0f})", owner_title)
        )
    closing_costs.append(("Title Search Fee", 200))
    closing_costs.append(("Settlement / Escrow Fee", 600))
    closing_costs.append(("Municipal Lien Search", 500))
    if has_hoa:
        closing_costs.append(("HOA Estoppel Fee", 500))
    closing_costs.append(("Recording Fees", 150))

    # Realtor commission — the big one
    commission = arv * commission_pct
    closing_costs.append(
        (f"Realtor Commission ({commission_pct*100:.1f}% of ${arv:,.0f}, "
         "listing + selling agents)", commission)
    )

    # Inspection concession — retail buyer demands credit for deferred
    # maintenance. Same repair scope Exodus sees, they see too.
    concession = rehab_estimate * inspection_concession_pct_of_rehab
    if concession > 0:
        concession_pct_label = int(inspection_concession_pct_of_rehab * 100)
        closing_costs.append(
            (f"Inspection Concession / Repair Credit "
             f"({concession_pct_label}% of ${rehab_estimate:,.0f} rehab est. — "
             "buyer sees condition & demands credit)", concession)
        )

    closing_costs_total = sum(a for _, a in closing_costs)

    # Payoffs (same as clean route — mortgages and liens don't care what
    # price the property sold for)
    payoffs = []
    if mortgage1_payoff and mortgage1_payoff > 0:
        payoffs.append(("1st Mortgage Payoff (est.)", mortgage1_payoff))
    if mortgage2_payoff and mortgage2_payoff > 0:
        payoffs.append(("2nd Mortgage / HELOC Payoff (est.)", mortgage2_payoff))
    if other_liens and other_liens > 0:
        payoffs.append(("Other Liens (tax, code — est.)", other_liens))
    payoffs_total = sum(a for _, a in payoffs)

    # Prorations — extended timeline means seller owned longer, owes more
    prorations = []
    if annual_property_taxes and annual_property_taxes > 0:
        year_start = datetime(closing_date.year, 1, 1)
        days_owned = max(0, (closing_date - year_start).days + 1)
        tax_proration = annual_property_taxes * (days_owned / 365.0)
        prorations.append(
            (f"Property Taxes (Jan 1 → closing, {days_owned} days)",
             tax_proration)
        )
    if hoa_monthly and hoa_monthly > 0:
        day_of_month = closing_date.day
        hoa_proration = hoa_monthly * (day_of_month / 30.0)
        prorations.append(
            (f"HOA Dues (partial month, {day_of_month} days)", hoa_proration)
        )
    if utility_owed_estimate and utility_owed_estimate > 0:
        prorations.append(
            ("Utilities / Water Owed (est.)", utility_owed_estimate)
        )
    prorations_total = sum(a for _, a in prorations)

    total_deductions = closing_costs_total + payoffs_total + prorations_total
    estimated_net = arv - total_deductions

    return {
        "sale_price": arv,
        "closing_costs": closing_costs,
        "closing_costs_total": closing_costs_total,
        "commission_amount": commission,
        "concession_amount": concession,
        "payoffs": payoffs,
        "payoffs_total": payoffs_total,
        "prorations": prorations,
        "prorations_total": prorations_total,
        "total_deductions": total_deductions,
        "estimated_net_to_seller": estimated_net,
        "closing_date": closing_date,
        "county": county,
    }


def build_options_comparison_pdf(
    prop: Dict[str, Any],
    rec: Dict[str, Any],
    seller: Dict[str, Any],
    buyer_entity_name: str = "Exodus Property Solutions",
    seller_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    exodus_offer_override: Optional[float] = None,
    arv_override: Optional[float] = None,
    rehab_estimate_override: Optional[float] = None,
    rehab_items: Optional[list] = None,
    exodus_closing_date: Optional[datetime] = None,
    realtor_closing_date: Optional[datetime] = None,
    realtor_commission_pct: float = 0.06,
    inspection_concession_pct_of_rehab: float = 1.00,
    exodus_days_to_close: int = 21,
    realtor_days_on_market: int = 45,
    realtor_days_to_close: int = 30,
) -> bytes:
    """Two-column comparison PDF — "Sell to Us" vs. "List with a Realtor".

    Designed to be persuasive: sellers see that even though the realtor
    route has a higher sticker price, the ACTUAL net after commission +
    inspection concession + longer closing costs + longer holding is
    usually similar or LESS — and takes 4-6x longer.

    Args mostly mirror build_settlement_pdf. Extra realtor-route params:
        realtor_commission_pct: 0.06 default (industry standard)
        inspection_concession_pct_of_rehab: 1.00 = buyer demands full rehab
            credit, 0.75 = negotiated down. Tune per market.
        exodus_days_to_close: 21 default (fast cash close)
        realtor_days_on_market: 45 default (SoFL avg, as-is condition)
        realtor_days_to_close: 30 default (financed buyer)
    """
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, KeepTogether)
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    buf = BytesIO()
    # v24.6 — balanced margins. v24.5's 0.3" top/bottom left ~30% empty
    # space on standard deals. Bump to 0.5" top/bottom for a more relaxed
    # look; heavy deals still fit thanks to the tightened rows below.
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.4*inch, rightMargin=0.4*inch,
        topMargin=0.5*inch, bottomMargin=0.4*inch,
    )

    # ---- Numbers ----------------------------------------------------
    exodus_offer = float(
        exodus_offer_override
        if exodus_offer_override is not None
        else (rec.get("cash_offer_to_seller") or rec.get("cash_offer", 0) or 0)
    )
    arv = float(
        arv_override
        if arv_override is not None
        else (rec.get("arv", 0) or 0)
    )
    rehab_estimate = float(
        rehab_estimate_override
        if rehab_estimate_override is not None
        else (rec.get("rehab_total", 0) or 0)
    )

    # Timelines
    if exodus_closing_date is None:
        exodus_closing_date = datetime.now() + timedelta(days=exodus_days_to_close)
    if realtor_closing_date is None:
        realtor_total_days = realtor_days_on_market + realtor_days_to_close
        realtor_closing_date = datetime.now() + timedelta(days=realtor_total_days)

    # ---- Exodus net sheet -------------------------------------------
    exodus_net = compute_seller_net_sheet(
        sale_price=exodus_offer,
        county=prop.get("county", "") or "",
        has_hoa=bool(prop.get("hoa", 0) and prop.get("hoa", 0) > 0),
        mortgage1_payoff=float(seller.get("mtg1", 0) or 0),
        mortgage2_payoff=float(seller.get("mtg2", 0) or 0),
        other_liens=float(seller.get("other_liens", 0) or 0),
        annual_property_taxes=float(prop.get("annual_taxes", 0) or 0),
        hoa_monthly=float(prop.get("hoa", 0) or 0),
        closing_date=exodus_closing_date,
        buyer_pays_seller_closings=bool(prop.get("buyer_pays_seller_closings")),
    )

    # ---- Realtor net sheet ------------------------------------------
    realtor_net = compute_realtor_route_net(
        arv=arv,
        rehab_estimate=rehab_estimate,
        county=prop.get("county", "") or "",
        has_hoa=bool(prop.get("hoa", 0) and prop.get("hoa", 0) > 0),
        mortgage1_payoff=float(seller.get("mtg1", 0) or 0),
        mortgage2_payoff=float(seller.get("mtg2", 0) or 0),
        other_liens=float(seller.get("other_liens", 0) or 0),
        annual_property_taxes=float(prop.get("annual_taxes", 0) or 0),
        hoa_monthly=float(prop.get("hoa", 0) or 0),
        utility_owed_estimate=0,
        closing_date=realtor_closing_date,
        commission_pct=realtor_commission_pct,
        inspection_concession_pct_of_rehab=inspection_concession_pct_of_rehab,
    )

    # ---- Deltas -----------------------------------------------------
    net_delta = exodus_net["estimated_net_to_seller"] - realtor_net["estimated_net_to_seller"]
    days_exodus = exodus_days_to_close
    days_realtor = realtor_days_on_market + realtor_days_to_close
    days_saved = days_realtor - days_exodus

    # ---- Styles -----------------------------------------------------
    styles = getSampleStyleSheet()
    brand_blue = colors.HexColor("#1F4E78")
    brand_blue_dark = colors.HexColor("#163A5A")
    exodus_bg = colors.HexColor("#EAF4EA")     # subtle green
    exodus_stripe = colors.HexColor("#1E7B3B") # green stripe
    realtor_bg = colors.HexColor("#FEF3F2")    # subtle red
    realtor_stripe = colors.HexColor("#B91C1C") # red stripe
    subtle_gray = colors.HexColor("#666666")
    positive_green = colors.HexColor("#1E7B3B")
    grid_gray = colors.HexColor("#D0D7DE")

    # v24.6 — font sizes tuned for readable single-page fit. v24.5 was
    # too tight; v24.6 sits at ~90-95% page fill on standard deals.
    # If this ever overflows on a very heavy deal (20+ rehab items),
    # tighten row padding first (see table style), THEN drop fonts.
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=16,
        textColor=brand_blue_dark, alignment=TA_CENTER,
        spaceBefore=0, spaceAfter=2, fontName="Helvetica-Bold",
    )
    subtitle_style = ParagraphStyle(
        "Subtitle", parent=styles["Normal"], fontSize=10,
        textColor=subtle_gray, alignment=TA_CENTER, spaceAfter=8,
        fontName="Helvetica-Oblique",
    )
    body_normal = ParagraphStyle(
        "BodyNormal", parent=styles["Normal"], fontSize=9,
        textColor=colors.HexColor("#222222"), leading=11.5,
    )
    body_small = ParagraphStyle(
        "BodySmall", parent=styles["Normal"], fontSize=8,
        textColor=colors.HexColor("#222222"), leading=10.5,
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer", parent=styles["Normal"], fontSize=7.5,
        textColor=subtle_gray, leading=9.5, alignment=TA_LEFT,
        spaceBefore=4, fontName="Helvetica-Oblique",
    )

    story = []

    # ---- Title ------------------------------------------------------
    story.append(Paragraph("YOUR OPTIONS — TWO WAYS TO SELL", title_style))
    story.append(Paragraph(
        "Side-by-side comparison of what you would actually net at closing",
        subtitle_style,
    ))

    # ---- Property / seller header block -----------------------------
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
        ["Property:", addr_full,   "Seller:",   _seller_name],
        ["",         city_state_zip, "Prepared:", datetime.now().strftime("%B %d, %Y")],
    ]
    header_tbl = Table(
        header_data,
        colWidths=[0.85*inch, 3.55*inch, 0.85*inch, 2.45*inch],
        style=TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
            ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
            ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9),
            ("TEXTCOLOR", (0, 0), (0, -1), brand_blue_dark),
            ("TEXTCOLOR", (2, 0), (2, -1), brand_blue_dark),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ]),
    )
    story.append(header_tbl)
    story.append(Spacer(1, 0.14*inch))

    # ---- Two-column comparison helper -------------------------------
    def _fmt_neg(amt: float) -> str:
        """Format a deduction as (parenthesized) currency."""
        if amt == 0:
            return "$0"
        return f"(${amt:,.0f})"

    def _row(label: str, exodus_val: str, realtor_val: str,
             is_bold: bool = False) -> list:
        """One row of the comparison."""
        return [label, exodus_val, realtor_val]

    # Build the comparison table row by row
    rows = [
        # Column headers
        ["", "SELL TO US", "LIST WITH A REALTOR"],
        # Sale Price
        ["Sale Price",
         f"${exodus_net['sale_price']:,.0f}",
         f"${realtor_net['sale_price']:,.0f}"],
        # Divider
        ["", "", ""],
        # Realtor commission (Exodus = $0 — this is the biggest lever)
        [f"Realtor Commission ({realtor_commission_pct*100:.1f}%)",
         "$0",
         _fmt_neg(realtor_net["commission_amount"])],
        # Repairs to reach market value — the burden line
        # This is the seller's biggest cost when going retail: to sell at
        # ARV, they have to either PAY for the repairs upfront OR give the
        # buyer a credit at closing after their inspection. Same money,
        # different door.
        [f"Repairs to Reach ${arv:,.0f} Market Value\n(you pay upfront OR credit at closing)",
         "$0  (we buy as-is)",
         _fmt_neg(realtor_net["concession_amount"])],
        # Closing costs
        ["Seller's Closing Costs",
         _fmt_neg(exodus_net["closing_costs_total"]),
         _fmt_neg(realtor_net["closing_costs_total"] - realtor_net["commission_amount"] - realtor_net["concession_amount"])],
        # Payoffs
        ["Mortgage & Lien Payoffs",
         _fmt_neg(exodus_net["payoffs_total"]),
         _fmt_neg(realtor_net["payoffs_total"])],
        # Prorations
        ["Prorated Taxes / HOA / Utilities",
         _fmt_neg(exodus_net["prorations_total"]),
         _fmt_neg(realtor_net["prorations_total"])],
        # Divider
        ["", "", ""],
        # NET
        ["ESTIMATED NET TO YOU",
         f"${exodus_net['estimated_net_to_seller']:,.0f}",
         f"${realtor_net['estimated_net_to_seller']:,.0f}"],
    ]

    # Build the timeline rows
    timeline_rows = [
        ["", "", ""],  # spacer
        ["Days on Market",
         "0 days",
         f"~{realtor_days_on_market} days"],
        ["Days to Close After Under Contract",
         f"{exodus_days_to_close} days",
         f"~{realtor_days_to_close} days"],
        ["TOTAL TIME TO CASH IN HAND",
         f"{days_exodus} days",
         f"~{days_realtor} days"],
        ["Certainty",
         "100% cash — no financing contingencies",
         "Contingent on financing + inspection"],
        ["What You Have To Do",
         "Nothing — we buy as-is",
         "Repairs, cleaning, staging, showings"],
    ]

    combined_rows = rows + timeline_rows

    # Column widths — small label column, then two roughly equal option columns
    tbl = Table(
        combined_rows,
        colWidths=[2.4*inch, 2.6*inch, 2.6*inch],
        style=TableStyle([
            # Global fonts
            ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            # Header row (SELL TO US / LIST WITH A REALTOR)
            ("BACKGROUND", (1, 0), (1, 0), exodus_stripe),
            ("BACKGROUND", (2, 0), (2, 0), realtor_stripe),
            ("TEXTCOLOR", (1, 0), (2, 0), colors.white),
            ("FONT", (1, 0), (2, 0), "Helvetica-Bold", 11),
            ("ALIGN", (1, 0), (2, 0), "CENTER"),
            ("TOPPADDING", (1, 0), (2, 0), 6),
            ("BOTTOMPADDING", (1, 0), (2, 0), 6),
            # Left label column
            ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
            ("TEXTCOLOR", (0, 0), (0, -1), brand_blue_dark),
            # Zebra striping on the option columns
            ("BACKGROUND", (1, 1), (1, len(combined_rows)-1), exodus_bg),
            ("BACKGROUND", (2, 1), (2, len(combined_rows)-1), realtor_bg),
            # Right-align numbers in the option columns
            ("ALIGN", (1, 1), (2, len(rows)-1), "RIGHT"),
            ("ALIGN", (1, len(rows)), (2, -1), "CENTER"),
            # ESTIMATED NET row (last row of `rows` = index len(rows)-1)
            ("FONT", (0, len(rows)-1), (2, len(rows)-1),
             "Helvetica-Bold", 12.5),
            ("BACKGROUND", (1, len(rows)-1), (1, len(rows)-1),
             exodus_stripe),
            ("BACKGROUND", (2, len(rows)-1), (2, len(rows)-1),
             realtor_stripe),
            ("TEXTCOLOR", (1, len(rows)-1), (2, len(rows)-1), colors.white),
            ("TEXTCOLOR", (0, len(rows)-1), (0, len(rows)-1),
             brand_blue_dark),
            ("TOPPADDING", (0, len(rows)-1), (2, len(rows)-1), 8),
            ("BOTTOMPADDING", (0, len(rows)-1), (2, len(rows)-1), 8),
            # TOTAL TIME row emphasis (row = len(rows) + 3)
            ("FONT", (0, len(rows)+3), (2, len(rows)+3),
             "Helvetica-Bold", 10),
            ("BACKGROUND", (1, len(rows)+3), (1, len(rows)+3),
             exodus_stripe),
            ("BACKGROUND", (2, len(rows)+3), (2, len(rows)+3),
             realtor_stripe),
            ("TEXTCOLOR", (1, len(rows)+3), (2, len(rows)+3),
             colors.white),
            # Grid lines
            ("LINEABOVE", (0, len(rows)-1), (-1, len(rows)-1),
             1.5, brand_blue),
            ("LINEBELOW", (0, len(rows)-1), (-1, len(rows)-1),
             1.5, brand_blue),
            ("BOX", (1, 0), (1, -1), 0.5, grid_gray),
            ("BOX", (2, 0), (2, -1), 0.5, grid_gray),
        ]),
    )
    story.append(tbl)

    # ---- Bottom summary / persuasion block --------------------------
    story.append(Spacer(1, 0.14*inch))

    # Build the takeaway narrative
    if net_delta > 0:
        # Exodus actually nets MORE — best case for us
        takeaway = (
            f"<b>Bottom line:</b> Selling to us puts <b>"
            f"${abs(net_delta):,.0f} MORE in your pocket</b> than listing "
            f"with a realtor — and you get it <b>{days_saved} days sooner</b>, "
            "with no repairs, no showings, and no financing risk."
        )
        takeaway_bg = exodus_bg
        takeaway_stripe = exodus_stripe
    elif abs(net_delta) < arv * 0.03:
        # Within 3% of each other — call it a wash and emphasize time/certainty
        takeaway = (
            f"<b>Bottom line:</b> Even at the higher listing price, the "
            f"realtor route only nets an extra <b>${abs(net_delta):,.0f}</b> "
            f"— but takes <b>{days_saved} days longer</b>, and only if the "
            "buyer's inspection and financing both come through. With us "
            "it's clean cash in your hand in weeks, guaranteed."
        )
        takeaway_bg = exodus_bg
        takeaway_stripe = exodus_stripe
    else:
        # Realtor genuinely nets more (rare when there's real deferred maint.)
        # Still lean on time + certainty + zero effort.
        takeaway = (
            f"<b>Bottom line:</b> Listing with a realtor MAY net an extra "
            f"<b>${abs(net_delta):,.0f}</b> — but only after ~{days_realtor} "
            "days of showings, negotiations, inspection contingencies, and "
            "financing hurdles. With us, it's <b>guaranteed cash in "
            f"{days_exodus} days</b>. Trade the uncertainty for the certainty."
        )
        takeaway_bg = colors.HexColor("#FFF8E1")  # neutral yellow
        takeaway_stripe = colors.HexColor("#B87700")

    takeaway_tbl = Table(
        [[Paragraph(takeaway, body_normal)]],
        colWidths=[7.6*inch],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), takeaway_bg),
            ("LINEBEFORE", (0, 0), (0, -1), 4, takeaway_stripe),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]),
    )
    story.append(takeaway_tbl)

    # ---- The Repair Burden callout box -----------------------------
    # Only appears when there's meaningful rehab. Spells out the
    # two-doors-same-room reality: to sell at retail (ARV), the seller
    # bears the cost of getting the property to market condition —
    # either by paying for repairs before listing OR by giving a credit
    # at closing after the buyer's inspection.
    # v24.5 — condensed to fit single-page constraint.
    if rehab_estimate > 0:
        story.append(Spacer(1, 0.10*inch))

        # Build the itemized top-repairs list, if we have one
        top_items_html = ""
        if rehab_items:
            # Show top 5 items by cost so the seller sees what needs doing
            items_sorted = sorted(
                [(lbl, amt) for lbl, amt in rehab_items if amt > 0],
                key=lambda x: x[1], reverse=True,
            )
            top = items_sorted[:5]
            if top:
                item_lines = []
                for lbl, amt in top:
                    # Compact the label — chop everything after the first paren
                    compact = lbl.split(" (")[0].strip()
                    item_lines.append(
                        f'&bull;&nbsp;<b>{compact}</b>&nbsp;'
                        f'<font color="#B91C1C">${amt:,.0f}</font>'
                    )
                if len(items_sorted) > 5:
                    remaining = sum(a for _, a in items_sorted[5:])
                    item_lines.append(
                        f'&bull;&nbsp;<i>+ {len(items_sorted) - 5} more items</i>'
                        f'&nbsp;<font color="#B91C1C">'
                        f'${remaining:,.0f}</font>'
                    )
                top_items_html = "<br/>".join(item_lines)

        # Burden narrative — restored to more readable size in v24.6.
        burden_html = (
            f'<font size="11" color="#B91C1C"><b>'
            f'THE REPAIR BURDEN &mdash; TWO DOORS, SAME ROOM'
            f'</b></font><br/><br/>'
            f'<font size="9">'
            f'To sell at <b>${arv:,.0f}</b> retail, the property has to be '
            f'in market-ready condition. That means <b>you pay the '
            f'<font color="#B91C1C">${rehab_estimate:,.0f}</font></b> '
            f'either way:<br/><br/>'
            f'&nbsp;&bull;&nbsp;<b>Door #1 &mdash;</b> Repairs upfront '
            f'(add 60&ndash;90 days for permits &amp; contractor '
            f'management), <b>OR</b><br/>'
            f'&nbsp;&bull;&nbsp;<b>Door #2 &mdash;</b> Buyer&rsquo;s '
            f'inspector finds the same issues and demands a closing '
            f'credit at least that big.<br/><br/>'
            f'The realtor route&rsquo;s "higher price" <i>already assumes '
            f'you&rsquo;ve absorbed this cost.</i>'
            f'</font>'
        )

        # 2-column layout: burden text on left, itemized repairs on right
        if top_items_html:
            burden_data = [[
                Paragraph(burden_html, body_normal),
                Paragraph(
                    '<font size="9.5"><b>What the repairs cover '
                    '(our estimate):</b></font><br/><br/>' + top_items_html,
                    body_small,
                ),
            ]]
            burden_tbl = Table(
                burden_data,
                colWidths=[4.6*inch, 3.0*inch],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1),
                     colors.HexColor("#FEF3F2")),
                    ("LINEBEFORE", (0, 0), (0, -1), 4,
                     colors.HexColor("#B91C1C")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (0, 0), 14),
                    ("LEFTPADDING", (1, 0), (1, 0), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("LINEBEFORE", (1, 0), (1, 0), 0.5,
                     colors.HexColor("#DDDDDD")),
                ]),
            )
        else:
            burden_tbl = Table(
                [[Paragraph(burden_html, body_normal)]],
                colWidths=[7.6*inch],
                style=TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1),
                     colors.HexColor("#FEF3F2")),
                    ("LINEBEFORE", (0, 0), (0, -1), 4,
                     colors.HexColor("#B91C1C")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]),
            )
        story.append(burden_tbl)

    # ---- Disclaimer -------------------------------------------------
    story.append(Spacer(1, 0.08*inch))
    footer_prep = prepared_by or "Exodus Property Solutions"
    combined_footer = (
        "<b>Good-faith estimate.</b> Realtor commission assumed at 6% "
        "(listing + selling). Inspection concession estimates repair credit "
        "a retail buyer would demand based on current condition; actual credit "
        "depends on inspector and negotiation. Days on market are SoFL "
        "averages for similar-condition properties. Actual results may vary."
        f' &nbsp;&mdash;&nbsp; <b>Prepared by {footer_prep}</b>, '
        f'{datetime.now().strftime("%B %d, %Y")}.'
    )
    story.append(Paragraph(combined_footer, disclaimer_style))

    doc.build(story)
    return buf.getvalue()
