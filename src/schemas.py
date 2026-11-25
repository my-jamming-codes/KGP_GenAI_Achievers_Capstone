"""Shared data models. Every agent step returns one of these, validated by Pydantic."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["met", "breached", "needs_information", "not_applicable", "unknown"]
# "unknown" = the rule for this lender type is not verified in rules.yaml, so the checker will not judge it.
LenderType = Literal["hfc", "bank"]
TriState = Literal["yes", "no", "not_recorded"]

CHECKS = [
    "ltv_cap",
    "risk_weight",
    "prepayment_charge",
    "key_facts_statement",
    "guarantor_disclosure",
    "valuation",
    "kyc",
    "rate_reset_communication",
    "related_party",
    "mitc",
]
CheckName = Literal[
    "ltv_cap", "risk_weight", "prepayment_charge", "key_facts_statement", "guarantor_disclosure",
    "valuation", "kyc", "rate_reset_communication", "related_party", "mitc",
]


class Case(BaseModel):
    """One synthetic home loan case, flattened from the application tabs or a scenario form."""

    model_config = ConfigDict(extra="ignore")

    lender_type: LenderType
    lender_name: str = ""
    hfc_layer: Optional[Literal["base", "middle", "upper"]] = None
    bank_asset_size_crore: Optional[float] = None
    borrower_type: Literal["individual", "non_individual"] = "individual"
    borrower_is_mse: bool = False
    loan_amount: int
    property_value: int
    property_cost: Optional[int] = None
    stamp_duty_registration: int = 0
    rate_type: Literal["floating", "fixed"] = "floating"
    prepayment_charge_levied: bool = False
    prepayment_charge_scope: Optional[Literal["any_source", "borrowed_funds_only"]] = None
    guarantor_present: bool = False
    guarantor_disclosure: Optional[TriState] = None
    kfs_provided: TriState = "yes"
    mitc_signed: TriState = "yes"
    valuation_reports: int = 1
    independent_valuation_reports: int = 1
    pan_or_form60: Literal["provided", "not_provided"] = "provided"
    identity_address_proof: Literal["provided", "not_provided"] = "provided"
    rate_reset_impact_communicated: TriState = "yes"
    related_party: Literal["none", "director", "director_relative", "kmp"] = "none"
    board_sanction: Optional[Literal["yes", "no"]] = None
    housing_loan_number: int = 1
    case_date: date
    sanction_date: Optional[date] = None
    free_text_note: str = ""

    @model_validator(mode="after")
    def _defaults(self) -> "Case":
        if self.sanction_date is None:
            self.sanction_date = self.case_date
        return self


class PlannedCheck(BaseModel):
    check: CheckName
    tool: str


class Plan(BaseModel):
    intent: Literal["rule_check", "rule_lookup", "refuse", "out_of_scope"] = "rule_check"
    lender_type: Optional[LenderType] = None
    checks: list[PlannedCheck] = Field(default_factory=list)
    search_query: str = ""


class RetrievedParagraph(BaseModel):
    paragraph_id: str
    doc_key: str
    lender_type: str
    score: float
    text: str


class ToolResult(BaseModel):
    """Output of a plain tool function. All arithmetic and date logic lives here."""

    check: str
    status: Status
    rule_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    values: dict[str, Any] = Field(default_factory=dict)
    note: str = ""    # shown to the officer as part of the reason
    hint: str = ""    # for the model only: added to the critic's retry message, never shown


class Judgement(BaseModel):
    """What the language model returns for one rule check."""

    check: str
    status: Status
    paragraph_id: Optional[str] = None
    values_used: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class CriticVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    failed_checks: list[dict[str, str]] = Field(default_factory=list)
    reason: str = ""


class CheckResult(BaseModel):
    """Final per-rule result shown to the officer."""

    check: str
    status: Status
    paragraph_id: Optional[str] = None
    rule_id: Optional[str] = None
    values: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    confirmed_by_critic: bool = True


class RunOutput(BaseModel):
    trace_id: str
    refused: bool = False
    refusal_reason: str = ""
    answer: str = ""
    citations: list[str] = Field(default_factory=list)
    results: list[CheckResult] = Field(default_factory=list)
    retrieved: list[str] = Field(default_factory=list)
    steps_used: int = 0
    stopped: str = ""  # "", "step_cap", "time_limit", "token_budget", "rate_limit"
    cache_hit: bool = False
    injection_detected: bool = False
    critic_failures: int = 0
    wrong_rulebook_caught: bool = False
    critic_kinds: list[str] = Field(default_factory=list)
    first_pass: dict[str, dict] = Field(default_factory=dict)
    tokens: int = 0


TAB_NAMES = [
    "Applicant",
    "Co-applicant and Guarantor",
    "Employment and Income",
    "Property",
    "Loan request",
    "Bank and Liabilities",
    "Documents",
    "Declarations",
]

LENDER_LABEL = {"hfc": "Housing finance company", "bank": "Scheduled commercial bank"}


def case_from_application(app: dict) -> Case:
    """Flatten an application's tabs into a Case. Field names in the tabs match Case fields."""
    flat: dict[str, Any] = {}
    for tab in app["tabs"].values():
        flat.update(tab)
    label = str(flat.get("lender_type", "")).lower()
    flat["lender_type"] = "hfc" if "housing finance" in label or label == "hfc" else "bank"
    flat["case_date"] = app["case_date"]
    flat["sanction_date"] = flat.get("proposed_sanction_date") or app["case_date"]
    flat["free_text_note"] = str(flat.get("officer_note", ""))
    return Case(**{k: v for k, v in flat.items() if v is not None or k in Case.model_fields})
