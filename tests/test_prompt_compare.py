"""The Prompt lab runs each strategy once on the same evidence and compares it with the tool."""
from src.llm import FakeLLM
from src.prompts import compare as PC
from tests.conftest import honest_responder, make_case


def test_each_strategy_runs_once_on_the_same_evidence(index, tmp_path):
    case = make_case(loan_amount=6400000, property_value=7600000)   # 84.21%, breached
    honest = honest_responder(case, ["ltv_cap"])

    def responder(schema, system, user):
        if schema == "ExtractReply":
            return {"paragraph_id": "HFC-MD/58", "rule_quote": "above ₹30 lakh and up to ₹75 lakh with LTV ratio exceeding 80 percent",
                    "field_values": ["ltv_pct = 84.21", "loan_amount = 6400000"]}
        if schema == "PlanJudgeReply":
            return {"plan": ["find band", "compare"], "status": "met", "paragraph_id": "HFC-MD/58", "limit_read": "90", "reason": "wrong band",
                    "self_check": {"numbers_match_paragraph": True, "paragraph_is_for_this_lender_type": True}}
        return honest(schema, system, user)

    res = PC.compare(case, "ltv_cap", FakeLLM(responder), index, trace_path=tmp_path / "t.jsonl")
    by = {r["strategy"]: r for r in res["rows"]}
    assert res["tool"]["status"] == "breached" and res["evidence"]
    assert by["A"]["agrees"] and by["B"]["agrees"] and by["B"]["model_calls"] == 2
    assert not by["C"]["agrees"] and by["C"]["problem"] == "status differs from the tool"


def test_a_failed_reply_is_shown_not_raised(index, tmp_path):
    res = PC.compare(make_case(), "ltv_cap", FakeLLM(lambda *a: "not json"), index, strategies=("A",), trace_path=tmp_path / "t.jsonl")
    assert res["rows"][0]["status"] == "error" and not res["rows"][0]["agrees"]
