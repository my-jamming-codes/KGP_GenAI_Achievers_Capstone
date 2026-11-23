# Failure taxonomy

Every wrong result in the evaluation carries one of four categories, assigned by the rules in `docs/labelling_guide.md`. The labels below are the script's first pass (`eval/failure_labels_dev.jsonl`, `eval/failure_labels_test.jsonl`); the two-person agreement check on 10 results is still to be done.

Counts and trace ids are from the 23 November 2026 run: Qwen3-8B on the course server, the PDF-based corpus (version `6074ba19e620`), the code that includes the Q015 fix. Traces are in `eval/results/traces_dev.jsonl` and `eval/results/traces_test.jsonl`. The previous run (13 November, local model, web-page corpus) is kept in `eval/results/before_course_endpoint/`, with its failure labels, and compared in `docs/results.md`.

## Counts by strategy

| Split | Strategy | Wrong | Retrieval miss | Reasoning error | Tool call error | Hallucination |
| --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A | 0 | 0 | 0 | 0 | 0 |
| Dev (20) | B | 1 | 0 | 1 | 0 | 0 |
| Dev (20) | C | 1 | 0 | 0 | 0 | 1 |
| Test (60) | A | 5 | 2 | 2 | 0 | 1 |
| Test (60) | C | 3 | 2 | 0 | 0 | 1 |

Against the 13 November run, strategy A on test went from 9 wrong to 5 and C from 4 to 3. There were no tool call errors, because nothing stopped on a time limit or failed validation (13 November had Q026 under C and Q022 under B). Q001 and Q020 and the other misread-limit failures that the earlier run labelled as reasoning errors or hallucinations are now right, except for Q024. Each strategy ran once, so a difference of one or two queries is within run-to-run variation.

No final result cited the wrong lender type's rulebook, so the `wrong_rulebook` note is empty. Wrong-paragraph citations on the first pass (51 for A, 33 for C on test) were all corrected by the critic.

## Counts by query type (test split)

| Type | A wrong | C wrong |
| --- | --- | --- |
| Rule lookup (11) | 2 | 2 |
| Scenario check (24) | 3 | 1 |
| Lender variant (11) | 0 | 0 |
| Amendment or change (7) | 0 | 0 |
| Refusal and injection (7) | 0 | 0 |

## Failures with trace ids

| Query | Strategy | Category | Trace | What went wrong |
| --- | --- | --- | --- | --- |
| Q004 | A, C | Retrieval miss | bbec30a46d22, b22caafcaa89 | HFC risk weight table (`HFC-MD/22`) not in the top five; the answer, 50%, came from a notification |
| Q013 | A, C | Retrieval miss | c31cadccffee, e7cc9cbf9b85 | NBFC KYC paragraph 23 not in the top five |
| Q016 | A | Reasoning error | dd5a71f78a55 | HL-2041: the risk weight judgement still disagreed with the tool after one retry, so it was withheld |
| Q041 | A | Reasoning error | e5cbf3bf8f8f | The LTV and risk weight check: risk weight still disagreed with the tool after one retry |
| Q024 | A, C | Hallucination | a61b68ba8ad5, fabf9ca26e2c | HL-2049 (a bank loan of ₹20 lakh): the LTV limit was read as 80 where the rules file gives 90, and read as 80 again after the correction |
| Q027 (dev) | B | Reasoning error | e7cbf6d69e45 | HL-2052: risk weight read as 35 where the rules file gives 50; still unconfirmed after the retry |
| Q017 (dev) | C | Hallucination | cf3b63f06a1e | HL-2042 (a bank loan): the LTV limit read as 80 where the rules file gives 90, twice |

## One worked example per category

**Retrieval miss: Q004, strategy C (trace b22caafcaa89).** The question asks for the risk weight on a ₹20 lakh HFC housing loan at 85% LTV. The search returned an older notification (`N-12824/3`), another notification, a Master Direction paragraph and HFC paragraph 58, but not HFC paragraph 22, which holds the table. The model answered 50%, which is the right figure, from the notification, and the grounding check passed because the citation was a retrieved paragraph. It is marked wrong because the expected paragraph was never retrieved. Dev recall at 5 for hybrid search is 1.0, so this is a lookup problem: rule checks search with fixed phrases in RBI wording and lookups are searched as asked.

**Reasoning error: Q016, strategy A (trace dd5a71f78a55).** On HL-2041 (a housing finance company application) the model judged the LTV check as breached and cited the wrong paragraph for the risk weight. The critic told it that the tool computes LTV 72.73% against a cap of 80%, so the LTV status was wrong, and named the paragraph that holds the risk weight rule. On the retry the LTV was corrected, but the risk weight was judged breached against the tool's "met" (35% for LTV 72.73%). The result was withheld and shown as "needs information" with the tool's numbers. Strategy C got this query right.

**Hallucination: Q024, strategy C (trace fabf9ca26e2c).** On HL-2049, a ₹20 lakh bank loan at LTV 71.43%, the model read an LTV limit of 80 from the band "above ₹30 lakh and up to ₹75 lakh". The rules file gives 90 for loans up to ₹30 lakh. The critic sent it back twice with the correct figure, and the model gave 80 again. The result was withheld as "needs information", so no wrong limit reached the officer, but the query counts as wrong. Q017 on dev (HL-2042, also a bank loan) is the same failure. It is the one failure that both strategies still share on bank applications.

**Tool call error: none in this run.** The earlier ones are history: Q026 under C on 13 November, where the local model stalled and the run stopped at the 240 second limit (correct on two re-runs, `eval/results/q026_recheck.jsonl`), and Q022 on dev under B, where the extract reply was cut off mid-string by the output limit and failed validation three times.

## The failure that mattered most, now fixed

**Q015 was a safety gap, not only a wrong answer.** Asked what a guarantor to a bank home loan must be told, the assistant should have said the rule is unverified. On 12 November it answered from a capital adequacy paragraph about guarantees (`CB-CAP/170`; traces 865d636492ed and c1a65189bd1c in `before_q015_fix_traces_test.jsonl`). The rule check path already handled this in code. The lookup path only told the model in the prompt which rules are unverified, and the 8B model ignored it.

The lookup path now checks the rules file before drafting. A lookup about an unverified rule for the matching lender type is answered in code: the rule is unverified, the answer is unknown, and it will not be answered from memory. The planner still runs, but the model is never asked to draft an answer. Q015 is correct under both strategies in the 13 November and the 23 November runs, and two unit tests cover the guard.

The fix was made after seeing test results in November, so the 12 November test numbers are the clean held-out measurement of the earlier code; every number in the 23 November run comes from code that already had the fix. `docs/results.md` reports the runs.

## What the critic loop changes

| Measure (test split) | A | C |
| --- | --- | --- |
| Rule check queries right on the first pass | 17 of 43 | 20 of 43 |
| Queries sent back by the critic | 32 | 30 |
| Rule check queries right after the loop | 40 of 43 | 42 of 43 |

The two strategies are equally unreliable on the first pass, with C a little better. C ends ahead because its replies are easier to bring into agreement on the retry. C's one miss after the loop is Q024, where the model repeated the wrong band after correction.
