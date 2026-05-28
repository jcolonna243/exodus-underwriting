"""Deal history persistence — Supabase backend.

Replaces the previous SQLite implementation. Settings, deals, and chat
messages all live in a Supabase Postgres database so they survive Streamlit
Cloud redeploys.

Tables (created via SQL in Supabase dashboard):
  - settings        (key TEXT PK, value JSONB, updated_at, updated_by)
  - deals           (mirrors prior SQLite columns; inputs/outputs are JSONB)
  - chat_messages   (id, deal_id FK, created_at, role, content)

Public API kept identical to the previous SQLite version so callers don't
need to change.
"""
from typing import List, Dict, Any, Optional
from modules.supabase_client import get_client


def init_db():
    """No-op for Supabase. Tables are pre-created via the SQL editor."""
    pass


# ---- Chat messages -----------------------------------------------------

def save_chat_message(deal_id: int, role: str, content: str) -> int:
    """Save a single chat message. Returns its new row ID."""
    c = get_client()
    res = c.table("chat_messages").insert({
        "deal_id": deal_id,
        "role": role,
        "content": content,
    }).execute()
    return res.data[0]["id"] if res.data else 0


def load_chat_messages(deal_id: int) -> List[Dict[str, Any]]:
    """Load all chat messages for a deal, in chronological order."""
    c = get_client()
    res = (c.table("chat_messages")
            .select("id,created_at,role,content")
            .eq("deal_id", deal_id)
            .order("id", desc=False)
            .execute())
    return res.data or []


def save_chat_bulk(deal_id: int, messages: List[Dict[str, str]]):
    """Bulk-insert a list of {role, content} messages for a deal."""
    if not messages:
        return
    c = get_client()
    rows = [{"deal_id": deal_id, "role": m["role"], "content": m["content"]}
            for m in messages]
    c.table("chat_messages").insert(rows).execute()


# ---- Deals -------------------------------------------------------------

def save_deal(inputs: Dict[str, Any], outputs: Dict[str, Any],
              user_email: Optional[str] = None) -> int:
    """Save a new deal. Returns the new deal ID."""
    c = get_client()
    prop = inputs.get("property", {})
    row = {
        "created_by": user_email or "unknown",
        "address": prop.get("address", "(no address)"),
        "city": prop.get("city", ""),
        "state": prop.get("state", ""),
        "zip": str(prop.get("zip", "")),
        "strategy": outputs.get("strategy", ""),
        "arv": float(outputs.get("arv", 0) or 0),
        "asking": float(prop.get("asking", 0) or 0),
        "cash_offer": float(outputs.get("cash_offer", 0) or 0),
        "wholesale_offer": float(outputs.get("wholesale_offer", 0) or 0),
        "net_profit": float(outputs.get("net_profit", 0) or 0),
        # JSONB columns — pass dicts, not JSON strings
        "inputs": inputs,
        "outputs": outputs,
    }
    res = c.table("deals").insert(row).execute()
    return res.data[0]["id"] if res.data else 0


def list_deals(limit: int = 200, search: Optional[str] = None,
               strategy_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List deals (newest first). Supports address search + strategy filter."""
    c = get_client()
    q = c.table("deals").select(
        "id,created_at,created_by,address,city,state,zip,strategy,"
        "arv,asking,cash_offer,wholesale_offer,net_profit"
    )
    if search:
        q = q.ilike("address", f"%{search}%")
    if strategy_filter and strategy_filter != "All":
        q = q.eq("strategy", strategy_filter)
    res = q.order("id", desc=True).limit(limit).execute()
    return res.data or []


def get_deal(deal_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single deal by ID. Returns dict with inputs/outputs as dicts
    (Supabase deserializes JSONB columns automatically)."""
    c = get_client()
    res = c.table("deals").select("*").eq("id", deal_id).limit(1).execute()
    if not res.data:
        return None
    return res.data[0]


def delete_deal(deal_id: int) -> bool:
    """Delete a deal and its chat messages (cascade via FK)."""
    c = get_client()
    res = c.table("deals").delete().eq("id", deal_id).execute()
    return bool(res.data)


def distinct_strategies() -> List[str]:
    """Return list of unique strategies in use. Supabase has no DISTINCT in
    its REST API, so we fetch the column and dedupe in Python."""
    c = get_client()
    res = c.table("deals").select("strategy").execute()
    strategies = sorted({r["strategy"] for r in (res.data or [])
                         if r.get("strategy")})
    return strategies
