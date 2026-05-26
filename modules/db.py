"""Deal history persistence — SQLite by default.

NOTE for deployment: Streamlit Cloud's free tier filesystem is NOT persistent
across deployments. For production, swap this for Supabase, Turso, or another
hosted DB. The API surface here is intentionally small so the swap is easy.
"""
import sqlite3, json, os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.environ.get("EXODUS_DB_PATH", "data/deals.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                created_by TEXT,
                address TEXT NOT NULL,
                city TEXT,
                state TEXT,
                zip TEXT,
                strategy TEXT NOT NULL,
                arv REAL,
                asking REAL,
                cash_offer REAL,
                wholesale_offer REAL,
                net_profit REAL,
                inputs_json TEXT NOT NULL,
                outputs_json TEXT NOT NULL
            )
        """)


def save_deal(inputs: Dict[str, Any], outputs: Dict[str, Any],
              user_email: Optional[str] = None) -> int:
    init_db()
    prop = inputs.get("property", {})
    with _conn() as c:
        cur = c.execute("""
            INSERT INTO deals (
                created_at, created_by, address, city, state, zip,
                strategy, arv, asking, cash_offer, wholesale_offer,
                net_profit, inputs_json, outputs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            user_email or "unknown",
            prop.get("address", "(no address)"),
            prop.get("city", ""), prop.get("state", ""), str(prop.get("zip", "")),
            outputs.get("strategy", ""),
            outputs.get("arv", 0), prop.get("asking", 0),
            outputs.get("cash_offer", 0), outputs.get("wholesale_offer", 0),
            outputs.get("net_profit", 0),
            json.dumps(inputs, default=str), json.dumps(outputs, default=str),
        ))
        return cur.lastrowid


def list_deals(limit: int = 200, search: Optional[str] = None,
               strategy_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM deals WHERE 1=1"
    params = []
    if search:
        sql += " AND address LIKE ?"
        params.append(f"%{search}%")
    if strategy_filter and strategy_filter != "All":
        sql += " AND strategy = ?"
        params.append(strategy_filter)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def get_deal(deal_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
        if not row: return None
        d = dict(row)
        d["inputs"] = json.loads(d["inputs_json"])
        d["outputs"] = json.loads(d["outputs_json"])
        return d


def delete_deal(deal_id: int) -> bool:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
        return c.total_changes > 0


def distinct_strategies() -> List[str]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT DISTINCT strategy FROM deals ORDER BY strategy").fetchall()
        return [r["strategy"] for r in rows]
