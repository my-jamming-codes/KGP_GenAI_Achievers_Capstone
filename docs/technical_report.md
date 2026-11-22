# RBI Home Loan Rule Checker (Loan Guarantor): technical report

Track B capstone, "Multi-Agent Operations Copilot built entirely on a hosted 8B inference API". November 2026.

## 1. Summary

The assistant tests a synthetic home loan case, including its guarantor, against the Reserve Bank of India (RBI) Directions. It lists each rule as met, breached, needs information, not applicable or unknown, and cites the RBI paragraph that says so. A compliance officer reviews the result and decides. The assistant never approves a loan and never declares a lender compliant.

It is built on one small model, Qwen3-8B, behind an OpenAI-compatible endpoint, with a separate embeddings endpoint. Everything else had to be won through prompting, retrieval, tools, orchestration and evaluation, which is what the track asks for.

| What | Result |
| --- | --- |
| Corpus | 1,080 RBI documents (987 read from the official PDF, 93 from web pages), 43,336 paragraph chunks |
| Retrieval | Hybrid BM25 plus dense search, then a cross-encoder reranker; recall at 5 is 1.0 on the 26 development citations |
| Agents | LangGraph graph with six roles: guard, planner, executor, judge, critic, drafter; 10 rule tools plus search |
| Accuracy on 60 held-out queries | Strategy C (plan, judge, self check) 56 of 60 (0.933); strategy A (direct check) 51 of 60 (0.85) |
| Cost per query | About 6,800 tokens and 4.6 model calls; 14 to 39 seconds |
| Safety sets | 40 of 40 cases passed (15 prompt injection, 15 PII, 10 refusal) |
| Deployment | Streamlit workspace in Docker Compose with Qdrant; caching, rate limiting and endpoint limit handling |

Two things matter most for reading the numbers. First, the model alone is often wrong: on the first pass it was right on under half of the rule checks. The final accuracy is high because every model judgement is compared in code with a plain tool result and sent back once if it disagrees. Section 6 describes this, since "what did we do to compensate for a small model" is the central design question. Second, the accuracy figures are from a run on 13 November, before PDF ingestion and the move to the course endpoints; retrieval was re-measured on the new index, and the full evaluation re-run is still pending (section 11).

## 2. Task decomposition and the gold set

**What the assistant does and refuses.** It picks the rulebook for the lender type (housing finance company, NBFC or bank), computes the loan to value ratio (LTV), compares it with the cap, checks the risk weight band, valuation reports, prepayment charge, key facts statement, know your customer (KYC) documents, rate reset communication, related party lending and guarantor disclosure, and drafts a cited note. It refuses to approve or reject a loan, declare compliance, predict repayment, give legal advice, or use real customer data. Refusal is code in the guard step, not a request to the model.

**Written specification.** `docs/RBI Home Loan Rule Checker (Loan Guarantor).md` (also as PDF) states scope, the rules, the scenario inputs, the architecture and the refusal policy. `docs/labelling_guide.md` defines each status and how gold answers and failures are labelled. The conventions that separate "breached" from "needs information" are written there and are also given to the model.

**The gold set.** There are 80 queries, frozen before any agent code was written: 15 rule lookups, 30 scenario checks, 15 lender-type variants, 10 amendment questions and 10 refusal or injection queries. Twenty are for development and 60 are held out for testing; 25 of the 80 run on 12 synthetic applications (HL-2041 to HL-2052). Every expected answer was typed by hand from the verified rules, not produced by the application, so a bug in the application cannot pass its own test. The builder script `gold/build_gold.py` is kept so a reviewer can check the labels. Only `eval/run_eval.py` reads the gold file.

**Verified rules.** Before any rule was used, its limit was read from the RBI text and recorded with a quote in `rules/rules.yaml`: 30 records from 13 sources, of which 29 are verified. The one unverified record is the disclosure a bank must make to a guarantor, because no RBI paragraph for banks was found. The tools and the critic read this file, so a limit appears in one place only, and a check on the unverified rule returns "unknown" instead of a guess.

## 3. Prompt engineering baseline

Three strategies were compared on the same retrieval and the same 20 development queries.

| Strategy | What it does | Dev accuracy | Tokens per query | Schema retries |
| --- | --- | --- | --- | --- |
| A. Direct check | One structured prompt per rule, with few-shot examples | 0.85 | 6,323 | 0 |
| B. Extract, then judge | First extract the values from the paragraph, then judge | 0.85 | 7,863 | 3 |
| C. Plan, judge, self check | Plan the checks, judge each, then check its own judgement | 0.95 | 6,757 | 0 |

Strategy C was chosen on the development queries only, and then run once on the held-out test queries together with A. B tied A on accuracy but used about 24% more tokens and produced the only schema failure, so it did not go forward.

**How the small model breaks.** The failures were specific and repeatable, and each one shaped the design:

- **It reads the wrong band.** With every LTV band in the paragraph, it gave the limit of the next band: 80% for a ₹25 lakh loan where the paragraph says 90% (Q001). The number appears in the cited text, so a simple grounding check passes it.
- **It cites a paragraph that does not hold the rule.** On the test split, 32 first-pass judgements under A and 30 under C cited the wrong paragraph. All were corrected by the critic.
- **It misses the second condition of a rule.** On HL-2048 (₹78 lakh, two valuation reports, one independent) every strategy saw two reports and judged the valuation rule met. Annex V requires both to be independent above ₹75 lakh. The Prompt lab page shows this live.
- **It ignores instructions in the prompt.** Told a rule is unverified, it answered from an unrelated capital adequacy paragraph (Q015). On a follow-up it kept the lender type from the history instead of the one asked about.
- **It repeats a mistake after being corrected.** Strategy A was told the right limit and gave the wrong one again on five test queries.
- **Long structured replies are cut off.** Strategy B's extract reply ran out of output tokens in the middle of a string and failed validation three times (Q022, dev).

First-pass accuracy, the share of rule-check queries where the model was right on every expected rule before any feedback, was 0.23 to 0.31 on dev and 0.47 to 0.49 on test. That gap is what the rest of the system exists to close.

## 4. Retrieval over a real corpus

**Corpus.** 1,080 documents from rbi.org.in: 8 for housing finance companies, 75 for NBFCs (several apply to housing finance companies), 130 for banks and 867 others (foreign exchange, payments, markets). The others are kept on purpose: the right paragraph must be found among unrelated RBI text. The corpus version is a hash of the chunk file (`6074ba19e620`); the answer cache and the vector collection are keyed on it.

**PDF ingestion.** Every RBI document has an official PDF, and some web page text is incomplete or carries site menus. The parser uses the PDF where one has been saved and passes quality checks (at least 60% of the web page's words and numbered paragraphs, and every paragraph the rules cite), and falls back to the web page otherwise. In the current index 987 documents come from PDFs and 93 from pages. Text is extracted with poppler's `pdftotext`, which kept words and a risk weight table cell that pypdf lost. A chunk from a PDF keeps the paragraph id the rules cite (for example `CB-CF/111`).

**Chunking.** The production scheme is paragraph-aware: each numbered RBI paragraph is one chunk, the unit an officer cites. A paragraph longer than 220 words is split between lines into sub-chunks that keep the same paragraph id, and a nested list inside a paragraph does not start a new one. Each chunk carries its paragraph id, document, title, lender type, date and source format. Four schemes are defined for comparison (paragraph at 220 words, paragraph at 120, fixed 200-word windows, fixed 400-word windows with an 80-word overlap), but only the production scheme has been measured; the other three are not yet run (section 11).

**Hybrid search.** A query goes to BM25 (`rank-bm25`) and to dense search (bge-small-en-v1.5 vectors in Qdrant); each returns its top 40. The two lists are merged by reciprocal rank fusion, which scores each result as 1 / (60 + rank) in each list and adds the scores. The top 20 are reranked by the cross-encoder `ms-marco-MiniLM-L-6-v2`. A lender filter limits a housing finance case to housing finance and NBFC documents and a bank case to bank documents, which stops the most damaging error in this domain: citing the other lender type's rulebook. The index records which embedding model built it and refuses to be searched with another.

**Recall measured apart from answer quality.** Recall at 5 on the 26 expected citations in the development queries:

| Mode | Earlier index (web pages only) | Current index (with PDFs) |
| --- | --- | --- |
| Dense only | 0.962 | 1.0 |
| BM25 only | 0.923 | 0.923 |
| Hybrid | 0.962 | 1.0 |
| Hybrid plus reranker | 1.0 | 1.0 |

On the current dev queries the reranker no longer changes recall at 5, because dense and hybrid already reach 1.0; its benefit was at rank 1 on the earlier index. On the held-out test queries recall at 5 for the production scheme on the earlier index was 0.852 dense, 0.951 BM25, 0.951 hybrid and 0.951 hybrid plus reranker. A diagnostic (`eval/rag_diagnostic.py`) separates retrieval errors from generator errors. Rule checks search with fixed phrases in RBI wording; free-text lookups are searched as asked, which is where the two retrieval misses on the test split came from (Q004, Q013).

**Citation-grounded answers.** Every answer cites paragraph ids. The critic rejects a citation that was not retrieved, comes from the wrong lender's rulebook, or does not contain the numbers in the answer.

## 5. The agentic layer

![Architecture](images/architecture.svg)

The flow runs on LangGraph (`src/agents/graph.py`): one node per agent step and conditional edges for the critic's verdict. The agents, tools and prompts are plain Python functions the nodes call, so the safety rules stay in code where they can be read and tested.

| Role | What it does | Model or code |
| --- | --- | --- |
| Guard | Refuses approval, compliance declarations, repayment predictions and legal advice; removes instruction-like text; masks personal identifiers; loads earlier turns for a follow-up | Code |
| Planner | Decides the intent (rule check, lookup, refuse, out of scope), the lender type and the checks to run | Model, with scope and required checks enforced in code |
| Executor | Runs the tool for each planned check and retrieves the paragraphs for it | Code |
| Judge | Reads the retrieved paragraph and gives a status, the paragraph id and the limit it read | Model (strategy A, B or C) |
| Critic | Compares each judgement with the tool result and the paragraph: status, number, citation, rulebook, missing checks. Reports every problem at once | Code |
| Drafter | Writes the cited note for the officer; falls back to a template if the model fails | Model, with a code fallback |

**Tools.** Ten rule check tools (LTV cap, risk weight, prepayment, key facts statement, guarantor disclosure, valuation, KYC, rate reset, related party, most important terms and conditions) plus the hybrid search tool and a `rule_in_force` tool that returns which version of a rule applies on a date. Only registered tools can run: `run_tool` looks the name up in a fixed table and raises for anything else, so a prompt cannot make other code run. All arithmetic and date logic lives in the tools.

**The loop.** After the guard and the planner, the executor runs the tools and the judge reads the paragraphs. The critic compares every judgement with the tool result. If any disagree, the failed checks go back once to the executor with the reason (the `revise` step) and are judged again. A judgement still unconfirmed after the retry is withheld and shown as "needs information" with the tool's numbers, so a wrong limit never reaches the officer. A lookup question takes a shorter path: guard, a check of the rules file, search, draft, critic.

**Persistent state.** Conversation is saved per officer and application and reused for follow-ups, across turns and across app restarts. A follow-up keeps the lender type unless the question names another.

**Error handling.**

| Failure | Handling |
| --- | --- |
| Tool raises | Recorded in the trace; the run continues with the check shown as not confirmed |
| Malformed JSON | Reply validated with Pydantic and retried; after the retries the step counts as a tool call error |
| Endpoint rejects JSON-schema output | Client falls back to plain JSON and validates it |
| Endpoint busy (429 or 503) | Retried after 2, 4 and 8 seconds, then a clear error |
| Infinite loop | Step cap of 10 per query, one critic retry, and a LangGraph recursion limit of 25 nodes |
| Token budget | 40,000 tokens per query, then a polite stop |
| Slow run | 240 second limit per query, then a stop that shows unconfirmed rules as "needs information" |

## 6. What we did to compensate for a small model

This is the answer to the track's graded question. The principle is that the model is allowed to read and explain, and everything that can be checked is checked in code.

1. **Tools do the arithmetic and the dates.** The model never computes LTV, never compares a number with a limit, and never decides which version of a rule applies on a date. A loan's LTV, the stamp duty adjustment and the risk weight band come from `src/tools/checks.py`.
2. **Every limit comes from a verified rules file.** The limits in `rules/rules.yaml` were read from the RBI text and recorded with a quote; `rules/check_quotes.py` re-checks every quote against the source text. The model is given the paragraph to read, but the number it is compared against comes from the file.
3. **A critic written in code compares every judgement.** It does not ask the model whether the model was right. It compares the status, the number, the paragraph, the rulebook and the list of required checks with the tool result, and reports every problem in one reply. On the test split it sent back 31 queries under A and 27 under C. The problems found (counts of first-pass problems on the test split):

| Problem | A | C |
| --- | --- | --- |
| Status disagrees with the tool computation | 32 | 36 |
| Cited paragraph is not the one that holds the rule | 32 | 30 |
| Limit or weight read from the paragraph is wrong | 10 | 0 |
| Required check missing from the plan | 1 | 1 |

   Strategy C's self check removed the wrong-number problem entirely, which is where A lost its scenario queries.
4. **One retry, then withhold.** A result that cannot be confirmed after one retry is not shown as a guess. It is shown as "needs information" with the tool's numbers.
5. **Unverified rules are handled in code.** The lookup path checks the rules file before any model call, and returns "unverified" for a rule that is not verified. This replaced a prompt instruction that the model ignored (the Q015 fix, section 7).
6. **Instruction-like text is removed before the model sees it.** Notes, questions and retrieved paragraphs are scanned; text that tries to give instructions is removed and marked as data.
7. **Structure and limits on every call.** Replies must match a JSON schema, are validated and retried, and each call has a step cap, a token budget and a time limit.
8. **Retrieval is narrowed by lender type.** A lender filter and fixed search phrases for rule checks keep the model from seeing the wrong rulebook.
9. **The model drafts last and cannot change a status.** The note is written after the results are confirmed, with a template as a fallback.

The cost of this design is that final scenario accuracy mostly reflects whether the model can be brought into agreement within one retry. First-pass accuracy is the fairer measure of the model's own reading, and it is reported alongside.

## 7. Evaluation

The evaluation script (`eval/run_eval.py`) runs every gold query through the same agent code as the application. A query is correct when every expected rule result matches on status (and on risk weight where one is expected), or, for a lookup, when the answer holds the expected figure and cites an expected paragraph. Model replies are not cached, so tokens and timings are real. The run below used `qwen3:8b` on a local Ollama endpoint, thinking off, temperature 0, corpus version `bd906e801acb` (1,080 documents, 42,204 chunks, web pages only).

| Split | Strategy | Accuracy | Citation rate | First-pass accuracy | Queries sent back by the critic | Tokens per query | Model calls | Run time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A | 0.85 | 0.962 | 0.231 | 11 | 6,323 | 97 | 4 min |
| Dev (20) | B | 0.85 | 0.962 | 0.231 | 10 | 7,863 | 139 | 7 min |
| Dev (20) | C | 0.95 | 0.962 | 0.308 | 9 | 6,757 | 93 | 6 min |
| Test (60) | A | 0.85 | 0.975 | 0.488 | 31 | 6,395 | 291 | 14 min |
| Test (60) | C | 0.933 | 0.975 | 0.465 | 27 | 6,798 | 277 | 39 min |

**Cost and latency.** Strategy C used about 6,800 tokens and 4.6 model calls per query; strategy A used about 6,400 tokens and 4.9 calls. Per query that is 14 seconds on average for A and 39 for C; C's average includes one 11 minute stall.

**By query type on the test split.**

| Type | A | C |
| --- | --- | --- |
| Rule lookup (11) | 8/11 | 8/11 |
| Scenario check (24) | 19/24 | 23/24 |
| Lender variant (11) | 11/11 | 11/11 |
| Amendment (7) | 6/7 | 7/7 |
| Refusal and injection (7) | 7/7 | 7/7 |

**Task completion and tool calls.** No run exceeded the 10 step cap; a full rule check with one critic retry takes 8 or 9 steps. One run in 180 hit the time limit.

**The Q015 fix.** Q015 asks what a guarantor to a bank home loan must be told. That rule is unverified, so the right answer is "unknown". On 12 November both strategies answered it from an unrelated capital adequacy paragraph; the model had been told in the prompt that the rule was unverified and ignored it. The fix moved the check into code. It was made after seeing test results, so the 12 November test numbers (A 0.833, C 0.933) are the clean held-out result, and the 13 November numbers above show that the fix works. After the fix, A is 51 of 60 and C is still 56 of 60: Q015 became correct, and one full check, Q026, hit the time limit when the local model stalled (651 seconds against a limit of 240), although it was correct on two re-runs in 84 and 76 seconds. Counting the re-run would put C at 57 of 60.

**Failure taxonomy.** Each wrong result carries one label: retrieval miss, reasoning error, tool call error or hallucination, assigned by the rules in the labelling guide. The labels are the script's first pass; the two-person agreement check on 10 results is still to be done.

| Split | Strategy | Wrong | Retrieval miss | Reasoning error | Tool call error | Hallucination |
| --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A | 3 | 0 | 3 | 0 | 0 |
| Dev (20) | B | 3 | 0 | 2 | 1 | 0 |
| Dev (20) | C | 1 | 0 | 1 | 0 | 0 |
| Test (60) | A | 9 | 2 | 2 | 0 | 5 |
| Test (60) | C | 4 | 2 | 1 | 1 | 0 |

One worked example per label, each with a trace to open (`docs/failure_taxonomy.md` lists them all):

- **Retrieval miss, Q004 (C).** The risk weight table (HFC paragraph 22) was not in the top five. The model answered 50%, which is right, from a notification, but the expected paragraph was never retrieved.
- **Reasoning error, Q001 (C).** The right paragraph was retrieved and cited, but the model read the 80% band for a ₹25 lakh loan where the paragraph gives 90%. The fix, not yet made, is to route lookups that name a loan amount through the LTV tool as rule checks already are.
- **Hallucination, Q020 (A).** On HL-2045 the model gave the right status but reported a limit of 80 where the band is 90. The critic sent it back with the correct figure and the model repeated 80, so the result was withheld. No wrong limit reached the officer, but the query counts as wrong. C had no failure of this kind.
- **Tool call error, Q026 (C).** The local model stalled and the run stopped at the time limit; the unconfirmed rules were shown as "needs information".

**Retrieval on the new index.** Section 4 reports it: dense and hybrid recall at 5 are 1.0 on dev, up from 0.962.

**Ragas report (extra, model-judged).** `eval/ragas_eval.py` scores the dev answers with Ragas 0.4 on three metrics that need no gold answer: faithfulness (is each claim in the answer supported by the retrieved paragraphs), answer relevancy and context precision. The judge is the course model, one request at a time. On the 18 dev queries with an answer to judge, strategy C scored the following:

| Answers judged | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Built from the check results | 0.124 | 0.402 | 0.758 |
| Written by the drafter | 0.223 | 0.505 | 0.668 |

These scores are low and noisy, and they disagree with the code-scored accuracy of 0.95 for C on the same queries. Short lookup answers score well on relevancy (0.82 to 0.99), while structured rule-check replies often score 0, which suggests the 8B judge cannot split a long multi-rule reply into claims; a 0 does not mean the answer was wrong. Only context precision is a steady signal, and it shows retrieval returning a useful paragraph for most queries. The report is a trend shown next to the code-scored accuracy, not a replacement for it. Ragas never sees the rules file or the gold answers, so it cannot catch an answer that is faithful to its paragraph but contradicts the rule the tools applied; the code check does.

## 8. Observability and tracing

Every question or rule check writes one JSON trace: the trace id, user, application, question and strategy; every step with its agent, its input and output after personal identifiers are masked, the retrieved paragraph ids with scores, the tool name, arguments and result, and the critic verdict; the latency and token counts of every model call; and the officer's action at the end. A separate activity log lists every model, embedding, Qdrant, BM25, rerank and tool call with timings and tokens, and holds no prompt text.

The History and evaluation page lists traces and the officer's actions for an application, opens one trace step by step, and shows the latest evaluation, safety and Ragas results. It is also the audit trail. The Prompt lab runs strategies A, B and C side by side on one check and compares each with the tool's result and the tokens used. The Retrieval lab compares the four retrieval modes for one query.

The tracing is the application's own. LangSmith, Langfuse and OpenTelemetry are not used. The traces cover the same ground (each step, tokens, latency, errors) but are not exported to one of those tools, and that is a gap against the track's wording.

## 9. Deployment

**Front end.** A Streamlit workspace with sign-in as one of three synthetic roles (compliance officer, credit processor, approver). Pages: Work queue, Application, Compliance checks, Comments and sign-off, FAQ, Prompt lab, Retrieval lab, Activity log, and History and evaluation. Only the compliance officer role can change an application's status; the agent code has no function that does.

**Containers.** `docker compose up` starts the app and a Qdrant server. The image copies the code, the rules, the synthetic applications and the saved index. The model servers stay outside Docker, and the folder with the certificate files is mounted read only.

**Course endpoints.** Chat and embeddings are separate OpenAI-compatible services, each with its own private certificate file (`LLM_CA_CERT`, `EMBED_CA_CERT`). The limits are one active request per key, 60 requests a minute, 8,192 tokens in total per call, 1,024 output tokens, and for embeddings 16 texts per call and 512 tokens per text. The client respects them: calls are queued one at a time, kept under 55 a minute, capped at 1,024 output tokens, retried after a busy reply, and a request that is too large raises a clear error. When the index is built, texts are sent 16 at a time and any text over 512 tokens is shortened and retried. The largest call measured in a full rule check was under 2,000 tokens.

**Caching and rate limiting.** Model replies and answers are cached on the question, the case, the strategy and the corpus and rules versions, so a repeated question costs nothing and a changed rule invalidates the answer. Each officer session is limited to six questions a minute.

**Tests.** 165 tests pass, covering the tools, the agent loop and graph, memory, the safety sets, the cache, the rate limit, the endpoint limit handling and the workspace pages.

## 10. Responsible AI

- **Synthetic data only.** All applications are invented; no real borrower data is used.
- **Human in the loop.** The assistant recommends. Accept, dispute, comment and sign-off are the officer's actions. The assistant cannot change a status, and it states that the officer decides.
- **Refusals.** Requests to approve or reject a loan, declare a lender compliant, predict repayment, or give legal advice are refused in the guard step. Ten refusal cases passed with the real model, and the seven refusal and injection queries in the gold test split pass under both strategies.
- **Prompt injection against the corpus and the notes.** Instruction-like text in notes, questions and retrieved paragraphs is removed in code and marked as data. The 15-case injection set was first run with a fake model built to obey any instruction it sees, and passed, because the text never reached it and because nothing in the agent code can change a status. It also passed with the real model.
- **Personal data.** Identity numbers and similar identifiers are masked before anything is shown, logged or sent to the model. Fifteen masking cases passed.
- **Citations every time.** Each result shows its paragraph, and the critic rejects a citation that was not retrieved.
- **Not a credit tool.** The checker answers "is this loan allowed under the rules", not "will this borrower repay".

## 11. Limits and next steps

1. **The accuracy figures are from 13 November.** They predate PDF ingestion, the course endpoints and the 43,336-chunk index, and were measured on a local Ollama model. Retrieval recall and the Ragas report were re-run on the new setup, and one full rule check on the course endpoints matches the expected result, but the full 80-query evaluation has not been re-run there. The course model may give different numbers.
2. **Gold labels have not had a second person's review.** The labelling guide and the failure label agreement check are also pending.
3. **Only one of four chunking schemes has been measured.** The comparison the track asks for is incomplete.
4. **The Q015 fix was made after seeing test results,** so the 13 November test numbers are not a clean held-out measurement. The 12 November numbers are.
5. **Final accuracy depends on the critic.** First-pass accuracy is the fairer measure of the model's own reading, and it is low (0.23 to 0.49).
6. **Rule lookups remain the weak spot.** Three of C's four test failures are lookups. The next change is to route questions that name an amount through the tools and to add fixed search phrases for lookups.
7. **Bank guarantor disclosure is unknown.** No RBI paragraph was found, so banks get "unknown" by design.
8. **Tracing is not LangSmith, Langfuse or OpenTelemetry.** The application's own traces cover the content but not the named tools.
9. **The Ragas scores are noisy.** An 8B judge is unreliable on structured replies, so they are shown as a trend.
10. **The credit scoring layer was not built.** The course provided no dataset or default target, so there is no NPA, AUC or ablation table, and the project makes no claim about default risk.
11. **Scope.** "Bank" means a commercial bank as the Directions define it. Regional rural banks, small finance banks and co-operative banks are out of scope.

## Appendix: where to find things

| What | Where |
| --- | --- |
| Install, build the index, run, Docker | `README.md` |
| Specification and refusal policy | `docs/RBI Home Loan Rule Checker (Loan Guarantor).md` and PDF |
| Verified rules | `rules/rules.yaml`, `rules/check_quotes.py` |
| Gold set, builder script and labels | `gold/gold_queries.jsonl`, `gold/build_gold.py`, `docs/labelling_guide.md` |
| Retrieval, chunking and the corpus | `docs/rag.md`, `src/ingest/`, `src/index/hybrid.py` |
| Agents, tools and the graph | `docs/agentic_layer.md`, `src/agents/`, `src/tools/checks.py` |
| Results, failure taxonomy and Ragas | `docs/results.md`, `docs/failure_taxonomy.md`, `eval/results/` |
| Walkthroughs and diagrams | `docs/notes/` |
| Demo script and video | `docs/demo_script.md`, `docs/demo_overview_5min.md`, `docs/demo/rule_checker_overview.mp4` |
| Run the evaluations | `python -m eval.run_eval`, `eval.retrieval_eval`, `eval.safety_eval --real`, `eval.ragas_eval` |
