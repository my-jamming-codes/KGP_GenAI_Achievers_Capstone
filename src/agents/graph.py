"""The agent flow as a LangGraph state graph.

Each agent step is a node; the edges between them are decided by the step's result:

    guard ─► planner ─► executor ─► judge ─► critic ──pass──► finalize ─► drafter ─► END
      │         │          ▲                    │
      │         │          └─ revise ◄── fail ──┘   (once: re-plan if needed, re-run only the failed checks)
      │         └─► lookup_guard ─► search ─► lookup_draft ─► lookup_critic ─► END
      │                                            ▲               │ fail, once
      └─► END (refused, cached, no screen)         └───────────────┘

The nodes call the same step functions as before (src/agents/steps.py); only the control flow lives here.
Every node records itself in the trace, and the trace's 10-step cap still applies inside the graph. The graph's own
recursion limit is a second guard against a loop that never ends.

The run context (case, model client, index, budget, trace, output) travels in the graph state. It is in-memory
only: no LangGraph checkpointer is used, because conversation state is kept by src/agents/memory.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import steps as S
from src.tracing import activity
from src.llm import LLM, Budget, BudgetExceeded, ToolCallError
from src.safety import injection, refusal
from src.schemas import Case, PlannedCheck, RunOutput
from src.tools import checks as tools

NEEDS_SCREEN = re.compile(r"\b(this|the open|the current|my) (ltv|loan|application|case|borrower|applicant|field|value|charge|guarantor|file)\b", re.I)
NO_SCREEN_MESSAGE = ("No screen is shared, so I can't see the application. Turn on 'Share this screen' to ask about it. "
                     "I can still answer general questions about RBI home loan rules.")
RECURSION_LIMIT = 25   # node visits per run; a full check with one retry visits 11


@dataclass
class RunContext:
    """Everything one run needs. Nodes read and update it; the graph only routes."""

    question: str
    case: Optional[Case]
    state: S.State
    out: RunOutput
    llm: LLM
    index: Any
    budget: Budget
    trace: Any
    cache: Any = None
    cache_key: str = ""
    fault: Optional[str] = None
    draft: bool = True
    force_full: bool = False
    retried: bool = False
    failed: list[str] = field(default_factory=list)
    unresolved: set[str] = field(default_factory=set)
    hits: list = field(default_factory=list)            # lookup path: retrieved paragraphs
    reply: Any = None                                    # lookup path: the drafted answer
    problem: Optional[str] = None                        # lookup path: the critic's objection
    redrafted: bool = False
    plan_fails: list[dict] = field(default_factory=list)  # critic objections to the plan itself (rulebook, missing check)


class GraphState(TypedDict, total=False):
    ctx: RunContext
    route: str


# ------------------------------------------------------------------ nodes
def guard(gs: GraphState) -> GraphState:
    """Refusals, injected instructions, the cache and the no-screen case, all in code before any model call."""
    c = gs["ctx"]
    st, out = c.state, c.out
    clean_q, hit = injection.sanitize(c.question)
    st.injection_detected |= hit
    code = refusal.classify(c.question)
    c.trace.step("guard", input=c.question, output={"refusal": code, "injection_detected": hit,
                                                    "follow_up_of": [t.get("trace_id") for t in st.history]},
                 screen_context=st.screen and {"tab": st.screen.get("tab"), "fields": st.screen.get("fields")})
    if code:
        out.refused, out.refusal_reason, out.answer = True, code, refusal.message(code)
        return {"route": "end"}
    st.question = clean_q
    if c.cache is not None:
        cached = c.cache.get(c.cache_key)
        if cached:
            cached.trace_id, cached.cache_hit = c.trace.trace_id, True
            c.out = cached
            c.trace.step("cache", output="answer served from cache")
            return {"route": "end"}
    if c.case is None and NEEDS_SCREEN.search(c.question):
        out.answer = NO_SCREEN_MESSAGE
        return {"route": "end"}
    return {"route": "plan"}


def planner(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    st, out = c.state, c.out
    st.plan = S.plan_step(st, c.llm, c.budget, c.trace)
    if c.force_full and c.case is not None:
        st.plan.intent, st.scope = "rule_check", "full"
    if c.fault == "wrong_rulebook" and c.case is not None:  # labelled fault injection for the demo and tests
        st.plan.lender_type = "bank" if c.case.lender_type == "hfc" else "hfc"
    c.trace.step("planner", input=st.question, output=st.plan.model_dump(), fault_injected=c.fault)
    if st.plan.intent in ("refuse", "out_of_scope"):
        code = "out_of_scope" if st.plan.intent == "out_of_scope" else "compliance_declaration"
        out.refused, out.refusal_reason, out.answer = True, code, refusal.message(code)
        return {"route": "end"}
    if c.case is None or st.plan.intent == "rule_lookup":
        return {"route": "lookup"}
    return {"route": "execute"}


def executor(gs: GraphState) -> GraphState:
    """Run the tools and retrieve paragraphs: every planned check, or on the retry only the failed ones."""
    c = gs["ctx"]
    if c.retried:
        log = S.execute_step(c.state, c.index, only=c.failed, targeted=True)
        c.trace.step("executor", input=c.failed, output=log)
    else:
        log = S.execute_step(c.state, c.index)
        c.trace.step("executor", input=[p.model_dump() for p in c.state.plan.checks], output=log)
    return {"route": "judge"}


def judge(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    st, out = c.state, c.out
    if not c.retried:
        for pc in st.plan.checks:
            st.judgements[pc.check] = S.judge_one(pc.check, st, c.llm, c.budget, c.trace)
        st.first_pass = dict(st.judgements)
        out.first_pass = {k: {"status": j.status, "paragraph_id": j.paragraph_id, "limit_read": j.values_used.get("limit_read")}
                          for k, j in st.judgements.items()}
        c.trace.step("judge", output={k: j.model_dump() for k, j in st.judgements.items()})
    else:
        for check in c.failed:
            st.judgements[check] = S.judge_one(check, st, c.llm, c.budget, c.trace)
            if check not in st.first_pass:      # a check the critic added gets its own correction pass
                fail = S.critic_judgement(check, st)
                if fail:
                    st.feedback[check] = fail["reason"]
                    out.critic_kinds.append(fail["kind"])
                    st.judgements[check] = S.judge_one(check, st, c.llm, c.budget, c.trace)
        c.trace.step("judge", output={k: st.judgements[k].model_dump() for k in c.failed})
    return {"route": "critic"}


def critic(gs: GraphState) -> GraphState:
    """Check every judgement against the tool result and the paragraph. One retry, then unresolved results are withheld."""
    c = gs["ctx"]
    st, out = c.state, c.out
    verdict = S.critic_step(st)
    c.trace.step("critic", output=verdict.model_dump())
    if c.retried:
        c.unresolved = {f["check"] for f in verdict.failed_checks if f["check"] in st.judgements}
        return {"route": "finalize"}
    if verdict.verdict == "pass":
        return {"route": "finalize"}
    out.critic_failures = len(verdict.failed_checks)
    out.critic_kinds = [f["kind"] for f in verdict.failed_checks]
    out.wrong_rulebook_caught = any(f["kind"] == "wrong_rulebook" for f in verdict.failed_checks)
    c.failed = [f["check"] for f in verdict.failed_checks if f["check"] in st.judgements]
    for f in verdict.failed_checks:
        st.feedback[f["check"]] = f["reason"]
    c.plan_fails = [f for f in verdict.failed_checks if f["check"] in ("rulebook", "plan")]
    return {"route": "revise"}


def revise(gs: GraphState) -> GraphState:
    """The retry: re-plan when the rulebook or the plan was wrong, with the critic's ruling enforced in code."""
    c = gs["ctx"]
    st, case = c.state, c.case
    c.retried = True
    plan_fails = c.plan_fails
    if plan_fails:
        st.plan_feedback = " ".join(f["reason"] for f in plan_fails)
        old = {p.check for p in st.plan.checks}
        new_plan = S.plan_step(st, c.llm, c.budget, c.trace)
        if c.force_full:
            st.scope = "full"
        picked = tools.pick_lender_type(case)["lender_type"]
        enforced = []
        if new_plan.lender_type != picked:          # the critic's ruling on the rulebook is final
            new_plan.lender_type = picked
            enforced.append("lender_type")
        have = {p.check for p in new_plan.checks} | old
        need = set(tools.required_checks(case)) if st.scope == "full" else set()
        for chk in sorted((need | old) - {p.check for p in new_plan.checks}):
            new_plan.checks.append(PlannedCheck(check=chk, tool=tools.CHECK_TOOL[chk]))
            if chk not in have:
                enforced.append(chk)
        new_plan.intent = "rule_check"
        wrong_book = any(f["kind"] == "wrong_rulebook" for f in plan_fails)
        c.failed = [p.check for p in new_plan.checks] if wrong_book else c.failed + [p.check for p in new_plan.checks if p.check not in old]
        st.plan = new_plan
        c.trace.step("planner", input=st.plan_feedback, output=new_plan.model_dump(), enforced_by_critic=enforced)
    return {"route": "execute" if c.failed else "critic"}


def finalize(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    out, st = c.out, c.state
    out.results = S.final_results(st, c.unresolved)
    out.citations = sorted({r.paragraph_id for r in out.results if r.paragraph_id})
    out.retrieved = sorted({h.paragraph_id for hits in st.evidence.values() for h in hits})
    return {"route": "draft"}


def drafter(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    out = c.out
    if c.draft:
        try:
            out.answer, _ = S.draft_report(c.state, out.results, c.llm, c.budget, c.trace, index=c.index)
        except (ToolCallError, BudgetExceeded):
            out.answer = S.template_report(out.results)
        c.trace.step("drafter", output=out.answer)
    else:
        out.answer = S.template_report(out.results)
    return {"route": "end"}


# ------------------------------------------------------------------ lookup path
def lookup_guard(gs: GraphState) -> GraphState:
    """Follow-up lender type and unverified rules are decided in code; the model is never asked about an unverified rule."""
    c = gs["ctx"]
    st, out = c.state, c.out
    if st.history:   # on a follow-up the lender type is decided in code: the new question's, else the previous turn's
        named = S.lender_named(st.question)
        st.plan.lender_type = named or st.history[-1].get("lender_type") or st.plan.lender_type
        c.trace.step("planner", output={"lender_type": st.plan.lender_type, "reason": "follow-up: lender type set in code"})
    blocked = S.unverified_rule_for(S.topic_question(st), st.plan.lender_type)
    if blocked:
        out.answer = (f"The rule on {blocked['theme'].replace('_', ' ')} for a {S.LENDER_LABEL[blocked['lender_type']].lower()} is unverified in the rules file, "
                      "so the answer is unknown. I will not answer it from memory. It needs to be found and read in the RBI text first.")
        c.trace.step("critic", output={"verdict": "pass", "reason": f"unverified rule {blocked['rule_id']}: answered as unknown without drafting"})
        return {"route": "end"}
    return {"route": "search"}


def search(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    st = c.state
    query = (st.plan.search_query + " " + S.topic_question(st)).strip()
    c.hits = c.index.search(query, lender_type=st.plan.lender_type, k=5)
    c.out.retrieved = [h.paragraph_id for h in c.hits]
    c.trace.step("executor", input={"query": query, "lender_type": st.plan.lender_type},
                 output={"retrieved": [{"paragraph_id": h.paragraph_id, "score": h.score} for h in c.hits]})
    return {"route": "draft"}


def lookup_draft(gs: GraphState) -> GraphState:
    c = gs["ctx"]
    c.reply = S.draft_lookup(c.state, c.hits, c.llm, c.budget, c.trace, feedback=c.problem or "")
    c.trace.step("drafter", output=c.reply.model_dump())
    return {"route": "critic"}


def lookup_critic(gs: GraphState) -> GraphState:
    """Grounding check: citations retrieved, right lender type, numbers present in the cited text. One redraft."""
    c = gs["ctx"]
    out = c.out
    c.problem = S.critic_lookup(c.state, c.reply, c.hits)
    c.trace.step("critic", output={"verdict": "fail" if c.problem else "pass", "reason": c.problem or ""})
    if c.problem and not c.redrafted:
        c.redrafted = True
        out.critic_failures = 1
        out.wrong_rulebook_caught = "from a document for" in c.problem
        return {"route": "redraft"}
    cites = [re.sub(r"^paragraph\s+", "", x.strip(), flags=re.I) for x in c.reply.citations]
    out.citations = [x for x in cites if any(S.same_paragraph(x, h.paragraph_id) for h in c.hits)]
    out.answer = c.reply.answer if not c.problem else (
        "I could not verify an answer against the retrieved paragraphs. The closest paragraphs are: " + ", ".join(out.retrieved[:3]) + ".")
    return {"route": "end"}


# ------------------------------------------------------------------ the graph
def _route(gs: GraphState) -> str:
    return gs["route"]


def _traced(name: str, fn):
    """Announce each node as it starts and ends, so the workspace can show what the assistant is doing."""
    def node(gs: GraphState) -> GraphState:
        label = activity.AGENT_LABEL.get(name, name)
        if name == "executor" and gs["ctx"].retried:
            label = "Re-running the failed checks with the critic's reasons"
        with activity.span("agent", label, node=name) as res:
            out = fn(gs)
            res.update(next=out.get("route"))
        return out
    node.__name__ = name
    return node


def build_graph():
    g = StateGraph(GraphState)
    for name, fn in [(n, _traced(n, f)) for n, f in [("guard", guard), ("planner", planner), ("executor", executor), ("judge", judge), ("critic", critic),
                     ("revise", revise), ("finalize", finalize), ("drafter", drafter), ("lookup_guard", lookup_guard),
                     ("search", search), ("lookup_draft", lookup_draft), ("lookup_critic", lookup_critic)]]:
        g.add_node(name, fn)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", _route, {"plan": "planner", "end": END})
    g.add_conditional_edges("planner", _route, {"execute": "executor", "lookup": "lookup_guard", "end": END})
    g.add_edge("executor", "judge")
    g.add_edge("judge", "critic")
    g.add_conditional_edges("critic", _route, {"finalize": "finalize", "revise": "revise"})
    g.add_conditional_edges("revise", _route, {"execute": "executor", "critic": "critic"})
    g.add_edge("finalize", "drafter")
    g.add_edge("drafter", END)
    g.add_conditional_edges("lookup_guard", _route, {"search": "search", "end": END})
    g.add_edge("search", "lookup_draft")
    g.add_edge("lookup_draft", "lookup_critic")
    g.add_conditional_edges("lookup_critic", _route, {"redraft": "lookup_draft", "end": END})
    return g.compile()


GRAPH = build_graph()


def invoke(ctx: RunContext) -> RunContext:
    """Run the graph for one question. Step-cap, budget and model errors propagate to the caller."""
    final = GRAPH.invoke({"ctx": ctx}, config={"recursion_limit": RECURSION_LIMIT})
    return final["ctx"]


def mermaid() -> str:
    """The graph as a Mermaid diagram, for the docs."""
    return GRAPH.get_graph().draw_mermaid()
