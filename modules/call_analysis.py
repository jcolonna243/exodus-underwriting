"""Claude-powered seller call analysis.

Given a transcribed seller call (with speaker labels) and the deal's context,
grade the call against the bundled sales methodology doc and return a
structured analysis:

  - UMBC scorecard (Urgency / Motivation / Ballpark / Condition)
  - Pain points the seller revealed (mapped to the 10 motivation categories)
  - What the rep did well (with quotes)
  - What the rep missed (with the exact play that would have fit)
  - Tactical recommendations for the next call
  - Objection handling — what came up, what framework was used
  - Tonality assessment
  - Estimated likelihood of getting to contract

The methodology doc (sales_methodology.md, bundled in this project) is the
ground-truth system prompt. Update it to change how every call gets graded.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import streamlit as st


MODEL = "claude-sonnet-4-6"  # match modules/chat.py
ANALYSIS_MAX_TOKENS = 8000   # analysis is long-form; give it room

METHODOLOGY_FILENAME = "sales_methodology.md"


# ---------------------------------------------------------------------------
# Methodology loader — read the bundled doc once per process
# ---------------------------------------------------------------------------
_methodology_cache: Optional[str] = None


def _project_root() -> Path:
    """Path to the exodus_web directory, regardless of where Streamlit runs."""
    # modules/call_analysis.py → exodus_web/
    return Path(__file__).resolve().parent.parent


def load_methodology() -> str:
    """Read the bundled methodology doc. Cached per process."""
    global _methodology_cache
    if _methodology_cache is not None:
        return _methodology_cache
    path = _project_root() / METHODOLOGY_FILENAME
    try:
        with open(path, "r", encoding="utf-8") as f:
            _methodology_cache = f.read()
    except FileNotFoundError:
        # Fall back to a minimal placeholder so the app doesn't crash if the
        # doc gets accidentally deleted from the deploy.
        _methodology_cache = (
            "# Exodus Sales Methodology\n\n"
            "Methodology document not found at expected path. "
            f"Expected: {path}\n\n"
            "Grade the call using standard real-estate acquisitions best "
            "practices (motivation discovery, property condition, roadblocks, "
            "objection handling) and return structured JSON."
        )
    return _methodology_cache


def is_configured() -> bool:
    """Anthropic API key required for analysis (same key as chat)."""
    try:
        return bool(st.secrets["anthropic"]["api_key"])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Anthropic client — reused from chat module pattern
# ---------------------------------------------------------------------------
def _client():
    import anthropic
    try:
        api_key = st.secrets["anthropic"]["api_key"]
    except Exception:
        api_key = None
    if not api_key:
        raise ValueError("Anthropic API key not configured.")
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Build the analysis prompt
# ---------------------------------------------------------------------------
def _fmt_money(x):
    try: return f"${float(x):,.0f}"
    except (TypeError, ValueError): return str(x)


def build_system_prompt() -> str:
    """The full methodology doc + grading instructions, returned as the
    system prompt for the analysis call."""
    methodology = load_methodology()
    return f"""{methodology}

---

# YOUR TASK — Grade This Specific Call

You will receive:
  1. A transcribed seller call with speaker labels (Rep / Seller)
  2. The deal context (property, asking price, ARV, recommended strategy)
  3. The call type (Process Call, Offer Call, Renegotiation, etc.)

Apply the methodology above. Be specific. Quote the call. Identify what the
rep did well AND what they missed. Recommend specific tactics for the next
call based on what the seller actually said (use their words back to them).

RETURN A SINGLE JSON OBJECT — no preamble, no markdown around it, just the
JSON. The schema is:

{{
  "summary_one_line": "string — your one-sentence verdict on how this call went",
  "overall_grade": "A" | "B" | "C" | "D" | "F",
  "contract_likelihood_pct": integer 0-100,

  "process_call_checklist": {{
    "applicable": true | false,
    "applicable_note": "string — if not applicable, briefly why (e.g. 'This was an Offer Call, not a Process Call')",
    "intro": {{ "covered": true | false, "feedback": "string — short, specific" }},
    "set_expectations_open": {{
      "time": {{ "covered": true | false, "feedback": "string" }},
      "agenda": {{ "covered": true | false, "feedback": "string" }},
      "result": {{ "covered": true | false, "feedback": "string" }},
      "permission_to_say_no": {{ "covered": true | false, "feedback": "string" }},
      "urgency": {{ "covered": true | false, "feedback": "string" }}
    }},
    "motivation": {{
      "situation": {{ "covered": true | false, "feedback": "string — did rep ask 'What's got you thinking about selling?'" }},
      "impact_1": {{ "covered": true | false, "feedback": "string — first Impact question used" }},
      "impact_2": {{ "covered": true | false, "feedback": "string — second Impact question used" }},
      "impact_3": {{ "covered": true | false, "feedback": "string — third Impact question used" }},
      "perfect_picture": {{ "covered": true | false, "feedback": "string — did rep ask the Picture Perfect/Goal question?" }}
    }},
    "property_condition": {{ "covered": true | false, "feedback": "string — number of 16 items covered and any pushback handling" }},
    "road_blocks": {{
      "time": {{ "covered": true | false, "feedback": "string — Timeline asked verbatim with 21-24 day anchor?" }},
      "influencers": {{ "covered": true | false, "feedback": "string — and if any, were they double/triple-confirmed?" }},
      "discomfort": {{ "covered": true | false, "feedback": "string — 'What's next thing you have to figure out?'" }},
      "money": {{ "covered": true | false, "feedback": "string — feel/fair language used? Common-enemy framing?" }}
    }},
    "set_expectations_close": {{
      "time": {{ "covered": true | false, "feedback": "string — 20-30 min to talk to partners, then 5 min callback?" }},
      "agenda": {{ "covered": true | false, "feedback": "string" }},
      "result": {{ "covered": true | false, "feedback": "string — confident yes/no expected" }},
      "permission_to_say_no": {{ "covered": true | false, "feedback": "string — 'no is perfectly okay'" }}
    }},
    "notes": "string — short paragraph synthesizing the checklist into 1-2 actionable takeaways"
  }},

  "umbc": {{
    "urgency": {{
      "score": "Strong" | "Adequate" | "Weak" | "Missed",
      "evidence": "string — quote from the call (or 'not asked')",
      "note": "string — your assessment in one sentence"
    }},
    "motivation": {{
      "score": "Strong" | "Adequate" | "Weak" | "Missed",
      "categories_found": ["array of categories from the 10: Financial, Emotional, Relationship, Physical, Mental, Time, Goal-Blocking, Extreme Pleasure, No Motivation, Toward-Pleasure-Only"],
      "evidence": "string — best quote that surfaced the motivation",
      "note": "string — did rep ask Impact + Socratic questions for each category?"
    }},
    "ballpark": {{
      "score": "Strong" | "Adequate" | "Weak" | "Missed",
      "seller_quoted_price": "string — the number they said, or 'not asked'",
      "note": "string — how the rep asked it (used 'feel/fair'? skipped 'ballpark'?)"
    }},
    "condition": {{
      "score": "Strong" | "Adequate" | "Weak" | "Missed",
      "items_covered_count": integer 0-16,
      "note": "string — quality of property condition questioning"
    }}
  }},

  "pain_points_identified": [
    {{
      "category": "one of the 10 motivation categories",
      "seller_quote": "string — what the seller actually said that revealed it",
      "rep_followed_up": true | false,
      "rep_response_quality": "Strong" | "Adequate" | "Weak" | "Missed"
    }}
  ],

  "what_rep_did_well": [
    {{
      "moment": "string — what happened",
      "quote_or_timestamp": "string — the line or roughly when",
      "why_good": "string — which methodology principle this matched"
    }}
  ],

  "what_rep_missed": [
    {{
      "moment": "string — what happened or didn't happen",
      "quote_or_context": "string — the relevant exchange",
      "what_should_have_fit": "string — exact methodology play that fit here, with sample wording"
    }}
  ],

  "objections_encountered": [
    {{
      "objection_type": "Working with me/company" | "Process" | "Price" | "Other Offers" | "Paperwork" | "Think about it" | "Talk to spouse" | "Other",
      "seller_quote": "string",
      "framework_used": "Deflect & Redirect" | "Go For No" | "Boxing Objections" | "Multiple Offers" | "None / mishandled",
      "handled_well": true | false,
      "note": "string"
    }}
  ],

  "tonality_assessment": "string — paragraph on rep's tone calibration (warmth, empathy vs sympathy, pace, handling of emotional moments)",

  "non_committal_language_detected": [
    {{
      "seller_phrase": "string — e.g., 'I think so', 'maybe', 'we'll see'",
      "rep_response": "string — how the rep handled it (drilled in? let it slide?)",
      "should_have": "string — what the methodology says to do"
    }}
  ],

  "next_call_recommendations": [
    {{
      "play": "string — name of the tactic (e.g., 'Recap motivation emotionally', 'Box objections after Go-For-No')",
      "specific_phrasing": "string — exact words to use, ideally echoing the seller's own language",
      "expected_outcome": "string — what this should accomplish"
    }}
  ],

  "qualification_verdict": {{
    "qualified": true | false,
    "path_taken": "Run Process" | "Talk price immediately" | "Disqualify" | "Try to overcome",
    "was_routing_correct": true | false,
    "note": "string — was the rep's path choice right per the UMBC qualification visual?"
  }}
}}

Do not wrap this JSON in code fences or any other formatting. Return raw JSON only."""


def build_user_prompt(
    transcript_text: str,
    deal_context: Dict[str, Any],
    call_type: str = "Process Call",
) -> str:
    """The user message — contains the transcript and deal context."""
    prop = deal_context.get("property", {}) or {}
    rec = deal_context.get("recommendation", {}) or {}
    seller = deal_context.get("seller", {}) or {}

    return f"""CALL TYPE: {call_type}

DEAL CONTEXT:
  Address:           {prop.get('address', '—')}
  Location:          {prop.get('city', '')}, {prop.get('state', '')}
  Beds / Baths:      {prop.get('beds', '—')} / {prop.get('baths', '—')}
  Living Sqft:       {prop.get('sqft', '—')}
  Year Built:        {prop.get('year', '—')}
  Pool:              {prop.get('pool', '—')}
  Seller's Asking:   {_fmt_money(prop.get('asking', 0))}

  ARV:               {_fmt_money(rec.get('arv', 0))}
  Total Rehab:       {_fmt_money(rec.get('rehab_total', 0))}
  Cash MAO:          {_fmt_money(rec.get('cash_offer', 0))}
  Recommended:       {rec.get('strategy', '—')}

  Payment Status:    {seller.get('payment_status', '—')}
  Reason for Sale:   {seller.get('reason', '—')}
  Required Net:      {_fmt_money(seller.get('required_net', 0))}

---

TRANSCRIPT (speakers separated by diarization):

{transcript_text}

---

Grade this call per the methodology. Return the JSON object as specified.
Do not include any text outside the JSON.
"""


# ---------------------------------------------------------------------------
# Run the analysis
# ---------------------------------------------------------------------------
def analyze_call(
    transcript_text: str,
    deal_context: Dict[str, Any],
    call_type: str = "Process Call",
) -> Dict[str, Any]:
    """Send the transcript to Claude and return the structured analysis.

    Args:
        transcript_text: The labeled transcript ("**Rep:** ...  **Seller:** ...")
        deal_context: {"property": prop_dict, "recommendation": rec_dict,
                       "seller": seller_dict}
        call_type:    "Process Call" | "Offer Call" | "Renegotiation" |
                      "Follow-up" | "Other"

    Returns:
        dict — either the parsed analysis with all the schema fields,
        OR {"error": "..."} if the call failed or JSON didn't parse.
    """
    if not transcript_text or not transcript_text.strip():
        return {"error": "Transcript is empty."}
    if not is_configured():
        return {"error": "Anthropic API key not configured."}

    try:
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(transcript_text, deal_context, call_type)

        client = _client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = (response.content[0].text or "").strip()

        # Claude sometimes wraps JSON in ```json ... ``` despite our instruction.
        # Strip that defensively.
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```", 2)
            # ['', 'json\n{...}\n', ''] — take the middle, drop the language tag
            raw_text = raw_text[1] if len(raw_text) > 1 else ""
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as e:
            return {
                "error": f"Could not parse analysis as JSON: {e}",
                "raw_response": raw_text[:2000],
            }

        # Stamp metadata
        parsed["_meta"] = {
            "model": MODEL,
            "call_type": call_type,
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
        return parsed

    except Exception as e:
        return {"error": f"Analysis failed: {e}"}


# ---------------------------------------------------------------------------
# Display helpers — render the structured analysis as Markdown for Streamlit
# ---------------------------------------------------------------------------
SCORE_EMOJI = {
    "Strong": "🟢",
    "Adequate": "🟡",
    "Weak": "🟠",
    "Missed": "🔴",
}


def _check(item: Dict[str, Any], label: str) -> str:
    """Render one checklist row as a Markdown line."""
    covered = bool((item or {}).get("covered", False))
    feedback = (item or {}).get("feedback", "") or ""
    icon = "✅" if covered else "❌"
    return f"- {icon} **{label}** — {feedback}".rstrip()


def format_process_call_checklist(analysis: Dict[str, Any]) -> str:
    """Render the Results Driven Process Call Checklist as a visual list.
    Returns empty string if not applicable to this call type."""
    cl = analysis.get("process_call_checklist", {}) or {}
    if not cl.get("applicable", False):
        note = cl.get("applicable_note", "")
        return ("### Process Call Checklist\n*Not applicable for this call type."
                + (f" — {note}*" if note else "*"))

    out = ["### Process Call Checklist"]

    # Intro
    out.append(_check(cl.get("intro"), "Intro"))

    # Set Expectations (open)
    se_open = cl.get("set_expectations_open", {}) or {}
    out.append("**Set Expectations (open)**")
    out.append(_check(se_open.get("time"), "Time"))
    out.append(_check(se_open.get("agenda"), "Agenda"))
    out.append(_check(se_open.get("result"), "Result"))
    out.append(_check(se_open.get("permission_to_say_no"), "Permission to say No"))
    out.append(_check(se_open.get("urgency"), "Urgency"))

    # Motivation
    mot = cl.get("motivation", {}) or {}
    out.append("**Motivation**")
    out.append(_check(mot.get("situation"), "Situation"))
    out.append(_check(mot.get("impact_1"), "Impact 1"))
    out.append(_check(mot.get("impact_2"), "Impact 2"))
    out.append(_check(mot.get("impact_3"), "Impact 3"))
    out.append(_check(mot.get("perfect_picture"), "Perfect Picture / Goal"))

    # Property Condition
    out.append(_check(cl.get("property_condition"), "Property Condition"))

    # Road Blocks
    rb = cl.get("road_blocks", {}) or {}
    out.append("**Road Blocks**")
    out.append(_check(rb.get("time"), "Time"))
    out.append(_check(rb.get("influencers"), "Influencers"))
    out.append(_check(rb.get("discomfort"), "Discomfort"))
    out.append(_check(rb.get("money"), "Money"))

    # Set Expectations (close)
    se_close = cl.get("set_expectations_close", {}) or {}
    out.append("**Set Expectations (close)**")
    out.append(_check(se_close.get("time"), "Time"))
    out.append(_check(se_close.get("agenda"), "Agenda"))
    out.append(_check(se_close.get("result"), "Result"))
    out.append(_check(se_close.get("permission_to_say_no"), "Permission to say No"))

    notes = cl.get("notes", "")
    if notes:
        out.append("")
        out.append(f"**Notes:** {notes}")

    return "\n".join(out)


def format_umbc_section(analysis: Dict[str, Any]) -> str:
    """Compact UMBC scorecard for display."""
    umbc = analysis.get("umbc", {}) or {}
    lines = ["### UMBC Qualification"]
    for letter, name in [("U", "urgency"), ("M", "motivation"),
                         ("B", "ballpark"), ("C", "condition")]:
        section = umbc.get(name, {}) or {}
        score = section.get("score", "—")
        emoji = SCORE_EMOJI.get(score, "⚪")
        note = section.get("note", "")
        lines.append(f"- **{letter} — {name.title()}** {emoji} *{score}* — {note}")
        if name == "motivation":
            cats = section.get("categories_found", []) or []
            if cats:
                lines.append(f"  - Categories surfaced: {', '.join(cats)}")
        evidence = section.get("evidence") or section.get("seller_quoted_price")
        if evidence and evidence not in ("not asked", ""):
            lines.append(f"  - Evidence: *\"{evidence}\"*")
    return "\n".join(lines)


def format_pain_points(analysis: Dict[str, Any]) -> str:
    pains = analysis.get("pain_points_identified", []) or []
    if not pains:
        return "### Pain Points Revealed\n*None identified.*"
    lines = ["### Pain Points Revealed"]
    for p in pains:
        cat = p.get("category", "—")
        quote = p.get("seller_quote", "")
        followed_up = "✅ rep followed up" if p.get("rep_followed_up") else "❌ rep did not follow up"
        quality = p.get("rep_response_quality", "")
        lines.append(f"- **{cat}** — {followed_up} ({quality})")
        if quote:
            lines.append(f"  > *\"{quote}\"*")
    return "\n".join(lines)


def format_what_rep_did_well(analysis: Dict[str, Any]) -> str:
    items = analysis.get("what_rep_did_well", []) or []
    if not items:
        return ""
    lines = ["### What You Did Well"]
    for it in items:
        lines.append(f"- **{it.get('moment', '—')}** — {it.get('why_good', '')}")
        q = it.get("quote_or_timestamp", "")
        if q:
            lines.append(f"  > *\"{q}\"*")
    return "\n".join(lines)


def format_what_rep_missed(analysis: Dict[str, Any]) -> str:
    items = analysis.get("what_rep_missed", []) or []
    if not items:
        return ""
    lines = ["### What You Missed"]
    for it in items:
        lines.append(f"- **{it.get('moment', '—')}**")
        ctx = it.get("quote_or_context", "")
        if ctx:
            lines.append(f"  - Context: *\"{ctx}\"*")
        fit = it.get("what_should_have_fit", "")
        if fit:
            lines.append(f"  - Should have: {fit}")
    return "\n".join(lines)


def format_next_call(analysis: Dict[str, Any]) -> str:
    items = analysis.get("next_call_recommendations", []) or []
    if not items:
        return ""
    lines = ["### Next-Call Tactical Recommendations"]
    for i, it in enumerate(items, 1):
        play = it.get("play", "—")
        phrasing = it.get("specific_phrasing", "")
        outcome = it.get("expected_outcome", "")
        lines.append(f"{i}. **{play}**")
        if phrasing:
            lines.append(f"   - *\"{phrasing}\"*")
        if outcome:
            lines.append(f"   - Outcome: {outcome}")
    return "\n".join(lines)


def format_objections(analysis: Dict[str, Any]) -> str:
    items = analysis.get("objections_encountered", []) or []
    if not items:
        return ""
    lines = ["### Objections Encountered"]
    for it in items:
        otype = it.get("objection_type", "—")
        framework = it.get("framework_used", "—")
        ok = "✅" if it.get("handled_well") else "❌"
        lines.append(f"- **{otype}** — {ok} framework: *{framework}*")
        if it.get("seller_quote"):
            lines.append(f"  > *\"{it['seller_quote']}\"*")
        if it.get("note"):
            lines.append(f"  - {it['note']}")
    return "\n".join(lines)


def format_non_committal(analysis: Dict[str, Any]) -> str:
    items = analysis.get("non_committal_language_detected", []) or []
    if not items:
        return ""
    lines = ["### Non-Committal Language Detected"]
    for it in items:
        lines.append(f"- Seller said: *\"{it.get('seller_phrase', '')}\"*")
        lines.append(f"  - Rep response: {it.get('rep_response', '')}")
        lines.append(f"  - Should have: {it.get('should_have', '')}")
    return "\n".join(lines)


def format_full_analysis(analysis: Dict[str, Any]) -> str:
    """Return a Markdown-formatted analysis ready for st.markdown()."""
    if "error" in analysis:
        return f"### Analysis failed\n\n{analysis['error']}"
    sections = [
        f"## Call Analysis — Grade **{analysis.get('overall_grade', '—')}** · "
        f"Contract Likelihood **{analysis.get('contract_likelihood_pct', '—')}%**",
        f"*{analysis.get('summary_one_line', '')}*",
        "",
        format_process_call_checklist(analysis),
        "",
        format_umbc_section(analysis),
        "",
        format_pain_points(analysis),
        "",
        format_what_rep_did_well(analysis),
        "",
        format_what_rep_missed(analysis),
        "",
        format_objections(analysis),
        "",
        format_non_committal(analysis),
        "",
        format_next_call(analysis),
    ]
    # Tonality at the end
    tone = analysis.get("tonality_assessment", "")
    if tone:
        sections.append("")
        sections.append("### Tonality")
        sections.append(tone)

    # Filter empty strings
    return "\n".join(s for s in sections if s is not None)
