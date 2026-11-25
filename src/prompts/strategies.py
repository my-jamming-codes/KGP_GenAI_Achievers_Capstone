"""Prompts and reply schemas. Three judging strategies get the same retrieved paragraphs and tool facts,
so only the prompt differs.

A. Direct check with citations: one prompt.
B. Extract then judge: quote the rule and the field values, then judge from the quotes only.
C. Plan then judge with self check: a short plan, the judgement, then a check of numbers and lender type.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.safety.injection import UNTRUSTED_RULE
from src.schemas import CHECKS, Status

CHECK_TITLES = {
    "ltv_cap": "Loan to value (LTV) cap for the loan size",
    "risk_weight": "Risk weight band for the LTV (report the weight you read as limit_read)",
    "prepayment_charge": "Whether the prepayment charge in the loan terms is allowed",
    "key_facts_statement": "Key facts statement given to the borrower before the loan contract",
    "guarantor_disclosure": "What the guarantor must be told",
    "valuation": "Property valuation reports required",
    "kyc": "Customer due diligence documents",
    "rate_reset_communication": "Communication of interest rate reset impact at sanction",
    "related_party": "Lending to a director or other related party",
    "mitc": "Most important terms and conditions (MITC) document",
}

CHECK_GUIDE = {
    "ltv_cap": "Find the loan size band for loan_amount, read its LTV limit, and compare ltv_pct with it. Not applicable when borrower_type is not individual.",
    "risk_weight": "Find the row for the loan size and ltv_pct and give its weight as limit_read; the status is then met. "
                   "If ltv_pct is above every band listed for that loan size, the status is not_applicable. Use the table in force on case_date.",
    "prepayment_charge": "If prepayment_charge_levied is False the status is met. Otherwise decide from rate_type, borrower_type, the source of funds "
                         "(prepayment_charge_scope) and sanction_date whether the paragraph bars the charge: barred is breached, not barred is met.",
    "key_facts_statement": "Not applicable if sanction_date is before the start date in the paragraph. Otherwise kfs_provided yes is met, no is breached, not_recorded is needs_information.",
    "guarantor_disclosure": "Not applicable if guarantor_present is False. Otherwise guarantor_disclosure yes is met, no is breached, not_recorded is needs_information.",
    "valuation": "Read how many valuation reports, and how many independent ones, the paragraph requires for this loan_amount (or property value), and compare with the reports on file. "
                 "Too few is breached; none on file is needs_information.",
    "kyc": "Both pan_or_form60 and identity_address_proof provided is met; anything not_provided is needs_information.",
    "rate_reset_communication": "Not applicable unless rate_type is floating and borrower_type is individual. Otherwise yes is met, no is breached, not_recorded is needs_information.",
    "related_party": "Not applicable if related_party is none. Otherwise apply the paragraph: a prohibition is breached; a loan above the materiality threshold needs Board sanction "
                     "(yes is met, no is breached, not recorded is needs_information); a loan within the threshold is met.",
    "mitc": "mitc_signed yes is met, no is breached, not_recorded is needs_information. Not applicable when borrower_type is not individual.",
}

CONVENTIONS = """Statuses:
- met: the case satisfies the rule in the paragraph.
- breached: a limit is exceeded, a barred charge is levied, or a field says a required step was not done ("no").
- needs_information: a required item is "not_recorded" or "not_provided".
- not_applicable: the rule does not cover this case (for example no guarantor, fixed rate for a floating rate rule, or a loan sanctioned before the rule's start date).
- unknown: only when told the rule is unverified.
A value exactly equal to a limit is met. "Up to ₹30 lakh" includes ₹30 lakh. 1 lakh = 100000."""

BASE_SYSTEM = (
    "You help a compliance officer check a synthetic home loan case against Reserve Bank of India directions. "
    "You read the quoted paragraphs and explain; you never calculate. Numbers such as the LTV are computed by tools and given to you. "
    "Every limit you state must come from a quoted paragraph, never from memory. Cite the paragraph id exactly as shown. "
    "You recommend only: never say an application is compliant or approved. " + UNTRUSTED_RULE
)


class JudgeReply(BaseModel):
    status: Status
    paragraph_id: str = Field(description="id of the quoted paragraph that holds the rule, exactly as shown")
    limit_read: Optional[str] = Field(default=None, description="the limit or weight read from the paragraph, for example '80' or '35'")
    reason: str = Field(description="one or two sentences")


class ExtractReply(BaseModel):
    paragraph_id: str
    rule_quote: str = Field(description="exact words copied from the paragraph that decide this check")
    field_values: list[str] = Field(description="the case facts that matter, each as 'name = value'")


class SelfCheck(BaseModel):
    numbers_match_paragraph: bool
    paragraph_is_for_this_lender_type: bool


class PlanJudgeReply(BaseModel):
    plan: list[str] = Field(description="two to four short steps")
    status: Status
    paragraph_id: str
    limit_read: Optional[str] = None
    reason: str
    self_check: SelfCheck


class PlannerReply(BaseModel):
    intent: Literal["rule_check", "rule_lookup", "refuse", "out_of_scope"]
    scope: Literal["full", "specific"] = "specific"
    lender_type: Optional[Literal["hfc", "bank"]] = None
    checks: list[Literal[tuple(CHECKS)]] = Field(default_factory=list)  # type: ignore[valid-type]
    search_query: str = ""


class DraftReply(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)


PLANNER_SYSTEM = (
    "You are the planner for an RBI home loan rule checker. Decide what the question needs.\n"
    "intent: rule_check when a case or open application must be tested against rules; rule_lookup for a general question about what RBI rules say; "
    "refuse when asked to approve or reject a loan, declare compliance, change a status, predict repayment or give legal advice; "
    "out_of_scope for anything that is not about RBI lending rules.\n"
    "lender_type: 'hfc' for a housing finance company, 'bank' for a scheduled commercial bank. Read it from the case; for a lookup read it from the question, or null.\n"
    "scope: 'full' only when the question asks for the full check, all rules, or what is missing or outstanding; otherwise 'specific'.\n"
    "checks: for scope 'specific', only the one or two checks the question names, nothing else. For scope 'full', every check that applies.\n"
    "Available checks:\n" + "\n".join(f"- {k}: {v}" for k, v in CHECK_TITLES.items()) + "\n"
    "search_query: for rule_lookup, a short search phrase using RBI wording.\n" + UNTRUSTED_RULE
)

DRAFT_LOOKUP_SYSTEM = (
    BASE_SYSTEM + "\nAnswer the officer's question using only the quoted paragraphs. Give the figure or condition asked for and cite the paragraph ids you used "
    "in `citations`. If the paragraphs do not answer the question, say so. If the question is about a rule listed as unverified, say it is unverified "
    "and unknown, and do not answer it from memory. Keep the answer under 90 words."
)

DRAFT_REPORT_SYSTEM = (
    BASE_SYSTEM + "\nWrite a short note for the officer from the rule check results given. State each result with its paragraph id in brackets. "
    "The statuses, numbers and paragraph ids in the results are final: never change them and add no other paragraph id. "
    "Cited paragraphs are quoted after the results. Explain the breached and needs_information items, or the result asked about, "
    "using what its own paragraph says, including the conditions that apply to this case. Say nothing that the results and "
    "paragraphs do not say. Start with the breached and needs_information items. "
    "End with: 'Recommendation only; the officer decides.' Keep it under 180 words."
)


def judge_user(check: str, lender_label: str, facts: dict, evidence: str, feedback: str = "") -> str:
    lines = [f"Lender type: {lender_label}", f"Rule check: {check} - {CHECK_TITLES[check]}",
             "Case facts (from the application and from tools; do not recalculate):"]
    lines += [f"- {k} = {v}" for k, v in facts.items()]
    lines += ["", "Paragraphs retrieved:", evidence, "", CONVENTIONS, f"For this check: {CHECK_GUIDE[check]}"]
    if feedback:
        lines += ["", f"A reviewer rejected the previous answer: {feedback}", "Correct it."]
    return "\n".join(lines)


JUDGE_A = BASE_SYSTEM + "\nDecide the status of this one rule check and cite the paragraph that holds the rule."
EXTRACT_B = BASE_SYSTEM + "\nStep 1 of 2. Do not judge yet. Copy the exact words of the rule that decides this check, with its paragraph id, and list the case facts that matter."
JUDGE_B = BASE_SYSTEM + "\nStep 2 of 2. Judge this rule check using only the quoted rule words and the listed facts below. Do not use any other knowledge."
JUDGE_C = (BASE_SYSTEM + "\nFirst write a short plan of how to test this rule check. Then give the status and the paragraph. "
           "Then self check: does every number you used appear in the cited paragraph, and is the cited paragraph from a document for this lender type? "
           "If a self check fails, fix the answer before replying.")
