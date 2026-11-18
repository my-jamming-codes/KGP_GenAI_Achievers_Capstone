"""Entry point for one question: guard, planner, executor, judge, critic, with one retry, then the drafter. Hard cap of 10 steps.

The flow itself is a LangGraph state graph (src/agents/graph.py). This module sets up the run, invokes the graph,
handles the stop conditions, and writes the trace, the cache entry and the conversation memory.

A step is one agent action recorded in the trace: guard, planner, executor, judge, critic or drafter.
Only the officer's click changes an application's status; nothing in this module can.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from langgraph.errors import GraphRecursionError

from src import config, rules
from src.agents import graph as G
from src.agents import steps as S
from src.tracing import activity
from src.llm import LLM, Budget, BudgetExceeded, ToolCallError
from src.safety import refusal
from src.safety.pii import mask_obj, mask_text
from src.schemas import Case, RunOutput
from src.tracing.tracer import StepCapReached, Trace

NEEDS_SCREEN, NO_SCREEN_MESSAGE = G.NEEDS_SCREEN, G.NO_SCREEN_MESSAGE


def _cache_key(question, case, application_id, screen, strategy, corpus_version) -> str:
    payload = [question, case.model_dump(mode="json") if case else None, application_id, screen, strategy, corpus_version, rules.rules_version()]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class AnswerCache:
    """Full answers keyed by question, application id, shared fields, strategy and corpus version."""

    def __init__(self, directory: Optional[Path] = None) -> None:
        self.dir = Path(directory) if directory else config.CACHE_DIR / "answers"

    def get(self, key: str) -> Optional[RunOutput]:
        f = self.dir / f"{key}.json"
        return RunOutput.model_validate_json(f.read_text(encoding="utf-8")) if f.exists() else None

    def put(self, key: str, out: RunOutput) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{key}.json").write_text(out.model_dump_json(), encoding="utf-8")


def run(question: str, *, case: Optional[Case] = None, application_id: Optional[str] = None, screen: Optional[dict] = None,
        strategy: str = "A", llm: Optional[LLM] = None, index=None, user: str = "system", fault: Optional[str] = None,
        draft: bool = True, force_full: bool = False, cache: Optional[AnswerCache] = None, trace_path=None,
        budget: Optional[Budget] = None, memory=None) -> RunOutput:
    """Answer one question or run one rule check. Never raises for model, tool, budget or step-cap problems.

    memory: a ConversationMemory. Every turn is saved to it, and the last turns are used when the question is a
    follow-up, so state persists across turns and across app restarts."""
    if index is None:
        from src.index.hybrid import get_index

        index = get_index()
    llm = llm or LLM()
    budget = budget or Budget()
    trace = Trace(question, user=user, application_id=application_id, strategy=strategy, path=trace_path)
    out = RunOutput(trace_id=trace.trace_id)
    state = S.State(question=question, case=case, application_id=application_id, screen=mask_obj(screen) if screen else None, strategy=strategy)
    if memory is not None and S.is_follow_up(question):
        state.history = memory.load(user, application_id, n=2)
    history_key = [t.get("trace_id") for t in state.history]   # a follow-up's answer depends on the turns it follows
    key = _cache_key(question, case, application_id, screen, f"{strategy}|{fault}|{draft}|{force_full}|{history_key}", index.corpus_version)

    def finish() -> RunOutput:
        out.steps_used = trace.steps_used
        out.tokens = trace.record["tokens"]["prompt"] + trace.record["tokens"]["completion"]
        out.injection_detected = state.injection_detected
        out.answer = refusal.soften_output(mask_text(out.answer))
        trace.record["cache_hit"] = out.cache_hit
        trace.finish(refused=out.refused, stopped=out.stopped, answer=out.answer, citations=out.citations,
                     results=[r.model_dump() for r in out.results], critic_failures=out.critic_failures,
                     wrong_rulebook_caught=out.wrong_rulebook_caught, injection_detected=out.injection_detected,
                     first_pass={k: {"status": j.status, "paragraph_id": j.paragraph_id} for k, j in state.first_pass.items()},
                     retrieved=out.retrieved, tool_errors=state.tool_errors, model_calls=llm.calls)
        if cache is not None and not out.cache_hit and not out.stopped:
            cache.put(key, out)
        if memory is not None:
            notes = [n for n, on in (("served from cache", out.cache_hit), ("instruction-like text was ignored", out.injection_detected),
                                     ("the critic sent it back once", out.critic_failures > 0),
                                     ("follow-up to the previous question", bool(state.history))) if on]
            memory.append(user, application_id, {
                "question": state.question if not out.refused else question, "answer": out.answer, "citations": out.citations,
                "results": {r.check: r.status for r in out.results}, "trace_id": out.trace_id, "refused": out.refused,
                "lender_type": (state.plan.lender_type if state.plan else None) or (case.lender_type if case else None), "notes": notes})
        return out

    ctx = G.RunContext(question=question, case=case, state=state, out=out, llm=llm, index=index, budget=budget, trace=trace,
                       cache=cache, cache_key=key, fault=fault, draft=draft, force_full=force_full)
    try:
        with activity.context(run=trace.trace_id, user=user, application_id=application_id, strategy=strategy):
            out = G.invoke(ctx).out   # the LangGraph state graph in src/agents/graph.py
        return finish()
    except (StepCapReached, GraphRecursionError):
        out = ctx.out
        out.stopped = "step_cap"
    except BudgetExceeded:
        out = ctx.out
        out.stopped = budget.reason or "token_budget"
    except ToolCallError as exc:
        out = ctx.out
        out.stopped = "tool_call_error"
        state.tool_errors.append(str(exc))
    # A stopped run still returns what the tools found, marked as unconfirmed, and says why it stopped.
    if state.tool_results and not out.results:
        out.results = S.final_results(state, set(state.tool_results) - set(state.judgements) | set(state.feedback))
        out.citations = sorted({r.paragraph_id for r in out.results if r.paragraph_id})
    reason = {"step_cap": "the 10 step limit", "time_limit": "the time limit", "token_budget": "the token budget",
              "tool_call_error": "a model reply that failed validation"}[out.stopped]
    out.answer = f"I stopped because of {reason}. " + (S.template_report(out.results) if out.results else "Please try a narrower question.")
    return finish()

