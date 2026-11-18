# RBI Home Loan Rule Checker (Loan Guarantor) – Open Questions

18 November 2026 · companion to RBI Home Loan Rule Checker (Loan Guarantor)

The questions raised while planning the project, and how each one stands now that the system is built. "Settled" means the build no longer depends on an answer; "assumed" means the build runs on a stated assumption that can be changed; "open" means nobody has answered it yet.

## Scope and course rules

| Question | Status | How it stands |
| --- | --- | --- |
| Is a domain outside the suggested list accepted for Track B? | Open | Not confirmed. The project went ahead with RBI home loan rules as its domain. |
| Is downloading RBI documents for a course project acceptable? | Assumed | The RBI web pages are public and were downloaded at one request a second. The official PDFs were saved by hand in a browser, because rbidocs.rbi.org.in blocks scripts. |
| Are synthetic scenarios with hand-written gold labels acceptable as the evaluation set? | Assumed | Used: 12 synthetic applications and 80 gold queries, all labelled from the verified rules file. The second-person review of the labels is still to be done. |
| Is a corpus below 500 documents accepted if the documents are long? | Settled | Not needed: the corpus holds 1,076 RBI documents. |
| Is multimodal input a requirement, and if so, which part? | Open | Not confirmed. Screen sharing sends the open tab's fields as text, not as an image. |

## Agent design and the model endpoint

| Question | Status | How it stands |
| --- | --- | --- |
| What counts as one step in the 10-step cap? | Assumed | One agent action recorded in the trace: guard, planner, executor, judge, critic or drafter. A second guard limits the agent graph to 25 node visits. A full rule check with one retry uses 8 or 9 steps. |
| Does the course endpoint support guided decoding (JSON-schema output)? | Open | Not tested on the course endpoint, which was not available. The local Ollama endpoint supports JSON-schema output. If an endpoint rejects it, the client falls back to plain JSON, validates every reply and retries twice. |
| What is the endpoint's token quota? | Assumed | Unknown. The build assumes 40,000 tokens and 240 seconds per question and 6 questions per minute per session. All are settings in the environment. |
| Do the bank LTV caps change on 1 April 2027? | Open | The 2027 capital charge Directions change bank risk weights from 1 April 2027, and those tables are in the rules file. No change to the bank LTV caps was found, so the checker applies the current caps on both sides of that date. |

## Credit lane (underwriting layer)

These questions decide the credit model, cutoff and decision gate in the main document. None was answered, so that layer was not built and the rule checker runs on its own.

| Question | Status | How it stands |
| --- | --- | --- |
| Which credit dataset should the model be trained on (Freddie Mac, Home Credit, Give Me Some Credit, Lending Club, German credit, or another)? | Open | Not chosen. |
| Is a US mortgage dataset acceptable for an RBI rule checker, given rupee thresholds do not carry over? | Open | Not decided. |
| Which NPA target and definition should the cutoff aim at? | Open | Not decided. The planned proxy is a loan 90 or more days past due in the dataset's own labels. |
| Does the rule gate count as part of underwriting, or must the credit model also use the guarantor? | Open | Not decided. |

## Known gaps in the build

Not questions for anyone else, but items still to finish, listed here so they are not lost.

| Item | Status |
| --- | --- |
| The bank guarantor disclosure rule was not found in the RBI text read | The checker reports it as unknown for banks and never answers it from memory |
| Second-person review of the gold labels, and the two-person agreement check on 10 labelled failures | Not done |
| Chunking experiments | One of the four schemes measured; the other three still to run |
| End-to-end evaluation on the current corpus | Last run before PDF ingestion and the move to Ollama embeddings; a re-run is pending |
| Course endpoint | Not tested; switching to it is a change to the environment settings only |
