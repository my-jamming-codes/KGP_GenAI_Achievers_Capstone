"""Agent loop tests with a fake model: critic catches errors, step cap holds, unverified rules stay unknown."""
from src.agents import loop
from src.llm import Budget, FakeLLM
from src.tracing.tracer import read_traces
from tests.conftest import honest_responder, make_case


def run(case, responder, index, tmp_path, **kw):
    llm = FakeLLM(responder)
    out = loop.run(kw.pop("question", "Check the LTV cap for this case."), case=case, llm=llm, index=index,
                   trace_path=tmp_path / "t.jsonl", **kw)
    return out, llm, read_traces(tmp_path / "t.jsonl")


def test_clean_run_passes_critic_and_writes_one_trace(index, tmp_path):
    case = make_case()
    out, llm, traces = run(case, honest_responder(case, ["ltv_cap"]), index, tmp_path)
    r = out.results[0]
    assert (r.check, r.status, r.paragraph_id, r.confirmed_by_critic) == ("ltv_cap", "met", "HFC-MD/58", True)
    assert out.critic_failures == 0 and len(traces) == 1
    agents = [s["agent"] for s in traces[0]["steps"]]
    assert agents == ["guard", "planner", "executor", "judge", "critic", "drafter"]
    assert traces[0]["steps"][2]["output"]["ltv_cap"]["retrieved"][0]["paragraph_id"]


def test_critic_catches_wrong_rulebook_and_sends_it_back(index, tmp_path):
    """The planner assumes a bank for an HFC case; the critic fails it and the retry cites the HFC paragraph."""
    case = make_case()
    out, llm, traces = run(case, honest_responder(case, ["ltv_cap"]), index, tmp_path, fault="wrong_rulebook")
    assert out.wrong_rulebook_caught and out.critic_failures >= 1
    assert out.results[0].paragraph_id == "HFC-MD/58" and out.results[0].status == "met"
    first_executor = traces[0]["steps"][2]["output"]["ltv_cap"]["result"]["paragraph_id"]
    assert first_executor == "CB-CF/111"
    assert traces[0]["outcome"]["first_pass"]["ltv_cap"]["paragraph_id"] == "CB-CF/111"
    assert out.steps_used <= 10


def test_critic_catches_wrong_status_and_retry_fixes_it(index, tmp_path):
    case = make_case(loan_amount=6400000, property_value=7600000)  # 84.21%, breached
    out, _, traces = run(case, honest_responder(case, ["ltv_cap"], wrong_first={"ltv_cap": "met"}), index, tmp_path)
    assert out.critic_failures == 1 and out.results[0].status == "breached"
    assert traces[0]["outcome"]["first_pass"]["ltv_cap"]["status"] == "met"


def test_unconfirmed_judgement_is_never_shown_as_a_verdict(index, tmp_path):
    case = make_case(loan_amount=6400000, property_value=7600000)

    def stubborn(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_check", "scope": "specific", "lender_type": "hfc", "checks": ["ltv_cap"]}
        if schema == "DraftReply":
            return {"answer": "note", "citations": []}
        return {"status": "met", "paragraph_id": "HFC-MD/58", "reason": "wrong on purpose"}

    out, _, _ = run(case, stubborn, index, tmp_path)
    r = out.results[0]
    assert r.status == "needs_information" and not r.confirmed_by_critic and r.values["ltv_pct"] == 84.21


def test_missing_guarantor_check_is_caught_on_a_full_check(index, tmp_path):
    case = make_case(guarantor_present=True, guarantor_disclosure="no")
    out, _, traces = run(case, honest_responder(case, ["ltv_cap"]), index, tmp_path, force_full=True,
                         question="Run the full RBI rule check.")
    by = {r.check: r for r in out.results}
    assert by["guarantor_disclosure"].status == "breached" and by["guarantor_disclosure"].paragraph_id == "HFC-MD/158"
    assert any(f["kind"] == "missing_check" for s in traces[0]["steps"] if s["agent"] == "critic" for f in s["output"]["failed_checks"])
    assert out.steps_used <= 10


def test_unverified_bank_guarantor_rule_is_unknown_without_a_model_call(index, tmp_path):
    case = make_case(lender_type="bank", guarantor_present=True, guarantor_disclosure="yes")
    out, llm, _ = run(case, honest_responder(case, ["guarantor_disclosure"]), index, tmp_path, draft=False)
    assert out.results[0].status == "unknown" and out.results[0].paragraph_id is None
    assert [p[0] for p in llm.prompts] == ["PlannerReply"]


def test_step_cap_stops_the_run_without_crashing(index, tmp_path, monkeypatch):
    from src.tracing import tracer

    case = make_case()
    real = tracer.Trace.__init__
    monkeypatch.setattr(tracer.Trace, "__init__", lambda self, *a, **k: real(self, *a, **{**k, "max_steps": 3}))
    out, _, traces = run(case, honest_responder(case, ["ltv_cap"]), index, tmp_path)
    assert out.stopped == "step_cap" and "10 step limit" in out.answer and len(traces[0]["steps"]) == 3


def test_token_budget_stops_politely(index, tmp_path):
    case = make_case()
    out, _, _ = run(case, honest_responder(case, ["ltv_cap"]), index, tmp_path, budget=Budget(tokens=60))
    assert out.stopped == "token_budget" and out.answer.startswith("I stopped")


def test_invalid_json_is_retried_then_counted_as_tool_call_error(index, tmp_path):
    case = make_case()
    out, llm, _ = run(case, lambda *a: "not json at all", index, tmp_path)
    assert out.stopped == "tool_call_error" and llm.calls == 3  # first try plus two retries


def test_no_screen_shared_message(index, tmp_path):
    out, llm, _ = run(None, lambda *a: {}, index, tmp_path, question="Is this LTV allowed?")
    assert "No screen is shared" in out.answer and llm.calls == 0


def test_lookup_answer_must_cite_a_retrieved_paragraph(index, tmp_path):
    calls = {"n": 0}

    def responder(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_lookup", "lender_type": "hfc", "checks": [], "search_query": "LTV ratio housing loans individuals"}
        calls["n"] += 1
        if calls["n"] == 1:
            return {"answer": "The cap is 95 percent.", "citations": ["HFC-MD/999"]}
        return {"answer": "Up to ₹30 lakh the LTV may not exceed 90 percent.", "citations": ["HFC-MD/58"]}

    out, _, _ = run(None, responder, index, tmp_path, question="What is the maximum LTV for an HFC loan of ₹25 lakh?")
    assert out.critic_failures == 1 and out.citations == ["HFC-MD/58"] and "90" in out.answer


def test_lookup_about_an_unverified_rule_is_answered_unknown_in_code(index, tmp_path):
    """The model plans a lookup, but is never asked to draft an answer for the unverified bank guarantor rule."""
    def responder(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_lookup", "lender_type": "bank", "checks": [], "search_query": "guarantor"}
        raise AssertionError("the drafter must not be called for an unverified rule")

    out, llm, _ = run(None, responder, index, tmp_path, question="What must a guarantor to a commercial bank home loan be told?")
    assert "unverified" in out.answer and "unknown" in out.answer and out.citations == [] and llm.calls == 1


def test_same_lookup_for_an_hfc_is_still_answered(index, tmp_path):
    def responder(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_lookup", "lender_type": "hfc", "checks": [], "search_query": "guarantor informed liability"}
        return {"answer": "The guarantor should be informed about their liability as guarantor.", "citations": ["HFC-MD/158"]}

    out, _, _ = run(None, responder, index, tmp_path, question="What must a guarantor to a housing finance company loan be told?")
    assert out.citations == ["HFC-MD/158"] and "unverified" not in out.answer


def test_model_hint_is_never_part_of_the_shown_note():
    """The "Report the limit as" instruction is for the critic's retry message, not for the officer."""
    from src.schemas import Case
    from src.tools import checks

    case = Case(lender_type="hfc", case_date="2026-06-01", sanction_date="2026-06-01", loan_amount=2700000, property_value=3000000)
    result = checks.run_tool("ltv_cap", case)
    assert result.hint.startswith("Report the limit as")
    assert "Report the limit" not in result.note


def test_drafter_is_given_the_cited_paragraph_text():
    from types import SimpleNamespace

    from src.agents import steps
    from src.schemas import CheckResult, RetrievedParagraph

    class Index:
        def fetch_paragraph(self, pid):
            texts = ["General wording about loans. " * 80, "Two valuation reports are needed, one from an independent valuer."]
            return [RetrievedParagraph(paragraph_id=pid, doc_key="X", lender_type="hfc", score=1.0, text=t) for t in texts]

    results = [CheckResult(check="valuation", status="needs_information", paragraph_id="X/1", reason="Only one valuation report; independent valuer needed.")]
    block = steps.cited_paragraphs_text(results, SimpleNamespace(injection_detected=False), Index())
    assert 'source="paragraph X/1"' in block and "independent valuer" in block
    assert len(block) < steps.DRAFT_PARAGRAPH_CHARS + 200
