# RBI Home Loan Rule Checker (Loan Guarantor)

A multi-agent copilot that tests a synthetic home loan case, including its guarantor, against Reserve Bank of India directions and reports each rule as met, breached, needs information, not applicable or unknown, with the paragraph that says so. Built for the upGrad x IIT Kharagpur Track B capstone.

The checker recommends with citations. It never approves a loan, never declares an application compliant, and never changes a status: only the compliance officer's click does. All data is synthetic.

## Latest results

Run on 25 November 2026 with Qwen3-8B on the course server, over 1,080 RBI documents in 43,336 chunks.

| Measure | Result |
| --- | --- |
| Accuracy on 60 held-out queries, scored in code against the gold file | Strategy C 56 of 60 (0.933); strategy A 55 of 60 (0.917) |
| Expected paragraphs cited | 0.975 |
| Retrieval recall at 5, development queries, hybrid with reranker | 1.0 |
| Safety cases (prompt injection, PII, refusal) | 40 of 40 |
| Tool calls on 13 development queries | Recall 1.00, F1 0.85 |

Each strategy ran once. Details, including the model-judged Ragas scores, are in `docs/results.md`.

## Setup from scratch

A fresh clone has the code, the rules file, the synthetic applications and the gold queries. The RBI corpus, the
official PDFs and the search index are not in git (they are large and regenerated), so steps 3 to 5 build them.

### 1. Install the tools

| Tool | Used for | Install (macOS) |
| --- | --- | --- |
| Python 3.11 and [uv](https://docs.astral.sh/uv/) | the code | `brew install uv` |
| poppler (`pdftotext`) | reading the RBI PDFs (pypdf is used if it is missing) | `brew install poppler` |
| Docker Desktop | optional, to run the app in containers | from docker.com |

On Linux, use your package manager: `poppler-utils` for poppler.

### 2. Create the environment and set the model endpoints

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

The chat model and the embedding model are not installed here: they are OpenAI-compatible endpoints, set in a `.env` file in the project folder (see "Switching the model" for every variable). For the course server set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_CA_CERT`, `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL` and `EMBED_CA_CERT`, and keep the `.crt` files in the `crt` folder.

The reranker (`Xenova/ms-marco-MiniLM-L-6-v2`) is downloaded automatically by fastembed the first time a search runs.

### 3. Build the RBI corpus

```bash
.venv/bin/python -m src.ingest.download    # RBI web pages into data/raw/html/, one request a second (about 20 minutes)
.venv/bin/python -m src.ingest.pdf_list    # writes data/raw/lists/pdf_download_list.html
```

Open `data/raw/lists/pdf_download_list.html` in a browser and save the official PDFs into `data/raw/pdf/` without renaming
them. RBI serves PDFs behind a bot check, so this is done by hand; re-run `pdf_list` to tick off what is saved.
Tier 1 (the 10 Directions the rules cite) is the important one; anything not saved is read from its web page.

```bash
.venv/bin/python -m src.ingest.parse       # paragraph chunks; a document's PDF when saved and readable, else its web page
```

### 4. Build the search index

```bash
.venv/bin/python -m src.index.hybrid build # embeds every chunk through the embedding endpoint, builds BM25 (this takes a long time on the course server, which accepts 16 texts per call and one request at a time; a stopped build resumes from its saved parts)
```

The index records which embedding model built it. If you change `EMBED_PROVIDER` or `EMBED_MODEL`, rebuild it.

### 5. Check the build

```bash
.venv/bin/python -m pytest tests -q        # 167 tests, no model needed
.venv/bin/python rules/check_quotes.py     # every rule quote against the RBI source text
.venv/bin/python -m eval.retrieval_eval    # recall at 5 for the four search modes (the Ragas report is in the evaluation list below)
```

### 6. Run the workspace

```bash
.venv/bin/streamlit run src/app/main.py    # http://localhost:8501
```

Sign in as **Kiran Rao (compliance officer)**. Dev Malhotra (credit processor) and Sana Qureshi (approver) can view but
not change a status. Pages: Work queue, Application (with the assistant and Share this screen), Compliance checks,
Comments and sign-off, FAQ, Prompt lab, Retrieval lab, Activity log, History and evaluation.

The capstone task list is a separate standalone page, `src/app/board/task_board.html` (open it in a browser; it is not part of the app). `src/app/board/tasks.json` is the same data.

### Or run it with Docker

After step 4 (the image copies the index and the `eval/results` files from the project folder at build time, so rebuild after a new index or a new evaluation run), with the model endpoints set as below:

```bash
docker compose up -d --build               # app on http://localhost:8501, plus a Qdrant server
APP_PORT=8502 docker compose up -d --build # if port 8501 is already taken
docker compose down                        # stop
```

The container reads the chat and embeddings addresses from `DOCKER_LLM_BASE_URL` and `DOCKER_EMBED_BASE_URL`; if they are not set it uses `http://host.docker.internal:11434/v1` for both, which is a server running on the host. For the course endpoints, also mount the certificate folder and give the paths inside the container:

```bash
APP_PORT=8502 CERT_DIR=./crt \
DOCKER_LLM_BASE_URL=<chat base URL> DOCKER_EMBED_BASE_URL=<embeddings base URL> \
DOCKER_LLM_CA_CERT=/certs/<chat>.crt DOCKER_EMBED_CA_CERT=/certs/<embeddings>.crt \
docker compose up -d --build
```

### If something goes wrong

| Message or symptom | Cause and fix |
| --- | --- |
| "the index was built with … but the settings say …" | The embedding settings changed since the index was built: set them back, or rebuild (step 4) |
| "I stopped because of a model reply that failed validation", or an embedding call failed | The chat or embeddings endpoint is unreachable, the key or the `.crt` path is wrong, or the model name is wrong. Check `LLM_BASE_URL`, `EMBED_BASE_URL`, the keys, the certificate paths and the model names in `.env` (step 2) |
| "ports are not available … 8501" | Another app uses the port: `APP_PORT=8502` (Docker) or `--server.port 8502` (Streamlit) |
| A document you saved as PDF still shows `source_format: html` in `data/parsed/corpus_manifest.json` | The PDF failed a quality check; the reason is in `pdf_fallbacks` in the same file |

## Switching the model

The clients are OpenAI-compatible, so only environment variables change (in `.env`, or the shell for Docker).
Nothing about an endpoint is stored in the repository.

An endpoint served with a private certificate needs the `.crt` file. Set `LLM_CA_CERT` and `EMBED_CA_CERT` to its exact path; the app does not search for it. With Docker, keep the files in the `crt` folder (or set `CERT_DIR`) and set `DOCKER_LLM_CA_CERT` and `DOCKER_EMBED_CA_CERT` to its path inside the container, for example `/certs/vm04-ca.crt`.

| Variable | Default in the code | Course endpoint |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:11434/v1` (a server on the same machine) | the course base URL |
| `LLM_API_KEY` | a placeholder value | the course key |
| `LLM_MODEL` | `qwen3:8b` | the course chat model, `Qwen/Qwen3-8B` |
| `LLM_CA_CERT` | empty | exact path of the endpoint's `.crt` file, for example `crt/vm07-ca.crt` |
| `EMBED_PROVIDER` | the endpoint mode (leave it unset) | leave unset to call an OpenAI-compatible embeddings endpoint, or set `fastembed` to run the model in-process |
| `EMBED_MODEL` | `qllama/bge-small-en-v1.5` | `bge-small-en-v1.5` on the course endpoint; `BAAI/bge-small-en-v1.5` with `fastembed` |
| `EMBED_BASE_URL`, `EMBED_API_KEY` | `http://localhost:11434/v1`, a placeholder value | the embeddings endpoint and its key |
| `EMBED_CA_CERT` | empty | exact path of the embeddings endpoint's `.crt` file (often the same file as `LLM_CA_CERT`) |

Course endpoint limits are built in and can be tuned: `LLM_MAX_CONCURRENT` (default 1 active request, queued inside the app), `LLM_RATE_PER_MINUTE` (default 55 of the 60 allowed per key; 0 turns it off), `LLM_MAX_OUTPUT_TOKENS` (default 1024) and `LLM_BACKOFF_S` (default `2,4,8` seconds before a 429 or 503 is retried). `EMBED_BATCH` is 16, the most texts the embeddings endpoint accepts per call. The limits are per process, so run one thing at a time on a group key.

JSON-schema output is requested first. If an endpoint rejects it, the client falls back to plain JSON, validates every reply with Pydantic and retries twice with the error before counting a tool call error. `LLM_REASONING_EFFORT=none` switches off Qwen3 thinking; leave it empty for endpoints that reject the parameter.

## Documentation

Start with `docs/technical_documentation.md` (PDF alongside): architecture, data, configuration, deployment, the operations runbook, testing and security in one place. `docs/technical_report.md` (PDF alongside) is the 12 page report, and `docs/project_structure.md` lists every file.

| Topic | File |
| --- | --- |
| Specification | `docs/RBI Home Loan Rule Checker (Loan Guarantor).md` |
| Results and failure taxonomy | `docs/results.md`, `docs/failure_taxonomy.md` |
| RAG and agentic layer | `docs/rag.md`, `docs/agentic_layer.md` |
| Labelling guide | `docs/labelling_guide.md` |
| Demo | `docs/demo_script.md`, `docs/demo_overview_5min.md`, `docs/images/Loan Guarantor Demo.pptx` |

## How a rule check runs

The flow is a LangGraph state graph (`src/agents/graph.py`): one node per step, with the critic's verdict as the branch.

| Stage | Agent | What it does |
| --- | --- | --- |
| 1 | Guard (code) | Refuses repayment, approval, compliance declaration and legal advice requests; strips instruction-like text from notes and pasted text; serves a repeated question from the cache |
| 2 | Planner (model) | Reads the case and question; returns the lender type and the checks to run, each with a tool |
| 3 | Executor (code) | Retrieves paragraphs for each check from the hybrid index and runs the tool, which does all arithmetic and date logic from `rules/rules.yaml` |
| 4 | Judge (model) | Reads the retrieved paragraphs and the tool's computed facts and returns a status and paragraph id per rule, using prompt strategy A, B or C |
| 5 | Critic (code) | Fails a wrong rulebook, a missing check, a citation that is not the rule's paragraph, a status or number that disagrees with the tool |
| 6 | Revise (once) | On a failure, the planner re-plans only if the rulebook or the plan was wrong; the failed checks are then re-run with the critic's reasons, judged again and re-checked |
| 7 | Drafter (model) | Writes the officer's note from the confirmed results and the text of the cited paragraphs; a template is used if the model fails |

A general question about the rules takes a shorter path: guard, planner, a check that the rule is verified, search, a cited draft, and a grounding check with one redraft.

The loop is capped at 10 steps, a token budget and a time limit per question. A judgement the critic cannot confirm is never shown as a verdict: it is reported as needs information, with the tool's numbers. A rule that is unverified in the rules file is reported as unknown and is never sent to the model. Follow-up questions such as "and for a bank?" use the previous turn, which is saved per officer and application.

While the assistant works, the workspace shows each step, model call and search live, and the Activity log page keeps a record of every one.

## Repository layout

| Path | Contents |
| --- | --- |
| `rules/rules.yaml`, `rules/verification_report.md` | 30 rule records read from rbi.org.in, 29 verified; `rules/check_quotes.py` checks every quote against the source text |
| `data/raw/` | `html/` the RBI web pages, `pdf/` the official PDFs saved by hand, `lists/` the download index and the PDF checklist (not in git, except the few files the rules were verified against) |
| `data/parsed/` | `chunks.jsonl` and `corpus_manifest.json` from the parser; `text/` holds the reference texts the rule quotes are checked against |
| `data/applications` | 12 synthetic applications across eight tabs; `answer_key.json` is read only and used by nobody but reviewers |
| `data/index`, `data/cache` | The saved search index, and saved model replies (both built or filled locally) |
| `gold/gold_queries.jsonl` | 80 frozen queries, 20 development and 60 test, 25 application based; read only by `eval/run_eval.py` |
| `src/ingest`, `src/index` | Downloader, PDF checklist and PDF reader, parser, BM25 plus Qdrant dense index with reciprocal rank fusion and a cross-encoder reranker |
| `src/tools` | LTV calculator, risk weight lookup, prepayment checker, lender type picker and the disclosure checks, plus an amendment chain walker (built and tested, but not called by the agents, which pick the rule version with `rule_in_force`) |
| `src/agents`, `src/prompts` | The LangGraph agent flow, shared state, conversation memory, the three prompt strategies, the side-by-side strategy comparison and the JSON schemas |
| `src/safety`, `src/tracing` | Refusals, injection handling, PII masking, JSON line traces, and the live activity log |
| `src/app` | Streamlit workspace, answer cache, rate limiter, application store, and the FAQ page (rule answers are the verbatim quotes from `rules/rules.yaml`); `board/` holds the standalone task board page, which is not part of the app |
| `eval/` | Evaluation, retrieval recall, chunking experiments, the retrieval-versus-answer diagnostic, the extra Ragas report, and the injection, PII and refusal sets; `eval/results/` holds the final run |
| `tests/` | 167 unit and integration tests, no model needed |
| `docs/` | The specification, technical documentation, technical report and the write-ups listed under Documentation above |

## Evaluation

```bash
.venv/bin/python -m eval.retrieval_eval                       # recall at 5: dense, BM25, hybrid, hybrid plus reranker
.venv/bin/python -m eval.ragas_eval --split dev --strategy C   # extra: Ragas answer scores and tool-call metrics (about 15 minutes on the course server)
.venv/bin/python -m eval.chunking_eval                        # four chunking schemes x four modes, recall@1/3/5/10 and MRR (re-embeds the corpus)
.venv/bin/python -m eval.run_eval --split dev --strategies A B C
.venv/bin/python -m eval.run_eval --split test --strategies A C
.venv/bin/python -m eval.safety_eval --real                   # injection, PII and refusal pass rates
.venv/bin/python -m eval.rag_diagnostic                       # retrieval errors versus generator errors, from the saved runs
```

Results are in `docs/results.md` and `docs/failure_taxonomy.md`. How the Week 2 (RAG) and Week 3 (agentic layer) requirements are met is in `docs/rag.md` and `docs/agentic_layer.md`.
