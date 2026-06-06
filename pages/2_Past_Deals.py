"""Past Deals — search and review the team's deal history."""
import streamlit as st
import pandas as pd
from modules.auth import require_login, sidebar_account_widget
from modules.db import (list_deals, get_deal, delete_deal, distinct_strategies,
                        load_chat_messages, save_chat_message)
from modules.memo import build_word_memo, build_pdf_memo
from modules import chat as chat_mod

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

        # Key Numbers — strategy-aware
        from modules.strategy import key_numbers_for
        metrics = key_numbers_for(outputs, prop)
        for i in range(0, len(metrics), 4):
            cols = st.columns(4)
            for j, (label, value) in enumerate(metrics[i:i + 4]):
                cols[j].metric(label, value)

        # Action row — Edit / Word memo / PDF memo / Delete
        c_edit, c_word, c_pdf, c_del = st.columns(4)

        # Edit — queue the deal for loading on New Deal page and switch.
        # No RentCast call needed; comps and inputs are restored from JSONB.
        if c_edit.button(
            "✏️ Open in editor",
            use_container_width=True,
            type="primary",
            help="Reopen this deal on the New Deal page with every "
                 "assumption, rehab toggle, and comp prepopulated. Edit "
                 "anything, then click 'Save changes' to update the deal "
                 "in place — no new row, no fresh comp pull.",
        ):
            st.session_state["_pending_deal_load"] = int(deal["id"])
            try:
                st.switch_page("pages/1_New_Deal.py")
            except Exception:
                # Streamlit < 1.30 fallback — show a manual nav prompt
                st.info(
                    "Deal queued. Open the **New Deal** page from the "
                    "left sidebar to continue editing."
                )

        try:
            word_bytes = build_word_memo(prop, outputs, seller)
            c_word.download_button(
                "📄 Word memo", word_bytes,
                file_name=f"Exodus_Memo_Deal_{deal['id']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True)
        except Exception as e:
            c_word.error(f"Word: {e}")

        try:
            pdf_bytes = build_pdf_memo(prop, outputs, seller)
            c_pdf.download_button(
                "📄 PDF memo", pdf_bytes,
                file_name=f"Exodus_Memo_Deal_{deal['id']}.pdf",
                mime="application/pdf",
                use_container_width=True)
        except Exception as e:
            c_pdf.error(f"PDF: {e}")

        if c_del.button("🗑 Delete", use_container_width=True):
            if delete_deal(int(selected_id)):
                st.success(f"Deal #{selected_id} deleted.")
                st.rerun()

        with st.expander("Show full inputs/outputs (JSON)"):
            st.json({"inputs": inputs, "outputs": outputs})

        # === CHAT — saved conversation about this deal ===
        st.markdown("---")
        with st.expander("💬 Brainstorm with Claude about this deal", expanded=True):
            if not chat_mod.is_configured():
                st.warning("Chat not configured — add Anthropic API key in Streamlit Secrets.")
            else:
                # Load saved chat from DB
                saved_messages = load_chat_messages(int(selected_id))

                # Render saved messages
                for msg in saved_messages:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

                # Allow continuing the conversation
                user_input = st.chat_input(
                    "Continue the conversation...",
                    key=f"chat_input_deal_{selected_id}",
                )
                if user_input:
                    # Save user message immediately
                    save_chat_message(int(selected_id), "user", user_input)
                    with st.chat_message("user"):
                        st.markdown(user_input)

                    # Stream Claude's response
                    with st.chat_message("assistant"):
                        placeholder = st.empty()
                        full_response = ""
                        try:
                            system_prompt = chat_mod.build_system_prompt(
                                prop, outputs, seller, rehab_items=None
                            )
                            history = [{"role": m["role"], "content": m["content"]}
                                       for m in saved_messages]
                            for chunk in chat_mod.stream_response(system_prompt, history, user_input):
                                full_response += chunk
                                placeholder.markdown(full_response + " ▌")
                            placeholder.markdown(full_response)
                            save_chat_message(int(selected_id), "assistant", full_response)
                        except Exception as e:
                            placeholder.error(f"Chat error: {e}")
