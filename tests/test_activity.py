"""Live activity: each run reports its agent steps, model calls, searches and tools, in order, and only to itself."""
from src.agents import loop
from src.llm import FakeLLM
from src.tracing import activity
from tests.conftest import honest_responder, make_case


def run_with_events(index, tmp_path):
    case = make_case()
    events = []
    with activity.listen(events.append):
        out = loop.run("Check the LTV cap for this case.", case=case, index=index, trace_path=tmp_path / "t.jsonl",
                       llm=FakeLLM(honest_responder(case, ["ltv_cap"])), user="officer.kiran")
    return out, events


def test_a_run_reports_what_it_is_doing(index, tmp_path):
    out, events = run_with_events(index, tmp_path)
    agent_starts = [e["node"] for e in events if e["kind"] == "agent" and e["phase"] == "start"]
    assert agent_starts == ["guard", "planner", "executor", "judge", "critic", "finalize", "drafter"]
    llm = [e for e in events if e["kind"] == "llm" and e["phase"] == "end"]
    assert [e["schema"] for e in llm] == ["PlannerReply", "JudgeReply", "DraftReply"]
    assert all(e["tokens"] > 0 and e["ms"] >= 0 for e in llm)
    judge = next(e for e in llm if e["schema"] == "JudgeReply")
    assert judge["check"] == "ltv_cap" and judge["label"] == "Model thinking: judging a rule"
    tool = next(e for e in events if e["kind"] == "tool" and e["phase"] == "end")
    assert tool["result"] == out.results[0].status
    assert all(e["run"] == out.trace_id and e["user"] == "officer.kiran" for e in events)


def test_finished_calls_are_logged_without_prompt_text(index, tmp_path):
    out, _ = run_with_events(index, tmp_path)
    logged = activity.read()
    assert logged and all(e["phase"] == "end" for e in logged)
    assert not any("Case facts" in str(e) for e in logged)          # no prompt content in the log
    assert {e["kind"] for e in logged} >= {"agent", "llm", "tool"}


def test_listeners_only_hear_their_own_run():
    heard = []
    with activity.listen(heard.append):
        activity.emit("agent", "inside")
    activity.emit("agent", "outside")
    assert [e["label"] for e in heard] == ["inside"]


def test_a_failing_listener_never_breaks_a_run(index, tmp_path):
    def broken(event):
        raise RuntimeError("display failed")

    with activity.listen(broken):
        out = loop.run("Check the LTV cap for this case.", case=make_case(), index=index, trace_path=tmp_path / "t.jsonl",
                       llm=FakeLLM(honest_responder(make_case(), ["ltv_cap"])))
    assert not out.stopped and out.results


def test_search_queries_are_logged_masked():
    activity.emit("vector", "Vector search", query=activity.safe_query("PAN ABCPV1234F loan to value"))
    assert "ABCPV1234F" not in str(activity.read(limit=1))


# ------------------------------------------------------------------ the Activity log page groups calls by question
def test_calls_are_listed_under_the_step_that_made_them(index, tmp_path):
    out, _ = run_with_events(index, tmp_path)
    [run] = activity.read_runs(user="officer.kiran", titles={out.trace_id: {"title": "Check the LTV cap", "outcome": "1 rule results"}})
    shape = [(r["kind"], r["depth"]) for r in run["rows"]]
    assert shape == [("agent", 0), ("agent", 0), ("llm", 1), ("agent", 0), ("tool", 1), ("agent", 0), ("llm", 1),
                     ("agent", 0), ("agent", 0), ("agent", 0), ("llm", 1)]
    assert [r["label"] for r in run["rows"][:3]] == ["Checking the request: refusals, injected instructions, cache",
                                                     "Planning which RBI rules to check", "Model thinking: planning the checks"]
    assert run["run"] == out.trace_id and run["title"] == "Check the LTV cap" and run["outcome"] == "1 rule results"
    assert run["llm_calls"] == 3 and run["tokens"] > 0 and run["user"] == "officer.kiran"
    assert run["rows"][2]["detail"] == "" or "rule:" not in run["rows"][2]["detail"]      # the planner call is not about one rule
    judge_call = run["rows"][6]
    assert "rule: ltv_cap" in judge_call["detail"]


def emit_run(run, user, title=None, label="Vector search"):
    with activity.context(run=run, user=user, title=title):
        activity.emit("embed", "Embedding the query", ms=5, model="m")
        activity.emit("vector", label, ms=10, hits=3, query="stamp duty", filter=["hfc"])


def test_groups_are_newest_first_and_can_be_limited_to_one_user():
    emit_run("r1", "officer.kiran", "Retrieval lab: stamp duty")
    emit_run("r2", "approver.sana")
    emit_run("r3", "officer.kiran", "Retrieval lab: guarantor")
    assert [r["run"] for r in activity.read_runs()] == ["r3", "r2", "r1"]
    mine = activity.read_runs(user="officer.kiran")
    assert [r["run"] for r in mine] == ["r3", "r1"] and [r["title"] for r in mine] == ["Retrieval lab: guarantor", "Retrieval lab: stamp duty"]
    assert len(activity.read_runs(runs=1)) == 1


def test_a_lab_search_has_no_step_above_it_and_shows_its_query():
    emit_run("lab1", "officer.kiran", "Retrieval lab: stamp duty")
    [run] = activity.read_runs()
    assert [(r["kind"], r["depth"]) for r in run["rows"]] == [("embed", 0), ("vector", 0)]
    assert run["rows"][1]["detail"] == "“stamp duty” · only hfc" and run["searches"] == 1


def test_hiding_the_steps_flattens_the_calls(index, tmp_path):
    run_with_events(index, tmp_path)
    [run] = activity.read_runs(kinds={"llm", "tool"})
    assert {r["kind"] for r in run["rows"]} == {"llm", "tool"} and {r["depth"] for r in run["rows"]} == {0}


def test_two_runs_that_overlap_stay_separate():
    with activity.context(run="a", user="u"):
        activity.emit("embed", "A first")
    with activity.context(run="b", user="u"):
        activity.emit("embed", "B first")
    with activity.context(run="a", user="u"):
        activity.emit("vector", "A second")
    runs = {r["run"]: [x["label"] for x in r["rows"]] for r in activity.read_runs()}
    assert runs == {"a": ["A first", "A second"], "b": ["B first"]}


# ------------------------------------------------------------------ the live list is one line per row, nested, with cache hits folded
def ev(kind, label, phase="end", **kw):
    return {"kind": kind, "label": label, "phase": phase, "time": "2026-11-18T10:00:00.000+00:00", **kw}


def test_live_list_has_one_line_per_row_with_calls_indented_under_their_step():
    live = activity.LiveLines()
    live.feed(ev("agent", "Planning which RBI rules to check", "start"))
    live.feed(ev("llm", "Model thinking: planning the checks", "start"))
    assert live.markdown().endswith("  ⏳")                                   # still running
    live.feed(ev("llm", "Model thinking: planning the checks", ms=1750, tokens=731))
    live.feed(ev("agent", "Planning which RBI rules to check", ms=1760))
    lines = live.markdown().split("\n")
    assert len(lines) == 2 and lines[0].startswith("- \U0001f916") and lines[1].startswith("    - \U0001f9e0")
    assert "731 tokens" in lines[1] and "⏳" not in live.markdown()


def test_repeated_cache_hits_fold_into_one_line():
    live = activity.LiveLines()
    live.feed(ev("agent", "Model reading the paragraphs and judging each rule", "start"))
    for _ in range(8):
        live.feed(ev("llm", "Model reply served from cache: judging a rule", status="cache hit"))
    live.feed(ev("llm", "Model thinking: judging a rule", ms=900, tokens=500))
    live.feed(ev("llm", "Model reply served from cache: judging a rule", status="cache hit"))
    lines = live.markdown().split("\n")
    assert len(lines) == 4 and "8 × Model reply served from cache" in lines[1] and "1 ×" not in lines[3]


def test_a_windowed_list_never_starts_indented():
    live = activity.LiveLines()
    live.feed(ev("agent", "Step", ms=5))
    for i in range(5):
        live.feed(ev("tool", f"Tool {i}", ms=1))
    first = live.markdown(last=3).split("\n")[0]
    assert first.startswith("- ") and not first.startswith("    ")


def test_the_log_folds_repeated_cache_hits_too():
    with activity.context(run="c1", user="u"):
        for _ in range(3):
            activity.emit("llm", "Model reply served from cache: judging a rule", status="cache hit")
        activity.emit("agent", "Model reading the paragraphs and judging each rule", ms=4, node="judge", next="critic")
    [run] = activity.read_runs()
    labels = [r["label"] for r in run["rows"]]
    assert labels == ["Model reading the paragraphs and judging each rule", "3 × Model reply served from cache: judging a rule"]


# ------------------------------------------------------------------ evaluation never writes to the activity log
def test_not_logged_keeps_events_out_of_the_log_but_not_out_of_the_live_view():
    heard = []
    with activity.listen(heard.append):
        with activity.not_logged():
            activity.emit("agent", "inside the block", ms=1)
        activity.emit("agent", "after the block", ms=1)
    assert [e["label"] for e in heard] == ["inside the block", "after the block"]      # the live view still hears both
    assert [e["label"] for e in activity.read()] == ["after the block"]                 # only the second is in the log


def test_not_logged_works_as_a_decorator_and_restores_logging_even_after_an_error():
    @activity.not_logged()
    def quiet():
        activity.emit("agent", "decorated", ms=1)
        raise ValueError("boom")

    try:
        quiet()
    except ValueError:
        pass
    activity.emit("agent", "logged again", ms=1)
    assert [e["label"] for e in activity.read()] == ["logged again"]


def test_the_safety_checks_leave_the_activity_log_empty(index):
    from eval import safety_eval as se

    for name, fn in (("pii", se.run_pii), ("injection", se.run_injection), ("refusal", se.run_refusal)):
        for item in se.load(name)[:3]:
            fn(item, index)
    assert activity.read() == []


def test_every_evaluation_entry_point_is_wrapped():
    from eval import chunking_eval, retrieval_eval, run_eval, safety_eval

    for fn in (run_eval.main, retrieval_eval.main, chunking_eval.main, safety_eval.main,
               safety_eval.run_pii, safety_eval.run_injection, safety_eval.run_refusal):
        assert hasattr(fn, "__wrapped__"), fn.__qualname__
