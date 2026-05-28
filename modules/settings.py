"""App settings stored in Supabase.

Key/value (JSONB) store. Keys we use:
  - repair_rates           dict   (REPAIR_RATES overrides for strategy.py)
  - strategy_thresholds    dict   (DEFAULTS overrides for strategy decision logic)
  - financing_params       dict   (DEFAULTS overrides for LTV / closing % / etc)
  - allowed_emails         list[str]   (sign-in allow list)
  - admin_emails           list[str]   (subset who can edit settings)

Reads are cached for 60 seconds (st.cache_data). After a write, the cache is
cleared so subsequent reads see the new value immediately.

For each key, if the DB row is missing or empty, callers fall back to defaults
defined in modules/strategy.py (or in modules/auth.py for emails).
"""
from typing import Any, Optional
import streamlit as st
from modules.supabase_client import get_client, is_configured


@st.cache_data(ttl=60, show_spinner=False)
def get_setting(key: str) -> Any:
    """Read a setting by key. Returns None if not configured or not found."""
    if not is_configured():
        return None
    try:
        c = get_client()
        res = c.table("settings").select("value").eq("key", key).limit(1).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]["value"]
    except Exception:
        return None
    return None


def set_setting(key: str, value: Any, updated_by: Optional[str] = None) -> bool:
    """Upsert a setting. Returns True on success."""
    if not is_configured():
        return False
    try:
        c = get_client()
        row = {
            "key": key,
            "value": value,
            "updated_by": updated_by or "system",
        }
        c.table("settings").upsert(row).execute()
        # Clear cache so next read returns the new value
        get_setting.clear()
        return True
    except Exception:
        return False


def list_settings() -> dict:
    """Fetch all settings as a key -> value dict. Used by the admin export."""
    if not is_configured():
        return {}
    try:
        c = get_client()
        res = c.table("settings").select("key, value, updated_at, updated_by").execute()
        return {r["key"]: r["value"] for r in (res.data or [])}
    except Exception:
        return {}


def is_admin(email: str) -> bool:
    """Check if an email has admin privileges. Falls back to a single
    hardcoded admin if the DB has no admin list yet (bootstrap case)."""
    if not email:
        return False
    admins = get_setting("admin_emails") or ["jo@exoduspropertysolutions.com"]
    return email.lower().strip() in [a.lower().strip() for a in admins]
