# Labelling guide for rule checks and gold answers

The gold file and the tools use the same conventions. Each rule check returns one status.

| Status | Meaning |
| --- | --- |
| met | The case satisfies the verified rule |
| breached | The case goes against the rule: a limit is exceeded, a prohibited charge is levied, or the lender recorded that a required step was not done |
| needs_information | A required item is missing or not recorded, so the officer must ask for it before clearing |
| not_applicable | The rule does not cover this case, for example no guarantor, or a loan sanctioned before the rule's start date |
| unknown | The rule for this lender type is not verified in `rules/rules.yaml`, so the checker does not judge it |

## Conventions

- **Steps the lender must take** (key facts statement, guarantor disclosure, rate reset communication, MITC): `yes` is met, `no` is breached, `not_recorded` is needs_information.
- **Documents the customer must supply** (PAN or Form 60, identity and address proof): `provided` is met, `not_provided` is needs_information.
- **LTV** is loan amount divided by property value. Stamp duty and registration are added to the value only when the cost of the house is ₹10 lakh or less. An LTV exactly at the cap is met.
- **Loan size bands** read "up to ₹30 lakh" as including ₹30 lakh, and "above ₹75 lakh" as excluding ₹75 lakh.
- **Risk weight** is reported as met with the weight as its value. No weight is reported when the LTV is outside every band.
- **Dates**: the case date picks the rule in force. Bank risk weights switch to the 2027 tables for case dates from 1 April 2027. Bank prepayment cites paragraph 352 for loans sanctioned up to 31 December 2025 and paragraph 353 from 1 January 2026. The key facts statement rule is not applicable to loans sanctioned before 1 October 2024.
- **Citations** match when the document and the top level paragraph number match, for example `HFC-MD/58` matches `HFC-MD/58-Note1`.
- **Bank cases dated from 1 April 2027** assert the risk weight only. Whether the bank LTV caps change on that date was not found in the text read.

## Failure categories

| Category | Assigned when |
| --- | --- |
| retrieval miss | The expected paragraph was not among the retrieved paragraphs |
| tool call error | A reply failed schema validation after retries, a tool raised an error, or the run hit the step, time or token limit |
| hallucination | The answer cites a paragraph that was not retrieved, or states a number that is not in the cited paragraph or the rules file |
| reasoning error | The right paragraph was retrieved and cited, but the status or value is wrong |

A separate note, `wrong_rulebook`, is set when the cited document applies to the other lender type.

## Review still owed

The labels were written from the verified rules and have not had the second person review the spec asks for. `gold/build_gold.py` lists every label in one place for that review. The gold file is read only; if a label is wrong, record the correction as a new version and re-run the evaluation.
