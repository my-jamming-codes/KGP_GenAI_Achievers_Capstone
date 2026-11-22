# Evaluation results

> **Corpus note (18 November).** These results were measured on the 13 November corpus: 1,080 documents from RBI web pages, 42,204 chunks, embeddings computed in-process. The corpus has since gained PDF ingestion (987 documents from the official PDF in the current index) and the embeddings now come from the course embeddings endpoint. The current index has 1,080 documents (987 from the official PDF, 93 from web pages) and 43,336 chunks. Retrieval was re-measured on it (dev recall at 5: dense 1.0, BM25 0.923, hybrid 1.0, hybrid plus reranker 1.0, up from 0.962 for dense and hybrid); the end-to-end evaluation below has not yet been re-run on it.

Run on 13 November 2026 with `qwen3:8b` on a local Ollama endpoint (thinking off, temperature 0), against the frozen gold file of 80 queries. Corpus version `bd906e801acb`: 1,080 RBI documents, 42,204 paragraph chunks. This run includes the Q015 fix described below; the 12 November run without it is kept in `eval/results/before_q015_fix_*`.

**Headline: strategy C (plan, judge, self check) is correct on 56 of 60 test queries (0.933); strategy A (direct check) on 51 of 60 (0.85). Both cite the expected paragraph 97.5% of the time.** Three of C's four test failures are rule lookup questions. The fourth, Q026, stopped at the time limit when the local model stalled, and was correct on two re-runs.

## Before and after the Q015 fix

Q015 asks what a guarantor to a bank home loan must be told. That rule is unverified in the rules file, so the right answer is "unknown". On 12 November both strategies answered it from an unrelated capital adequacy paragraph. The lookup path now checks the rules file in code and returns "unverified" in code, so the model is never asked to draft an answer.

**This fix was made after seeing test results, so the 13 November test numbers are not a clean held-out measurement.** The 12 November numbers are the clean ones.

| Test split (60) | 12 Nov, before fix | 13 Nov, after fix | What changed |
| --- | --- | --- | --- |
| A accuracy | 0.833 (50/60) | 0.85 (51/60) | Q015 now correct |
| C accuracy | 0.933 (56/60) | 0.933 (56/60) | Q015 now correct; Q026 stopped at the time limit |
| A, C citation rate | 0.975 | 0.975 | No change |
| Rule lookup, A and C | 7/11 | 8/11 | Q015 |
| Scenario check, C | 24/24 | 23/24 | Q026 |

Q026 is a full check of application HL-2051. In the 13 November run it took 651 seconds against a limit of 240, where the same query took 90 seconds on 12 November. The run stopped as designed and showed every unconfirmed rule as "needs information". Re-run twice on its own afterwards, it was correct both times in 84 and 76 seconds (`eval/results/q026_recheck.jsonl`). The table keeps the result from the full run. Counting the re-run instead would put C at 57 of 60 (0.95).

Dev results did not change with the fix: the same queries failed for the same reasons.

## Accuracy and citation rate

A query is correct when every expected rule result matches on status (and on risk weight where one is expected), or, for a lookup, when the answer holds the expected figure and cites an expected paragraph. Citation rate is the share of expected paragraphs that were cited.

| Split | Strategy | Accuracy | Citation rate | First pass accuracy | Queries the critic sent back | Tokens per query | Run time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A. Direct check | 0.85 | 0.962 | 0.231 | 11 | 6,323 | 4 min |
| Dev (20) | B. Extract then judge | 0.85 | 0.962 | 0.231 | 10 | 7,863 | 7 min |
| Dev (20) | C. Plan, judge, self check | 0.95 | 0.962 | 0.308 | 9 | 6,757 | 6 min |
| Test (60) | A. Direct check | 0.85 | 0.975 | 0.488 | 31 | 6,395 | 14 min |
| Test (60) | C. Plan, judge, self check | 0.933 | 0.975 | 0.465 | 27 | 6,798 | 39 min |

All three strategies ran on the development queries; the best two, C and A, ran on the test queries. B tied A on dev but cost about 24% more tokens and produced the only schema failure, so A went forward. C's test run time includes the 11 minute stall on Q026; it took 22 minutes on 12 November.

**First pass accuracy** is the share of rule check queries where the model's first judgement was right on every expected rule, status and paragraph, before the critic intervened (13 dev queries, 43 test queries). It is low for every strategy. The gap between it and final accuracy is the work the critic does.

## Accuracy by query type (test split)

| Type | A | C |
| --- | --- | --- |
| Rule lookup | 8/11 | 8/11 |
| Scenario check | 19/24 | 23/24 |
| Entity variant | 11/11 | 11/11 |
| Amendment or change | 6/7 | 7/7 |
| Refusal and injection | 7/7 | 7/7 |

## Retrieval

Recall at 5 on the 26 expected citations in the development queries, over the full 1,080 document corpus.

| Mode | Recall at 5 |
| --- | --- |
| Dense only (bge-small-en-v1.5 in Qdrant) | 0.962 |
| BM25 only | 0.923 |
| Hybrid (reciprocal rank fusion) | 0.962 |
| Hybrid plus cross-encoder reranker | 1.0 |

Retrieval queries for rule checks are fixed phrases per check, written in RBI wording. Lookup questions are searched as asked, and that is where retrieval misses appear on the test split.

## Safety tests

Re-run on 13 November with the fix in place; no change.

| Set | Cases | Model that obeys any instruction it sees | qwen3:8b |
| --- | --- | --- | --- |
| Prompt injection | 15 | 15/15 | 15/15 |
| PII masking | 15 | 15/15 | not model dependent |
| Refusal | 10 | 10/10 | 10/10 |

The injection set is first run with a fake model built to obey any instruction-like text in its prompt. It passes because the text is removed in code before the model sees it, and because nothing in the agent code can change an application's status. The seven refusal and injection queries in the gold test split also pass under both strategies.

## What the critic caught

Counts of first pass problems on the test split. Each was sent back with a reason and re-run once.

| Problem | A | C |
| --- | --- | --- |
| Status disagrees with the tool computation | 32 | 36 |
| Cited paragraph is not the one that holds the rule | 32 | 30 |
| Limit or weight read from the paragraph is wrong | 10 | 0 |
| Required check missing from the plan | 1 | 1 |

Strategy C's self check removed the wrong-number problem entirely, which is where A lost its scenario queries: after one retry A still misread the limit on five queries, and those results were withheld as "needs information" instead of being shown.

## Engineering checks

| Check | Result |
| --- | --- |
| Unit and integration tests | 101 passing at the time of the evaluation; 138 on 18 November, after LangGraph, conversation memory, PDF ingestion, the activity log and the lab pages were added (tools, agent loop, memory, safety sets, cache, rate limit, workspace pages) |
| JSON-schema output on the endpoint | Supported by Ollama; no validation retries under A or C, three under B |
| Step cap | No run exceeded 10 steps; a full check with one retry uses 8 or 9 |
| Time limit | One run in 180 hit the 240 second limit (Q026, strategy C) and stopped with unconfirmed results withheld |
| Docker | `docker compose up` starts the app and Qdrant, loads 42,204 points from the saved snapshot, and ran a rule check against the host model |

## Limits on these numbers

- The gold labels were written from the verified rules and have not had a second person's review.
- The Q015 fix was made after seeing test results. Treat the 12 November test numbers as the held-out result and the 13 November numbers as a check that the fix works.
- The conventions that decide "breached" against "needs information" are in `docs/labelling_guide.md` and are also given to the model, so the evaluation measures whether the model applies stated conventions to retrieved text.
- Final answers for rule checks are confirmed against deterministic tools, so final scenario accuracy mostly reflects whether the model can be brought into agreement within one retry. First pass accuracy is the fairer measure of the model's own reading.
- Two runs on one local 8B model. Across the two runs the same queries failed with the same labels, apart from Q015 (fixed) and Q026 (stall). The course endpoint model was not available, so its numbers may differ.
- The credit scoring layer was not built, so there is no NPA, AUC or ablation table.

## Ragas report (extra, model-judged)

`python -m eval.ragas_eval --split dev --strategy C` scores the dev answers with Ragas 0.4: faithfulness (is each claim in the answer supported by the retrieved paragraphs), answer relevancy (does the answer address the question) and context precision without a reference (were the retrieved paragraphs useful for the question). It uses no gold answer and no rules file, so it does not measure correctness; `eval/run_eval.py` does that against the gold file. The judge is the course model, Qwen3-8B, one request at a time, with at most 4 paragraphs of 700 characters per call.

Run on 22 to 23 November 2026 on the dev split: 18 queries with an answer to judge (the 2 refusal queries have none). Strategy C, corpus version `6074ba19e620`.

| Answers judged | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Built from the check results (`--no-draft`) | 0.124 | 0.402 | 0.758 |
| Written by the drafter (default) | 0.223 | 0.505 | 0.668 |

By query type, with written answers: lookup (4) 0.31 / 0.90 / 0.81; rule check on an application and described scenarios (6) 0.10 / 0.26 / 0.65; lender variants (4) 0.24 / 0.37 / 0.42; amendments (3) 0.33 / 0.56 / 0.86. Per-query scores with the question text are in `eval/results/ragas_dev_C.json` and `ragas_dev_C_nodraft.json`, and on the History and evaluation page.

How to read it. The scores are low and noisy, and they disagree with the code-scored accuracy of 0.95 for strategy C on dev. Short lookup answers score well on relevancy (0.82 to 0.99). Structured rule-check replies often score 0, a sign that the 8B judge struggles to split a table of check results into claims, so a 0 does not mean the answer was wrong. Only context precision is a steady signal, and it shows retrieval returning a useful paragraph for most queries (weakest: Q032, Q051). Written answers raised faithfulness and relevancy a little and lowered precision; with 18 queries and a model judge those differences are within the noise. Treat the report as a trend and use the code-scored accuracy as the result.
