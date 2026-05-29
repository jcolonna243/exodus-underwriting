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
RENTCAST_AVM_VALUE_ENDPOINT = f"{RENTCAST_BASE_URL}/avm/value"
TIMEOUT_SECONDS = 12

# Mapping from our UI labels to RentCast's propertyType values
PROPERTY_TYPE_MAP = {
    "Single Family Residence": "Single Family",
    "Condo": "Condo",
    "Townhouse": "Townhouse",
    "Multi-Family (2-4 units)": "Multi-Family",
    "Manufactured / Mobile": "Manufactured",
    "Land": "Land",
}


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
    garage_count = int(features.get("garageSpaces") or features.get("garage") or 0)
    # Property type — normalize to our UI labels
    raw_type = rec.get("propertyType", "")
    prop_type_map_reverse = {v: k for k, v in PROPERTY_TYPE_MAP.items()}
    property_type = prop_type_map_reverse.get(raw_type, "Single Family Residence")

    # HOA fee — RentCast returns "hoa" as a dict like {"fee": 250}
    hoa_dict = rec.get("hoa") or {}
    hoa = int(hoa_dict.get("fee") or 0)

    # Property tax — RentCast returns propertyTaxes as a dict keyed by year
    # like {"2024": {"total": 4500}, "2023": {"total": 4200}}. Take most recent.
    annual_taxes = 0
    tax_data = rec.get("propertyTaxes") or {}
    if isinstance(tax_data, dict) and tax_data:
        latest_year = max(tax_data.keys())
        latest = tax_data.get(latest_year) or {}
        annual_taxes = int(latest.get("total") or 0)

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
        "garage_spaces": garage_count,
        "property_type": property_type,
        "hoa": hoa,
        "annual_taxes": annual_taxes,
        "last_sale_price": rec.get("lastSalePrice"),
        "last_sale_date": rec.get("lastSaleDate"),
        "raw": rec,
    }


# ---------------------------------------------------------------------------
# Comp pulling — RentCast /avm/sale
# ---------------------------------------------------------------------------
def fetch_comps(address: str, property_type: str = "Single Family Residence",
                radius: float = 0.5, days_old: int = 180, comp_count: int = 7,
                beds: Optional[int] = None, baths: Optional[float] = None,
                sqft: Optional[int] = None) -> Dict[str, Any]:
    """Pull comparable sales from RentCast's /avm/sale endpoint.

    Returns:
        dict with keys:
          - found: bool
          - error: str | None
          - subject_avm: float — RentCast's estimated value
          - subject_range: (low, high)
          - comps: List[Dict] — normalized comp records with keys:
              address, city, state, zip, sold_price, sold_date,
              beds, baths, sqft, year, distance, lot_size, pool, garage_spaces,
              property_type, dollar_per_sqft, days_old
    """
    if not address or not address.strip():
        return {"found": False, "error": "Address is empty.", "comps": []}
    if not is_configured():
        return {"found": False, "error": "RentCast API key not configured.", "comps": []}

    # Translate our UI label to RentCast's value
    rc_type = PROPERTY_TYPE_MAP.get(property_type, "Single Family")

    params = {
        "address": address.strip(),
        "propertyType": rc_type,
        "compCount": max(1, min(25, comp_count)),
    }
    if radius and radius > 0:
        params["maxRadius"] = radius
    if days_old and days_old > 0:
        params["daysOld"] = days_old
    if beds is not None and beds > 0:
        params["bedrooms"] = beds
    if baths is not None and baths > 0:
        params["bathrooms"] = baths
    if sqft is not None and sqft > 0:
        params["squareFootage"] = sqft

    try:
        url = f"{RENTCAST_AVM_VALUE_ENDPOINT}?{urllib.parse.urlencode(params)}"
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
        return {"found": False, "error": msg, "comps": []}
    except Exception as e:
        return {"found": False, "error": f"Comp pull failed: {e}", "comps": []}

    # /avm/sale returns: { price, priceRangeLow, priceRangeHigh, comparables: [...] }
    raw_comps = data.get("comparables") or []
    comps = []
    for c in raw_comps:
        features = c.get("features") or {}
        sqft_val = int(c.get("squareFootage") or 0)
        sold_price = float(c.get("price") or 0)
        sold_date = c.get("removedDate") or c.get("lastSeenDate") or c.get("createdDate") or ""
        listing_type = c.get("listingType") or ""
        # Build a notes string flagging distressed/new-construction sales
        notes = ""
        if listing_type and listing_type not in ("Standard", ""):
            notes = f"⚠ {listing_type}"
        psf = (sold_price / sqft_val) if sqft_val > 0 and sold_price > 0 else 0
        comps.append({
            "address": c.get("formattedAddress") or c.get("addressLine1") or "",
            "city": c.get("city") or "",
            "state": c.get("state") or "",
            "zip": c.get("zipCode") or "",
            "sold_price": sold_price,
            "sold_date": sold_date,
            "beds": int(c.get("bedrooms") or 0),
            "baths": float(c.get("bathrooms") or 0),
            "sqft": sqft_val,
            "year": int(c.get("yearBuilt") or 0),
            "distance": float(c.get("distance") or 0),
            "lot_size": int(c.get("lotSize") or 0),
            "pool": bool(features.get("pool")),
            "garage_spaces": int(features.get("garageSpaces") or features.get("garage") or 0),
            "property_type": c.get("propertyType") or "",
            "listing_type": listing_type,
            "dollar_per_sqft": psf,
            "notes": notes,
        })

    return {
        "found": True,
        "error": None,
        "subject_avm": float(data.get("price") or 0),
        "subject_range": (float(data.get("priceRangeLow") or 0),
                          float(data.get("priceRangeHigh") or 0)),
        "comps": comps,
    }
