"""Shared Supabase client. Cached across Streamlit reruns.

Reads credentials from st.secrets["supabase"]:
  url                — your project URL (e.g. https://xxxxx.supabase.co)
  service_role_key   — secret service-role key (bypasses RLS)

The service-role key MUST stay secret. It lives in Streamlit Cloud Secrets
only — never in code, GitHub, or chat logs.
"""
import streamlit as st


@st.cache_resource
def get_client():
    """Return a cached Supabase client. Created once per Streamlit session."""
    from supabase import create_client
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["service_role_key"]
    return create_client(url, key)


def is_configured() -> bool:
    """True if Supabase credentials are set in Streamlit Secrets."""
    try:
        return bool(st.secrets["supabase"]["url"]
                    and st.secrets["supabase"]["service_role_key"])
    except Exception:
        return False
