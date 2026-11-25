# Project structure

What each folder and file of the RBI Home Loan Rule Checker (Loan Guarantor) holds, and how the parts fit together.

## How the pieces fit together

```
RBI website ─► ingest ─► data/parsed ─► index (hybrid search)
                                             │
officer in app (src/app) ─► agent graph ─► tools (checks.py) ◄─ rules/rules.yaml
                     ▲          │  ▲
                     │          ▼  │
                     │   safety, prompts, llm (model)
                     │          │
                     └── answer + trace (tracing)

gold + eval ─► measures the whole loop; tests ─► checks each part
```

1. `src/ingest` downloads and parses the RBI text into paragraph chunks.
2. `src/index` builds the hybrid search index over those chunks.
3. The officer works in the Streamlit app (`src/app`) and asks a question or runs a rule check.
4. The agent flow (`src/agents`, a LangGraph state graph) plans the checks, runs the tools (`src/tools`) and the search, asks the model (`src/llm.py`, `src/prompts`) to judge, and has the critic confirm each result.
5. Every limit comes from `rules/rules.yaml`; every calculation is done by a tool, never the model.
6. `src/safety` masks PII, removes injected instructions and refuses decisions that belong to the officer.
7. `src/tracing` writes one trace per question, which is also the audit trail.
8. `gold/` and `eval/` measure the whole system; `tests/` checks each part.

## Top-level files

| File | What it is |
| --- | --- |
| `README.md` | How to install, build the index, run the app, run Docker and run the evaluation |
| `requirements.txt` | Python packages: LangGraph, Streamlit, Qdrant client, fastembed, rank-bm25, Pydantic, openai and others |
| `.env` | Optional settings: model endpoint, key, model name, certificate paths, budgets, limits. Not tracked by git |
| `Dockerfile` | Builds the app image (Python 3.11 with the code and the saved index) |
| `docker-compose.yml` | Starts two containers: the app (port 8501 by default; set `APP_PORT` to change it) and the Qdrant vector database |
| `.dockerignore`, `.gitignore` | Keep secrets, caches and large generated files out of the image and out of git |
| `session.txt` | The project owner's own notes file |
| `crt/` | The CA certificate files (`.crt`) for the course endpoints. Not tracked by git; mounted read only into the Docker app |
| `docs/notes/` | Scratchpad notes: gold answers with their citations, diagrams, and walkthroughs of chunking and of how the gold set is built |
| `debug/`, `.vscode/` | Debug helper that runs one question end to end, the breakpoint guide, and the VS Code launch configurations |
| `traces/` | Created when the app runs locally: `traces.jsonl` (one trace per question) and `activity.jsonl` (every model, search and tool call). Not in git; under Docker they live in the app's volume |

## `rules/`: the verified RBI rules (the source of truth)

| File | What it is |
| --- | --- |
| `rules.yaml` | 30 rule records, 29 verified and 1 unverified. Each holds the exact RBI quote, paragraph id, source document, numeric values (for example LTV bands) and the date it takes effect. Every limit in the system comes from here |
| `check_quotes.py` | Confirms every quote in `rules.yaml` appears word for word in the downloaded RBI text |
| `verification_report.md` | Step 1 of the build: which RBI documents were read, what each rule says, and what could not be verified |

## `data/`: inputs and generated data

| Folder | What it is |
| --- | --- |
| `applications/` | 12 synthetic home loan applications (`HL-2041.json` to `HL-2052.json`) with fake applicants, laid out in the app's 8 tabs. `answer_key.json` lists the problems planted in each one; it is read-only and only the evaluation reads it |
| `raw/html/` | The RBI web pages as downloaded (Master Directions and notifications). Not in git |
| `raw/pdf/` | The official RBI PDFs, saved by hand in a browser. `154MD_verified_2026-11-12.pdf` is the copy the bank rules were checked against; the later `154MD.pdf` is RBI's regenerated file with the same text. Not in git, except Annex V and the verified copy |
| `raw/lists/` | `md_index.tsv` (which Master Directions to download) and `pdf_download_list.html` / `.csv` (the PDF checklist) |
| `parsed/` | `chunks.jsonl` (the paragraph chunks the index is built from) and `corpus_manifest.json` (document list, corpus version, and for each document whether it came from its PDF or its web page) |
| `parsed/text/` | Plain text of the RBI documents read while verifying the rules (26 files); `rules/check_quotes.py` checks every rule quote against the 13 the rules file cites |
| `index/` | Saved search index: embeddings, BM25 index, chunks and metadata. Built by a command; not in git |
| `cache/` | Saved model replies, so a repeated question costs nothing. Not in git |

## `src/`: the application code

### Core files

| File | What it does |
| --- | --- |
| `config.py` | Reads every setting from environment variables: chat endpoint, key and model; the embedding provider, model and endpoint; budgets, step cap, time limit, rate limit |
| `schemas.py` | Data shapes used everywhere (Pydantic): `Case` (one loan case), `Plan`, `ToolResult`, `Judgement`, `CheckResult`, `RunOutput` (the full answer). Also converts an application's tabs into a `Case` |
| `rules.py` | Reads `rules.yaml`. Hands out verified rules only; asking for an unverified one raises an error, which becomes "unknown" |
| `tls.py` | Builds the HTTP client that trusts a course endpoint's `.crt` file (plain and async) |
| `llm.py` | The model client (OpenAI-compatible, so it works with the course endpoint or any compatible server). Forces JSON replies that match a schema, retries twice, enforces the token budget, caches replies. Includes `FakeLLM` for tests |

### `src/ingest/`: getting the RBI text

| File | What it does |
| --- | --- |
| `download.py` | Downloads RBI Master Directions and notifications from rbi.org.in, one request a second |
| `pdf_list.py` | Builds `data/raw/lists/pdf_download_list.html`, the checklist of official RBI PDFs (from the PDF link inside each downloaded page), ticking off the ones already saved |
| `pdf_text.py` | PDF ingestion: finds a document's saved PDF, extracts its text with poppler's pdftotext (pypdf as fallback), and removes page numbers, contents lines, Hindi letterhead and watermarks |
| `parse.py` | Splits each document into paragraph chunks and tags each with paragraph number, lender type (HFC or bank), date and source format. Uses the official PDF when one is saved and passes the quality checks, the web page otherwise |

### `src/index/`: search

| File | What it does |
| --- | --- |
| `hybrid.py` | Hybrid search: bge-small-en-v1.5 embeddings (from the embeddings endpoint, or in-process with fastembed) in Qdrant plus BM25 keyword search, merged by reciprocal rank fusion, then reranked by a cross-encoder. `python -m src.index.hybrid build` creates `data/index/` |

### `src/tools/`: all calculations

| File | What it does |
| --- | --- |
| `checks.py` | One plain function per rule check: LTV cap, risk weight, prepayment, Key Facts Statement, guarantor, valuation, KYC, rate reset, related party, MITC. Also picks which rule version is in force on a date and which checks a case needs. The amendment chain walker (`amendment_chain`) is built and tested but not called by the agents. All arithmetic lives here, never in the model |

### `src/agents/`: the multi-agent flow

| File | What it does |
| --- | --- |
| `steps.py` | Each agent step: planner (which checks), executor (runs tools and search), judge (the model reads the paragraph), critic (compares the judgement with the tool and the paragraph), drafter (writes the cited answer). Also the guard that answers unverified-rule lookups as "unknown" in code |
| `graph.py` | The agent flow as a LangGraph `StateGraph`: one node per agent step (guard, planner, executor, judge, critic, revise, finalize, drafter, and the lookup nodes), with the critic's verdict as a conditional edge |
| `memory.py` | Conversation memory: saves every turn per officer and application to disk, and supplies the previous turn when a question is a follow-up such as “and for a bank?” |
| `loop.py` | Entry point for one question: sets up the run, invokes the graph, handles the stop conditions (10-step cap, recursion limit, time limit, token budget, model errors), and writes the trace, the answer cache and the conversation memory |

The flow runs on LangGraph; the agents, tools and safety rules it calls are plain Python. `docs/agentic_layer.md` maps the Week 3 agentic requirements to this code.

### `src/prompts/`

| File | What it does |
| --- | --- |
| `strategies.py` | Prompts and reply schemas for the three judging strategies: A (direct check), B (extract then judge) and C (plan, judge, self check), and the conventions the model must follow |
| `compare.py` | Runs the three strategies once each on the same case, rule and evidence, and compares each reply with the tool; used by the Prompt lab page |

### `src/safety/`

| File | What it does |
| --- | --- |
| `injection.py` | Finds and removes instruction-like text in notes, questions and retrieved paragraphs, and marks such text as data |
| `pii.py` | Masks PAN, Aadhaar and similar numbers in views, traces and anything sent to the model |
| `refusal.py` | Catches requests to approve a loan, declare compliance, predict repayment or give legal advice, before any model call |

### `src/tracing/`

| File | What it does |
| --- | --- |
| `tracer.py` | Writes one JSON line per question with every step, tokens and timing, plus officer actions. This is the audit trail. Also makes the trace ids, built so an id can never look like a phone or Aadhaar number to the PII masker |
| `activity.py` | Live activity: every agent step, LLM call, Qdrant vector search, BM25 search, rerank and tool run, shown live in the workspace while the assistant works and logged to `activity.jsonl` (no prompt text). Also groups the log by question for the Activity log page, with each step's calls nested under it, and provides `not_logged()` so evaluation runs stay out of the log |

### `src/app/`: the officer workspace

| File | What it does |
| --- | --- |
| `main.py` | The Streamlit app: sign-in and the pages Work queue, Application (tabs plus assistant with a saved conversation and Share this screen), Compliance checks, Comments and sign-off, FAQ, Prompt lab (the three prompting strategies run side by side on one rule), Retrieval lab (the four retrieval modes side by side, with the recall, chunking and diagnostic tables), Activity log (every model, search and tool call, grouped by question with each step's calls under it), and History and evaluation. A live status box shows what the assistant is doing while it works |
| `store.py` | Saves applications, statuses, comments and officer actions. Only the compliance officer role can change a status |
| `ratelimit.py` | Limits questions per minute per session and returns a polite wait message |
| `board/` | The standalone task board: `task_board.html` (open in a browser; not part of the app) and `tasks.json`, the same data |
| `faq.py` | FAQ content: rule answers taken word for word from `rules.yaml`, plus how-to questions about the workspace |
| `textfmt.py` | Text helper: makes line breaks in plain text, such as the officer note, show up when written as Markdown |

## `gold/`: the frozen test questions

| File | What it is |
| --- | --- |
| `gold_queries.jsonl` | 80 questions with expected answers (20 dev, 60 test), frozen before any agent code. Read-only; only the evaluation reads it |
| `build_gold.py` | The one-off script that wrote the gold file, the 12 applications and the answer key, kept so a reviewer can check the labels |

## `eval/`: measuring the system

| File | What it does |
| --- | --- |
| `run_eval.py` | Runs every gold question through the agent loop per strategy, scores accuracy and citation rate, and labels each failure (retrieval miss, reasoning error, tool call error, hallucination) |
| `ragas_eval.py` | Extra report on the dev set: Ragas scores for rule lookups and application checks separately, tool call and agent goal metrics, judged by the course model. Does not replace `run_eval.py` |
| `retrieval_eval.py` | Measures search recall at 5 for dense, BM25, hybrid and hybrid plus reranker |
| `chunking_eval.py` | Chunking experiments: four chunking schemes, each with its own BM25 and embeddings, measured with all four retrieval modes on recall@1/3/5/10 and MRR |
| `rag_diagnostic.py` | Retrieval versus answer quality: for each query, whether the pipeline retrieved the expected paragraph and whether the answer was right, separating retrieval errors from generator errors |
| `safety_eval.py` | Runs the injection, PII and refusal sets with a fake model built to obey anything it sees, then with the real model |
| `sets/` | Safety test cases: `injection.jsonl` (15), `pii.jsonl` (15), `refusal.jsonl` (10) |
| `failure_labels_dev.jsonl`, `failure_labels_test.jsonl` | Each wrong answer with its category and trace id |
| `results/` | The final evaluation (25 November, course endpoints, PDF corpus): summaries, per-query rows, traces, safety, retrieval recall, diagnostic, Ragas and the run log; `chunking.json` holds the chunking measurement |

## `tests/`: automated tests (153)

| File | What it tests |
| --- | --- |
| `conftest.py` | Shared fakes, a fake model and a small in-memory index, so no test calls a real endpoint |
| `test_tools.py` | Every rule check tool: LTV bands, stamp duty, the 2027 risk weights, valuation and the rest |
| `test_agents.py` | The loop: the critic catches the wrong rulebook and a wrong status, the step cap holds, unverified rules stay unknown |
| `test_safety.py` | Injection ignored, PII masked, refusals, only the officer can change a status, and trace ids never look like identifiers |
| `test_cache_ratelimit.py` | The cache avoids repeat model calls, and the rate limit refuses bursts politely |
| `test_app_smoke.py` | Every page of the app renders, identifiers are masked, and only the officer's buttons are enabled |
| `test_faq.py` | FAQ answers match `rules.yaml` exactly, and the unverified rule is never answered |
| `test_pdf_ingest.py` | PDF cleaning, paragraph detection, the quality checks that keep a weak PDF out, and matching saved PDF files |
| `test_activity.py` | Live activity: each run reports its steps, model calls, searches and tools in order, only to itself, with no prompt text logged; and the Activity log grouping (calls nested under their step, newest group first, per-user filtering, overlapping runs kept apart) |
| `test_prompt_compare.py` | The Prompt lab runs each strategy once on the same evidence and compares it with the tool |
| `test_chunking.py` | The chunking schemes used in the experiments: paragraph chunks never cross a paragraph, fixed windows span and overlap correctly |
| `test_graph.py` | The LangGraph graph: one node per agent step, the critic's conditional edge, the retry cycle, and the recursion limit |
| `test_textfmt.py` | Line breaks in plain text survive being written as Markdown: single breaks, and plain text after a list getting its own paragraph |
| `test_memory.py` | Turns persist across restarts, follow-ups use the previous turn, the follow-up lender type is set in code, and a failing tool does not stop the run |

## `docs/`: written material

| File | What it is |
| --- | --- |
| `RBI Home Loan Rule Checker (Loan Guarantor).md` and `.pdf` | The project specification, with the architecture as built (diagrams in `images/`) |
| `images/` | The architecture and agent-flow diagrams (SVG) used in the project specification |
| `Capstone-Project-Templates.pdf` | The course template |
| `labelling_guide.md` | What each status means and how gold answers and failures are labelled |
| `technical_documentation.md`, `technical_documentation.pdf` | The single technical reference: architecture, data, agent runtime, endpoint integration, configuration, workspace, deployment, operations runbook, testing, security, limitations, developer guide, glossary |
| `technical_report.md`, `technical_report.pdf` | The 12 page technical report the course asks for: scope, prompts, retrieval, agents, what compensates for a small model, evaluation, deployment, responsible AI and limits |
| `results.md` | Evaluation results: accuracy, citation, retrieval, safety, before and after the Q015 fix |
| `failure_taxonomy.md` | Every failure by category, with trace ids and a worked example of each |
| `demo_overview_5min.md`, `demo/` | The five minute overview script and its narrated video |
| `demo_script.md` | The ten-minute demo walkthrough and fallbacks |
| `rag.md` | How the Week 2 RAG requirements are met: corpus, chunking experiments, hybrid retrieval, reranker, citation grounding, recall measured apart from answer quality, and a TA walkthrough |
| `agentic_layer.md` | How the Week 3 agentic layer requirements are met: agents, tools, loop, persistent state, error handling, with tests |
| `project_structure.md` | This file |

## Common commands

| Task | Command |
| --- | --- |
| Run the app locally | `.venv/bin/streamlit run src/app/main.py` |
| Run the app in Docker | `docker compose up -d --build` (open http://localhost:8501, or the port in `APP_PORT`) |
| List the official RBI PDFs to save | `.venv/bin/python -m src.ingest.pdf_list` |
| Parse the corpus | `.venv/bin/python -m src.ingest.parse` |
| Build the search index | `.venv/bin/python -m src.index.hybrid build` |
| Run all tests | `.venv/bin/python -m pytest -q` |
| Check rule quotes | `.venv/bin/python rules/check_quotes.py` |
| Evaluate on dev | `.venv/bin/python -m eval.run_eval --split dev --strategies A B C` |
| Evaluate on test | `.venv/bin/python -m eval.run_eval --split test --strategies A C` |
| Safety sets | `.venv/bin/python -m eval.safety_eval --real` |
| Retrieval recall | `.venv/bin/python -m eval.retrieval_eval` |
| Ragas report (extra) | `.venv/bin/python -m eval.ragas_eval --split dev --strategy C` |
| Chunking experiments | `.venv/bin/python -m eval.chunking_eval` |
| Retrieval versus answer quality | `.venv/bin/python -m eval.rag_diagnostic` |
