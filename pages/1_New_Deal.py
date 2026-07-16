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


# --- Deal-edit handler ---------------------------------------------------
# When the user clicks "Open in editor" on Past Deals, that page sets
# _pending_deal_load = deal_id and switches here. We must populate every
# widget's session_state key BEFORE the widget renders for the first time —
# Streamlit reads widget defaults from session_state at construction time.
def _preload_deal(deal_id: int) -> bool:
    """Pull a deal from Supabase and populate every form-field key in
    session_state. Returns True if the deal was found and loaded."""
    from modules.db import get_deal as _get_deal
    deal = _get_deal(int(deal_id))
    if not deal:
        return False

    inputs = deal.get("inputs", {}) or {}
    outputs = deal.get("outputs", {}) or {}
    prop = inputs.get("property", {}) or {}
    seller = inputs.get("seller", {}) or {}
    rehab = inputs.get("rehab", {}) or {}

    # --- 1. Wipe any existing widget state so old values don't bleed in ---
    # We keep app-level keys (auth, role badge, etc.) intact.
    _stale = [k for k in list(st.session_state.keys())
              if not k.startswith("_st_")
              and k not in ("loaded_deal_id",)
              and not k.startswith("FormSubmitter:")]
    for k in _stale:
        try:
            del st.session_state[k]
        except Exception:
            pass

    # --- 2. Property fields ---
    st.session_state["address"] = prop.get("address", "")
    st.session_state["city"] = prop.get("city", "")
    st.session_state["state"] = prop.get("state", "FL")
    st.session_state["zip"] = str(prop.get("zip", ""))
    st.session_state["beds"] = int(prop.get("beds", 3) or 3)
    st.session_state["baths"] = float(prop.get("baths", 2.0) or 2.0)
    st.session_state["sqft"] = int(prop.get("sqft", 1500) or 1500)
    st.session_state["year"] = int(prop.get("year", 1980) or 1980)
    _stories = prop.get("stories", 1) or 1
    try:
        _stories = float(_stories)
        if _stories not in (1, 1.5, 2):
            _stories = 1
    except (TypeError, ValueError):
        _stories = 1
    st.session_state["stories"] = _stories
    st.session_state["pool"] = prop.get("pool", "No")
    st.session_state["hoa"] = int(prop.get("hoa", 0) or 0)
    st.session_state["annual_taxes"] = int(prop.get("annual_taxes", 0) or 0)
    st.session_state["asking"] = int(prop.get("asking", 0) or 0)
    st.session_state["property_type"] = prop.get("property_type", "Single Family Residence")
    st.session_state["waterfront"] = prop.get("waterfront", "No")
    st.session_state["garage_spaces"] = int(prop.get("garage_spaces", 0) or 0)
    st.session_state["acquisition_type"] = prop.get("acquisition_type", "Regular")

    # --- 2b. Contract info — Property Tax ID + Legal Description + County
    # (v23). Used by the Prepare Contract page so the rep doesn't re-type
    # these every time they generate a contract for the same property.
    st.session_state["parcel_folio"] = prop.get("parcel_folio", "")
    st.session_state["legal_description"] = prop.get("legal_description", "")
    st.session_state["appraiser_county"] = prop.get("county", "")

    # --- 2c. v24 closing-cost detail fields (all optional; drive the itemized
    # closing model). Existing deals without these will get defaults on load.
    st.session_state["is_short_sale"] = bool(prop.get("is_short_sale")
                                             or prop.get("acquisition_type",
                                                          "Regular").lower().startswith("short"))
    st.session_state["buyer_pays_seller_closings"] = bool(
        prop.get("buyer_pays_seller_closings", False))
    st.session_state["seller_concession"] = int(prop.get("seller_concession", 0) or 0)
    st.session_state["hoa_capital_contrib"] = int(prop.get("hoa_capital_contrib", 0) or 0)
    st.session_state["utility_escrow_estimate"] = int(
        prop.get("utility_escrow_estimate", 0) or 0)
    st.session_state["short_sale_lien_estimate"] = int(
        prop.get("short_sale_lien_estimate", 0) or 0)
    # BC commission per-deal override (float 0.05–0.06). None means use default.
    _bc_comm = prop.get("bc_commission_pct")
    st.session_state["bc_commission_pct_pct"] = (
        float(_bc_comm) * 100 if _bc_comm is not None else 5.5)

    # --- 3. Rehab toggles ---
    # The rehab dict lives in session_state.rehab and drives the _toggle()
    # helper. We also need to set each widget's individual key because the
    # checkbox/selectbox/qty inputs read from those keys directly.
    st.session_state["rehab"] = rehab
    for r_key, cfg in (rehab or {}).items():
        cfg = cfg or {}
        st.session_state[f"rehab_{r_key}_include"] = bool(cfg.get("include", False))
        if "type" in cfg:
            st.session_state[f"rehab_{r_key}_type"] = cfg["type"]
        if "qty" in cfg:
            st.session_state[f"rehab_{r_key}_qty"] = cfg["qty"]
        if "amount" in cfg:
            st.session_state[f"rehab_{r_key}_amount"] = cfg["amount"]
        # Bathrooms have a Full + Partial split (v8)
        if r_key == "bathrooms":
            if "full" in cfg:
                st.session_state["rehab_bath_full"] = int(cfg["full"] or 0)
            if "partial" in cfg:
                st.session_state["rehab_bath_partial"] = int(cfg["partial"] or 0)

    # --- 4. Seller fields ---
    st.session_state["mtg1"] = int(seller.get("mtg1", 0) or 0)
    st.session_state["mtg2"] = int(seller.get("mtg2", 0) or 0)
    st.session_state["other_liens"] = int(seller.get("other_liens", 0) or 0)
    st.session_state["payment_status"] = seller.get("payment_status", "Current")
    st.session_state["required_net"] = int(seller.get("required_net", 0) or 0)
    st.session_state["timeline_days"] = int(seller.get("timeline_days", 30) or 30)
    st.session_state["reason_for_selling"] = seller.get("reason_for_selling", "Other")
    st.session_state["occupancy"] = seller.get("occupancy", "Owner")
    st.session_state["condition_confirmed"] = seller.get("condition_confirmed", "No")
    st.session_state["open_to_mls_listing"] = seller.get("open_to_mls_listing", "Yes")
    st.session_state["buyer_demand_confirmed"] = seller.get("buyer_demand_confirmed", False)
    st.session_state["buyer_prefers_dc"] = seller.get("buyer_prefers_dc", False)
    st.session_state["assignable"] = seller.get("assignable", True)

    # --- 4b. Seller party block — v23 contract info ---
    st.session_state["seller_party_name"] = seller.get("name", "")
    st.session_state["seller_party_mailing"] = seller.get("mailing_address", "")
    st.session_state["seller_party_phone"] = seller.get("phone", "")
    st.session_state["seller_party_email"] = seller.get("email", "")

    # --- 5. Comps (CRITICAL — no RentCast re-pull) ---
    saved_comps = inputs.get("comps")
    if saved_comps:
        import pandas as _pd
        try:
            st.session_state["comps_df"] = _pd.DataFrame(saved_comps)
        except Exception:
            pass

    # --- 6. ARV + ARV method ---
    arv_value = outputs.get("arv") or inputs.get("arv") or 0
    if arv_value:
        st.session_state["arv"] = float(arv_value)
    arv_method = inputs.get("arv_method")
    if arv_method:
        st.session_state["arv_method"] = arv_method

    # --- 7. Mark this deal as the one being edited ---
    st.session_state["loaded_deal_id"] = int(deal_id)
    return True


_pending_load = st.session_state.pop("_pending_deal_load", None)
if _pending_load:
    if _preload_deal(int(_pending_load)):
        st.toast(f"✏️ Editing deal #{_pending_load}", icon="✅")
    else:
        st.warning(f"Deal #{_pending_load} could not be loaded. "
                   "It may have been deleted.")


st.title("📝 New Deal Analysis")

# Editor banner — only when we're editing an existing deal
_editing_id = st.session_state.get("loaded_deal_id")
if _editing_id:
    c_banner, c_clear = st.columns([5, 1])
    c_banner.info(
        f"✏️ **Editing deal #{_editing_id}.** Make any changes, then click "
        "**💾 Save changes** at the bottom. To leave edit mode and start "
        "a new deal, click **✨ Start fresh** on the right."
    )
    if c_clear.button("✨ Start fresh", use_container_width=True,
                       help="Exit editor mode and clear the form for a new deal."):
        st.session_state["_pending_reset"] = True
        st.rerun()

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

# ============================================================================
# v24 — CLOSING COST DETAIL
# Drives the itemized AB/BC closing cost model derived from real HUDs.
# Auto-defaults sensibly for a Regular Broward acquisition; expand to override.
# ============================================================================
with st.expander("Closing Cost Detail (county, short sale, concessions, HOA)",
                 expanded=False):
    st.caption(
        "These inputs feed the v24 itemized closing cost model. Defaults "
        "reflect the most common Broward scenario. Override per deal."
    )
    cc1, cc2, cc3 = st.columns(3)
    county_options = [
        "Broward", "Miami-Dade", "Palm Beach", "Monroe",
        "Martin", "St. Lucie", "Indian River", "Sarasota", "Collier",
        "Lee", "Charlotte", "Hillsborough", "Pinellas", "Pasco",
        "Orange", "Osceola", "Seminole", "Volusia", "Brevard", "Other",
    ]
    # Reuse appraiser_county key so this stays synced with the Contract page
    _default_county = st.session_state.get("appraiser_county", "Broward")
    if _default_county not in county_options:
        _default_county = "Other"
    county = cc1.selectbox(
        "County",
        county_options,
        index=county_options.index(_default_county),
        key="cc_detail_county",
        help="Drives owner's title split. Broward/Miami-Dade/Sarasota/Collier "
             "are buyer-pays counties — everywhere else, seller (us at BC) pays "
             "the ~0.4% owner's title policy.",
    )
    bc_commission_pct_ui = cc2.number_input(
        "BC Commission % (retail sale)",
        min_value=0.0, max_value=10.0, value=5.5, step=0.1,
        key="bc_commission_pct_pct",
        help="Realtor commission at retail sale. 5.5% default (typical range 5.0–6.0%). "
             "Half is assumed listing-side (recoverable to household).",
    )
    is_short_sale = cc3.checkbox(
        "Short Sale acquisition",
        key="is_short_sale",
        help="Adds $4,000 negotiation fee + optional lien coverage estimate. "
             "Auto-checked if Acquisition Type = Short Sale.",
    )

    cc4, cc5, cc6 = st.columns(3)
    buyer_pays_seller_closings = cc4.checkbox(
        "We pay seller's closing costs",
        key="buyer_pays_seller_closings",
        help="Equity deals where we sweetened the offer by covering the seller's "
             "title/tax items. Adds 0.7% deed doc stamps + owner's title (in "
             "seller-pays counties) + $700 title admin + $500 HOA estoppel (if HOA).",
    )
    seller_concession = cc5.number_input(
        "Seller concession to end buyer ($)",
        min_value=0, value=0, step=500,
        key="seller_concession",
        help="Common with FHA buyers ($10K–$20K typical). Reduces net proceeds at BC.",
    )
    hoa_capital_contrib = cc6.number_input(
        "HOA capital contribution ($)",
        min_value=0, value=0, step=100,
        key="hoa_capital_contrib",
        help="One-time HOA transfer fee. Only if HOA. Varies wildly ($500–$3,500).",
    )

    cc7, cc8, _ = st.columns(3)
    utility_escrow_estimate = cc7.number_input(
        "Utility escrow holdback ($)",
        min_value=0, value=0, step=100,
        key="utility_escrow_estimate",
        help="Escrow held pending final utility/water readings ($250–$1,000 typical).",
    )
    short_sale_lien_estimate = cc8.number_input(
        "Est. unpaid liens (short sale) ($)",
        min_value=0, value=0, step=250,
        key="short_sale_lien_estimate",
        help="Municipal violations, unpaid utilities, code enforcement liens that "
             "the seller's bank won't cover in the short sale. Only applies if "
             "Short Sale is checked.",
        disabled=not st.session_state.get("is_short_sale", False),
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
    # v24 closing-cost detail fields
    "county": county,
    "is_short_sale": bool(is_short_sale
                          or acquisition_type.lower().startswith("short")),
    "buyer_pays_seller_closings": bool(buyer_pays_seller_closings),
    "seller_concession": seller_concession,
    "hoa_capital_contrib": hoa_capital_contrib,
    "utility_escrow_estimate": utility_escrow_estimate,
    "short_sale_lien_estimate": short_sale_lien_estimate,
    # BC commission stored as decimal (0.055 not 5.5)
    "bc_commission_pct": bc_commission_pct_ui / 100.0,
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
    # NOTE: mutate st.session_state.comps_df in place (adding missing cols) so
    # the object identity stays stable across reruns. Passing a NEW dataframe
    # object (e.g. df[display_cols] slice) on each rerun causes st.data_editor
    # to misalign its internal edit-delta with the base — the classic symptom
    # is "typed value disappears on first entry, sticks on second".
    _persist_df = st.session_state.comps_df
    has_adjusted = ("adjusted_price" in _persist_df.columns
                    and _persist_df["adjusted_price"].notna().any())
    if has_adjusted:
        display_cols = ["use", "address", "city", "sqft", "beds", "baths",
                        "year", "sold_price", "adjusted_price", "sold_date",
                        "distance", "notes"]
    else:
        display_cols = ["use", "address", "city", "sqft", "beds", "baths",
                        "year", "sold_price", "sold_date", "distance", "notes"]
    # Ensure required columns exist on the persistent df (idempotent — safe
    # to call every rerun since we only add cols that are missing).
    for col in display_cols:
        if col not in _persist_df.columns:
            _persist_df[col] = None

    edited = st.data_editor(
        _persist_df,                       # pass the SAME object reference every render
        column_order=display_cols,         # visibility/order without slicing
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
    # Only sync back if the shape/content actually differs. Avoids the
    # write-triggers-rerun-triggers-reconciliation loop that was nulling cells.
    if edited is not None:
        try:
            _changed = not edited.equals(st.session_state.comps_df)
        except Exception:
            _changed = True
        if _changed:
            st.session_state.comps_df = edited

    # Show adjustment breakdown for each comp (if available)
    if has_adjusted and "adjustments" in _persist_df.columns:
        with st.expander("🔧 Adjustment details (click to see how each comp was adjusted)"):
            for i, row in _persist_df.iterrows():
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
# 4b. CONTRACT INFO — required to generate the Purchase & Sale Agreement.
# Collapsed by default so it doesn't clutter the underwriting flow; expand
# when you're ready to prepare a contract. Saves with the deal.
# ============================================================================
with st.expander("📄 Contract Info (seller party, folio, legal description)",
                 expanded=False):
    st.caption("These fields are saved with the deal and auto-populate the "
               "Purchase & Sale Agreement when you click **Prepare Contract**. "
               "You only need to enter them once per property.")

    st.markdown("**Seller Party** — exactly as it should appear in the contract")
    c1, c2 = st.columns([2, 1])
    seller_name = c1.text_input(
        "Seller Name (full legal name)", key="seller_party_name",
        placeholder='e.g. "John Q. Sample, an unmarried man" or "Estate of …"',
    )
    seller_phone = c2.text_input(
        "Seller Phone", key="seller_party_phone",
        placeholder="(305) 555-1234",
    )
    c1, c2 = st.columns([2, 1])
    seller_mailing = c1.text_input(
        "Seller Mailing Address (for notices)", key="seller_party_mailing",
        placeholder="123 Main St, City, FL 33101",
    )
    seller_email = c2.text_input(
        "Seller Email", key="seller_party_email",
        placeholder="seller@example.com",
    )

    st.markdown("**Property Tax ID & Legal Description** — from the county "
                "property appraiser")
    c1, c2 = st.columns([1, 2])
    parcel_folio = c1.text_input(
        "Property Tax ID / Folio #", key="parcel_folio",
        placeholder="01-2345-678-9012",
    )

    # County appraiser quick-link — opens the right county's search page so
    # the user can copy the folio and legal description without leaving the
    # context of the deal.
    _county = (city if False else "").strip()  # placeholder; will use prop county
    _county_links = {
        "Miami-Dade": "https://www.miamidade.gov/Apps/PA/PAOnlineTools/Property/PropertySearch",
        "Broward":    "https://web.bcpa.net/bcpaclient/#/Record-Search",
        "Palm Beach": "https://www.pbcpao.gov/Property/Search",
        "Hillsborough": "https://gis.hcpafl.org/propertysearch/",
        "Pinellas":   "https://www.pcpao.gov/quick-search",
        "Lee":        "https://www.leepa.org/Search/PropertySearch.aspx",
    }
    # Heuristic: pick a county from city. Rough mapping for common cities.
    _city_to_county = {
        "miami": "Miami-Dade", "hialeah": "Miami-Dade", "opa-locka": "Miami-Dade",
        "homestead": "Miami-Dade", "miami beach": "Miami-Dade",
        "fort lauderdale": "Broward", "pompano beach": "Broward",
        "oakland park": "Broward", "hollywood": "Broward",
        "west palm beach": "Palm Beach", "riviera beach": "Palm Beach",
        "boca raton": "Palm Beach", "lake worth": "Palm Beach",
        "tampa": "Hillsborough", "brandon": "Hillsborough",
        "st. petersburg": "Pinellas", "clearwater": "Pinellas",
        "fort myers": "Lee", "north fort myers": "Lee", "cape coral": "Lee",
    }
    _guessed = _city_to_county.get((city or "").strip().lower(), "")
    appraiser_county = c2.selectbox(
        "County (for appraiser lookup link)",
        ["", "Miami-Dade", "Broward", "Palm Beach", "Hillsborough",
         "Pinellas", "Lee", "Other"],
        index=(["", "Miami-Dade", "Broward", "Palm Beach", "Hillsborough",
                "Pinellas", "Lee", "Other"].index(_guessed) if _guessed else 0),
        key="appraiser_county",
        help="Pick the county the property is in. Used both for the appraiser "
             "look-up link below AND printed on the contract's Property Description.",
    )

    if appraiser_county and appraiser_county != "Other":
        _link = _county_links.get(appraiser_county)
        if _link:
            st.link_button(
                f"🔗 Open {appraiser_county} Property Appraiser",
                _link, help="Opens in a new tab. Search by address, then copy "
                            "the Folio # and Legal Description fields back into "
                            "the boxes here.",
            )
    elif appraiser_county == "Other":
        _q = ", ".join(filter(None, [address, city, state])) or "Florida property appraiser"
        st.link_button(
            "🔗 Google county property appraiser",
            f"https://www.google.com/search?q={_q}+property+appraiser+folio+legal+description",
            help="Opens a Google search. Find your county's appraiser site, "
                 "look up this property, then copy the folio and legal description here.",
        )

    legal_description = st.text_area(
        "Legal Description", key="legal_description",
        height=80,
        placeholder='e.g. "LOT 12, BLOCK 5, SUNNYDALE ESTATES, ACCORDING TO '
                    'THE PLAT THEREOF AS RECORDED IN PLAT BOOK 42, PAGE 17 '
                    'OF THE PUBLIC RECORDS OF MIAMI-DADE COUNTY, FLORIDA"',
        help="Copy verbatim from the county appraiser. This text prints into "
             "Paragraph 1(c) of the contract — it's worth being exact.",
    )

# Extend the property + seller dicts with the new contract-info fields so
# they round-trip with the deal record. Both contract.py and the
# Prepare Contract page read from these.
property_dict["county"] = appraiser_county or ""
property_dict["parcel_folio"] = parcel_folio or ""
property_dict["legal_description"] = legal_description or ""
seller_dict["name"] = seller_name or ""
seller_dict["mailing_address"] = seller_mailing or ""
seller_dict["phone"] = seller_phone or ""
seller_dict["email"] = seller_email or ""

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
    # Round-trip data so re-opening a saved deal doesn't trigger a fresh
    # RentCast pull (which would count against the monthly comp quota):
    "comps": (
        st.session_state.comps_df.to_dict("records")
        if (st.session_state.get("comps_df") is not None
            and hasattr(st.session_state.comps_df, "to_dict"))
        else None
    ),
    "arv_method": st.session_state.get("arv_method"),
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
        """How much the seller walks away with under each strategy.
        Uses the clamped *_to_seller fields so the displayed number never
        exceeds the seller's asking price (v19/v20 fix)."""
        kind = r.get("proforma_kind")
        if kind == "novation":
            return r.get("benchmark", 0)
        if kind == "assignment":
            return r.get("wholesale_offer_to_seller",
                         r.get("wholesale_offer", 0))
        if kind == "dc":
            return r.get("likely_purchase_price", 0)
        if kind == "pass":
            return r.get("benchmark", 0) or r.get("asking", 0)
        # rehab
        return (r.get("cash_offer_to_seller")
                or r.get("likely_purchase_price", 0)
                or r.get("cash_offer", 0))

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
                # Add fee-range help on assignment so the rep sees the
                # practical cap (above $25k forces a Double Close).
                if alt.get("proforma_kind") == "assignment":
                    m2.metric(
                        "Exodus makes",
                        f"${_alt_exodus:,.0f}",
                        help="Assignment fee range: $5,000 – $25,000. "
                             "If the natural spread exceeds $25,000, "
                             "switch to Double Close to capture the full "
                             "margin — end-buyer title attorneys typically "
                             "push back on bigger assignment fees.",
                    )
                else:
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

# Context-aware button: "Save changes" when editing an existing deal,
# "Save deal to history" when creating a new one. The handler dispatches
# to either update_deal() or save_deal() based on whether loaded_deal_id
# is set AND the deal still exists in the database.
_editing_existing_deal = bool(st.session_state.get("loaded_deal_id"))
_save_button_label = (
    "💾 Save changes" if _editing_existing_deal else "Save deal to history"
)

if c1.button(_save_button_label, type="primary", use_container_width=True):
    if not address:
        st.error("Enter an address before saving.")
    else:
        from modules.db import update_deal
        deal_id = st.session_state.get("loaded_deal_id")
        was_update = False
        if _editing_existing_deal:
            ok = update_deal(int(deal_id), inputs_dict, rec,
                              user_email=user.get("email"))
            if ok:
                was_update = True
            else:
                # Update returned no rows — the deal may have been deleted
                # while we were editing. Fall back to a fresh insert so
                # the work isn't lost.
                deal_id = save_deal(inputs_dict, rec,
                                     user_email=user.get("email"))
                st.session_state["loaded_deal_id"] = deal_id
        else:
            deal_id = save_deal(inputs_dict, rec, user_email=user.get("email"))
            st.session_state["loaded_deal_id"] = deal_id

        # Also save any chat messages from this session
        chat_history = st.session_state.get("chat_history", [])
        if chat_history:
            save_chat_bulk(deal_id, chat_history)

        # Flush any call analyses that were uploaded BEFORE this save.
        pending_calls = st.session_state.get("pending_call_analyses", []) or []
        flushed_calls = 0
        flush_errors: list = []
        if pending_calls:
            from modules.db import save_call_analysis as _save_ca
            for p in pending_calls:
                try:
                    new_id = _save_ca(deal_id=deal_id, **p)
                    if new_id:
                        flushed_calls += 1
                except Exception as e:
                    flush_errors.append(str(e))
            st.session_state["pending_call_analyses"] = []

        verb = "updated" if was_update else "saved"
        st.success(
            f"Deal #{deal_id} {verb}."
            + (f" Chat history ({len(chat_history)} messages) preserved." if chat_history else "")
            + (f" {flushed_calls} call recording{'s' if flushed_calls != 1 else ''} attached." if flushed_calls else "")
        )
        if flush_errors:
            st.warning(
                f"⚠️ {len(flush_errors)} call recording(s) could not be saved: "
                + "; ".join(flush_errors)
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

# Homeowner Presentation — kitchen-table view, only available after save
# so the saved deal context (comps + rehab + recommendation) is on disk.
_homeowner_deal_id = st.session_state.get("loaded_deal_id")
if _homeowner_deal_id:
    if st.button(
        "🏠 Show to Homeowner",
        use_container_width=True,
        help="Open the seller-facing breakdown — shows ARV, repairs, our "
             "costs, our minimum profit, and the cash offer. No jargon, "
             "no other strategies. Designed to be screen-shared or printed "
             "at the kitchen table.",
    ):
        st.session_state["homeowner_deal_id"] = int(_homeowner_deal_id)
        try:
            st.switch_page("pages/6_Homeowner_Presentation.py")
        except Exception:
            st.info(
                "Deal queued. Open the **🏠 Homeowner Presentation** page "
                "from the left sidebar."
            )
else:
    st.caption(
        "💡 Save this deal first to unlock the **🏠 Show to Homeowner** view "
        "— a clean kitchen-table breakdown of how we arrived at the offer."
    )

# Prepare Contract — only available after save AND only visible to
# Admin/Manager (Agents can't generate contracts).
_contract_deal_id = st.session_state.get("loaded_deal_id")
try:
    from modules import settings as _st_sett
    _can_prepare_contract = _st_sett.can_view_admin(user.get("email", ""))
except Exception:
    _can_prepare_contract = False

if _contract_deal_id and _can_prepare_contract:
    if st.button(
        "📄 Prepare Contract",
        use_container_width=True,
        help="Generate a Florida AS-IS Residential Purchase & Sale Agreement "
             "PDF for this property. Pre-fills seller party, property folio, "
             "and legal description from the deal record. Buyer is always "
             "NSGC Investing Services, Inc. Lead-Based Paint Disclosure "
             "attaches automatically when year built is pre-1978.",
    ):
        st.session_state["contract_deal_id"] = int(_contract_deal_id)
        try:
            st.switch_page("pages/7_Prepare_Contract.py")
        except Exception:
            st.info(
                "Deal queued. Open the **📄 Prepare Contract** page "
                "from the left sidebar."
            )
elif _can_prepare_contract:
    st.caption(
        "💡 Save this deal first to unlock the **📄 Prepare Contract** "
        "PDF generator."
    )

# Dispo Marketing — only available after save. Agents can use it too — the
# cash-buyer deal sheet is internal-facing and useful for anyone routing the
# deal to a buyer.
_dispo_deal_id = st.session_state.get("loaded_deal_id")
if _dispo_deal_id:
    if st.button(
        "🚀 Dispo Marketing",
        key="dispo_launch_btn",
        use_container_width=True,
        type="secondary",
        help="Open the Dispo Marketing editor. Edit asking price, comps, "
             "and rehab scope, then generate a two-page PDF: cash-buyer "
             "deal sheet on page 1, ready-to-copy Email / SMS / Facebook "
             "drafts on page 2.",
    ):
        st.session_state["dispo_deal_id"] = int(_dispo_deal_id)
        try:
            st.switch_page("pages/8_Dispo_Marketing.py")
        except Exception:
            st.info(
                "Deal queued. Open the **🚀 Dispo Marketing** page "
                "from the left sidebar."
            )
else:
    st.caption(
        "💡 Save this deal first to unlock the **🚀 Dispo Marketing** editor."
    )

# ============================================================================
# 🎤 SALES CALL ANALYSIS
# ============================================================================
# Upload a recording of the call you had with the seller; Deepgram transcribes
# it with speaker diarization; Claude grades it against the methodology doc.
st.markdown("---")
_role = (user.get("role") if isinstance(user, dict) else "agent") or "agent"
_can_review = _role in ("admin", "manager")
_expander_title = (
    "🎤 Sales Call Analysis — upload a recording and grade the call"
    if _can_review
    else "🎤 Sales Call Upload — upload a recording for your manager to review"
)
with st.expander(_expander_title, expanded=False):
    from modules import transcribe as transcribe_mod
    from modules import call_analysis as analysis_mod
    from modules.db import (save_call_analysis, load_call_analyses_for_deal,
                            delete_call_analysis)

    # Configuration guards — show actionable warnings if anything's missing
    _missing = []
    if not transcribe_mod.is_configured():
        _missing.append(
            '**Deepgram** — add a `[deepgram] api_key = "..."` block to '
            "Streamlit Cloud → Settings → Secrets. Sign up at deepgram.com "
            "(free $200 trial credit, no card required)."
        )
    if not analysis_mod.is_configured():
        _missing.append(
            '**Anthropic** — already required for the chat feature. Same '
            'key under `[anthropic]`.'
        )

    if _missing:
        st.warning(
            "Call analysis needs the following before it can run:\n\n"
            + "\n\n".join(f"- {m}" for m in _missing)
        )
    else:
        if _can_review:
            st.caption(
                "Drag in an audio recording of the call (mp3, m4a, wav, mp4, "
                "or mov). Deepgram transcribes it with speaker diarization, "
                "then Claude grades it against your sales methodology. "
                "~$0.30–0.60 per call, 30-60 seconds end-to-end."
            )
        else:
            st.caption(
                "Drag in an audio recording of your call with the homeowner "
                "(mp3, m4a, wav, mp4, or mov). It'll be transcribed and "
                "saved for your manager to review and provide coaching on."
            )

        # --- Pending (unsaved) analyses banner ---------------------------
        # If the user uploaded calls BEFORE saving the deal, show a
        # clear indicator so they know what's queued and will save next.
        _pending = st.session_state.get("pending_call_analyses", []) or []
        if _pending:
            st.info(
                f"📌 **{len(_pending)} call recording"
                f"{'s' if len(_pending) != 1 else ''} queued.** "
                "Will save to this deal automatically when you click "
                "💾 Save deal above."
            )

        # --- Render history of past analyses for this deal ----------------
        # ADMIN / MANAGER ONLY. Agents never see prior analyses, even on
        # the same deal — that's by design (coaching is the manager's job).
        existing_deal_id = st.session_state.get("loaded_deal_id")
        existing_analyses = []
        if existing_deal_id and _can_review:
            try:
                existing_analyses = load_call_analyses_for_deal(existing_deal_id)
            except Exception:
                existing_analyses = []

        if existing_analyses and _can_review:
            st.markdown("**Previous calls on this deal:**")
            for row in existing_analyses:
                hdr = (f"• {row.get('call_type', 'Call')} "
                       f"— {row.get('audio_filename', '(no name)')}")
                with st.expander(hdr, expanded=False):
                    a = row.get("analysis") or {}
                    st.markdown(analysis_mod.format_full_analysis(a))
                    t = row.get("transcript") or {}
                    if t.get("labeled_text"):
                        with st.expander("Show transcript"):
                            st.markdown(t["labeled_text"])
                    if st.button("🗑 Delete this analysis",
                                 key=f"del_ca_{row['id']}"):
                        if delete_call_analysis(row["id"]):
                            st.rerun()
            st.markdown("---")
        elif existing_deal_id and not _can_review:
            # Agent view — just a count, no detail
            try:
                count = len(load_call_analyses_for_deal(existing_deal_id))
            except Exception:
                count = 0
            if count:
                st.caption(
                    f"📬 {count} call{'s' if count != 1 else ''} already "
                    "uploaded for this deal and queued for review."
                )

        # --- Upload form ---------------------------------------------------
        c_upl, c_type = st.columns([3, 2])
        audio_file = c_upl.file_uploader(
            "Audio or video recording (up to 1 GB)",
            type=["mp3", "m4a", "wav", "aac", "mp4", "mov", "m4v", "webm"],
            key="call_audio_uploader",
            help="Audio (mp3/m4a/wav) or video (mp4/mov from Google Meet) — "
                 "videos are auto-converted to audio server-side.",
        )
        with c_upl.expander("💡 Faster uploads: convert video to MP3 first"):
            st.markdown(
                "Video files are mostly picture data — the audio you need is "
                "5-10% of the size. Converting locally first means a much "
                "smaller upload and the same grading result.\n\n"
                "**On Mac:** Open the .mp4 in QuickTime → File → Export As → "
                "Audio Only → save as .m4a.\n\n"
                "**Any OS:** Use [VLC](https://www.videolan.org/vlc/) — "
                "File → Convert/Stream → pick Audio MP3 profile.\n\n"
                "If you skip this, that's fine — we'll strip the video for "
                "you (just a slower upload)."
            )
        call_type = c_type.selectbox(
            "Call type",
            ["Process Call", "Offer Call", "Renegotiation",
             "Follow-up", "Other"],
            key="call_type_select",
            help="Which call in the sequence? Affects how the methodology "
                 "evaluates the rep's structure.",
        )
        script_used = c_type.selectbox(
            "Script used",
            ["Standard Process Script", "Foreclosure Process Script"],
            key="call_script_used",
            help="Which script the rep was running. The Foreclosure script "
                 "adds the Educational Pivot (4 routes) and three "
                 "foreclosure-specific situation questions to the rubric.",
        )

        # Speaker mapping — let the user say who's speaker 0 vs speaker 1
        # AFTER transcription (Deepgram doesn't know which is rep vs seller).
        # For the analysis step we'll use whatever mapping is current.
        c_s0, c_s1 = st.columns(2)
        speaker_0_label = c_s0.selectbox(
            "Speaker A is…", ["Rep", "Seller", "Other"],
            key="speaker_0_label", index=0,
            help="Diarization separates voices but doesn't know who's who. "
                 "Pick after listening to the first few seconds; can be "
                 "changed before re-running analysis.",
        )
        speaker_1_label = c_s1.selectbox(
            "Speaker B is…", ["Rep", "Seller", "Other"],
            key="speaker_1_label", index=1,
        )
        speaker_labels = {0: speaker_0_label, 1: speaker_1_label}

        do_analyze = st.button(
            "🎯 Transcribe & Analyze",
            type="primary",
            use_container_width=True,
            disabled=(audio_file is None),
        )

        if do_analyze and audio_file is not None:
            # Step 1: transcribe (auto-extracts audio if uploaded a video)
            file_bytes = audio_file.getvalue()
            ext = (audio_file.name.rsplit(".", 1)[-1] or "").lower()
            mime = {
                "mp3": "audio/mpeg", "m4a": "audio/mp4", "m4b": "audio/mp4",
                "wav": "audio/wav", "aac": "audio/aac",
                "mp4": "video/mp4", "mov": "video/quicktime",
                "m4v": "video/mp4", "webm": "video/webm",
            }.get(ext, "audio/mpeg")
            is_video = transcribe_mod.is_video_file(audio_file.name)
            spinner_msg = (
                f"Extracting audio from {ext.upper()} then transcribing "
                "via Deepgram… (1-2 minutes for a video)"
                if is_video
                else "Transcribing audio via Deepgram… (30-60 sec for a 10-min call)"
            )
            with st.spinner(spinner_msg):
                tr = transcribe_mod.transcribe_audio(
                    file_bytes, mime_type=mime,
                    source_filename=audio_file.name,
                )

            if not tr.get("found"):
                st.error(f"Transcription failed: {tr.get('error', 'unknown')}")
            else:
                # Apply user's speaker labels
                tr = transcribe_mod.relabel_speakers(tr, speaker_labels)

                # Step 2: analyze
                with st.spinner("Claude is grading the call against the methodology…"):
                    deal_context = {
                        "property": property_dict,
                        "recommendation": rec,
                        "seller": seller_dict,
                    }
                    analysis = analysis_mod.analyze_call(
                        tr.get("labeled_text", ""),
                        deal_context,
                        call_type=call_type,
                        script_used=script_used,
                    )

                if "error" in analysis:
                    # Show analysis failure to Admin/Manager only — Agents
                    # don't see analysis output at all, even errors.
                    if _can_review:
                        st.error(f"Analysis failed: {analysis['error']}")
                        if analysis.get("raw_response"):
                            with st.expander("Raw response (debug)"):
                                st.code(analysis["raw_response"])
                    else:
                        st.warning(
                            "The recording was uploaded but the analysis step "
                            "failed. Your manager has been notified — they "
                            "may re-run the analysis on their end."
                        )

                # Step 4: persist. Two paths:
                #   (a) Deal already saved → write directly to DB now.
                #   (b) Deal not yet saved → stash in session_state pending
                #       queue; the next Save Deal click will flush it.
                #
                # This way the user can upload+analyze before OR after saving
                # the deal — order doesn't matter, the analysis is never lost.
                save_succeeded = False
                queued_for_save = False
                if "error" not in analysis:
                    payload = {
                        "call_type": call_type,
                        "audio_filename": audio_file.name,
                        "audio_duration_seconds": tr.get("duration_seconds", 0),
                        "transcript": tr,
                        "analysis": analysis,
                        "user_email": user.get("email") if isinstance(user, dict) else None,
                    }
                    if existing_deal_id:
                        try:
                            new_id = save_call_analysis(deal_id=existing_deal_id, **payload)
                            save_succeeded = bool(new_id)
                            if not save_succeeded and _can_review:
                                st.warning(
                                    "Analysis ran but the database write "
                                    "returned no row. Check Supabase logs."
                                )
                        except Exception as e:
                            if _can_review:
                                st.warning(
                                    f"Analysis ran successfully but not saved "
                                    f"to the database: {e}"
                                )
                            else:
                                st.error(
                                    "Upload could not be saved — please try "
                                    "again or contact your manager."
                                )
                    else:
                        # Stash for later — will be flushed by Save Deal
                        pending = st.session_state.get("pending_call_analyses", [])
                        pending.append(payload)
                        st.session_state["pending_call_analyses"] = pending
                        queued_for_save = True

                if "error" not in analysis:
                    if _can_review:
                        # Step 3a: Admin / Manager — render the full analysis
                        st.success(
                            f"✅ Analysis complete — "
                            f"{tr.get('duration_seconds', 0):.0f} sec audio, "
                            f"{analysis.get('_meta', {}).get('input_tokens', '—')} "
                            f"input + "
                            f"{analysis.get('_meta', {}).get('output_tokens', '—')} "
                            f"output tokens."
                        )
                        st.markdown(analysis_mod.format_full_analysis(analysis))
                        with st.expander("📜 Transcript (speaker-labeled)"):
                            st.markdown(tr.get("labeled_text", ""))
                        if queued_for_save:
                            st.info(
                                "📌 This analysis is queued and will save "
                                "automatically the moment you click 💾 Save "
                                "deal above."
                            )
                        elif save_succeeded:
                            st.toast("💾 Saved to deal history", icon="✅")
                    else:
                        # Step 3b: Agent — clean confirmation only, no analysis,
                        # no transcript, no grade. Coaching is the manager's job.
                        if save_succeeded:
                            st.success(
                                "✅ **Call uploaded for review.** Your manager "
                                "will review it and may follow up with you "
                                "about tactics for the next call."
                            )
                        elif queued_for_save:
                            st.success(
                                "✅ **Call uploaded.** Click 💾 Save deal "
                                "above to attach this recording to the deal "
                                "and send it to your manager for review."
                            )
                        else:
                            st.warning(
                                "Call upload had an issue. Try saving the "
                                "deal first, then re-upload."
                            )


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
