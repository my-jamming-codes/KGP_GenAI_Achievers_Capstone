"""Side-by-side prompt comparison for one case and one rule check (the Week 1 single-prompt baseline).

Each strategy gets the same case facts, the same tool result and the same retrieved paragraphs, and answers once:
no planner, no critic, no retry. Its reply is then compared with the deterministic tool, which is what the critic
would have done. Used by the Prompt lab page; it never changes an application.
"""
from __future__ import annotations

import time
from typing import Optional

from src.agents import steps as S
from src.llm import LLM, Budget, BudgetExceeded, ToolCallError
from src.prompts import strategies as P
from src.schemas import Case, Plan, PlannedCheck
from src.tools import checks as tools
from src.tracing import activity
from src.tracing.tracer import Trace

TECHNIQUE = {
    "A": ("Direct check", "Structured output: one prompt, a JSON schema reply (status, paragraph, limit read, reason). Zero-shot.",
          [("System prompt", "JUDGE_A")], "JudgeReply"),
    "B": ("Extract, then judge", "Decomposition: two prompts. The first copies the rule words and the case facts; "
          "the second judges from those quotes only.", [("Step 1 system prompt", "EXTRACT_B"), ("Step 2 system prompt", "JUDGE_B")], "ExtractReply, then JudgeReply"),
    "C": ("Plan, judge, self check", "Chain of thought with self-verification: a short written plan, the judgement, then a self check "
          "that every number is in the cited paragraph and the paragraph fits the lender type.", [("System prompt", "JUDGE_C")], "PlanJudgeReply"),
}


def prompt_text(name: str) -> str:
    return getattr(P, name)


def compare(case: Case, check: str, llm: LLM, index, strategies=("A", "B", "C"), user: str = "prompt-lab",
            application_id: Optional[str] = None, trace_path=None) -> dict:
    """Run each strategy once on the same evidence. Returns the tool result, the evidence ids and one row per strategy."""
    base = S.State(question=f"Prompt lab: {check}", case=case, application_id=application_id)
    base.plan = Plan(intent="rule_check", lender_type=tools.pick_lender_type(case)["lender_type"],
                     checks=[PlannedCheck(check=check, tool=tools.CHECK_TOOL[check])])
    S.execute_step(base, index)
    tool = base.tool_results[check]
    rows = []
    for strat in strategies:
        st = S.State(question=base.question, case=case, application_id=application_id, strategy=strat, plan=base.plan,
                     evidence=base.evidence, tool_results=base.tool_results)
        trace = Trace(st.question, user=user, application_id=application_id, strategy=strat, path=trace_path)
        calls_before, started = llm.calls, time.time()
        row = {"strategy": strat, "name": TECHNIQUE[strat][0]}
        try:
            with activity.context(strategy=strat):
                j = S.judge_one(check, st, llm, Budget(), trace)
            agrees_status = j.status == tool.status
            agrees_para = not tool.paragraph_id or (j.paragraph_id and S.same_paragraph(j.paragraph_id, tool.paragraph_id))
            row.update(status=j.status, paragraph_id=j.paragraph_id, limit_read=j.values_used.get("limit_read"), reason=j.reason,
                       agrees=bool(agrees_status and agrees_para),
                       problem="" if agrees_status and agrees_para else
                       ("status differs from the tool" if not agrees_status else "cites a different paragraph from the tool's rule"))
        except (ToolCallError, BudgetExceeded) as exc:
            row.update(status="error", paragraph_id=None, limit_read=None, reason=str(exc)[:300], agrees=False,
                       problem="reply failed: " + type(exc).__name__)
        row.update(model_calls=llm.calls - calls_before, tokens=trace.record["tokens"]["prompt"] + trace.record["tokens"]["completion"],
                   seconds=round(time.time() - started, 1), trace_id=trace.trace_id)
        trace.finish(prompt_lab=True, check=check, result=row, tool=tool.model_dump())
        rows.append(row)
    return {"tool": tool.model_dump(), "evidence": [h.paragraph_id for h in base.evidence.get(check, [])], "rows": rows}
