"""Rule check tools. Plain functions: all arithmetic, date logic and thresholds live here, never in the model.

Every threshold is read from rules/rules.yaml (verified records only). A tool that needs an unverified
rule returns status 'unknown' and says why.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Optional

from src import rules
from src.schemas import Case, ToolResult

TRI = {"yes": "met", "no": "breached", "not_recorded": "needs_information", None: "needs_information"}
RUPEE_LAKH = 100000


def _lakh(n: Optional[int]) -> str:
    return "no upper limit" if n is None else f"₹{n / RUPEE_LAKH:g} lakh"


# ------------------------------------------------------------------ lender type picker
def pick_lender_type(case: Case) -> dict:
    """Rulebook selection for the lender type."""
    books = {
        "hfc": {"rulebook": "HFC-MD", "title": "Reserve Bank of India (Housing Finance Companies) Directions, 2025",
                "also_applies": ["NBFC-RBC", "NBFC-KYC", "NBFC-CRM"]},
        "bank": {"rulebook": "CB-CF", "title": "Reserve Bank of India (Commercial Banks – Credit Facilities) Directions, 2025",
                 "also_applies": ["CB-CAP", "CB-SA-2027", "CB-RBC", "CB-KYC", "CB-CRM"]},
    }
    return {"lender_type": case.lender_type, **books[case.lender_type]}


def lender_type_from_label(label: str) -> Optional[str]:
    t = label.lower()
    if "housing finance" in t or t.strip() == "hfc":
        return "hfc"
    if "bank" in t:
        return "bank"
    return None


# ------------------------------------------------------------------ amendment chain walker
def rule_in_force(theme: str, lender_type: str, on: date) -> Optional[dict]:
    """The verified record for a theme that is in force on a date."""
    live = []
    for r in rules.rules_for(theme, lender_type):
        start = date.fromisoformat(str(r["effective_date"]))
        end = date.fromisoformat(str(r["effective_until"])) if r.get("effective_until") else None
        if start <= on and (end is None or on <= end):
            live.append(r)
    return live[-1] if live else None


def amendment_chain(theme: str, lender_type: str) -> list[dict]:
    """Ordered list of records for a theme, with the dates each applies from and to."""
    return [{"rule_id": r["rule_id"], "paragraph_id": r["paragraph_id"], "effective_date": str(r["effective_date"]),
             "effective_until": str(r["effective_until"]) if r.get("effective_until") else None,
             "amendments": r.get("amendments", "")} for r in rules.rules_for(theme, lender_type)]


# ------------------------------------------------------------------ LTV calculator
def compute_ltv(case: Case) -> dict:
    rule_id = "HFC-LTV-COMPUTE" if case.lender_type == "hfc" else "BANK-LTV-COMPUTE"
    limit = rules.get_rule(rule_id)["values"]["stamp_duty_addable_if_cost_up_to"]
    cost = case.property_cost if case.property_cost is not None else case.property_value
    add = case.stamp_duty_registration > 0 and cost <= limit
    denominator = case.property_value + (case.stamp_duty_registration if add else 0)
    ltv = (Decimal(case.loan_amount) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"ltv_pct": float(ltv), "loan_amount": case.loan_amount, "value_used": denominator,
            "stamp_duty_added": add, "compute_rule_id": rule_id}


def _band(bands: list[dict], loan: int) -> Optional[dict]:
    for b in bands:
        if loan > b["loan_above"] and (b["loan_up_to"] is None or loan <= b["loan_up_to"]):
            return b
    return None


def check_ltv_cap(case: Case) -> ToolResult:
    rule = rules.get_rule("HFC-LTV-CAP" if case.lender_type == "hfc" else "BANK-LTV-CAP")
    if case.borrower_type != "individual":
        return ToolResult(check="ltv_cap", status="not_applicable", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"],
                          note="The LTV cap paragraph covers housing loans to individuals.")
    ltv = compute_ltv(case)
    band = _band(rule["values"]["bands"], case.loan_amount)
    status = "met" if ltv["ltv_pct"] <= band["max_ltv_pct"] else "breached"
    values = {**ltv, "cap_pct": band["max_ltv_pct"],
              "loan_band": (f"up to {_lakh(band['loan_up_to'])}" if band["loan_above"] == 0 else
                            f"above {_lakh(band['loan_above'])}" + (f" and up to {_lakh(band['loan_up_to'])}" if band["loan_up_to"] else ""))}
    note = f"LTV {ltv['ltv_pct']}% against a cap of {band['max_ltv_pct']}% for loans {values['loan_band']}."
    if ltv["stamp_duty_added"]:
        note += " Stamp duty and registration were added to the value because the house costs ₹10 lakh or less."
    return ToolResult(check="ltv_cap", status=status, rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values, note=note, hint=f"Report the limit as {band['max_ltv_pct']}.")


# ------------------------------------------------------------------ risk weight lookup
def check_risk_weight(case: Case) -> ToolResult:
    rule = rule_in_force("risk_weight_by_ltv", case.lender_type, case.case_date)
    if rule is None:
        return ToolResult(check="risk_weight", status="unknown", note="No verified risk weight rule is in force on the case date.")
    base = dict(check="risk_weight", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"])
    if case.borrower_type != "individual":
        return ToolResult(**base, status="not_applicable", note="The housing loan risk weight bands cover loans to individuals.")
    ltv = compute_ltv(case)["ltv_pct"]
    v = rule["values"]
    if "rows" in v:
        rows = v["rows"]
        if case.lender_type == "hfc":
            period = "before_cutoff" if case.sanction_date < date.fromisoformat(v["sanction_cutoff"]) else "on_or_after_cutoff"
            rows = [r for r in rows if r["sanctioned"] in ("any", period)]
        rows = [r for r in rows if case.loan_amount > r["loan_above"] and (r["loan_up_to"] is None or case.loan_amount <= r["loan_up_to"])]
    else:
        rows = v["up_to_two_loans"] if case.housing_loan_number <= 2 else v["third_loan_onward"]
    for r in rows:
        if (ltv > r["ltv_above"] or r["ltv_above"] == 0) and ltv <= r["ltv_up_to"]:
            return ToolResult(**base, status="met",
                              values={"ltv_pct": ltv, "risk_weight_pct": r["risk_weight_pct"], "housing_loan_number": case.housing_loan_number,
                                      "ltv_band": f"above {r['ltv_above']}% and up to {r['ltv_up_to']}%"},
                              note=f"Risk weight {r['risk_weight_pct']}% for LTV {ltv}% under {rule['paragraph_id']}, in force on {case.case_date}.")
    return ToolResult(**base, status="not_applicable", values={"ltv_pct": ltv},
                      note=f"LTV {ltv}% is outside every band in {rule['paragraph_id']} for this loan size, so no band weight applies.")


# ------------------------------------------------------------------ prepayment checker
def check_prepayment(case: Case) -> ToolResult:
    if case.lender_type == "hfc":
        rule = rules.get_rule("HFC-PREPAY-HOUSING")
        if not case.prepayment_charge_levied:
            status, note = "met", "No prepayment charge is levied."
        elif case.rate_type == "floating":
            status, note = "breached", "A charge is levied on a floating rate housing loan; paragraph 163(1) bars it from any source."
        elif case.prepayment_charge_scope == "borrowed_funds_only":
            status, note = "met", "Fixed rate loan; the charge applies only to pre-closure with borrowed funds, which paragraph 163 does not bar."
        else:
            status, note = "breached", "Fixed rate loan; the charge also applies to pre-closure from the borrower's own sources, barred by paragraph 163(2)."
        return ToolResult(check="prepayment_charge", status=status, rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"],
                          values={"rate_type": case.rate_type, "charge_levied": case.prepayment_charge_levied,
                                  "charge_scope": case.prepayment_charge_scope}, note=note)
    rule = rules.get_rule("BANK-PREPAY-FLOATING-INDIVIDUAL")
    start = date.fromisoformat(rule["values"]["para_353_applies_to_loans_sanctioned_or_renewed_on_or_after"])
    new_regime = case.sanction_date >= start
    paragraph = "CB-RBC/353" if new_regime else "CB-RBC/352"
    barred = case.rate_type == "floating" and (
        case.borrower_type == "individual" or (new_regime and case.borrower_is_mse))
    if not case.prepayment_charge_levied:
        status, note = "met", "No prepayment charge is levied."
    elif barred:
        status, note = "breached", f"A charge is levied on a floating rate loan where paragraph {paragraph.split('/')[1]} bars it."
    else:
        status, note = "met", "Not a case where the charge is barred; paragraph 353(5) leaves it to the bank's approved policy."
    return ToolResult(check="prepayment_charge", status=status, rule_id=rule["rule_id"], paragraph_id=paragraph,
                      values={"rate_type": case.rate_type, "borrower_type": case.borrower_type, "charge_levied": case.prepayment_charge_levied,
                              "sanction_date": str(case.sanction_date), "regime_start": str(start)}, note=note)


# ------------------------------------------------------------------ disclosure checks
def check_kfs(case: Case) -> ToolResult:
    rule = rules.get_rule("HFC-KFS" if case.lender_type == "hfc" else "BANK-KFS")
    start = date.fromisoformat(rule["values"]["applies_to_loans_sanctioned_on_or_after"])
    base = dict(check="key_facts_statement", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"])
    values = {"kfs_provided": case.kfs_provided, "sanction_date": str(case.sanction_date), "rule_start": str(start)}
    if case.sanction_date < start:
        return ToolResult(**base, status="not_applicable", values=values, note=f"The rule covers loans sanctioned on or after {start}.")
    return ToolResult(**base, status=TRI[case.kfs_provided], values=values,
                      note=f"Key facts statement provided before the loan contract: {case.kfs_provided}.")


def check_guarantor(case: Case) -> ToolResult:
    if not case.guarantor_present:
        return ToolResult(check="guarantor_disclosure", status="not_applicable", note="No guarantor in this case.")
    if case.lender_type == "bank" or not rules.is_verified("HFC-GUARANTOR-DISCLOSURE"):
        return ToolResult(check="guarantor_disclosure", status="unknown",
                          note="The guarantor disclosure rule for banks is unverified in the rules file, so it is reported as unknown.")
    rule = rules.get_rule("HFC-GUARANTOR-DISCLOSURE")
    return ToolResult(check="guarantor_disclosure", status=TRI[case.guarantor_disclosure], rule_id=rule["rule_id"],
                      paragraph_id=rule["paragraph_id"], values={"guarantor_disclosure": case.guarantor_disclosure or "not_recorded"},
                      note=f"Guarantor informed of the seven items in paragraph 158: {case.guarantor_disclosure or 'not_recorded'}.")


def check_valuation(case: Case) -> ToolResult:
    values = {"loan_amount": case.loan_amount, "valuation_reports": case.valuation_reports,
              "independent_valuation_reports": case.independent_valuation_reports}
    if case.lender_type == "hfc":
        rule = rules.get_rule("HFC-VALUATION-ANNEX-V")
        v = rule["values"]
        need, need_indep = 1, 0
        if case.loan_amount >= v["two_independent_valuation_reports_if_loan_at_least"]:
            need, need_indep = 2, 2
        elif case.loan_amount >= v["two_valuation_reports_if_loan_at_least"]:
            need, need_indep = 2, 1
    else:
        rule = rules.get_rule("BANK-VALUATION")
        big = case.property_value >= rule["values"]["two_valuations_if_property_value_at_least"]
        need, need_indep = (2, 2) if big else (1, 1)
    values.update(reports_required=need, independent_reports_required=need_indep)
    base = dict(check="valuation", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values)
    if case.valuation_reports == 0:
        return ToolResult(**base, status="needs_information", note="No valuation report is on file.")
    ok = case.valuation_reports >= need and case.independent_valuation_reports >= need_indep
    return ToolResult(**base, status="met" if ok else "breached",
                      note=f"{case.valuation_reports} report(s), {case.independent_valuation_reports} independent; the rule needs {need}, with {need_indep} independent.")


def check_kyc(case: Case) -> ToolResult:
    rule = rules.get_rule("HFC-KYC-CDD" if case.lender_type == "hfc" else "BANK-KYC-CDD")
    missing = [name for name, val in (("PAN or Form 60", case.pan_or_form60), ("identity and address proof", case.identity_address_proof))
               if val != "provided"]
    return ToolResult(check="kyc", status="needs_information" if missing else "met", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"],
                      values={"pan_or_form60": case.pan_or_form60, "identity_address_proof": case.identity_address_proof},
                      note=("Missing: " + ", ".join(missing) + ".") if missing else "PAN or Form 60 and identity proof are on file.")


def check_rate_reset(case: Case) -> ToolResult:
    rule = rules.get_rule("HFC-RATE-RESET" if case.lender_type == "hfc" else "BANK-RATE-RESET")
    base = dict(check="rate_reset_communication", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"])
    if case.rate_type != "floating" or case.borrower_type != "individual":
        return ToolResult(**base, status="not_applicable", note="The rule covers floating rate instalment loans to individuals.")
    return ToolResult(**base, status=TRI[case.rate_reset_impact_communicated],
                      values={"rate_reset_impact_communicated": case.rate_reset_impact_communicated},
                      note=f"Impact of a benchmark rate change communicated at sanction: {case.rate_reset_impact_communicated}.")


def check_related_party(case: Case) -> ToolResult:
    if case.related_party == "none":
        return ToolResult(check="related_party", status="not_applicable", note="The borrower is not declared as a related party.")
    values = {"related_party": case.related_party, "loan_amount": case.loan_amount, "board_sanction": case.board_sanction}
    if case.lender_type == "bank":
        if case.related_party == "director":
            rule = rules.get_rule("BANK-RELATED-PARTY-DIRECTORS")
            return ToolResult(check="related_party", status="breached", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values,
                              note="A bank is prohibited from granting loans to its own directors (paragraph 14); exceptions in paragraph 15A do not cover a home loan.")
        rule = rules.get_rule("BANK-RELATED-PARTY-THRESHOLD")
        if case.case_date < date.fromisoformat(str(rule["effective_date"])) or case.bank_asset_size_crore is None:
            return ToolResult(check="related_party", status="needs_information", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values,
                              note="The bank's asset size is needed to pick the materiality threshold, or the rule was not yet in force.")
        size = case.bank_asset_size_crore
        ceiling = next(r["ceiling"] for r in rule["values"]["materiality_threshold_ceiling_by_bank_asset_size_crore"]
                       if ("asset_size_above" in r and size > r["asset_size_above"])
                       or ("asset_size_from" in r and r["asset_size_from"] <= size <= r["asset_size_up_to"])
                       or ("asset_size_below" in r and size < r["asset_size_below"]))
    else:
        rule = rules.get_rule("HFC-RELATED-PARTY")
        if case.case_date < date.fromisoformat(str(rule["effective_date"])):
            return ToolResult(check="related_party", status="unknown", values=values,
                              note=f"The verified related party rule took effect on {rule['effective_date']}; no verified rule covers an earlier case date.")
        key = {"upper": "upper_and_top_layer", "middle": "middle_layer", "base": "base_layer"}.get(case.hfc_layer or "")
        if key is None:
            return ToolResult(check="related_party", status="needs_information", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values,
                              note="The HFC's layer is needed to pick the materiality threshold.")
        ceiling = rule["values"]["materiality_threshold_ceiling"][key]
    values["materiality_threshold_ceiling"] = ceiling
    base = dict(check="related_party", rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"], values=values)
    if case.loan_amount <= ceiling:
        return ToolResult(**base, status="met", note="The loan is within the materiality threshold ceiling, so Board sanction is not required by this rule.")
    status = {"yes": "met", "no": "breached"}.get(case.board_sanction or "", "needs_information")
    return ToolResult(**base, status=status, note=f"The loan is above the materiality threshold ceiling; Board sanction recorded: {case.board_sanction or 'not recorded'}.")


def check_mitc(case: Case) -> ToolResult:
    if case.lender_type != "hfc" or case.borrower_type != "individual":
        return ToolResult(check="mitc", status="not_applicable", note="The MITC paragraph read applies to HFC housing loans to individual borrowers.")
    rule = rules.get_rule("HFC-MITC")
    return ToolResult(check="mitc", status=TRI[case.mitc_signed], rule_id=rule["rule_id"], paragraph_id=rule["paragraph_id"],
                      values={"mitc_signed": case.mitc_signed}, note=f"MITC document executed and copy given to the borrower: {case.mitc_signed}.")


# ------------------------------------------------------------------ registry
CHECK_TOOL: dict[str, str] = {
    "ltv_cap": "ltv_calculator",
    "risk_weight": "risk_weight_lookup",
    "prepayment_charge": "prepayment_checker",
    "key_facts_statement": "disclosure_checker",
    "guarantor_disclosure": "guarantor_checker",
    "valuation": "valuation_checker",
    "kyc": "kyc_checker",
    "rate_reset_communication": "rate_reset_checker",
    "related_party": "related_party_checker",
    "mitc": "mitc_checker",
}
TOOLS: dict[str, Callable[[Case], ToolResult]] = {
    "ltv_cap": check_ltv_cap,
    "risk_weight": check_risk_weight,
    "prepayment_charge": check_prepayment,
    "key_facts_statement": check_kfs,
    "guarantor_disclosure": check_guarantor,
    "valuation": check_valuation,
    "kyc": check_kyc,
    "rate_reset_communication": check_rate_reset,
    "related_party": check_related_party,
    "mitc": check_mitc,
}


def run_tool(check: str, case: Case) -> ToolResult:
    """Only registered tools can run. An unknown name raises, so a prompt cannot make a tool run other code."""
    if check not in TOOLS:
        raise KeyError(f"unknown tool for check '{check}'")
    try:
        return TOOLS[check](case)
    except rules.UnverifiedRule as exc:
        return ToolResult(check=check, status="unknown", note=str(exc))


def required_checks(case: Case) -> list[str]:
    """Checks a full rule check must contain for this case. The critic uses this to catch a missing check."""
    checks = ["ltv_cap", "risk_weight", "prepayment_charge", "key_facts_statement", "guarantor_disclosure", "valuation", "kyc",
              "rate_reset_communication"]
    if case.lender_type == "hfc":
        checks.append("mitc")
    if case.related_party != "none":
        checks.append("related_party")
    return checks
