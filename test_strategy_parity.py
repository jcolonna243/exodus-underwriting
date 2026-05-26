"""Parity test: Python strategy module vs. Excel v3 tool.

Runs the same 6 scenarios that pass in the Excel tool's verify_scenarios.py
and confirms the Python port produces matching strategy outputs.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from modules.strategy import compute_recommendation

# Same scenario definitions as the Excel tool's verify_scenarios.py
SCENARIOS = {
    "wholesale_assignment": {
        "expected": "Wholesale — Assignment",
        "property": {
            "address": "8420 SW 152nd St", "city": "Miami", "state": "FL", "zip": 33157,
            "beds": 3, "baths": 2, "sqft": 1500, "year": 1980,
            "pool": "No", "hoa": 0, "asking": 280000,
        },
        "arv": 455000,
        "rehab": {
            "roof": {"include": True, "type": "Shingle"},
            "kitchen": {"include": True},
            "bathrooms": {"include": True},
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 180000, "payment_status": "Current", "required_net": 280000,
            "timeline": 30, "reason": "Relocation", "occupancy": "Owner",
            "condition_confirmed": "Yes", "buyer_demand": "No",
            "assignable": "Yes", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
    "wholesale_dc": {
        "expected": "Wholesale — Double Close",
        "property": {
            "address": "14250 SW 92nd Ct", "city": "Miami", "state": "FL", "zip": 33176,
            "beds": 3, "baths": 2, "sqft": 1700, "year": 1975,
            "pool": "Yes", "hoa": 0, "asking": 290000,
        },
        "arv": 480000,
        "rehab": {
            "roof": {"include": True, "type": "Shingle"},
            "kitchen": {"include": True},
            "bathrooms": {"include": True},
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 0, "payment_status": "Current", "required_net": 290000,
            "timeline": 45, "reason": "Probate", "occupancy": "Vacant",
            "condition_confirmed": "Yes", "buyer_demand": "Yes",
            "assignable": "No", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
    "rehab": {
        "expected": "Rehab",
        "property": {
            "address": "7350 SW 132nd St", "city": "Pinecrest", "state": "FL", "zip": 33156,
            "beds": 4, "baths": 3, "sqft": 2000, "year": 1985,
            "pool": "Yes", "hoa": 0, "asking": 440000,
        },
        "arv": 700000,
        "rehab": {
            "roof": {"include": True, "type": "Shingle"},
            "kitchen": {"include": True},
            "ac": {"include": True},
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 200000, "payment_status": "Current", "required_net": 440000,
            "timeline": 60, "reason": "Divorce", "occupancy": "Owner",
            "condition_confirmed": "Yes", "buyer_demand": "No",
            "assignable": "Yes", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
    "mls_referral": {
        "expected": "MLS Referral",
        "property": {
            "address": "9080 SW 142nd Ave", "city": "Miami", "state": "FL", "zip": 33186,
            "beds": 3, "baths": 2, "sqft": 1700, "year": 1995,
            "pool": "No", "hoa": 0, "asking": 495000,
        },
        "arv": 510000,
        "rehab": {
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 50000, "payment_status": "Current", "required_net": 495000,
            "timeline": 90, "reason": "Tired landlord", "occupancy": "Vacant",
            "condition_confirmed": "Yes", "buyer_demand": "No",
            "assignable": "Yes", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
    "short_sale": {
        "expected": "Short Sale → Wholesale (Double Close)",
        "property": {
            "address": "13420 SW 80th Ter", "city": "Miami", "state": "FL", "zip": 33183,
            "beds": 3, "baths": 2, "sqft": 1600, "year": 1978,
            "pool": "No", "hoa": 0, "asking": 400000,
        },
        "arv": 450000,
        "rehab": {
            "roof": {"include": True, "type": "Shingle"},
            "kitchen": {"include": True},
            "bathrooms": {"include": True},
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 465000, "other_liens": 8000, "payment_status": "NOD",
            "required_net": 0, "timeline": 60, "reason": "Financial distress",
            "occupancy": "Owner", "condition_confirmed": "Yes", "buyer_demand": "No",
            "assignable": "Yes", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
    "novation": {
        "expected": "Novation",
        "property": {
            "address": "6840 SW 102nd St", "city": "Miami", "state": "FL", "zip": 33156,
            "beds": 3, "baths": 2, "sqft": 1500, "year": 1990,
            "pool": "No", "hoa": 0, "asking": 430000,
        },
        "arv": 545000,
        "rehab": {
            "interior_paint": {"include": True, "type": "Knockdown + Paint"},
            "flooring": {"include": True},
        },
        "seller": {
            "mtg1": 80000, "payment_status": "Current", "required_net": 430000,
            "timeline": 90, "reason": "Other", "occupancy": "Owner",
            "condition_confirmed": "Yes", "buyer_demand": "No",
            "assignable": "Yes", "buyer_prefers_dc": "No", "open_to_mls": "Yes",
        },
    },
}

if __name__ == "__main__":
    all_pass = True
    for name, scn in SCENARIOS.items():
        expected = scn.pop("expected")
        result = compute_recommendation(scn)
        actual = result["strategy"]
        match = expected in actual
        flag = "✓" if match else "✗"
        if not match: all_pass = False
        print(f"\n{flag} {name.upper()}")
        print(f"  Expected: {expected}")
        print(f"  Actual:   {actual}")
        print(f"  Rehab:    ${result['rehab_total']:,.0f}")
        print(f"  Profit:   ${result['net_profit']:,.0f}")
        print(f"  Cash MAO: ${result['cash_offer']:,.0f}")
    print(f"\n{'ALL PASS' if all_pass else 'FAILURES PRESENT'}")
