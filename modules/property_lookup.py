"""RentCast property lookup — feeds the New Deal page's auto-fill button.

Requires st.secrets["rentcast"]["api_key"] to be configured. If not configured,
is_configured() returns False and the UI hides the lookup button gracefully.

API docs: https://developers.rentcast.io/reference/property-data
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any
import streamlit as st


RENTCAST_BASE_URL = "https://api.rentcast.io/v1"
RENTCAST_PROPERTIES_ENDPOINT = f"{RENTCAST_BASE_URL}/properties"
TIMEOUT_SECONDS = 12


def is_configured() -> bool:
    """True if a RentCast API key is set in Streamlit Secrets."""
    try:
        return bool(st.secrets["rentcast"]["api_key"])
    except Exception:
        return False


def _api_key() -> str:
    return st.secrets["rentcast"]["api_key"]


def lookup_property(address: str) -> Dict[str, Any]:
    """Look up a property by address. Returns a normalized dict.

    Args:
        address: A US property address. Best results with the full street + city
            + state + zip, but RentCast accepts partial addresses too.

    Returns:
        A dict with these keys:
          - found: bool        — True if RentCast returned at least one match
          - error: str | None  — non-None if the API call failed
          - address: str       — formatted street + unit
          - city: str
          - state: str
          - zip: str
          - beds: int
          - baths: float
          - sqft: int
          - year: int          — year built
          - lot_size: int      — lot size in sqft
          - pool: str          — "Yes" / "No" (best-effort from features)
          - hoa: int           — monthly HOA in dollars (best-effort)
          - last_sale_price: int | None
          - last_sale_date: str | None
          - raw: dict          — full RentCast response for debugging
    """
    if not address or not address.strip():
        return {"found": False, "error": "Address is empty."}

    if not is_configured():
        return {"found": False, "error": "RentCast API key not configured."}

    try:
        query = urllib.parse.urlencode({"address": address.strip()})
        url = f"{RENTCAST_PROPERTIES_ENDPOINT}?{query}"
        req = urllib.request.Request(url, headers={
            "X-Api-Key": _api_key(),
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = f"RentCast API error {e.code}"
        try:
            body = e.read().decode("utf-8")
            msg += f": {body[:200]}"
        except Exception:
            pass
        return {"found": False, "error": msg}
    except Exception as e:
        return {"found": False, "error": f"Lookup failed: {e}"}

    # RentCast returns a list of matches; take the first.
    if not isinstance(data, list) or len(data) == 0:
        return {"found": False, "error": None, "raw": data}

    rec = data[0]

    # Best-effort feature detection
    features = rec.get("features") or {}
    pool = "Yes" if features.get("pool") else "No"

    # HOA fee — RentCast returns "hoa" as a dict like {"fee": 250}
    hoa_dict = rec.get("hoa") or {}
    hoa = int(hoa_dict.get("fee") or 0)

    return {
        "found": True,
        "error": None,
        "address": rec.get("addressLine1") or "",
        "city": rec.get("city") or "",
        "state": rec.get("state") or "",
        "zip": rec.get("zipCode") or "",
        "beds": int(rec.get("bedrooms") or 0),
        "baths": float(rec.get("bathrooms") or 0),
        "sqft": int(rec.get("squareFootage") or 0),
        "year": int(rec.get("yearBuilt") or 0),
        "lot_size": int(rec.get("lotSize") or 0),
        "pool": pool,
        "hoa": hoa,
        "last_sale_price": rec.get("lastSalePrice"),
        "last_sale_date": rec.get("lastSaleDate"),
        "raw": rec,
    }
