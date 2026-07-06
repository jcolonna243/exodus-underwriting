"""Admin — Jo-only settings.

Lets the admin edit:
  - Repair rates (per-sqft, per-unit, flat costs used by the rehab calculator)
  - Strategy thresholds (decision rules for the recommendation engine)
  - Financing & closing rates (LTV, interest, points, AB/BC closing percentages)
  - Allow-list emails (who can sign in, who has admin)

Settings are stored in Supabase (table: settings). The hardcoded defaults in
modules/strategy.py are the fallback if nothing is saved.
"""
import json
import streamlit as st
from modules.auth import require_login, sidebar_account_widget
from modules import settings as st_settings
from modules import strategy
from modules.supabase_client import is_configured as supabase_configured

st.set_page_config(page_title="Admin", page_icon="⚙️", layout="wide")
user = require_login()
sidebar_account_widget()

# --- Access control -----------------------------------------------------
# Admin: full edit access. Manager: can VIEW everything except the Roles tab
# but cannot save changes (every form's submit button is hidden / disabled).
# Agents: hard-blocked.
if not st_settings.can_view_admin(user["email"]):
    st.title("⛔ Admin access required")
    st.error(f"`{user['email']}` is not on the admin or manager list. "
             "Contact Jo if you should have access.")
    st.stop()

IS_ADMIN = st_settings.can_edit_admin(user["email"])
IS_MANAGER_RO = (not IS_ADMIN) and st_settings.can_view_admin(user["email"])

if not supabase_configured():
    st.title("⚙️ Admin — not configured")
    st.error("Supabase is not configured. Add `[supabase]` to Streamlit Secrets "
             "with `url` and `service_role_key` to enable the admin page.")
    st.stop()

st.title("⚙️ Admin Settings")
if IS_ADMIN:
    st.caption(f"Signed in as **{user['email']}** (admin). Changes here apply "
               "to all users on the next page load.")
else:
    st.info(
        f"👔 Signed in as **{user['email']}** (manager). "
        "You can view all admin settings here, but cannot make changes. "
        "Contact Jo to update any value."
    )


# Helper to render a "Save row" inside a form. Returns False without rendering
# if the current user is read-only (Manager) — keeps Manager from ever firing
# a save accidentally.
def _save_button(label: str) -> bool:
    if IS_MANAGER_RO:
        st.caption("🔒 Read-only — Manager role cannot save changes.")
        return False
    return st.form_submit_button(label, type="primary", use_container_width=True)


# Tabs — Roles tab is Admin-only (hidden from Manager).
_tab_labels = [
    "🔨 Repair Rates",
    "📐 Strategy Thresholds",
    "💵 Financing & Closing",
    "📊 Comp Settings",
    "🏢 Title Companies",
    "👥 Allow-List",
    "📦 Export / Import",
]
if IS_ADMIN:
    _tab_labels.append("🛂 Roles")

_tabs = st.tabs(_tab_labels)
(tab_repair, tab_strat, tab_fin, tab_comps, tab_title,
 tab_emails, tab_export) = _tabs[:7]
tab_roles = _tabs[7] if IS_ADMIN else None


# ============================================================================
# TAB 1 — REPAIR RATES
# ============================================================================
with tab_repair:
    st.markdown("### Repair Rates")
    st.caption("Per-sqft, per-unit, and flat costs used by the rehab calculator. "
               "Leave a field at its default to use the hardcoded baseline.")

    saved = st_settings.get_setting("repair_rates") or {}
    live = {**strategy.REPAIR_RATES, **saved}

    with st.form("repair_rates_form"):
        new_rates = {}

        st.markdown("**Sqft-driven (cost per square foot)**")
        c1, c2, c3 = st.columns(3)
        new_rates["roof_shingle_per_sqft"] = c1.number_input(
            "Roof — Shingle ($/sf)", value=float(live["roof_shingle_per_sqft"]), step=0.5)
        new_rates["roof_tile_per_sqft"] = c2.number_input(
            "Roof — Tile ($/sf)", value=float(live["roof_tile_per_sqft"]), step=0.5)
        new_rates["roof_flat_per_sqft"] = c3.number_input(
            "Roof — Flat ($/sf)", value=float(live["roof_flat_per_sqft"]), step=0.5)

        st.caption(
            "Roof footprint multipliers — actual roof area = living sqft × this multiplier. "
            "A 2-story house has roughly half the roof area of a 1-story with the same living sqft."
        )
        c1, c2, c3 = st.columns(3)
        new_rates["roof_footprint_pct_1story"] = c1.number_input(
            "1-story multiplier",
            value=float(live.get("roof_footprint_pct_1story", 1.00)),
            min_value=0.30, max_value=1.20, step=0.05, format="%.2f")
        new_rates["roof_footprint_pct_1_5story"] = c2.number_input(
            "1.5-story multiplier",
            value=float(live.get("roof_footprint_pct_1_5story", 0.75)),
            min_value=0.30, max_value=1.20, step=0.05, format="%.2f")
        new_rates["roof_footprint_pct_2story"] = c3.number_input(
            "2-story multiplier",
            value=float(live.get("roof_footprint_pct_2story", 0.55)),
            min_value=0.30, max_value=1.20, step=0.05, format="%.2f")

        c1, c2, c3 = st.columns(3)
        new_rates["interior_paint_full_per_sqft"] = c1.number_input(
            "Interior Paint — Knockdown + Paint ($/sf)",
            value=float(live["interior_paint_full_per_sqft"]), step=0.25)
        new_rates["interior_paint_paint_only_per_sqft"] = c2.number_input(
            "Interior Paint — Paint Only ($/sf)",
            value=float(live["interior_paint_paint_only_per_sqft"]), step=0.25)
        new_rates["interior_paint_texture_per_sqft"] = c3.number_input(
            "Interior Paint — Knockdown Only ($/sf)",
            value=float(live["interior_paint_texture_per_sqft"]), step=0.25)

        c1, c2 = st.columns(2)
        new_rates["exterior_paint_per_sqft"] = c1.number_input(
            "Exterior Paint ($/sf)",
            value=float(live["exterior_paint_per_sqft"]), step=0.5)
        new_rates["flooring_luxury_vinyl_per_sqft"] = c2.number_input(
            "Flooring — Luxury Vinyl ($/sf)",
            value=float(live["flooring_luxury_vinyl_per_sqft"]), step=0.5)

        st.markdown("---")
        st.markdown("**Per-unit**")
        c1, c2, c3 = st.columns(3)
        new_rates["ac_per_ton"] = c1.number_input(
            "A/C ($/ton)", value=int(live["ac_per_ton"]), step=100)
        new_rates["door_exterior_each"] = c2.number_input(
            "Door — Exterior (each)", value=int(live["door_exterior_each"]), step=25)
        new_rates["door_interior_each"] = c3.number_input(
            "Door — Interior (each)", value=int(live["door_interior_each"]), step=25)

        c1, c2, c3 = st.columns(3)
        new_rates["door_patch_paint_each"] = c1.number_input(
            "Door — Patch & Paint (each)",
            value=int(live["door_patch_paint_each"]), step=10)
        new_rates["window_non_impact_each"] = c2.number_input(
            "Window — Non-Impact (each)",
            value=int(live["window_non_impact_each"]), step=25)
        new_rates["window_impact_each"] = c3.number_input(
            "Window — Impact (each)",
            value=int(live["window_impact_each"]), step=25)

        c1, c2 = st.columns(2)
        new_rates["shutter_new_each"] = c1.number_input(
            "Shutter — New (each)", value=int(live["shutter_new_each"]), step=25)
        new_rates["shutter_replace_each"] = c2.number_input(
            "Shutter — Replace (each)",
            value=int(live["shutter_replace_each"]), step=25)

        st.markdown("---")
        st.markdown("**Flat amounts**")
        c1, c2, c3 = st.columns(3)
        new_rates["kitchen_full_remodel"] = c1.number_input(
            "Kitchen — Full remodel",
            value=int(live["kitchen_full_remodel"]), step=500)
        new_rates["kitchen_light_update"] = c2.number_input(
            "Kitchen — Light update",
            value=int(live.get("kitchen_light_update", 5_000)), step=500)
        new_rates["bathroom_full_remodel"] = c3.number_input(
            "Bath — Full (per bath)",
            value=int(live["bathroom_full_remodel"]), step=500,
            help="Full demo, new tile, vanity, shower/tub, fixtures.")

        c1, c2, c3 = st.columns(3)
        new_rates["bathroom_partial_remodel"] = c1.number_input(
            "Bath — Partial (per bath)",
            value=int(live.get("bathroom_partial_remodel", 2_500)), step=250,
            help="Paint, vanity, fixtures, toilet, re-glaze tub if needed.")
        new_rates["bathroom_half"] = c2.number_input(
            "Bath — Half (per half-bath)",
            value=int(live.get("bathroom_half", 1_500)), step=100)
        new_rates["appliances"] = c3.number_input(
            "Appliances", value=int(live["appliances"]), step=500)

        c1, c2, c3 = st.columns(3)
        new_rates["lighting_all_new"] = c1.number_input(
            "Lighting (all new)",
            value=int(live.get("lighting_all_new", 1_500)), step=100)
        new_rates["hot_water_tank"] = c2.number_input(
            "Hot water tank",
            value=int(live.get("hot_water_tank", 1_000)), step=100)
        new_rates["cosmetic_demo"] = c3.number_input(
            "Cosmetic demo",
            value=int(live.get("cosmetic_demo", 1_500)), step=100)

        c1, c2, c3 = st.columns(3)
        new_rates["final_cleaning"] = c1.number_input(
            "Final cleaning",
            value=int(live.get("final_cleaning", 350)), step=50)

        c1, c2, c3 = st.columns(3)
        new_rates["electrical_standard_misc"] = c1.number_input(
            "Electrical — Standard misc",
            value=int(live["electrical_standard_misc"]), step=100)
        new_rates["electrical_breaker_box"] = c2.number_input(
            "Electrical — Breaker box",
            value=int(live["electrical_breaker_box"]), step=100)
        new_rates["electrical_full"] = c3.number_input(
            "Electrical — Full (panel + misc)",
            value=int(live["electrical_full"]), step=100)

        new_rates["landscaping"] = st.number_input(
            "Landscaping", value=int(live["landscaping"]), step=100)

        st.markdown("---")
        st.markdown("**Pool**")
        c1, c2, c3 = st.columns(3)
        new_rates["pool_replace_motor"] = c1.number_input(
            "Pool — Replace motor",
            value=int(live["pool_replace_motor"]), step=50)
        new_rates["pool_replace_pump"] = c2.number_input(
            "Pool — Replace pump",
            value=int(live["pool_replace_pump"]), step=50)
        new_rates["pool_heater"] = c3.number_input(
            "Pool — Heater", value=int(live["pool_heater"]), step=100)
        c1, c2 = st.columns(2)
        new_rates["pool_waterline_tile"] = c1.number_input(
            "Pool — Waterline tile",
            value=int(live["pool_waterline_tile"]), step=100)
        new_rates["pool_diamond_brite"] = c2.number_input(
            "Pool — Diamond brite",
            value=int(live["pool_diamond_brite"]), step=250)

        st.markdown("---")
        st.markdown("**Monthly holding costs**")
        c1, c2 = st.columns(2)
        new_rates["water_no_pool"] = c1.number_input(
            "Water — No pool ($/mo)",
            value=int(live["water_no_pool"]), step=10)
        new_rates["water_with_pool"] = c2.number_input(
            "Water — With pool ($/mo)",
            value=int(live["water_with_pool"]), step=10)

        c1, c2 = st.columns(2)
        new_rates["electric_no_pool"] = c1.number_input(
            "Electric — No pool ($/mo)",
            value=int(live["electric_no_pool"]), step=10)
        new_rates["electric_with_pool"] = c2.number_input(
            "Electric — With pool ($/mo)",
            value=int(live["electric_with_pool"]), step=10)

        st.caption("Insurance is no longer a flat monthly cost — it's calculated "
                   "from the loan amount on the **Financing & Closing** tab.")

        st.markdown("---")
        col_save, col_reset = st.columns([3, 1])
        with col_save:
            saved_ok = _save_button("💾 Save Repair Rates")
        with col_reset:
            reset = st.form_submit_button("Reset to Defaults",
                                          use_container_width=True)

        if saved_ok:
            if st_settings.set_setting("repair_rates", new_rates,
                                       updated_by=user["email"]):
                st.success("✅ Repair rates saved. Changes apply on next page load.")
                st.rerun()
            else:
                st.error("Failed to save. Check Supabase configuration.")
        elif reset:
            if st_settings.set_setting("repair_rates", {},
                                       updated_by=user["email"]):
                st.success("✅ Reset to hardcoded defaults.")
                st.rerun()


# ============================================================================
# TAB 2 — STRATEGY THRESHOLDS
# ============================================================================
with tab_strat:
    st.markdown("### Strategy Decision Thresholds")
    st.caption("Decision rules used by the recommendation engine. Profit floors, "
               "gap thresholds, scope severity, MLS / novation / fat-fee parameters.")

    saved = st_settings.get_setting("strategy_thresholds") or {}
    live = {**strategy.DEFAULTS, **saved}
    THRESH_KEYS = [
        ("rehab_zone_floor", "Rehab zone floor — profit ≥ this = rehab band", 1000),
        ("wholesale_only_floor", "Wholesale-only floor — profit ≥ this = wholesale viable", 1000),
        ("min_profit_threshold", "Minimum profit floor (deal status)", 1000),
        ("gap_marginal_threshold", "Gap marginal — gap > this = marginal", 1000),
        ("gap_too_wide_threshold", "Gap too wide — gap > this = pivot", 1000),
        ("scope_light_max", "Light scope max (rehab ≤ this = Light)", 500),
        ("scope_heavy_min", "Heavy scope min (rehab > this = Heavy)", 1000),
        ("dc_assignment_fee_threshold", "DC trigger — assignment fee ≥ this", 500),
        ("novation_rehab_cap", "Novation rehab cap (rehab must be ≤ this)", 500),
        ("novation_min_floor", "Novation min profit floor", 500),
        ("novation_preferred_target", "Novation preferred target (else 'marginal')", 1000),
        ("novation_holding_costs", "Novation holding costs ($)", 250),
        ("mls_min_commission", "MLS minimum commission to be viable", 250),
        ("fat_fee_buyer_floor", "Fat fee — end buyer should keep ≥ this", 1000),
        ("default_assignment_fee", "Default assignment fee (target)", 500),
        ("assignment_fee_min", "Assignment fee floor (anything below = not worth it)", 250),
        ("assignment_fee_max", "Assignment fee ceiling (above = forces Double Close)", 500),
    ]
    PCT_KEYS = [
        ("mls_rehab_pct_of_arv", "MLS rehab cap as % of ARV"),
        ("mls_commission_rate", "MLS commission rate"),
        ("fat_fee_target_pct", "Fat fee target (% of net profit)"),
        ("novation_retail_costs_pct", "Novation retail costs % of ARV"),
    ]

    with st.form("strategy_thresholds_form"):
        new_thresh = {}
        st.markdown("**Dollar thresholds**")
        for i in range(0, len(THRESH_KEYS), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(THRESH_KEYS):
                    key, label, step = THRESH_KEYS[i + j]
                    new_thresh[key] = col.number_input(
                        label, value=int(live.get(key, 0)), step=step,
                        key=f"thresh_{key}")

        st.markdown("---")
        st.markdown("**Percentage thresholds** (enter as decimals — e.g. 0.08 = 8%)")
        for i in range(0, len(PCT_KEYS), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(PCT_KEYS):
                    key, label = PCT_KEYS[i + j]
                    new_thresh[key] = col.number_input(
                        label, value=float(live.get(key, 0)),
                        step=0.01, format="%.3f",
                        key=f"pct_{key}")

        st.markdown("---")
        col_save, col_reset = st.columns([3, 1])
        with col_save:
            saved_ok = _save_button("💾 Save Strategy Thresholds")
        with col_reset:
            reset = st.form_submit_button("Reset to Defaults",
                                          use_container_width=True)

        if saved_ok:
            if st_settings.set_setting("strategy_thresholds", new_thresh,
                                       updated_by=user["email"]):
                st.success("✅ Strategy thresholds saved.")
                st.rerun()
            else:
                st.error("Failed to save.")
        elif reset:
            if st_settings.set_setting("strategy_thresholds", {},
                                       updated_by=user["email"]):
                st.success("✅ Reset to hardcoded defaults.")
                st.rerun()


# ============================================================================
# TAB 3 — FINANCING & CLOSING
# ============================================================================
with tab_fin:
    st.markdown("### Financing & Closing Cost Rates")
    st.caption("Hard-money loan parameters + AB / BC closing percentages + "
               "target ROI used by the MAO calculator.")

    saved = st_settings.get_setting("financing_params") or {}
    live = {**strategy.DEFAULTS, **saved}

    with st.form("financing_form"):
        new_fin = {}
        st.markdown("**Hard money loan (LTC model)**")
        c1, c2, c3 = st.columns(3)
        new_fin["ltc"] = c1.number_input(
            "LTC (loan-to-cost)", value=float(live.get("ltc", live.get("ltv", 0.90))),
            min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
            help="Loan = LTC × purchase price, capped by ARV ratio below")
        new_fin["arv_loan_cap"] = c2.number_input(
            "ARV loan cap", value=float(live.get("arv_loan_cap", 0.75)),
            min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
            help="Loan cannot exceed this × ARV")
        new_fin["interest_rate"] = c3.number_input(
            "Interest rate (annual)", value=float(live["interest_rate"]),
            min_value=0.0, max_value=0.5, step=0.005, format="%.3f")

        c1, c2, c3 = st.columns(3)
        new_fin["origination_flat"] = c1.number_input(
            "Origination flat fee ($)", value=int(live.get("origination_flat", 999)),
            min_value=0, step=50)
        new_fin["origination_pct"] = c2.number_input(
            "Origination % (points)", value=float(live.get("origination_pct", live.get("points", 0.015))),
            min_value=0.0, max_value=0.1, step=0.005, format="%.3f")
        new_fin["loan_duration_months"] = c3.number_input(
            "Loan duration (months)", value=int(live["loan_duration_months"]),
            min_value=1, max_value=24, step=1)

        st.markdown("---")
        st.markdown("**Strategy-specific closing percentages**")
        st.caption("AB = our share at purchase. BC = our share at sale. The engine "
                   "picks (AB, BC) automatically from acquisition type + disposition.")
        c1, c2, c3 = st.columns(3)
        new_fin["regular_ab_pct"] = c1.number_input(
            "AB — Regular purchase", value=float(live.get("regular_ab_pct", 0.04)),
            min_value=0.0, max_value=0.10, step=0.005, format="%.3f")
        new_fin["short_sale_ab_pct"] = c2.number_input(
            "AB — Short sale", value=float(live.get("short_sale_ab_pct", 0.02)),
            min_value=0.0, max_value=0.10, step=0.005, format="%.3f")
        new_fin["dc_ab_pct"] = c3.number_input(
            "AB — Double Close", value=float(live.get("dc_ab_pct", 0.04)),
            min_value=0.0, max_value=0.10, step=0.005, format="%.3f")

        c1, c2 = st.columns(2)
        new_fin["rehab_bc_pct"] = c1.number_input(
            "BC — Rehab / Retail sale (legacy flat %)",
            value=float(live.get("rehab_bc_pct", 0.07)),
            min_value=0.0, max_value=0.15, step=0.005, format="%.3f",
            help="Legacy flat % — retained for Double Close and fallback paths. "
                 "For Rehab and Novation strategies, the v24 itemized model below "
                 "replaces this.")
        new_fin["dc_bc_pct"] = c2.number_input(
            "BC — Wholesale DC sale", value=float(live.get("dc_bc_pct", 0.02)),
            min_value=0.0, max_value=0.10, step=0.005, format="%.3f")

        st.markdown("---")
        st.markdown("**v24 Itemized Closing Cost Model** — derived from 7 real "
                    "AB HUDs + 6 BC HUDs. Overrides the flat %s above for "
                    "Rehab/Novation strategies. Backtested against actuals: the "
                    "old flat model under-forecasted by ~$17K/deal; the itemized "
                    "model tracks actuals within ~$5K/deal.")

        st.markdown("**AB (Acquisition) — v24**")
        c1, c2 = st.columns(2)
        new_fin["ab_baseline_flat"] = c1.number_input(
            "AB baseline flat ($) — always charged",
            value=int(live.get("ab_baseline_flat", 4750)), step=100,
            help="Attorney $1,250 + Tax Service $999 + Settlement $850 + "
                 "ALTA endorsement $100 + Lender's Title $700 + Recording $400 "
                 "+ Courier/Scanning/Notary ~$450 = ~$4,750.")
        new_fin["ab_loan_pct"] = c2.number_input(
            "AB loan-related % (of loan)",
            value=float(live.get("ab_loan_pct", 0.0270)),
            min_value=0.0, max_value=0.06, step=0.001, format="%.4f",
            help="1.75% points + 0.35% mtg doc stamps + 0.20% intangible + "
                 "0.40% prepaid interest = 2.70% of the LOAN amount.")

        c1, c2 = st.columns(2)
        new_fin["seller_closings_pickup_flat"] = c1.number_input(
            "Seller-closings pickup — title admin ($)",
            value=int(live.get("seller_closings_pickup_flat", 700)), step=50,
            help="Title search + municipal lien search when we cover seller's closings.")
        new_fin["hoa_estoppel_fee"] = c2.number_input(
            "HOA estoppel fee ($) — when HOA",
            value=int(live.get("hoa_estoppel_fee", 500)), step=50)

        new_fin["short_sale_negotiation_fee"] = st.number_input(
            "Short Sale negotiation fee ($) — flat per SS deal",
            value=int(live.get("short_sale_negotiation_fee", 4000)), step=100)

        st.markdown("**BC (Disposition) — v24**")
        c1, c2 = st.columns(2)
        new_fin["bc_baseline_flat"] = c1.number_input(
            "BC baseline flat ($) — always charged",
            value=int(live.get("bc_baseline_flat", 3000)), step=100,
            help="Attorney $1,250 + Settlement $600–$975 + Title Search $200 + "
                 "Lien Search $450 + Wire/Courier/Admin ~$150 = ~$3,000.")
        new_fin["bc_commission_pct"] = c2.number_input(
            "BC commission % (default retail sale)",
            value=float(live.get("bc_commission_pct", 0.055)),
            min_value=0.03, max_value=0.08, step=0.005, format="%.3f",
            help="Realtor commission at retail sale. Range 5.0–6.0% typical. "
                 "Per-deal override available on New Deal page.")

        c1, c2 = st.columns(2)
        new_fin["bc_commission_listing_share"] = c1.number_input(
            "Listing agent share of commission",
            value=float(live.get("bc_commission_listing_share", 0.5)),
            min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
            help="What portion of the total commission goes to the listing side. "
                 "Since your listing agent is a family member, this share is "
                 "shown separately as 'internally recoverable' on the memo.")
        new_fin["bc_doc_stamp_pct"] = c2.number_input(
            "Deed doc stamp % (FL statutory)",
            value=float(live.get("bc_doc_stamp_pct", 0.007)),
            min_value=0.005, max_value=0.01, step=0.0005, format="%.4f",
            help="Florida statutory rate on deed. 0.70% everywhere except "
                 "Miami-Dade single-family. Don't change unless statute changes.")

        new_fin["bc_owner_title_pct_seller_pays"] = st.number_input(
            "Owner's title policy % (seller-pays counties only)",
            value=float(live.get("bc_owner_title_pct_seller_pays", 0.004)),
            min_value=0.002, max_value=0.006, step=0.0005, format="%.4f",
            help="What we pay at BC in seller-pays counties (Palm Beach, most "
                 "of FL). In Broward/Miami-Dade/Sarasota/Collier, the buyer "
                 "pays this and we contribute $0.")

        st.markdown("---")
        st.markdown("**Insurance (lender-required, scales with loan)**")
        c1, c2 = st.columns(2)
        new_fin["insurance_per_100k_monthly"] = c1.number_input(
            "$/mo per $100k of loan",
            value=int(live.get("insurance_per_100k_monthly", 244)), step=10)
        new_fin["insurance_bracket"] = c2.number_input(
            "Round loan to nearest ($)",
            value=int(live.get("insurance_bracket", 25_000)), step=5_000)

        st.markdown("---")
        st.markdown("**Targets**")
        c1, c2 = st.columns(2)
        new_fin["target_roi"] = c1.number_input(
            "Target ROI", value=float(live["target_roi"]),
            min_value=0.0, max_value=1.0, step=0.01, format="%.3f")
        new_fin["min_profit_threshold"] = c2.number_input(
            "Minimum profit threshold ($)",
            value=int(live["min_profit_threshold"]), step=1000)

        st.markdown("---")
        col_save, col_reset = st.columns([3, 1])
        with col_save:
            saved_ok = _save_button("💾 Save Financing Settings")
        with col_reset:
            reset = st.form_submit_button("Reset to Defaults",
                                          use_container_width=True)

        if saved_ok:
            if st_settings.set_setting("financing_params", new_fin,
                                       updated_by=user["email"]):
                st.success("✅ Financing settings saved.")
                st.rerun()
            else:
                st.error("Failed to save.")
        elif reset:
            if st_settings.set_setting("financing_params", {},
                                       updated_by=user["email"]):
                st.success("✅ Reset to hardcoded defaults.")
                st.rerun()


# ============================================================================
# TAB 4 — COMP SETTINGS (filters + adjustments + test button)
# ============================================================================
with tab_comps:
    st.markdown("### Comp Filter Rules")
    st.caption("These rules filter the comps that RentCast returns. Loosen them "
               "if you're getting too few comps in sparse markets.")

    # Comp filter rules and adjustments live in the strategy_thresholds bucket
    # (they're admin-overrideable like every other parameter).
    saved = st_settings.get_setting("comp_settings") or {}
    live = {**strategy.DEFAULTS, **saved}

    with st.form("comp_settings_form"):
        new_comps = {}
        c1, c2, c3 = st.columns(3)
        new_comps["comp_max_radius_miles"] = c1.number_input(
            "Max radius (miles)",
            value=float(live.get("comp_max_radius_miles", 0.5)),
            min_value=0.05, max_value=5.0, step=0.05, format="%.2f")
        new_comps["comp_max_days_old"] = c2.number_input(
            "Max age (days sold)",
            value=int(live.get("comp_max_days_old", 180)),
            min_value=30, max_value=730, step=30)
        new_comps["comp_count"] = c3.number_input(
            "Comp count (5-25)",
            value=int(live.get("comp_count", 7)),
            min_value=1, max_value=25, step=1)

        c1, c2, c3 = st.columns(3)
        new_comps["comp_sqft_tolerance_pct"] = c1.number_input(
            "Sqft tolerance (±%)",
            value=float(live.get("comp_sqft_tolerance_pct", 0.25)),
            min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        new_comps["comp_beds_tolerance"] = c2.number_input(
            "Beds tolerance (±)",
            value=int(live.get("comp_beds_tolerance", 1)),
            min_value=0, max_value=3, step=1)
        new_comps["comp_baths_tolerance"] = c3.number_input(
            "Baths tolerance (±)",
            value=float(live.get("comp_baths_tolerance", 0.5)),
            min_value=0.0, max_value=2.0, step=0.5, format="%.1f")

        new_comps["comp_year_tolerance"] = st.number_input(
            "Year built tolerance (± years)",
            value=int(live.get("comp_year_tolerance", 15)),
            min_value=0, max_value=50, step=1)

        st.markdown("---")
        st.markdown("### Price Adjustments")
        st.caption("Dollar values for amenity differences between subject and comp. "
                   "Tune these to match your market — South Florida defaults shown.")
        c1, c2, c3 = st.columns(3)
        new_comps["adj_pool"] = c1.number_input(
            "Pool ($)", value=int(live.get("adj_pool", 25_000)), step=1_000)
        new_comps["adj_waterfront_canal"] = c2.number_input(
            "Waterfront — Canal/Lake ($)",
            value=int(live.get("adj_waterfront_canal", 75_000)), step=5_000)
        new_comps["adj_waterfront_ocean"] = c3.number_input(
            "Waterfront — Ocean ($)",
            value=int(live.get("adj_waterfront_ocean", 250_000)), step=10_000)

        c1, c2, c3 = st.columns(3)
        new_comps["adj_garage_1car"] = c1.number_input(
            "Garage — 1-car ($)",
            value=int(live.get("adj_garage_1car", 10_000)), step=1_000)
        new_comps["adj_garage_2car"] = c2.number_input(
            "Garage — 2-car or more ($)",
            value=int(live.get("adj_garage_2car", 20_000)), step=1_000)
        new_comps["adj_extra_bedroom"] = c3.number_input(
            "Extra bedroom ($ each)",
            value=int(live.get("adj_extra_bedroom", 15_000)), step=1_000)

        new_comps["adj_extra_half_bath"] = st.number_input(
            "Extra half-bath ($ each)",
            value=int(live.get("adj_extra_half_bath", 7_500)), step=500)

        st.markdown("---")
        col_save, col_reset = st.columns([3, 1])
        with col_save:
            saved_ok = _save_button("💾 Save Comp Settings")
        with col_reset:
            reset = st.form_submit_button("Reset to Defaults",
                                          use_container_width=True)

        if saved_ok:
            if st_settings.set_setting("comp_settings", new_comps,
                                       updated_by=user["email"]):
                st.success("✅ Comp settings saved.")
                st.rerun()
            else:
                st.error("Failed to save.")
        elif reset:
            if st_settings.set_setting("comp_settings", {},
                                       updated_by=user["email"]):
                st.success("✅ Reset to hardcoded defaults.")
                st.rerun()

    # --- Test button (outside the form so it can re-run independently) ---
    st.markdown("---")
    st.markdown("### 🧪 Test current settings")
    st.caption("Enter an address to preview what comps come back with your current "
               "filter rules and adjustments. Costs 1 RentCast lookup per test.")
    c_addr, c_btn = st.columns([3, 1])
    test_addr = c_addr.text_input("Test address", key="test_comp_address",
                                  placeholder="e.g. 8420 SW 152nd St, Miami, FL 33176")
    test_btn = c_btn.button("🧪 Run Test", use_container_width=True)

    if test_btn and test_addr.strip():
        from modules import property_lookup as _pl
        from modules.comp_import import filter_comps, adjust_all
        params = {**strategy.DEFAULTS, **(st_settings.get_setting("comp_settings") or {})}
        with st.spinner("Pulling test comps…"):
            # First look up the subject so we know its sqft / beds / etc. for filtering
            subj = _pl.lookup_property(test_addr)
            if not subj.get("found"):
                st.error(f"Couldn't look up subject: {subj.get('error', 'no match')}")
            else:
                res = _pl.fetch_comps(
                    test_addr, property_type=subj.get("property_type", "Single Family Residence"),
                    radius=params.get("comp_max_radius_miles", 0.5),
                    days_old=params.get("comp_max_days_old", 180),
                    comp_count=params.get("comp_count", 7),
                )
                if res.get("error"):
                    st.error(f"Pull failed: {res['error']}")
                else:
                    raw = res.get("comps", [])
                    filtered = filter_comps(raw, subj, params)
                    adj_table = {k: params.get(k, 0) for k in [
                        "adj_pool", "adj_waterfront_canal", "adj_waterfront_ocean",
                        "adj_garage_1car", "adj_garage_2car",
                        "adj_extra_bedroom", "adj_extra_half_bath",
                    ]}
                    adjusted = adjust_all(filtered, subj, adj_table)
                    st.success(f"Pulled {len(raw)} raw comps, "
                               f"{len(filtered)} passed filters. "
                               f"RentCast AVM: ${res.get('subject_avm', 0):,.0f}")
                    if adjusted:
                        import pandas as pd
                        cols = ["address", "sqft", "beds", "baths", "year",
                                "sold_price", "adjusted_price", "sold_date",
                                "distance"]
                        df = pd.DataFrame(adjusted)
                        for c in cols:
                            if c not in df.columns: df[c] = None
                        st.dataframe(df[cols], use_container_width=True,
                                     hide_index=True)


# ============================================================================
# TAB 5 — TITLE COMPANIES (used by Prepare Contract — Escrow Agent block)
# ============================================================================
with tab_title:
    st.markdown("### Title Companies (for the Prepare Contract page)")
    st.caption(
        "Companies entered here appear in the **Title Company** dropdown on "
        "the Prepare Contract page. The name + contact + phone + email + "
        "address auto-populate the Escrow Agent block of paragraph 2 in the "
        "Purchase & Sale Agreement when a contract is generated."
    )

    saved_title = st_settings.get_setting("title_companies") or []
    if isinstance(saved_title, list) and not saved_title:
        saved_title = [{"name": "", "contact_name": "", "phone": "",
                        "email": "", "address": ""}]

    with st.form("title_companies_form"):
        st.markdown("**One row per title company.** Leave a row blank to remove it.")
        new_rows = []
        for i in range(max(len(saved_title), 1) + 1):  # always one extra blank row
            row = saved_title[i] if i < len(saved_title) else {}
            st.markdown(f"**Title Company #{i+1}**")
            c1, c2 = st.columns([2, 2])
            name = c1.text_input(
                "Company name", value=row.get("name", ""),
                key=f"tc_name_{i}",
                placeholder="e.g. Premier Title & Escrow LLC",
            )
            contact = c2.text_input(
                "Contact name (closer / paralegal)",
                value=row.get("contact_name", ""), key=f"tc_contact_{i}",
                placeholder="e.g. Maria Lopez",
            )
            c1, c2 = st.columns([1, 2])
            phone = c1.text_input(
                "Phone", value=row.get("phone", ""), key=f"tc_phone_{i}",
                placeholder="(954) 555-7890",
            )
            email = c2.text_input(
                "Email", value=row.get("email", ""), key=f"tc_email_{i}",
                placeholder="closings@example.com",
            )
            address = st.text_input(
                "Address", value=row.get("address", ""), key=f"tc_address_{i}",
                placeholder="100 NE 5th St, Fort Lauderdale, FL 33301",
            )
            st.markdown("---")
            # Only keep rows that have at least a name
            if name.strip():
                new_rows.append({
                    "name": name.strip(),
                    "contact_name": contact.strip(),
                    "phone": phone.strip(),
                    "email": email.strip(),
                    "address": address.strip(),
                })

        if _save_button("💾 Save Title Companies"):
            if st_settings.set_setting("title_companies", new_rows,
                                        updated_by=user["email"]):
                st.success(f"Saved {len(new_rows)} title companies.")
                st.rerun()
            else:
                st.error("Failed to save. Check Supabase logs.")


# ============================================================================
# TAB 6 — ALLOW-LIST
# ============================================================================
with tab_emails:
    st.markdown("### Sign-in Allow-List")
    st.caption("Emails on the **allowed list** can sign in. Emails on the "
               "**admin list** can also see this page and edit settings.")

    allowed_now = st_settings.get_setting("allowed_emails") or []
    admins_now = st_settings.get_setting("admin_emails") or ["jo@exoduspropertysolutions.com"]

    with st.form("emails_form"):
        st.markdown("**Allowed emails** (one per line)")
        allowed_text = st.text_area(
            "Allowed emails",
            value="\n".join(allowed_now),
            height=150,
            label_visibility="collapsed",
            help="Anyone with a @exoduspropertysolutions.com Workspace account "
                 "AND on this list can sign in.",
        )

        st.markdown("**Admin emails** (one per line — subset of allowed)")
        admins_text = st.text_area(
            "Admin emails",
            value="\n".join(admins_now),
            height=100,
            label_visibility="collapsed",
            help="Admins can see this Admin page and edit all settings. "
                 "Keep this list small.",
        )

        saved_ok = _save_button("💾 Save Allow-List")

        if saved_ok:
            new_allowed = [e.strip() for e in allowed_text.splitlines() if e.strip()]
            new_admins = [e.strip() for e in admins_text.splitlines() if e.strip()]
            # Safety: prevent locking yourself out
            if user["email"] not in new_allowed:
                new_allowed.append(user["email"])
            if user["email"] not in new_admins:
                st.warning("⚠️ You removed yourself from the admin list. "
                           "Adding yourself back as a safety measure.")
                new_admins.append(user["email"])

            ok1 = st_settings.set_setting("allowed_emails", new_allowed,
                                          updated_by=user["email"])
            ok2 = st_settings.set_setting("admin_emails", new_admins,
                                          updated_by=user["email"])
            if ok1 and ok2:
                st.success(f"✅ Saved. {len(new_allowed)} allowed, "
                           f"{len(new_admins)} admin.")
                st.rerun()
            else:
                st.error("Failed to save.")


# ============================================================================
# TAB 5 — EXPORT / IMPORT
# ============================================================================
with tab_export:
    st.markdown("### Export / Import All Settings")
    st.caption("Use Export to save a JSON backup of every setting. Use Import "
               "to restore from a backup or copy settings between environments.")

    snapshot = st_settings.list_settings()
    snapshot_json = json.dumps(snapshot, indent=2, default=str)
    st.markdown("**Current settings (JSON)**")
    st.code(snapshot_json, language="json")
    st.download_button(
        "📥 Download settings.json",
        snapshot_json.encode("utf-8"),
        file_name="exodus_settings.json",
        mime="application/json",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("**Import settings**")
    uploaded = st.file_uploader("Upload a previously exported settings.json",
                                type=["json"])
    if uploaded is not None:
        try:
            data = json.loads(uploaded.read().decode("utf-8"))
            st.write("Preview:")
            st.json(data)
            if st.button("⚠️ Import & overwrite all current settings",
                         type="primary"):
                ok = True
                for k, v in data.items():
                    if not st_settings.set_setting(k, v, updated_by=user["email"]):
                        ok = False
                if ok:
                    st.success("✅ Imported. Reloading…")
                    st.rerun()
                else:
                    st.error("Some settings failed to import. Check format.")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")


# ============================================================================
# TAB 7 — ROLES (Admin-only; hidden from Manager)
# ============================================================================
if tab_roles is not None:
    with tab_roles:
        st.markdown("### User Roles")
        st.caption(
            "Three roles control what each user sees in the app:\n\n"
            "- **Admin** — full edit access everywhere. Can assign roles.\n"
            "- **Manager** — can view this page (read-only) and access the "
            "📞 Call Reviews page. Can underwrite deals.\n"
            "- **Agent** — can underwrite deals and upload call recordings, "
            "but does NOT see call analysis output (coaching is the "
            "manager's job).\n\n"
            "Any allowed-list user who isn't assigned here defaults to **Agent**."
        )

        current_roles = st_settings.list_user_roles()

        allowed_now = (st_settings.get_setting("allowed_emails")
                       or ["jo@exoduspropertysolutions.com"])
        allowed_lower = sorted({e.lower().strip() for e in allowed_now})

        # Show a table-style view so it's easy to scan
        st.markdown("**Current assignments**")
        if not allowed_lower:
            st.info("No users on the allow-list yet. Add emails on the "
                    "Allow-List tab first.")
        else:
            with st.form("roles_form"):
                new_roles_dict = {}
                # Render one row per allowed email
                for em in allowed_lower:
                    c_em, c_role = st.columns([3, 2])
                    c_em.write(em)
                    current_role = current_roles.get(em, "agent")
                    idx = ["admin", "manager", "agent"].index(current_role) \
                        if current_role in ("admin", "manager", "agent") else 2
                    picked = c_role.selectbox(
                        f"Role for {em}",
                        ["admin", "manager", "agent"],
                        index=idx,
                        key=f"role_{em}",
                        label_visibility="collapsed",
                    )
                    new_roles_dict[em] = picked

                st.markdown("---")
                # Sanity: the current admin user can't demote themselves below
                # admin in the same save (that'd lock them out).
                current_email = user["email"].lower().strip()
                save_clicked = _save_button("💾 Save role assignments")
                if save_clicked:
                    if new_roles_dict.get(current_email) != "admin":
                        st.warning(
                            "⚠️ You can't demote yourself from Admin in this "
                            "tab — that would lock you out. Keep your own "
                            "row set to admin (or have another admin do it)."
                        )
                    else:
                        ok = st_settings.set_setting(
                            "user_roles", new_roles_dict,
                            updated_by=user["email"],
                        )
                        if ok:
                            st.success(
                                f"✅ Saved. "
                                f"{sum(1 for v in new_roles_dict.values() if v == 'admin')} admin, "
                                f"{sum(1 for v in new_roles_dict.values() if v == 'manager')} manager, "
                                f"{sum(1 for v in new_roles_dict.values() if v == 'agent')} agent."
                            )
                            st.rerun()
                        else:
                            st.error("Failed to save role assignments.")

        st.markdown("---")
        st.caption(
            "💡 Adding a new user: first add their email to the **👥 Allow-List** "
            "tab. Once they appear above, you can assign their role here. "
            "They sign in with their Google account at the app URL."
        )
