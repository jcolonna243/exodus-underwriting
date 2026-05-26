"""Comp import — parse MLS exports (CSV/Excel) into structured comp records.

This is a port/adaptation of the standalone comp_import.py script for use
in the Streamlit app. It reads from a file-like object (so it works with
Streamlit's file_uploader directly) and returns a list of comp dicts.
"""
import re, csv, io
from typing import Optional, List, Dict, Any, BinaryIO


COLUMN_PATTERNS = {
    "address":   ["property address", "full address", "site address", "street address", "address"],
    "city":      ["city", "municipality"],
    "state":     ["state"],
    "zip":       ["zip code", "zipcode", "postal code", "postal", "zip"],
    "beds":      ["bedrooms", "bed rooms", "beds", "br", "# beds"],
    "baths":     ["total baths", "bathrooms", "baths", "ba", "# baths", "full baths"],
    "sqft":      ["total living area", "living area sqft", "living sqft", "living area",
                  "sq ft", "sqft", "square feet", "approx sqft"],
    "year":      ["year built", "yr built", "year"],
    "sold_price":["close price", "closed price", "sold price", "sale price", "sold for", "price"],
    "sold_date": ["close date", "closed date", "sold date", "sale date", "closing date"],
    "distance":  ["approximate distance", "distance", "miles", "mi"],
    "notes":     ["public remarks", "remarks", "notes", "comments"],
}


def _normalize(s):
    if s is None: return ""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _find_column(header, patterns):
    norm = [_normalize(h) for h in header]
    for p in patterns:
        pn = _normalize(p)
        for i, h in enumerate(norm):
            if h == pn: return i
    for p in patterns:
        pn = _normalize(p)
        for i, h in enumerate(norm):
            if pn and (pn in h or (len(h) >= 3 and h in pn)):
                return i
    return None


def _parse_money(v):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try: return float(s) if s else None
    except ValueError: return None


def _parse_int(v):
    n = _parse_money(v)
    return int(n) if n is not None else None


def _read_csv_bytes(data: bytes) -> List[List]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _read_excel_bytes(data: bytes) -> List[List]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), data_only=True)
    ws = wb.active
    return [list(row) for row in ws.iter_rows(values_only=True)]


def parse_comp_file(file_obj_or_bytes, filename: str = "") -> List[Dict[str, Any]]:
    """Parse a comp file into a list of comp dicts.

    file_obj_or_bytes: a Streamlit UploadedFile, bytes, or file-like object
    filename: used to detect file type if extension hint isn't obvious

    Returns list of dicts with keys: address, city, state, zip, beds, baths,
    sqft, year, sold_price, sold_date, distance, notes, dollar_per_sqft
    """
    # Get bytes
    if hasattr(file_obj_or_bytes, "read"):
        data = file_obj_or_bytes.read()
        if hasattr(file_obj_or_bytes, "name"):
            filename = filename or file_obj_or_bytes.name
    elif isinstance(file_obj_or_bytes, bytes):
        data = file_obj_or_bytes
    else:
        raise ValueError("Pass a file-like object or bytes.")

    # Detect format from extension or content
    lower_name = filename.lower()
    is_excel = (lower_name.endswith(".xlsx") or lower_name.endswith(".xls")
                or lower_name.endswith(".xlsm"))
    if not lower_name and data[:4] == b'PK\x03\x04':
        is_excel = True  # zip signature → assume xlsx

    rows = _read_excel_bytes(data) if is_excel else _read_csv_bytes(data)

    # Find header row
    header_idx = None
    for i, row in enumerate(rows[:10]):
        non_empty = sum(1 for v in row if v not in (None, ""))
        if non_empty < 4: continue
        addr_idx = _find_column(row, COLUMN_PATTERNS["address"])
        price_idx = _find_column(row, COLUMN_PATTERNS["sold_price"])
        if addr_idx is not None and price_idx is not None:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find a header row with Address and Price columns.")

    header = rows[header_idx]
    cols = {f: _find_column(header, ps) for f, ps in COLUMN_PATTERNS.items()}

    comps = []
    for row in rows[header_idx + 1:]:
        if all(v in (None, "") for v in row): continue

        def g(field, parser=None):
            i = cols.get(field)
            if i is None or i >= len(row): return None
            v = row[i]
            if parser: return parser(v)
            return v if v not in ("", None) else None

        comp = {
            "address": g("address"), "city": g("city"), "state": g("state"),
            "zip": g("zip"), "beds": g("beds", _parse_int),
            "baths": g("baths"), "sqft": g("sqft", _parse_int),
            "year": g("year", _parse_int), "sold_price": g("sold_price", _parse_money),
            "sold_date": str(g("sold_date") or ""),
            "distance": g("distance"), "notes": g("notes"),
        }
        if not comp["address"] or comp["sold_price"] is None: continue
        comp["dollar_per_sqft"] = (comp["sold_price"] / comp["sqft"]) if comp["sqft"] else None
        # Convert sold_date if it's a datetime
        comps.append(comp)
    return comps


def suggested_arv(comps: List[Dict[str, Any]], subject_sqft: Optional[int] = None) -> Dict[str, float]:
    """Compute the 4 ARV estimates + the suggested ARV (average of non-zero methods)."""
    selected = [c for c in comps if c.get("sold_price")]
    if not selected:
        return {
            "avg_sale": 0, "median_sale": 0,
            "avg_psf_times_sqft": 0, "median_psf_times_sqft": 0,
            "suggested": 0,
        }
    prices = [c["sold_price"] for c in selected]
    psf = [c["dollar_per_sqft"] for c in selected if c.get("dollar_per_sqft")]

    avg_sale = sum(prices) / len(prices)
    sorted_p = sorted(prices)
    median_sale = (sorted_p[len(sorted_p)//2] if len(sorted_p) % 2
                   else (sorted_p[len(sorted_p)//2 - 1] + sorted_p[len(sorted_p)//2]) / 2)

    if psf and subject_sqft:
        avg_psf = sum(psf) / len(psf)
        sorted_psf = sorted(psf)
        median_psf = (sorted_psf[len(sorted_psf)//2] if len(sorted_psf) % 2
                      else (sorted_psf[len(sorted_psf)//2 - 1] + sorted_psf[len(sorted_psf)//2]) / 2)
        avg_psf_times_sqft = avg_psf * subject_sqft
        median_psf_times_sqft = median_psf * subject_sqft
    else:
        avg_psf_times_sqft = 0
        median_psf_times_sqft = 0

    methods = [v for v in (avg_sale, median_sale, avg_psf_times_sqft, median_psf_times_sqft) if v > 0]
    suggested = sum(methods) / len(methods) if methods else 0

    return {
        "avg_sale": avg_sale, "median_sale": median_sale,
        "avg_psf_times_sqft": avg_psf_times_sqft,
        "median_psf_times_sqft": median_psf_times_sqft,
        "suggested": suggested,
    }
