"""Sell to Exodus — Acquisitions Underwriting Tool (web).

Run locally:
    streamlit run app.py

Hosted: deploy to Streamlit Cloud, point at this repo, set secrets.toml.
"""
import streamlit as st
from modules.auth import require_login, sidebar_account_widget
from modules.db import list_deals, init_db

LOGO_PATH = "assets/sell_to_exodus.png"

st.set_page_config(
    page_title="Sell to Exodus — Underwriting",
    page_icon=LOGO_PATH,
    layout="wide",
    initial_sidebar_state="expanded",
)

user = require_login()
sidebar_account_widget()
init_db()

# === HOME ===
# Centered brand logo
_l, _c, _r = st.columns([1, 3, 1])
with _c:
    st.image(LOGO_PATH, use_container_width=True)

st.subheader("Acquisitions Underwriting Tool")

st.markdown("""
This tool takes a property's details, comps, rehab estimate, and seller-call
signals — and recommends the right acquisition strategy: wholesale assignment,
double close, rehab, novation, short sale, MLS referral, or pass.

Every recommendation comes with offer terms, a target assignment fee or
commission, action items, and a one-paragraph rationale. You can also export
a polished Word or PDF memo and save the deal to your team's history.
""")

st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    st.markdown("### 📝 New Deal Analysis")
    st.write("Run a new underwriting from scratch — fill in property details, "
             "upload comps, toggle rehab items, enter seller info, get a "
             "recommendation in seconds.")
    if st.button("Start a new deal →", type="primary", use_container_width=True):
        st.switch_page("pages/1_New_Deal.py")

with col2:
    st.markdown("### 📚 Past Deals")
    st.write("Search and review every deal your team has analyzed. Pull up the "
             "inputs, recommendation, and memo for any prior underwriting.")
    if st.button("View past deals →", use_container_width=True):
        st.switch_page("pages/2_Past_Deals.py")

# Quick stats
st.markdown("---")
st.markdown("### Recent Activity")
recent = list_deals(limit=5)
if recent:
    import pandas as pd
    df = pd.DataFrame(recent)[["created_at", "address", "strategy", "net_profit"]]
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%b %d, %Y %H:%M")
    df["net_profit"] = df["net_profit"].apply(lambda x: f"${x:,.0f}" if x else "—")
    df.columns = ["Analyzed", "Address", "Strategy", "Projected Profit"]
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No deals yet. Start by analyzing your first deal.")
