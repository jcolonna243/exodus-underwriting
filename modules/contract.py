"""Generate a filled Purchase & Sale Agreement PDF.

Approach: overlay text + checkmarks onto the existing Florida Realtors /
Florida Bar AS-IS template (bundled at data/contract_template.pdf). All
template legal language is preserved exactly — we only stamp the field
values. Coordinates were probed once from the template (PDF points, origin
bottom-left, page size 612×792).

When the property's year built is pre-1978, a Lead-Based Paint Disclosure
rider is appended automatically.

Always-true business rules (per Jo, v23):
  - Buyer is always "NSGC Investing Services, Inc"
  - §7 Assignability — box 2 (assign but NOT released) is always checked
  - §8 Financing — box (a) Cash is always checked
  - Closing date language is always "30 days from execution"
  - Initial deposit is always 1% of purchase price

Public API:
    build_contract_pdf(deal, contract_inputs, title_company) -> bytes
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

import pypdf
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (Paragraph, Spacer, SimpleDocTemplate,
                                Table, TableStyle, PageBreak)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

TEMPLATE_PATH = Path(__file__).parent.parent / "data" / "contract_template.pdf"

PAGE_W = 612.0
PAGE_H = 792.0
BUYER_NAME = "NSGC Investing Services, Inc."


# ---------------------------------------------------------------------------
# Coordinate map — PDF points, origin BOTTOM-LEFT.
# Each entry: (x, y) where overlay text should be drawn.
# Keep these in one place so re-aligning the template only touches one block.
# ---------------------------------------------------------------------------
COORDS = {
    # Page 1 (0-indexed = 0)
    "p1": {
        "seller_name":      (80, 690.5),
        "buyer_name":       (80, 679.0),
        "street_addr":      (215, 621.3),
        "county":           (170, 609.7),
        "tax_id":           (460, 609.7),
        "legal_desc_top":   (72, 587.0),    # top of 3-line block
        "purchase_price":   (505, 379.1),
        "initial_deposit":  (495, 361.7),
        "deposit_days":     (320, 332.0),   # "_____ (if left blank, then 3) days"
        "escrow_name":      (175, 312.0),
        "escrow_phone":     (480, 290.7),
        "escrow_email":     (175, 280.0),
        "additional_deposit_days": (260, 269.7),  # _____ (if left blank, then 10)
        "additional_deposit":     (505, 268.0),
        "balance_to_close":  (505, 209.2),
        "effective_offer_date":  (180, 174.0),  # line 45 acceptance date
    },
    # Page 2 (0-indexed = 1)
    "p2": {
        "closing_date_text": (240, 726.5),   # "30 days from execution"
        "assign_check":      (130, 370.0),   # X mark for "may assign but not be released"
        "cash_check":        (74, 312.0),    # X mark for cash transaction (a)
    },
    # Page 5 (0-indexed = 4) — inspection period
    "p5": {
        "inspection_days":   (290, 243.1),
    },
    # Page 11 (0-indexed = 10) — addenda checklist
    "p11": {
        "lbp_addenda_check": (378, 309.6),   # X for "P. Lead Paint Disclosure (Pre-1978)"
    },
    # Page 13 (0-indexed = 12) — signature blocks
    "p13": {
        "buyer_signature_name":  (160, 412.0),
        "seller_signature_name": (160, 377.0),
    },
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _money(amount: float) -> str:
    """Format as USD with commas, no $ sign (the template prints $ already)."""
    try:
        return f"{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _safe(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _initial_deposit_amount(purchase_price: float) -> float:
    """1% of purchase price, rounded to the nearest dollar."""
    try:
        return round(float(purchase_price) * 0.01)
    except (TypeError, ValueError):
        return 0


def _county_from_deal(deal_inputs: Dict[str, Any]) -> str:
    """Best-effort county derivation. Prefers explicit field; falls back to
    saved deal["property"]["county"]; else returns empty so the user can fill
    it on the contract form if needed."""
    prop = deal_inputs.get("property", {}) or {}
    return prop.get("county") or prop.get("County") or ""


# ---------------------------------------------------------------------------
# Overlay PDF generator
# ---------------------------------------------------------------------------
def _build_overlay(
    deal_inputs: Dict[str, Any],
    contract_inputs: Dict[str, Any],
    title_company: Dict[str, Any],
) -> BytesIO:
    """Render a multi-page overlay PDF whose pages line up 1:1 with the
    template. Returns a seekable BytesIO."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)

    seller = deal_inputs.get("seller", {}) or {}
    prop = deal_inputs.get("property", {}) or {}

    purchase_price = float(contract_inputs.get("purchase_price") or 0)
    initial_deposit = _initial_deposit_amount(purchase_price)
    additional_deposit = float(contract_inputs.get("additional_deposit") or 0)
    balance_to_close = max(0.0, purchase_price - initial_deposit - additional_deposit)

    # ------------------------------------------------------------------
    # PAGE 1 — parties, property, deposits
    # ------------------------------------------------------------------
    # First, mask prior overlay text that's baked into the template (the
    # uploaded template has remnants of a previous fill in 3 spots). White
    # rectangles preserve the form lines visually because they sit on the
    # baseline only, but eat the bigger overlay text that's above the line.
    from reportlab.lib.colors import white
    c.setFillColor(white)
    c.setStrokeColor(white)
    # Buyer name line — wipe prior "NSGC Investing Services, Inc" overlay
    c.rect(72, 676, 478, 14, fill=1, stroke=0)
    # Initial purchase price field — wipe prior "0" overlay
    c.rect(478, 376, 80, 10, fill=1, stroke=0)
    c.setFillColor(black)

    c.setFont("Helvetica", 10)

    # Seller name (parties block, very top)
    seller_name = _safe(seller.get("name"))
    if seller_name:
        x, y = COORDS["p1"]["seller_name"]
        c.drawString(x, y, seller_name)

    # Buyer name — always NSGC
    x, y = COORDS["p1"]["buyer_name"]
    c.drawString(x, y, BUYER_NAME)

    # Property address (street, city, zip together)
    street_line = ", ".join(filter(None, [
        prop.get("address", ""),
        prop.get("city", ""),
        f'{prop.get("state","FL")} {prop.get("zip","")}'.strip(),
    ]))
    if street_line:
        x, y = COORDS["p1"]["street_addr"]
        c.drawString(x, y, street_line[:80])

    # County + Tax ID
    county = _county_from_deal(deal_inputs) or contract_inputs.get("county", "")
    if county:
        x, y = COORDS["p1"]["county"]
        c.drawString(x, y, county)
    tax_id = prop.get("parcel_folio") or contract_inputs.get("parcel_folio", "")
    if tax_id:
        x, y = COORDS["p1"]["tax_id"]
        c.drawString(x, y, str(tax_id))

    # Legal description (up to 3 lines)
    legal_desc = prop.get("legal_description") or contract_inputs.get("legal_description", "")
    if legal_desc:
        x, y0 = COORDS["p1"]["legal_desc_top"]
        c.setFont("Helvetica", 9)
        # Wrap to ~95 chars per line, max 3 lines
        words = legal_desc.split()
        lines, cur = [], ""
        for w in words:
            if len(cur) + len(w) + 1 <= 95:
                cur = (cur + " " + w).strip()
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for i, line in enumerate(lines[:3]):
            c.drawString(x, y0 - i * 11, line)
        c.setFont("Helvetica", 10)

    # Money fields (right-aligned to land in the $ blanks)
    def _money_right(field: str, amount: float):
        x, y = COORDS["p1"][field]
        text = _money(amount)
        # right-align by computing string width
        w = c.stringWidth(text, "Helvetica", 10)
        c.drawString(x - w, y, text)

    _money_right("purchase_price", purchase_price)
    _money_right("initial_deposit", initial_deposit)
    _money_right("additional_deposit", additional_deposit)
    _money_right("balance_to_close", balance_to_close)

    # Escrow agent / title company info
    if title_company:
        x, y = COORDS["p1"]["escrow_name"]
        c.drawString(x, y, _safe(title_company.get("name"))[:60])
        x, y = COORDS["p1"]["escrow_phone"]
        c.drawString(x, y, _safe(title_company.get("phone"))[:30])
        x, y = COORDS["p1"]["escrow_email"]
        c.drawString(x, y, _safe(title_company.get("email"))[:60])

    # Time for acceptance (Effective Date proxy) — print today's date as the
    # offer date so the rep doesn't have to think about it.
    eff_date = contract_inputs.get("effective_date") or ""
    if eff_date:
        x, y = COORDS["p1"]["effective_offer_date"]
        c.drawString(x, y, str(eff_date))

    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 2 — closing date + assignability + financing
    # ------------------------------------------------------------------
    # Mask prior "30 days after contract execution" overlay on closing line
    c.setFillColor(white)
    c.rect(150, 722, 290, 14, fill=1, stroke=0)
    c.setFillColor(black)

    c.setFont("Helvetica", 10)
    # Closing date language (locked per Jo's rule)
    x, y = COORDS["p2"]["closing_date_text"]
    c.drawString(x, y, "30 days from execution")

    # §7 — "may assign but not be released" — always checked
    x, y = COORDS["p2"]["assign_check"]
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "X")

    # §8(a) — Cash transaction — always checked
    x, y = COORDS["p2"]["cash_check"]
    c.drawString(x, y, "X")
    c.setFont("Helvetica", 10)

    c.showPage()

    # ------------------------------------------------------------------
    # PAGES 3, 4 — pass-through (no overlay)
    # ------------------------------------------------------------------
    c.showPage()
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 5 — inspection period
    # ------------------------------------------------------------------
    c.setFont("Helvetica", 10)
    inspection_days = int(contract_inputs.get("inspection_days") or 10)
    x, y = COORDS["p5"]["inspection_days"]
    c.drawString(x, y, str(inspection_days))
    c.showPage()

    # ------------------------------------------------------------------
    # PAGES 6 – 10 — pass-through
    # ------------------------------------------------------------------
    for _ in range(5):
        c.showPage()

    # ------------------------------------------------------------------
    # PAGE 11 — addenda checklist (only check LBP if pre-1978)
    # ------------------------------------------------------------------
    if contract_inputs.get("lbp_required"):
        c.setFont("Helvetica-Bold", 12)
        x, y = COORDS["p11"]["lbp_addenda_check"]
        c.drawString(x, y, "X")
        c.setFont("Helvetica", 10)
    c.showPage()

    # Pages 12 (no overlay — section 20 boilerplate is pre-printed)
    c.showPage()

    # ------------------------------------------------------------------
    # PAGE 13 — signature blocks (pre-print Buyer + Seller name; signatures stay blank)
    # ------------------------------------------------------------------
    c.setFont("Helvetica", 10)
    x, y = COORDS["p13"]["buyer_signature_name"]
    c.drawString(x, y, BUYER_NAME)
    if seller_name:
        x, y = COORDS["p13"]["seller_signature_name"]
        c.drawString(x, y, seller_name)
    c.showPage()

    # Page 14 — FinCEN addendum, no overlay
    c.showPage()

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# LBP rider (separate PDF appended after the main contract)
# ---------------------------------------------------------------------------
def _build_lbp_rider(deal_inputs: Dict[str, Any]) -> BytesIO:
    """Generate the standard HUD-compliant Lead-Based Paint Disclosure for
    pre-1978 properties. Returns a PDF as BytesIO."""
    seller = deal_inputs.get("seller", {}) or {}
    prop = deal_inputs.get("property", {}) or {}
    property_address = ", ".join(filter(None, [
        prop.get("address", ""),
        prop.get("city", ""),
        f'{prop.get("state","FL")} {prop.get("zip","")}'.strip(),
    ]))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=13, alignment=TA_CENTER, spaceAfter=6,
        textColor=HexColor("#1F4E78"),
    )
    sub = ParagraphStyle(
        "Sub", parent=styles["Normal"], fontName="Helvetica-Oblique",
        fontSize=9, alignment=TA_CENTER, spaceAfter=10,
        textColor=HexColor("#555555"),
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=10.5, spaceBefore=8, spaceAfter=3,
        textColor=HexColor("#1F4E78"),
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, leading=11, spaceAfter=4, alignment=TA_LEFT,
    )
    warn_box = ParagraphStyle(
        "Warn", parent=body, fontName="Helvetica-Bold", fontSize=9, leading=11,
        borderColor=HexColor("#666666"), borderWidth=0.7, borderPadding=6,
        spaceAfter=10,
    )

    story = []
    story.append(Paragraph(
        "DISCLOSURE OF INFORMATION ON LEAD-BASED PAINT "
        "AND/OR LEAD-BASED PAINT HAZARDS", title))
    story.append(Paragraph(
        "Federal law (Title X, Section 1018) requires this disclosure for "
        "residential properties built before 1978.", sub))

    # Property identification
    story.append(Paragraph("Property Address", h2))
    story.append(Paragraph(property_address or "(see attached contract)", body))

    # Lead Warning Statement
    story.append(Paragraph("Lead Warning Statement", h2))
    story.append(Paragraph(
        "Every purchaser of any interest in residential real property on which "
        "a residential dwelling was built prior to 1978 is notified that such "
        "property may present exposure to lead from lead-based paint that may "
        "place young children at risk of developing lead poisoning. Lead "
        "poisoning in young children may produce permanent neurological "
        "damage, including learning disabilities, reduced intelligence "
        "quotient, behavioral problems, and impaired memory. Lead poisoning "
        "also poses a particular risk to pregnant women. The seller of any "
        "interest in residential real property is required to provide the "
        "buyer with any information on lead-based paint hazards from risk "
        "assessments or inspections in the seller's possession and notify the "
        "buyer of any known lead-based paint hazards. A risk assessment or "
        "inspection for possible lead-based paint hazards is recommended "
        "prior to purchase.",
        warn_box,
    ))

    # Seller's Disclosure
    story.append(Paragraph("Seller's Disclosure", h2))
    story.append(Paragraph(
        "<b>(a) Presence of lead-based paint and/or lead-based paint hazards "
        "(check (i) or (ii) below):</b>", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (i) ☐ &nbsp;Known lead-based paint and/or "
        "lead-based paint hazards are present in the housing (explain):", body))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;_______________________________________________________________", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (ii) ☒ &nbsp;Seller has no knowledge of "
        "lead-based paint and/or lead-based paint hazards in the housing.", body))
    story.append(Paragraph(
        "<b>(b) Records and reports available to the Seller "
        "(check (i) or (ii) below):</b>", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (i) ☐ &nbsp;Seller has provided the "
        "purchaser with all available records and reports pertaining to "
        "lead-based paint and/or lead-based paint hazards in the housing "
        "(list documents):", body))
    story.append(Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;_______________________________________________________________", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (ii) ☒ &nbsp;Seller has no reports or "
        "records pertaining to lead-based paint and/or lead-based paint "
        "hazards in the housing.", body))

    # Purchaser's Acknowledgment
    story.append(Paragraph("Purchaser's Acknowledgment", h2))
    story.append(Paragraph(
        "(c) Purchaser has received copies of all information listed above.", body))
    story.append(Paragraph(
        "(d) Purchaser has received the pamphlet "
        "<i>Protect Your Family From Lead in Your Home</i>.", body))
    story.append(Paragraph(
        "(e) Purchaser has (check (i) or (ii) below):", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (i) ☐ &nbsp;Received a 10-day opportunity "
        "(or mutually agreed upon period) to conduct a risk assessment or "
        "inspection for the presence of lead-based paint and/or lead-based "
        "paint hazards; or", body))
    story.append(Paragraph(
        "&nbsp;&nbsp;&nbsp;&nbsp; (ii) ☒ &nbsp;Waived the opportunity to "
        "conduct a risk assessment or inspection for the presence of "
        "lead-based paint and/or lead-based paint hazards.", body))

    # Agent's Acknowledgment
    story.append(Paragraph("Agent's Acknowledgment", h2))
    story.append(Paragraph(
        "(f) Agent has informed the seller of the seller's obligations under "
        "42 U.S.C. § 4852d and is aware of his/her responsibility to ensure "
        "compliance.", body))

    # Certification
    story.append(Paragraph("Certification of Accuracy", h2))
    story.append(Paragraph(
        "The following parties have reviewed the information above and "
        "certify, to the best of their knowledge, that the information they "
        "have provided is true and accurate.",
        body,
    ))

    # Signature lines
    story.append(Spacer(1, 10))
    sig_data = [
        ["Seller", "Date", "Seller", "Date"],
        ["_____________________", "____________",
         "_____________________", "____________"],
        ["Purchaser — NSGC Investing Services, Inc.", "Date",
         "Purchaser", "Date"],
        ["_____________________", "____________",
         "_____________________", "____________"],
        ["Agent", "Date", "Agent", "Date"],
        ["_____________________", "____________",
         "_____________________", "____________"],
    ]
    t = Table(sig_data, colWidths=[2.7 * inch, 0.8 * inch,
                                   2.4 * inch, 0.8 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)

    doc.build(story)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_contract_pdf(
    deal: Dict[str, Any],
    contract_inputs: Dict[str, Any],
    title_company: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Return a filled Purchase & Sale Agreement PDF as bytes.

    Args:
        deal: the saved deal record (with `inputs` dict containing
            `property` and `seller` sub-dicts).
        contract_inputs: form fields from the Prepare Contract page —
            {purchase_price, additional_deposit, inspection_days,
             effective_date (str MM/DD/YYYY), lbp_required (bool),
             parcel_folio, legal_description, county}
        title_company: {name, contact_name, phone, email, address} for the
            escrow agent block.
    """
    deal_inputs = deal.get("inputs") if isinstance(deal, dict) else {}
    deal_inputs = deal_inputs or {}

    # Decide whether the LBP rider is needed
    year_built = (deal_inputs.get("property", {}) or {}).get("year") or 9999
    try:
        lbp_required = int(year_built) < 1978
    except (TypeError, ValueError):
        lbp_required = False
    contract_inputs = dict(contract_inputs)
    contract_inputs.setdefault("lbp_required", lbp_required)

    # 1. Build overlay PDF with the same page count as the template
    overlay_buf = _build_overlay(
        deal_inputs, contract_inputs, title_company or {},
    )

    # 2. Merge each overlay page onto the corresponding template page
    template_reader = pypdf.PdfReader(str(TEMPLATE_PATH))
    overlay_reader = pypdf.PdfReader(overlay_buf)
    writer = pypdf.PdfWriter()

    n_template = len(template_reader.pages)
    for i in range(n_template):
        base = template_reader.pages[i]
        if i < len(overlay_reader.pages):
            base.merge_page(overlay_reader.pages[i])
        writer.add_page(base)

    # 3. Append the LBP rider when required
    if contract_inputs["lbp_required"]:
        rider_buf = _build_lbp_rider(deal_inputs)
        rider_reader = pypdf.PdfReader(rider_buf)
        for page in rider_reader.pages:
            writer.add_page(page)

    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()
