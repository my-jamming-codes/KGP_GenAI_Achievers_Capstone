"""Requests the assistant must refuse or hand back to the officer. Checked in code before any model call."""
from __future__ import annotations

import re
from typing import Optional

RULES: list[tuple[str, str]] = [
    ("repayment_prediction", r"\b(will|would|can|could|is|does)\b.{0,60}\b(repay|pay back|default|pay (the |his |her |their )?emis?)\b|\b(likely|chance|probability|risk) (to|of) (default|repay)|\btrust (him|her|them|this borrower|the borrower)\b|\b(salary|income) (is |be )?(enough|sufficient|adequate)\b|\bcredit ?worth"),
    ("loan_decision", r"^\s*(please )?(approve|sanction|reject|decline)\b|\b(approve|sanction|reject|decline) (this|the|that|his|her) (loan|application|case)\b|\bshould (we|i|the bank|the lender) (approve|sanction|reject|lend)\b"),
    ("compliance_declaration", r"\b(declare|certify|confirm|mark|flag|stamp)\b.{0,60}\b(compliant|compliance cleared|cleared|fully compliant)\b|\bset\b.{0,40}\bstatus\b|\bchange\b.{0,30}\bstatus\b|\bis (this|the) (application|loan|lender) (fully )?compliant\b"),
    ("legal_advice", r"\blegal advice\b|\b(sue|suing|lawsuit|litigat|court case|legal notice|take .{0,20}to court)\b"),
    ("pii_disclosure", r"\b(full|complete|unmasked|entire)\b.{0,30}\b(pan|aadhaar|aadhar|phone|mobile|email|address)\b|\b(pan|aadhaar|aadhar) (number|no\.?)\b.{0,30}\b(full|unmasked|complete)\b|\b(tell|give|show|reveal) me\b.{0,40}\b(aadhaar|aadhar|pan) (number|no)"),
    ("out_of_scope", r"\b(mutual fund|stock|share market|crypto|bitcoin|invest in|weather|recipe|cricket|movie|horoscope|election|poem|joke)\b"),
]
_RE = [(code, re.compile(rx, re.I)) for code, rx in RULES]

MESSAGES = {
    "repayment_prediction": "I can't say whether a borrower will repay or judge their income or creditworthiness. I check the application against RBI rules and cite the paragraph.",
    "loan_decision": "I can't approve or reject a loan. I can run the RBI rule check and show each result with its paragraph; the decision is made at a later stage.",
    "compliance_declaration": "I can't declare an application compliant or change its status. Only the officer's click does that. I can show each rule result with its cited paragraph.",
    "legal_advice": "I can't give legal advice. I can show what the RBI directions say, with the paragraph, for you to take to a legal adviser.",
    "pii_disclosure": "I can't share full identity numbers or contact details. They are masked in everything I see and write.",
    "out_of_scope": "That is outside what I cover. I answer questions about RBI rules for home loans and about the open application.",
}


def classify(question: str) -> Optional[str]:
    """Return a refusal code, or None when the question may proceed."""
    for code, rx in _RE:
        if rx.search(question or ""):
            return code
    return None


def message(code: str) -> str:
    return MESSAGES.get(code, MESSAGES["out_of_scope"])


FORBIDDEN_OUTPUT = re.compile(r"\b(is|are|stands|has been|now) (fully )?(compliant|compliance cleared|approved)\b|\bI (have )?(approved?|cleared|marked)\b|\bloan (is|has been) approved\b", re.I)


def soften_output(text: str) -> str:
    """The model recommends; it never declares compliance or approval. Replace any such wording."""
    return FORBIDDEN_OUTPUT.sub("meets the checked rules (recommendation only; the officer decides)", text)
