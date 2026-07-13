"""Dispo Marketing — cash-buyer deal sheet editor + PDF generator.

The page loads a saved deal, pre-populates comps + rehab items from the
underwriting file, and lets Jo edit anything (asking price, comp values,
rehab lines) before generating the marketing PDF. The PDF's ARV, spread,
rehab range, and the email/SMS/Facebook copy templates all reflect the
live edits — so if he bumps a comp to $600k or trims the rehab estimate,
the drafts update in the download.

Rehab range is a slider (5-30%) that widens/tightens the band around each
line item. Asking price starts BLANK — Jo types it in each time.
"""
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from modules.auth import require_login, sidebar_account_widget
from modules.db import get_deal
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

# Recompute as REHAB strategy so every field (ARV, rehab_total, MAO) reflects
# the buy-rehab-resell math — the math a cash buyer cares about.
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
    "Edit the asking price, comps, and rehab scope on the left. The marketing "
    "PDF on the right updates every time you change something."
)

st.markdown("---")


# ============================================================================
# LEFT: editable inputs   |   RIGHT: live preview + download
# ============================================================================
col_edit, col_preview = st.columns([3, 2])

with col_edit:
    st.markdown("## ✏️ Edit the deal")

    # --- Asking price ---------------------------------------------------
    st.markdown("### 💰 Asking price to cash buyer")
    asking_price = st.number_input(
        "What you're asking the cash buyer to pay",
        min_value=0, value=None, step=1000,
        placeholder="Enter asking price (e.g. 385000)",
        help="Leave blank to see the PDF with TBD in the asking box. "
             "Type a number to compute the spread.",
        key="dispo_asking",
    )

    # --- ARV override ---------------------------------------------------
    st.markdown("### 📈 ARV (After-Repair Value)")
    _default_arv = float(rec.get("arv", 0) or 0)
    arv_override = st.number_input(
        "ARV to justify with your comps",
        min_value=0, value=int(_default_arv) if _default_arv > 0 else 0,
        step=1000,
        help="Pre-filled from your underwriting file. Bump it up if the "
             "top comps support a higher number, or trim it down if you "
             "want the cash-buyer story to feel more conservative.",
        key="dispo_arv_override",
    )

    # --- Rehab range % --------------------------------------------------
    st.markdown("### 📏 Rehab range width")
    range_pct = st.slider(
        "How wide should the LOW-HIGH range be?",
        min_value=5, max_value=30, value=15, step=1,
        format="±%d%%",
        help="Narrower = more confident to buyers. Wider = more conservative.",
        key="dispo_range_pct",
    ) / 100.0

    # --- Comps editor ---------------------------------------------------
    st.markdown("### 🏘️ Comps — highest defensible")
    st.caption(
        "Pre-loaded from your underwriting file, sorted by sold price DESC. "
        "Bump prices up if you want the ARV story to look stronger. "
        "**Click the blank row at the bottom of the table to add a new comp** — "
        "type the address, city, sold price, sqft, etc. Only the **top 5** "
        "by sold price get printed on the PDF."
    )
    saved_comps = inputs.get("comps") or []
    if saved_comps:
        comps_df = pd.DataFrame(saved_comps)
    else:
        comps_df = pd.DataFrame(columns=[
            "address", "city", "beds", "baths", "sqft",
            "year", "sold_price", "sold_date",
        ])
    # Keep only display cols; sort by sold_price DESC
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
            "address": st.column_config.TextColumn("Address"),
            "city": st.column_config.TextColumn("City"),
            "beds": st.column_config.NumberColumn("Beds", format="%d"),
            "baths": st.column_config.NumberColumn("Baths"),
            "sqft": st.column_config.NumberColumn("Sqft", format="%d"),
            "year": st.column_config.NumberColumn("Built", format="%d"),
            "sold_price": st.column_config.NumberColumn(
                "Sold For", format="$%d",
            ),
            "sold_date": st.column_config.TextColumn("Sold Date"),
        },
        key="dispo_comps_editor",
    )

    # --- Rehab items editor ---------------------------------------------
    st.markdown("### 🔨 Rehab line items")
    st.caption(
        "Pre-loaded from your underwriting rehab breakdown. Edit any cost, "
        "add missing items, delete anything the cash buyer wouldn't care "
        "about. The LOW-HIGH range on the PDF is computed from these lines."
    )
    # Pull the initial rehab breakdown from the underwriting engine
    sqft_val = int(prop.get("sqft", 0) or 0)
    baths_val = float(prop.get("baths", 0) or 0)
    pool_val = (prop.get("pool", "No") == "Yes")
    stories_val = prop.get("stories", 1) or 1
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
            "Item": st.column_config.TextColumn("Item / Scope"),
            "Cost": st.column_config.NumberColumn("Est. Cost", format="$%d"),
        },
        key="dispo_rehab_editor",
    )


# ============================================================================
# RIGHT PANEL — live math + download button
# ============================================================================
with col_preview:
    st.markdown("## 📊 Live preview")

    # Build the edited data structures for the PDF
    # Clean up the comps: drop rows missing address, cast numbers
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

    clean_comps = _clean_comps(edited_comps)
    clean_rehab = _clean_rehab(edited_rehab)

    # Compute live math (use the user's ARV override so the metrics and PDF
    # reflect any bump the user made)
    arv = float(arv_override if arv_override else rec.get("arv", 0) or 0)
    rehab_est = sum(c for _, c in clean_rehab)
    low_mult = 1.0 - range_pct
    high_mult = 1.0 + range_pct
    rehab_low = sum(round(c * low_mult / 50) * 50 for _, c in clean_rehab)
    rehab_high = sum(round(c * high_mult / 50) * 50 for _, c in clean_rehab)
    rehab_mid = (rehab_low + rehab_high) / 2

    if asking_price and asking_price > 0:
        spread = arv - asking_price - rehab_mid
    else:
        spread = None

    # Show it
    c1, c2 = st.columns(2)
    c1.metric("Asking",
              f"${asking_price:,.0f}" if asking_price else "TBD")
    c2.metric("ARV", f"${arv:,.0f}")
    c3, c4 = st.columns(2)
    c3.metric(
        "Rehab LOW-HIGH",
        f"${rehab_low:,.0f} - ${rehab_high:,.0f}",
    )
    c4.metric(
        "Spread (mid)",
        f"${spread:,.0f}" if spread is not None else "TBD",
        delta=None,
    )

    st.markdown("### Top 5 comps that will print")
    if clean_comps:
        top5 = sorted(
            [c for c in clean_comps if c.get("sold_price")],
            key=lambda c: c["sold_price"], reverse=True,
        )[:5]
        st.dataframe(
            pd.DataFrame(top5)[["address", "sold_price", "sqft"]],
            hide_index=True, use_container_width=True,
        )
    else:
        st.warning("No comps to print. Add at least 1 with a sold price.")

    # --- Download button ------------------------------------------------
    st.markdown("---")

    def _gen_pdf() -> bytes:
        # Merge the user's ARV override into rec so the PDF sees it in the
        # ARV box, the comps banner, and the marketing copy templates.
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
