"""Frequently asked questions for the compliance officer.

Two kinds of entry:
- Workspace questions: how the tool behaves. Fixed text, and it states no RBI limit.
- Rule questions: answered only with the verbatim RBI quotes and paragraph ids held in rules/rules.yaml.
  No figure is written here by hand, so an answer can never drift from the verified rules file.
  An unverified record is shown as unverified, with the reason, never with an answer.
"""
from __future__ import annotations

from src import rules

# One plain-language question per rule theme in rules.yaml. A theme missing here is still shown (see rule_faq).
THEME_QUESTIONS = {
    "loan_to_value_cap": "What is the maximum loan-to-value (LTV) ratio for a housing loan?",
    "loan_to_value_computation_and_stamp_duty": "How is LTV computed, and can stamp duty and registration charges be added to the property cost?",
    "risk_weight_by_ltv": "What risk weight applies to a housing loan, and does it change over time?",
    "valuation_of_property": "How many valuation reports does the property need?",
    "prepayment_charges": "Can a prepayment or foreclosure charge be levied on a home loan?",
    "interest_rate_reset_communication": "What must the borrower be told when a floating interest rate is reset?",
    "key_facts_statement": "When must the Key Facts Statement (KFS) be given to the borrower?",
    "disclosure_to_borrower": "What terms must be disclosed to the borrower, such as the MITC?",
    "guarantor_disclosure": "What must a guarantor to the loan be told?",
    "kyc_customer_due_diligence": "What KYC and customer due diligence is needed for the borrower?",
    "related_party_lending": "Can the lender give a home loan to a director, a director's relative or key managerial personnel?",
    "grievance_redressal": "How must borrower complaints and grievances be handled?",
}

LENDER_NAMES = {"hfc": "Housing finance company", "bank": "Scheduled commercial bank"}

WORKSPACE_FAQ = [
    ("How do I run the RBI rule check on an application?",
     "Open the application from the Work queue, go to Compliance checks and click **Run RBI rule check**. "
     "The planner picks the rules for the lender type, tools compute the numbers, the model reads the cited paragraph, "
     "and the critic confirms each result before you see it."),
    ("What do the statuses mean?",
     "**Met**: the rule is satisfied. **Breached**: the application does not meet the rule. "
     "**Needs information**: a field is missing, not recorded, or the critic could not confirm the model's reading. "
     "**Not applicable**: the rule does not apply to this case, for example no guarantor. "
     "**Unknown**: the rule for this lender type is not verified in the rules file, so the checker will not judge it."),
    ("Why does a result say “Unknown (rule unverified)”?",
     "Every limit comes from the verified rules file, never from memory. When no RBI paragraph has been found and read for a rule, "
     "the record is marked unverified and every answer about it is “unknown”. Check that rule yourself against the RBI text."),
    ("Does the assistant approve loans or mark applications compliant?",
     "No. It recommends only. Requests to approve, declare compliance, predict repayment or give legal advice are refused. "
     "Only your click on **Request more information**, **Return with reasons** or **Compliance cleared** changes a status."),
    ("Who does the calculations?",
     "Plain Python tools compute LTV, risk weight bands and dates. The model never does arithmetic; "
     "the values a tool used are shown under each result as “Tool values”."),
    ("Why is a result shown as “not confirmed by the critic”?",
     "The critic compares each judgement with the tool result and the cited paragraph and allows one retry. "
     "If they still disagree, the result is withheld as “Needs information” so that a wrong figure never reaches you."),
    ("What does “Share this screen” send to the assistant?",
     "Only the fields of the open tab, as text, with PAN and Aadhaar masked. No image is sent. "
     "The expander under the toggle shows exactly what was shared. With sharing off, the assistant answers general rule questions only."),
    ("The application note contains an instruction such as “ignore the rules”. What happens?",
     "Instruction-like text in notes, questions and retrieved paragraphs is removed before the model sees it, "
     "and the result says that instruction-like text was ignored. Nothing in the agent code can change a status."),
    ("Why did I get a “served from cache” or a “please wait” message?",
     "A repeated question is answered from the cache, so it is instant and costs no tokens. "
     "Questions are rate limited per session to protect the model quota; the message says how long to wait."),
    ("Where can I see what the assistant did?",
     "**History and evaluation** lists every question and officer action. Open a trace to see each step: guard, planner, "
     "executor, judge, critic and drafter, with the paragraphs retrieved and the tokens used."),
]


def _entry(r: dict) -> dict:
    src = rules.sources().get(r.get("source") or "", {})
    return {
        "rule_id": r["rule_id"],
        "lender_type": r["lender_type"],
        "status": r["status"],
        "paragraph_id": r.get("paragraph_id"),
        "paragraph": r.get("paragraph"),
        "source_title": src.get("title", ""),
        "source_url": src.get("url", ""),
        "quote": list(r.get("quote") or []),
        "effective_date": str(r["effective_date"]) if r.get("effective_date") else None,
        "effective_until": str(r["effective_until"]) if r.get("effective_until") else None,
        "superseded_by": r.get("superseded_by"),
        "unverified_reason": (r.get("unverified_reason") or "").strip(),
    }


def rule_faq() -> list[dict]:
    """One FAQ item per theme in rules.yaml, each holding every record (verified or not) for that theme."""
    items: dict[str, dict] = {}
    for r in rules.all_rules(include_unverified=True):
        theme = r["theme"]
        item = items.setdefault(theme, {"theme": theme, "question": THEME_QUESTIONS.get(theme, theme.replace("_", " ").capitalize() + "?"),
                                        "entries": []})
        item["entries"].append(_entry(r))
    order = list(THEME_QUESTIONS)
    return sorted(items.values(), key=lambda i: order.index(i["theme"]) if i["theme"] in order else len(order))


def filter_faq(items: list[dict], text: str = "", lender_type: str | None = None) -> list[dict]:
    """Keep items whose question or quotes contain the search text; keep only entries for the lender type if given."""
    needle = text.strip().lower()
    out = []
    for item in items:
        entries = [e for e in item["entries"] if lender_type in (None, e["lender_type"])]
        if not entries:
            continue
        haystack = " ".join([item["question"], *(q for e in entries for q in e["quote"])]).lower()
        if needle and needle not in haystack:
            continue
        out.append({**item, "entries": entries})
    return out


def filter_workspace(text: str = "") -> list[tuple[str, str]]:
    needle = text.strip().lower()
    return [(q, a) for q, a in WORKSPACE_FAQ if not needle or needle in (q + " " + a).lower()]
