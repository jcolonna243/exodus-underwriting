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
from modules.strategy import (compute_recommendation, compute_alternatives,
                              DEFAULTS, rehab_subtotal,
                              rehab_with_contingency, rehab_breakdown)
from modules.comp_import import parse_comp_file, suggested_arv
from modules.memo import build_word_memo, build_pdf_memo
from modules.db import save_deal, save_chat_bulk
from modules import chat as chat_mod
from modules import property_lookup

st.set_page_config(page_title="New Deal", page_icon="📝", layout="wide")
user = require_login()
sidebar_account_widget()

# --- Reset Form handler --------------------------------------------------
# Streamlit's widget state persists across reruns even when we delete
# session_state keys at the END of a run. We clear here at the TOP of the
# next run instead — before any widgets render — which actually resets them.
if st.session_state.pop("_pending_reset", False):
    keys = [k for k in list(st.session_state.keys()) if not k.startswith("_st_")]
    for k in keys:
        try:
            del st.session_state[k]
        except Exception:
            pass
    st.toast("✅ Form reset.", icon="🧹")

st.title("📝 New Deal Analysis")

# ============================================================================
# 1. PROPERTY DETAILS
# ============================================================================
st.markdown("### 1. Property Details")

# Address + "Look up property" button on their own row
c_addr, c_btn = st.columns([5, 1])
address = c_addr.text_input(
    "Address", key="address",
    placeholder="8420 SW 152nd St, Miami, FL 33176",
    help="Type or paste the full address, then click Look up to auto-fill the rest.",
)
with c_btn:
    st.markdown("&nbsp;", unsafe_allow_html=True)  # vertical spacer for button alignment
    do_lookup = st.button(
        "🔍 Look up",
        use_container_width=True,
        help="Auto-fill city/state/zip/beds/baths/sqft/year/stories/pool/HOA "
             "from RentCast public records.",
    )

# Lookup handler runs BEFORE the remaining widgets render, so session_state
# updates take effect immediately on this run.
if do_lookup:
    if not property_lookup.is_configured():
        st.warning("⚠️ RentCast API key not configured. Add `[rentcast] api_key` "
                   "to Streamlit Secrets.")
    elif not (address or "").strip():
        st.warning("Type an address first.")
    else:
        with st.spinner("Looking up property records…"):
            result = property_lookup.lookup_property(address)
        if result.get("found"):
            updated = []
            for field in ["city", "state", "zip", "beds", "baths", "sqft",
                          "year", "stories", "pool", "hoa", "annual_taxes",
                          "garage_spaces", "property_type"]:
                v = result.get(field)
                if v not in (None, 0, "", "0"):
                    st.session_state[field] = v
                    updated.append(field)
            # Note: we don't auto-update the address field itself because Streamlit
            # disallows modifying a widget's session_state after it has rendered
            # this run. The user's typed address is preserved.
            loc = (f"{result.get('city', '')}, {result.get('state', '')} "
                   f"{result.get('zip', '')}").strip().strip(",").strip()
            st.success(
                f"✅ Found {result.get('address', '')} — {loc}. "
                f"Filled: {', '.join(updated) if updated else '(no new data)'}."
            )
            st.rerun()
        elif result.get("error"):
            st.error(f"Lookup failed: {result['error']}")
        else:
            st.warning("No property records found for that address. "
                       "Enter the details manually below.")

c1, c2, c3 = st.columns([2, 1, 1])
city = c1.text_input("City", value="Miami", key="city")
state = c2.text_input("State", value="FL", key="state")
zip_code = c3.text_input("ZIP", key="zip")

c1, c2, c3, c4, c5 = st.columns(5)
beds = c1.number_input("Beds", min_value=0, max_value=20, value=3, key="beds")
baths = c2.number_input("Baths", min_value=0.0, max_value=20.0, step=0.5,
                        value=2.0, key="baths")
sqft = c3.number_input("Living Sqft", min_value=0, value=1500, step=50, key="sqft")
year = c4.number_input("Year Built", min_value=1900, max_value=2030,
                       value=1980, key="year")
stories = c5.selectbox(
    "Stories", [1, 1.5, 2], key="stories",
    help="Used to compute actual roof footprint. A 2-story house has roughly "
         "55% the roof area of a 1-story house with the same living sqft.",
)

c1, c2, c3, c4 = st.columns(4)
pool = c1.selectbox("Pool?", ["No", "Yes"], key="pool")
hoa = c2.number_input("HOA Monthly", min_value=0, value=0, step=25, key="hoa")
annual_taxes = c3.number_input(
    "Annual Property Taxes ($/yr)", min_value=0, value=0, step=100, key="annual_taxes",
    help="Auto-fills from RentCast. Override if needed.",
)
asking = c4.number_input("Seller's Asking Price",
                         min_value=0, value=0, step=1000, key="asking",
                         help="What the seller publicly wants for the property.")

c1, c2, c3, c4 = st.columns(4)
property_type = c1.selectbox(
    "Property Type",
    ["Single Family Residence", "Condo", "Townhouse",
     "Multi-Family (2-4 units)", "Manufactured / Mobile", "Land"],
    key="property_type",
    help="Used for matching comps in the auto comp pull.",
)
waterfront = c2.selectbox(
    "Waterfront", ["No", "Canal/Lake", "Ocean"],
    key="waterfront",
    help="Applied as a comp adjustment: subject on canal pays +$75k vs no-water comps, etc.",
)
garage_spaces = c3.number_input(
    "Garage Spaces", min_value=0, max_value=6, value=0, step=1, key="garage_spaces",
    help="Applied as a comp adjustment when comp differs.",
)
acquisition_type = c4.selectbox(
    "Acquisition Type", ["Regular", "Short Sale"],
    key="acquisition_type",
    help="Short Sale = seller's lender covers seller-side closing costs.",
)

property_dict = {
    "address": address, "city": city, "state": state, "zip": zip_code,
    "beds": beds, "baths": baths, "sqft": sqft, "year": year,
    "stories": stories,
    "pool": pool, "hoa": hoa, "asking": asking,
    "acquisition_type": acquisition_type,
    "annual_taxes": annual_taxes,
    "property_type": property_type,
    "waterfront": waterfront,
    "garage_spaces": garage_spaces,
}

# ============================================================================
# 2. COMPS
# ============================================================================
st.markdown("---")
st.markdown("### 2. Comps")

if "comps_df" not in st.session_state:
    st.session_state.comps_df = None

# --- Auto pull from RentCast ---------------------------------------------
from modules.comp_import import filter_comps, adjust_all
from modules import property_lookup as _proplookup

c_pull, c_pull_info = st.columns([1, 4])
do_pull = c_pull.button(
    "🔍 Pull Comps",
    use_container_width=True,
    help="Pull comparable sold properties from RentCast based on the subject's "
         "address and property type. Filters by team rules; applies adjustments.",
)
with c_pull_info:
    if _proplookup.is_configured():
        st.caption("Pulls 5-7 comps from RentCast (MLS + public records), "
                   "filters by team rules (distance, age, sqft, beds/baths/year, "
                   "same property type), and auto-adjusts for pool / waterfront / "
                   "garage / bed-bath differences.")
    else:
        st.caption("⚠️ RentCast API key not configured in Streamlit Secrets.")

if do_pull:
    if not address.strip():
        st.warning("Enter the address first.")
    elif not _proplookup.is_configured():
        st.error("RentCast API key not configured.")
    else:
        from modules.strategy import get_strategy_defaults
        params = get_strategy_defaults()
        with st.spinner("Pulling comps from RentCast…"):
            result = _proplookup.fetch_comps(
                address,
                property_type=property_type,
                radius=params.get("comp_max_radius_miles", 0.5),
                days_old=params.get("comp_max_days_old", 180),
                comp_count=params.get("comp_count", 7),
            )
        if result.get("error"):
            st.error(f"Pull failed: {result['error']}")
        elif not result.get("comps"):
            st.warning("No comps returned. Try widening filter rules in Admin, "
                       "or upload a PropStream PDF as fallback below.")
        else:
            raw = result["comps"]
            filtered = filter_comps(raw, property_dict, params)
            # If filtering drops everything, fall back to raw (warn user)
            if not filtered:
                st.warning(f"All {len(raw)} comps were filtered out. Showing "
                           f"unfiltered. Loosen filter rules in Admin to keep more.")
                filtered = raw
            # Apply price adjustments (pool, waterfront, garage, etc.)
            adj_table = {k: params.get(k, 0) for k in [
                "adj_pool", "adj_waterfront_canal", "adj_waterfront_ocean",
                "adj_garage_1car", "adj_garage_2car",
                "adj_extra_bedroom", "adj_extra_half_bath",
            ]}
            adjusted = adjust_all(filtered, property_dict, adj_table)
            df_pulled = pd.DataFrame(adjusted)
            df_pulled.insert(0, "use", True)
            st.session_state.comps_df = df_pulled
            st.success(f"✅ Pulled {len(adjusted)} comps (of {len(raw)} returned). "
                       f"RentCast AVM: ${result.get('subject_avm', 0):,.0f}.")
            st.rerun()

# --- PDF / Excel upload fallback ----------------------------------------
with st.expander("📎 Or upload a comp report (PDF, CSV, Excel) — fallback for sparse markets",
                 expanded=False):
    upload = st.file_uploader(
        "Upload your realtor's comp report",
        type=["pdf", "csv", "xlsx", "xls"],
        help="Auto-parses common MLS export formats and PDF Comparable Sales Reports.",
    )
    if upload is not None:
        try:
            comps = parse_comp_file(upload, filename=upload.name)
            df = pd.DataFrame(comps)
            df.insert(0, "use", True)
            st.session_state.comps_df = df
            st.success(f"Imported {len(df)} comp candidates.")
        except Exception as e:
            st.error(f"Could not parse the file: {e}")

if st.session_state.comps_df is not None and not st.session_state.comps_df.empty:
    st.write("**Candidate comps** — uncheck rows you don't want to use.")
    df = st.session_state.comps_df
    # If pulled from RentCast, show adjusted_price column too
    has_adjusted = "adjusted_price" in df.columns and df["adjusted_price"].notna().any()
    if has_adjusted:
        display_cols = ["use", "address", "city", "sqft", "beds", "baths",
                        "year", "sold_price", "adjusted_price", "sold_date",
                        "distance", "notes"]
    else:
        display_cols = ["use", "address", "city", "sqft", "beds", "baths",
                        "year", "sold_price", "sold_date", "distance", "notes"]
    for col in display_cols:
        if col not in df.columns: df[col] = None

    edited = st.data_editor(
        df[display_cols],
        column_config={
            "use": st.column_config.CheckboxColumn("Use?", default=True),
            "sold_price": st.column_config.NumberColumn(
                "Sold Price", format="$%d"),
            "adjusted_price": st.column_config.NumberColumn(
                "Adjusted Price", format="$%d",
                help="Sold price adjusted for pool / waterfront / garage / "
                     "bed-bath differences vs your subject."),
            "sqft": st.column_config.NumberColumn("Sqft", format="%d"),
            "distance": st.column_config.NumberColumn("Distance (mi)", format="%.1f"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="comps_editor",
    )
    st.session_state.comps_df = edited

    # Show adjustment breakdown for each comp (if available)
    if has_adjusted and "adjustments" in df.columns:
        with st.expander("🔧 Adjustment details (click to see how each comp was adjusted)"):
            for i, row in df.iterrows():
                adjs = row.get("adjustments") or []
                if isinstance(adjs, list) and adjs:
                    addr = row.get("address", "?")
                    sold = row.get("sold_price", 0)
                    adj = row.get("adjusted_price", 0)
                    st.markdown(f"**{addr}** — sold ${sold:,.0f} → adjusted ${adj:,.0f}")
                    for label, amt in adjs:
                        sign = "+" if amt >= 0 else "−"
                        st.write(f"  {sign}${abs(amt):,.0f}  {label}")

    # Compute ARV from selected comps — use adjusted prices when available
    selected = edited[edited["use"] == True].to_dict("records") if "use" in edited.columns else edited.to_dict("records")
    arv_info = suggested_arv(selected, subject_sqft=sqft, use_adjusted=has_adjusted)

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
    _toggle("kitchen", "Kitchen", ["Full remodel", "Light update"], "Full remodel")

    # Bathrooms — Full ($6k) vs Partial ($2.5k). Defaults: all baths counted as Full.
    bath_prev = st.session_state.rehab.get("bathrooms", {}) or {}
    bcols = st.columns([1, 2, 2, 2])
    bath_include = bcols[0].checkbox(
        "Bathrooms", key="rehab_bathrooms_include",
        value=bath_prev.get("include", False),
    )
    bath_cfg = {"include": bath_include}
    if bath_include:
        default_full = int(bath_prev.get("full", baths) or 0)
        default_partial = int(bath_prev.get("partial", 0) or 0)
        full_n = bcols[1].number_input(
            "Full ($6k ea)", min_value=0, max_value=20, value=default_full, step=1,
            key="rehab_bath_full",
            help="Full demo + new tile + new vanity + new shower/tub + fixtures.",
        )
        partial_n = bcols[2].number_input(
            "Partial ($2.5k ea)", min_value=0, max_value=20, value=default_partial, step=1,
            key="rehab_bath_partial",
            help="Paint, vanity, fixtures, toilet, re-glaze tub if needed.",
        )
        bath_cfg["full"] = full_n
        bath_cfg["partial"] = partial_n
        # Show inline subtotal so the user sees the impact immediately
        bath_sub = full_n * 6000 + partial_n * 2500
        bcols[3].markdown(f"<div style='padding-top:0.4em'>= **${bath_sub:,}**</div>",
                          unsafe_allow_html=True)
    st.session_state.rehab["bathrooms"] = bath_cfg

    _toggle("half_bathrooms", "Half Bathrooms", qty_default=1)
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
    _toggle("lighting", "Lighting (all new)")
    _toggle("hot_water_tank", "Hot Water Tank")
    _toggle("cosmetic_demo", "Cosmetic Demo")
    _toggle("final_cleaning", "Final Cleaning")
    if pool == "Yes":
        _toggle("pool", "Pool",
                ["Replace Motor", "Replace Pump", "Heater",
                 "Waterline Tile", "Diamond Brite"], "Replace Motor")

# Live rehab total + line-item breakdown — pass stories so roof math is correct
sub_total = rehab_subtotal(st.session_state.rehab, sqft, baths, pool == "Yes",
                           stories=stories)
total_rehab = rehab_with_contingency(sub_total)
contingency = total_rehab - sub_total
c1, c2, c3 = st.columns(3)
c1.metric("Subtotal", f"${sub_total:,.0f}")
c2.metric("Contingency", f"${contingency:,.0f}",
          help="10% if subtotal > $50k, otherwise $5,000")
c3.metric("Total Rehab", f"${total_rehab:,.0f}")

# Line-item breakdown — shows each included item with its computed amount
items = rehab_breakdown(st.session_state.rehab, sqft, baths, pool == "Yes",
                        stories=stories)
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

# Compute the tool's auto-recommendation PLUS any alternative qualifying
# strategies so we can show the user a side-by-side comparison and let them
# pick which one drives the memo.
alternatives = compute_alternatives(inputs_dict)
auto_rec = alternatives[0]  # the tool's automatic pick

# Did the user override the auto pick by clicking "Use this strategy" on
# one of the alternative cards in a previous run?
forced_strategy = st.session_state.get("forced_strategy")
if forced_strategy and forced_strategy != auto_rec["auto_strategy"]:
    rec = compute_recommendation(inputs_dict, force_strategy=forced_strategy)
else:
    rec = auto_rec

# Headline banner
banner_color = "#C6EFCE"  # green default
strat = rec["strategy"]
if "Pass" in strat or "NO-GO" in strat:
    banner_color = "#FCE4D6"  # orange
elif "Marginal" in strat:
    banner_color = "#FFF2CC"  # yellow

_is_override = rec.get("is_forced", False)
_tag = "STRATEGY (your override)" if _is_override else "RECOMMENDED STRATEGY"
st.markdown(
    f"""
    <div style="background-color:{banner_color}; padding:20px; border-radius:8px;
                border-left:6px solid #1F4E78; margin-bottom:16px;">
        <div style="font-size:11px; color:#666; font-weight:bold;">{_tag}</div>
        <div style="font-size:24px; font-weight:bold; color:#1F4E78; margin:4px 0;">
            {strat}
        </div>
        <div style="font-size:13px; color:#333; font-style:italic;">
            {rec['rationale']}
        </div>
    </div>
    """, unsafe_allow_html=True)

if _is_override:
    _auto_name = auto_rec["auto_strategy"]
    c_undo, _ = st.columns([2, 5])
    if c_undo.button(f"↩ Revert to tool's auto pick ({_auto_name})",
                     use_container_width=True):
        st.session_state.pop("forced_strategy", None)
        st.rerun()

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

# Snapshot — Key Numbers vary by strategy
st.markdown("### Key Numbers")
from modules.strategy import key_numbers_for
metrics = key_numbers_for(rec, property_dict)
# Render 4 per row, wrapping for shorter lists
for i in range(0, len(metrics), 4):
    cols = st.columns(4)
    for j, (label, value) in enumerate(metrics[i:i + 4]):
        cols[j].metric(label, value)

# ============================================================================
# ALTERNATIVE STRATEGIES — side-by-side comparison cards
# ============================================================================
# Show the OTHER qualifying strategies for this deal so the user can compare
# trade-offs and override the auto-pick if they want. Filter out whatever's
# currently active (auto or forced) so we don't show a "use" button for the
# strategy already in play.
_active_strat_name = rec["strategy"]
_other_alts = [a for a in alternatives
               if a["strategy"] != _active_strat_name]
# If the user has forced a strategy that ISN'T in `alternatives` (edge case
# — e.g. they forced Novation, then the input changed such that the auto
# decided on Novation too), the auto-rec needs to appear as an alternative
# so they can switch back.
if rec.get("is_forced"):
    _auto_already_listed = any(a["strategy"] == auto_rec["strategy"]
                                for a in _other_alts)
    if not _auto_already_listed and auto_rec["strategy"] != _active_strat_name:
        _other_alts.insert(0, auto_rec)
if _other_alts:
    st.markdown("### ⚖️ Compare Other Strategies")
    st.caption(
        "These are other strategies the tool considered for this deal. "
        "Click **Use this strategy** to switch the memo and offer terms to "
        "that path."
    )

    def _seller_gets_for(r):
        """How much the seller walks away with under each strategy."""
        kind = r.get("proforma_kind")
        if kind == "novation":
            return r.get("benchmark", 0)
        if kind == "assignment":
            return r.get("wholesale_offer", 0)
        if kind == "dc":
            return r.get("likely_purchase_price", 0)
        if kind == "pass":
            return r.get("benchmark", 0) or r.get("asking", 0)
        # rehab
        return r.get("likely_purchase_price", 0) or r.get("cash_offer", 0)

    def _exodus_makes_for(r):
        """How much Exodus actually takes home under each strategy."""
        strat_str = r.get("strategy", "")
        if "MLS" in strat_str:
            return r.get("mls_commission_estimate", 0)
        if "Assignment" in strat_str:
            return r.get("target_assignment_fee") or r.get("net_profit", 0)
        return r.get("net_profit", 0)

    # Render 2 cards per row
    for i in range(0, len(_other_alts), 2):
        cols = st.columns(2)
        for j, alt in enumerate(_other_alts[i:i + 2]):
            with cols[j].container(border=True):
                _alt_strat = alt["strategy"]
                _alt_seller = _seller_gets_for(alt)
                _alt_exodus = _exodus_makes_for(alt)
                st.markdown(f"**{_alt_strat}**")
                m1, m2 = st.columns(2)
                m1.metric("Seller gets", f"${_alt_seller:,.0f}")
                m2.metric("Exodus makes", f"${_alt_exodus:,.0f}")
                # Show any caveats
                if "Novation" in _alt_strat and not alt.get("novation_feasible"):
                    st.caption(
                        "⚠️ Heavy rehab — MLS buyers may pass on this as-is. "
                        "Shown for comparison; use only if you have a buyer-type "
                        "strategy that works around the condition."
                    )
                if "MLS" in _alt_strat and not alt.get("mls_feasible"):
                    st.caption(
                        "⚠️ Doesn't strictly qualify (heavy scope or seller "
                        "not open to listing). Shown for comparison."
                    )
                # Rationale
                if alt.get("rationale"):
                    with st.expander("Why this strategy"):
                        st.write(alt["rationale"])
                if st.button("Use this strategy",
                             key=f"force_strat_{_alt_strat}",
                             use_container_width=True):
                    st.session_state["forced_strategy"] = _alt_strat
                    st.rerun()

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
        # Also save any chat messages from this session
        chat_history = st.session_state.get("chat_history", [])
        if chat_history:
            save_chat_bulk(deal_id, chat_history)
        st.success(
            f"Deal #{deal_id} saved." +
            (f" Chat history ({len(chat_history)} messages) preserved." if chat_history else "")
        )

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
    # Set flag; actual clearing happens at top of next run (before widgets
    # render) so Streamlit's internal widget cache also resets.
    st.session_state["_pending_reset"] = True
    st.rerun()

# ============================================================================
# 💬 BRAINSTORM WITH CLAUDE
# ============================================================================
st.markdown("---")
with st.expander("💬 Brainstorm with Claude about this deal", expanded=False):
    if not chat_mod.is_configured():
        st.warning(
            "Chat not configured. Add your Anthropic API key in "
            "**Streamlit Cloud → Settings → Secrets**:\n\n"
            "```toml\n[anthropic]\napi_key = \"sk-ant-...\"\n```"
        )
    else:
        # Initialize chat history in session state
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        st.caption(
            "Claude has the full deal context loaded — property, comps, ARV, rehab, "
            "recommendation, all of it. Ask anything. Chat is preserved when you save the deal."
        )

        # Suggested prompts (only show before first message)
        if not st.session_state.chat_history:
            st.write("**Try a starter prompt:**")
            cols = st.columns(2)
            for i, prompt in enumerate(chat_mod.get_suggested_prompts(property_dict)):
                if cols[i % 2].button(prompt, key=f"suggested_{i}", use_container_width=True):
                    st.session_state.chat_history.append({"role": "user", "content": prompt})
                    st.session_state._chat_pending = prompt
                    st.rerun()

        # Display chat history (every message that's already been exchanged)
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # If there's a pending user message — either from a suggested-prompt
        # button click OR from a chat_input submission on the previous run —
        # stream Claude's response inline. The user message is already in
        # chat_history (the suggested-prompt button or chat_input handler
        # added it). We just render the streaming assistant message here so
        # it appears above the input box.
        pending = st.session_state.pop("_chat_pending", None)
        if pending:
            with st.chat_message("assistant"):
                placeholder = st.empty()
                full_response = ""
                try:
                    system_prompt = chat_mod.build_system_prompt(
                        property_dict, rec, seller_dict, rehab_items=items
                    )
                    # History to send (excludes the user message we just added)
                    history_for_api = st.session_state.chat_history[:-1]
                    for chunk in chat_mod.stream_response(
                        system_prompt, history_for_api, pending
                    ):
                        full_response += chunk
                        placeholder.markdown(full_response + " ▌")
                    placeholder.markdown(full_response)
                    st.session_state.chat_history.append(
                        {"role": "assistant", "content": full_response}
                    )
                except Exception as e:
                    placeholder.error(f"Chat error: {e}")

        # Clear chat button — render BEFORE chat_input so chat_input always
        # sits dead last in the column. (Streamlit renders top-to-bottom.)
        if st.session_state.chat_history:
            if st.button("🗑 Clear chat", key="clear_chat"):
                st.session_state.chat_history = []
                st.rerun()

        # Chat input — placed LAST so it always sits at the bottom of the
        # conversation, below Claude's most recent reply. Submitting just
        # queues the message via _chat_pending and reruns; the streaming
        # happens at the top of the next run (above this input).
        user_input = st.chat_input("Ask Claude about this deal...")
        if user_input:
            st.session_state.chat_history.append(
                {"role": "user", "content": user_input}
            )
            st.session_state._chat_pending = user_input
            st.rerun()
