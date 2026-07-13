"""Dispo Marketing — cash-buyer deal sheet editor + PDF generator.

v24.12d layout:
  - Section 1: Asking + Rehab range width side-by-side (2 cols).
  - Section 2: Comps editor (full width). Bump prices, add rows.
  - Section 3: Rehab line items editor (full width).
  - Section 4: Live preview.
      * ARV is AUTO-COMPUTED from top-5 comps' avg $/sqft × subject sqft.
      * "Override ARV" input right above the metrics — 0 = use auto.
      * 4-metric row: Asking / ARV / Rehab range / Spread.
      * Top 5 comps preview + Generate PDF button.
"""
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from modules.auth import require_login, sidebar_account_widget
from modules.db import get_deal, get_dispo_edits, save_dispo_edits
from modules.dispo_marketing_pdf import build_dispo_marketing_pdf
from modules.strategy import compute_recommendation, rehab_breakdown


st.set_page_config(page_title="Dispo Marketing",
                   page_icon="🚀", layout="wide")
user = require_login()
sidebar_account_widget()


# --- Load the deal ------------------------------------------------------
deal_id = st.session_state.get("dispo_deal_id")
if not deal_id:
    st.title("🚀 Dispo Marketing")
    st.warning(
        "No deal loaded. Open a saved deal from the **📝 New Deal** or "
        "**📚 Past Deals** page and click **🚀 Dispo Marketing**."
    )
    st.stop()

deal = get_deal(int(deal_id))
if not deal:
    st.error(f"Deal #{deal_id} was not found.")
    st.stop()

inputs = deal.get("inputs", {}) or {}
prop = inputs.get("property", {}) or {}

# --- Load saved Dispo edits (kept separate from underwriting inputs) ---
saved_dispo = get_dispo_edits(int(deal_id)) or {}

# Pre-populate widget session state from saved edits ONCE per deal load.
# Streamlit uses session_state[key] as the source of truth on re-renders,
# so this only matters the first time we land on the page for this deal.
_load_marker_key = "_dispo_loaded_deal"
if st.session_state.get(_load_marker_key) != deal_id:
    if "asking_price" in saved_dispo:
        st.session_state["dispo_asking"] = int(saved_dispo["asking_price"] or 0) or None
    if "arv_override" in saved_dispo:
        st.session_state["dispo_arv_override"] = int(saved_dispo["arv_override"] or 0)
    if "range_pct" in saved_dispo:
        # Slider stores percent (5-30), not decimal fraction.
        st.session_state["dispo_range_pct"] = int(round(float(saved_dispo["range_pct"]) * 100))
    st.session_state[_load_marker_key] = deal_id

try:
    rec = compute_recommendation(inputs, force_strategy="Rehab")
except Exception as e:
    st.error(f"Could not compute the deal numbers: {e}")
    st.stop()


# --- Header --------------------------------------------------------------
addr = prop.get("address", "your property")
city_state = ", ".join(filter(None, [prop.get("city", ""),
                                     prop.get("state", "")]))
loc_line = f"{addr}" + (f" — {city_state}" if city_state else "")

st.markdown(
    f"""
    <div style="text-align:center; padding:12px 0 4px 0;">
        <div style="font-size:12px; color:#666; font-weight:600;
                    letter-spacing:1px;">EXODUS PROPERTY SOLUTIONS</div>
        <h1 style="color:#1F4E78; margin:6px 0 0 0; font-size:32px;">
            🚀 Dispo Marketing — {addr}
        </h1>
        <div style="font-size:14px; color:#555; margin-top:4px;">
            {loc_line}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Edit the asking price, comps, and rehab scope below. ARV auto-calculates "
    "from your top-5 comps × subject sqft — bump comps up to raise ARV. "
    "The 📊 preview at the bottom updates live."
)

st.markdown("---")


# ============================================================================
# SECTION 1 — Asking + Rehab range width (2 cols, full page width)
# ============================================================================
st.markdown("## ✏️ Deal parameters")

pc1, pc2 = st.columns(2)

with pc1:
    st.markdown("#### 💰 Asking price")
    asking_price = st.number_input(
        "What you're asking the cash buyer to pay",
        min_value=0, value=None, step=1000,
        placeholder="e.g. 385000",
        help="Leave blank to see the PDF with TBD in the asking box. "
             "Type a number to compute the spread.",
        key="dispo_asking",
        label_visibility="collapsed",
    )
    st.caption("Blank = TBD on the PDF")

with pc2:
    st.markdown("#### 📏 Rehab range width")
    range_pct = st.slider(
        "How wide should the LOW-HIGH range be?",
        min_value=5, max_value=30, value=15, step=1,
        format="±%d%%",
        help="Narrower = more confident to buyers. Wider = more conservative.",
        key="dispo_range_pct",
        label_visibility="collapsed",
    ) / 100.0
    st.caption("±% band around each rehab item")

st.markdown("---")


# ============================================================================
# SECTION 2 — Comps editor (FULL WIDTH)
# ============================================================================
st.markdown("## 🏘️ Comps — highest defensible")
st.caption(
    "Pre-loaded from your underwriting file, sorted by sold price DESC. "
    "Bump prices up to raise ARV. **Click the blank row at the bottom of the "
    "table to add a new comp** — type the address, city, sold price, sqft, "
    "etc. Only the **top 5** by sold price drive the ARV and print on the PDF."
)

# Prefer previously-saved Dispo edits; fall back to underwriting comps.
saved_comps = saved_dispo.get("comps") or inputs.get("comps") or []
if saved_comps:
    comps_df = pd.DataFrame(saved_comps)
else:
    comps_df = pd.DataFrame(columns=[
        "address", "city", "beds", "baths", "sqft",
        "year", "sold_price", "sold_date",
    ])
display_cols = ["address", "city", "beds", "baths", "sqft",
                "year", "sold_price", "sold_date"]
for c in display_cols:
    if c not in comps_df.columns:
        comps_df[c] = None
comps_df = comps_df[display_cols]
if "sold_price" in comps_df.columns:
    comps_df = comps_df.sort_values(
        by="sold_price", ascending=False, na_position="last",
    ).reset_index(drop=True)

edited_comps = st.data_editor(
    comps_df,
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "address": st.column_config.TextColumn(
            "Address", width="large",
            help="Street address of the comp",
        ),
        "city": st.column_config.TextColumn("City", width="medium"),
        "beds": st.column_config.NumberColumn("Beds", format="%d", width="small"),
        "baths": st.column_config.NumberColumn("Baths", width="small"),
        "sqft": st.column_config.NumberColumn(
            "Sqft", format="%d", width="small",
        ),
        "year": st.column_config.NumberColumn(
            "Built", format="%d", width="small",
        ),
        "sold_price": st.column_config.NumberColumn(
            "Sold For", format="$%d", width="medium",
        ),
        "sold_date": st.column_config.TextColumn(
            "Sold Date", width="medium",
        ),
    },
    key="dispo_comps_editor",
)

st.markdown("---")


# ============================================================================
# SECTION 3 — Rehab items editor (FULL WIDTH)
# ============================================================================
st.markdown("## 🔨 Rehab line items")
st.caption(
    "Pre-loaded from your underwriting rehab breakdown. Edit any cost, "
    "**click the blank row at the bottom to add missing items** (pool, "
    "landscaping, etc.), or delete anything a cash buyer wouldn't care "
    "about. Cost detail like *sq ft × $/sf* is scrubbed on the PDF."
)

sqft_val = int(prop.get("sqft", 0) or 0)
baths_val = float(prop.get("baths", 0) or 0)
pool_val = (prop.get("pool", "No") == "Yes")
stories_val = prop.get("stories", 1) or 1
if saved_dispo.get("rehab_items"):
    # Restore what the rep last edited.
    initial_items = [tuple(item) for item in saved_dispo["rehab_items"]]
else:
    try:
        initial_items = rehab_breakdown(
            inputs.get("rehab", {}) or {},
            sqft_val, baths_val, pool_val, stories=stories_val,
        )
    except Exception:
        initial_items = []
rehab_df = pd.DataFrame(initial_items, columns=["Item", "Cost"])

edited_rehab = st.data_editor(
    rehab_df,
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "Item": st.column_config.TextColumn(
            "Item / Scope", width="large",
        ),
        "Cost": st.column_config.NumberColumn(
            "Est. Cost", format="$%d", width="medium",
        ),
    },
    key="dispo_rehab_editor",
)

st.markdown("---")


# ============================================================================
# SECTION 4 — Live preview + download
# ============================================================================
st.markdown("## 📊 Live preview")


def _clean_comps(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        if pd.isna(row.get("address")) or not str(row.get("address")).strip():
            continue
        out.append({
            "address": str(row["address"]).strip(),
            "city": str(row.get("city") or "").strip(),
            "beds": row.get("beds") if pd.notna(row.get("beds")) else None,
            "baths": row.get("baths") if pd.notna(row.get("baths")) else None,
            "sqft": row.get("sqft") if pd.notna(row.get("sqft")) else None,
            "year": row.get("year") if pd.notna(row.get("year")) else None,
            "sold_price": float(row["sold_price"]) if pd.notna(row.get("sold_price")) else None,
            "sold_date": str(row.get("sold_date") or "").strip(),
        })
    return out


def _clean_rehab(df: pd.DataFrame) -> List:
    out = []
    for _, row in df.iterrows():
        lbl = row.get("Item")
        cost = row.get("Cost")
        if pd.isna(lbl) or not str(lbl).strip():
            continue
        if pd.isna(cost) or float(cost) <= 0:
            continue
        out.append((str(lbl).strip(), float(cost)))
    return out


def _compute_auto_arv(clean_comps: List[Dict[str, Any]],
                     subject_sqft: int) -> float:
    """Auto-ARV = subject sqft × avg $/sqft of top-5 comps by sold price.
    Only includes comps that have BOTH sold_price and sqft."""
    if not subject_sqft or subject_sqft <= 0:
        return 0.0
    priced = [c for c in clean_comps
              if c.get("sold_price") and c.get("sqft")
              and float(c["sqft"]) > 0]
    if not priced:
        return 0.0
    priced.sort(key=lambda c: c["sold_price"], reverse=True)
    top5 = priced[:5]
    ppsqft_vals = [float(c["sold_price"]) / float(c["sqft"]) for c in top5]
    avg_ppsqft = sum(ppsqft_vals) / len(ppsqft_vals)
    # Round to nearest $1000 for clean presentation
    return round(avg_ppsqft * subject_sqft, -3)


clean_comps = _clean_comps(edited_comps)
clean_rehab = _clean_rehab(edited_rehab)

subject_sqft = int(prop.get("sqft", 0) or 0)
auto_arv = _compute_auto_arv(clean_comps, subject_sqft)

# ARV override input — 0 means use auto
override_col, help_col = st.columns([1, 3])
with override_col:
    arv_override = st.number_input(
        "Override ARV (0 = use auto)",
        min_value=0, value=0, step=1000,
        key="dispo_arv_override",
        help="Auto ARV is computed from your top-5 comps' avg $/sqft × the "
             "subject property sqft. Set a non-zero number here to override.",
    )
with help_col:
    if subject_sqft > 0 and auto_arv > 0:
        top5_priced = sorted(
            [c for c in clean_comps
             if c.get("sold_price") and c.get("sqft")
             and float(c["sqft"]) > 0],
            key=lambda c: c["sold_price"], reverse=True,
        )[:5]
        avg_ppsqft = (sum(float(c["sold_price"]) / float(c["sqft"])
                          for c in top5_priced) / len(top5_priced))
        st.info(
            f"**Auto ARV: ${auto_arv:,.0f}** "
            f"(avg ${avg_ppsqft:,.0f}/sqft × {subject_sqft:,} sqft, "
            f"from top {len(top5_priced)} comps)"
        )
    else:
        st.warning(
            "Need at least 1 comp with both a Sold Price AND a Sqft, plus "
            "a subject-property Sqft, to auto-calculate ARV."
        )

# Actual ARV = override if set, else auto
arv = float(arv_override if arv_override and arv_override > 0 else auto_arv)

# Rehab range from cleaned items
low_mult = 1.0 - range_pct
high_mult = 1.0 + range_pct
rehab_low = sum(round(c * low_mult / 50) * 50 for _, c in clean_rehab)
rehab_high = sum(round(c * high_mult / 50) * 50 for _, c in clean_rehab)
rehab_mid = (rehab_low + rehab_high) / 2

if asking_price and asking_price > 0 and arv > 0:
    spread = arv - asking_price - rehab_mid
else:
    spread = None

# 4-metric row, full width
m1, m2, m3, m4 = st.columns(4)
m1.metric("Asking",
          f"${asking_price:,.0f}" if asking_price else "TBD")
m2.metric("ARV", f"${arv:,.0f}" if arv > 0 else "TBD")
m3.metric(
    "Rehab (LOW-HIGH)",
    f"${rehab_low:,.0f} – ${rehab_high:,.0f}",
)
m4.metric(
    "Spread (mid)",
    f"${spread:,.0f}" if spread is not None else "TBD",
)

st.markdown("### Top 5 comps that will print on the PDF")
if clean_comps:
    top5 = sorted(
        [c for c in clean_comps if c.get("sold_price")],
        key=lambda c: c["sold_price"], reverse=True,
    )[:5]
    if top5:
        preview_df = pd.DataFrame(top5)
        preview_cols = [c for c in ["address", "city", "beds", "baths",
                                     "sqft", "sold_price", "sold_date"]
                        if c in preview_df.columns]
        st.dataframe(
            preview_df[preview_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "sold_price": st.column_config.NumberColumn(
                    "Sold For", format="$%d",
                ),
                "sqft": st.column_config.NumberColumn(
                    "Sqft", format="%d",
                ),
            },
        )
    else:
        st.warning("Add a Sold Price to at least 1 comp for the PDF to print.")
else:
    st.warning("No comps to print. Add at least 1 with a sold price.")


# --- Download button (full width, primary) ------------------------------
st.markdown("---")


def _gen_pdf() -> bytes:
    # Merge the effective ARV into rec so the PDF's ARV box, comps banner,
    # and email/SMS/FB copy templates all reflect it.
    rec_for_pdf = {**rec, "arv": arv}
    return build_dispo_marketing_pdf(
        prop=prop,
        rec=rec_for_pdf,
        inputs={"comps": clean_comps},
        rehab_items=clean_rehab,
        asking_price_override=asking_price,
        rehab_range_pct=range_pct,
    )


try:
    pdf_bytes = _gen_pdf()
    safe_addr = "".join(c if c.isalnum() or c in "-_" else "_"
                         for c in (prop.get("address", "deal") or "deal"))[:60]
    safe_date = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    st.download_button(
        "🚀 Generate Dispo Marketing PDF",
        data=pdf_bytes,
        file_name=f"Dispo_Marketing_{safe_addr}_{safe_date}.pdf",
        mime="application/pdf",
        type="primary",
        use_container_width=True,
        help="Two-page PDF: deal sheet (share with cash buyers) + "
             "pre-written Email / SMS / Facebook drafts (internal — "
             "copy-paste and send).",
    )
except Exception as e:
    st.error(f"Could not build the PDF: {e}")


# ============================================================================
# Persist current Dispo edits back to the deal record so they load next time
# ============================================================================
# We save on EVERY rerun — cheap Supabase write, keeps the state fresh. The
# save uses the "dispo" sub-key of inputs so underwriting numbers stay clean.
try:
    save_dispo_edits(int(deal_id), {
        "asking_price": int(asking_price) if asking_price else 0,
        "arv_override": int(arv_override) if arv_override else 0,
        "range_pct": float(range_pct),
        "comps": clean_comps,
        # rehab items are tuples in memory; JSON-safe as lists
        "rehab_items": [[lbl, cost] for lbl, cost in clean_rehab],
    })
except Exception:
    # Silent — don't block the page if the save has a transient hiccup.
    pass


st.markdown("---")
_reset_col, _reset_help = st.columns([1, 3])
with _reset_col:
    if st.button(
        "🔄 Reset from underwriting",
        key="dispo_reset_btn",
        use_container_width=True,
        help="Discard all Dispo edits (comps, rehab items, asking, ARV "
             "override, range %) and reload the initial underwriting values.",
    ):
        # Wipe saved dispo edits + widget state and re-run to reload defaults.
        try:
            save_dispo_edits(int(deal_id), {})
        except Exception:
            pass
        for k in ["dispo_asking", "dispo_arv_override", "dispo_range_pct",
                  "dispo_comps_editor", "dispo_rehab_editor",
                  "_dispo_loaded_deal"]:
            st.session_state.pop(k, None)
        st.rerun()
with _reset_help:
    st.caption(
        "Every edit above is auto-saved to this deal — leave the page and "
        "come back anytime. The Reset button discards those edits and "
        "reloads whatever's in the underwriting file."
    )
