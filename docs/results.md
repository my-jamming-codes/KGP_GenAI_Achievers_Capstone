# Evaluation results

Run on 25 November 2026 (22:42 to 23:43 IST) with Qwen3-8B on the course server (thinking off, temperature 0), against the frozen gold file of 80 queries. The corpus is version `6074ba19e620`: 1,080 RBI documents (987 read from the official PDF, 93 from web pages) in 43,336 paragraph chunks, with embeddings from the course embeddings endpoint. Replies were not cached, so tokens and timings are real.

**Strategy C (plan, judge, self check) is correct on 56 of 60 test queries (0.933); strategy A (direct check) on 55 of 60 (0.917). Both cite the expected paragraph 97.5% of the time.** Two of C's four test failures are rule lookups whose expected paragraph was never retrieved (Q004, Q013). The other two are full rule checks where the model misread a limit again after correction and the result was withheld as "needs information" (Q016, Q024).

## Accuracy and citation rate

A query is correct when every expected rule result matches on status (and on risk weight where one is expected), or, for a lookup, when the answer holds the expected figure and cites an expected paragraph. Citation rate is the share of expected paragraphs that were cited.

| Split | Strategy | Accuracy | Citation rate | First pass accuracy | Queries the critic sent back | Tokens per query | Model calls | Run time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A. Direct check | 0.95 | 1.0 | 0.231 | 11 | 6,701 | 99 | 4.4 min |
| Dev (20) | B. Extract then judge | 0.95 | 1.0 | 0.154 | 12 | 10,034 | 175 | 8.9 min |
| Dev (20) | C. Plan, judge, self check | 0.95 | 1.0 | 0.308 | 10 | 7,429 | 96 | 7.3 min |
| Test (60) | A. Direct check | 0.917 | 0.975 | 0.395 | 32 | 6,919 | 304 | 14.6 min |
| Test (60) | C. Plan, judge, self check | 0.933 | 0.975 | 0.442 | 29 | 7,590 | 291 | 24.0 min |

All three strategies ran on the development queries; C and A ran on the test queries. B tied A and C on accuracy but used about 35% more tokens than C (10,034 against 7,429 per query) and had the lowest first-pass accuracy, so it did not go forward. No run hit the step cap or the time limit, and there were no schema validation retries.

**First pass accuracy** is the share of rule check queries where the model's first judgement was right on every expected rule, status and paragraph, before the critic intervened (13 dev queries, 43 test queries).

**Cost and latency.** Strategy C used about 7,600 tokens and 4.9 model calls per query and took 24 seconds on average; strategy A used about 6,900 tokens and 5.1 calls and took 15 seconds. These runs do not include the drafter, which adds one model call to an application check.

## Accuracy by query type (test split)

| Type | A | C |
| --- | --- | --- |
| Rule lookup | 9/11 | 9/11 |
| Scenario check | 21/24 | 22/24 |
| Lender variant | 11/11 | 11/11 |
| Amendment or change | 7/7 | 7/7 |
| Refusal and injection | 7/7 | 7/7 |

## The Q015 fix

Q015 asks what a guarantor to a bank home loan must be told. That rule is unverified in the rules file, so the right answer is "unknown". The lookup path checks the rules file in code and returns "unverified", so the model is never asked to draft an answer. Q015 is correct under both strategies.

**The fix was made after seeing test results,** so the test figures here are not a clean held-out measurement for rule lookups about unverified rules. The gold set was not changed.

## Retrieval

Recall at 5 on the 26 expected citations in the development queries, over the full 1,080 document corpus (25 November, PDF-based index).

| Mode | Recall at 5 |
| --- | --- |
| Dense only (bge-small-en-v1.5 in Qdrant) | 1.0 |
| BM25 only | 0.923 |
| Hybrid (reciprocal rank fusion) | 1.0 |
| Hybrid plus cross-encoder reranker | 1.0 |

Retrieval queries for rule checks are fixed phrases per check, written in RBI wording. Lookup questions are searched as asked, and that is where retrieval misses appear on the test split (Q004, Q013).

**Retrieval apart from answer quality** (`eval/rag_diagnostic.py`, over the queries with an expected paragraph, from the saved runs):

| Run | Queries | Pipeline recall | Answer accuracy | Accuracy if retrieved | Accuracy if missed | Generator errors | Retrieval errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dev, A | 17 | 1.0 | 0.941 | 0.941 | none | Q027 | none |
| Dev, B | 17 | 1.0 | 0.941 | 0.941 | none | Q027 | none |
| Dev, C | 17 | 1.0 | 0.941 | 0.941 | none | Q017 | none |
| Test, A | 51 | 0.961 | 0.902 | 0.939 | 0.0 | Q016, Q024, Q041 | Q004, Q013 |
| Test, C | 51 | 0.961 | 0.922 | 0.959 | 0.0 | Q016, Q024 | Q004, Q013 |

When the expected paragraph is retrieved, strategy C is right 96% of the time on test; when it is missed, it is never right.

## Safety tests

Re-run on 25 November.

| Set | Cases | Model that obeys any instruction it sees | Qwen3-8B (course server) |
| --- | --- | --- | --- |
| Prompt injection | 15 | 15/15 | 15/15 |
| PII masking | 15 | 15/15 | not model dependent |
| Refusal | 10 | 10/10 | 10/10 |

The injection set is first run with a fake model built to obey any instruction-like text in its prompt. It passes because the text is removed in code before the model sees it, and because nothing in the agent code can change an application's status. The seven refusal and injection queries in the gold test split also pass under both strategies.

## What the critic caught

Counts of first pass problems on the test split. Each was sent back with a reason and re-run once.

| Problem | A | C |
| --- | --- | --- |
| Status disagrees with the tool computation | 34 | 38 |
| Cited paragraph is not the one that holds the rule | 49 | 33 |
| Limit or weight read from the paragraph is wrong | 4 | 3 |
| Required check missing from the plan | 0 | 0 |

The self check does not remove misread limits: C had 3 and A had 4. The model still misreads a limit on some applications (Q016, Q024, Q017), although the critic catches it.

## Engineering checks

| Check | Result |
| --- | --- |
| Unit and integration tests | 167 passing on 25 November |
| JSON-schema output on the endpoint | Accepted by the course endpoint; no validation retries under A, B or C in this run |
| Step cap | No run exceeded 10 steps |
| Time limit | No run stopped on the 240 second limit |
| Endpoint limits | One request at a time, kept under 55 a minute, 1,024 output tokens, retried when busy; no run failed on a busy or too-large reply |
| Docker | `docker compose up` starts the app and Qdrant and reports healthy; the image holds the 43,336-chunk index and the certificate folder is mounted read only |

## Limits on these numbers

- The gold labels were written from the verified rules and have not had a second person's review.
- Each strategy ran once, so a difference of one or two queries is within run-to-run variation.
- The development queries were used to choose strategy C and to look at failures, so dev accuracy (0.95) is optimistic. The test split is the fairer measure.
- The conventions that decide "breached" against "needs information" are in `docs/labelling_guide.md` and are also given to the model, so the evaluation measures whether the model applies stated conventions to retrieved text.
- Final answers for rule checks are confirmed against deterministic tools, so final scenario accuracy mostly reflects whether the model can be brought into agreement within one retry. First pass accuracy is the fairer measure of the model's own reading, and it is low (0.15 to 0.44).
- The credit scoring layer was not built, so there is no NPA, AUC or ablation table.
- Only one of the four chunking schemes has been measured (`docs/rag.md`).

## Ragas report (extra, model-judged)

`python -m eval.ragas_eval --split dev --strategy C` scores the dev answers with Ragas 0.4. Correctness is scored in code by `eval/run_eval.py` against the gold file; this report adds model-judged and tool-call figures beside it. The judge is the course model, Qwen3-8B, one request at a time. Run on 25 November 2026 on the dev split: 18 queries (the 2 refusal queries have no answer), strategy C, corpus version `6074ba19e620`.

The judge is given what the drafting step was given, recorded from the real prompt:

- **Rule lookups:** the retrieved paragraphs.
- **Application checks:** the check results and the cited paragraphs. Faithfulness here means the note is supported by one or the other.
- **Tools and goal:** the reference is one tool call per rule check the gold answer lists, with the case's lender type as its argument.

| Group (queries) | Metric | Average |
| --- | --- | --- |
| Rule lookups (5) | Faithfulness | 0.90 |
| | Answer relevancy | 0.72 |
| | Context precision | 0.96 |
| Application checks (13) | Faithfulness | 0.77 |
| | Answer relevancy | 0.61 |
| Tools and goal (13) | Tool recall (our figure, not a Ragas metric) | 1.00 |
| | Tool call F1 | 0.85 |
| | Tool call accuracy | 0.69 |
| | Agent goal accuracy | 0.77 |

The judge is the same model that writes the answers, so these scores are indicative. On the 4 queries that run all 10 checks, tool call F1 and accuracy are lower because the gold file lists only the graded rules.
