"""The agent steps: planner, executor (retrieval and tools), judge, critic and drafter.

Each function does one step and returns validated data. The loop in loop.py records them in the trace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from src import rules
from src.tracing import activity
from src.index.hybrid import same_paragraph
from src.llm import LLM, Budget
from src.prompts import strategies as P
from src.safety import injection
from src.safety.pii import mask_obj, mask_text
from src.safety.refusal import soften_output
from src.schemas import LENDER_LABEL, Case, CheckResult, CriticVerdict, Judgement, Plan, PlannedCheck, RetrievedParagraph, ToolResult
from src.tools import checks as tools

# Search phrases for each rule check. Retrieval for rule checks uses these, written in RBI wording.
CHECK_QUERY = {
    "ltv_cap": "LTV ratio housing loans to individuals up to ₹30 lakh above ₹75 lakh loan to value exceeding per cent",
    "risk_weight": "risk weight housing loans to individuals LTV ratio standard assets claims secured by residential property",
    "prepayment_charge": "pre-payment charges levy penalty on pre-closure of floating rate housing loans individuals",
    "key_facts_statement": "Key Facts Statement KFS shall be provided to all prospective borrowers before executing the loan contract",
    "guarantor_disclosure": "person considering being a guarantor to a loan should be informed about liability as guarantor",
    "valuation": "valuation of properties independent valuers minimum two valuation reports loan amount",
    "kyc": "customer due diligence CDD obtain from an individual Aadhaar PAN Form No. 60 officially valid document",
    "rate_reset_communication": "reset of floating interest rate EMI communicate to borrower impact of change in benchmark interest rate at sanction",
    "related_party": "loans and advances to directors lending to related parties materiality threshold sanctioned by the Board",
    "mitc": "most important terms and conditions MITC of housing loan document duplicate copy handed over to borrower",
}
# Tool outputs that would give the judge the answer; it must read these from the paragraph instead.
HIDDEN_FACTS = {"cap_pct", "loan_band", "risk_weight_pct", "ltv_band", "reports_required", "independent_reports_required",
                "materiality_threshold_ceiling", "regime_start", "rule_start", "compute_rule_id", "value_used"}
CASE_FACTS = {
    "ltv_cap": ["borrower_type", "loan_amount", "property_value", "property_cost", "stamp_duty_registration"],
    "risk_weight": ["borrower_type", "loan_amount", "housing_loan_number", "sanction_date", "case_date"],
    "prepayment_charge": ["borrower_type", "borrower_is_mse", "rate_type", "prepayment_charge_levied", "prepayment_charge_scope", "sanction_date"],
    "key_facts_statement": ["kfs_provided", "sanction_date"],
    "guarantor_disclosure": ["guarantor_present", "guarantor_disclosure"],
    "valuation": ["loan_amount", "property_value", "valuation_reports", "independent_valuation_reports"],
    "kyc": ["pan_or_form60", "identity_address_proof"],
    "rate_reset_communication": ["rate_type", "borrower_type", "rate_reset_impact_communicated"],
    "related_party": ["related_party", "loan_amount", "hfc_layer", "bank_asset_size_crore", "board_sanction", "case_date"],
    "mitc": ["borrower_type", "mitc_signed"],
}
FULL_SCOPE = re.compile(r"\b(full|complete|entire|all (the )?(rbi )?(rules|checks)|every (rule|check)|missing|outstanding|before .{0,40}cleared)\b", re.I)
NUMERIC_KEY = {"ltv_cap": "cap_pct", "risk_weight": "risk_weight_pct"}


@dataclass
class State:
    """Shared state for one question. Holds the case, the shared screen context and everything the agents produce."""

    question: str
    case: Optional[Case] = None
    application_id: Optional[str] = None
    screen: Optional[dict] = None          # {"application_id", "tab", "fields"} sent by Share this screen
    strategy: str = "A"
    plan: Optional[Plan] = None
    scope: str = "specific"
    evidence: dict[str, list[RetrievedParagraph]] = field(default_factory=dict)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    judgements: dict[str, Judgement] = field(default_factory=dict)
    first_pass: dict[str, Judgement] = field(default_factory=dict)
    feedback: dict[str, str] = field(default_factory=dict)
    plan_feedback: str = ""
    injection_detected: bool = False
    tool_errors: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)   # earlier turns, only when this question is a follow-up


FOLLOW_UP = re.compile(
    r"^\s*(and|also|but|so|then|what about|how about|same for|why)\b"
    r"|\b(that rule|this rule|that check|that one|the same|same rule|previous answer|your last answer|you just said|earlier answer)\b", re.I)


def is_follow_up(question: str) -> bool:
    """A question that only makes sense with the previous turn, such as 'And for a bank?'. Decided in code."""
    return bool(FOLLOW_UP.search(question))


def lender_named(question: str) -> Optional[str]:
    """The lender type the question itself names, or None when it names neither or both."""
    q = question.lower()
    hfc = bool(re.search(r"\b(hfcs?|housing finance)\b", q))
    bank = bool(re.search(r"\bbanks?\b", q))
    return "hfc" if hfc and not bank else "bank" if bank and not hfc else None


def history_text(history: list[dict]) -> str:
    """Earlier turns for a prompt, masked and wrapped as untrusted data."""
    lines = []
    for t in history:
        lines.append(f"Officer asked: {t.get('question', '')}")
        lines.append(f"Assistant answered: {str(t.get('answer', ''))[:300]}")
    text, _ = injection.sanitize(mask_text("\n".join(lines)))
    return injection.wrap_untrusted(text, "earlier turns of this conversation")


def topic_question(state: "State") -> str:
    """The question with the previous question in front when this one is a follow-up, for search and rule matching."""
    if state.history:
        return f"{state.history[-1].get('question', '')} {state.question}".strip()
    return state.question


def lender_of_paragraph(paragraph_id: str, retrieved: list[RetrievedParagraph]) -> Optional[str]:
    """Lender type a cited paragraph applies to: from the rules file sources, else from the chunk metadata."""
    known = rules.source_lender_type(paragraph_id)
    if known:
        return known
    for r in retrieved:
        if r.doc_key == paragraph_id.split("/")[0]:
            return {"nbfc": "hfc"}.get(r.lender_type, r.lender_type)
    return None


# ------------------------------------------------------------------ planner
def case_summary(case: Case) -> str:
    d = mask_obj(case.model_dump(mode="json", exclude={"free_text_note"}))
    d["lender_type"] = LENDER_LABEL[case.lender_type]
    return "\n".join(f"- {k} = {v}" for k, v in d.items() if v is not None)


def plan_step(state: State, llm: LLM, budget: Budget, trace) -> Plan:
    parts = [f"Question: {state.question}"]
    if state.case:
        parts += ["", "Case fields:", case_summary(state.case)]
        if state.case.free_text_note:
            note, hit = injection.sanitize(state.case.free_text_note)
            state.injection_detected |= hit
            parts += ["", injection.wrap_untrusted(mask_text(note), "free text note on the application")]
    else:
        parts += ["", "No case or application is shared, so only a general rule question can be answered."]
    if state.screen:
        parts += ["", f"Shared screen: application {state.screen.get('application_id')}, tab '{state.screen.get('tab')}'."]
    if state.history:
        parts += ["", "This question is a follow-up. Use the earlier turns only to work out what it refers to "
                      "(topic, lender type, rule); answer only the new question.", history_text(state.history)]
    if state.plan_feedback:
        parts += ["", f"A reviewer rejected the previous plan: {state.plan_feedback}", "Correct the plan."]
    reply = llm.chat_json(P.PLANNER_SYSTEM, "\n".join(parts), P.PlannerReply, budget=budget, trace=trace, max_tokens=300)
    state.scope = "full" if FULL_SCOPE.search(state.question) else "specific"   # scope is decided in code, not by the model
    seen, planned = set(), []
    for c in reply.checks:
        if c not in seen:
            seen.add(c)
            planned.append(PlannedCheck(check=c, tool=tools.CHECK_TOOL[c]))
    return Plan(intent=reply.intent, lender_type=reply.lender_type, checks=planned, search_query=reply.search_query)


# ------------------------------------------------------------------ executor: retrieval + tools
def execute_step(state: State, index, only: Optional[list[str]] = None, targeted: bool = False) -> dict:
    """Retrieve paragraphs and run the tool for each planned check. Uses the planner's lender type,
    so a wrong plan produces wrong-rulebook evidence for the critic to catch."""
    plan = state.plan
    lender = plan.lender_type or state.case.lender_type
    case = state.case if lender == state.case.lender_type else state.case.model_copy(update={"lender_type": lender})
    log: dict[str, Any] = {}
    for pc in plan.checks:
        if only is not None and pc.check not in only:
            continue
        try:
            with activity.span("tool", f"Tool {tools.CHECK_TOOL[pc.check]}: {pc.check.replace('_', ' ')}", check=pc.check) as res:
                result = tools.run_tool(pc.check, case)
                res.update(result=result.status)
        except Exception as exc:  # a tool error is recorded, never fatal
            state.tool_errors.append(f"{pc.check}: {exc}")
            result = ToolResult(check=pc.check, status="needs_information", note=f"tool error: {exc}")
        query = CHECK_QUERY[pc.check]
        if pc.check == "risk_weight" and lender == "bank" and case.case_date >= date(2027, 4, 1):
            query += " Table 9 housing loans to individuals up to two loans third loan onward"
        hits = index.search(query, lender_type=lender, k=4)
        if targeted and result.paragraph_id and not any(same_paragraph(h.paragraph_id, result.paragraph_id) for h in hits):
            extra = index.fetch_paragraph(result.paragraph_id)
            hits = extra[:2] + hits[:3]
        state.evidence[pc.check] = hits
        state.tool_results[pc.check] = result
        log[pc.check] = {"tool": pc.tool, "arguments": {"lender_type": lender}, "result": result.model_dump(),
                         "retrieved": [{"paragraph_id": h.paragraph_id, "score": h.score} for h in hits]}
    return log


def evidence_text(hits: list[RetrievedParagraph], state: State, limit: int = 3, chars: int = 1100) -> str:
    out = []
    for h in hits[:limit]:
        text, hit = injection.sanitize(h.text[:chars], strict=True)
        state.injection_detected |= hit
        out.append(f"<data source=\"paragraph {h.paragraph_id}\">\n{text}\n</data>")
    return "\n".join(out) if out else "(no paragraph was retrieved)"


# ------------------------------------------------------------------ judge
def facts_for(check: str, case: Case, tool: ToolResult) -> dict:
    data = case.model_dump(mode="json")
    facts = {k: data[k] for k in CASE_FACTS[check] if data.get(k) is not None}
    facts.update({k: v for k, v in tool.values.items() if k not in HIDDEN_FACTS and k not in facts})
    return facts


def judge_one(check: str, state: State, llm: LLM, budget: Budget, trace) -> Judgement:
    with activity.context(check=check):
        return _judge_one(check, state, llm, budget, trace)


def _judge_one(check: str, state: State, llm: LLM, budget: Budget, trace) -> Judgement:
    tool = state.tool_results[check]
    if tool.status == "unknown":  # unverified rule: never judged, never answered from memory
        return Judgement(check=check, status="unknown", paragraph_id=None, reason=tool.note)
    lender = state.plan.lender_type or state.case.lender_type
    facts = facts_for(check, state.case, tool)
    evidence = evidence_text(state.evidence.get(check, []), state)
    user = P.judge_user(check, LENDER_LABEL[lender], facts, evidence, state.feedback.get(check, ""))
    if state.strategy == "B":
        ex = llm.chat_json(P.EXTRACT_B, user, P.ExtractReply, budget=budget, trace=trace, max_tokens=400)
        user2 = "\n".join([f"Lender type: {LENDER_LABEL[lender]}", f"Rule check: {check} - {P.CHECK_TITLES[check]}",
                           f"Quoted rule from paragraph {ex.paragraph_id}: \"{ex.rule_quote}\"", "Case facts:",
                           *[f"- {v}" for v in ex.field_values], "", P.CONVENTIONS, f"For this check: {P.CHECK_GUIDE[check]}",
                           *([f"A reviewer rejected the previous answer: {state.feedback[check]} Correct it."] if check in state.feedback else [])])
        r = llm.chat_json(P.JUDGE_B, user2, P.JudgeReply, budget=budget, trace=trace, max_tokens=300)
        pid = r.paragraph_id or ex.paragraph_id
    elif state.strategy == "C":
        r = llm.chat_json(P.JUDGE_C, user, P.PlanJudgeReply, budget=budget, trace=trace, max_tokens=500)
        pid = r.paragraph_id
    else:
        r = llm.chat_json(P.JUDGE_A, user, P.JudgeReply, budget=budget, trace=trace, max_tokens=300)
        pid = r.paragraph_id
    pid = re.sub(r"^paragraph\s+", "", (pid or "").strip(), flags=re.I) or None
    return Judgement(check=check, status=r.status, paragraph_id=pid, values_used={"limit_read": r.limit_read}, reason=r.reason)


# ------------------------------------------------------------------ critic
def _number(text: Any) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


def critic_plan(state: State) -> list[dict[str, str]]:
    """Plan-level checks: right rulebook for the lender type, and no required check left out."""
    fails = []
    picked = tools.pick_lender_type(state.case)
    if state.plan.lender_type and state.plan.lender_type != picked["lender_type"]:
        fails.append({"check": "rulebook", "kind": "wrong_rulebook",
                      "reason": f"The case says {LENDER_LABEL[picked['lender_type']]}, but the plan used the "
                                f"{LENDER_LABEL[state.plan.lender_type]} rulebook. Use lender_type '{picked['lender_type']}'."})
    if state.scope == "full":
        have = {c.check for c in state.plan.checks}
        missing = [c for c in tools.required_checks(state.case) if c not in have]
        if missing:
            fails.append({"check": "plan", "kind": "missing_check", "reason": "A full check must also include: " + ", ".join(missing) + "."})
    elif not state.plan.checks:
        fails.append({"check": "plan", "kind": "missing_check", "reason": "The plan lists no checks. Pick the checks the question asks about."})
    return fails


def critic_judgement(check: str, state: State) -> Optional[dict[str, str]]:
    j, tool = state.judgements[check], state.tool_results[check]
    hits = state.evidence.get(check, [])
    lender = state.case.lender_type
    if tool.status == "unknown":
        return None if j.status == "unknown" else {"check": check, "kind": "unverified_rule", "reason": "The rule is unverified; the status must be unknown."}
    if j.paragraph_id:
        cited_lender = lender_of_paragraph(j.paragraph_id, hits)
        if cited_lender and cited_lender not in (lender, "other") and not (tool.paragraph_id and same_paragraph(j.paragraph_id, tool.paragraph_id)):
            return {"check": check, "kind": "wrong_rulebook",
                    "reason": f"Paragraph {j.paragraph_id} is from a document for {cited_lender}, but the case is a {LENDER_LABEL[lender]}."}
    problems: list[tuple[str, str]] = []
    if tool.paragraph_id and (not j.paragraph_id or not same_paragraph(j.paragraph_id, tool.paragraph_id)):
        kind = "ungrounded_citation" if j.paragraph_id and not any(same_paragraph(j.paragraph_id, h.paragraph_id) for h in hits) else "wrong_paragraph"
        problems.append((kind, f"The rule for this check is in paragraph {tool.paragraph_id}; you cited {j.paragraph_id or 'nothing'}. Cite {tool.paragraph_id}."))
    if j.status != tool.status:
        problems.append(("wrong_status", f"Status '{j.status}' does not match the tool computation, which gives '{tool.status}': {tool.note}"))
    key = NUMERIC_KEY.get(check)
    if key and key in tool.values:
        read = _number(j.values_used.get("limit_read"))
        if read is not None and abs(read - float(tool.values[key])) > 1e-6:
            problems.append(("wrong_number", f"You read {read:g} from the paragraph, but the rules file gives {tool.values[key]} for this case: {tool.note}"))
    if not problems:
        return None
    # every problem is reported at once, so the single retry can fix all of them
    return {"check": check, "kind": problems[0][0], "reason": " ".join(dict.fromkeys(r for _, r in problems))}


def critic_step(state: State, only: Optional[list[str]] = None) -> CriticVerdict:
    fails = critic_plan(state)
    for check in state.judgements:
        if only is None or check in only:
            f = critic_judgement(check, state)
            if f:
                fails.append(f)
    return CriticVerdict(verdict="fail" if fails else "pass", failed_checks=fails,
                         reason="; ".join(f["reason"] for f in fails)[:1200])


def final_results(state: State, unresolved: set[str]) -> list[CheckResult]:
    """Numbers and paragraph ids always come from the tools and the rules file. A judgement the critic could not
    confirm is never shown as a verdict: it becomes needs_information."""
    out = []
    for check, tool in state.tool_results.items():
        j = state.judgements.get(check)
        confirmed = check not in unresolved and j is not None
        status = j.status if confirmed else "needs_information"
        reason = (j.reason if confirmed else "The critic could not confirm the model's reading of this rule, so it is left for the officer. ")
        out.append(CheckResult(check=check, status=status, paragraph_id=tool.paragraph_id, rule_id=tool.rule_id, values=tool.values,
                               reason=(reason + " " + tool.note).strip(), confirmed_by_critic=confirmed))
    return out


# ------------------------------------------------------------------ drafter
def template_report(results: list[CheckResult]) -> str:
    order = {"breached": 0, "needs_information": 1, "unknown": 2, "met": 3, "not_applicable": 4}
    lines = []
    for r in sorted(results, key=lambda r: order[r.status]):
        cite = f" [{r.paragraph_id}]" if r.paragraph_id else ""
        lines.append(f"- {r.check}: {r.status}{cite}. {r.reason.split('. ')[-1] if r.status in ('met', 'not_applicable') else r.reason}")
    return "\n".join(lines) + "\nRecommendation only; the officer decides."


def draft_report(state: State, results: list[CheckResult], llm: LLM, budget: Budget, trace) -> tuple[str, list[str]]:
    allowed = [r.paragraph_id for r in results if r.paragraph_id]
    listing = "\n".join(f"- {r.check}: {r.status}; paragraph {r.paragraph_id or 'none'}; {r.reason[:260]}" for r in results)
    reply = llm.chat_json(P.DRAFT_REPORT_SYSTEM, f"Question: {state.question}\nRule check results:\n{listing}", P.DraftReply,
                          budget=budget, trace=trace, max_tokens=450)
    text = soften_output(mask_text(reply.answer))
    cited = set(re.findall(r"[A-Z][A-Z0-9-]+/[A-Za-z0-9-]+", text))
    if any(not any(same_paragraph(c, a) for a in allowed) for c in cited):  # the note cites something not in the results
        text = template_report(results)
    return text, sorted(set(allowed))


def unverified_notes(lender: Optional[str]) -> str:
    rows = [r for r in rules.all_rules(include_unverified=True) if r["status"] != "verified" and (lender is None or r["lender_type"] == lender)]
    if not rows:
        return "none"
    return "; ".join(f"{r['theme'].replace('_', ' ')} for {LENDER_LABEL[r['lender_type']]}s" for r in rows)


def unverified_rule_for(question: str, lender: Optional[str]) -> Optional[dict]:
    """The unverified rule a lookup question is about, if any. Decided in code, so the model is never asked
    to answer a rule that is not verified. Matches the rule's subject word (for example 'guarantor') and lender type."""
    lender = lender or tools.lender_type_from_label(question)
    q = question.lower()
    for r in rules.all_rules(include_unverified=True):
        if r["status"] == "verified" or r["lender_type"] != lender:
            continue
        words = r.get("keywords") or [r["theme"].split("_")[0]]
        if any(re.search(rf"\b{re.escape(w)}s?\b", q) for w in words):
            return r
    return None


def draft_lookup(state: State, hits: list[RetrievedParagraph], llm: LLM, budget: Budget, trace, feedback: str = ""):
    follow = [f"This is a follow-up to the earlier question: {state.history[-1].get('question', '')}"] if state.history else []
    user = "\n".join([*follow, f"Question: {state.question}", "", "Paragraphs retrieved:", evidence_text(hits, state, limit=5, chars=1300), "",
                      f"Rules listed as unverified (answer 'unverified, unknown' for these): {unverified_notes(state.plan.lender_type if state.plan else None)}",
                      *([f"\nA reviewer rejected the previous answer: {feedback} Correct it."] if feedback else [])])
    return llm.chat_json(P.DRAFT_LOOKUP_SYSTEM, user, P.DraftReply, budget=budget, trace=trace, max_tokens=400)


def critic_lookup(state: State, reply, hits: list[RetrievedParagraph]) -> Optional[str]:
    """Grounding check for a lookup answer: citations were retrieved, fit the lender type, and numbers appear in them."""
    cites = [re.sub(r"^paragraph\s+", "", c.strip(), flags=re.I) for c in reply.citations]
    for c in cites:
        if not any(same_paragraph(c, h.paragraph_id) for h in hits):
            return f"Citation {c} is not one of the retrieved paragraphs. Cite only ids shown."
    lender = state.plan.lender_type if state.plan else None
    if lender:
        for c in cites:
            cl = lender_of_paragraph(c, hits)
            if cl and cl not in (lender, "other"):
                return f"Paragraph {c} is from a document for {cl}, but the question is about a {LENDER_LABEL[lender]}."
    cited_text = " ".join(h.text for h in hits if any(same_paragraph(c, h.paragraph_id) for c in cites)) + " " + state.question
    cited_text = cited_text.replace(",", "")
    for num in re.findall(r"\d+(?:\.\d+)?", reply.answer.replace(",", "")):
        if len(num) >= 2 and num not in cited_text and not any(num in c for c in cites):
            return f"The number {num} in the answer does not appear in the cited paragraphs. State only figures that are in them."
    unknown_words = ("unverified", "unknown", "not verified")
    if not cites and not any(w in reply.answer.lower() for w in unknown_words) and "not" not in reply.answer.lower():
        return "The answer has no citation. Cite the paragraph id that supports it, or say the paragraphs do not answer the question."
    return None
