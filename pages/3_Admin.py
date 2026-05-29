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
if not st_settings.is_admin(user["email"]):
    st.title("⛔ Admin access required")
    st.error(f"`{user['email']}` is not on the admin list. Contact Jo if you "
             f"should have access.")
    st.stop()

if not supabase_configured():
    st.title("⚙️ Admin — not configured")
    st.error("Supabase is not configured. Add `[supabase]` to Streamlit Secrets "
             "with `url` and `service_role_key` to enable the admin page.")
    st.stop()

st.title("⚙️ Admin Settings")
st.caption(f"Signed in as **{user['email']}** (admin). Changes here apply to "
           f"all users on the next page load.")


# Helper to render a "Save row" inside a form
def _save_button(label: str) -> bool:
    return st.form_submit_button(label, type="primary", use_container_width=True)


tab_repair, tab_strat, tab_fin, tab_emails, tab_export = st.tabs([
    "🔨 Repair Rates",
    "📐 Strategy Thresholds",
    "💵 Financing & Closing",
    "👥 Allow-List",
    "📦 Export / Import",
])


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
            value=int(live["bathroom_full_remodel"]), step=500)

        c1, c2, c3 = st.columns(3)
        new_rates["bathroom_half"] = c1.number_input(
            "Bath — Half (per half-bath)",
            value=int(live.get("bathroom_half", 1_500)), step=100)
        new_rates["appliances"] = c2.number_input(
            "Appliances", value=int(live["appliances"]), step=500)
        new_rates["lighting_all_new"] = c3.number_input(
            "Lighting (all new)",
            value=int(live.get("lighting_all_new", 1_500)), step=100)

        c1, c2, c3 = st.columns(3)
        new_rates["hot_water_tank"] = c1.number_input(
            "Hot water tank",
            value=int(live.get("hot_water_tank", 1_000)), step=100)
        new_rates["cosmetic_demo"] = c2.number_input(
            "Cosmetic demo",
            value=int(live.get("cosmetic_demo", 1_500)), step=100)
        new_rates["final_cleaning"] = c3.number_input(
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
        ("default_assignment_fee", "Default assignment fee", 500),
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
            "BC — Rehab / Retail sale", value=float(live.get("rehab_bc_pct", 0.07)),
            min_value=0.0, max_value=0.15, step=0.005, format="%.3f")
        new_fin["dc_bc_pct"] = c2.number_input(
            "BC — Wholesale DC sale", value=float(live.get("dc_bc_pct", 0.02)),
            min_value=0.0, max_value=0.10, step=0.005, format="%.3f")

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
# TAB 4 — ALLOW-LIST
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
