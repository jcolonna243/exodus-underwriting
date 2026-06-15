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

# Use net_profit_at_mao when present (asking < MAO case stamps both),
# else fall back to net_profit. We want the rehab-math minimum profit.
min_profit = float(
    rec.get("net_profit_at_mao") or rec.get("net_profit", 0) or 0
)

cash_offer = float(rec.get("cash_offer", 0) or 0)


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
    "no surprises. Use the **Print** option (⌘+P / Ctrl+P) to leave a copy "
    "with the homeowner."
)

st.markdown("---")


# --- Section 1: After-Repair Value -------------------------------------
st.markdown("## 1. What your house will be worth after improvements")
c_a, c_b = st.columns([1, 2])
c_a.markdown(
    f"""
    <div style="background:#F2F8FF; padding:24px; border-radius:8px;
                border-left:6px solid #1F4E78; text-align:center;">
        <div style="font-size:13px; color:#666; font-weight:600;">
            AFTER-REPAIR VALUE
        </div>
        <div style="font-size:42px; color:#1F4E78; font-weight:bold;
                    margin-top:6px;">
            ${arv:,.0f}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
c_b.markdown(
    f"""
    This is what we expect a fully renovated **{prop.get('beds', '—')} bed /
    {prop.get('baths', '—')} bath** home of your size
    ({prop.get('sqft', 0):,} sqft, built {prop.get('year', '—')}) to sell
    for in your neighborhood, based on the most recent comparable sales we
    could find.

    The actual sale-price evidence is below.
    """
)

# Show the comps that were saved with the deal (v13+ — stored in inputs)
comps_data = inputs.get("comps") or []
if comps_data:
    st.markdown("**Recent comparable sales we used to estimate your value:**")
    df_comps = pd.DataFrame(comps_data)
    # Only show comps the rep actually used (use=True), if that column exists
    if "use" in df_comps.columns:
        df_comps = df_comps[df_comps["use"].fillna(False)]
    # Pick the columns the seller would care about; gracefully skip missing ones
    display_cols = []
    for col in ["address", "city", "sqft", "beds", "baths", "year",
                "sold_price", "sold_date"]:
        if col in df_comps.columns:
            display_cols.append(col)
    if display_cols and not df_comps.empty:
        nice_names = {
            "address": "Address",
            "city": "City",
            "sqft": "Sqft",
            "beds": "Beds",
            "baths": "Baths",
            "year": "Year Built",
            "sold_price": "Sold For",
            "sold_date": "Sold Date",
        }
        show = df_comps[display_cols].rename(columns=nice_names).copy()
        if "Sold For" in show.columns:
            show["Sold For"] = show["Sold For"].apply(
                lambda v: f"${float(v):,.0f}" if pd.notna(v) and v else "—"
            )
        if "Sqft" in show.columns:
            show["Sqft"] = show["Sqft"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) and v else "—"
            )
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption("Comparable sale records are stored with this deal but "
                   "don't have the standard fields. Walk the seller through "
                   "them verbally or open the New Deal page to see the full "
                   "list.")
else:
    st.caption(
        "(Comparable sale records weren't saved with this deal. Walk the "
        "seller through your comps verbally, or save the deal again to "
        "capture them.)"
    )

st.markdown("---")


# --- Section 2: Renovation Cost ----------------------------------------
st.markdown("## 2. What it'll cost to bring it to top condition")
c_a, c_b = st.columns([1, 2])
c_a.markdown(
    f"""
    <div style="background:#FFF8F0; padding:24px; border-radius:8px;
                border-left:6px solid #C77;
                text-align:center;">
        <div style="font-size:13px; color:#666; font-weight:600;">
            ESTIMATED RENOVATION COST
        </div>
        <div style="font-size:42px; color:#8B4513; font-weight:bold;
                    margin-top:6px;">
            ${rehab_total:,.0f}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
c_b.markdown(
    "This is what we estimate it will cost to bring your home to the "
    "condition of the homes you just saw. Every line item is below — "
    "nothing inflated, nothing hidden."
)

# Pull the rehab line items the rep ticked on the deal
sqft_val = int(prop.get("sqft", 0) or 0)
baths_val = float(prop.get("baths", 0) or 0)
pool_val = (prop.get("pool", "No") == "Yes")
stories_val = prop.get("stories", 1) or 1
try:
    rehab_items = rehab_breakdown(
        inputs.get("rehab", {}) or {},
        sqft_val, baths_val, pool_val, stories=stories_val,
    )
except Exception:
    rehab_items = []

if rehab_items:
    items_df = pd.DataFrame(rehab_items, columns=["Item", "Cost"])
    items_df["Cost"] = items_df["Cost"].apply(lambda v: f"${float(v):,.0f}")
    st.dataframe(items_df, use_container_width=True, hide_index=True)

# Subtotal / contingency / total
sub = float(rec.get("rehab_subtotal", 0) or 0)
contingency = rehab_total - sub
contingency_label = "Contingency (10%)" if sub > 50_000 else "Contingency ($5,000 flat)"
c1, c2, c3 = st.columns(3)
c1.metric("Subtotal", f"${sub:,.0f}")
c2.metric(contingency_label, f"${contingency:,.0f}",
          help="A safety buffer for surprises during renovation — "
               "almost every project has them.")
c3.metric("Total renovation", f"${rehab_total:,.0f}")

st.markdown("---")


# --- Section 3: Our Cost of Doing the Deal -----------------------------
st.markdown("## 3. Our cost of doing the deal")
c_a, c_b = st.columns([1, 2])
c_a.markdown(
    f"""
    <div style="background:#FFF9E6; padding:24px; border-radius:8px;
                border-left:6px solid #C9A227; text-align:center;">
        <div style="font-size:13px; color:#666; font-weight:600;">
            OUR DEAL COSTS
        </div>
        <div style="font-size:42px; color:#9A7B1A; font-weight:bold;
                    margin-top:6px;">
            ${our_costs:,.0f}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
c_b.markdown(
    "These are the costs of being the buyer: closing costs when we buy "
    "from you, holding the property while we renovate (insurance, taxes, "
    "utilities, financing), and then closing again when we sell."
)

# Simple breakdown — no AB/BC jargon
cost_rows = []
if purchase_closing > 0:
    cost_rows.append(("Closing costs when we buy from you",
                       f"${purchase_closing:,.0f}"))
if holding > 0:
    cost_rows.append((f"Holding the property (~6 months — insurance, taxes, utilities)",
                       f"${holding:,.0f}"))
if cost_of_money > 0:
    cost_rows.append(("Financing costs (loan interest + fees)",
                       f"${cost_of_money:,.0f}"))
if sale_closing > 0:
    cost_rows.append(("Closing costs + agent commissions when we sell",
                       f"${sale_closing:,.0f}"))
if cost_rows:
    breakdown_df = pd.DataFrame(cost_rows, columns=["What it covers", "Amount"])
    st.dataframe(breakdown_df, use_container_width=True, hide_index=True)

st.markdown("---")


# --- Section 4: Our Minimum Profit (Jo's exact wording) ---------------
st.markdown("## 4. Our minimum profit")
c_a, c_b = st.columns([1, 2])
c_a.markdown(
    f"""
    <div style="background:#F0F8F2; padding:24px; border-radius:8px;
                border-left:6px solid #2E7D32; text-align:center;">
        <div style="font-size:13px; color:#666; font-weight:600;">
            MINIMUM PROFIT
        </div>
        <div style="font-size:42px; color:#2E7D32; font-weight:bold;
                    margin-top:6px;">
            ${min_profit:,.0f}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
c_b.markdown(
    "We want to create a win-win — those are the best deals. You walk "
    "away knowing exactly what you're going to make and that we've been "
    "transparent with you, with the understanding that we have to make a "
    "profit to keep this business running. **This is the minimum profit "
    "we require to make this deal happen.**"
)

st.markdown("---")


# --- Section 5: Your Cash Offer ---------------------------------------
st.markdown("## 5. Your cash offer")
st.markdown(
    f"""
    <div style="background:#1F4E78; padding:36px; border-radius:10px;
                text-align:center; margin: 12px 0;">
        <div style="font-size:14px; color:#B8D8EB; font-weight:600;
                    letter-spacing:1px;">YOUR HIGHEST CASH OFFER</div>
        <div style="font-size:72px; color:white; font-weight:bold;
                    margin-top:8px; line-height:1;">
            ${cash_offer:,.0f}
        </div>
        <div style="font-size:14px; color:#B8D8EB; margin-top:14px;">
            Cash. As-is. No repairs. No commissions. Close on your timeline.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Sanity check — these should add up to ARV
total_back = cash_offer + rehab_total + our_costs + min_profit
delta = arv - total_back
st.caption(
    f"**The math:** ${arv:,.0f} (after-repair value) − ${rehab_total:,.0f} "
    f"(renovation) − ${our_costs:,.0f} (our deal costs) − "
    f"${min_profit:,.0f} (our minimum profit) = **${cash_offer:,.0f}** "
    f"(your cash offer)."
    + (f"  *(Rounding: ${delta:,.0f})*" if abs(delta) > 50 else "")
)


# --- Footer ------------------------------------------------------------
st.markdown("---")
st.markdown(
    """
    <div style="text-align:center; color:#666; font-size:13px;
                padding: 8px 0 20px 0;">
        What you get: cash, certainty, and a closing date you choose. We pay
        the closing costs. You sell as-is — no cleaning, no repairs, no
        showings, no realtor commissions.
        <br><br>
        <b>Exodus Property Solutions</b>
    </div>
    """,
    unsafe_allow_html=True,
)

# Print hint
st.info(
    "💡 **To print or save as PDF:** press ⌘+P (Mac) or Ctrl+P (Windows) "
    "in your browser. Choose 'Save as PDF' to leave a copy with the homeowner."
)
