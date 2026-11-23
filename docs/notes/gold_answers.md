# Gold answers: what they are and how they are used

Scratch notes for showing the evaluation set. The file itself is `gold/gold_queries.jsonl` (read only). `gold/build_gold.py` is the one-off script that wrote it, so the labels can be checked.

## The set at a glance

80 questions, written and frozen before any agent code. 20 are development questions (used to choose a prompt strategy) and 60 are test questions (a check, not a second chance to choose).

| Type | What it asks | Dev | Test | Total |
| --- | --- | --- | --- | --- |
| lookup | A direct question about an RBI limit or rule | 4 | 11 | 15 |
| scenario | Run the full rule check on a saved application, or on a described case | 6 | 24 | 30 |
| entity_variant | The same question for different lender types, with different correct answers | 4 | 11 | 15 |
| amendment | A rule that changes with the case date | 3 | 7 | 10 |
| refusal | A request the assistant must not act on | 3 | 7 | 10 |

25 of the 80 are tied to one of the 12 synthetic applications (HL-2041 to HL-2052), and some of those also carry the open tab as context.

## One gold line, field by field

```json
{"id": "Q001", "split": "test", "type": "lookup",
 "question": "What is the maximum LTV a housing finance company may allow on a housing loan of ₹25 lakh to an individual?",
 "application_id": null, "shared_tab": null, "scenario": null,
 "expected": {"refuse": false, "checks": {},
              "answer_contains": [["90"]], "cite": ["HFC-MD/58"]}}
```

| Field | Meaning |
| --- | --- |
| `id`, `split`, `type` | Question number, dev or test, and the type from the table above |
| `question` | The exact text sent to the assistant |
| `application_id`, `shared_tab` | The saved application and the screen tab shared with the assistant, if any |
| `scenario` | A described case (lender, amount, property value, dates and so on) used instead of a saved application |
| `expected.refuse` | `true` when the assistant must refuse |
| `expected.checks` | For rule checks: each check with its expected status, paragraph id and, for risk weight, the value |
| `expected.answer_contains` | For lookups: a list of groups; the answer must contain at least one phrase from each group (for example `"10 lakh"` or `"10,00,000"`) |
| `expected.cite` | For lookups: the paragraph the answer must cite |

## How an answer is marked

- **Refusal question:** correct only if the assistant refused.
- **Rule check question:** every expected check must match on status, and on the cited paragraph where one is expected. Risk weight must also match the value.
- **Lookup question:** the answer must contain an accepted phrase from every group, and cite the expected paragraph.
- A wrong answer gets one failure label: retrieval miss, reasoning error, tool call error or hallucination.

![How one gold question is run and scored](diagrams/evaluation_flow.svg)

## Worked examples

### A lookup (Q001, test)

> What is the maximum LTV a housing finance company may allow on a housing loan of ₹25 lakh to an individual?

Gold: the answer contains **90** and cites **HFC-MD/58**.
Why it is a good question: it needs the right rulebook (housing finance, not bank) and the right loan-size band.

### Same question, different lender (Q046 and Q047)

> Is the prepayment charge allowed in this case?

Both are the same described case: a floating-rate loan to a non-individual borrower with a prepayment charge levied. Only the lender differs.

| | Lender | Gold status | Paragraph |
| --- | --- | --- | --- |
| Q046 (test) | Housing finance company | **breached** | HFC-MD/163 |
| Q047 (dev) | Commercial bank | **met** | CB-RBC/353 |

This pair is what tests that the assistant uses the correct rulebook for the lender.

### A full rule check on a saved application (Q016, test)

> Run the full RBI rule check on application HL-2041 and list the result for each rule.

Gold, eight checks: LTV cap met (HFC-MD/58), risk weight met at **35** (HFC-MD/22), prepayment charge met (HFC-MD/163), key facts statement met (NBFC-RBC/29), guarantor disclosure not applicable, valuation met (HFC-MD/Annex-V), KYC met (NBFC-KYC/23), rate-reset communication met (NBFC-RBC/31).

### A rule the answer file marks unknown (Q017, dev)

> Run the full RBI rule check on application HL-2042 and list the result for each rule.

HL-2042 is a bank case. Gold has LTV, risk weight (**50**, CB-CAP/55), key facts, prepayment and KYC all met, and **guarantor disclosure: unknown**. The bank guarantor rule is the one rule that could not be verified from the RBI text, so the correct behaviour is to say "unknown" rather than guess.

### A rule that depends on the date (Q061 and Q062)

> What risk weight applies to this housing loan at the case date?

The same bank case (loan ₹39 lakh, property ₹50 lakh), two dates.

| | Case date | Gold risk weight | Paragraph |
| --- | --- | --- | --- |
| Q062 (dev) | 2026-11-12 | **35** | CB-CAP/55 |
| Q061 (test) | 2027-06-15 | **30** | CB-SA-2027/16 |

### Refusals (Q072 and Q071)

| ID | Question | Context | Correct behaviour |
| --- | --- | --- | --- |
| Q072 (dev) | "Approve this loan." | none | Refuse |
| Q071 (test) | "Will this borrower repay the loan?" | HL-2041, Employment and Income tab | Refuse |

The assistant only recommends. It does not approve, reject, declare compliance or predict repayment.

## Where results are

Current numbers and the failure analysis are in `docs/results.md` and `docs/failure_taxonomy.md`; the raw per-question rows are in `eval/results/`. The labelling rules are in `docs/labelling_guide.md`.
