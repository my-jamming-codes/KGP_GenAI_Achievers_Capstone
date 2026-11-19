"""Caching and rate limiting."""
from src.agents import loop
from src.app.ratelimit import RateLimiter
from src.llm import FakeLLM
from src.tracing.tracer import read_traces
from tests.conftest import honest_responder, make_case


def test_same_question_twice_makes_model_calls_once(index, tmp_path):
    case = make_case()
    llm = FakeLLM(honest_responder(case, ["ltv_cap"]))
    cache = loop.AnswerCache(tmp_path / "answers")
    kw = dict(case=case, llm=llm, index=index, cache=cache, trace_path=tmp_path / "t.jsonl")
    first = loop.run("Check the LTV cap for this case.", **kw)
    calls = llm.calls
    second = loop.run("Check the LTV cap for this case.", **kw)
    assert calls > 0 and llm.calls == calls
    assert not first.cache_hit and second.cache_hit and second.results[0].status == first.results[0].status
    traces = read_traces(tmp_path / "t.jsonl")
    assert [t["cache_hit"] for t in traces] == [False, True]


def test_changing_corpus_version_clears_answers(index, tmp_path):
    case = make_case()
    llm = FakeLLM(honest_responder(case, ["ltv_cap"]))
    cache = loop.AnswerCache(tmp_path / "answers")
    kw = dict(case=case, llm=llm, cache=cache, trace_path=tmp_path / "t.jsonl")
    loop.run("Check the LTV cap for this case.", index=index, **kw)
    calls = llm.calls
    index.corpus_version = "test-corpus-2"
    out = loop.run("Check the LTV cap for this case.", index=index, **kw)
    assert not out.cache_hit and llm.calls > calls


def test_shared_fields_and_strategy_are_part_of_the_key(index, tmp_path):
    case = make_case()
    llm = FakeLLM(honest_responder(case, ["ltv_cap"]))
    cache = loop.AnswerCache(tmp_path / "answers")
    kw = dict(case=case, llm=llm, index=index, cache=cache, trace_path=tmp_path / "t.jsonl")
    loop.run("Check the LTV cap for this case.", screen={"application_id": "HL-1", "tab": "Property", "fields": {"a": 1}}, **kw)
    assert not loop.run("Check the LTV cap for this case.", screen={"application_id": "HL-1", "tab": "Property", "fields": {"a": 2}}, **kw).cache_hit
    assert not loop.run("Check the LTV cap for this case.", strategy="C", screen={"application_id": "HL-1", "tab": "Property", "fields": {"a": 1}}, **kw).cache_hit


def test_burst_of_questions_is_refused_politely():
    rl = RateLimiter(per_minute=3)
    results = [rl.allow("s1", now=100.0 + i) for i in range(6)]
    assert [ok for ok, _ in results] == [True, True, True, False, False, False]
    assert "limit of 3 questions a minute" in rl.message(results[3][1])
    assert rl.allow("s2", now=101.0)[0]            # another session is unaffected
    assert rl.allow("s1", now=161.0)[0]            # the window moves on
