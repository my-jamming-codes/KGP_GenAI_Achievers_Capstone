"""Persistent state across turns, and error handling for tool failures."""
from src.agents import loop
from src.agents import steps as S
from src.agents.memory import ConversationMemory
from src.llm import FakeLLM
from src.tools import checks as tools
from tests.conftest import honest_responder, make_case


def lookup_responder(answers):
    """Plans a lookup with the lender type named in the question; drafts from `answers` in order."""
    def respond(schema, system, user):
        if schema == "PlannerReply":
            q = user.split("\n")[0].lower()
            lender = "bank" if "bank" in q else "hfc"
            return {"intent": "rule_lookup", "lender_type": lender, "checks": [], "search_query": "guarantor informed liability"}
        return answers.pop(0)
    return respond


def ask(question, responder, index, tmp_path, memory, **kw):
    llm = FakeLLM(responder)
    out = loop.run(question, llm=llm, index=index, user="officer.kiran", trace_path=tmp_path / "t.jsonl", memory=memory, **kw)
    return out, llm


def test_turns_persist_across_memory_instances(tmp_path):
    ConversationMemory(tmp_path).append("officer.kiran", "HL-2048", {"question": "PAN ABCPV1234F ok?", "answer": "yes", "trace_id": "t1"})
    (tmp_path / "officer.kiran__HL-2048.jsonl").open("a").write("{broken json\n")
    turns = ConversationMemory(tmp_path).load("officer.kiran", "HL-2048")   # a new instance, as after an app restart
    assert len(turns) == 1 and turns[0]["trace_id"] == "t1"
    assert "ABCPV1234F" not in turns[0]["question"]                         # stored masked
    assert ConversationMemory(tmp_path).load("officer.kiran", "HL-2041") == []   # one conversation per application


def test_memory_keeps_only_recent_turns(tmp_path):
    mem = ConversationMemory(tmp_path, keep=3)
    for i in range(5):
        mem.append("u", None, {"question": f"q{i}", "answer": "a", "trace_id": str(i)})
    assert [t["question"] for t in mem.load("u", None)] == ["q2", "q3", "q4"]


def test_every_turn_is_saved_with_its_results(index, tmp_path):
    mem = ConversationMemory(tmp_path / "mem")
    case = make_case()
    out, _ = ask("Check the LTV cap for this case.", honest_responder(case, ["ltv_cap"]), index, tmp_path, mem, case=case,
                 application_id="HL-2048")
    [turn] = mem.load("officer.kiran", "HL-2048")
    assert turn["trace_id"] == out.trace_id and turn["results"] == {"ltv_cap": "met"} and turn["lender_type"] == "hfc"


def test_follow_up_uses_the_previous_turn(index, tmp_path):
    """'And for a bank?' after an HFC guarantor question is about the bank guarantor rule, which is unverified."""
    mem = ConversationMemory(tmp_path / "mem")
    first, _ = ask("What must a guarantor to a housing finance company loan be told?",
                   lookup_responder([{"answer": "The guarantor should be informed about their liability as guarantor.", "citations": ["HFC-MD/158"]}]),
                   index, tmp_path, mem)
    assert first.citations == ["HFC-MD/158"]
    second, llm = ask("And for a bank?", lookup_responder([]), index, tmp_path, mem)
    assert "unverified" in second.answer and "guarantor" in second.answer and llm.calls == 1   # planner only; no drafting
    assert "earlier turns of this conversation" in llm.prompts[0][2]                          # history reached the planner
    assert mem.load("officer.kiran", None)[-1]["notes"] == ["follow-up to the previous question"]


def test_a_new_question_does_not_use_history(index, tmp_path):
    mem = ConversationMemory(tmp_path / "mem")
    mem.append("officer.kiran", None, {"question": "What must a guarantor to a bank loan be told?", "answer": "unknown", "trace_id": "t0"})
    out, llm = ask("What must a guarantor to a housing finance company loan be told?",
                   lookup_responder([{"answer": "Their liability as guarantor.", "citations": ["HFC-MD/158"]}]), index, tmp_path, mem)
    assert out.citations == ["HFC-MD/158"] and "earlier turns" not in llm.prompts[0][2]


def test_follow_up_detection():
    assert S.is_follow_up("And for a bank?") and S.is_follow_up("What about prepayment?") and S.is_follow_up("Why is that rule breached?")
    assert not S.is_follow_up("What is the LTV cap for an HFC loan of ₹40 lakh?")


def test_a_failing_tool_is_recorded_and_the_run_continues(index, tmp_path, monkeypatch):
    def broken(case):
        raise ValueError("property value missing")

    case = make_case()
    honest = honest_responder(case, ["ltv_cap", "risk_weight"])

    def responder(schema, system, user):   # the fake model judges the broken check as needing information
        if schema not in ("PlannerReply", "DraftReply") and "Rule check: ltv_cap" in user:
            return {"status": "needs_information", "paragraph_id": "", "limit_read": None, "reason": "tool failed"}
        return honest(schema, system, user)

    monkeypatch.setitem(tools.TOOLS, "ltv_cap", broken)
    out = loop.run("Check the LTV cap and the risk weight for this case.", case=case,
                   llm=FakeLLM(responder), index=index, trace_path=tmp_path / "t.jsonl")
    by = {r.check: r for r in out.results}
    assert not out.stopped and by["ltv_cap"].status == "needs_information" and by["risk_weight"].status == "met"
    from src.tracing.tracer import read_traces
    assert "property value missing" in read_traces(tmp_path / "t.jsonl")[0]["outcome"]["tool_errors"][0]


def test_follow_up_lender_type_is_decided_in_code(index, tmp_path):
    """The real 8B planner kept 'hfc' from history for 'And for a bank?'. The question's own lender type wins."""
    mem = ConversationMemory(tmp_path / "mem")
    ask("What must a guarantor to a housing finance company loan be told?",
        lookup_responder([{"answer": "Their liability as guarantor.", "citations": ["HFC-MD/158"]}]), index, tmp_path, mem)

    def confused_planner(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_lookup", "lender_type": "hfc", "checks": [], "search_query": "guarantor"}
        raise AssertionError("the drafter must not be called for the unverified bank rule")

    out, llm = ask("And for a bank?", confused_planner, index, tmp_path, mem)
    assert "unverified" in out.answer and out.citations == [] and llm.calls == 1
    assert mem.load("officer.kiran", None)[-1]["lender_type"] == "bank"


def test_follow_up_without_a_lender_keeps_the_previous_one():
    assert S.lender_named("And for a bank?") == "bank" and S.lender_named("What about HFCs?") == "hfc"
    assert S.lender_named("What about prepayment?") is None and S.lender_named("bank or HFC?") is None
