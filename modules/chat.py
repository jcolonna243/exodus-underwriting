"""Anthropic-powered chat about a specific deal.

Each conversation gets the full deal context as a system message so Claude
can reason about the actual numbers without the user re-explaining anything.
Designed to be embedded into Streamlit pages via the helper functions below.
"""
from typing import Dict, Any, List, Generator, Optional
import streamlit as st


# Model choice — Claude Sonnet 4.6 is the sweet spot of quality + cost
# for real-estate decision support. ~$3/M input tokens, $15/M output.
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024  # cap response length per turn
MAX_HISTORY_TURNS = 10  # include last N exchanges in each request


def _client():
    """Construct Anthropic client from Streamlit secrets."""
    import anthropic
    try:
        api_key = st.secrets["anthropic"]["api_key"]
    except Exception:
        api_key = None
    if not api_key:
        raise ValueError(
            "Anthropic API key not configured. Add [anthropic] api_key in "
            "Streamlit Cloud → Settings → Secrets."
        )
    return anthropic.Anthropic(api_key=api_key)


def is_configured() -> bool:
    """Return True if the Anthropic API key is present in secrets."""
    try:
        return bool(st.secrets["anthropic"]["api_key"])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# System prompt construction
# ---------------------------------------------------------------------------
def _fmt_money(x):
    try: return f"${float(x):,.0f}"
    except (TypeError, ValueError): return str(x)

def _fmt_pct(x):
    try: return f"{float(x):.1%}"
    except (TypeError, ValueError): return str(x)


def build_system_prompt(prop: Dict, rec: Dict, seller: Dict,
                        rehab_items: Optional[List] = None) -> str:
    """Build the system message containing the deal context."""
    rehab_lines = ""
    if rehab_items:
        rehab_lines = "\n  Rehab line items:\n" + "\n".join(
            f"    - {label}: {_fmt_money(amt)}" for label, amt in rehab_items
        )

    return f"""You are an experienced real-estate acquisitions consultant helping Exodus Property Solutions analyze a specific deal. The team uses a structured decision tree: wholesale assignment, double close (with fat fee for heavy scope), rehab, novation, short sale, MLS referral, or pass.

THIS DEAL — currently being analyzed:

PROPERTY
  Address:          {prop.get('address', '—')}
  Location:         {prop.get('city', '')}, {prop.get('state', '')} {prop.get('zip', '')}
  Beds / Baths:     {prop.get('beds', '—')} / {prop.get('baths', '—')}
  Living Sqft:      {prop.get('sqft', '—'):,} sf
  Year Built:       {prop.get('year', '—')}
  Pool / HOA:       {prop.get('pool', '—')} / {_fmt_money(prop.get('hoa', 0))}/mo
  Seller's Asking:  {_fmt_money(prop.get('asking', 0))}

KEY NUMBERS
  ARV:                  {_fmt_money(rec.get('arv', 0))}
  Total Rehab:          {_fmt_money(rec.get('rehab_total', 0))}{rehab_lines}
  Cash MAO:             {_fmt_money(rec.get('cash_offer', 0))}
  Wholesale MAO:        {_fmt_money(rec.get('wholesale_offer', 0))}
  Total Project Cost:   {_fmt_money(rec.get('total_project_cost', 0))}
  Projected Net Profit: {_fmt_money(rec.get('net_profit', 0))}
  Projected ROI:        {_fmt_pct(rec.get('roi', 0))}
  Deal Status:          {rec.get('deal_status', '—')}

ASKING-MAO GAP
  Gap:              {_fmt_money(rec.get('gap', 0))}
  Category:         {rec.get('gap_category', '—')}
  Max Novatable:    {_fmt_money(rec.get('novation_max_asking', 0))}

SELLER & LOAN
  1st Mortgage:     {_fmt_money(seller.get('mtg1', 0))}
  2nd / HELOC:      {_fmt_money(seller.get('mtg2', 0))}
  Other Liens:      {_fmt_money(seller.get('other_liens', 0))}
  Equity:           {_fmt_money(rec.get('equity', 0))}
  Payment Status:   {seller.get('payment_status', '—')}
  Required Net:     {_fmt_money(seller.get('required_net', 0))}
  Timeline:         {seller.get('timeline', '—')} days
  Reason for sale:  {seller.get('reason', '—')}
  Occupancy:        {seller.get('occupancy', '—')}

RECOMMENDED STRATEGY: {rec.get('strategy', '—')}
RATIONALE: {rec.get('rationale', '—')}
DISPOSITION: {rec.get('disposition', '—')}

DIAGNOSTICS
  Scope severity:        {rec.get('scope_severity', '—')}
  Profit band:           {rec.get('profit_band', '—')}
  Distress flag:         {'YES' if rec.get('distress_flag') else 'no'}
  Novation feasible:     {'YES' if rec.get('novation_feasible') else 'no'}  (profit: {_fmt_money(rec.get('novation_profit', 0))})
  MLS Referral feasible: {'YES' if rec.get('mls_feasible') else 'no'}  (commission: {_fmt_money(rec.get('mls_commission_estimate', 0))})

EXODUS' DECISION FRAMEWORK (key thresholds):
  • Rehab requires ≥$50k net profit AFTER all costs
  • Wholesale-only band: $30k–$50k profit
  • <$30k profit = NO-GO floor
  • Gap >$70k forces pivot (novation, MLS referral, or pass)
  • Gap $50–70k forces wholesale even if rehab math works at MAO
  • Novation requires rehab ≤$20k, scope not Heavy, profit ≥$10k (preferred ≥$30k)
  • MLS referral requires rehab ≤8% of ARV, commission ≥$8k
  • Fat fee on heavy-scope DC: 25% of profit, floor $15k, ceiling = profit − $50k buyer floor
  • Default assignment fee floor: $2k (will go that low to clear)
  • Novation fee structure: 100% of proceeds above seller's net (with full disclosure)

YOUR ROLE — be direct, practical, and decision-oriented. Use the specific numbers from this deal. When the team asks about alternatives or what-ifs, do the math. Push back if their question implies a strategy that violates the framework. Be conversational, not exhaustive — answer the specific question asked, briefly, then offer to dig deeper if useful."""


# ---------------------------------------------------------------------------
# Sending a message and streaming the response
# ---------------------------------------------------------------------------
def stream_response(system_prompt: str, history: List[Dict],
                    user_message: str) -> Generator[str, None, None]:
    """Stream Claude's response to a user message.

    history: list of {"role": "user"|"assistant", "content": str}
    user_message: the new user message

    Yields content chunks as they arrive.
    """
    client = _client()

    # Trim history to last N turns (keep system fixed)
    trimmed = history[-(MAX_HISTORY_TURNS * 2):]  # 2 messages per turn (user+assistant)

    messages = trimmed + [{"role": "user", "content": user_message}]

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def get_complete_response(system_prompt: str, history: List[Dict],
                          user_message: str) -> str:
    """Non-streaming version — returns the full response as one string.
    Used when we want the response without streaming UI (e.g., batch ops)."""
    client = _client()
    trimmed = history[-(MAX_HISTORY_TURNS * 2):]
    messages = trimmed + [{"role": "user", "content": user_message}]
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Suggested starter prompts — quick-click buttons above the input
# ---------------------------------------------------------------------------
SUGGESTED_PROMPTS = [
    "What's the biggest risk in this deal?",
    "What would change the recommendation?",
    "How should I negotiate with the seller?",
    "What if the seller drops to {asking_minus_10}?",
    "Compare wholesale vs. novation on this deal.",
]


def get_suggested_prompts(prop: Dict) -> List[str]:
    """Return personalized suggested prompts for the current deal."""
    asking = prop.get("asking", 0) or 0
    asking_minus_10 = int(asking * 0.9)
    result = []
    for p in SUGGESTED_PROMPTS:
        if "{asking_minus_10}" in p and asking_minus_10:
            result.append(p.replace("{asking_minus_10}", _fmt_money(asking_minus_10)))
        elif "{asking_minus_10}" not in p:
            result.append(p)
    return result
