# Failure taxonomy

Every wrong result in the evaluation carries one of four categories, assigned by the rules in `docs/labelling_guide.md`. The labels below are the script's first pass (`eval/failure_labels_dev.jsonl`, `eval/failure_labels_test.jsonl`); the two-person agreement check on 10 results is still to be done.

Counts and trace ids are from the 13 November 2026 run, on the corpus before PDF ingestion (see the note in `docs/results.md`), which includes the Q015 fix. The 12 November run without it is kept in `eval/results/before_q015_fix_*`.

## Counts by strategy

| Split | Strategy | Wrong | Retrieval miss | Reasoning error | Tool call error | Hallucination |
| --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A | 3 | 0 | 3 | 0 | 0 |
| Dev (20) | B | 3 | 0 | 2 | 1 | 0 |
| Dev (20) | C | 1 | 0 | 1 | 0 | 0 |
| Test (60) | A | 9 | 2 | 2 | 0 | 5 |
| Test (60) | C | 4 | 2 | 1 | 1 | 0 |

Against the 12 November run, the test split lost one reasoning error under each strategy (Q015) and gained one tool call error under C (Q026). Dev is unchanged.

No final result cited the wrong lender type's rulebook, so the `wrong_rulebook` note is empty. Wrong-paragraph citations on the first pass (32 for A, 30 for C on test) were all corrected by the critic.

## Counts by query type (test split)

| Type | A wrong | C wrong |
| --- | --- | --- |
| Rule lookup (11) | 3 | 3 |
| Scenario check (24) | 5 | 1 |
| Entity variant (11) | 0 | 0 |
| Amendment or change (7) | 1 | 0 |
| Refusal and injection (7) | 0 | 0 |

## Failures with trace ids

Traces are in `eval/results/traces_dev.jsonl` and `eval/results/traces_test.jsonl`.

| Query | Strategy | Category | Trace | What went wrong |
| --- | --- | --- | --- | --- |
| Q001 | A, C | Reasoning error | 8d6498b05354, bd9bf56b6260 | Answered 80% for a ₹25 lakh HFC loan; the paragraph retrieved says 90% |
| Q004 | A, C | Retrieval miss | fad196de9d99, 4c2a2fed9fb7 | HFC risk weight table (para 22) not in the top five |
| Q013 | A, C | Retrieval miss | 03223c2db418, 5b35d8f4a257 | NBFC KYC para 23 not in the top five |
| Q016 | A | Reasoning error | 7d92291026e8 | LTV judgement not confirmed after one retry |
| Q020, Q024, Q040 | A | Hallucination | ef0ad7641a66, 01c913e7184e, ca1e90774678 | LTV limit read as a number that is not the limit for the loan band |
| Q041, Q061 | A | Hallucination | c3ddf8387a6c, 50e1c9512fe9 | Risk weight read wrongly from the table |
| Q026 | C | Tool call error | 6818322f5293 | Stopped at the 240 second time limit before the retry was judged; correct on two re-runs |
| Q022, Q027 (dev) | A | Reasoning error | de52338fd2ee, 2be5f7a80e92 | Risk weight judgement not confirmed after one retry |
| Q017 (dev) | B | Reasoning error | eec7bf8f1410 | Risk weight judgement not confirmed after one retry |
| Q022 (dev) | B | Tool call error | 2384a1209e3e | Extract step reply was cut off; failed validation three times |
| Q070 (dev) | A, B, C | Reasoning error | 1e79ff2db158, 7c723260162c, ab3ca3cf23b1 | Did not give the 1 April 2027 start date or cite the 2027 Directions |

## One worked example per category

**Retrieval miss: Q004, strategy C.** The question asks for the risk weight on a ₹20 lakh HFC housing loan at 85% LTV. The search returned an older notification (`N-12824/3`), the NBFC capital Directions and HFC paragraphs 58 and 59, but not HFC paragraph 22, which holds the table. The model answered 50%, which is the right figure, from the notification, and the grounding check passed because the citation was a retrieved paragraph. It is marked wrong because the expected paragraph was never retrieved. Fix: for lookup questions, also run the fixed search phrase for the matching rule check, which reached recall of 1.0 on the dev set.

**Reasoning error: Q001, strategy C.** HFC paragraph 58 was retrieved and cited, but the model answered that the cap for a ₹25 lakh loan is 80% when the paragraph gives 90% for loans up to ₹30 lakh. The grounding check only tests that each number in the answer appears in the cited paragraph, and 80 does appear there for the next band. Fix: route lookup questions that name a loan amount through the LTV tool, as rule checks already are.

**Hallucination: Q020, strategy A.** On the full check of application HL-2045 (₹30 lakh loan, LTV 75%), the model gave the right status but reported the limit it read as 80, where the band for loans up to ₹30 lakh is 90. The critic sent it back with the correct figure and the model repeated 80. The result was withheld and shown as "needs information" with the tool's numbers, so no wrong limit reached the officer, but the query counts as wrong. Strategy C had no failure of this kind.

**Tool call error: Q026, strategy C.** On the full check of HL-2051 the critic sent seven judgements back. The local model then stalled, and the run reached 651 seconds against a limit of 240. The loop stopped after re-running the tools and before the second judgement, so the seven rules were shown as "needs information" and only the three already confirmed kept their status. The same query took 90 seconds on 12 November and 84 and 76 seconds on two re-runs (`eval/results/q026_recheck.jsonl`), all correct. The cause is the endpoint, not the query. The limit is only checked between steps, so one slow model call can overrun it; a per-call timeout on the client would stop the run closer to 240 seconds.

**Tool call error: Q022 on dev, strategy B.** On the full check of HL-2047 the extract step's JSON reply was cut off mid-string by the output limit. It failed validation on the first try and both retries, so the run stopped and reported the tool results as unconfirmed. Fix: raise the output limit for the extract step, or cap the number of field values it may list.

## The failure that mattered most, now fixed

**Q015 was a safety gap, not only a wrong answer.** Asked what a guarantor to a bank home loan must be told, the assistant should have said the rule is unverified. On 12 November it answered from a capital adequacy paragraph about guarantees (`CB-CAP/170`; traces 865d636492ed and c1a65189bd1c in `before_q015_fix_traces_test.jsonl`). The rule check path already handled this in code. The lookup path only told the model in the prompt which rules are unverified, and the 8B model ignored it.

The lookup path now checks the rules file before drafting. A lookup about an unverified rule for the matching lender type is answered in code: the rule is unverified, the answer is unknown, and it will not be answered from memory. The planner still runs, but the model is never asked to draft an answer. Q015 is correct under both strategies in the 13 November run (three steps, 728 tokens), and two unit tests cover the guard.

The fix was made after seeing test results, so it biases the 13 November test numbers by one query per strategy. `docs/results.md` reports both runs.

## What the critic loop changes

| Measure (test split) | A | C |
| --- | --- | --- |
| Rule check queries right on the first pass | 21 of 43 | 20 of 43 |
| Queries sent back by the critic | 31 | 27 |
| Rule check queries right after the loop | 37 of 43 | 42 of 43 |

The two strategies are equally unreliable on the first pass. C ends ahead because its replies are easier to bring into agreement on the retry: it stopped misreporting the limit it had read. C's one miss after the loop is the Q026 time limit stop; it was 43 of 43 on 12 November.
