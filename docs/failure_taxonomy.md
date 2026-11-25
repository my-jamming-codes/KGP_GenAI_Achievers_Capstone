# Failure taxonomy

Every wrong result in the evaluation carries one of four categories, assigned by the rules in `docs/labelling_guide.md`. The labels below are the script's first pass (`eval/failure_labels_dev.jsonl`, `eval/failure_labels_test.jsonl`); the two-person agreement check on 10 results is still to be done.

Counts and trace ids are from the 25 November 2026 run: Qwen3-8B on the course server, the PDF-based corpus (version `6074ba19e620`). Traces are in `eval/results/traces_dev.jsonl` and `eval/results/traces_test.jsonl`.

## Counts by strategy

| Split | Strategy | Wrong | Retrieval miss | Reasoning error | Tool call error | Hallucination |
| --- | --- | --- | --- | --- | --- | --- |
| Dev (20) | A | 1 | 0 | 1 | 0 | 0 |
| Dev (20) | B | 1 | 0 | 1 | 0 | 0 |
| Dev (20) | C | 1 | 0 | 0 | 0 | 1 |
| Test (60) | A | 5 | 2 | 2 | 0 | 1 |
| Test (60) | C | 4 | 2 | 1 | 0 | 1 |

There were no tool call errors, because nothing stopped on a time limit or failed validation. Each strategy ran once, so a difference of one or two queries is within run-to-run variation.

No final result cited the wrong lender type's rulebook, so the `wrong_rulebook` note is empty. Wrong-paragraph citations on the first pass (49 for A, 33 for C on test) were all corrected by the critic.

## Counts by query type (test split)

| Type | A wrong | C wrong |
| --- | --- | --- |
| Rule lookup (11) | 2 | 2 |
| Scenario check (24) | 3 | 2 |
| Lender variant (11) | 0 | 0 |
| Amendment or change (7) | 0 | 0 |
| Refusal and injection (7) | 0 | 0 |

## Failures with trace ids

| Query | Strategy | Category | Trace | What went wrong |
| --- | --- | --- | --- | --- |
| Q004 | A, C | Retrieval miss | a16b1fd0aa8f, a1bb4ae00dcc | HFC risk weight table (`HFC-MD/22`) not in the top five; the answer, 50%, came from a notification |
| Q013 | A, C | Retrieval miss | d01dd2c33a59, fafa44e85a4f | NBFC KYC paragraph 23 not in the top five |
| Q016 | A, C | Reasoning error | fdfe7edcfd0e, afccb5b50b18 | HL-2041: the risk weight still disagreed with the tool after one retry, so it was withheld |
| Q041 | A | Reasoning error | a18a5cdadfea | The risk weight still disagreed with the tool after one retry |
| Q024 | A, C | Hallucination | c67d02b08a56, f60d4edd6caf | HL-2049 (a bank loan of ₹20 lakh): the LTV limit read as 80 where the rules file gives 90, and again after the correction |
| Q027 (dev) | A, B | Reasoning error | f5bb2cfc0d65, db7c2cc67d03 | HL-2052: risk weight read as 35 where the rules file gives 50; still unconfirmed after the retry |
| Q017 (dev) | C | Hallucination | bafc6bc86be1 | HL-2042 (a bank loan): the LTV limit read as 80 where the rules file gives 90, twice |

## One worked example per category

**Retrieval miss: Q004, strategy C (trace a1bb4ae00dcc).** The question asks for the risk weight on a ₹20 lakh HFC housing loan at 85% LTV. The search returned an older notification (`N-12824/3`), another notification, a Master Direction paragraph and HFC paragraph 58, but not HFC paragraph 22, which holds the table. The model answered 50%, which is the right figure, from the notification, and the grounding check passed because the citation was a retrieved paragraph. It is marked wrong because the expected paragraph was never retrieved. Dev recall at 5 for hybrid search is 1.0, so this is a lookup problem: rule checks search with fixed phrases in RBI wording and lookups are searched as asked.

**Reasoning error: Q016, strategy C (trace afccb5b50b18).** On HL-2041 (a housing finance company application) the model first judged a check as breached against the tool and read an LTV limit of 75 where the rules file gives 80. The critic sent it back with the tool's figures. On the retry it read a risk weight of 50 where the rules file gives 35 (LTV 72.73%). The result was withheld and shown as "needs information" with the tool's numbers.

**Hallucination: Q024, strategy C (trace f60d4edd6caf).** On HL-2049, a ₹20 lakh bank loan at LTV 71.43%, the model read an LTV limit of 80 from the band "above ₹30 lakh and up to ₹75 lakh". The rules file gives 90 for loans up to ₹30 lakh. The critic sent it back twice with the correct figure, and the model gave 80 again. The result was withheld as "needs information", so no wrong limit reached the officer, but the query counts as wrong. Q017 on dev (HL-2042, also a bank loan) is the same failure.

**Tool call error: none in this run.** The category covers a run that stops on the time limit or a reply that fails validation after its retries.

## The Q015 fix

Asked what a guarantor to a bank home loan must be told, the assistant should say the rule is unverified. The model answered from a capital adequacy paragraph about guarantees (`CB-CAP/170`), although the prompt told it which rules are unverified.

The lookup path now checks the rules file before drafting. A lookup about an unverified rule for the matching lender type is answered in code: the rule is unverified, the answer is unknown, and it will not be answered from memory. The planner still runs, but the model is never asked to draft an answer. Q015 is correct under both strategies, and two unit tests cover the guard.

**The fix was made after seeing test results,** so the test figures here are not a clean held-out measurement for rule lookups about unverified rules. The gold set was not changed. `docs/results.md` reports the run.

## What the critic loop changes

| Measure (test split) | A | C |
| --- | --- | --- |
| Rule check queries right on the first pass | 17 of 43 | 19 of 43 |
| Queries sent back by the critic | 32 | 29 |
| Rule check queries right after the loop | 40 of 43 | 41 of 43 |
