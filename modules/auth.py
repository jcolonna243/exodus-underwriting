"""Auth gate using Streamlit's native st.login() + email allow-list.

Streamlit 1.42+ ships a built-in st.login() that wraps OAuth (Google, etc.)
configured via .streamlit/secrets.toml. After login, st.user.email is the
signed-in email. We allow-list emails configured in secrets.toml.

Setup (one-time, in Streamlit Cloud):
  1. Create a Google Cloud OAuth credential (Web application).
  2. Set the redirect URI to your Streamlit app URL + "/oauth2callback".
  3. Add this to .streamlit/secrets.toml in your Streamlit Cloud app settings:

     [auth]
     redirect_uri = "https://your-app.streamlit.app/oauth2callback"
     cookie_secret = "<long random string>"
     client_id = "<Google OAuth client id>"
     client_secret = "<Google OAuth client secret>"
     server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

     [allowed_emails]
     emails = ["jo@exoduspropertysolutions.com", "teammate@..."]

When secrets are not configured (local dev), auth is bypassed.
"""
import streamlit as st


def require_login():
    """Block app rendering until user is signed in AND on the allow-list."""
    # Local dev / no auth configured: skip
    try:
        has_auth = "auth" in st.secrets
    except Exception:
        has_auth = False

    if not has_auth:
        # Show a banner that the app is in dev mode and let them through
        st.warning("⚙️ Auth not configured — running in open mode. "
                   "Configure secrets.toml to enable Google sign-in.")
        return {"email": "dev@local", "name": "Dev User"}

    # If user is not logged in, show login button
    if not getattr(st, "user", None) or not st.user.is_logged_in:
      # Centered brand logo on the login screen
        _l, _c, _r = st.columns([1, 2, 1])
        with _c:
            st.image("assets/sell_to_exodus.png", use_container_width=True)
        st.subheader("Acquisitions Underwriting Tool")
        st.markdown("---")
        st.write("Sign in with your team Google account to continue.")
        if st.button("Sign in with Google", type="primary"):
            st.login()
        st.stop()

    # User is logged in — check allow-list
    email = (st.user.email or "").lower().strip()
    try:
        allowed = [e.lower().strip() for e in st.secrets["allowed_emails"]["emails"]]
    except Exception:
        allowed = []

    if email not in allowed:
        st.error(f"Sorry — `{email}` is not on the allow-list for this app.")
        st.write("If you should have access, contact Jo to add your email.")
        if st.button("Sign out"):
            st.logout()
        st.stop()

    # Auth OK — return user info
    return {"email": email, "name": st.user.name or email}


def sidebar_account_widget():
    """Render account info + sign-out in the sidebar."""
    try:
        if getattr(st, "user", None) and st.user.is_logged_in:
            with st.sidebar:
                st.markdown("---")
                st.write(f"👤 **{st.user.name or st.user.email}**")
                if st.button("Sign out", key="signout_btn"):
                    st.logout()
    except Exception:
        pass
