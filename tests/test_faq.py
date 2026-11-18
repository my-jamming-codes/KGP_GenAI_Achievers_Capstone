"""FAQ answers come only from the verified rules file; unverified rules are never answered."""
from src import rules
from src.app import faq


def test_every_rule_record_appears_once():
    shown = [e["rule_id"] for item in faq.rule_faq() for e in item["entries"]]
    assert sorted(shown) == sorted(r["rule_id"] for r in rules.all_rules(include_unverified=True))


def test_every_theme_has_a_written_question():
    themes = {r["theme"] for r in rules.all_rules(include_unverified=True)}
    assert themes <= set(faq.THEME_QUESTIONS)


def test_verified_answers_are_the_rules_file_quotes():
    by_id = {r["rule_id"]: r for r in rules.all_rules(include_unverified=True)}
    for item in faq.rule_faq():
        for e in item["entries"]:
            if e["status"] == "verified":
                assert e["quote"] and e["quote"] == by_id[e["rule_id"]]["quote"]
                assert e["paragraph_id"]


def test_unverified_rule_has_no_answer():
    bank = faq.filter_faq(faq.rule_faq(), "guarantor", "bank")
    [entry] = bank[0]["entries"]
    assert entry["status"] == "unverified" and entry["quote"] == [] and entry["unverified_reason"]


def test_filters():
    hfc = faq.filter_faq(faq.rule_faq(), "", "hfc")
    assert all(e["lender_type"] == "hfc" for i in hfc for e in i["entries"])
    assert [i["theme"] for i in faq.filter_faq(faq.rule_faq(), "stamp duty")] == ["loan_to_value_computation_and_stamp_duty"]
    assert faq.filter_faq(faq.rule_faq(), "zzz-no-match") == []
    assert faq.filter_workspace("share this screen") and faq.filter_workspace("zzz-no-match") == []


def test_workspace_answers_state_no_rbi_figures():
    import re
    for q, a in faq.WORKSPACE_FAQ:
        assert not re.search(r"\d+\s*(%|percent|lakh|crore)", q + a), q
