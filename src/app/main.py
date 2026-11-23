"""Compliance officer workspace (Streamlit). Run: streamlit run src/app/main.py

The assistant recommends with citations. Only the officer's click changes an application's status.
All data is synthetic.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from src import config  # noqa: E402
from src.agents import loop  # noqa: E402
from src.agents.memory import ConversationMemory  # noqa: E402
from src.app import faq  # noqa: E402
from src.app.textfmt import softbreaks  # noqa: E402
from src.app.ratelimit import RateLimiter  # noqa: E402
from src.app.store import STATUSES, USERS, Store  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.schemas import LENDER_LABEL, TAB_NAMES, RunOutput, case_from_application  # noqa: E402
from src.tracing import activity  # noqa: E402
from src.tracing.tracer import new_id, read_traces  # noqa: E402

st.set_page_config(page_title="RBI Home Loan Rule Checker", layout="wide")
PAGES = ["Work queue", "Application", "Compliance checks", "Comments and sign-off", "FAQ", "Prompt lab", "Retrieval lab", "Activity log", "History and evaluation"]
BADGE = {"met": "🟢 Met", "breached": "🔴 Breached", "needs_information": "🟠 Needs information",
         "not_applicable": "⚪ Not applicable", "unknown": "🟣 Unknown (rule unverified)"}
ISSUE_VALUES = {"no", "not_recorded", "not_provided"}
STATUS_LABEL = {"new": "New", "in_review": "In review", "information_requested": "Information requested",
                "compliance_cleared": "Compliance cleared", "returned": "Returned"}


@st.cache_resource(show_spinner="Loading the RBI paragraph index…")
def get_index():
    from src.index.hybrid import get_index as _get

    return _get()


@st.cache_resource
def get_llm() -> LLM:
    return LLM()


@st.cache_resource
def get_limiter() -> RateLimiter:
    return RateLimiter()


def get_memory() -> ConversationMemory:
    return ConversationMemory()


store = Store()
ss = st.session_state
ss.setdefault("session_id", uuid.uuid4().hex)
ss.setdefault("notice", {})
ss.setdefault("page", PAGES[0])


class LiveActivity:
    """A status box that shows what the assistant is doing while it works: each agent step, with the model calls,
    searches and tools it made listed under it, as they start and finish."""

    def __init__(self, title: str) -> None:
        self.box = st.status(title, expanded=True)
        self.lines = activity.LiveLines()
        self.area = self.box.empty()
        self.started = time.time()
        self.summary = ""
        self.cached = 0
        self.counts = {"llm": 0, "embed": 0, "vector": 0, "bm25": 0, "rerank": 0, "tool": 0, "tokens": 0}

    def __call__(self, e: dict) -> None:
        self.lines.feed(e)
        if e["phase"] == "start":
            if e["kind"] == "agent":
                self.box.update(label=e["label"] + " …")
        else:
            if e.get("status") == "cache hit":
                self.cached += 1
            elif e["kind"] in self.counts:
                self.counts[e["kind"]] += 1
            self.counts["tokens"] += e.get("tokens") or 0
        self.area.markdown(self.lines.markdown(last=40))

    def __enter__(self):
        self._ctx = activity.listen(self)
        self._ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._ctx.__exit__(exc_type, exc, tb)
        took = time.time() - self.started
        c = self.counts
        models = f"{c['llm']} model call(s)" + (f", {self.cached} from cache" if self.cached else "")
        summary = (f"Done in {took:.1f} s · {models}, {c['tokens']:,} tokens · "
                   f"{c['vector']} vector search(es), {c['bm25']} BM25, {c['rerank']} rerank · {c['tool']} tool run(s)")
        self.summary = summary if exc_type is None else "Stopped with an error"
        self.box.update(label=self.summary, state="complete" if exc_type is None else "error", expanded=False)
        return False

    def text(self) -> str:
        return self.lines.markdown()


def label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def is_officer() -> bool:
    return USERS[ss["user"]]["role"] == "compliance_officer"


# ------------------------------------------------------------------ sign in
def sign_in() -> None:
    st.title("RBI Home Loan Rule Checker")
    st.caption("Compliance officer workspace · synthetic data only · course project, not for real lending decisions")
    user = st.selectbox("Sign in as", list(USERS), format_func=lambda u: f"{USERS[u]['name']} — {USERS[u]['role'].replace('_', ' ')}")
    if st.button("Sign in", type="primary"):
        ss["user"] = user
        st.rerun()


# ------------------------------------------------------------------ work queue
def work_queue() -> None:
    st.header("Work queue")
    apps = store.applications()
    chosen = st.multiselect("Status", STATUSES, default=STATUSES, format_func=STATUS_LABEL.get)
    rows = []
    for a in apps:
        if a["status"] not in chosen:
            continue
        t = a["tabs"]
        rows.append({"Application": a["application_id"], "Applicant": t["Applicant"]["applicant_name"],
                     "Amount (₹)": f"{t['Loan request']['loan_amount']:,}", "Lender type": t["Loan request"]["lender_type"],
                     "Assigned by": a["assigned_by"], "Due date": a["due_date"], "Status": STATUS_LABEL[a["status"]]})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    ids = [r["Application"] for r in rows]
    if ids:
        pick = st.selectbox("Open application", ids, index=ids.index(ss["app_id"]) if ss.get("app_id") in ids else 0)
        if st.button("Open", type="primary"):
            ss["app_id"], ss["page"] = pick, "Application"
            if is_officer() and store.application(pick)["status"] == "new":
                store.set_status(pick, "in_review", ss["user"], "opened from the work queue")
            st.rerun()


def current_app() -> dict | None:
    if not ss.get("app_id"):
        st.info("Open an application from the work queue first.")
        return None
    return store.application(ss["app_id"])


def app_header(app: dict) -> None:
    t = app["tabs"]
    st.subheader(f"{app['application_id']} · {t['Applicant']['applicant_name']}")
    st.caption(f"{t['Loan request']['lender_name']} ({t['Loan request']['lender_type']}) · loan ₹{t['Loan request']['loan_amount']:,} · "
               f"status: {STATUS_LABEL[app['status']]} · due {app['due_date']}")


# ------------------------------------------------------------------ assistant side panel
def assistant_panel(app: dict | None, tab: str | None) -> None:
    st.markdown("#### Assistant")
    share = st.toggle("Share this screen", value=False, disabled=app is None, help="Sends the open tab's fields as text. No image is sent.")
    screen, case = None, None
    if share and app is not None:
        fields = store.masked_view(app)["tabs"][tab]
        screen = {"application_id": app["application_id"], "tab": tab, "fields": fields}
        case = case_from_application(app)
        with st.expander(f"Shared with the assistant: {app['application_id']} · {tab} ({len(fields)} fields)"):
            st.json(fields)
    else:
        st.caption("No screen is shared. The assistant answers general rule questions only.")
    app_id = app["application_id"] if app else None
    key = app_id or "general"
    memory = get_memory()
    turns = memory.load(ss["user"], app_id, n=8)
    if turns:
        st.caption(f"Conversation saved for {key}: {len(turns)} recent turn(s). Follow-ups such as “and for a bank?” use the previous turn.")
    for t in turns:
        st.chat_message("user").write(t["question"])
        text = softbreaks(t["answer"]) + ("\n\nCited: " + ", ".join(t["citations"]) if t["citations"] else "")
        notes = t.get("notes") or []
        text += "\n\n_" + "; ".join(notes) + (" · " if notes else "") + f"trace {t['trace_id']}_"
        st.chat_message("assistant").markdown(text)
    if ss.get("last_activity", {}).get(key):
        summary, text = ss["last_activity"][key]
        with st.expander(f"What the assistant did for the last question · {summary}"):
            st.markdown(text)
            st.caption("Every call is also in the Activity log page.")
    if ss["notice"].get(key):
        st.warning(ss["notice"].pop(key))
    with st.form(f"ask_{key}", clear_on_submit=True):
        q = st.text_input("Ask about the rules or the open application")
        sent = st.form_submit_button("Ask")
    if turns and st.button("Clear conversation", key=f"clear_{key}"):
        memory.clear(ss["user"], app_id)
        st.rerun()
    if sent and q.strip():
        ok, wait = get_limiter().allow(ss["session_id"])
        if not ok:
            ss["notice"][key] = get_limiter().message(wait)
        else:
            with LiveActivity("Working on your question…") as live:
                loop.run(q, case=case, application_id=app_id, screen=screen, strategy=ss.get("strategy", "C"), llm=get_llm(),
                         index=get_index(), user=ss["user"], cache=loop.AnswerCache(), memory=memory)
            ss.setdefault("last_activity", {})[key] = (live.summary, live.text())
        st.rerun()


# ------------------------------------------------------------------ application tabs
def application_page() -> None:
    app = current_app()
    if not app:
        return
    left, right = st.columns([2, 1])
    with left:
        app_header(app)
        tab = st.radio("Tab", TAB_NAMES, horizontal=True, label_visibility="collapsed")
        view = store.masked_view(app)["tabs"][tab]
        for key, value in view.items():
            state = app["verification"].get(f"{tab}.{key}", "verified")
            if value == "not_provided" or (value in (None, "") and key == "guarantor_disclosure" and view.get("guarantor_present")):
                state = "not provided"
            flagged = str(value) in ISSUE_VALUES or state != "verified"
            dot = {"verified": "🟢", "unverified": "🟠", "not provided": "⚪"}[state]
            c1, c2, c3, c4 = st.columns([3, 4, 2, 1])
            c1.write(label(key))
            shown = "—" if value in (None, "") else (f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value))
            c2.markdown(f":orange-background[{shown}]" if flagged else shown)
            c3.caption(f"{dot} {state}")
            with c4.popover("💬"):
                note = st.text_input("Comment on this field", key=f"c_{tab}_{key}")
                if st.button("Save", key=f"s_{tab}_{key}", disabled=not is_officer()) and note.strip():
                    store.add_comment(app["application_id"], ss["user"], f"{tab}.{key}", note)
                    st.rerun()
        st.caption("🟠 amber marks a field the officer should look at: unverified, missing, or recorded as not done.")
    with right:
        assistant_panel(app, tab)


# ------------------------------------------------------------------ compliance checks
def checks_page() -> None:
    app = current_app()
    if not app:
        return
    left, right = st.columns([2, 1])
    with left:
        app_header(app)
        c1, c2, c3 = st.columns([2, 3, 2])
        ss["strategy"] = c1.selectbox("Prompt strategy", ["A", "B", "C"], index=2,
                                      format_func={"A": "A · direct check", "B": "B · extract then judge", "C": "C · plan, judge, self check"}.get)
        fault = c2.checkbox("Demo: start the planner with the wrong rulebook", help="Fault injection, so the critic catching it can be shown.")
        if c3.button("Run RBI rule check", type="primary", disabled=not is_officer()):
            with LiveActivity("Running the RBI rule check…"):
                out = loop.run(f"Run the full RBI rule check on application {app['application_id']}.", case=case_from_application(app),
                               application_id=app["application_id"], strategy=ss["strategy"], llm=get_llm(), index=get_index(),
                               user=ss["user"], force_full=True, fault="wrong_rulebook" if fault else None)
            store.save_results(app["application_id"], out.model_dump())
        saved = store.last_check(app["application_id"])
        if not saved:
            st.info("No rule check has been run on this application yet.")
        else:
            out = RunOutput.model_validate(saved)
            if out.wrong_rulebook_caught:
                st.warning("The critic caught the wrong rulebook on the first pass and sent the case back. The results below cite the right one.")
            st.caption(f"Trace {out.trace_id} · {out.steps_used} of {config.MAX_STEPS} steps · critic failures on first pass: {out.critic_failures}"
                       + (" · instruction-like text in the application was ignored" if out.injection_detected else ""))
            st.markdown(softbreaks(out.answer))
            actions = store.check_actions(app["application_id"])
            order = {"breached": 0, "needs_information": 1, "unknown": 2, "met": 3, "not_applicable": 4}
            for r in sorted(out.results, key=lambda r: order[r.status]):
                with st.container(border=True):
                    a, b = st.columns([3, 2])
                    a.markdown(f"**{label(r.check)}** — {BADGE[r.status]}")
                    a.write(r.reason)
                    a.caption(("Paragraph " + r.paragraph_id if r.paragraph_id else "No verified paragraph")
                              + ("" if r.confirmed_by_critic else " · not confirmed by the critic"))
                    if r.values:
                        a.caption("Tool values: " + ", ".join(f"{k}={v}" for k, v in r.values.items()))
                    done = actions.get(r.check)
                    b.caption(f"Officer action: {done or 'none yet'}")
                    b1, b2 = b.columns(2)
                    if b1.button("Accept", key=f"acc_{r.check}", disabled=not is_officer()):
                        store.record_check_action(app["application_id"], ss["user"], r.check, "accepted", out.trace_id)
                        st.rerun()
                    if b2.button("Dispute", key=f"dis_{r.check}", disabled=not is_officer()):
                        store.record_check_action(app["application_id"], ss["user"], r.check, "disputed", out.trace_id)
                        st.rerun()
                    note = b.text_input("Comment", key=f"cm_{r.check}", label_visibility="collapsed", placeholder="Comment on this result")
                    if b.button("Save comment", key=f"cs_{r.check}", disabled=not is_officer()) and note.strip():
                        store.add_comment(app["application_id"], ss["user"], f"check.{r.check}", note)
                        st.rerun()
            st.caption("The assistant recommends only. It never declares an application compliant.")
    with right:
        assistant_panel(app, "Loan request")


# ------------------------------------------------------------------ comments and sign-off
def signoff_page() -> None:
    app = current_app()
    if not app:
        return
    app_header(app)
    st.markdown("#### Comments")
    comments = store.comments(app["application_id"])
    if comments:
        st.dataframe([{"Time": c["time"], "User": c["user"], "On": c["target"], "Comment": c["text"]} for c in comments],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No comments yet.")
    note = st.text_area("Note or reason", placeholder="Write a note, the information you need, or the reasons for returning the application.")
    c1, c2, c3, c4 = st.columns(4)
    off = not is_officer()
    if off:
        st.caption("Only the compliance officer can comment, request information or sign off.")
    if c1.button("Add note", disabled=off) and note.strip():
        store.add_comment(app["application_id"], ss["user"], "application", note)
        st.rerun()
    if c2.button("Request more information", disabled=off):
        if note.strip():
            store.add_comment(app["application_id"], ss["user"], "information request", note)
            store.set_status(app["application_id"], "information_requested", ss["user"], note)
            st.rerun()
        st.warning("Write what information you need first.")
    if c3.button("Return with reasons", disabled=off):
        if note.strip():
            store.set_status(app["application_id"], "returned", ss["user"], note)
            st.rerun()
        st.warning("Write the reasons first.")
    if c4.button("Compliance cleared", type="primary", disabled=off):
        store.set_status(app["application_id"], "compliance_cleared", ss["user"], note or "cleared by the officer")
        st.rerun()
    saved = store.last_check(app["application_id"])
    if saved:
        open_items = [r["check"] for r in saved["results"] if r["status"] in ("breached", "needs_information", "unknown")]
        if open_items:
            st.warning("The last rule check has open items: " + ", ".join(label(c) for c in open_items) + ". The decision is yours.")
    else:
        st.info("No rule check has been run on this application.")


# ------------------------------------------------------------------ frequently asked questions
def faq_page() -> None:
    st.header("Frequently asked questions")
    st.caption("Rule answers are the verbatim RBI text held in the verified rules file, with the paragraph to cite. "
               "Nothing here is written from memory. To judge a specific application, use Compliance checks.")
    c1, c2 = st.columns([3, 2])
    text = c1.text_input("Search the questions", placeholder="for example: stamp duty, guarantor, KFS, valuation")
    lenders = ["Both", "hfc", "bank"]
    default = 0
    if ss.get("app_id"):
        default = lenders.index(case_from_application(store.application(ss["app_id"])).lender_type)
    pick = c2.radio("Lender type", lenders, index=default, horizontal=True,
                    format_func=lambda v: "Both" if v == "Both" else faq.LENDER_NAMES[v])
    lender = None if pick == "Both" else pick
    rules_tab, app_tab = st.tabs(["RBI rule questions", "Using the workspace"])
    with rules_tab:
        items = faq.filter_faq(faq.rule_faq(), text, lender)
        if not items:
            st.caption("No rule question matches. Try another word, or ask the assistant on the Application page.")
        for item in items:
            with st.expander(item["question"]):
                for e in item["entries"]:
                    head = f"**{faq.LENDER_NAMES[e['lender_type']]}**"
                    if e["status"] != "verified":
                        st.markdown(f"{head} — {BADGE['unknown']}")
                        st.warning("This rule is unverified in the rules file, so the answer is unknown. "
                                   "Check it yourself against the RBI text. " + e["unverified_reason"])
                        continue
                    st.markdown(f"{head} — paragraph `{e['paragraph_id']}`")
                    dates = []
                    if e["effective_date"]:
                        dates.append(f"in force from {e['effective_date']}")
                    if e["effective_until"]:
                        dates.append(f"until {e['effective_until']}")
                    if e["superseded_by"]:
                        dates.append(f"replaced by {e['superseded_by']}")
                    st.caption(f"{e['source_title']}, paragraph {e['paragraph']}" + (" · " + ", ".join(dates) if dates else ""))
                    for q in e["quote"]:
                        if q.lstrip().startswith("|"):
                            st.code(q.strip(), language=None)
                        else:
                            st.markdown("> " + q.replace("\n", "\n> "))
    with app_tab:
        rows = faq.filter_workspace(text)
        if not rows:
            st.caption("No workspace question matches.")
        for q, a in rows:
            with st.expander(q):
                st.markdown(a)


# ------------------------------------------------------------------ prompt lab (Week 1 prompt engineering showcase)
def prompt_lab_page() -> None:
    from src.prompts import compare as PC
    from src.prompts import strategies as P

    st.header("Prompt lab")
    st.caption("Three prompting strategies for the same rule check, run as single prompts: no planner, no critic, no retry. "
               "Each one sees the same case facts, the same tool result and the same RBI paragraphs; only the prompt differs.")

    st.markdown("#### The three strategies")
    for code, (name, what, prompts, schema) in PC.TECHNIQUE.items():
        with st.expander(f"{code}. {name} — {what.split(':')[0]}"):
            st.write(what)
            st.caption(f"Reply schema: {schema}")
            for title, attr in prompts:
                st.markdown(f"**{title}**")
                st.code(PC.prompt_text(attr), language=None)
    with st.expander("What all three receive (the user message)"):
        st.write("The lender type, the rule check, the case facts with numbers already computed by tools, the retrieved "
                 "paragraphs wrapped as data, the status conventions and a one-line guide for the check:")
        st.code(P.CONVENTIONS, language=None)

    st.markdown("#### Try it: one case, one rule, three prompts")
    apps = [a["application_id"] for a in store.applications()]
    c1, c2, c3 = st.columns([2, 2, 1])
    default = apps.index(ss["app_id"]) if ss.get("app_id") in apps else 0
    app_id = c1.selectbox("Application", apps, index=default)
    case = case_from_application(store.application(app_id))
    from src.tools.checks import required_checks
    checks = required_checks(case)
    check = c2.selectbox("Rule check", checks, format_func=label)
    picked = c3.multiselect("Strategies", ["A", "B", "C"], default=["A", "B", "C"])
    st.caption(f"{LENDER_LABEL[case.lender_type]} · loan ₹{case.loan_amount:,} · property ₹{case.property_value:,}. "
               "Uses the language model: about 1 call for A and C, 2 for B.")
    if st.button("Run the strategies", type="primary", disabled=not picked):
        ok, wait = get_limiter().allow(ss["session_id"])
        if not ok:
            st.warning(get_limiter().message(wait))
        else:
            with activity.context(run=new_id(), user=ss["user"], application_id=app_id,
                                  title=f"Prompt lab: {label(check)} on {app_id}"), LiveActivity("Running each prompt once…"):
                ss["prompt_lab"] = {"app": app_id, "check": check,
                                    **PC.compare(case, check, get_llm(), get_index(), strategies=picked, user=ss["user"], application_id=app_id)}
    res = ss.get("prompt_lab")
    if res and res["app"] == app_id and res["check"] == check:
        t = res["tool"]
        st.info(f"Tool computation (the reference): **{BADGE.get(t['status'], t['status'])}** · paragraph {t.get('paragraph_id') or '—'}"
                + (" · " + ", ".join(f"{k}={v}" for k, v in t["values"].items()) if t.get("values") else ""))
        st.caption("Paragraphs given to every strategy: " + (", ".join(res["evidence"]) or "none"))
        cols = st.columns(len(res["rows"]))
        for col, r in zip(cols, res["rows"]):
            with col.container(border=True):
                st.markdown(f"**{r['strategy']}. {r['name']}**")
                st.markdown(("✅ agrees with the tool" if r["agrees"] else f"❌ {r['problem']}"))
                st.write(f"Status: {BADGE.get(r['status'], r['status'])}")
                st.write(f"Paragraph: {r['paragraph_id'] or '—'} · limit read: {r['limit_read'] or '—'}")
                st.caption(r["reason"])
                st.caption(f"{r['model_calls']} model call(s) · {r['tokens']:,} tokens · {r['seconds']} s · trace {r['trace_id']}")
        st.caption("In the full workflow, the critic would send a ❌ reply back once with the reason; here you see the raw first reply.")

    st.markdown("#### Results on the frozen gold set")
    rows = []
    for split in ("dev", "test"):
        summ = _results_json(f"summary_{split}.json")
        if not summ:
            continue
        for code, v in summ["strategies"].items():
            rows.append({"Split": f"{split} ({summ['queries']})", "Strategy": f"{code}. {PC.TECHNIQUE[code][0]}",
                         "Single-prompt accuracy (first pass)": v["first_pass_accuracy"], "After critic and one retry": v["accuracy"],
                         "Citation rate": v["citation_rate"], "Tokens per query": v["avg_tokens_per_query"],
                         "Schema retries": v["validation_retries"]})
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("First pass = the strategy's own judgement on every expected rule, before any critic feedback "
                   "(the rule-check queries only). The test split was run for A and C, the two best on dev.")

    st.markdown("#### How the 8B model breaks")
    summ = _results_json("summary_test.json")
    if summ:
        kinds = {"wrong_status": "Status disagrees with the tool's computation", "wrong_paragraph": "Cites a paragraph that does not hold the rule",
                 "wrong_number": "Misreads the limit or weight in the paragraph", "missing_check": "Leaves a required check out of the plan",
                 "wrong_rulebook": "Uses the other lender type's rulebook"}
        st.dataframe([{"Failure on the first pass (test)": desc, **{f"Strategy {c}": v["critic_kinds"].get(k, 0) for c, v in summ["strategies"].items()}}
                      for k, desc in kinds.items()], hide_index=True, use_container_width=True)
    st.markdown(
        "- **It agrees with the wrong band.** With every LTV band in the paragraph, it reads the limit of the next band (80% for a ₹25 lakh loan "
        "where the paragraph says 90%), and the number still appears in the cited text, so a simple grounding check passes it (Q001).\n"
        "- **It cites the wrong paragraph.** On the test split, 32 (A) and 30 (C) first-pass judgements cited a paragraph that does not hold the rule; the critic corrected all of them.\n"
        "- **It misses the second condition in a rule.** On HL-2048 (₹78 lakh, 2 valuation reports, 1 independent) all three strategies saw two reports "
        "and judged the valuation rule met; the tool, applying Annex V, finds both reports must be independent above ₹75 lakh (try it above).\n"
        "- **It ignores instructions it was given in the prompt.** Told a rule is unverified, it answered it anyway from an unrelated paragraph (Q015); "
        "on a follow-up it kept the lender type from history instead of the one asked about. Both are now enforced in code.\n"
        "- **It repeats a mistake after being corrected.** Strategy A was told the right limit and gave the wrong one again on five test queries.\n"
        "- **Long structured replies get cut off.** Strategy B's extract step ran out of output tokens mid-string and failed validation three times (Q022, dev).")
    st.caption("Full list with trace ids: docs/failure_taxonomy.md")


# ------------------------------------------------------------------ retrieval lab (Week 2 RAG showcase)
MODE_LABEL = {"dense": "Dense only (bge-small, Qdrant)", "bm25": "BM25 only", "hybrid": "Hybrid (rank fusion)",
              "hybrid_rerank": "Hybrid + reranker (production)"}
LAB_EXAMPLES = ["loan to value ratio housing loans individuals", "stamp duty registration charges cost of the house LTV",
                "guarantor informed liability", "prepayment charges floating rate term loan individual borrowers",
                "key facts statement before execution of the contract"]


def _results_json(name: str):
    f = config.ROOT / "eval" / "results" / name
    return json.loads(f.read_text()) if f.exists() else None


def retrieval_lab_page() -> None:
    st.header("Retrieval lab")
    st.caption("Search the RBI corpus the way the agents do, and compare the four retrieval modes side by side. "
               "No language model is called on this page.")
    meta = _results_json("retrieval_dev.json") or {}
    if meta:
        fmt = meta.get("by_source_format") or {}
        st.caption(f"Corpus: {meta.get('corpus_documents', '?'):,} RBI documents · {meta.get('chunks', '?'):,} paragraph chunks"
                   + (f" · {fmt.get('pdf', 0)} parsed from the official PDF, {fmt.get('html', 0)} from the RBI web page" if fmt else ""))
    c1, c2, c3 = st.columns([4, 2, 1])
    pick = c1.selectbox("Example query", ["(type your own)"] + LAB_EXAMPLES)
    query = c1.text_input("Query", value="" if pick == "(type your own)" else pick)
    lender = c2.radio("Lender filter", ["any", "hfc", "bank"], horizontal=True,
                      format_func=lambda v: {"any": "Any", "hfc": "HFC (with NBFC Directions)", "bank": "Bank"}[v])
    k = c3.number_input("Top k", 3, 10, 5)
    if st.button("Search all four modes", type="primary") and query.strip():
        with activity.context(run=new_id(), user=ss["user"], title="Retrieval lab: " + activity.safe_query(query, 80)), \
                LiveActivity("Searching all four modes…"):
            idx = get_index()
            found = {m: idx.search(query, lender_type=None if lender == "any" else lender, k=int(k), mode=m) for m in MODE_LABEL}
        best = {h.paragraph_id for h in found["hybrid_rerank"]}
        cols = st.columns(4)
        for col, (mode, hits) in zip(cols, found.items()):
            col.markdown(f"**{MODE_LABEL[mode]}**")
            for rank, h in enumerate(hits, 1):
                mark = "⭐ " if h.paragraph_id in best and mode != "hybrid_rerank" else ""
                with col.expander(f"{rank}. {mark}{h.paragraph_id} · {h.score:.3f}"):
                    st.caption(f"{h.doc_key} · {h.lender_type}")
                    st.code(h.text[:700] + ("…" if len(h.text) > 700 else ""), language=None, wrap_lines=True)
        st.caption("⭐ also in the production top k. Scores are not comparable across modes: cosine for dense, BM25 score, "
                   "rank-fusion score for hybrid, cross-encoder score for the reranker.")

    st.markdown("#### Retrieval measured on its own: recall at 5")
    if meta:
        st.dataframe([{"Mode": MODE_LABEL[m], "Recall@5 (dev)": v} for m, v in meta["recall_at_5"].items()], hide_index=True,
                     use_container_width=True)
        st.caption(f"{meta['queries']} expected citations in the dev queries. `python -m eval.retrieval_eval`")
    chunking = _results_json("chunking.json")
    st.markdown("#### Chunking experiments")
    if chunking:
        rows = []
        for name, e in chunking["schemes"].items():
            for split in ("dev", "test"):
                r = e["results"][split]["hybrid_rerank"]
                rows.append({"Scheme": name, "Chunks": e["chunks"], "Mean words": e["mean_words"], "Split": split,
                             "Recall@1": r["recall@1"], "Recall@5": r["recall@5"], "Recall@10": r["recall@10"], "MRR@10": r["mrr@10"]})
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Hybrid + reranker for every scheme; the full grid of modes is in eval/results/chunking.json. `python -m eval.chunking_eval`")
    else:
        st.caption("Not run yet. `python -m eval.chunking_eval`")
    diag = _results_json("rag_diagnostic.json")
    st.markdown("#### Retrieval versus answer quality")
    if diag:
        st.dataframe([{"Run": k.replace("_", " · strategy "), "Queries": d["queries_with_expected_paragraph"], "Pipeline recall": d["pipeline_recall"],
                       "Answer accuracy": d["answer_accuracy"], "Accuracy when retrieved": d["accuracy_when_retrieved"],
                       "Generator errors": ", ".join(d["cells"]["retrieved_wrong"]) or "—",
                       "Retrieval errors": ", ".join(d["cells"]["missed_wrong"]) or "—"} for k, d in diag.items()],
                     hide_index=True, use_container_width=True)
        st.caption("A generator error had the expected paragraph and still answered wrong; a retrieval error never saw it. "
                   "`python -m eval.rag_diagnostic`")
    else:
        st.caption("Not run yet. `python -m eval.rag_diagnostic`")


# ------------------------------------------------------------------ activity log
KIND_LABEL = {"agent": "Agent steps", "llm": "LLM calls", "embed": "Embedding calls", "vector": "Vector DB (Qdrant)", "bm25": "BM25 search",
              "rerank": "Reranker", "tool": "Rule tools"}


def _run_titles() -> dict:
    """Heading and outcome for each question, from the traces, so a group reads as the question it answered."""
    out = {}
    for t in read_traces():
        if t.get("type") != "trace":
            continue
        o = t.get("outcome") or {}
        if o.get("refused"):
            outcome = "refused"
        elif o.get("stopped"):
            outcome = "stopped: " + str(o["stopped"]).replace("_", " ")
        elif t.get("cache_hit"):
            outcome = "answered from cache"
        elif o.get("results"):
            outcome = f"{len(o['results'])} rule results"
        else:
            outcome = "answered"
        out[t["trace_id"]] = {"title": str(t.get("question", ""))[:120], "outcome": outcome}
    return out


def activity_page() -> None:
    st.header("Activity log")
    st.caption("Each question or rule check is one group, newest first. Inside a group, things are listed in the order they "
               "happened, with the model, vector and tool calls under the step that made them. Prompt text is never logged; "
               "search queries are logged with identifiers masked. Times are UTC.")
    c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
    kinds = c1.multiselect("Show", list(KIND_LABEL), default=list(KIND_LABEL), format_func=KIND_LABEL.get)
    mine = c2.checkbox("Only my activity", value=True)
    n_runs = c3.number_input("Groups", 1, 200, 10, step=5)
    c4.write("")
    c4.button("Refresh")
    runs = activity.read_runs(int(n_runs), user=ss["user"] if mine else None, kinds=set(kinds), titles=_run_titles())
    if not runs:
        st.info("No activity yet. Ask the assistant a question or run a rule check, then refresh.")
        return
    calls = sum(r["llm_calls"] for r in runs)
    m = st.columns(6)
    m[0].metric("Groups shown", len(runs))
    m[1].metric("Model calls", calls)
    m[2].metric("Tokens", f"{sum(r['tokens'] for r in runs):,}")
    m[3].metric("Average model call", f"{sum(r['llm_ms'] for r in runs) / max(calls, 1) / 1000:.1f} s")
    m[4].metric("Vector searches", sum(r["searches"] for r in runs))
    m[5].metric("Retried or failed", sum(r["issues"] for r in runs))
    for i, r in enumerate(runs):
        head = f"{r['when']} · {r['title']} · {r['seconds']:.1f} s · {r['llm_calls']} model calls · {r['tokens']:,} tokens"
        head += (f" · {r['outcome']}" if r["outcome"] else "") + (f" · ⚠️ {r['issues']} retried or failed" if r["issues"] else "")
        with st.expander(head, expanded=(i == 0)):
            who = [x for x in (r["user"], r["application_id"], f"strategy {r['strategy']}" if r["strategy"] else None) if x]
            st.caption(" · ".join(who + [f"group {r['run']} (the trace id in History and evaluation)"]))
            table = [{"At": f"+{x['at']:.1f} s", "Step": ("↳ " if x["depth"] else "") + f"{activity.ICON.get(x['kind'], '•')} {x['label']}",
                      "Detail": x["detail"], "Seconds": x["seconds"], "Tokens": x["tokens"], "Hits": x["hits"], "Status": x["status"]}
                     for x in r["rows"]]
            if not table:
                st.caption("No calls of the selected types in this group.")
                continue
            st.dataframe(table, hide_index=True, use_container_width=True, height=min(38 * (len(table) + 1) + 3, 640),
                         column_config={"Seconds": st.column_config.NumberColumn(format="%.2f"), "Tokens": st.column_config.NumberColumn(format="%d")})
    st.caption(f"Log file: {activity.log_path()}")


# ------------------------------------------------------------------ history and evaluation
def history_page() -> None:
    st.header("History and evaluation")
    app_id = ss.get("app_id")
    rows = read_traces(application_id=app_id) if app_id else read_traces()
    st.markdown(f"#### Audit trail{' for ' + app_id if app_id else ' (all applications)'}")
    table = []
    for r in reversed(rows[-200:]):
        if r["type"] == "officer_action":
            table.append({"Time": r["timestamp"], "User": r["user"], "Kind": "officer action", "What": f"{r['action']}: {r.get('detail', '')}"[:140],
                          "Steps": "", "Trace": r.get("trace_id") or ""})
        else:
            o = r["outcome"]
            what = "refused" if o.get("refused") else (o.get("stopped") or f"{len(o.get('results', []))} rule results")
            table.append({"Time": r["timestamp"], "User": r["user"], "Kind": "cache hit" if r.get("cache_hit") else "question",
                          "What": f"{r['question'][:90]} → {what}", "Steps": len(r["steps"]), "Trace": r["trace_id"]})
    st.dataframe(table, use_container_width=True, hide_index=True)
    traces = [r for r in rows if r["type"] == "trace"]
    if traces:
        pick = st.selectbox("Open a trace", [t["trace_id"] for t in reversed(traces)])
        t = next(t for t in traces if t["trace_id"] == pick)
        st.caption(f"{t['latency_ms']} ms · tokens: {t['tokens']['prompt']} prompt, {t['tokens']['completion']} completion · strategy {t['strategy'] or '-'}")
        for s in t["steps"]:
            with st.expander(f"Step {s['step']} · {s['agent']}"):
                st.json(s)
    st.markdown("#### Last evaluation run")
    results = config.ROOT / "eval" / "results"
    shown = False
    for name in ("summary_test.json", "summary_dev.json"):
        f = results / name
        if f.exists():
            shown = True
            s = json.loads(f.read_text())
            st.caption(f"{s['split']} split · {s['queries']} queries · model {s['model']}")
            st.dataframe([{"Strategy": k, "Accuracy": v["accuracy"], "First pass accuracy": v["first_pass_accuracy"], "Citation rate": v["citation_rate"],
                           **{c.capitalize(): n for c, n in v["failures"].items()}} for k, v in s["strategies"].items()],
                         use_container_width=True, hide_index=True)
    f = results / "safety.json"
    if f.exists():
        s = json.loads(f.read_text())
        st.dataframe([{"Run": mode, "Set": name, "Passed": f"{r['passed']}/{r['total']}"} for mode, sets in s.items() for name, r in sets.items()],
                     use_container_width=True, hide_index=True)
    if not shown:
        st.caption("No evaluation results found. Run `python -m eval.run_eval`.")
    st.markdown("#### Ragas scores (extra, model-judged)")
    ragas_files = sorted(results.glob("ragas_*.json"))
    if not ragas_files:
        st.caption("Not run yet. `python -m eval.ragas_eval --split dev --strategy C` (about 20 minutes on the course server).")
    for f in ragas_files:
        r = json.loads(f.read_text())
        st.caption(f"{r['split']} split · strategy {r['strategy']} · {'written answers' if r.get('drafted', False) else 'answers built from check results'} · {r['queries_judged']} queries judged · judge {r['judge_model']}")
        names = {"faithfulness": "Faithfulness", "answer_relevancy": "Answer relevancy",
                 "llm_context_precision_without_reference": "Context precision"}
        st.dataframe([{"Metric": names.get(k, k), "Average": v, "Queries with no score": r["failed_scores"].get(k, 0)}
                      for k, v in r["average"].items()], use_container_width=True, hide_index=True)
        with st.expander("Scores for each query"):
            st.dataframe([{"Query": q["id"], "Type": q["type"], "Question": q.get("question", ""), **{names.get(k, k): q.get(k) for k in r["average"]}, "Trace": q["trace_id"]}
                          for q in r["per_query"]], use_container_width=True, hide_index=True)
    st.caption("The judge is the 8B course model, so these scores are noisy and show a trend. Correctness is scored in code against the gold answers (table above).")


def main() -> None:
    if "user" not in ss:
        sign_in()
        return
    with st.sidebar:
        st.markdown(f"**{USERS[ss['user']]['name']}**  \n{USERS[ss['user']]['role'].replace('_', ' ')}")
        ss["page"] = st.radio("Go to", PAGES, index=PAGES.index(ss["page"]))
        st.caption(f"Model: {config.LLM_MODEL} · embeddings: {config.EMBED_MODEL} ({config.EMBED_SOURCE_LABEL})")
        if st.button("Sign out"):
            for k in ("user", "app_id"):
                ss.pop(k, None)
            st.rerun()
    {"Work queue": work_queue, "Application": application_page, "Compliance checks": checks_page,
     "Comments and sign-off": signoff_page, "FAQ": faq_page, "Prompt lab": prompt_lab_page, "Retrieval lab": retrieval_lab_page, "Activity log": activity_page, "History and evaluation": history_page}[ss["page"]]()


main()
