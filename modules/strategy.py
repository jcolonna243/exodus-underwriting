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
# DEFAULTS — same values as v3 Excel's Section 4 / 4B / Repair Rates tabs
# ============================================================================
DEFAULTS = {
    # Financing (Underwriting!B41-B49)
    "ltv": 0.90,
    "interest_rate": 0.11,
    "points": 0.015,
    "loan_duration_months": 6,
    "purchase_closing_pct": 0.04,
    "sale_closing_pct": 0.07,
    "target_roi": 0.10,
    "default_assignment_fee": 15_000,
    "min_profit_threshold": 30_000,
    # Novation (Underwriting!E41-E44)
    "novation_retail_costs_pct": 0.09,
    "novation_holding_costs": 3_000,
    "novation_min_floor": 10_000,
    "novation_preferred_target": 30_000,
    # Strategy thresholds (encoded in v3 strategy formula)
    "rehab_zone_floor": 50_000,           # profit ≥ this → rehab-zone band
    "wholesale_only_floor": 30_000,       # profit ≥ this → at least wholesale viable
    "gap_marginal_threshold": 50_000,     # gap > this → marginal
    "gap_too_wide_threshold": 70_000,     # gap > this → forces pivot
    "scope_light_max": 20_000,            # rehab ≤ this → Light scope
    "scope_heavy_min": 80_000,            # rehab > this → Heavy scope
    "dc_assignment_fee_threshold": 25_000,  # fee ≥ this → DC required
    "novation_rehab_cap": 20_000,         # novation requires rehab ≤ this
    "mls_rehab_pct_of_arv": 0.08,         # MLS requires rehab ≤ this % of ARV
    "mls_min_commission": 8_000,
    "mls_commission_rate": 0.03,
    "fat_fee_buyer_floor": 50_000,        # end buyer should keep at least this
    "fat_fee_target_pct": 0.25,           # fat fee = this % of projected profit
}


# ============================================================================
# REPAIR RATE TABLE — mirror of Repair Rates tab
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
    "bathroom_full_remodel": 6_000,
    "electrical_standard_misc": 1_500,
    "electrical_breaker_box": 2_500,
    "electrical_full": 4_000,
    "landscaping": 1_500,
    "appliances": 6_000,
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


# ============================================================================
# REHAB CALCULATOR — mirrors the toggle-based rehab estimate
# ============================================================================
def rehab_subtotal(rehab: Dict[str, Any], sqft: int, baths: float, pool: bool) -> float:
    """Compute total rehab from toggle dict.

    rehab dict keys (all optional; missing = not included):
      roof: {"include": bool, "type": "Shingle"|"Tile"|"Flat"}
      electrical: {"include": bool, "type": "Standard misc work"|"Replace Breaker Box"|"Full (panel + misc)"}
      ac: {"include": bool}
      kitchen: {"include": bool}
      bathrooms: {"include": bool}
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
    """
    r = REPAIR_RATES
    total = 0.0

    def get(key):
        return rehab.get(key, {}) or {}

    # Roof
    roof = get("roof")
    if roof.get("include"):
        t = roof.get("type", "Shingle")
        rate = {
            "Flat": r["roof_flat_per_sqft"],
            "Shingle": r["roof_shingle_per_sqft"],
            "Tile": r["roof_tile_per_sqft"],
        }.get(t, r["roof_shingle_per_sqft"])
        total += sqft * rate

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

    # Kitchen
    if get("kitchen").get("include"):
        total += r["kitchen_full_remodel"]

    # Bathrooms
    if get("bathrooms").get("include"):
        total += baths * r["bathroom_full_remodel"]

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

    # Landscaping
    if get("landscaping").get("include"):
        total += r["landscaping"]

    # Appliances
    if get("appliances").get("include"):
        total += r["appliances"]

    # Pool (only if subject has pool)
    pool_r = get("pool")
    if pool_r.get("include") and pool:
        t = pool_r.get("type", "Replace Motor")
        total += {
            "Replace Motor": r["pool_replace_motor"],
            "Replace Pump": r["pool_replace_pump"],
            "Heater": r["pool_heater"],
            "Waterline Tile": r["pool_waterline_tile"],
            "Diamond Brite": r["pool_diamond_brite"],
        }.get(t, r["pool_replace_motor"])

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


# ============================================================================
# HOLDING COSTS
# ============================================================================
def monthly_holding(pool: bool, hoa: float = 0, maintenance: float = 0) -> float:
    r = REPAIR_RATES
    water = r["water_with_pool"] if pool else r["water_no_pool"]
    electric = r["electric_with_pool"] if pool else r["electric_no_pool"]
    insurance = r["insurance_vacant"]
    return maintenance + water + electric + insurance + hoa


# ============================================================================
# MAO / PROFIT CALCULATIONS
# ============================================================================
def cost_of_money_factor(ltv: float, rate: float, months: int, points: float) -> float:
    """COM factor per $ of purchase price. Mirror of B61."""
    return ltv * (rate * months / 12 + points)


def cash_mao(
    arv: float,
    rehab_total: float,
    sale_closing_pct: float,
    holding_total: float,
    target_roi: float,
    purchase_closing_pct: float,
    com_factor: float,
) -> float:
    """Mirror of Underwriting!B74. Maximum cash offer."""
    if arv <= 0:
        return 0
    sale_closing = arv * sale_closing_pct
    numerator = arv / (1 + target_roi) - rehab_total - sale_closing - holding_total
    denominator = 1 + purchase_closing_pct + com_factor
    return numerator / denominator if denominator > 0 else 0


def round_down_to_1k(x: float) -> float:
    return math.floor(x / 1000) * 1000


def net_profit_at_price(
    purchase_price: float,
    arv: float,
    rehab_total: float,
    sale_closing_pct: float,
    holding_total: float,
    purchase_closing_pct: float,
    ltv: float,
    com_factor: float,
) -> tuple:
    """Returns (net_profit, tpc, roi)."""
    sale_closing = arv * sale_closing_pct
    purchase_closing = purchase_price * purchase_closing_pct
    com = ltv * purchase_price * com_factor  # mirrors v3 B85 (uses LTV again)
    tpc = (purchase_price + purchase_closing + rehab_total + sale_closing
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
    distress_statuses = {"60+", "90+", "NOD", "Foreclosure"}
    return equity <= 0 or payment_status in distress_statuses


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
) -> Dict[str, float]:
    """Returns dict with walk_away, opening, stretch."""
    is_novation = "Novation" in strategy
    is_pass = strategy in ("NO-GO — Pass", "Pass — Gap to MAO Too Wide")
    is_mls = strategy == "MLS Referral"

    if is_pass or is_mls:
        return {"walk_away": 0, "opening": 0, "stretch": 0}

    if strategy == "Rehab":
        walk = cash_mao_value
    elif is_novation:
        walk = benchmark
    else:
        walk = wholesale_mao_value

    opening = round_down_to_1k(walk * 0.96) if walk > 0 else 0
    stretch_bonus = 5_000 if buyer_demand_confirmed else 2_000
    stretch = walk + stretch_bonus if walk > 0 else 0

    return {"walk_away": walk, "opening": opening, "stretch": stretch}


# ============================================================================
# RATIONALE / DISPOSITION / ACTION ITEMS
# ============================================================================
def fmt_money(x: float) -> str:
    if x is None:
        return "$0"
    return f"${x:,.0f}"


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
        return (f"Net profit {fmt_money(p['net_profit'])} clears "
                f"${p['params']['rehab_zone_floor']:,.0f} rehab threshold; scope {p['eff_scope']}; "
                f"gap {fmt_money(p['gap'])} within range.")
    if strategy == "Wholesale — Assignment":
        return (f"Profit {fmt_money(p['net_profit'])} in $30-50k band; assignment fits "
                f"(fee {fmt_money(p['assignment_fee'])} below DC trigger).")
    if "heavy scope" in strategy:
        return (f"Profit {fmt_money(p['net_profit'])} puts this in rehab zone, but "
                f"{fmt_money(p['rehab_total'])} rehab is Heavy — too risky to take down. "
                f"Wholesale via DC with fat fee target {fmt_money(p['target_fat_fee'])} "
                f"(25% of profit).")
    return f"Profit fits wholesale band; DC required — fee/contract/buyer triggers active."


def disposition_text(strategy: str) -> str:
    if strategy == "NO-GO — Pass":
        return "Pass — math fails the $30k floor."
    if strategy == "Pass — Gap to MAO Too Wide":
        return "Pass — educate seller on market value; circle back if asking drops."
    if strategy == "MLS Referral":
        return "Refer to in-house realtor; list on MLS at retail; collect listing commission on close."
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
def compute_recommendation(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level orchestrator. Given a complete inputs dict, returns a complete
    recommendation dict.

    Required input keys:
      property: {address, city, state, zip, beds, baths, sqft, year, pool, hoa, asking}
      arv: float (the "ARV to use" — user override)
      rehab: dict of toggles (see rehab_subtotal docstring)
      seller: {mtg1, mtg2, other_liens, payment_status, required_net, timeline,
               reason, occupancy, condition_confirmed, buyer_demand, assignable,
               buyer_prefers_dc, open_to_mls}
      params: optional dict to override DEFAULTS
    """
    params = {**DEFAULTS, **(inputs.get("params") or {})}
    prop = inputs["property"]
    arv = inputs.get("arv", 0) or 0
    rehab = inputs.get("rehab", {}) or {}
    seller = inputs.get("seller", {}) or {}

    sqft = prop.get("sqft", 0) or 0
    baths = prop.get("baths", 0) or 0
    pool = (prop.get("pool", "No") == "Yes")
    asking = prop.get("asking", 0) or 0
    hoa = prop.get("hoa", 0) or 0

    # Rehab
    rehab_sub = rehab_subtotal(rehab, sqft, baths, pool)
    rehab_total = rehab_with_contingency(rehab_sub)

    # Holding
    monthly_hold = monthly_holding(pool, hoa)
    duration = params["loan_duration_months"]
    total_holding = monthly_hold * duration

    # COM factor + Cash MAO
    com = cost_of_money_factor(
        params["ltv"], params["interest_rate"], duration, params["points"]
    )
    raw_cash_mao = cash_mao(
        arv, rehab_total, params["sale_closing_pct"], total_holding,
        params["target_roi"], params["purchase_closing_pct"], com,
    )
    cash_offer = round_down_to_1k(max(0, raw_cash_mao))
    wholesale_mao_raw = raw_cash_mao - params["default_assignment_fee"]
    wholesale_offer = round_down_to_1k(max(0, wholesale_mao_raw))

    # Net profit at Cash MAO
    net_profit, tpc, roi = net_profit_at_price(
        cash_offer, arv, rehab_total, params["sale_closing_pct"],
        total_holding, params["purchase_closing_pct"], params["ltv"], com,
    )

    # Deal Status
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

    # Gap analysis
    gap = (asking - cash_offer) if asking > 0 else 0
    gap_cat = gap_category(gap, params)
    nov_max_asking = novation_max_asking(arv, rehab_total, params)

    # Scope, profit band
    eff_scope = scope_severity(rehab_total, params)
    pb = profit_band(net_profit, params)

    # Novation
    benchmark = required_net if required_net > 0 else asking
    nov_profit = novation_profit(
        arv, benchmark, rehab_total,
        params["novation_retail_costs_pct"],
        params["novation_holding_costs"],
    )
    nov_ok = novation_feasible(rehab_total, eff_scope, nov_profit, params)

    # MLS
    mls_comm = mls_commission(asking, arv, params["mls_commission_rate"])
    mls_ok = mls_feasible(rehab_total, arv, eff_scope, open_to_mls, mls_comm, params)

    # Master strategy
    strategy = decide_strategy(
        pb, distress, asking, gap, nov_ok, nov_profit, mls_ok,
        benchmark, wholesale_offer, eff_scope,
        params["default_assignment_fee"], assignable, buyer_prefers_dc, params,
    )

    # Offer terms
    terms = offer_terms(strategy, cash_offer, wholesale_offer, benchmark,
                        buyer_demand_confirmed)

    # Target assignment fee
    if "heavy scope" in strategy:
        recommended_fee = target_fat_fee(net_profit, params)
        fee_note = (f"Fat fee = 25% of profit {fmt_money(net_profit)}; floor $15k, "
                    f"ceiling {fmt_money(max(15000, net_profit - 50000))} "
                    f"(end buyer keeps ≥$50k)")
    elif "Wholesale" in strategy or "Short Sale" in strategy:
        recommended_fee = params["default_assignment_fee"]
        fee_note = "Default $15k; flex down to $2k floor to clear"
    else:
        recommended_fee = None
        fee_note = ""

    # Build context for rationale text
    rationale_ctx = {
        "params": params, "net_profit": net_profit, "rehab_total": rehab_total,
        "asking": asking, "gap": gap, "cash_offer": cash_offer,
        "nov_max_asking": nov_max_asking, "mls_commission": mls_comm,
        "equity": equity, "payment_status": payment_status,
        "benchmark": benchmark, "nov_profit": nov_profit,
        "eff_scope": eff_scope, "assignment_fee": params["default_assignment_fee"],
        "target_fat_fee": recommended_fee or params["default_assignment_fee"],
    }

    return {
        # Headline
        "strategy": strategy,
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

        # Snapshot
        "arv": arv,
        "asking": asking,
        "rehab_total": rehab_total,
        "rehab_subtotal": rehab_sub,
        "total_holding": total_holding,
        "monthly_holding": monthly_hold,
        "total_project_cost": tpc,
        "net_profit": net_profit,
        "roi": roi,
        "cash_offer": cash_offer,
        "wholesale_offer": wholesale_offer,
        "deal_status": status,
        "deal_status_reason": status_reason,
        "mls_commission_estimate": mls_comm,

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
