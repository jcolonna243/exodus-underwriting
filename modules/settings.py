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
        res = c.table("settings").select("key,value,updated_at,updated_by").execute()
        return {r["key"]: r["value"] for r in (res.data or [])}
    except Exception:
        return {}


def is_admin(email: str) -> bool:
    """Check if an email has admin privileges. Falls back to a single
    hardcoded admin if the DB has no admin list yet (bootstrap case)."""
    if not email:
        return False
    return get_role(email) == "admin"


# ---------------------------------------------------------------------------
# Roles — three-tier access (Admin / Manager / Agent)
# ---------------------------------------------------------------------------
# Roles are stored in settings["user_roles"] as {email: role}.
#   "admin"   → full edit access everywhere; can assign roles
#   "manager" → read-only Admin page + access to Call Reviews page
#   "agent"   → underwrite deals + upload call recordings; no analysis access
#
# Lookup priority (so the system still works after v10 with no user_roles yet):
#   1. settings["user_roles"][email]  → use it
#   2. email in settings["admin_emails"]  → "admin" (backward compat)
#   3. otherwise  → "agent"
# ---------------------------------------------------------------------------
VALID_ROLES = ("admin", "manager", "agent")


def get_role(email: str) -> str:
    """Return the role for a given email. Defaults to 'agent' for any
    logged-in user not explicitly assigned."""
    if not email:
        return "agent"
    em = email.lower().strip()

    # Primary source: user_roles dict
    roles_dict = get_setting("user_roles") or {}
    if isinstance(roles_dict, dict):
        for k, v in roles_dict.items():
            if k.lower().strip() == em and v in VALID_ROLES:
                return v

    # Backward-compat: legacy admin_emails list still grants admin role
    admin_emails = get_setting("admin_emails") or ["jo@exoduspropertysolutions.com"]
    if isinstance(admin_emails, list):
        if em in [a.lower().strip() for a in admin_emails]:
            return "admin"

    return "agent"


def set_role(email: str, role: str, updated_by: Optional[str] = None) -> bool:
    """Assign a role to an email. Admin-only operation; callers must gate it."""
    if not email or role not in VALID_ROLES:
        return False
    em = email.lower().strip()
    roles_dict = get_setting("user_roles") or {}
    if not isinstance(roles_dict, dict):
        roles_dict = {}
    roles_dict[em] = role
    return set_setting("user_roles", roles_dict, updated_by=updated_by)


def remove_role(email: str, updated_by: Optional[str] = None) -> bool:
    """Unassign a role (drops the user back to default 'agent')."""
    em = (email or "").lower().strip()
    if not em:
        return False
    roles_dict = get_setting("user_roles") or {}
    if not isinstance(roles_dict, dict):
        return True
    # Find the key case-insensitively and remove it
    target_keys = [k for k in roles_dict if k.lower().strip() == em]
    for k in target_keys:
        roles_dict.pop(k, None)
    return set_setting("user_roles", roles_dict, updated_by=updated_by)


def list_user_roles() -> dict:
    """Return the current {email: role} mapping. Includes admin_emails too
    (those are auto-promoted to admin for backward compat)."""
    out: dict = {}
    # Start with explicit user_roles
    explicit = get_setting("user_roles") or {}
    if isinstance(explicit, dict):
        out.update({k.lower().strip(): v for k, v in explicit.items()
                    if v in VALID_ROLES})
    # Add legacy admin_emails (only if not already in explicit roles)
    legacy_admins = get_setting("admin_emails") or ["jo@exoduspropertysolutions.com"]
    if isinstance(legacy_admins, list):
        for a in legacy_admins:
            key = a.lower().strip()
            if key not in out:
                out[key] = "admin"
    return out


def is_manager(email: str) -> bool:
    """True if the user has Manager role specifically (NOT admin)."""
    return get_role(email) == "manager"


def can_view_admin(email: str) -> bool:
    """Admin AND Manager can view the Admin page (Manager is read-only)."""
    return get_role(email) in ("admin", "manager")


def can_edit_admin(email: str) -> bool:
    """Only Admin can edit the Admin page."""
    return get_role(email) == "admin"


def can_review_calls(email: str) -> bool:
    """Admin and Manager can review call analyses. Agents cannot see them."""
    return get_role(email) in ("admin", "manager")
