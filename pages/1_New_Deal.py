"""New Deal Analysis — the centerpiece page.

Workflow:
  1. Property details (form)
  2. Comps (file upload + editable candidates table → Suggested ARV)
  3. Rehab toggles (per-line YES/NO with type selectors)
  4. Seller & loan info (form)
  5. Novation Parameters (collapsed, with defaults)
  6. Live recommendation (auto-updates on every change)
  7. Save / export memo
"""
import streamlit as st
import pandas as pd
from io import BytesIO
from modules.auth import require_login, sidebar_account_widget
from modules.strategy import (compute_recommendation, DEFAULTS,
                              rehab_subtotal, rehab_with_contingency,
                              rehab_breakdown)
from modules.comp_import import parse_comp_file, suggested_arv
from modules.memo import build_word_memo, build_pdf_memo
from modules.db import save_deal

st.set_page_config(page_title="New Deal", page_icon="📝", layout="wide")
user = require_login()
sidebar_account_widget()

st.title("📝 New Deal Analysis")

# ============================================================================
# 1. PROPERTY DETAILS
# ============================================================================
st.markdown("### 1. Property Details")

c1, c2, c3 = st.columns([3, 1, 1])
address = c1.text_input("Address", key="address",
                        placeholder="8420 SW 152nd St")
city = c2.text_input("City", value="Miami", key="city")
state = c3.text_input("State", value="FL", key="state")

c1, c2, c3, c4 = st.columns(4)
zip_code = c1.text_input("ZIP", key="zip")
beds = c2.number_input("Beds", min_value=0, max_value=20, value=3, key="beds")
baths = c3.number_input("Baths", min_value=0.0, max_value=20.0, step=0.5,
                        value=2.0, key="baths")
sqft = c4.number_input("Living Sqft", min_value=0, value=1500, step=50, key="sqft")

c1, c2, c3, c4 = st.columns(4)
year = c1.number_input("Year Built", min_value=1900, max_value=2030,
                       value=1980, key="year")
pool = c2.selectbox("Pool?", ["No", "Yes"], key="pool")
hoa = c3.number_input("HOA Monthly", min_value=0, value=0, step=25, key="hoa")
asking = c4.number_input("Seller's Asking Price",
                         min_value=0, value=0, step=1000, key="asking",
                         help="What the seller publicly wants for the property.")

property_dict = {
    "address": address, "city": city, "state": state, "zip": zip_code,
    "beds": beds, "baths": baths, "sqft": sqft, "year": year,
    "pool": pool, "hoa": hoa, "asking": asking,
}

# ============================================================================
# 2. COMPS
# ============================================================================
st.markdown("---")
st.markdown("### 2. Comps")

if "comps_df" not in st.session_state:
    st.session_state.comps_df = None

upload = st.file_uploader(
    "Upload your realtor's comp report (PDF, CSV, or Excel)",
    type=["pdf", "csv", "xlsx", "xls"],
    help="Auto-parses common MLS export formats and PDF Comparable Sales Reports "
         "(PropStream-style). Populates the candidates table below — uncheck rows "
         "that don't fit, and Suggested ARV recalculates.",
)
if upload is not None:
    try:
        comps = parse_comp_file(upload, filename=upload.name)
        # Build the candidates DataFrame
        df = pd.DataFrame(comps)
        df.insert(0, "use", True)
        st.session_state.comps_df = df
        st.success(f"Imported {len(df)} comp candidates.")
    except Exception as e:
        st.error(f"Could not parse the file: {e}")

if st.session_state.comps_df is not None and not st.session_state.comps_df.empty:
    st.write("**Candidate comps** — uncheck rows you don't want to use.")
    display_cols = ["use", "address", "city", "sqft", "beds", "baths",
                    "year", "sold_price", "sold_date", "distance", "notes"]
    df = st.session_state.comps_df
    for col in display_cols:
        if col not in df.columns: df[col] = None

    edited = st.data_editor(
        df[display_cols],
        column_config={
            "use": st.column_config.CheckboxColumn("Use?", default=True),
            "sold_price": st.column_config.NumberColumn(
                "Sold Price", format="$%d"),
            "sqft": st.column_config.NumberColumn("Sqft", format="%d"),
            "distance": st.column_config.NumberColumn("Distance (mi)", format="%.1f"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="comps_editor",
    )
    st.session_state.comps_df = edited

    # Compute ARV from selected comps
    selected = edited[edited["use"] == True].to_dict("records") if "use" in edited.columns else edited.to_dict("records")
    arv_info = suggested_arv(selected, subject_sqft=sqft)

    cA, cB, cC, cD = st.columns(4)
    cA.metric("Avg sale", f"${arv_info['avg_sale']:,.0f}")
    cB.metric("Median sale", f"${arv_info['median_sale']:,.0f}")
    cC.metric("Avg $/sqft × subject", f"${arv_info['avg_psf_times_sqft']:,.0f}")
    cD.metric("Median $/sqft × subject", f"${arv_info['median_psf_times_sqft']:,.0f}")

    # ARV calculation method — Option D: user picks per deal
    method_to_value = {
        "Suggested (avg of all methods)": arv_info["suggested"],
        "Avg sale price (ignores sqft)": arv_info["avg_sale"],
        "Median sale price (ignores sqft)": arv_info["median_sale"],
        "Avg $/sqft × subject sqft": arv_info["avg_psf_times_sqft"],
        "Median $/sqft × subject sqft": arv_info["median_psf_times_sqft"],
    }
    arv_method = st.radio(
        "ARV calculation method",
        options=list(method_to_value.keys()),
        index=0,
        help=("Pick which method the tool uses to populate ARV. The $/sqft methods "
              "are more accurate when your subject differs in size from the comps."),
    )
    chosen_arv = method_to_value[arv_method]
    st.success(f"**{arv_method}** → **${chosen_arv:,.0f}**")
    st.session_state.suggested_arv = chosen_arv
else:
    st.info("Upload a comp file above, or enter ARV manually below.")
    st.session_state.suggested_arv = 0

# ARV override
c1, c2 = st.columns([1, 3])
arv = c1.number_input(
    "ARV to use",
    min_value=0,
    value=int(round(st.session_state.get("suggested_arv", 0) or 0)),
    step=1000,
    key="arv",
    help="Defaults to Suggested ARV from comps; override with your own judgment.",
)
c2.markdown("&nbsp;\n*This is the ARV used in all downstream calculations.*")

# ============================================================================
# 3. REHAB TOGGLES
# ============================================================================
st.markdown("---")
st.markdown("### 3. Rehab Estimate")

# Use session state to maintain rehab dict across reruns
if "rehab" not in st.session_state:
    st.session_state.rehab = {}

def _toggle(key: str, label: str, type_options=None, default_type=None,
            qty_default=None, manual_amount=False):
    """Render a rehab toggle row. Returns updated config dict."""
    cols = st.columns([1, 2, 2, 2])
    include = cols[0].checkbox(label, key=f"rehab_{key}_include",
                                value=st.session_state.rehab.get(key, {}).get("include", False))
    cfg = {"include": include}
    if include:
        if type_options:
            t = cols[1].selectbox(
                "Type", type_options, key=f"rehab_{key}_type",
                index=(type_options.index(default_type) if default_type in type_options else 0),
                label_visibility="collapsed",
            )
            cfg["type"] = t
        if qty_default is not None:
            qty = cols[2].number_input(
                "Qty", min_value=0, value=qty_default, key=f"rehab_{key}_qty",
                label_visibility="collapsed", step=1,
            )
            cfg["qty"] = qty
        if manual_amount:
            amt = cols[2].number_input(
                "$ Amount", min_value=0, value=0, step=500,
                key=f"rehab_{key}_amount", label_visibility="collapsed",
            )
            cfg["amount"] = amt
    st.session_state.rehab[key] = cfg
    return cfg

with st.container(border=True):
    _toggle("roof", "Roof", ["Shingle", "Tile", "Flat"], "Shingle")
    _toggle("electrical", "Electrical",
            ["Standard misc work", "Replace Breaker Box", "Full (panel + misc)"],
            "Standard misc work")
    _toggle("ac", "A/C")
    _toggle("kitchen", "Kitchen")
    _toggle("bathrooms", "Bathrooms")
    _toggle("interior_paint", "Interior Paint",
            ["Knockdown + Paint", "Paint only", "Knockdown only"], "Knockdown + Paint")
    _toggle("exterior_paint", "Exterior Paint")
    _toggle("flooring", "Flooring (Luxury Vinyl)")
    _toggle("doors", "Doors", ["Exterior Replace", "Interior Replace", "Patch & Paint"],
            "Interior Replace", qty_default=2)
    _toggle("windows", "Windows / Shutters",
            ["Non-Impact", "Impact", "New Shutter", "Replace Shutter"],
            "Non-Impact", qty_default=8)
    _toggle("plumbing", "Plumbing", manual_amount=True)
    _toggle("landscaping", "Landscaping")
    _toggle("appliances", "Appliances")
    if pool == "Yes":
        _toggle("pool", "Pool",
                ["Replace Motor", "Replace Pump", "Heater",
                 "Waterline Tile", "Diamond Brite"], "Replace Motor")

# Live rehab total + line-item breakdown
sub_total = rehab_subtotal(st.session_state.rehab, sqft, baths, pool == "Yes")
total_rehab = rehab_with_contingency(sub_total)
contingency = total_rehab - sub_total
c1, c2, c3 = st.columns(3)
c1.metric("Subtotal", f"${sub_total:,.0f}")
c2.metric("Contingency", f"${contingency:,.0f}",
          help="10% if subtotal > $50k, otherwise $5,000")
c3.metric("Total Rehab", f"${total_rehab:,.0f}")

# Line-item breakdown — shows each included item with its computed amount
items = rehab_breakdown(st.session_state.rehab, sqft, baths, pool == "Yes")
if items:
    with st.expander("📋 Rehab line items (click to expand)", expanded=True):
        for label, amount in items:
            cA, cB = st.columns([4, 1])
            cA.write(f"• {label}")
            cB.write(f"**${amount:,.0f}**")
        st.markdown("---")
        cA, cB = st.columns([4, 1])
        cA.write("**Subtotal**")
        cB.write(f"**${sub_total:,.0f}**")
        cA, cB = st.columns([4, 1])
        cA.write(f"Contingency ({'10%' if sub_total > 50000 else '$5,000 flat'})")
        cB.write(f"${contingency:,.0f}")
        cA, cB = st.columns([4, 1])
        cA.write("**TOTAL REHAB**")
        cB.write(f"**${total_rehab:,.0f}**")
else:
    items = []

# ============================================================================
# 4. SELLER & LOAN INFO
# ============================================================================
st.markdown("---")
st.markdown("### 4. Seller & Loan Info")

c1, c2, c3 = st.columns(3)
mtg1 = c1.number_input("1st Mortgage Balance", min_value=0, value=0, step=1000)
mtg2 = c2.number_input("2nd Mortgage / HELOC", min_value=0, value=0, step=1000)
other_liens = c3.number_input("Other Liens (tax, mechanic, code)",
                              min_value=0, value=0, step=500)

c1, c2, c3 = st.columns(3)
payment_status = c1.selectbox("Payment Status",
                              ["Current", "30+", "60+", "90+", "NOD", "Foreclosure"])
required_net = c2.number_input("Seller's Required Net Price",
                               min_value=0, value=0, step=1000,
                               help="What the seller needs to walk with. Drives novation.")
timeline = c3.number_input("Timeline (days to close)", min_value=0, value=30)

c1, c2 = st.columns(2)
reason = c1.selectbox("Reason for Selling",
                     ["Other", "Financial distress", "Divorce", "Probate",
                      "Relocation", "Tired landlord", "Health"])
occupancy = c2.selectbox("Occupancy", ["Vacant", "Owner", "Tenant"])

c1, c2, c3, c4 = st.columns(4)
condition_confirmed = c1.selectbox("Condition Confirmed?", ["Yes", "No"], index=1)
buyer_demand = c2.selectbox("End-Buyer Demand?", ["No", "Yes"])
assignable = c3.selectbox("Contract Assignable?", ["Yes", "No"])
buyer_prefers_dc = c4.selectbox("End Buyer Prefers DC?", ["No", "Yes"])
open_to_mls = st.selectbox("Seller Open to MLS Listing?", ["Yes", "No"])

seller_dict = {
    "mtg1": mtg1, "mtg2": mtg2, "other_liens": other_liens,
    "payment_status": payment_status, "required_net": required_net,
    "timeline": timeline, "reason": reason, "occupancy": occupancy,
    "condition_confirmed": condition_confirmed, "buyer_demand": buyer_demand,
    "assignable": assignable, "buyer_prefers_dc": buyer_prefers_dc,
    "open_to_mls": open_to_mls,
}

# ============================================================================
# 5. NOVATION PARAMETERS (collapsible)
# ============================================================================
with st.expander("⚙️ Advanced: Novation parameters (tunable)"):
    c1, c2 = st.columns(2)
    nov_retail_pct = c1.number_input(
        "Retail Transaction Costs (%)",
        value=DEFAULTS["novation_retail_costs_pct"] * 100, step=0.5)
    nov_holding = c2.number_input(
        "Novation Holding/Capital Costs ($)",
        value=DEFAULTS["novation_holding_costs"], step=500)
    c1, c2 = st.columns(2)
    nov_floor = c1.number_input(
        "Novation Min Profit Floor ($)",
        value=DEFAULTS["novation_min_floor"], step=1000)
    nov_target = c2.number_input(
        "Novation Preferred Target ($)",
        value=DEFAULTS["novation_preferred_target"], step=1000)

params_override = {
    "novation_retail_costs_pct": nov_retail_pct / 100,
    "novation_holding_costs": nov_holding,
    "novation_min_floor": nov_floor,
    "novation_preferred_target": nov_target,
}

# ============================================================================
# RECOMMENDATION (auto-computed)
# ============================================================================
st.markdown("---")
st.markdown("## 🎯 Recommendation")

inputs_dict = {
    "property": property_dict,
    "arv": arv,
    "rehab": st.session_state.rehab,
    "seller": seller_dict,
    "params": params_override,
}
rec = compute_recommendation(inputs_dict)

# Headline banner
banner_color = "#C6EFCE"  # green default
strat = rec["strategy"]
if "Pass" in strat or "NO-GO" in strat:
    banner_color = "#FCE4D6"  # orange
elif "Marginal" in strat:
    banner_color = "#FFF2CC"  # yellow

st.markdown(
    f"""
    <div style="background-color:{banner_color}; padding:20px; border-radius:8px;
                border-left:6px solid #1F4E78; margin-bottom:16px;">
        <div style="font-size:11px; color:#666; font-weight:bold;">RECOMMENDED STRATEGY</div>
        <div style="font-size:24px; font-weight:bold; color:#1F4E78; margin:4px 0;">
            {strat}
        </div>
        <div style="font-size:13px; color:#333; font-style:italic;">
            {rec['rationale']}
        </div>
    </div>
    """, unsafe_allow_html=True)

# Offer numbers
if not (strat.startswith("Pass") or strat == "NO-GO — Pass" or strat == "MLS Referral"):
    c1, c2, c3 = st.columns(3)
    c1.metric("Opening Offer", f"${rec['opening_offer']:,.0f}")
    c2.metric("Walk-Away (MAO)", f"${rec['walk_away']:,.0f}")
    c3.metric("Stretch Ceiling", f"${rec['stretch_ceiling']:,.0f}")
    if rec["target_assignment_fee"] is not None:
        st.info(f"**Target Assignment Fee: ${rec['target_assignment_fee']:,.0f}** "
                f"— {rec['fat_fee_note']}")

# Disposition
st.markdown(f"**Disposition:** {rec['disposition']}")

# Action items
if rec["action_items"]:
    st.markdown("**Action Items:**")
    for i, a in enumerate(rec["action_items"], 1):
        st.markdown(f"{i}. {a}")

# Snapshot
st.markdown("### Key Numbers")
c1, c2, c3, c4 = st.columns(4)
c1.metric("ARV", f"${rec['arv']:,.0f}")
c2.metric("Total Rehab", f"${rec['rehab_total']:,.0f}")
c3.metric("Net Profit", f"${rec['net_profit']:,.0f}")
c4.metric("ROI", f"{rec['roi']:.1%}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Cash Offer", f"${rec['cash_offer']:,.0f}")
c2.metric("Wholesale Offer", f"${rec['wholesale_offer']:,.0f}")
c3.metric("Total Project Cost", f"${rec['total_project_cost']:,.0f}")
c4.metric("Deal Status", rec["deal_status"])

# Diagnostics (collapsed)
with st.expander("🔍 Diagnostics — decision logic flags"):
    diag = {
        "Profit Band": rec["profit_band"],
        "Effective Scope Severity": rec["scope_severity"],
        "Distress Flag": "YES" if rec["distress_flag"] else "no",
        "Equity Position": f"${rec['equity']:,.0f}",
        "Gap (Asking − Cash MAO)": f"${rec['gap']:,.0f}",
        "Gap Category": rec["gap_category"],
        "Novation Feasible?": "YES" if rec["novation_feasible"] else "no",
        "Projected Novation Profit": f"${rec['novation_profit']:,.0f}",
        "Max Asking for Novation": f"${rec['novation_max_asking']:,.0f}",
        "MLS Referral Feasible?": "YES" if rec["mls_feasible"] else "no",
        "Est. MLS Commission": f"${rec['mls_commission_estimate']:,.0f}",
        "Effective Benchmark (for Novation)": f"${rec['benchmark']:,.0f}",
    }
    for k, v in diag.items():
        st.write(f"- **{k}:** {v}")

# ============================================================================
# SAVE & EXPORT
# ============================================================================
st.markdown("---")
st.markdown("### 💾 Save & Export")
c1, c2, c3, c4 = st.columns(4)

if c1.button("Save deal to history", type="primary", use_container_width=True):
    if not address:
        st.error("Enter an address before saving.")
    else:
        deal_id = save_deal(inputs_dict, rec, user_email=user.get("email"))
        st.success(f"Deal #{deal_id} saved.")

# Word memo (with rehab line items)
try:
    word_bytes = build_word_memo(property_dict, rec, seller_dict, rehab_items=items)
    c2.download_button(
        "Download Word memo", word_bytes,
        file_name=f"Exodus_Memo_{(address or 'deal').replace(' ','_')}.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )
except Exception as e:
    c2.error(f"Word error: {e}")

# PDF memo (with rehab line items)
try:
    pdf_bytes = build_pdf_memo(property_dict, rec, seller_dict, rehab_items=items)
    c3.download_button(
        "Download PDF memo", pdf_bytes,
        file_name=f"Exodus_Memo_{(address or 'deal').replace(' ','_')}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )
except Exception as e:
    c3.error(f"PDF error: {e}")

if c4.button("Reset form", use_container_width=True):
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()
