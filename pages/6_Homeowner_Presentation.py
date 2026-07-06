"""Homeowner Presentation — kitchen-table view of how we arrived at our offer.

Built to be opened in front of a seller and walked through together. Shows
only the Rehab math — buy → repair → sell → costs → minimum profit → offer.
No internal jargon, no strategy menu, no wholesale/DC/novation visibility.

The cash offer math is identical regardless of how we ultimately dispose of
the contract (assignment, DC, etc.) — it's the Cash MAO, which is what an
end-buyer would pay to rehab the property. So this page is honest even when
the actual disposition strategy ends up being something other than Rehab.

Visible to all roles (Admin / Manager / Agent) since agents often present
this at the kitchen table or over a screen share.
"""
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from modules.auth import require_login, sidebar_account_widget
from modules.db import get_deal
from modules.homeowner_pdf import build_homeowner_pdf
from modules.settlement_pdf import (build_settlement_pdf,
                                     build_options_comparison_pdf)
from modules.strategy import compute_recommendation, rehab_breakdown


st.set_page_config(page_title="Cash Offer Breakdown",
                   page_icon="🏠", layout="wide")
user = require_login()
sidebar_account_widget()


# --- Load the deal -----------------------------------------------------
deal_id = st.session_state.get("homeowner_deal_id")
if not deal_id:
    st.title("🏠 Cash Offer Breakdown")
    st.warning(
        "No deal loaded. Open a saved deal from the **📝 New Deal** or "
        "**📚 Past Deals** page and click **🏠 Show to Homeowner**."
    )
    st.stop()

deal = get_deal(int(deal_id))
if not deal:
    st.error(f"Deal #{deal_id} was not found.")
    st.stop()

inputs = deal.get("inputs", {}) or {}
prop = inputs.get("property", {}) or {}

# Recompute as REHAB strategy so every dollar figure here reflects the
# rehab math — even if the deal was auto-routed to wholesale/novation.
# We're showing the seller the buy-rehab-resell math because that's the
# math that determines what they'd be paid no matter what we do with the
# contract after we sign.
try:
    rec = compute_recommendation(inputs, force_strategy="Rehab")
except Exception as e:
    st.error(f"Could not compute the rehab numbers for this deal: {e}")
    st.stop()


# --- Extract the numbers ----------------------------------------------
arv = float(rec.get("arv", 0) or 0)
rehab_total = float(rec.get("rehab_total", 0) or 0)
purchase_closing = float(rec.get("purchase_closing_costs", 0) or 0)
sale_closing = float(rec.get("sale_closing_costs", 0) or 0)
holding = float(rec.get("total_holding", 0) or 0)
cost_of_money = float(rec.get("cost_of_money", 0) or 0)
our_costs = purchase_closing + sale_closing + holding + cost_of_money

# The seller-facing cash offer is clamped to never exceed asking. When
# asking < MAO, the gap becomes additional margin captured as profit.
cash_offer = float(
    rec.get("cash_offer_to_seller") or rec.get("cash_offer", 0) or 0
)

# Recompute the minimum profit so the math reconciles back to ARV.
# When asking < MAO, our actual profit = ARV − rehab − our_costs − asking,
# which is BIGGER than net_profit_at_mao. We show the seller our actual
# extracted margin (not the conservative MAO-floor) — that's honest:
# this is what we'd make on THIS deal if they accept our offer.
min_profit = arv - rehab_total - our_costs - cash_offer
if min_profit < 0:
    # Edge case (shouldn't happen on a GO deal) — fall back to MAO profit
    min_profit = float(
        rec.get("net_profit_at_mao") or rec.get("net_profit", 0) or 0
    )


# --- Header ------------------------------------------------------------
addr = prop.get("address", "your property")
city_state = ", ".join(filter(None, [prop.get("city", ""),
                                      prop.get("state", "")]))
loc_line = f"{addr}" + (f" — {city_state}" if city_state else "")

st.markdown(
    f"""
    <div style="text-align:center; padding:18px 0 8px 0;">
        <div style="font-size:13px; color:#666; font-weight:600;
                    letter-spacing:1px;">EXODUS PROPERTY SOLUTIONS</div>
        <h1 style="color:#1F4E78; margin:6px 0 0 0; font-size:36px;">
            🏠 Your Cash Offer — How We Got Here
        </h1>
        <div style="font-size:16px; color:#555; margin-top:6px;">
            {loc_line}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "We want to show you our exact math — from start to finish. No black box, "
    "no surprises."
)

# --- Download as PDF (top of page, prominent) -------------------------
# Generated on-the-fly with the same
