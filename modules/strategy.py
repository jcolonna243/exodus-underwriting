"""Core strategy decision logic — ported from Exodus Underwriting Tool v3 Excel.

This module is a pure-Python replica of every formula on the v3 spreadsheet.
Given a set of inputs (property details, rehab toggles, comps, seller info,
financing assumptions, novation parameters), it produces:
  - Cash MAO and Wholesale MAO
  - Net Profit and ROI at MAO
  - Deal Status (GO/CAUTION/NO-GO)
  - Strategy recommendation (Wholesale Assignment, Wholesale DC, Rehab,
    Short Sale, Novation, Novation — Marginal, MLS Referral, or Pass)
  - Opening Offer / Walk-Away / Stretch Ceiling
  - Target Assignment Fee (with fat-fee logic for heavy-scope deals)
  - Rationale paragraph
  - Strategy-specific action items
  - Diagnostic flags for transparency

The output of compute_recommendation() is a single dict (RecommendationResult)
that the UI renders directly. Every number in that dict is reproducible by
running the same inputs through Excel v3.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
import math


# ============================================================================
# DEFAULTS — admin-editable financial parameters
# ============================================================================
DEFAULTS = {
    # Hard money loan model (Loan-to-Cost, capped by ARV)
    "ltc": 0.90,                          # loan = ltc × purchase (capped below)
    "arv_loan_cap": 0.75,                 # loan ≤ this × ARV
    "interest_rate": 0.10,                # 10% annual
    "origination_flat": 999,              # $999 flat origination fee
    "origination_pct": 0.015,             # 1.5% of loan, points-equivalent
    "loan_duration_months": 6,
    # Legacy keys kept for backward-compat with saved deals / settings.
    # New code should NOT read these — use the strategy-specific keys below.
    "ltv": 0.90,
    "points": 0.015,
    "purchase_closing_pct": 0.04,
    "sale_closing_pct": 0.07,
    # Legacy strategy-specific closing percentages — kept for backward compat
    # and used as FALLBACKS if the v24 itemized model is disabled. New code
    # should use the ab_baseline_flat / bc_baseline_flat / etc. keys below.
    "regular_ab_pct": 0.04,               # Standard purchase, financed
    "short_sale_ab_pct": 0.02,            # Bank covers seller's portion
    "dc_ab_pct": 0.04,                    # Includes ~1% transactional funding
    "rehab_bc_pct": 0.07,                 # FL retail: 5.5% comm + 0.7% doc stamps + misc
    "dc_bc_pct": 0.02,                    # No commission, just doc stamps + closing

    # ---- v24 itemized closing cost model ---------------------------------
    # Built from analysis of 7 real AB HUDs and 6 real BC HUDs (2024–2025).
    # Fixed baseline captures the always-charged flat fees; loan_pct/comm_pct/etc.
    # capture the % components; situational items get added on the deal form.
    #
    # AB (buyer / acquisition):
    "ab_baseline_flat": 4750,             # Attorney $1,250 + Tax Service $999 + Settlement $850
                                           #  + ALTA 8.1 $100 + Lender's Title $700 + Recording
                                           #  $400 + Courier/Scanning/Notary ~$450 = ~$4,750
    "ab_loan_pct": 0.0270,                # 1.75% points + 0.35% mortgage doc stamps
                                           #  + 0.20% intangible + 0.40% prepaid interest
                                           #  = 2.70% of the LOAN amount (not purchase)
    # AB when we're absorbing the seller's typical closing items (equity deals
    # where we sweetened the offer by paying their side):
    "seller_closings_pickup_flat": 700,   # Title search + municipal lien search
    "hoa_estoppel_fee": 500,              # When property is in an HOA
    # Short sale:
    "short_sale_negotiation_fee": 4000,   # Flat coordinator/negotiator fee per SS
    # BC (seller / disposition):
    "bc_baseline_flat": 3000,             # Attorney $1,250 + Settlement $600–$975
                                           #  + Title Search $200 + Lien Search $450
                                           #  + Wire/Courier/Admin ~$150 = ~$3,000
    "bc_commission_pct": 0.055,           # Realtor commission — 5.0–6.0% range,
                                           #  5.5% default; UI can override per deal
    "bc_commission_listing_share": 0.5,   # Half of commission is listing side,
                                           #  which is internally recoverable (family
                                           #  member is licensed listing agent).
                                           #  Reported as expense with an asterisk.
    "bc_doc_stamp_pct": 0.007,            # 0.70% of sale price — FL statutory
    "bc_owner_title_pct_seller_pays": 0.004,  # 0.40% of sale — only in seller-pays
                                                #  counties. In buyer-pays counties this
                                                #  drops to 0 (buyer absorbs it).
    # Which FL counties customarily have the BUYER pay the owner's title policy.
    # In these counties, we (seller at BC) don't pay it; in all other counties
    # we do. Verified across the 6 BC closings.
    "buyer_pays_owner_title_counties": ["Broward", "Miami-Dade", "Sarasota", "Collier"],
    # Insurance (lender-required, scales with loan)
    "insurance_per_100k_monthly": 244,    # $244/mo per $100k of loan
    "insurance_bracket": 25_000,          # round loan to this for insurance calc
    # Targets and thresholds
    "target_roi": 0.10,
    "default_assignment_fee": 15_000,
    # Assignment fee practical floor/ceiling. Anything above the ceiling
    # triggers end-buyer title-attorney scrutiny ("why is the wholesaler
    # taking $X off the table?"), so deals with bigger spreads are routed
    # to Double Close instead — DC hides the end-buyer price from the seller
    # and captures the full margin cleanly. Anything below the floor isn't
    # worth the effort of an assignment.
    "assignment_fee_min": 5_000,
    "assignment_fee_max": 25_000,
    "min_profit_threshold": 30_000,
    # Novation
    "novation_retail_costs_pct": 0.09,
    "novation_holding_costs": 3_000,
    "novation_min_floor": 10_000,
    "novation_preferred_target": 30_000,
    # Strategy thresholds
    "rehab_zone_floor": 50_000,
    "wholesale_only_floor": 30_000,
    "gap_marginal_threshold": 50_000,
    "gap_too_wide_threshold": 70_000,
    "scope_light_max": 20_000,
    "scope_heavy_min": 80_000,
    "dc_assignment_fee_threshold": 25_000,
    "novation_rehab_cap": 30_000,
    "mls_rehab_pct_of_arv": 0.08,
    "mls_min_commission": 8_000,
    "mls_commission_rate": 0.03,
    "fat_fee_buyer_floor": 50_000,
    "fat_fee_target_pct": 0.25,
    # Comp filter rules (used when pulling comps from RentCast)
    "comp_max_radius_miles": 0.5,
    "comp_max_days_old": 180,          # 6 months
    "comp_sqft_tolerance_pct": 0.25,   # ±25%
    "comp_beds_tolerance": 1,          # subject ±1 bed
    "comp_baths_tolerance": 0.5,       # ±0.5 bath
    "comp_year_tolerance": 15,         # ±15 years
    "comp_count": 7,                   # ask RentCast for this many
    # Comp price adjustments (SoFL market norms — admin-editable)
    "adj_pool": 25_000,
    "adj_waterfront_canal": 75_000,
    "adj_waterfront_ocean": 250_000,
    "adj_garage_1car": 10_000,
    "adj_garage_2car": 20_000,
    "adj_extra_bedroom": 15_000,
    "adj_extra_half_bath": 7_500,
}


# ============================================================================
# REPAIR RATE TABLE — mirror of Repair Rates tab.
# Hardcoded values below are the fallback defaults. The admin page can override
# any of these via modules/settings.py; the live values are returned by
# get_repair_rates() and get_strategy_defaults().
# ============================================================================
REPAIR_RATES = {
    # Sqft-driven
    "roof_flat_per_sqft": 11,
    "roof_shingle_per_sqft": 10.5,
    "roof_tile_per_sqft": 19,
    "interior_paint_texture_per_sqft": 1.5,
    "interior_paint_paint_only_per_sqft": 3,
    "interior_paint_full_per_sqft": 3.5,
    "exterior_paint_per_sqft": 4,
    "flooring_luxury_vinyl_per_sqft": 6,
    # Per-unit
    "ac_per_ton": 2_500,
    "door_exterior_each": 725,
    "door_interior_each": 150,
    "door_patch_paint_each": 50,
    "window_non_impact_each": 600,
    "window_impact_each": 900,
    "shutter_new_each": 500,
    "shutter_replace_each": 150,
    # Flat
    "kitchen_full_remodel": 12_000,
    "kitchen_light_update": 5_000,           # NEW: refresh vs full remodel
    "bathroom_full_remodel": 6_000,          # full demo + tile + vanity + fixtures
    "bathroom_partial_remodel": 2_500,       # NEW: paint, vanity, fixtures, re-glaze
    "bathroom_half": 1_500,                  # NEW: per half-bath
    # Roof footprint multipliers — actual roof area = sqft × multiplier.
    # A 2-story house has ~half the roof footprint of its living sqft.
    "roof_footprint_pct_1story": 1.00,
    "roof_footprint_pct_1_5story": 0.75,
    "roof_footprint_pct_2story": 0.55,
    "electrical_standard_misc": 1_500,
    "electrical_breaker_box": 2_500,
    "electrical_full": 4_000,
    "landscaping": 1_500,
    "appliances": 6_000,
    "lighting_all_new": 1_500,               # NEW
    "hot_water_tank": 1_000,                 # NEW
    "cosmetic_demo": 1_500,                  # NEW
    "final_cleaning": 350,                   # NEW
    # Pool
    "pool_replace_motor": 800,
    "pool_replace_pump": 1_500,
    "pool_heater": 4_000,
    "pool_waterline_tile": 3_000,
    "pool_diamond_brite": 6_500,
    # Monthly holding
    "water_no_pool": 150,
    "water_with_pool": 200,
    "electric_no_pool": 100,
    "electric_with_pool": 150,
    "insurance_vacant": 1_500,
}


def get_repair_rates() -> Dict[str, float]:
    """Live repair rates: hardcoded defaults merged with any admin overrides
    from Supabase. Falls back to defaults if settings can't be read."""
    try:
        from modules.settings import get_setting
        saved = get_setting("repair_rates") or {}
        return {**REPAIR_RATES, **saved}
    except Exception:
        return REPAIR_RATES


def get_strategy_defaults() -> Dict[str, Any]:
    """Live DEFAULTS: hardcoded defaults merged with admin overrides for strategy
    thresholds, financing params, and comp settings. Falls back on error."""
    try:
        from modules.settings import get_setting
        saved_t = get_setting("strategy_thresholds") or {}
        saved_f = get_setting("financing_params") or {}
        saved_c = get_setting("comp_settings") or {}
        return {**DEFAULTS, **saved_t, **saved_f, **saved_c}
    except Exception:
        return DEFAULTS


# ============================================================================
# REHAB CALCULATOR — mirrors the toggle-based rehab estimate
# ============================================================================
def _roof_footprint_pct(stories: float, r: Dict[str, float]) -> float:
    """Return the roof-area-to-living-sqft ratio for a given story count.
    A 2-story house has roughly 55% the roof footprint of a 1-story with the
    same living area. Defaults: 1 story=1.0, 1.5 story=0.75, 2 story=0.55.
    """
    try:
        s = float(stories)
    except (TypeError, ValueError):
        s = 1.0
    if s >= 2.0:
        return r.get("roof_footprint_pct_2story", 0.55)
    if s >= 1.5:
        return r.get("roof_footprint_pct_1_5story", 0.75)
    return r.get("roof_footprint_pct_1story", 1.0)


def rehab_subtotal(rehab: Dict[str, Any], sqft: int, baths: float, pool: bool,
                   stories: float = 1.0) -> float:
    """Compute total rehab from toggle dict.

    rehab dict keys (all optional; missing = not included):
      roof: {"include": bool, "type": "Shingle"|"Tile"|"Flat"}
      electrical: {"include": bool, "type": "Standard misc work"|"Replace Breaker Box"|"Full (panel + misc)"}
      ac: {"include": bool}
      kitchen: {"include": bool}
      bathrooms: {"include": bool, "full": int, "partial": int}
         If "full" / "partial" not set, all `baths` counted at full rate (legacy).
      interior_paint: {"include": bool, "type": "Knockdown + Paint"|"Paint only"|"Knockdown only"}
      exterior_paint: {"include": bool}
      flooring: {"include": bool}
      doors: {"include": bool, "qty": int, "type": "Exterior Replace"|"Interior Replace"|"Patch & Paint"}
      windows: {"include": bool, "qty": int, "type": "Non-Impact"|"Impact"|"New Shutter"|"Replace Shutter"}
      plumbing: {"include": bool, "amount": float}
      landscaping: {"include": bool}
      appliances: {"include": bool}
      pool: {"include": bool, "type": "Replace Motor"|"Replace Pump"|"Heater"|"Waterline Tile"|"Diamond Brite"}
      other_1: {"include": bool, "amount": float, "description": str}
      other_2: {"include": bool, "amount": float, "description": str}

    `stories` (1, 1.5, or 2) drives the roof footprint calc — a 2-story
    house's roof covers about half its living sqft.
    """
    r = get_repair_rates()
    total = 0.0

    def get(key):
        return rehab.get(key, {}) or {}

    # Roof — actual roof footprint depends on # of stories
    roof = get("roof")
    if roof.get("include"):
        t = roof.get("type", "Shingle")
        rate = {
            "Flat": r["roof_flat_per_sqft"],
            "Shingle": r["roof_shingle_per_sqft"],
            "Tile": r["roof_tile_per_sqft"],
        }.get(t, r["roof_shingle_per_sqft"])
        roof_sqft = sqft * _roof_footprint_pct(stories, r)
        total += roof_sqft * rate

    # Electrical
    el = get("electrical")
    if el.get("include"):
        t = el.get("type", "Standard misc work")
        total += {
            "Standard misc work": r["electrical_standard_misc"],
            "Replace Breaker Box": r["electrical_breaker_box"],
            "Full (panel + misc)": r["electrical_full"],
        }.get(t, r["electrical_standard_misc"])

    # AC — tons based on sqft
    ac = get("ac")
    if ac.get("include"):
        tons = math.ceil(sqft / 500) if sqft > 0 else 0
        total += tons * r["ac_per_ton"]

    # Kitchen — full or light update
    kit = get("kitchen")
    if kit.get("include"):
        kind = kit.get("type", "Full remodel")
        total += r["kitchen_light_update"] if kind == "Light update" else r["kitchen_full_remodel"]

    # Bathrooms — supports a split between Full and Partial remodels.
    # New format:    {"include": True, "full": 2, "partial": 3}
    # Legacy format: {"include": True}  → all `baths` counted at full rate.
    bath_cfg = get("bathrooms")
    if bath_cfg.get("include"):
        if "full" in bath_cfg or "partial" in bath_cfg:
            full_n = bath_cfg.get("full", 0) or 0
            partial_n = bath_cfg.get("partial", 0) or 0
            total += full_n * r["bathroom_full_remodel"]
            total += partial_n * r["bathroom_partial_remodel"]
        else:
            total += baths * r["bathroom_full_remodel"]

    # Half bathrooms (count)
    half = get("half_bathrooms")
    if half.get("include"):
        qty = half.get("qty", 0) or 0
        total += qty * r["bathroom_half"]

    # Interior Paint
    ip = get("interior_paint")
    if ip.get("include"):
        t = ip.get("type", "Knockdown + Paint")
        rate = {
            "Knockdown + Paint": r["interior_paint_full_per_sqft"],
            "Paint only": r["interior_paint_paint_only_per_sqft"],
            "Knockdown only": r["interior_paint_texture_per_sqft"],
        }.get(t, r["interior_paint_full_per_sqft"])
        total += sqft * rate

    # Exterior Paint
    if get("exterior_paint").get("include"):
        total += sqft * r["exterior_paint_per_sqft"]

    # Flooring
    if get("flooring").get("include"):
        total += sqft * r["flooring_luxury_vinyl_per_sqft"]

    # Doors
    doors = get("doors")
    if doors.get("include"):
        qty = doors.get("qty", 0) or 0
        t = doors.get("type", "Interior Replace")
        rate = {
            "Exterior Replace": r["door_exterior_each"],
            "Interior Replace": r["door_interior_each"],
            "Patch & Paint": r["door_patch_paint_each"],
        }.get(t, r["door_interior_each"])
        total += qty * rate

    # Windows / Shutters
    win = get("windows")
    if win.get("include"):
        qty = win.get("qty", 0) or 0
        t = win.get("type", "Non-Impact")
        rate = {
            "Non-Impact": r["window_non_impact_each"],
            "Impact": r["window_impact_each"],
            "New Shutter": r["shutter_new_each"],
            "Replace Shutter": r["shutter_replace_each"],
        }.get(t, r["window_non_impact_each"])
        total += qty * rate

    # Plumbing (manual amount)
    plumb = get("plumbing")
    if plumb.get("include"):
        total += plumb.get("amount", 0) or 0

    # Landscaping — honors per-deal cost override if set
    _land = get("landscaping")
    if _land.get("include"):
        _land_cost = _land.get("cost_override")
        if _land_cost is None or float(_land_cost) <= 0:
            _land_cost = r["landscaping"]
        total += float(_land_cost)

    # Appliances
    if get("appliances").get("include"):
        total += r["appliances"]

    # Lighting refresh
    if get("lighting").get("include"):
        total += r["lighting_all_new"]

    # Hot water tank
    if get("hot_water_tank").get("include"):
        total += r["hot_water_tank"]

    # Cosmetic demo (pre-rehab cleanup)
    if get("cosmetic_demo").get("include"):
        total += r["cosmetic_demo"]

    # Final cleaning
    if get("final_cleaning").get("include"):
        total += r["final_cleaning"]

    # Pool (only if subject has pool). v24.14 — multi-select: sums every
    # checked pool item, honoring per-item cost overrides. Falls back to the
    # legacy single-select {"type": "..."} format for pre-v24.14 saved deals.
    pool_r = get("pool")
    if pool_r.get("include") and pool:
        _pool_defaults = {
            "Replace Motor": r["pool_replace_motor"],
            "Replace Pump": r["pool_replace_pump"],
            "Heater": r["pool_heater"],
            "Waterline Tile": r["pool_waterline_tile"],
            "Diamond Brite": r["pool_diamond_brite"],
        }
        _pool_items = pool_r.get("items") or {}
        if _pool_items:
            for _name, _cfg in _pool_items.items():
                if _cfg.get("selected"):
                    _c = _cfg.get("cost")
                    if _c is None or float(_c) <= 0:
                        _c = _pool_defaults.get(_name, 0)
                    total += float(_c)
        else:
            # Legacy single-select fallback
            t = pool_r.get("type", "Replace Motor")
            total += _pool_defaults.get(t, r["pool_replace_motor"])

    # Other (manual)
    for key in ("other_1", "other_2"):
        o = get(key)
        if o.get("include"):
            total += o.get("amount", 0) or 0

    return total


def rehab_with_contingency(subtotal: float) -> float:
    """Subtotal + contingency (10% if subtotal > $50k, else $5k flat)."""
    contingency = subtotal * 0.10 if subtotal > 50_000 else 5_000
    return subtotal + contingency


def rehab_breakdown(rehab: Dict[str, Any], sqft: int, baths: float, pool: bool,
                    stories: float = 1.0) -> List[tuple]:
    """Return a list of (item_name, amount) for each included rehab line item.
    Items not included (toggle=No) are omitted.

    `stories` (1, 1.5, or 2) drives the roof footprint calc.
    """
    r = get_repair_rates()
    items = []
    def get(key): return rehab.get(key, {}) or {}

    roof = get("roof")
    if roof.get("include"):
        t = roof.get("type", "Shingle")
        rate = {"Flat": r["roof_flat_per_sqft"], "Shingle": r["roof_shingle_per_sqft"],
                "Tile": r["roof_tile_per_sqft"]}.get(t, r["roof_shingle_per_sqft"])
        roof_pct = _roof_footprint_pct(stories, r)
        roof_sqft = sqft * roof_pct
        label_stories = f"{stories:g}-story" if float(stories) != 1.0 else "1-story"
        items.append(
            (f"Roof ({t}, {label_stories}, {roof_sqft:,.0f} sf footprint × ${rate}/sf)",
             roof_sqft * rate)
        )

    el = get("electrical")
    if el.get("include"):
        t = el.get("type", "Standard misc work")
        amt = {"Standard misc work": r["electrical_standard_misc"],
               "Replace Breaker Box": r["electrical_breaker_box"],
               "Full (panel + misc)": r["electrical_full"]}.get(t, r["electrical_standard_misc"])
        items.append((f"Electrical ({t})", amt))

    if get("ac").get("include"):
        tons = math.ceil(sqft / 500) if sqft > 0 else 0
        items.append((f"A/C ({tons} ton{'s' if tons != 1 else ''} × ${r['ac_per_ton']:,})",
                      tons * r["ac_per_ton"]))

    kit = get("kitchen")
    if kit.get("include"):
        kind = kit.get("type", "Full remodel")
        if kind == "Light update":
            items.append(("Kitchen (light update)", r["kitchen_light_update"]))
        else:
            items.append(("Kitchen (full remodel)", r["kitchen_full_remodel"]))

    bath_cfg = get("bathrooms")
    if bath_cfg.get("include"):
        if "full" in bath_cfg or "partial" in bath_cfg:
            full_n = bath_cfg.get("full", 0) or 0
            partial_n = bath_cfg.get("partial", 0) or 0
            if full_n > 0:
                items.append(
                    (f"Bathrooms — Full ({full_n} × ${r['bathroom_full_remodel']:,})",
                     full_n * r["bathroom_full_remodel"])
                )
            if partial_n > 0:
                items.append(
                    (f"Bathrooms — Partial ({partial_n} × ${r['bathroom_partial_remodel']:,})",
                     partial_n * r["bathroom_partial_remodel"])
                )
        else:
            items.append(
                (f"Bathrooms ({baths:g} bath{'s' if baths != 1 else ''} × ${r['bathroom_full_remodel']:,})",
                 baths * r["bathroom_full_remodel"])
            )

    half = get("half_bathrooms")
    if half.get("include"):
        qty = half.get("qty", 0) or 0
        items.append((f"Half Baths ({qty} × ${r['bathroom_half']:,})", qty * r["bathroom_half"]))

    ip = get("interior_paint")
    if ip.get("include"):
        t = ip.get("type", "Knockdown + Paint")
        rate = {"Knockdown + Paint": r["interior_paint_full_per_sqft"],
                "Paint only": r["interior_paint_paint_only_per_sqft"],
                "Knockdown only": r["interior_paint_texture_per_sqft"]}.get(t, r["interior_paint_full_per_sqft"])
        items.append((f"Interior Paint ({t}, {sqft:,} sf × ${rate}/sf)", sqft * rate))

    if get("exterior_paint").get("include"):
        items.append((f"Exterior Paint ({sqft:,} sf × ${r['exterior_paint_per_sqft']}/sf)",
                      sqft * r["exterior_paint_per_sqft"]))

    if get("flooring").get("include"):
        items.append((f"Flooring (luxury vinyl, {sqft:,} sf × ${r['flooring_luxury_vinyl_per_sqft']}/sf)",
                      sqft * r["flooring_luxury_vinyl_per_sqft"]))

    doors = get("doors")
    if doors.get("include"):
        qty = doors.get("qty", 0) or 0
        t = doors.get("type", "Interior Replace")
        rate = {"Exterior Replace": r["door_exterior_each"],
                "Interior Replace": r["door_interior_each"],
                "Patch & Paint": r["door_patch_paint_each"]}.get(t, r["door_interior_each"])
        items.append((f"Doors ({qty} × {t} @ ${rate})", qty * rate))

    win = get("windows")
    if win.get("include"):
        qty = win.get("qty", 0) or 0
        t = win.get("type", "Non-Impact")
        rate = {"Non-Impact": r["window_non_impact_each"], "Impact": r["window_impact_each"],
                "New Shutter": r["shutter_new_each"], "Replace Shutter": r["shutter_replace_each"]}.get(t, r["window_non_impact_each"])
        items.append((f"Windows/Shutters ({qty} × {t} @ ${rate})", qty * rate))

    plumb = get("plumbing")
    if plumb.get("include"):
        items.append(("Plumbing (manual)", plumb.get("amount", 0) or 0))

    _land_bd = get("landscaping")
    if _land_bd.get("include"):
        _land_cost_bd = _land_bd.get("cost_override")
        if _land_cost_bd is None or float(_land_cost_bd) <= 0:
            _land_cost_bd = r["landscaping"]
        items.append(("Landscaping", float(_land_cost_bd)))

    if get("appliances").get("include"):
        items.append(("Appliances", r["appliances"]))

    if get("lighting").get("include"):
        items.append(("Lighting (all new)", r["lighting_all_new"]))

    if get("hot_water_tank").get("include"):
        items.append(("Hot water tank", r["hot_water_tank"]))

    if get("cosmetic_demo").get("include"):
        items.append(("Cosmetic demo", r["cosmetic_demo"]))

    if get("final_cleaning").get("include"):
        items.append(("Final cleaning", r["final_cleaning"]))

    pool_r = get("pool")
    if pool_r.get("include") and pool:
        _pool_defaults_bd = {
            "Replace Motor": r["pool_replace_motor"],
            "Replace Pump": r["pool_replace_pump"],
            "Heater": r["pool_heater"],
            "Waterline Tile": r["pool_waterline_tile"],
            "Diamond Brite": r["pool_diamond_brite"],
        }
        _pool_items_bd = pool_r.get("items") or {}
        if _pool_items_bd:
            for _name_bd, _cfg_bd in _pool_items_bd.items():
                if _cfg_bd.get("selected"):
                    _c_bd = _cfg_bd.get("cost")
                    if _c_bd is None or float(_c_bd) <= 0:
                        _c_bd = _pool_defaults_bd.get(_name_bd, 0)
                    items.append((f"Pool ({_name_bd})", float(_c_bd)))
        else:
            # Legacy single-select fallback
            t = pool_r.get("type", "Replace Motor")
            amt = _pool_defaults_bd.get(t, r["pool_replace_motor"])
            items.append((f"Pool ({t})", amt))

    for key in ("other_1", "other_2"):
        o = get(key)
        if o.get("include"):
            items.append((o.get("description") or key.replace("_", " ").title(),
                          o.get("amount", 0) or 0))

    return items


# ============================================================================
# LOAN, COM, INSURANCE — hard money model (LTC capped by ARV)
# ============================================================================
def compute_loan(purchase: float, arv: float, ltc: float, arv_cap: float) -> float:
    """Lender funds min(ltc × purchase, arv_cap × ARV). Rehab is draws (not in loan)."""
    if purchase <= 0:
        return 0
    return min(ltc * purchase, arv_cap * arv if arv > 0 else ltc * purchase)


def compute_com(loan: float, origination_flat: float, origination_pct: float,
                interest_rate: float, months: float) -> float:
    """Cost of Money in DOLLARS (origination + interest)."""
    if loan <= 0:
        return 0
    origination = origination_flat + origination_pct * loan
    interest = loan * interest_rate * (months / 12.0)
    return origination + interest


def compute_insurance_monthly(loan: float, per_100k: float, bracket: float) -> float:
    """Insurance per month, scaled by loan and rounded to nearest $bracket."""
    if loan <= 0:
        return 0
    loan_rounded = round(loan / bracket) * bracket if bracket > 0 else loan
    return per_100k * (loan_rounded / 100_000.0)


# ============================================================================
# HOLDING COSTS — now includes property tax + loan-based insurance
# ============================================================================
def monthly_holding(loan: float = 0, pool: bool = False, hoa: float = 0,
                    annual_taxes: float = 0, maintenance: float = 0,
                    insurance_per_100k: float = 244,
                    insurance_bracket: float = 25_000) -> float:
    """Updated monthly holding. Insurance scales with loan amount."""
    r = get_repair_rates()
    water = r["water_with_pool"] if pool else r["water_no_pool"]
    electric = r["electric_with_pool"] if pool else r["electric_no_pool"]
    insurance = compute_insurance_monthly(loan, insurance_per_100k, insurance_bracket)
    taxes_monthly = (annual_taxes or 0) / 12.0
    return maintenance + water + electric + insurance + hoa + taxes_monthly


# ============================================================================
# MAO / PROFIT CALCULATIONS — LTC-based hard money
# ============================================================================
def cash_mao_ltc(
    arv: float,
    rehab_total: float,
    bc_pct: float,            # disposition-specific sale closing % (of ARV)
    ab_pct: float,            # acquisition-specific purchase closing % (of purchase)
    holding_total: float,
    target_roi: float,
    ltc: float,
    arv_cap: float,
    origination_flat: float,
    origination_pct: float,
    interest_rate: float,
    months: float,
    ab_flat: float = 0.0,     # v24: fixed AB $ (baseline + situational)
    bc_flat: float = 0.0,     # v24: fixed BC $ (baseline + situational)
    ab_pct_of_loan: float = 0.0,  # v24: AB fees that scale with LOAN, not purchase
) -> float:
    """Max purchase price such that target ROI is achieved.

    Loan = min(ltc × P, arv_cap × ARV). Two analytic cases:
      Case 1 (cap doesn't bind): loan = ltc × P, COM grows with P
      Case 2 (cap binds):        loan = arv_cap × ARV, COM is fixed

    v24: `ab_flat` and `bc_flat` add fixed dollars to the AB/BC side, and
    `ab_pct_of_loan` adds a % that scales with the LOAN (not purchase). This
    lets the itemized closing model (v24) flow into the MAO math without
    changing the closed-form algebra:

      Case 1 total cost = P × (1 + ab_pct + ab_pct_of_loan × ltc + k)
                        + ab_flat + bc_flat + rehab + arv×bc_pct
                        + holding + origination_flat
    """
    if arv <= 0:
        return 0
    bc_costs = arv * bc_pct + bc_flat
    target_tpc = arv / (1 + target_roi)
    loan_cost_factor = origination_pct + interest_rate * (months / 12.0)
    # k combines COM and the v24 AB-of-loan factor. Both scale with LOAN,
    # which in Case 1 = ltc × P — so they both roll into the same denominator.
    k = ltc * (loan_cost_factor + ab_pct_of_loan)

    # --- Case 1: loan = ltc × P ---
    numer1 = (target_tpc - rehab_total - bc_costs - holding_total
              - origination_flat - ab_flat)
    denom1 = 1 + ab_pct + k
    p_case1 = numer1 / denom1 if denom1 > 0 else 0

    # Case 1 is consistent if loan stays under cap:
    #   ltc × p_case1 ≤ arv_cap × ARV   ↔   p_case1 ≤ (arv_cap / ltc) × ARV
    cap_threshold = (arv_cap / ltc) * arv if ltc > 0 else float("inf")
    if p_case1 <= cap_threshold:
        return max(0, p_case1)

    # --- Case 2: loan = arv_cap × ARV (fixed) ---
    capped_loan = arv_cap * arv
    com_fixed = compute_com(capped_loan, origination_flat, origination_pct,
                            interest_rate, months)
    # v24: AB-of-loan is also fixed in Case 2 (loan is capped)
    ab_loan_fixed = capped_loan * ab_pct_of_loan
    numer2 = (target_tpc - rehab_total - bc_costs - holding_total
              - com_fixed - ab_flat - ab_loan_fixed)
    denom2 = 1 + ab_pct
    p_case2 = numer2 / denom2 if denom2 > 0 else 0
    return max(0, p_case2)


def round_down_to_1k(x: float) -> float:
    return math.floor(x / 1000) * 1000


def net_profit_at_price(
    purchase_price: float,
    arv: float,
    rehab_total: float,
    bc_pct: float,
    holding_total: float,
    ab_pct: float,
    loan: float,
    origination_flat: float,
    origination_pct: float,
    interest_rate: float,
    months: float,
    ab_flat: float = 0.0,     # v24: fixed AB $ (baseline + situational)
    bc_flat: float = 0.0,     # v24: fixed BC $ (baseline + situational)
    ab_pct_of_loan: float = 0.0,  # v24: AB fees that scale with LOAN
) -> tuple:
    """Returns (net_profit, tpc, roi) using LTC-based COM in dollars.

    v24: `ab_flat`, `bc_flat`, `ab_pct_of_loan` support the itemized closing
    cost model. Defaults are 0 so existing callers still work identically.
    """
    bc_costs = arv * bc_pct + bc_flat
    ab_costs = purchase_price * ab_pct + ab_flat + loan * ab_pct_of_loan
    com = compute_com(loan, origination_flat, origination_pct, interest_rate, months)
    tpc = (purchase_price + ab_costs + rehab_total + bc_costs
           + holding_total + com)
    profit = arv - tpc
    roi = profit / tpc if tpc > 0 else 0
    return profit, tpc, roi


# ============================================================================
# DERIVED FLAGS
# ============================================================================
def scope_severity(rehab_total: float, params: Dict) -> str:
    if rehab_total > params["scope_heavy_min"]:
        return "Heavy"
    if rehab_total <= params["scope_light_max"]:
        return "Light"
    return "Moderate"


def profit_band(net_profit: float, params: Dict) -> str:
    if net_profit < params["wholesale_only_floor"]:
        return "NO-GO"
    if net_profit < params["rehab_zone_floor"]:
        return "Wholesale only"
    return "Rehab zone"


def deal_status(net_profit: float, roi: float, params: Dict) -> tuple:
    """Returns (status, reason)."""
    target_roi = params["target_roi"]
    min_profit = params["min_profit_threshold"]
    if roi >= target_roi and net_profit >= min_profit:
        return "GO", "Meets both ROI & profit thresholds."
    if roi < target_roi * 0.8 or net_profit < min_profit * 0.7:
        reasons = []
        if net_profit < min_profit:
            reasons.append(f"Profit below ${min_profit:,.0f} minimum.")
        if roi < target_roi:
            reasons.append(f"ROI below {target_roi:.1%} target.")
        return "NO-GO", " ".join(reasons)
    return "CAUTION", "Borderline — review assumptions."


def equity_position(arv: float, mortgages: float, liens: float) -> float:
    return arv - mortgages - liens


def distress_flag(equity: float, payment_status: str) -> bool:
    """True only when the seller is BOTH underwater AND in active distress —
    i.e. a true short-sale candidate where the bank will need to take a haircut.

    A high-equity foreclosure is NOT a short sale candidate: the bank gets paid
    off in full at closing and the seller walks with their equity, so the deal
    routes through the normal Rehab / Wholesale / Novation paths instead. The
    distress status still drives urgency framing in the call but does not
    override strategy selection.

    The $25k cushion below "underwater" accounts for closing costs eating into
    the payoff room — even at +$15k equity, by the time you cover commissions
    and seller costs there may not be enough to pay the bank in full.
    """
    distress_statuses = {"60+", "90+", "NOD", "Foreclosure"}
    is_underwater_or_marginal = equity <= 25_000
    has_distress_status = payment_status in distress_statuses
    return is_underwater_or_marginal and has_distress_status


def gap_category(gap: float, params: Dict) -> str:
    if gap > params["gap_too_wide_threshold"]:
        return "Too Wide (>$70k) — pivot or pass"
    if gap > params["gap_marginal_threshold"]:
        return "Wide ($50–70k) — marginal, expect negotiation"
    return "Tight (≤$50k) — workable"


# ============================================================================
# NOVATION
# ============================================================================
def novation_profit(
    arv: float,
    benchmark: float,
    rehab_total: float,
    retail_costs_pct: float,
    holding_costs: float,
) -> float:
    if arv <= 0 or benchmark <= 0:
        return 0
    return arv * (1 - retail_costs_pct) - benchmark - rehab_total - holding_costs


def novation_max_asking(arv: float, rehab_total: float, params: Dict) -> float:
    """Max asking that still clears the novation min floor."""
    return (arv * (1 - params["novation_retail_costs_pct"])
            - rehab_total
            - params["novation_holding_costs"]
            - params["novation_min_floor"])


def novation_feasible(
    rehab_total: float,
    scope: str,
    nov_profit: float,
    params: Dict,
) -> bool:
    return (rehab_total <= params["novation_rehab_cap"]
            and scope != "Heavy"
            and nov_profit >= params["novation_min_floor"])


# ============================================================================
# MLS REFERRAL
# ============================================================================
def mls_commission(asking: float, arv: float, rate: float = 0.03) -> float:
    if asking > 0:
        return ((asking + arv) / 2) * rate
    return arv * rate


def mls_feasible(
    rehab_total: float,
    arv: float,
    scope: str,
    seller_open: bool,
    commission: float,
    params: Dict,
) -> bool:
    rehab_cap = arv * params["mls_rehab_pct_of_arv"]
    return (rehab_total <= rehab_cap
            and scope != "Heavy"
            and seller_open
            and commission >= params["mls_min_commission"])


# ============================================================================
# FAT FEE
# ============================================================================
def target_fat_fee(net_profit: float, params: Dict) -> float:
    """Recommended assignment fee for heavy-scope wholesale deals."""
    target_pct = params["fat_fee_target_pct"]
    floor = params["default_assignment_fee"]
    ceiling = max(floor, net_profit - params["fat_fee_buyer_floor"])
    return max(floor, min(net_profit * target_pct, ceiling))


# ============================================================================
# MASTER STRATEGY DECISION
# ============================================================================
def decide_strategy(
    profit_band_value: str,
    distress: bool,
    asking: float,
    gap: float,
    nov_ok: bool,
    nov_profit: float,
    mls_ok: bool,
    benchmark: float,
    wholesale_mao: float,
    eff_scope: str,
    assignment_fee: float,
    assignable: bool,
    buyer_prefers_dc: bool,
    params: Dict,
) -> str:
    """Mirror of v3's master strategy formula. Returns the strategy string."""
    pref_target = params["novation_preferred_target"]
    nov_label = "Novation" if nov_profit >= pref_target else "Novation — Marginal"

    dc_triggered = (assignment_fee >= params["dc_assignment_fee_threshold"]
                    or not assignable
                    or buyer_prefers_dc)

    # 1. NO-GO floor: check novation, then MLS, then Pass
    if profit_band_value == "NO-GO":
        if nov_ok:
            return nov_label
        if mls_ok:
            return "MLS Referral"
        return "NO-GO — Pass"

    # 2. Distress overlay
    if distress:
        return "Short Sale → Wholesale (Double Close)"

    # 3. Gap > $70k → pivot
    if asking > 0 and gap > params["gap_too_wide_threshold"]:
        if nov_ok:
            return nov_label
        if mls_ok:
            return "MLS Referral"
        return "Pass — Gap to MAO Too Wide"

    # 4. Gap $50-70k → forced wholesale
    if asking > 0 and gap > params["gap_marginal_threshold"]:
        if dc_triggered:
            return "Wholesale — Double Close (wide gap forces wholesale)"
        return "Wholesale — Assignment (wide gap forces wholesale)"

    # 5. Novation when benchmark > Wholesale MAO
    if nov_ok and benchmark > wholesale_mao:
        return nov_label

    # 6. Profit band logic
    if profit_band_value == "Wholesale only":
        if dc_triggered:
            return "Wholesale — Double Close"
        return "Wholesale — Assignment"

    # Rehab zone
    if eff_scope == "Heavy":
        # Was forced DC, but Assignment has no closing costs and is structurally
        # more profitable for high-priced deals. Only fall to DC when forced.
        if assignable and not buyer_prefers_dc:
            return "Wholesale — Assignment (heavy scope, fat fee)"
        return "Wholesale — Double Close (heavy scope, fat fee)"
    return "Rehab"


# ============================================================================
# OFFER TERMS
# ============================================================================
def offer_terms(
    strategy: str,
    cash_mao_value: float,
    wholesale_mao_value: float,
    benchmark: float,
    buyer_demand_confirmed: bool,
    asking: float = 0,
) -> Dict[str, float]:
    """Returns dict with walk_away, opening, stretch.

    Critical rule: we NEVER offer the seller more than they're asking.
    If their asking is below our math ceiling (cash MAO / wholesale MAO /
    benchmark), the walk-away is clamped to asking. The 'extra' margin
    between asking and our true MAO becomes additional profit, not a
    higher offer to the seller.
    """
    is_novation = "Novation" in strategy
    is_pass = strategy in ("NO-GO — Pass", "Pass — Gap to MAO Too Wide")
    is_mls = strategy == "MLS Referral"

    if is_pass or is_mls:
        return {"walk_away": 0, "opening": 0, "stretch": 0}

    if "Rehab" in strategy:  # plain "Rehab" or "Short Sale → Rehab"
        walk = cash_mao_value
    elif is_novation:
        walk = benchmark
    else:
        walk = wholesale_mao_value

    # CLAMP: never offer above the seller's asking. If they said $245k and
    # our math ceiling is $302k, we walk at $245k — the $57k gap is now
    # extra margin for us, not a higher offer to them.
    if asking and asking > 0 and asking < walk:
        walk = asking

    opening = round_down_to_1k(walk * 0.96) if walk > 0 else 0
    stretch_bonus = 5_000 if buyer_demand_confirmed else 2_000
    stretch = walk + stretch_bonus if walk > 0 else 0

    # And the stretch ceiling must also never exceed asking.
    if asking and asking > 0 and stretch > asking:
        stretch = asking

    return {"walk_away": walk, "opening": opening, "stretch": stretch}


# ============================================================================
# RATIONALE / DISPOSITION / ACTION ITEMS
# ============================================================================
def fmt_money(x: float) -> str:
    if x is None:
        return "$0"
    return f"${x:,.0f}"


def fmt_pct(x: float) -> str:
    if x is None:
        return "0%"
    return f"{x:.1%}"


def key_numbers_for(rec: Dict[str, Any], prop: Dict[str, Any]) -> List[tuple]:
    """Return [(label, value_str), ...] of strategy-appropriate Key Numbers
    for display in the UI and memos.

    Different strategy families surface different metrics:
      - Investor strategies (Wholesale / DC / Rehab / Novation / Short Sale)
        show MAO-based numbers + Deal Status (the original 8 metrics).
      - MLS Referral hides MAO offers and shows commission + equity instead.
      - Pass strategies show gap analysis only — no investor metrics.

    Always returns a list of 2-tuples (label, formatted_value) so callers can
    render however they like (st.metric, kv table, etc.).
    """
    strategy = rec.get("strategy", "")
    asking = prop.get("asking", 0) or 0

    # MLS Referral — we're listing, not buying
    if "MLS" in strategy:
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Asking", fmt_money(asking)),
            ("Total Rehab", fmt_money(rec.get("rehab_total", 0))),
            ("Est. MLS Commission", fmt_money(rec.get("mls_commission_estimate", 0))),
            ("Equity Position", fmt_money(rec.get("equity", 0))),
        ]

    # Pass strategies — walk-away, gap is what matters
    if "Pass" in strategy or strategy.startswith("NO-GO"):
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Asking", fmt_money(asking)),
            ("Total Rehab", fmt_money(rec.get("rehab_total", 0))),
            ("Cash MAO (Our Max)", fmt_money(rec.get("cash_offer", 0))),
            ("Gap (Asking − MAO)", fmt_money(rec.get("gap", 0))),
            ("Gap Category", rec.get("gap_category", "—")),
        ]

    # Strategy-specific Key Numbers based on proforma_kind
    kind = rec.get("proforma_kind", "rehab")

    if kind == "dc":
        # Double Close — buy from seller, resell to end buyer same day
        spread = rec.get("cash_offer", 0) - rec.get("likely_purchase_price", 0)
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Our Buy Price", fmt_money(rec.get("likely_purchase_price", 0))),
            ("End Buyer Price", fmt_money(rec.get("cash_offer", 0))),
            ("Gross Spread", fmt_money(spread)),
            ("Rehab (end buyer's)", fmt_money(rec.get("rehab_total", 0))),
            ("Net Profit (DC)", fmt_money(rec.get("net_profit", 0))),
            ("ROI", fmt_pct(rec.get("roi", 0))),
            ("Deal Status", rec.get("deal_status", "—")),
        ]

    if kind == "assignment":
        # Format the Target Assignment Fee as a range display (e.g. "$25,000
        # (range $5K–$25K)") so reps see the practical cap and floor.
        fee_target = rec.get("target_assignment_fee") or rec.get("net_profit", 0)
        try:
            from modules.settings import get_setting as _g
            saved = _g("strategy_thresholds") or {}
        except Exception:
            saved = {}
        fee_floor = saved.get("assignment_fee_min", 5_000)
        fee_ceiling = saved.get("assignment_fee_max", 25_000)
        fee_display = (
            f"{fmt_money(fee_target)} "
            f"(range {fmt_money(fee_floor)}–{fmt_money(fee_ceiling)})"
        )
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Total Rehab (end buyer's)", fmt_money(rec.get("rehab_total", 0))),
            # Use the clamped-by-asking value for what we'd actually offer
            ("Our Wholesale Offer",
             fmt_money(rec.get("wholesale_offer_to_seller",
                                rec.get("wholesale_offer", 0)))),
            ("End Buyer MAO (Cash)", fmt_money(rec.get("cash_offer", 0))),
            ("Target Assignment Fee", fee_display),
            ("Net Profit", fmt_money(rec.get("net_profit", 0))),
            ("Deal Status", rec.get("deal_status", "—")),
        ]

    if kind == "novation":
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Seller's Benchmark", fmt_money(rec.get("benchmark", 0))),
            ("Max Asking for Novation", fmt_money(rec.get("novation_max_asking", 0))),
            ("Total Rehab", fmt_money(rec.get("rehab_total", 0))),
            ("Novation Profit", fmt_money(rec.get("net_profit", 0))),
            ("Deal Status", rec.get("deal_status", "—")),
        ]

    # Default: Rehab strategy (incl. Short Sale → Rehab, Novation handled above)
    # When asking < MAO, show profit at asking AND profit at MAO. The displayed
    # Cash Offer / Wholesale Offer use the *_to_seller fields which are
    # clamped to never exceed asking.
    at_asking_profit = rec.get("net_profit_at_asking")
    if at_asking_profit is not None:
        return [
            ("ARV", fmt_money(rec.get("arv", 0))),
            ("Total Rehab", fmt_money(rec.get("rehab_total", 0))),
            ("Net Profit at Asking", fmt_money(at_asking_profit)),
            ("Net Profit at MAO", fmt_money(rec.get("net_profit_at_mao", 0))),
            ("ROI at Asking", fmt_pct(rec.get("roi_at_asking", 0))),
            ("Cash Offer",
             fmt_money(rec.get("cash_offer_to_seller",
                                rec.get("cash_offer", 0)))),
            ("Wholesale Offer",
             fmt_money(rec.get("wholesale_offer_to_seller",
                                rec.get("wholesale_offer", 0)))),
            ("Deal Status", rec.get("deal_status", "—")),
        ]

    # asking ≥ MAO — show standard 8 metrics (no clamping needed since MAO is the cap)
    return [
        ("ARV", fmt_money(rec.get("arv", 0))),
        ("Total Rehab", fmt_money(rec.get("rehab_total", 0))),
        ("Net Profit", fmt_money(rec.get("net_profit", 0))),
        ("ROI", fmt_pct(rec.get("roi", 0))),
        ("Cash Offer",
         fmt_money(rec.get("cash_offer_to_seller",
                            rec.get("cash_offer", 0)))),
        ("Wholesale Offer",
         fmt_money(rec.get("wholesale_offer_to_seller",
                            rec.get("wholesale_offer", 0)))),
        ("Total Project Cost", fmt_money(rec.get("total_project_cost", 0))),
        ("Deal Status", rec.get("deal_status", "—")),
    ]


def rationale_text(strategy: str, ctx: Dict) -> str:
    """Generate the strategy rationale paragraph."""
    p = ctx
    if strategy == "NO-GO — Pass":
        return (f"Net profit {fmt_money(p['net_profit'])} below ${p['params']['min_profit_threshold']:,.0f} "
                f"floor and MLS not viable.")
    if strategy == "Pass — Gap to MAO Too Wide":
        reason = (f"Rehab {fmt_money(p['rehab_total'])} too heavy for novation"
                  if p['rehab_total'] > p['params']['novation_rehab_cap']
                  else f"Novation fails — ARV minus costs below asking "
                       f"(max novatable {fmt_money(p['nov_max_asking'])})")
        return (f"Seller's asking {fmt_money(p['asking'])} is {fmt_money(p['gap'])} above our Cash MAO "
                f"{fmt_money(p['cash_offer'])}. {reason} and MLS not viable. Walk unless seller drops.")
    if strategy == "MLS Referral":
        return (f"Investor strategies don't work here, but rehab is light ({fmt_money(p['rehab_total'])}) "
                f"and seller's asking {fmt_money(p['asking'])} is within retail reach. List on MLS via "
                f"in-house realtor; estimated commission {fmt_money(p['mls_commission'])}.")
    if "wide gap forces" in strategy:
        return (f"Gap of {fmt_money(p['gap'])} between asking and Cash MAO is too wide for rehab — "
                f"even though math at MAO works, seller likely won't accept. Pivoting to wholesale: "
                f"keep your skin small, let end buyer take the risk.")
    if strategy == "Short Sale → Rehab":
        ask_profit = p.get("net_profit_at_asking")
        if ask_profit is not None and p.get("asking"):
            return (f"Short sale acquisition: asking {fmt_money(p['asking'])} below "
                    f"Cash MAO {fmt_money(p['cash_offer'])}, projected profit "
                    f"{fmt_money(ask_profit)} after rehab. Lower AB closing (2%) "
                    f"helps margin.")
        return (f"Short sale acquisition with light/moderate rehab. "
                f"Net profit {fmt_money(p['net_profit'])} at MAO.")
    if "Short Sale" in strategy:
        return (f"Distress signals present (equity {fmt_money(p['equity'])}, status {p['payment_status']}). "
                f"Negotiate the bank down, exit via DC.")
    if "Novation" in strategy:
        gap_note = (f"gap of {fmt_money(p['gap'])} rules out wholesale/rehab"
                    if p['gap'] > p['params']['gap_too_wide_threshold']
                    else "above Wholesale MAO")
        marginal_note = (f" ⚠ Below ${p['params']['novation_preferred_target']:,.0f} preferred target "
                         f"— confirm before committing 90 days." if "Marginal" in strategy else "")
        return (f"Seller wants {fmt_money(p['benchmark'])} — {gap_note}. Light rehab "
                f"{fmt_money(p['rehab_total'])} supports retail listing; projected novation profit "
                f"{fmt_money(p['nov_profit'])}.{marginal_note}")
    if strategy == "Rehab":
        # If asking < MAO, surface the at-asking economics
        ask_profit = p.get("net_profit_at_asking")
        if ask_profit is not None and p.get("asking"):
            return (f"Asking {fmt_money(p['asking'])} is "
                    f"{fmt_money(p['cash_offer'] - p['asking'])} below Cash MAO "
                    f"{fmt_money(p['cash_offer'])} — excellent margin. At asking, "
                    f"projected profit {fmt_money(ask_profit)}; scope {p['eff_scope']}.")
        return (f"Net profit {fmt_money(p['net_profit'])} clears "
                f"${p['params']['rehab_zone_floor']:,.0f} rehab threshold; scope {p['eff_scope']}; "
                f"gap {fmt_money(p['gap'])} within range.")
    if strategy == "Wholesale — Assignment":
        return (f"Profit {fmt_money(p['net_profit'])} in $30-50k band; assignment fits "
                f"(fee {fmt_money(p['assignment_fee'])} below DC trigger).")
    if "heavy scope" in strategy:
        exit_kind = ("Assignment" if "Assignment" in strategy else "DC")
        rehab_profit = p.get("net_profit_at_mao") or p['net_profit']
        return (f"Heavy rehab {fmt_money(p['rehab_total'])} — too risky to take down. "
                f"End buyer's projected profit after rehab is {fmt_money(rehab_profit)}; "
                f"wholesale via {exit_kind} with fat fee target "
                f"{fmt_money(p['target_fat_fee'])} (25% of end buyer's profit, "
                f"floor $15k, end buyer keeps ≥$50k).")
    return f"Profit fits wholesale band; DC required — fee/contract/buyer triggers active."


def disposition_text(strategy: str) -> str:
    if strategy == "NO-GO — Pass":
        return "Pass — math fails the $30k floor."
    if strategy == "Pass — Gap to MAO Too Wide":
        return "Pass — educate seller on market value; circle back if asking drops."
    if strategy == "MLS Referral":
        return "Refer to in-house realtor; list on MLS at retail; collect listing commission on close."
    if strategy == "Short Sale → Rehab":
        return ("Negotiate short sale with lender; close AB on approved price; "
                "rehab to ARV; list retail.")
    if strategy == "Rehab":
        return "Take down, rehab to ARV, list retail."
    if strategy == "Novation — Marginal":
        return ("List property at ARV; capture proceeds above seller's net. "
                "CAUTION: profit below $30k preferred target.")
    if strategy == "Novation":
        return "List property at ARV; capture proceeds above seller's net."
    if "Short Sale" in strategy:
        return "Negotiate short sale with lender; wholesale double-close on accepted price."
    if "Assignment" in strategy:
        return "Assign contract to cash buyer for projected fee."
    return "Double-close A→B→C; spread captured between contracts."


ACTION_ITEMS = {
    "NO-GO — Pass": [
        "Document why this was passed for future reference.",
        "Note seller contact for callback if circumstances change.",
    ],
    "Pass — Gap to MAO Too Wide": [
        "Send seller a CMA showing realistic market value.",
        "Educate on rehab costs that justify our MAO.",
        "Ask seller what their bottom-line number is.",
        "Set 30-day reminder to follow up if asking drops.",
        "Record this gap for market trend tracking.",
        "Add seller to long-term nurture list.",
    ],
    "MLS Referral": [
        "Confirm seller open to signing listing agreement (60-90 day term).",
        "Schedule in-house realtor for listing presentation within 7 days.",
        "Order CMA to set realistic list price; align seller expectations.",
        "Identify $1-3k cosmetic improvements that boost list price (paint, staging, photos).",
        "Confirm commission split with in-house realtor if applicable.",
        "Set 60-day check-in on listing progress; adjust price if no offers.",
    ],
    "Short Sale → Wholesale (Double Close)": [
        "Pull current payoff statement from 1st lender.",
        "Confirm seller has hardship documentation ready.",
        "Order BPO comp package for lender negotiation.",
        "Line up transactional funding for double close.",
        "Identify 2-3 likely cash buyers before listing.",
        "Set 90-day reminder if short sale stalls.",
    ],
    "Short Sale → Rehab": [
        "Pull current payoff statement from 1st lender.",
        "Confirm seller has hardship documentation ready.",
        "Order BPO comp package for lender negotiation.",
        "Confirm contractor availability and lock pricing.",
        "Line up rehab financing (hard money + draws).",
        "Order inspection during inspection period.",
        "Plan 4-month rehab timeline contingent on SS approval.",
    ],
    "Novation": [
        "Record Memorandum of Agreement at Miami-Dade clerk.",
        "Finalize 90-day novation agreement with reimbursement clause.",
        "Confirm seller's required net in writing.",
        "Do NOT spend on improvements until retail buyer under contract.",
        "List property at ARV; market to retail buyers.",
        "Set up right-of-first-refusal post-expiration.",
    ],
    "Rehab": [
        "Validate ARV with 2nd round of comps (within 0.5 mi, 6 months).",
        "Confirm contractor availability and lock pricing.",
        "Line up rehab financing or confirm cash position.",
        "Order property inspection during inspection period.",
        "Confirm insurance binder for closing.",
        "Plan 4-month renovation timeline; lock title company.",
    ],
    "_assignment_default": [
        "Verify contract has 'and/or assigns' language.",
        "Push EMD to 14-day refundable period during inspection.",
        "Reach out to 3-5 cash buyers within 24 hrs of contract.",
        "Set assignment fee floor at $2k; flex down to clear.",
        "Confirm end buyer EMD covers your EMD.",
        "Schedule walkthrough with end buyer in inspection window.",
    ],
    "_dc_default": [
        "Line up transactional funding before going firm.",
        "Use separate title companies for AB and BC if needed.",
        "Disclose double-close structure to your title agent.",
        "Do not let end buyer see your contract price.",
        "Confirm BC closing scheduled same day as AB.",
        "Have backup buyer ready in case BC falls through.",
    ],
}


def action_items_for(strategy: str) -> List[str]:
    if strategy == "Novation — Marginal":
        return ACTION_ITEMS["Novation"]
    if strategy in ACTION_ITEMS:
        return ACTION_ITEMS[strategy]
    if "Assignment" in strategy:
        return ACTION_ITEMS["_assignment_default"]
    if "Double Close" in strategy:
        return ACTION_ITEMS["_dc_default"]
    return []


# ============================================================================
# CONTRACT TERMS
# ============================================================================
def contract_terms(strategy: str) -> Dict[str, Any]:
    is_rehab = strategy == "Rehab"
    has_assignment = "Assignment" in strategy
    return {
        "offer_type": "Cash" if is_rehab else "Cash (assignable)",
        "earnest_money": 10_000 if is_rehab else 5_000,
        "inspection_period": "21 days" if is_rehab else "14 days",
        "close_date": ("21 days from inspection clear" if is_rehab
                       else "14 days from inspection clear"),
        "assignment_language": ("Required: 'and/or assigns' in buyer name"
                                if has_assignment else "Not required"),
    }


# ============================================================================
# THE TOP-LEVEL COMPUTE FUNCTION
# ============================================================================
# ============================================================================
# v24 ITEMIZED CLOSING COST MODEL
# Based on analysis of 7 real AB HUDs + 6 real BC HUDs (2024–2025).
# The old flat ab_pct / bc_pct model missed 3 real drivers of variance:
#   1. Loan-based fees (points, doc stamps, intangible, prepaid interest)
#      scale with LOAN, not purchase price.
#   2. County convention flips whether we pay owner's title at BC.
#   3. Situational items (short sale $4K fee, seller concession to FHA
#      buyer, HOA capital contribution, unpaid liens) show up on real
#      closings and had no home in the old model.
# ============================================================================

def _seller_pays_owner_title(county: str, params: Dict[str, Any]) -> bool:
    """True if the SELLER customarily pays the owner's title policy in this
    county. At BC that's us (adds a real cost). At AB when buyer_pays_seller_
    closings is active, we absorb it.

    Broward / Miami-Dade / Sarasota / Collier are BUYER-pays counties, so at
    BC in those counties we skip this line (the retail buyer picks it up).
    All other FL counties are seller-pays.
    """
    buyer_counties = params.get(
        "buyer_pays_owner_title_counties",
        ["Broward", "Miami-Dade", "Sarasota", "Collier"],
    )
    county_norm = (county or "").strip()
    if not county_norm:
        # Default assumption when county isn't set: seller pays (worst-case for us).
        return True
    return county_norm not in buyer_counties


def compute_ab_closing(
    purchase_price: float,
    loan_amount: float,
    is_financed: bool,
    is_short_sale: bool = False,
    buyer_pays_seller_closings: bool = False,
    has_hoa: bool = False,
    county: str = "",
    short_sale_lien_estimate: float = 0,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute AB (acquisition) closing costs using the v24 itemized model.

    Args:
        purchase_price: what we pay the seller
        loan_amount: hard-money loan on this deal (0 for cash)
        is_financed: True if using hard money
        is_short_sale: adds $4K negotiation fee + any lien coverage
        buyer_pays_seller_closings: True if we sweetened offer by covering
            seller's title/tax items
        has_hoa: True if property has an HOA (drives estoppel fee)
        county: property county — drives owner's title split
        short_sale_lien_estimate: manual estimate of liens the bank won't cover
        params: strategy defaults dict (falls back to get_strategy_defaults())

    Returns a dict:
        baseline: fixed flat baseline ($4,750 default)
        loan_costs: percentage of loan (0 if cash)
        seller_closings_pickup: total if buyer_pays_seller_closings
        short_sale_fees: negotiation fee + lien estimate
        total: sum of everything above
        line_items: list of (label, amount) tuples for pro-forma display
    """
    p = params or get_strategy_defaults()
    baseline = p.get("ab_baseline_flat", 4750)
    loan_pct = p.get("ab_loan_pct", 0.0270)
    loan_costs = loan_amount * loan_pct if is_financed else 0.0

    # Seller closings pickup — buyer (us) absorbs the seller's typical items.
    seller_closings_pickup = 0.0
    seller_closings_items: List[tuple] = []
    if buyer_pays_seller_closings:
        # Deed doc stamps — 0.7% of purchase, FL statutory
        doc_stamp = purchase_price * p.get("bc_doc_stamp_pct", 0.007)
        seller_closings_items.append(("Deed doc stamps (for seller)", doc_stamp))
        seller_closings_pickup += doc_stamp

        # Owner's title policy — only if we're in a seller-pays county
        if _seller_pays_owner_title(county, p):
            owner_title = purchase_price * p.get("bc_owner_title_pct_seller_pays", 0.004)
            seller_closings_items.append(("Owner's title policy (for seller)", owner_title))
            seller_closings_pickup += owner_title

        # Title search + municipal lien search
        title_admin = p.get("seller_closings_pickup_flat", 700)
        seller_closings_items.append(("Title search + lien search (for seller)", title_admin))
        seller_closings_pickup += title_admin

        # HOA estoppel — only if HOA
        if has_hoa:
            hoa_estoppel = p.get("hoa_estoppel_fee", 500)
            seller_closings_items.append(("HOA estoppel (for seller)", hoa_estoppel))
            seller_closings_pickup += hoa_estoppel

    # Short sale — flat negotiation fee + any lien coverage
    short_sale_fees = 0.0
    short_sale_items: List[tuple] = []
    if is_short_sale:
        neg_fee = p.get("short_sale_negotiation_fee", 4000)
        short_sale_items.append(("Short sale negotiation fee", neg_fee))
        short_sale_fees += neg_fee
        if short_sale_lien_estimate and short_sale_lien_estimate > 0:
            short_sale_items.append(
                ("Est. unpaid liens (bank didn't cover)", short_sale_lien_estimate)
            )
            short_sale_fees += short_sale_lien_estimate

    total = baseline + loan_costs + seller_closings_pickup + short_sale_fees

    # Build the display line-items in a stable order
    line_items = [
        ("Fixed baseline (attorney, tax service, settlement, endorsements, "
         "recording, misc)", baseline),
    ]
    if is_financed and loan_costs > 0:
        line_items.append(
            (f"Loan-related fees ({loan_pct*100:.2f}% of ${loan_amount:,.0f} loan: "
             "points + mtg doc stamps + intangible + prepaid interest)",
             loan_costs)
        )
    line_items.extend(seller_closings_items)
    line_items.extend(short_sale_items)

    return {
        "baseline": baseline,
        "loan_costs": loan_costs,
        "seller_closings_pickup": seller_closings_pickup,
        "short_sale_fees": short_sale_fees,
        "total": total,
        "line_items": line_items,
    }


def compute_bc_closing(
    sale_price: float,
    county: str = "",
    seller_concession: float = 0,
    hoa_capital_contrib: float = 0,
    utility_escrow: float = 0,
    commission_pct: Optional[float] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute BC (disposition/sale) closing costs using the v24 itemized model.

    Args:
        sale_price: gross price we sell for (usually ARV in the underwriting model)
        county: property county — drives owner's title split
        seller_concession: dollar amount we give the retail buyer at closing
            (common with FHA buyers, $10K–$20K range)
        hoa_capital_contrib: dollars owed at BC to HOA capital reserves
        utility_escrow: dollars held back for final utility readings ($250–$1K)
        commission_pct: override for BC_COMMISSION_PCT (default 5.5%)
        params: strategy defaults dict

    Returns a dict:
        baseline: fixed flat baseline ($3,000 default)
        commissions_total: full realtor commission
        commission_listing: half — internally recoverable (household)
        commission_selling: half — external cost
        doc_stamps: 0.7% deed doc stamps
        owner_title: 0.4% of sale (only in seller-pays counties)
        situational: concession + HOA cap + utility escrow
        total: sum of everything
        total_external: total minus listing commission (household-adjusted view)
        line_items: list for pro-forma display
    """
    p = params or get_strategy_defaults()
    baseline = p.get("bc_baseline_flat", 3000)
    comm_pct = commission_pct if commission_pct is not None \
        else p.get("bc_commission_pct", 0.055)
    listing_share = p.get("bc_commission_listing_share", 0.5)

    comm_total = sale_price * comm_pct
    comm_listing = comm_total * listing_share
    comm_selling = comm_total * (1 - listing_share)

    doc_stamps = sale_price * p.get("bc_doc_stamp_pct", 0.007)

    owner_title = 0.0
    if _seller_pays_owner_title(county, p):
        owner_title = sale_price * p.get("bc_owner_title_pct_seller_pays", 0.004)

    situational = (seller_concession or 0) + (hoa_capital_contrib or 0) \
        + (utility_escrow or 0)

    total = baseline + comm_total + doc_stamps + owner_title + situational
    # Household-adjusted view: listing commission comes back to the household
    total_external = total - comm_listing

    # Build the display line-items. `line_items` amounts SUM to `total` — the
    # commission line shows the full total once. The listing/selling split
    # is available as a separate string (`commission_note`) for the memo to
    # render as a footnote below the commission line.
    county_note = county if county else "(county not set)"
    commission_note = (
        f"({listing_share*100:.0f}% listing agent ${comm_listing:,.0f} "
        "— internally recoverable to household*; "
        f"{(1-listing_share)*100:.0f}% selling agent ${comm_selling:,.0f} "
        "— external cost)"
    )
    line_items = [
        ("Fixed baseline (attorney, title search, lien search, settlement, "
         "admin)", baseline),
        (f"Realtor commissions ({comm_pct*100:.1f}% of sale) *",
         comm_total),
        (f"Deed doc stamps (0.70% of sale, FL statutory)", doc_stamps),
    ]
    if owner_title > 0:
        line_items.append(
            (f"Owner's title policy (0.40% — {county_note} is seller-pays)",
             owner_title)
        )
    if seller_concession and seller_concession > 0:
        line_items.append(("Seller concession to buyer", seller_concession))
    if hoa_capital_contrib and hoa_capital_contrib > 0:
        line_items.append(("HOA capital contribution", hoa_capital_contrib))
    if utility_escrow and utility_escrow > 0:
        line_items.append(("Utility escrow holdback", utility_escrow))

    return {
        "baseline": baseline,
        "commissions_total": comm_total,
        "commission_listing": comm_listing,
        "commission_selling": comm_selling,
        "commission_note": commission_note,
        "doc_stamps": doc_stamps,
        "owner_title": owner_title,
        "situational": situational,
        "total": total,
        "total_external": total_external,
        "line_items": line_items,
    }


def closing_pcts_for(acquisition_type: str, strategy: str, params: Dict[str, Any]) -> tuple:
    """Return (ab_pct, bc_pct) for the given combination.

    AB% comes from acquisition type (Regular vs Short Sale).
    BC% comes from disposition (Rehab/Retail 7%, DC 2%, Novation N/A, etc.)."""
    is_short = (acquisition_type or "").lower().startswith("short")
    # AB
    if is_short:
        ab = params.get("short_sale_ab_pct", 0.02)
    elif "Double Close" in strategy:
        ab = params.get("dc_ab_pct", params.get("regular_ab_pct", 0.04))
    elif "Assignment" in strategy:
        ab = 0.0  # we never close
    else:
        ab = params.get("regular_ab_pct", 0.04)
    # BC
    if "Double Close" in strategy:
        bc = params.get("dc_bc_pct", 0.02)
    elif "Assignment" in strategy:
        bc = 0.0
    elif "MLS" in strategy or "Pass" in strategy or strategy.startswith("NO-GO"):
        bc = 0.0  # we don't sell — N/A
    else:  # Rehab, Novation, Short Sale (default exit)
        bc = params.get("rehab_bc_pct", 0.07)
    return ab, bc


def compute_recommendation(inputs: Dict[str, Any],
                           force_strategy: Optional[str] = None) -> Dict[str, Any]:
    """Top-level orchestrator with LTC hard-money math + profit-at-asking logic.

    Required input keys:
      property: {address, city, state, zip, beds, baths, sqft, year, pool, hoa,
                 asking, acquisition_type, annual_taxes}
      arv: float
      rehab: dict of toggles
      seller: {mtg1, mtg2, other_liens, payment_status, required_net, ...}
      params: optional dict to override DEFAULTS

    If `force_strategy` is provided, that string is used instead of the result
    of decide_strategy(). Useful for computing alternative-strategy pro-formas
    in compute_alternatives() — lets the user compare Wholesale vs Novation
    side-by-side on the same deal.
    """
    params = {**get_strategy_defaults(), **(inputs.get("params") or {})}
    prop = inputs["property"]
    arv = inputs.get("arv", 0) or 0
    rehab = inputs.get("rehab", {}) or {}
    seller = inputs.get("seller", {}) or {}

    sqft = prop.get("sqft", 0) or 0
    baths = prop.get("baths", 0) or 0
    pool = (prop.get("pool", "No") == "Yes")
    stories = prop.get("stories", 1) or 1
    asking = prop.get("asking", 0) or 0
    hoa = prop.get("hoa", 0) or 0
    annual_taxes = prop.get("annual_taxes", 0) or 0
    acquisition_type = (prop.get("acquisition_type") or "Regular")

    # v24 closing cost inputs — all optional; defaults preserve v23 behavior
    # for existing deals that don't have these fields yet.
    county = prop.get("county", "") or ""
    has_hoa = bool(hoa and hoa > 0)
    is_short_sale_input = bool(prop.get("is_short_sale")
                                or acquisition_type.lower().startswith("short"))
    buyer_pays_seller_closings = bool(prop.get("buyer_pays_seller_closings"))
    seller_concession = float(prop.get("seller_concession", 0) or 0)
    hoa_capital_contrib = float(prop.get("hoa_capital_contrib", 0) or 0)
    utility_escrow_estimate = float(prop.get("utility_escrow_estimate", 0) or 0)
    short_sale_lien_estimate = float(prop.get("short_sale_lien_estimate", 0) or 0)
    # BC commission override — user can set a per-deal rate (5.0–6.0 typical).
    # Falls back to bc_commission_pct default (5.5%) when not set.
    bc_commission_pct_override = prop.get("bc_commission_pct")
    if bc_commission_pct_override is not None:
        try:
            bc_commission_pct_override = float(bc_commission_pct_override)
        except (TypeError, ValueError):
            bc_commission_pct_override = None

    # Rehab — roof footprint depends on # of stories
    rehab_sub = rehab_subtotal(rehab, sqft, baths, pool, stories=stories)
    rehab_total = rehab_with_contingency(rehab_sub)

    # Loan / financing params
    ltc = params.get("ltc", params.get("ltv", 0.90))
    arv_cap = params.get("arv_loan_cap", 0.75)
    orig_flat = params.get("origination_flat", 999)
    orig_pct = params.get("origination_pct", params.get("points", 0.015))
    rate = params.get("interest_rate", 0.10)
    duration = params.get("loan_duration_months", 6)
    ins_per_100k = params.get("insurance_per_100k_monthly", 244)
    ins_bracket = params.get("insurance_bracket", 25_000)

    # ---- v24 closing cost model for the MAO calculation ---------------
    # The old flat ab_pct / bc_pct model missed real drivers. Now we decompose:
    #   AB: baseline flat + %-of-loan + situational (short sale, seller closings)
    #   BC: baseline flat + commissions + doc stamps + owner's title + situational
    # For MAO we assume a Rehab disposition (BC is the sell-to-retail flow).
    # We compute BC once against ARV (fixed given ARV), and compute AB's
    # SITUATIONAL fixed portion once (doesn't depend on purchase price yet).
    # ab_pct_of_loan is passed separately so it scales with the loan the MAO
    # math is solving for.

    # BC (against ARV — the retail sale price we're targeting)
    bc_dict_for_mao = compute_bc_closing(
        sale_price=arv,
        county=county,
        seller_concession=seller_concession,
        hoa_capital_contrib=hoa_capital_contrib,
        utility_escrow=utility_escrow_estimate,
        commission_pct=bc_commission_pct_override,
        params=params,
    )
    # Decompose BC into a "%-of-ARV" portion and a "flat $" portion so the
    # closed-form MAO algebra still works:
    #   pct_of_arv = (commissions + doc_stamps + owner_title) / arv
    #   flat = baseline + situational  (fixed regardless of arv)
    if arv > 0:
        bc_pct_of_arv = (bc_dict_for_mao["commissions_total"]
                         + bc_dict_for_mao["doc_stamps"]
                         + bc_dict_for_mao["owner_title"]) / arv
    else:
        bc_pct_of_arv = 0
    bc_flat_for_mao = bc_dict_for_mao["baseline"] + bc_dict_for_mao["situational"]

    # AB — split into: pct_of_purchase (0 in v24), pct_of_loan, flat
    # The old model rolled everything into a % of purchase. v24 recognizes
    # that most AB fees scale with LOAN, not purchase (points, doc stamps,
    # intangible, prepaid interest all scale with loan). Only the situational
    # buyer-pays-seller-closings has a % of purchase piece (deed doc stamp).
    ab_pct_of_purchase_for_mao = 0.0
    ab_pct_of_loan_for_mao = params.get("ab_loan_pct", 0.0270)
    # Situational AB fixed items (do not depend on purchase). We compute at
    # a proxy purchase = 0 so buyer_pays_seller_closings items that DO scale
    # with purchase are handled separately: we approximate them as a % of
    # purchase by rolling into ab_pct_of_purchase_for_mao when applicable.
    if buyer_pays_seller_closings:
        # 0.7% deed doc stamps + optional 0.4% owner's title (if seller-pays county)
        ab_pct_of_purchase_for_mao += params.get("bc_doc_stamp_pct", 0.007)
        if _seller_pays_owner_title(county, params):
            ab_pct_of_purchase_for_mao += params.get(
                "bc_owner_title_pct_seller_pays", 0.004)

    # AB flat portion (doesn't scale with purchase or loan)
    ab_flat_for_mao = params.get("ab_baseline_flat", 4750)
    if buyer_pays_seller_closings:
        ab_flat_for_mao += params.get("seller_closings_pickup_flat", 700)
        if has_hoa:
            ab_flat_for_mao += params.get("hoa_estoppel_fee", 500)
    if is_short_sale_input:
        ab_flat_for_mao += params.get("short_sale_negotiation_fee", 4000)
        ab_flat_for_mao += short_sale_lien_estimate

    # Legacy ab_default / bc_default kept for a) the "what-if" pro-forma
    # display on Pass/MLS strategies, and b) any external caller that still
    # reads them.
    ab_default = ab_pct_of_purchase_for_mao
    bc_default = bc_pct_of_arv

    # MAO calc has chicken-and-egg with holding (insurance depends on loan).
    # Iterate 3x to converge.
    cash_mao_value = arv * 0.7  # initial guess
    for _ in range(3):
        loan_guess = compute_loan(cash_mao_value, arv, ltc, arv_cap)
        monthly_hold = monthly_holding(loan_guess, pool, hoa, annual_taxes,
                                        insurance_per_100k=ins_per_100k,
                                        insurance_bracket=ins_bracket)
        total_holding = monthly_hold * duration
        cash_mao_value = cash_mao_ltc(
            arv, rehab_total, bc_pct_of_arv, ab_pct_of_purchase_for_mao,
            total_holding,
            params.get("target_roi", 0.10), ltc, arv_cap,
            orig_flat, orig_pct, rate, duration,
            ab_flat=ab_flat_for_mao, bc_flat=bc_flat_for_mao,
            ab_pct_of_loan=ab_pct_of_loan_for_mao,
        )

    # cash_offer = MATH ceiling (what an end buyer could pay max).
    # wholesale_offer = ceiling minus default $15k assignment fee.
    # These STAY as math ceilings — DC and Assignment downstream use them
    # to model end-buyer behavior. The "what we actually offer the seller"
    # numbers are computed separately below.
    cash_offer = round_down_to_1k(max(0, cash_mao_value))
    wholesale_offer = round_down_to_1k(max(0, cash_mao_value - params.get("default_assignment_fee", 15_000)))

    # What we'd ACTUALLY offer the seller: clamped by asking so we never
    # overbid. If asking < ceiling, the gap becomes additional margin we
    # capture downstream (bigger Assignment fee, more profit at asking
    # for Rehab).
    cash_offer_to_seller = (round_down_to_1k(asking)
                            if (asking and asking > 0 and asking < cash_offer)
                            else cash_offer)
    wholesale_offer_to_seller = (round_down_to_1k(asking)
                                  if (asking and asking > 0
                                      and asking < wholesale_offer)
                                  else wholesale_offer)

    # Final loan + holding at the resolved cash_offer
    final_loan = compute_loan(cash_offer, arv, ltc, arv_cap)
    monthly_hold = monthly_holding(final_loan, pool, hoa, annual_taxes,
                                    insurance_per_100k=ins_per_100k,
                                    insurance_bracket=ins_bracket)
    total_holding = monthly_hold * duration

    # Net profit AT MAO (conservative ceiling) — v24 itemized closings
    net_profit_at_mao, tpc_at_mao, roi_at_mao = net_profit_at_price(
        cash_offer, arv, rehab_total, bc_pct_of_arv, total_holding,
        ab_pct_of_purchase_for_mao, final_loan, orig_flat, orig_pct, rate,
        duration,
        ab_flat=ab_flat_for_mao, bc_flat=bc_flat_for_mao,
        ab_pct_of_loan=ab_pct_of_loan_for_mao,
    )

    # Net profit AT ASKING (realistic when asking < MAO)
    if asking > 0 and asking < cash_offer:
        ask_loan = compute_loan(asking, arv, ltc, arv_cap)
        monthly_hold_ask = monthly_holding(ask_loan, pool, hoa, annual_taxes,
                                            insurance_per_100k=ins_per_100k,
                                            insurance_bracket=ins_bracket)
        total_holding_ask = monthly_hold_ask * duration
        net_profit_at_asking, tpc_at_asking, roi_at_asking = net_profit_at_price(
            asking, arv, rehab_total, bc_pct_of_arv, total_holding_ask,
            ab_pct_of_purchase_for_mao, ask_loan, orig_flat, orig_pct, rate,
            duration,
            ab_flat=ab_flat_for_mao, bc_flat=bc_flat_for_mao,
            ab_pct_of_loan=ab_pct_of_loan_for_mao,
        )
        # Use the realistic numbers for the decision
        net_profit = net_profit_at_asking
        tpc = tpc_at_asking
        roi = roi_at_asking
        likely_purchase = asking
        likely_loan = ask_loan
        likely_monthly_holding = monthly_hold_ask
        likely_total_holding = total_holding_ask
    else:
        net_profit_at_asking = None  # not applicable
        tpc_at_asking = None
        roi_at_asking = None
        net_profit = net_profit_at_mao
        tpc = tpc_at_mao
        roi = roi_at_mao
        likely_purchase = cash_offer
        likely_loan = final_loan
        likely_monthly_holding = monthly_hold
        likely_total_holding = total_holding

    # Deal status uses REALISTIC profit
    status, status_reason = deal_status(net_profit, roi, params)

    # Seller inputs
    mtg1 = seller.get("mtg1", 0) or 0
    mtg2 = seller.get("mtg2", 0) or 0
    liens = seller.get("other_liens", 0) or 0
    required_net = seller.get("required_net", 0) or 0
    payment_status = seller.get("payment_status", "Current")
    buyer_demand_confirmed = (seller.get("buyer_demand", "No") == "Yes")
    assignable = (seller.get("assignable", "Yes") == "Yes")
    buyer_prefers_dc = (seller.get("buyer_prefers_dc", "No") == "Yes")
    open_to_mls = (seller.get("open_to_mls", "Yes") == "Yes")

    equity = equity_position(arv, mtg1 + mtg2, liens)
    distress = distress_flag(equity, payment_status)
    is_short_sale_acq = acquisition_type.lower().startswith("short")

    # Gap analysis
    gap = (asking - cash_offer) if asking > 0 else 0
    gap_cat = gap_category(gap, params)
    nov_max_asking = novation_max_asking(arv, rehab_total, params)

    # Scope, profit band uses REALISTIC profit
    eff_scope = scope_severity(rehab_total, params)
    pb = profit_band(net_profit, params)

    # Novation
    benchmark = required_net if required_net > 0 else asking
    nov_profit = novation_profit(
        arv, benchmark, rehab_total,
        params["novation_retail_costs_pct"], params["novation_holding_costs"],
    )
    nov_ok = novation_feasible(rehab_total, eff_scope, nov_profit, params)

    # MLS
    mls_comm = mls_commission(asking, arv, params["mls_commission_rate"])
    mls_ok = mls_feasible(rehab_total, arv, eff_scope, open_to_mls, mls_comm, params)

    # Master strategy — when acquisition is Short Sale, route to short sale flow
    if is_short_sale_acq:
        # User explicitly chose short sale; pick best disposition
        if eff_scope == "Heavy" or rehab_total > params.get("scope_heavy_min", 80_000):
            auto_strategy = "Short Sale → Wholesale (Double Close)"
        elif pb == "NO-GO":
            auto_strategy = "Short Sale → Wholesale (Double Close)" if pb != "NO-GO" else (
                "MLS Referral" if mls_ok else "NO-GO — Pass"
            )
        else:
            # Light/moderate scope + clears profit floor → close + rehab + retail
            auto_strategy = "Short Sale → Rehab"
    else:
        auto_strategy = decide_strategy(
            pb, distress, asking, gap, nov_ok, nov_profit, mls_ok,
            benchmark, wholesale_offer, eff_scope,
            params["default_assignment_fee"], assignable, buyer_prefers_dc, params,
        )

    # Allow the caller to override the auto-decided strategy. This is how
    # compute_alternatives() reuses this function to produce side-by-side
    # comparison cards (e.g. "what would Novation look like on this deal?").
    strategy = force_strategy or auto_strategy

    # Strategy-specific closing %s for the actual recommendation.
    # v24: closing_pcts_for() still returns legacy percentages (used by
    # DC/Assignment pro-forma logic below), but the itemized dicts drive
    # the Rehab / Pass / MLS branches so the pro-forma matches reality.
    ab_strat, bc_strat = closing_pcts_for(acquisition_type, strategy, params)

    is_pass_strat = ("Pass" in strategy or strategy.startswith("NO-GO"))
    is_mls_strat = ("MLS" in strategy)
    is_dc_strat = ("Double Close" in strategy)
    is_assignment_strat = ("Assignment" in strategy)
    is_novation_strat = ("Novation" in strategy)

    # Initialize pro-forma display fields (may be overridden below)
    pf_ab, pf_bc = ab_strat, bc_strat
    cost_of_money_amount = compute_com(likely_loan, orig_flat, orig_pct, rate, duration)
    proforma_kind = "rehab"  # rehab | dc | assignment | novation | pass

    # v24: build itemized AB and BC dicts for the ACTUAL likely purchase.
    # For strategies where we don't actually close (Assignment / Novation /
    # Pass / MLS), we still compute a "what-if" version for display purposes.
    ab_closing_dict = compute_ab_closing(
        purchase_price=likely_purchase,
        loan_amount=likely_loan,
        is_financed=(likely_loan > 0),
        is_short_sale=is_short_sale_input,
        buyer_pays_seller_closings=buyer_pays_seller_closings,
        has_hoa=has_hoa,
        county=county,
        short_sale_lien_estimate=short_sale_lien_estimate,
        params=params,
    )
    bc_closing_dict = compute_bc_closing(
        sale_price=arv,
        county=county,
        seller_concession=seller_concession,
        hoa_capital_contrib=hoa_capital_contrib,
        utility_escrow=utility_escrow_estimate,
        commission_pct=bc_commission_pct_override,
        params=params,
    )

    if is_pass_strat or is_mls_strat:
        # Pass / MLS: pro-forma displays "what we would have made if we'd
        # done it" using the itemized model.
        purchase_closing_costs = ab_closing_dict["total"]
        sale_closing_costs = bc_closing_dict["total"]
        # Preserve legacy pf_ab/pf_bc for callers that still want a %
        pf_ab = (purchase_closing_costs / likely_purchase
                 if likely_purchase > 0 else 0)
        pf_bc = sale_closing_costs / arv if arv > 0 else 0
        proforma_kind = "pass"
        # net_profit stays as already-calculated (uses rehab defaults)

    elif is_assignment_strat:
        # Wholesale Assignment: we never close. Profit = the assignment fee.
        # Costs ≈ 0 (no AB, no BC, no holding, no COM).
        purchase_closing_costs = 0
        sale_closing_costs = 0
        cost_of_money_amount = 0
        likely_total_holding = 0
        likely_monthly_holding = 0
        # For assignments, zero out the itemized dicts too so downstream
        # displays don't show phantom costs.
        ab_closing_dict = compute_ab_closing(
            purchase_price=0, loan_amount=0, is_financed=False, params=params
        )
        ab_closing_dict["total"] = 0
        ab_closing_dict["line_items"] = []
        bc_closing_dict["total"] = 0
        bc_closing_dict["line_items"] = []
        fee_floor = params.get("assignment_fee_min", 5_000)
        fee_ceiling = params.get("assignment_fee_max", 25_000)
        # When seller's asking is BELOW the end-buyer Cash MAO, the spread
        # we can capture is (cash_mao − asking − buyer cushion). We cap at
        # the assignment ceiling — anything bigger gets killed by the end-
        # buyer's title attorney and forces a Double Close (handled by the
        # auto-router below). Seller-cushion logic keeps the deal attractive
        # for the end buyer.
        if asking and asking > 0 and asking < cash_offer:
            spread_opportunity = cash_offer - asking - 5_000
            net_profit = max(fee_floor, min(spread_opportunity, fee_ceiling))
        else:
            # Default case (asking ≥ MAO): use the team's default targeted fee,
            # but never below the floor or above the ceiling.
            default_fee = params.get("default_assignment_fee", 15_000)
            net_profit = max(fee_floor, min(default_fee, fee_ceiling))
        tpc = 0
        roi = 0  # n/a since no capital deployed
        proforma_kind = "assignment"

    elif is_dc_strat:
        # Double Close: we briefly close on the property and immediately
        # resell to an end buyer at THEIR Cash MAO. We do NOT rehab.
        # Profit = end buyer price − our purchase − DC closing − transactional.
        #
        # v24 note: DC still uses the legacy flat percentages because the
        # itemized model isn't a clean fit — same-day double-close has a
        # different fee structure (no commissions, minimal title work) that
        # the flat dc_ab_pct / dc_bc_pct (~2% each) still captures well.
        # Left this branch mostly untouched.
        end_buyer_price = cash_offer        # end buyer's max (Cash MAO)
        our_buy_price = likely_purchase     # asking, or our wholesale willingness
        purchase_closing_costs = our_buy_price * ab_strat
        sale_closing_costs = end_buyer_price * bc_strat
        # Overwrite itemized dicts with the DC-flavored total for display
        ab_closing_dict["total"] = purchase_closing_costs
        ab_closing_dict["line_items"] = [
            (f"DC AB closing (~{ab_strat*100:.1f}% of purchase — attorney, title, "
             "transactional funding)", purchase_closing_costs)
        ]
        bc_closing_dict["total"] = sale_closing_costs
        bc_closing_dict["line_items"] = [
            (f"DC BC closing (~{bc_strat*100:.1f}% of sale — doc stamps + closing)",
             sale_closing_costs)
        ]
        # 1% transactional funding fee (replaces normal COM for same-day close)
        transactional_fee = our_buy_price * 0.01
        cost_of_money_amount = transactional_fee
        # Same-day close → no holding period
        likely_total_holding = 0
        likely_monthly_holding = 0
        tpc = (our_buy_price + purchase_closing_costs
               + sale_closing_costs + transactional_fee)
        net_profit = end_buyer_price - tpc
        roi = net_profit / tpc if tpc > 0 else 0
        proforma_kind = "dc"

    elif is_novation_strat:
        # Novation: we never take title. We list and capture proceeds above
        # seller's net. Profit = ARV × (1 - retail_costs%) − benchmark − rehab − holding.
        # (Already computed earlier as nov_profit.)
        purchase_closing_costs = 0
        sale_closing_costs = arv * params.get("novation_retail_costs_pct", 0.09)
        # Zero out AB / repurpose BC
        ab_closing_dict["total"] = 0
        ab_closing_dict["line_items"] = []
        bc_closing_dict["total"] = sale_closing_costs
        bc_closing_dict["line_items"] = [
            (f"Novation retail costs ({params.get('novation_retail_costs_pct', 0.09)*100:.1f}% "
             "of ARV)", sale_closing_costs)
        ]
        cost_of_money_amount = 0
        likely_total_holding = params.get("novation_holding_costs", 3_000)
        likely_monthly_holding = likely_total_holding / max(duration, 1)
        net_profit = nov_profit
        tpc = benchmark + rehab_total + likely_total_holding
        roi = net_profit / tpc if tpc > 0 else 0
        proforma_kind = "novation"

    else:
        # Standard Rehab path (incl. Short Sale → Rehab) — v24 itemized model
        purchase_closing_costs = ab_closing_dict["total"]
        sale_closing_costs = bc_closing_dict["total"]
        # Recompute net_profit using the itemized model.
        net_profit, tpc, roi = net_profit_at_price(
            likely_purchase, arv, rehab_total,
            bc_pct_of_arv, likely_total_holding,
            ab_pct_of_purchase_for_mao, likely_loan,
            orig_flat, orig_pct, rate, duration,
            ab_flat=ab_flat_for_mao, bc_flat=bc_flat_for_mao,
            ab_pct_of_loan=ab_pct_of_loan_for_mao,
        )
        # Preserve legacy pf_ab/pf_bc so external callers still get a %
        pf_ab = (purchase_closing_costs / likely_purchase
                 if likely_purchase > 0 else 0)
        pf_bc = sale_closing_costs / arv if arv > 0 else 0
        proforma_kind = "rehab"

    # Offer terms — pass asking so walk_away, opening, stretch are all
    # clamped to never exceed what the seller is asking for.
    terms = offer_terms(strategy, cash_offer, wholesale_offer, benchmark,
                        buyer_demand_confirmed, asking=asking)

    # Target assignment fee
    if "heavy scope" in strategy:
        # Fat fee = 25% of what the end buyer's REHAB profit would be — NOT the
        # DC profit (which is artificially small due to DC closing costs).
        # net_profit_at_mao reflects Rehab-math profit at end buyer's MAO.
        fat_fee_basis = net_profit_at_mao if net_profit_at_mao and net_profit_at_mao > 0 else net_profit
        recommended_fee = target_fat_fee(fat_fee_basis, params)
        fee_note = (f"Fat fee = 25% of end buyer's projected rehab profit "
                    f"{fmt_money(fat_fee_basis)}; floor $15k, "
                    f"ceiling {fmt_money(max(15000, fat_fee_basis - 50000))} "
                    f"(end buyer keeps ≥$50k)")
        # For Assignment (heavy scope, fat fee), the assignment fee IS our profit
        if "Assignment" in strategy:
            net_profit = recommended_fee
            tpc = 0
            roi = 0
    elif "Wholesale" in strategy or "Short Sale" in strategy:
        # Assignment fee range: $5k floor, $25k ceiling. Below floor isn't
        # worth the effort; above ceiling triggers end-buyer title pushback
        # ("why is the wholesaler taking $X off the table?") and forces a DC.
        fee_floor = params.get("assignment_fee_min", 5_000)
        fee_ceiling = params.get("assignment_fee_max", 25_000)
        if (proforma_kind == "assignment" and asking and asking > 0
                and asking < cash_offer):
            spread_opportunity = cash_offer - asking - 5_000
            capped_fee = max(fee_floor, min(spread_opportunity, fee_ceiling))
            recommended_fee = capped_fee
            if spread_opportunity > fee_ceiling:
                fee_note = (
                    f"Asking ${asking:,.0f} below end-buyer MAO ${cash_offer:,.0f}. "
                    f"Natural spread ${spread_opportunity:,.0f} exceeds the "
                    f"${fee_ceiling:,.0f} assignment ceiling — typically requires "
                    "Double Close to capture cleanly. Fee shown is the assignment "
                    f"cap; range ${fee_floor:,.0f}–${fee_ceiling:,.0f}."
                )
            else:
                fee_note = (
                    f"Asking ${asking:,.0f} below end-buyer MAO ${cash_offer:,.0f}. "
                    f"Spread (with $5k buyer cushion): ${spread_opportunity:,.0f}. "
                    f"Range ${fee_floor:,.0f}–${fee_ceiling:,.0f}."
                )
        elif proforma_kind == "assignment":
            default_fee = params.get("default_assignment_fee", 15_000)
            recommended_fee = max(fee_floor, min(default_fee, fee_ceiling))
            fee_note = (
                f"Targeted ${recommended_fee:,.0f}; "
                f"range ${fee_floor:,.0f}–${fee_ceiling:,.0f}."
            )
        else:
            recommended_fee = params.get("default_assignment_fee", 15_000)
            fee_note = "Default $15k; flex down to $2k floor to clear"
    else:
        recommended_fee = None
        fee_note = ""

    # Recompute Deal Status against the FINAL strategy's net profit.
    # The earlier status was computed against rehab-math placeholders before
    # we routed to the actual strategy (Assignment/DC/Novation/Short Sale).
    # A strategy that nets a loss must never read "GO" — that's the bug we
    # saw on the Lake Worth memo (Short Sale → DC, −$24,500, status GO).
    # For wholesale assignments we use the default-fee floor as the GO bar
    # instead of the buy-and-hold min_profit_threshold (since assignments
    # use zero capital — $15k on $0 capital is an excellent return).
    if proforma_kind == "assignment":
        assignment_go_floor = params.get("default_assignment_fee", 15_000)
        if net_profit >= assignment_go_floor:
            status = "GO"
            status_reason = (f"Assignment fee of ${net_profit:,.0f} clears the "
                             f"${assignment_go_floor:,.0f} floor — zero capital, "
                             "no rehab risk on our side.")
        elif net_profit >= max(2_000, assignment_go_floor * 0.4):
            status = "CAUTION"
            status_reason = (f"Assignment fee ${net_profit:,.0f} below the "
                             f"${assignment_go_floor:,.0f} floor — flex price "
                             "or push for a higher end-buyer.")
        else:
            status = "NO-GO"
            status_reason = (f"Assignment fee ${net_profit:,.0f} too thin — "
                             "pivot to DC or pass.")
    else:
        status, status_reason = deal_status(net_profit, roi, params)
        if net_profit < 0:
            status = "NO-GO"
            status_reason = (f"Strategy nets a loss of ${abs(net_profit):,.0f}. "
                             "Closing/financing costs exceed the spread — pivot or pass.")

    # Rationale context
    rationale_ctx = {
        "params": params, "net_profit": net_profit, "rehab_total": rehab_total,
        "asking": asking, "gap": gap, "cash_offer": cash_offer,
        "nov_max_asking": nov_max_asking, "mls_commission": mls_comm,
        "equity": equity, "payment_status": payment_status,
        "benchmark": benchmark, "nov_profit": nov_profit,
        "eff_scope": eff_scope, "assignment_fee": params["default_assignment_fee"],
        "target_fat_fee": recommended_fee or params["default_assignment_fee"],
        "net_profit_at_mao": net_profit_at_mao,
        "net_profit_at_asking": net_profit_at_asking,
    }

    return {
        # Headline
        "strategy": strategy,
        "auto_strategy": auto_strategy,
        "is_forced": bool(force_strategy) and force_strategy != auto_strategy,
        "rationale": rationale_text(strategy, rationale_ctx),
        "disposition": disposition_text(strategy),
        "action_items": action_items_for(strategy),
        "contract_terms": contract_terms(strategy),

        # Offer
        "opening_offer": terms["opening"],
        "walk_away": terms["walk_away"],
        "stretch_ceiling": terms["stretch"],
        "target_assignment_fee": recommended_fee,
        "fat_fee_note": fee_note,

        # Snapshot — uses REALISTIC numbers (at asking when asking < MAO)
        "arv": arv,
        "asking": asking,
        "rehab_total": rehab_total,
        "rehab_subtotal": rehab_sub,
        "total_holding": likely_total_holding,
        "monthly_holding": likely_monthly_holding,
        "total_project_cost": tpc,
        "net_profit": net_profit,
        "roi": roi,
        "cash_offer": cash_offer,
        "wholesale_offer": wholesale_offer,
        # What we'd actually offer the seller — clamped by asking. These are
        # the numbers shown on memos and to the homeowner; cash_offer above
        # is the math ceiling that drives DC / Assignment downstream.
        "cash_offer_to_seller": cash_offer_to_seller,
        "wholesale_offer_to_seller": wholesale_offer_to_seller,
        "deal_status": status,
        "deal_status_reason": status_reason,
        "mls_commission_estimate": mls_comm,

        # Both views (for Key Numbers display)
        "net_profit_at_mao": net_profit_at_mao,
        "net_profit_at_asking": net_profit_at_asking,
        "tpc_at_mao": tpc_at_mao,
        "tpc_at_asking": tpc_at_asking,
        "roi_at_mao": roi_at_mao,
        "roi_at_asking": roi_at_asking,
        "likely_purchase_price": likely_purchase,
        "likely_loan": likely_loan,

        # Pro-forma line items (strategy-specific %, or 'what-if' for Pass/MLS)
        "purchase_closing_costs": purchase_closing_costs,
        "sale_closing_costs": sale_closing_costs,
        "cost_of_money": cost_of_money_amount,
        "purchase_closing_pct": pf_ab,
        "sale_closing_pct": pf_bc,

        # v24 itemized closing cost model — full breakdown for memos + UI.
        # Each dict has: baseline, total, line_items (list of (label, amount)),
        # plus category subtotals. See compute_ab_closing / compute_bc_closing.
        "ab_closing_itemized": ab_closing_dict,
        "bc_closing_itemized": bc_closing_dict,
        # Household-adjusted net: adds the listing commission back (paid to a
        # family member who is the licensed listing agent). This gives Jo
        # the "true household economics" view alongside the conservative
        # LLC-only net profit. Only meaningful for BC-including strategies.
        "commission_listing_recoverable": bc_closing_dict.get(
            "commission_listing", 0) if proforma_kind in ("rehab", "novation") else 0,
        "net_profit_household_adjusted": (
            net_profit + bc_closing_dict.get("commission_listing", 0)
            if proforma_kind in ("rehab", "novation") else net_profit
        ),
        "annual_taxes": annual_taxes,
        "monthly_taxes": annual_taxes / 12.0 if annual_taxes else 0,
        "monthly_insurance": compute_insurance_monthly(likely_loan, ins_per_100k, ins_bracket),
        "ltc": ltc,
        "loan_duration_months": duration,
        "interest_rate": rate,
        "origination_flat": orig_flat,
        "origination_pct": orig_pct,

        # Acquisition / strategy meta
        "acquisition_type": acquisition_type,
        "proforma_kind": proforma_kind,  # "rehab" | "dc" | "assignment" | "novation" | "pass"

        # Diagnostics
        "equity": equity,
        "distress_flag": distress,
        "scope_severity": eff_scope,
        "profit_band": pb,
        "gap": gap,
        "gap_category": gap_cat,
        "novation_max_asking": nov_max_asking,
        "novation_profit": nov_profit,
        "novation_feasible": nov_ok,
        "mls_feasible": mls_ok,
        "benchmark": benchmark,
    }


def compute_alternatives(inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of recommendation dicts, one per *viable* strategy for
    this deal. The first entry is always the tool's primary recommendation
    (from auto_strategy). Subsequent entries are alternative qualifying paths
    so the user can compare side-by-side and pick.

    Examples of when alternatives surface:
      - A deal qualifies for both Wholesale Assignment and Novation
        (seller's asking ≤ novation max ask, AND wholesale math works)
      - A heavy-scope deal where either Assignment or DC could work
      - A deal that would normally be Rehab but Novation profit is higher

    Each alternative dict has the same shape as compute_recommendation()
    (with `is_forced=True` if it differs from the auto-decided strategy),
    so the UI can render each as a comparison card and offer a "Use this
    strategy" button.
    """
    primary = compute_recommendation(inputs)
    auto = primary["auto_strategy"]
    results = [primary]

    # Build the candidate list — strategies viable for this deal that aren't
    # the auto-recommended one. We're permissive here: even if novation/MLS
    # are flagged not-strictly-feasible, surface them as informational
    # alternatives when the math pencils, so the user can compare and decide.
    candidates = []
    params = {**get_strategy_defaults(), **(inputs.get("params") or {})}

    if "Novation" not in auto:
        nov_profit = primary.get("novation_profit", 0) or 0
        nov_max = primary.get("novation_max_asking", 0) or 0
        asking_for_nov = (inputs.get("property") or {}).get("asking", 0) or 0
        # Show novation alternative when (a) profit is meaningful AND
        # (b) seller's asking fits under the novation ceiling. The "feasible"
        # flag is stricter (caps rehab scope) — but Jo can still see the
        # comparison even on heavy-scope deals.
        nov_floor = params.get("novation_min_floor", 10_000)
        if nov_profit >= nov_floor and (asking_for_nov == 0 or asking_for_nov <= nov_max):
            pref = params.get("novation_preferred_target", 30_000)
            candidates.append("Novation" if nov_profit >= pref else "Novation — Marginal")

    if "MLS" not in auto:
        mls_comm = primary.get("mls_commission_estimate", 0) or 0
        if mls_comm >= params.get("mls_min_commission", 8_000):
            candidates.append("MLS Referral")

    # Surface both wholesale flavors (Assignment + Double Close) whenever the
    # auto is something other than wholesale, so the user can see what a quick
    # exit would look like. If the auto IS wholesale, surface the OTHER flavor.
    asking = (inputs.get("property") or {}).get("asking", 0) or 0
    if asking > 0:
        if "Wholesale — Assignment" not in auto and "Short Sale" not in auto:
            candidates.append("Wholesale — Assignment")
        if "Double Close" not in auto and "Short Sale" not in auto:
            candidates.append("Wholesale — Double Close")

    # Surface Rehab as an alternative whenever the rehab math nets positive
    # profit. Useful comparison even when the auto pick is Novation / MLS /
    # Wholesale — you can see what the long-hold profit would have been.
    if (primary.get("net_profit_at_mao") or 0) > 0 and "Rehab" not in auto:
        candidates.append("Rehab")

    seen = {auto}
    for strat in candidates:
        if strat in seen:
            continue
        seen.add(strat)
        try:
            alt = compute_recommendation(inputs, force_strategy=strat)
            # Don't include if the math doesn't pencil (negative profit for
            # buy-side strategies, etc.) — but keep Novation/MLS even at
            # marginal profit because they're informational comparisons.
            np = alt.get("net_profit", 0) or 0
            if "Novation" in strat or "MLS" in strat or np >= 0:
                results.append(alt)
        except Exception:
            # If forcing a strategy throws (e.g. math edge case), skip it.
            continue

    return results
