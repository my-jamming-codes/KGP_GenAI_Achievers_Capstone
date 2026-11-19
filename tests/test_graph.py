"""The agent flow runs as a LangGraph state graph: its nodes, its critic branch, and its loop guards."""
from src.agents import graph as G
from src.agents import loop
from src.llm import FakeLLM
from src.tracing.tracer import read_traces
from tests.conftest import honest_responder, make_case


def edges():
    g = G.GRAPH.get_graph()
    return {(e.source, e.target, bool(e.conditional)) for e in g.edges}


def test_graph_has_one_node_per_agent_step():
    nodes = set(G.GRAPH.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"guard", "planner", "executor", "judge", "critic", "revise", "finalize", "drafter",
                     "lookup_guard", "search", "lookup_draft", "lookup_critic"}


def test_critic_verdict_is_a_conditional_edge_back_to_the_executor():
    e = edges()
    assert ("critic", "revise", True) in e and ("critic", "finalize", True) in e
    assert ("revise", "executor", True) in e and ("executor", "judge", False) in e and ("judge", "critic", False) in e
    assert ("lookup_critic", "lookup_draft", True) in e            # one grounded redraft on the lookup path


def test_retry_goes_through_the_graph_cycle(index, tmp_path):
    case = make_case(loan_amount=6400000, property_value=7600000)
    out = loop.run("Check the LTV cap for this case.", case=case, index=index, trace_path=tmp_path / "t.jsonl",
                   llm=FakeLLM(honest_responder(case, ["ltv_cap"], wrong_first={"ltv_cap": "met"})))
    agents = [s["agent"] for s in read_traces(tmp_path / "t.jsonl")[0]["steps"]]
    assert agents == ["guard", "planner", "executor", "judge", "critic", "executor", "judge", "critic", "drafter"]
    assert out.results[0].status == "breached"


def test_graph_recursion_limit_stops_a_runaway_loop(index, tmp_path, monkeypatch):
    """A second guard beside the 10-step cap: if a routing bug ever looped, LangGraph's recursion limit ends the run."""
    monkeypatch.setattr(G, "RECURSION_LIMIT", 3)
    case = make_case()
    out = loop.run("Check the LTV cap for this case.", case=case, index=index, trace_path=tmp_path / "t.jsonl",
                   llm=FakeLLM(honest_responder(case, ["ltv_cap"])))
    assert out.stopped == "step_cap" and out.answer.startswith("I stopped")


def test_mermaid_diagram_is_available():
    assert "critic" in G.mermaid() and "revise" in G.mermaid()
