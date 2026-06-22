"""Prepare Contract — generates a filled Florida AS-IS Residential
Purchase & Sale Agreement PDF for the current deal.

Page is reached by clicking "📄 Prepare Contract" on either the New Deal
or Past Deals page. Loads the deal via session_state["contract_deal_id"]
and renders a form for the few remaining inputs that aren't already on
the deal record: purchase price, title company (dropdown from Admin),
inspection period, and effective date.

Locked / always-true business rules per Jo (v23):
  - Buyer is always "NSGC Investing Services, Inc."
  - Initial deposit is always 1% of purchase price (computed, read-only)
  - Closing date is always "30 days from execution" (printed verbatim)
  - Financing is always Cash (§8(a) checkbox)
  - Assignability is always "may assign but not be released" (§7 box 2)
  - If year built < 1978, Lead-Based Paint Disclosure rider is appended
    AND the §19 P checkbox is checked

Access: Admin + Manager only. Agents can't generate contracts directly.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st

from modules import settings as st_settings
from modules.auth import require_login, sidebar_account_widget
from modules.contract import build_contract_pdf
from modules.db import get_deal


st.set_page_config(page_title="Prepare Contract",
                   page_icon="📄", layout="wide")
user = require_login()
sidebar_account_widget()


# --- Access control ----------------------------------------------------
# Only Admin + Manager can generate contracts. Agents see a friendly
# message and are blocked.
_email = user.get("email", "") if isinstance(user, dict) else ""
if not st_settings.can_view_admin(_email):
    st.title("⛔ Contract preparation restricted")
    st.error(
        f"`{_email}` doesn't have permission to prepare contracts. "
        "Contact Jo or Victor if you should have access."
    )
    st.stop()


# --- Load the deal -----------------------------------------------------
deal_id = st.session_state.get("contract_deal_id")
if not deal_id:
    st.title("📄 Prepare Contract")
    st.warning(
        "No deal loaded. Open a saved deal from the **📝 New Deal** or "
        "**📚 Past Deals** page and click **📄 Prepare Contract**."
    )
    st.stop()

deal = get_deal(int(deal_id))
if not deal:
    st.error(f"Deal #{deal_id} was not found.")
    st.stop()

inputs = deal.get("inputs", {}) or {}
outputs = deal.get("outputs", {}) or {}
prop = inputs.get("property", {}) or {}
seller = inputs.get("seller", {}) or {}


# --- Header ------------------------------------------------------------
st.title("📄 Prepare Contract")
address_line = ", ".join(filter(None, [
    prop.get("address", ""),
    prop.get("city", ""),
    f'{prop.get("state","FL")} {prop.get("zip","")}'.strip(),
]))
st.markdown(
    f"### {address_line}  &nbsp;&nbsp; "
    f"<span style='color:#666; font-weight:400; font-size:0.9em;'>"
    f"Deal #{deal['id']}</span>",
    unsafe_allow_html=True,
)


# --- Verify the required deal-level data is present --------------------
_missing = []
if not seller.get("name"):
    _missing.append("Seller name")
if not prop.get("parcel_folio"):
    _missing.append("Property Tax ID / Folio #")
if not prop.get("legal_description"):
    _missing.append("Legal Description")
if not prop.get("county"):
    _missing.append("County")

if _missing:
    st.warning(
        "⚠️ This deal is missing the following contract-required fields. "
        "Go back to **📝 New Deal**, open the deal in the editor, expand "
        "**📄 Contract Info**, fill them in, then **Save changes**:\n\n- "
        + "\n- ".join(_missing)
    )


# --- Default values for the form ---------------------------------------
# Purchase Price starts BLANK so the rep consciously enters the negotiated
# number. The Cash MAO and seller's asking are shown as reference figures
# above the form, but neither pre-fills the field.
default_purchase_price = 0

# Reference numbers for the rep (display only — do not pre-fill the price)
ref_cash_mao = int(outputs.get("cash_offer_to_seller")
                    or outputs.get("cash_offer") or 0)
ref_asking = int(prop.get("asking") or 0)

# Year-built drives the LBP rider
year_built = prop.get("year") or 9999
try:
    lbp_required = int(year_built) < 1978
except (TypeError, ValueError):
    lbp_required = False


# ============================================================================
# Form — the few inputs not already on the deal record
# ============================================================================
st.markdown("---")
st.markdown("### Contract fields")

# Reference panel — Cash MAO + Asking shown for context, but neither
# pre-fills the Purchase Price field. The rep types in whatever was
# actually negotiated with the seller.
_ref_bits = []
if ref_cash_mao:
    _ref_bits.append(f"Cash MAO from underwriting: **${ref_cash_mao:,.0f}**")
if ref_asking:
    _ref_bits.append(f"Seller's asking: **${ref_asking:,.0f}**")
if _ref_bits:
    st.caption("📊 For reference — " + "  ·  ".join(_ref_bits))

c1, c2 = st.columns(2)
purchase_price = c1.number_input(
    "Purchase Price ($)",
    min_value=0, value=default_purchase_price, step=1000,
    key="contract_purchase_price",
    help="Enter the price you negotiated with the seller. The Cash MAO "
         "and asking price are shown above for reference but do not "
         "pre-fill this field — the negotiated number is whatever you "
         "and the seller agreed to.",
)
additional_deposit = c2.number_input(
    "Additional Deposit ($) — optional",
    min_value=0, value=0, step=500,
    key="contract_additional_deposit",
    help="Most deals leave this at $0. Use if the seller wants an additional "
         "deposit on top of the 1% initial.",
)

# Initial deposit is ALWAYS 1% of purchase price — display, don't edit.
initial_deposit = round(purchase_price * 0.01)
balance_to_close = max(0, purchase_price - initial_deposit - additional_deposit)
st.caption(
    f"💵 **Initial Deposit (locked, 1% of price): ${initial_deposit:,.2f}** "
    f"·&nbsp;Additional Deposit: ${additional_deposit:,.2f} "
    f"·&nbsp;Balance to close (wire): **${balance_to_close:,.2f}**"
)


# --- Title Company dropdown -------------------------------------------
title_companies = st_settings.get_setting("title_companies") or []
if not title_companies:
    st.error(
        "⚠️ No title companies have been added yet. An Admin needs to add "
        "at least one company on the **⚙️ Admin** page → "
        "**🏢 Title Companies** tab before contracts can be generated."
    )
    st.stop()

company_names = [tc.get("name", "") for tc in title_companies if tc.get("name")]
sel_name = st.selectbox(
    "Title Company / Escrow Agent",
    company_names,
    key="contract_title_company",
    help="Manage this list on the Admin page → 🏢 Title Companies tab.",
)
selected_tc = next((tc for tc in title_companies if tc.get("name") == sel_name),
                   {})
if selected_tc:
    st.caption(
        f"📞 {selected_tc.get('contact_name','')}  ·  "
        f"{selected_tc.get('phone','')}  ·  "
        f"{selected_tc.get('email','')}  ·  "
        f"{selected_tc.get('address','')}"
    )


# --- Remaining inputs -------------------------------------------------
c1, c2 = st.columns(2)
inspection_days = c1.number_input(
    "Inspection Period (days)",
    min_value=0, max_value=60, value=10, step=1,
    key="contract_inspection_days",
    help="Methodology default is 10 days. Form default if left blank on the "
         "actual contract template is 15.",
)
eff_date_input = c2.date_input(
    "Effective Date (offer date)",
    value=date.today(),
    key="contract_effective_date",
    help="Printed on the offer's time-for-acceptance line. Closing date is "
         "auto-set to '30 days from execution' regardless of this date.",
)


# --- Locked rules display ---------------------------------------------
with st.expander("🔒 Locked contract terms (always applied)", expanded=False):
    st.markdown(
        "- **Buyer**: NSGC Investing Services, Inc.\n"
        "- **§7 Assignability**: *may assign but not be released from liability under this Contract*\n"
        "- **§8 Financing**: Cash transaction (no financing contingency)\n"
        "- **Closing Date**: 30 days from execution\n"
        "- **Initial Deposit**: 1% of purchase price\n"
        f"- **Lead-Based Paint Disclosure**: "
        f"{'attached (year built ' + str(year_built) + ' is pre-1978)' if lbp_required else 'not required (year built ' + str(year_built) + ')'}\n"
        "- **Additional Terms (§20)**: pre-printed on the template (vacant at "
        "closing, open-permit cancellation right, buyer pays seller's closing "
        "costs except taxes/liens/utilities)."
    )


# --- Generate button --------------------------------------------------
st.markdown("---")
if st.button(
    "📄 Generate Contract PDF",
    type="primary", use_container_width=True,
    disabled=bool(_missing) or purchase_price <= 0,
):
    contract_inputs = {
        "purchase_price": purchase_price,
        "additional_deposit": additional_deposit,
        "inspection_days": int(inspection_days),
        "effective_date": eff_date_input.strftime("%m/%d/%Y"),
        "lbp_required": lbp_required,
    }
    try:
        pdf_bytes = build_contract_pdf(deal, contract_inputs, selected_tc)
        # Compose a filename based on deal address + date
        addr_slug = (prop.get("address", "Contract")
                     .replace(",", "").replace(" ", "_")[:60])
        fname = (f"Contract_{addr_slug}_"
                 f"{eff_date_input.strftime('%Y-%m-%d')}.pdf")
        st.success(
            f"✅ Contract generated "
            f"({len(pdf_bytes):,} bytes, "
            f"{'with' if lbp_required else 'without'} Lead-Based Paint "
            f"rider)."
        )
        st.download_button(
            "⬇️ Download Contract PDF",
            data=pdf_bytes,
            file_name=fname,
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"Failed to build the contract PDF: {e}")
        st.exception(e)


# --- Footer help ------------------------------------------------------
st.markdown("---")
st.caption(
    "ℹ️ The PDF is generated by overlaying onto the standard Florida "
    "Realtors / Florida Bar AS-IS template. All §20 Additional Terms are "
    "pre-printed on the template and preserved verbatim. The Initial "
    "Deposit is calculated dynamically — change the Purchase Price and the "
    "Deposit updates instantly."
)
