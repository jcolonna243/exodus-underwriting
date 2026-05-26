"""Past Deals — search and review the team's deal history."""
import streamlit as st
import pandas as pd
from modules.auth import require_login, sidebar_account_widget
from modules.db import list_deals, get_deal, delete_deal, distinct_strategies
from modules.memo import build_word_memo, build_pdf_memo

st.set_page_config(page_title="Past Deals", page_icon="📚", layout="wide")
user = require_login()
sidebar_account_widget()

st.title("📚 Past Deals")

# Filters
c1, c2, c3 = st.columns([3, 2, 1])
search = c1.text_input("Search by address", placeholder="e.g., SW 152nd")
strategies = ["All"] + distinct_strategies()
strat_filter = c2.selectbox("Filter by strategy", strategies)
limit = c3.number_input("Show", min_value=10, max_value=500, value=50, step=10)

deals = list_deals(limit=int(limit), search=search or None,
                   strategy_filter=strat_filter)

if not deals:
    st.info("No deals match your filters yet. Run an underwriting on the New Deal page.")
    st.stop()

# Build the table
df = pd.DataFrame(deals)
df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%b %d, %Y %H:%M")
display = df[["id", "created_at", "address", "city", "strategy",
              "arv", "asking", "cash_offer", "net_profit", "created_by"]].copy()
display["arv"] = display["arv"].apply(lambda x: f"${x:,.0f}" if x else "—")
display["asking"] = display["asking"].apply(lambda x: f"${x:,.0f}" if x else "—")
display["cash_offer"] = display["cash_offer"].apply(lambda x: f"${x:,.0f}" if x else "—")
display["net_profit"] = display["net_profit"].apply(lambda x: f"${x:,.0f}" if x else "—")
display.columns = ["ID", "Analyzed", "Address", "City", "Strategy",
                   "ARV", "Asking", "Cash Offer", "Net Profit", "By"]
st.dataframe(display, use_container_width=True, hide_index=True)

# Deal detail
st.markdown("---")
st.markdown("### Deal Detail")
selected_id = st.number_input("Enter Deal ID to view", min_value=0,
                              value=int(df.iloc[0]["id"]) if not df.empty else 0)
if selected_id > 0:
    deal = get_deal(int(selected_id))
    if not deal:
        st.warning(f"No deal with ID {selected_id}.")
    else:
        inputs = deal["inputs"]
        outputs = deal["outputs"]
        prop = inputs.get("property", {})
        seller = inputs.get("seller", {})

        st.subheader(f"Deal #{deal['id']} — {prop.get('address', '')}")
        st.caption(f"Analyzed {deal['created_at']} by {deal['created_by']}")

        # Banner
        st.markdown(
            f"""
            <div style="background-color:#C6EFCE; padding:14px; border-radius:6px;
                        border-left:5px solid #1F4E78; margin-bottom:12px;">
                <div style="font-size:18px; font-weight:bold; color:#1F4E78;">
                    {outputs.get('strategy', '')}
                </div>
                <div style="font-size:12px; color:#333; font-style:italic;">
                    {outputs.get('rationale', '')}
                </div>
            </div>
            """, unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ARV", f"${outputs.get('arv', 0):,.0f}")
        c2.metric("Asking", f"${prop.get('asking', 0):,.0f}")
        c3.metric("Net Profit", f"${outputs.get('net_profit', 0):,.0f}")
        c4.metric("Cash Offer", f"${outputs.get('cash_offer', 0):,.0f}")

        c1, c2, c3 = st.columns(3)

        try:
            word_bytes = build_word_memo(prop, outputs, seller)
            c1.download_button(
                "📄 Re-download Word memo", word_bytes,
                file_name=f"Exodus_Memo_Deal_{deal['id']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True)
        except Exception as e:
            c1.error(f"Word: {e}")

        try:
            pdf_bytes = build_pdf_memo(prop, outputs, seller)
            c2.download_button(
                "📄 Re-download PDF memo", pdf_bytes,
                file_name=f"Exodus_Memo_Deal_{deal['id']}.pdf",
                mime="application/pdf",
                use_container_width=True)
        except Exception as e:
            c2.error(f"PDF: {e}")

        if c3.button("🗑 Delete this deal", use_container_width=True):
            if delete_deal(int(selected_id)):
                st.success(f"Deal #{selected_id} deleted.")
                st.rerun()

        with st.expander("Show full inputs/outputs (JSON)"):
            st.json({"inputs": inputs, "outputs": outputs})
