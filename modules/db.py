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
    """Load all chat messages for a deal, in chronological order.

    Note: PostgREST .order() chain returns PGRST125 against this Supabase
    project. Fetching unordered and sorting in Python until that's resolved.
    """
    c = get_client()
    res = (c.table("chat_messages")
            .select("*")
            .eq("deal_id", deal_id)
            .execute())
    rows = res.data or []
    rows.sort(key=lambda r: r.get("id") or 0)
    return rows


def save_chat_bulk(deal_id: int, messages: List[Dict[str, str]]):
    """Bulk-insert a list of {role, content} messages for a deal."""
    if not messages:
        return
    c = get_client()
    rows = [{"deal_id": deal_id, "role": m["role"], "content": m["content"]}
            for m in messages]
    c.table("chat_messages").insert(rows).execute()


# ---- Deals -------------------------------------------------------------

def _deal_row(inputs: Dict[str, Any], outputs: Dict[str, Any],
              user_email: Optional[str] = None) -> Dict[str, Any]:
    """Shared row construction used by both insert (save_deal) and
    update (update_deal). Keeping it in one place means new columns get
    written consistently no matter which path runs."""
    prop = inputs.get("property", {})
    return {
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
        # JSONB columns — pass dicts, not JSON strings.
        # The full inputs dict now also carries 'comps' + 'rehab' + 'arv_method'
        # so the deal round-trips for editing without re-pulling RentCast.
        "inputs": inputs,
        "outputs": outputs,
    }


def save_deal(inputs: Dict[str, Any], outputs: Dict[str, Any],
              user_email: Optional[str] = None) -> int:
    """Save a NEW deal. Returns the new deal ID. Use update_deal() to
    save changes to an existing deal."""
    c = get_client()
    row = _deal_row(inputs, outputs, user_email)
    res = c.table("deals").insert(row).execute()
    return res.data[0]["id"] if res.data else 0


def update_deal(deal_id: int, inputs: Dict[str, Any], outputs: Dict[str, Any],
                user_email: Optional[str] = None) -> bool:
    """Update an existing deal in place. Returns True if at least one row
    was modified. This is what runs when the user clicks 'Save changes'
    after re-opening a past deal in the editor.

    Note: created_by is overwritten with the current user_email so we can
    see who most-recently edited the deal. If you want immutable creator
    history, swap that field for an updated_by column."""
    c = get_client()
    row = _deal_row(inputs, outputs, user_email)
    res = c.table("deals").update(row).eq("id", deal_id).execute()
    return bool(res.data)


def list_deals(limit: int = 200, search: Optional[str] = None,
               strategy_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List deals — bypasses supabase-py and calls the REST API directly.

    We hit PGRST125 with supabase-py against the deals table even though the
    same client works fine against the settings table. Until that's diagnosed,
    we call the REST API ourselves so we can (a) see the actual HTTP error if
    any, and (b) work around the library issue.
    """
    import json
    import urllib.parse
    import urllib.request
    import urllib.error
    import streamlit as st

    base_url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["service_role_key"]

    # Build query
    params = {"select": "*"}
    if search:
        params["address"] = f"ilike.*{search}*"
    if strategy_filter and strategy_filter != "All":
        params["strategy"] = f"eq.{strategy_filter}"
    full_url = f"{base_url}/rest/v1/deals?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(full_url, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Supabase REST returned HTTP {e.code}.\n"
            f"URL: {full_url}\n"
            f"Body: {body[:800]}"
        )

    if not isinstance(data, list):
        return []
    data.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return data[:limit]


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


# ---- Call analyses (seller-call recordings + AI grading) ----------------
# Schema (v10 baseline, then add v11 columns for coaching notes):
#
#   -- v10 (already run):
#   CREATE TABLE call_analyses (
#       id BIGSERIAL PRIMARY KEY,
#       deal_id BIGINT REFERENCES deals(id) ON DELETE CASCADE,
#       created_at TIMESTAMPTZ DEFAULT NOW(),
#       created_by TEXT,
#       call_type TEXT,
#       audio_filename TEXT,
#       audio_duration_seconds NUMERIC,
#       transcript JSONB,
#       analysis JSONB
#   );
#   CREATE INDEX call_analyses_deal_id_idx ON call_analyses(deal_id);
#   ALTER TABLE call_analyses ENABLE ROW LEVEL SECURITY;
#
#   -- v11 (run once to add coaching-note support):
#   ALTER TABLE call_analyses
#     ADD COLUMN IF NOT EXISTS coaching_note TEXT,
#     ADD COLUMN IF NOT EXISTS coaching_note_by TEXT,
#     ADD COLUMN IF NOT EXISTS coaching_note_at TIMESTAMPTZ;
#
#   -- No policies needed; the app uses service_role which bypasses RLS.

def save_call_analysis(deal_id: Optional[int], call_type: str,
                       audio_filename: str, audio_duration_seconds: float,
                       transcript: Dict[str, Any], analysis: Dict[str, Any],
                       user_email: Optional[str] = None) -> int:
    """Persist a completed call transcription + Claude analysis.

    Args:
        deal_id: ID of the deal this call belongs to, or None for an
            untied call (training/practice analysis with no deal record).
        call_type: "Process Call" / "Offer Call" / "Renegotiation" / etc.
        audio_filename: original uploaded filename for reference
        audio_duration_seconds: from Deepgram metadata
        transcript: full transcribe_audio() result dict
        analysis: full analyze_call() result dict
        user_email: who ran the analysis

    Returns:
        The new row's ID, or 0 if the insert returned no data.
    """
    c = get_client()
    row = {
        "deal_id": deal_id,
        "created_by": user_email or "unknown",
        "call_type": call_type,
        "audio_filename": audio_filename,
        "audio_duration_seconds": float(audio_duration_seconds or 0),
        "transcript": transcript,
        "analysis": analysis,
    }
    res = c.table("call_analyses").insert(row).execute()
    return res.data[0]["id"] if res.data else 0


def load_call_analyses_for_deal(deal_id: int) -> List[Dict[str, Any]]:
    """Return all call analyses for a given deal, oldest-first."""
    c = get_client()
    res = (c.table("call_analyses")
            .select("*")
            .eq("deal_id", deal_id)
            .execute())
    rows = res.data or []
    rows.sort(key=lambda r: r.get("id") or 0)
    return rows


def delete_call_analysis(analysis_id: int) -> bool:
    """Delete a single call analysis row."""
    c = get_client()
    res = c.table("call_analyses").delete().eq("id", analysis_id).execute()
    return bool(res.data)


def list_all_call_analyses(limit: int = 500) -> List[Dict[str, Any]]:
    """List every call analysis across all deals — for the Call Reviews page.

    Fetches call_analyses and deals separately and joins in Python. We used
    to use PostgREST's embed syntax (`select=*,deals(...)`) but that relies
    on the FK schema cache being current; a stale cache returns empty rows
    silently. The two-query pattern is more reliable and easier to debug.

    Returns rows with a `deals` key shaped like the embed used to be:
        {"deals": {"address": ..., "city": ..., "state": ..., "strategy": ...}}
    so callers don't need to change.
    """
    import json
    import urllib.parse
    import urllib.request
    import urllib.error
    import streamlit as st

    base_url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["service_role_key"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    # 1. Fetch call_analyses (without embed)
    ca_url = (f"{base_url}/rest/v1/call_analyses"
              f"?select=*&limit={int(limit)}")
    try:
        with urllib.request.urlopen(urllib.request.Request(ca_url, headers=headers),
                                     timeout=12) as resp:
            calls = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Supabase REST returned HTTP {e.code} fetching call_analyses.\n"
            f"URL: {ca_url}\nBody: {body[:800]}"
        )
    if not isinstance(calls, list):
        return []

    # 2. Fetch all referenced deal rows in a single query (uses `in.(...)`)
    deal_ids = sorted({r["deal_id"] for r in calls
                       if r.get("deal_id") is not None})
    deals_by_id: Dict[int, Dict[str, Any]] = {}
    if deal_ids:
        ids_csv = ",".join(str(d) for d in deal_ids)
        deals_url = (f"{base_url}/rest/v1/deals"
                     f"?select=id,address,city,state,strategy"
                     f"&id=in.({ids_csv})")
        try:
            with urllib.request.urlopen(urllib.request.Request(deals_url, headers=headers),
                                         timeout=12) as resp:
                deal_rows = json.loads(resp.read().decode("utf-8"))
            if isinstance(deal_rows, list):
                for d in deal_rows:
                    deals_by_id[d["id"]] = d
        except urllib.error.HTTPError:
            # Don't fail the whole page if the deals fetch fails — the
            # call list is still useful with empty deal slots.
            pass

    # 3. Stitch them together so the UI doesn't need to change
    for r in calls:
        did = r.get("deal_id")
        if did and did in deals_by_id:
            r["deals"] = {
                "address": deals_by_id[did].get("address"),
                "city": deals_by_id[did].get("city"),
                "state": deals_by_id[did].get("state"),
                "strategy": deals_by_id[did].get("strategy"),
            }
        else:
            r["deals"] = {}

    calls.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return calls


def get_call_analysis(analysis_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single call analysis by ID, including its deal context.

    Uses the same two-query pattern as list_all_call_analyses() to avoid
    relying on PostgREST's FK schema cache.
    """
    import json
    import urllib.request
    import urllib.error
    import streamlit as st

    base_url = st.secrets["supabase"]["url"].rstrip("/")
    key = st.secrets["supabase"]["service_role_key"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    # 1. Fetch the analysis row itself
    ca_url = (f"{base_url}/rest/v1/call_analyses"
              f"?select=*&id=eq.{int(analysis_id)}&limit=1")
    try:
        with urllib.request.urlopen(urllib.request.Request(ca_url, headers=headers),
                                     timeout=12) as resp:
            calls = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(calls, list) or not calls:
        return None
    row = calls[0]

    # 2. Fetch the deal row (best-effort)
    did = row.get("deal_id")
    if did is not None:
        deal_url = (f"{base_url}/rest/v1/deals"
                    f"?select=id,address,city,state,strategy,inputs,outputs"
                    f"&id=eq.{int(did)}&limit=1")
        try:
            with urllib.request.urlopen(urllib.request.Request(deal_url, headers=headers),
                                         timeout=12) as resp:
                deals = json.loads(resp.read().decode("utf-8"))
            if isinstance(deals, list) and deals:
                d = deals[0]
                row["deals"] = {
                    "address": d.get("address"),
                    "city": d.get("city"),
                    "state": d.get("state"),
                    "strategy": d.get("strategy"),
                    "inputs": d.get("inputs"),
                    "outputs": d.get("outputs"),
                }
            else:
                row["deals"] = {}
        except Exception:
            row["deals"] = {}
    else:
        row["deals"] = {}

    return row


def save_coaching_note(analysis_id: int, note: str, author_email: str) -> bool:
    """Persist a coaching note written by a Manager against a call analysis."""
    c = get_client()
    res = c.table("call_analyses").update({
        "coaching_note": note,
        "coaching_note_by": author_email,
        # coaching_note_at is set client-side because supabase-py upserts
        # don't auto-stamp on update; PostgREST sends the literal value.
        "coaching_note_at": _now_iso(),
    }).eq("id", analysis_id).execute()
    return bool(res.data)


def _now_iso() -> str:
    """Current UTC timestamp in ISO 8601, suitable for TIMESTAMPTZ writes."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
