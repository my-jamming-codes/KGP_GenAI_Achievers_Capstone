"""Unit tests for the rule check tools. Expected values are worked out here from rules.yaml, not from the gold file."""
from datetime import date

import pytest

from src import rules
from src.schemas import Case
from src.tools import checks as t

L = 100000


def case(**kw):
    base = dict(lender_type="hfc", hfc_layer="middle", loan_amount=40 * L, property_value=55 * L, case_date=date(2026, 11, 12))
    base.update(kw)
    return Case(**base)


def test_ltv_is_computed_to_two_decimals():
    assert t.compute_ltv(case(loan_amount=64 * L, property_value=76 * L))["ltv_pct"] == 84.21


@pytest.mark.parametrize("lender,loan,value,status", [
    ("hfc", 64 * L, 80 * L, "met"),          # 80% at the 80% cap
    ("hfc", 27 * L, 30 * L, "met"),          # 90% at the 90% cap
    ("hfc", 28 * L, 30 * L, "breached"),     # 93.33% over 90%
    ("hfc", 76 * L, 100 * L, "breached"),    # 76% over 75%
    ("bank", 30 * L, 34 * L, "met"),         # ₹30 lakh is in the first band
    ("bank", 31 * L, 36 * L, "breached"),    # 86.11% over 80%
    ("bank", 75 * L, 95 * L, "met"),         # ₹75 lakh is in the middle band, 78.95%
])
def test_ltv_cap_bands(lender, loan, value, status):
    r = t.check_ltv_cap(case(lender_type=lender, loan_amount=loan, property_value=value))
    assert r.status == status
    assert r.paragraph_id == ("HFC-MD/58" if lender == "hfc" else "CB-CF/111")


def test_stamp_duty_added_only_up_to_ten_lakh():
    small = t.check_ltv_cap(case(loan_amount=850000, property_value=900000, property_cost=900000, stamp_duty_registration=60000))
    assert small.values["stamp_duty_added"] and small.values["ltv_pct"] == 88.54 and small.status == "met"
    big = t.check_ltv_cap(case(loan_amount=1100000, property_value=1200000, property_cost=1200000, stamp_duty_registration=80000))
    assert not big.values["stamp_duty_added"] and big.values["ltv_pct"] == 91.67 and big.status == "breached"


def test_ltv_cap_not_applicable_to_non_individual():
    assert t.check_ltv_cap(case(borrower_type="non_individual")).status == "not_applicable"


@pytest.mark.parametrize("lender,loan,value,weight", [
    ("hfc", 20 * L, 25 * L, 35), ("hfc", 20 * L, 23 * L, 50), ("hfc", 45 * L, 60 * L, 35), ("hfc", 120 * L, 160 * L, 50),
    ("bank", 25 * L, 30 * L, 50), ("bank", 50 * L, 70 * L, 35), ("bank", 90 * L, 130 * L, 50),
])
def test_risk_weight_current(lender, loan, value, weight):
    r = t.check_risk_weight(case(lender_type=lender, loan_amount=loan, property_value=value))
    assert r.status == "met" and r.values["risk_weight_pct"] == weight


def test_bank_risk_weight_switches_on_1_april_2027():
    kw = dict(lender_type="bank", loan_amount=39 * L, property_value=50 * L)
    before = t.check_risk_weight(case(case_date=date(2027, 3, 31), **kw))
    after = t.check_risk_weight(case(case_date=date(2027, 4, 1), **kw))
    third = t.check_risk_weight(case(case_date=date(2027, 4, 1), housing_loan_number=3, **kw))
    assert (before.values["risk_weight_pct"], before.paragraph_id) == (35, "CB-CAP/55-T12")
    assert (after.values["risk_weight_pct"], after.paragraph_id) == (30, "CB-SA-2027/16-5-ii")
    assert third.values["risk_weight_pct"] == 45


def test_risk_weight_outside_bands():
    assert t.check_risk_weight(case(loan_amount=28 * L, property_value=30 * L)).status == "not_applicable"


def test_amendment_chain_lists_both_bank_risk_weight_rules():
    chain = t.amendment_chain("risk_weight_by_ltv", "bank")
    assert [c["rule_id"] for c in chain] == ["BANK-RW-CURRENT", "BANK-RW-2027"]
    assert t.rule_in_force("risk_weight_by_ltv", "bank", date(2026, 1, 1))["rule_id"] == "BANK-RW-CURRENT"


@pytest.mark.parametrize("kw,status,para", [
    (dict(prepayment_charge_levied=False), "met", "HFC-MD/163"),
    (dict(prepayment_charge_levied=True, prepayment_charge_scope="any_source"), "breached", "HFC-MD/163"),
    (dict(rate_type="fixed", prepayment_charge_levied=True, prepayment_charge_scope="borrowed_funds_only"), "met", "HFC-MD/163"),
    (dict(rate_type="fixed", prepayment_charge_levied=True, prepayment_charge_scope="any_source"), "breached", "HFC-MD/163"),
    (dict(lender_type="bank", prepayment_charge_levied=True), "breached", "CB-RBC/353"),
    (dict(lender_type="bank", prepayment_charge_levied=True, sanction_date=date(2025, 6, 10)), "breached", "CB-RBC/352"),
    (dict(lender_type="bank", prepayment_charge_levied=True, rate_type="fixed"), "met", "CB-RBC/353"),
    (dict(lender_type="bank", prepayment_charge_levied=True, borrower_type="non_individual"), "met", "CB-RBC/353"),
    (dict(lender_type="bank", prepayment_charge_levied=True, borrower_type="non_individual", borrower_is_mse=True), "breached", "CB-RBC/353"),
])
def test_prepayment(kw, status, para):
    r = t.check_prepayment(case(**kw))
    assert (r.status, r.paragraph_id) == (status, para)


def test_kfs_states_and_start_date():
    assert t.check_kfs(case(kfs_provided="no")).status == "breached"
    assert t.check_kfs(case(kfs_provided="not_recorded")).status == "needs_information"
    old = t.check_kfs(case(lender_type="bank", kfs_provided="no", case_date=date(2024, 8, 20)))
    assert (old.status, old.paragraph_id) == ("not_applicable", "CB-RBC/348")


def test_guarantor_rule_is_unknown_for_banks():
    assert t.check_guarantor(case(guarantor_present=False)).status == "not_applicable"
    assert t.check_guarantor(case(guarantor_present=True, guarantor_disclosure="no")).status == "breached"
    assert t.check_guarantor(case(guarantor_present=True)).status == "needs_information"
    bank = t.check_guarantor(case(lender_type="bank", guarantor_present=True, guarantor_disclosure="yes"))
    assert bank.status == "unknown" and bank.paragraph_id is None


def test_unverified_rule_cannot_be_read():
    with pytest.raises(rules.UnverifiedRule):
        rules.get_rule("BANK-GUARANTOR-DISCLOSURE")


def test_valuation_thresholds_differ_by_lender():
    one = dict(loan_amount=60 * L, property_value=80 * L, valuation_reports=1, independent_valuation_reports=1)
    assert t.check_valuation(case(**one)).status == "breached"
    assert t.check_valuation(case(lender_type="bank", **one)).status == "met"
    big = dict(loan_amount=78 * L, property_value=100 * L, valuation_reports=2, independent_valuation_reports=1)
    assert t.check_valuation(case(**big)).status == "breached"
    assert t.check_valuation(case(valuation_reports=0, independent_valuation_reports=0)).status == "needs_information"


def test_kyc_rate_reset_mitc():
    assert t.check_kyc(case(pan_or_form60="not_provided")).status == "needs_information"
    assert t.check_rate_reset(case(rate_reset_impact_communicated="no")).status == "breached"
    assert t.check_rate_reset(case(rate_type="fixed")).status == "not_applicable"
    assert t.check_mitc(case(mitc_signed="not_recorded")).status == "needs_information"
    assert t.check_mitc(case(lender_type="bank")).status == "not_applicable"


def test_related_party():
    assert t.check_related_party(case()).status == "not_applicable"
    bank = t.check_related_party(case(lender_type="bank", related_party="director"))
    assert (bank.status, bank.paragraph_id) == ("breached", "CB-CRM/14")
    assert t.check_related_party(case(related_party="director")).status == "met"
    over = dict(related_party="director", hfc_layer="base", loan_amount=150 * L, property_value=220 * L)
    assert t.check_related_party(case(board_sanction="no", **over)).status == "breached"
    assert t.check_related_party(case(board_sanction="yes", **over)).status == "met"
    assert t.check_related_party(case(case_date=date(2026, 2, 1), **over)).status == "unknown"


def test_lender_picker_and_registry():
    assert t.pick_lender_type(case())["rulebook"] == "HFC-MD"
    assert t.pick_lender_type(case(lender_type="bank"))["rulebook"] == "CB-CF"
    assert t.lender_type_from_label("Housing finance company") == "hfc"
    with pytest.raises(KeyError):
        t.run_tool("os.system", case())
    assert "guarantor_disclosure" in t.required_checks(case(guarantor_present=True))
