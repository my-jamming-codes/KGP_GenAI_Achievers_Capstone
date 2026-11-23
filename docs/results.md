# Evaluation results

Run on 23 November 2026 (00:27 to 01:19 IST) with Qwen3-8B on the course server (thinking off, temperature 0), against the frozen gold file of 80 queries. The corpus is version `6074ba19e620`: 1,080 RBI documents (987 read from the official PDF, 93 from web pages) in 43,336 paragraph chunks, with embeddings from the course embeddings endpoint. The code is the current code, including the Q015 fix. Replies were not cached, so tokens and timings are real. The previous run (13 November, a local model, web-page corpus) is kept in `eval/results/before_course_endpoint/` and summarised in "Compared with the 13 November run" below.

**Headline: strategy C (plan, judge, self check) is correct on 57 of 60 test queries (0.95); strategy A (direct check) on 55 of 60 (0.917). Both cite the expected paragraph 97.5% of the time.** Two of C's three test failures are rule lookup questions whose expected paragraph was never retrieved (Q004 and Q013). The third, Q024, is a full rule check where the model read the wrong LTV band twice and the result was withheld as "needs information".

## Accuracy and citation rate

A query is correct when every expected rule result matches on status (and on risk weight where one is expected), or, for a lookup, when the answer holds the expected figure and cites an expected paragraph. Citation rate is the share of expected paragraphs that were cited.

| Split | Strategy | Accuracy | Citation rate | First pass accuracy | Queries the critic sent back | Tokens per query | Model calls | Run time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A. Direct check | 1.0 | 1.0 | 0.231 | 11 | 6,710 | 99 | 3.5 min |
| Dev (20) | B. Extract then judge | 0.95 | 1.0 | 0.154 | 13 | 10,170 | 177 | 8.4 min |
| Dev (20) | C. Plan, judge, self check | 0.95 | 1.0 | 0.308 | 10 | 7,440 | 96 | 6.9 min |
| Test (60) | A. Direct check | 0.917 | 0.975 | 0.395 | 32 | 6,947 | 305 | 11.2 min |
| Test (60) | C. Plan, judge, self check | 0.95 | 0.975 | 0.465 | 30 | 7,587 | 291 | 21.0 min |

All three strategies ran on the development queries; C and A ran on the test queries, as before. B tied C on accuracy but used about 37% more tokens than C (10,170 against 7,440 per query) and had the lowest first-pass accuracy, so it did not go forward. No run hit the step cap or the time limit, and there were no schema validation retries.

**First pass accuracy** is the share of rule check queries where the model's first judgement was right on every expected rule, status and paragraph, before the critic intervened (13 dev queries, 43 test queries). It is low for every strategy. The gap between it and final accuracy is the work the critic does.

**Cost and latency.** Strategy C used about 7,600 tokens and 4.9 model calls per query and took 21 seconds on average; strategy A used about 6,900 tokens and 5.1 calls and took 11 seconds.

## Accuracy by query type (test split)

| Type | A | C |
| --- | --- | --- |
| Rule lookup | 9/11 | 9/11 |
| Scenario check | 21/24 | 23/24 |
| Lender variant | 11/11 | 11/11 |
| Amendment or change | 7/7 | 7/7 |
| Refusal and injection | 7/7 | 7/7 |

## Compared with the 13 November run

| | 13 Nov (local model, web-page corpus) | 23 Nov (course model, PDF corpus) |
| --- | --- | --- |
| Test accuracy, C | 0.933 (56/60) | 0.95 (57/60) |
| Test accuracy, A | 0.85 (51/60) | 0.917 (55/60) |
| Dev accuracy, A, B, C | 0.85, 0.85, 0.95 | 1.0, 0.95, 0.95 |
| Test first-pass accuracy, A, C | 0.488, 0.465 | 0.395, 0.465 |
| Citation rate, dev | 0.962 | 1.0 |
| Test run time, C | 39 min (one 11 minute stall) | 21 min |
| Test queries wrong, C | Q001, Q004, Q013, Q026 | Q004, Q013, Q024 |
| Test queries wrong, A | Q001, Q004, Q013, Q016, Q020, Q024, Q040, Q041, Q061 | Q004, Q013, Q016, Q024, Q041 |

What changed on named queries:

- **Q001** (the loan amount's LTV band, which both strategies misread as 80% instead of 90%) is now right under both strategies. Q020, Q040 and Q061, wrong under A before, are right too.
- **Q026** (a full check that hit the 240 second limit on 13 November) completed normally under C. Nothing stopped on a time limit in this run.
- **Q004 and Q013** are wrong again in the same way: the expected paragraph (`HFC-MD/22`, `NBFC-KYC/23`) is not in the top five. Retrieval recall on the development queries is unchanged at 1.0 for hybrid, so the misses are on free-text lookups.
- **Q024** is wrong under both strategies and was wrong under A before. It is the same failure each time: the bank loan band is read as 80 where the rules file gives 90.
- **Q016 and Q041** are still wrong under A. Strategy C gets both right.
- **Q017 (dev, C)** is new: on application HL-2042 the model read 80 for the LTV limit twice, so the LTV result was withheld as "needs information".

These results are not a controlled comparison. Between the two runs the model server, the corpus and the embeddings changed, and each run is a single pass, so differences of a query or two (for example A's gain from 0.85 to 0.917, or the fix on Q001) may be run-to-run variation as much as any real improvement. The run time fell mostly because the course server answered faster than the local model did.

## Before and after the Q015 fix

Q015 asks what a guarantor to a bank home loan must be told. That rule is unverified in the rules file, so the right answer is "unknown". On 12 November both strategies answered it from an unrelated capital adequacy paragraph. The lookup path now checks the rules file in code and returns "unverified" in code, so the model is never asked to draft an answer. Q015 is correct under both strategies in this run too.

**The fix was made after seeing test results in November, so the 12 November test numbers (A 0.833, C 0.933) are the clean held-out measurement of the earlier code.** Every number above comes from code that already had the fix, and the gold set was not changed. The earlier runs are kept in `eval/results/before_q015_fix_*` and `eval/results/before_course_endpoint/`.

## Retrieval

Recall at 5 on the 26 expected citations in the development queries, over the full 1,080 document corpus (re-run on 23 November on the PDF-based index).

| Mode | Earlier index (web pages only) | Current index (with PDFs) |
| --- | --- | --- |
| Dense only (bge-small-en-v1.5 in Qdrant) | 0.962 | 1.0 |
| BM25 only | 0.923 | 0.923 |
| Hybrid (reciprocal rank fusion) | 0.962 | 1.0 |
| Hybrid plus cross-encoder reranker | 1.0 | 1.0 |

Retrieval queries for rule checks are fixed phrases per check, written in RBI wording. Lookup questions are searched as asked, and that is where retrieval misses appear on the test split (Q004, Q013).

**Retrieval apart from answer quality** (`eval/rag_diagnostic.py`, over the queries with an expected paragraph, from the saved runs):

| Run | Queries | Pipeline recall | Answer accuracy | Accuracy if retrieved | Accuracy if missed | Generator errors | Retrieval errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dev, A | 17 | 1.0 | 1.0 | 1.0 | none | none | none |
| Dev, B | 17 | 1.0 | 0.941 | 0.941 | none | Q027 | none |
| Dev, C | 17 | 1.0 | 0.941 | 0.941 | none | Q017 | none |
| Test, A | 51 | 0.961 | 0.902 | 0.939 | 0.0 | Q016, Q024, Q041 | Q004, Q013 |
| Test, C | 51 | 0.961 | 0.941 | 0.98 | 0.0 | Q024 | Q004, Q013 |

When the expected paragraph is retrieved, strategy C is right 98% of the time on test; when it is missed, it is never right. The two retrieval errors are the only misses that retrieval alone explains.

## Safety tests

Re-run on 23 November.

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
| Status disagrees with the tool computation | 35 | 38 |
| Cited paragraph is not the one that holds the rule | 51 | 33 |
| Limit or weight read from the paragraph is wrong | 2 | 3 |
| Required check missing from the plan | 0 | 0 |

On 13 November strategy C had no wrong-number problems on the test split and A had 10. In this run C has 3 and A has 2, so the claim that the self check removes that problem no longer holds: the model still misreads a limit band on some bank applications (Q024 and Q017 are the two that ended unconfirmed), although the critic catches it every time. Wrong-paragraph citations under A rose from 32 to 51, and all were corrected.

## Engineering checks

| Check | Result |
| --- | --- |
| Unit and integration tests | 165 passing on 23 November |
| JSON-schema output on the endpoint | Accepted by the course endpoint; no validation retries under A, B or C in this run |
| Step cap | No run exceeded 10 steps |
| Time limit | No run stopped on the 240 second limit |
| Endpoint limits | One request at a time, kept under 55 a minute, 1,024 output tokens, retried when busy; no run failed on a busy or too-large reply |
| Docker | `docker compose up` starts the app and Qdrant and reports healthy; the image holds the 43,336-chunk index and the certificate folder is mounted read only |

## Limits on these numbers

- The gold labels were written from the verified rules and have not had a second person's review.
- Each strategy ran once per split. Differences of one or two queries are within run-to-run variation, so the comparison with 13 November is indicative only.
- The development queries were used to choose strategy C and to look at failures, so dev accuracy (1.0 for A, 0.95 for B and C) is optimistic. The test split is the fairer measure.
- The conventions that decide "breached" against "needs information" are in `docs/labelling_guide.md` and are also given to the model, so the evaluation measures whether the model applies stated conventions to retrieved text.
- Final answers for rule checks are confirmed against deterministic tools, so final scenario accuracy mostly reflects whether the model can be brought into agreement within one retry. First pass accuracy is the fairer measure of the model's own reading, and it is low (0.15 to 0.47).
- The credit scoring layer was not built, so there is no NPA, AUC or ablation table.
- Only one of the four chunking schemes has been measured (`docs/rag.md`).

## Ragas report (extra, model-judged)

`python -m eval.ragas_eval --split dev --strategy C` scores the dev answers with Ragas 0.4: faithfulness (is each claim in the answer supported by the retrieved paragraphs), answer relevancy (does the answer address the question) and context precision without a reference (were the retrieved paragraphs useful for the question). It uses no gold answer and no rules file, so it does not measure correctness; `eval/run_eval.py` does that against the gold file. The judge is the course model, Qwen3-8B, one request at a time, with at most 4 paragraphs of 700 characters per call.

Run on 22 to 23 November 2026 on the dev split: 18 queries with an answer to judge (the 2 refusal queries have none). Strategy C, corpus version `6074ba19e620`.

| Answers judged | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Built from the check results (`--no-draft`) | 0.124 | 0.402 | 0.758 |
| Written by the drafter (default) | 0.223 | 0.505 | 0.668 |

By query type, with written answers: lookup (4) 0.31 / 0.90 / 0.81; rule check on an application and described scenarios (6) 0.10 / 0.26 / 0.65; lender variants (4) 0.24 / 0.37 / 0.42; amendments (3) 0.33 / 0.56 / 0.86. Per-query scores with the question text are in `eval/results/ragas_dev_C.json` and `ragas_dev_C_nodraft.json`, and on the History and evaluation page.

How to read it. The scores are low and noisy, and they disagree with the code-scored accuracy of 0.95 for strategy C on dev. Short lookup answers score well on relevancy (0.82 to 0.99). Structured rule-check replies often score 0, a sign that the 8B judge struggles to split a table of check results into claims, so a 0 does not mean the answer was wrong. Only context precision is a steady signal, and it shows retrieval returning a useful paragraph for most queries (weakest: Q032, Q051). Written answers raised faithfulness and relevancy a little and lowered precision; with 18 queries and a model judge those differences are within the noise. Treat the report as a trend and use the code-scored accuracy as the result.
