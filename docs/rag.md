# RAG over a real corpus (Week 2)

How the project meets the Week 2 requirement: *RAG over a real corpus of 500–2,000 documents, with chunking experiments, hybrid retrieval (BM25 plus dense), a reranker, and citation-grounded answers; retrieval recall@k measured separately from end-to-end answer quality.*

## Summary

| Requirement | What the project has | Evidence |
| --- | --- | --- |
| Real corpus, 500–2,000 documents | 1,076 RBI documents from rbi.org.in, 40,890 paragraph chunks; 118 parsed from the official PDF, the rest from the RBI web page | `data/parsed/corpus_manifest.json` |
| Chunking experiments | Four schemes defined and measured on recall@1/3/5/10 and MRR, dev and test; **only the production scheme has been run so far** | `eval/chunking_eval.py` → `eval/results/chunking.json` |
| Hybrid retrieval (BM25 + dense) | BM25 and bge-small-en-v1.5 vectors (served by Ollama as `qllama/bge-small-en-v1.5`) in Qdrant, merged by reciprocal rank fusion | `src/index/hybrid.py` |
| A reranker | Cross-encoder `ms-marco-MiniLM-L-6-v2` over the top 20 fused results | `src/index/hybrid.py` |
| Citation-grounded answers | Every answer cites RBI paragraph ids; the critic rejects a citation that was not retrieved, is from the wrong lender's rulebook, or does not contain the numbers in the answer | `src/agents/steps.py` `critic_lookup`, `critic_step` |
| Recall@k measured apart from answer quality | Retrieval-only recall on fixed query sets, plus a diagnostic that splits answer errors into retrieval errors and generator errors | `eval/retrieval_eval.py`, `eval/chunking_eval.py`, `eval/rag_diagnostic.py` |

## The corpus

- **1,076 documents** from rbi.org.in: Master Directions and notifications.
- **By lender type:** 8 for housing finance companies, 75 for NBFCs (several apply to HFCs), 130 for banks, and 863 others (foreign exchange, payments, markets). The others are kept on purpose: they make retrieval realistic, because the right paragraph has to be found among unrelated RBI text.
- **Corpus version** `a64d829a84ef`, a hash of the chunk file. The answer cache and the Qdrant collection are keyed on it, so a re-parse invalidates both.

### PDF ingestion

Every RBI document has an official PDF; most also have a web page with the same text. The parser uses the **PDF where one has been saved**, and the web page otherwise.

| Source | Documents | Includes |
| --- | --- | --- |
| Official PDF | 118 | All 10 Directions the rules file cites, among them the current bank Credit Facilities and Capital Adequacy Directions, whose current versions exist only as PDF |
| RBI web page | 958 | Documents whose PDF has not been saved, and 15 whose PDF failed the quality checks below |

1. **Finding the PDFs.** `python -m src.ingest.pdf_list` reads the official PDF link inside each downloaded RBI page and writes `data/raw/lists/pdf_download_list.html`, a checklist ordered by priority that ticks off PDFs already saved. RBI serves PDFs from `rbidocs.rbi.org.in`, which blocks scripts with a bot check, so the PDFs are saved by a person in a browser into `data/raw/pdf/`.
2. **Extracting text** (`src/ingest/pdf_text.py`). poppler's `pdftotext` was compared with pypdf on the paragraphs the rules depend on. pypdf split words ("d irections") and dropped a risk weight cell from the HFC table; pdftotext kept them, so it is used, with pypdf as the fallback.
3. **Cleaning.** Page numbers at the page edges, the table of contents, Hindi letterhead lines, the RBI caution notice and the "Withdrawn" watermark are removed; words hyphenated across a line are joined.
4. **Paragraphs.** The same numbered-paragraph splitter as the web pages, so a chunk from a PDF keeps the paragraph id the rules cite (`CB-CF/111`). RBI's PDFs print "352.In case…" without a space; the splitter accepts that.
5. **Quality checks** (`pdf_check` in `src/ingest/parse.py`). A PDF replaces the web page only if it has at least 60% of the web page's words and numbered paragraphs, and every paragraph the rules file cites in that document. Otherwise the web page is kept and the reason is recorded in the manifest. A PDF is used for one document only, so the current bank PDF is indexed once, under the key the rules cite.
6. **Checked against the rules.** 94 of the 101 rule quotes appear word for word in the PDF text of their document; the rest differ only in spacing after a number ("(1)The"), two words pdftotext joins across a line ("all-incost"), and two tables laid out differently. Every paragraph the rules cite is present.

**What PDF ingestion found in the web pages.** Comparing the two showed that every RBI notification web page ended with the site's archive menu (months and years, about 480 words), which had been indexed as document text. The parser now removes it from web pages too. Four web pages that held only that menu and a link to a PDF that has not been saved were dropped.

**Effect on retrieval.** Recall at 5 on the dev queries rose from 0.962 to 1.0 for dense and for hybrid search, and stayed at 1.0 with the reranker (table below).

## Chunking

**Production scheme: paragraph-aware.** `src/ingest/parse.py` splits each document at its numbered RBI paragraphs (`58.`, `13G.`, ...), so a chunk is one paragraph, the unit a compliance officer cites. A paragraph longer than 220 words is split into sub-chunks that keep the same paragraph id. Each chunk carries its paragraph id (`HFC-MD/58`), document, title, lender type and date.

**Experiments.** `eval/chunking_eval.py` rebuilds the corpus four ways and measures each with all four retrieval modes:

| Scheme | How it splits |
| --- | --- |
| `paragraph_220` | One numbered paragraph per chunk, split at 220 words (production) |
| `paragraph_120` | The same, split at 120 words |
| `fixed_200` | Fixed 200-word windows that ignore paragraph boundaries |
| `fixed_400_o80` | Fixed 400-word windows with an 80-word overlap |

Every scheme gets its own BM25 index and its own embeddings, and uses the same fusion and reranker as production. A fixed window can span several paragraphs; it counts as a hit when it contains text of the expected paragraph, which favours the fixed schemes slightly. A scheme is to be chosen on the dev queries; the test column is a check.

**Status: one of the four schemes has been measured.** The run on 15 November was stopped after the production scheme, `paragraph_220`, to free the machine; the other three were not reached. The figures below are on the earlier corpus (1,080 documents, web pages only), with 26 dev and 81 test expected citations.

| `paragraph_220`, split | Mode | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 |
| --- | --- | --- | --- | --- | --- | --- |
| Dev | Dense | 0.577 | 0.885 | 0.962 | 1.0 | 0.722 |
| Dev | BM25 | 0.692 | 0.923 | 0.923 | 0.923 | 0.801 |
| Dev | Hybrid | 0.846 | 0.885 | 0.962 | 1.0 | 0.891 |
| Dev | Hybrid + reranker | 0.692 | 0.885 | 1.0 | 1.0 | 0.813 |
| Test | Dense | 0.568 | 0.84 | 0.852 | 0.938 | 0.697 |
| Test | BM25 | 0.63 | 0.914 | 0.951 | 0.951 | 0.771 |
| Test | Hybrid | 0.778 | 0.827 | 0.951 | 0.951 | 0.829 |
| Test | Hybrid + reranker | 0.852 | 0.914 | 0.951 | 0.951 | 0.888 |

To finish the comparison, run `python -m eval.chunking_eval` (each new scheme is embedded once; with Ollama embeddings this takes a few minutes per scheme). The Retrieval lab page shows the table as soon as `eval/results/chunking.json` is written.

## Hybrid retrieval and reranking

```
query ─┬─► BM25 (rank-bm25) ─────────────┐ top 40
       │                                  ├─► reciprocal rank fusion (k = 60) ─► top 20 ─► cross-encoder reranker ─► top k
       └─► bge-small (Ollama) ─► Qdrant ┘ top 40
```

- **Embeddings.** The corpus and every query are embedded with bge-small-en-v1.5, served by Ollama (`EMBED_PROVIDER=ollama`, `EMBED_MODEL=qllama/bge-small-en-v1.5`); `EMBED_PROVIDER=fastembed` runs the same model in-process instead. The two give the same vectors (cosine 1.000 on a sample), and recall did not change. The index records which model built it and refuses to be searched with another.
- **Lender filter.** A search for an HFC case looks only at HFC and NBFC documents; a bank case only at bank documents. This stops the most damaging error in this domain, citing the other lender type's rulebook.
- **Query side.** A rule check searches with a fixed phrase in RBI wording for that check (`CHECK_QUERY` in `src/agents/steps.py`). A lookup question is searched as asked, with the planner's search phrase added.
- **Targeted fetch.** On a critic retry, if the paragraph the tool cites was not retrieved, it is fetched by id and put in front of the model.

## Citation-grounded answers

- **Rule checks:** each result carries the paragraph id of the rule it was judged against. The critic fails a judgement whose paragraph is not the one holding the rule, or that comes from the other lender type's rulebook, and sends it back once.
- **Lookups:** the model must cite paragraph ids from the retrieved set. The critic rejects a citation that was not retrieved, a paragraph from the wrong lender type, or a number in the answer that does not appear in the cited text, and asks for one redraft. If the redraft also fails, the officer sees "I could not verify an answer" and the closest paragraph ids, not the unverified answer.
- **Unverified rules** are never answered, only reported as unknown (`rules/rules.yaml`).
- **Citation rate** on the 60 test queries: 0.975 of expected paragraphs cited, under both strategies A and C (`docs/results.md`).

## Recall@k, measured apart from answer quality

There are three separate measurements, from retrieval alone to the full pipeline.

**1. Retrieval only, production index** (`python -m eval.retrieval_eval`). The fixed query for each expected citation in the dev queries, 26 in all:

| Mode | Web pages only (before) | With PDF ingestion |
| --- | --- | --- |
| Dense only | 0.962 | 1.0 |
| BM25 only | 0.923 | 0.923 |
| Hybrid (rank fusion) | 0.962 | 1.0 |
| Hybrid + reranker | 1.0 | 1.0 |

**2. Retrieval only, per chunking scheme** (`python -m eval.chunking_eval`): recall@1, 3, 5 and 10 and MRR@10 on dev and test, above.

**3. Inside the pipeline, next to the answer** (`python -m eval.rag_diagnostic`). For every gold query that expects a paragraph, it takes what the pipeline actually retrieved in the evaluation run (from the trace) and whether the final answer was right:

| Run | Queries | Pipeline recall | Answer accuracy | Accuracy when the paragraph was retrieved | Generator errors | Retrieval errors |
| --- | --- | --- | --- | --- | --- | --- |
| Dev, A | 17 | 1.0 | 0.824 | 0.824 | Q022, Q027, Q070 | none |
| Dev, B | 16 | 1.0 | 0.875 | 0.875 | Q017, Q070 | none |
| Dev, C | 17 | 1.0 | 0.941 | 0.941 | Q070 | none |
| Test, A | 51 | 0.961 | 0.824 | 0.857 | Q001, Q016, Q020, Q024, Q040, Q041, Q061 | Q004, Q013 |
| Test, C | 50 | 0.96 | 0.94 | 0.979 | Q001 | Q004, Q013 |

Refusals and runs stopped by a limit are left out, because they say nothing about retrieval.

**What it shows.** Retrieval is not the bottleneck: the pipeline found the expected paragraph 96–100% of the time. Strategy A's extra errors on test are all generator errors (it misread the limit in a paragraph it had), which is why prompt strategy C, not a retrieval change, fixed them. The two test queries no strategy answered, Q004 and Q013, are pure retrieval errors on free-text lookups; no prompt change can fix those, and the fix is in the search (add the fixed rule-check phrase to lookup queries). Without this split, all of these would look like "the model is weak".

## How to show it to the TA (10 minutes)

1. **The corpus and PDF ingestion (2 min).** Open `data/raw/lists/pdf_download_list.html` to show the official PDFs and which are ingested, then `data/parsed/corpus_manifest.json`: each document records `source_format` (pdf or html), the PDF file, its version line, and the reason when a PDF was not used. 1,076 documents, 40,890 chunks.
2. **Live retrieval (3 min).** In the workspace (http://localhost:8501, or 8502 if `APP_PORT` is set), open **Retrieval lab**.
   - Pick the example "stamp duty registration charges cost of the house LTV" with Lender filter HFC and click **Search all four modes**. Show the four columns side by side and the ⭐ marks for paragraphs that also reach the production top k.
   - Switch the filter to Bank and run it again: the results move to the bank Directions, which is the lender filter at work.
   - Try a paraphrase that shares few words with the RBI text, such as "can the lender charge a fee if I repay my home loan early", to show dense search catching what BM25 misses, and the reranker ordering them.
3. **Chunking experiments (2 min).** On the same page, show the chunking table: four schemes, recall@1/5/10 and MRR, dev and test, and why paragraph-aware chunks were kept.
4. **Citation grounding (2 min).** On **Application** with sharing on, ask a rule question and point at the "Cited:" paragraph ids. Open the trace on **History and evaluation** and show the critic step that checked the citation.
5. **Retrieval versus answer quality (2 min).** Show the last table on **Retrieval lab**: pipeline recall next to answer accuracy, with generator errors and retrieval errors named. Make the point above: A's errors are the generator's, Q004 and Q013 are retrieval's, and each needs a different fix.

All of this works with the model switched off, apart from step 4.

## Commands

| Task | Command |
| --- | --- |
| Download and parse the corpus | `python -m src.ingest.download`, `python -m src.ingest.pdf_list` (then save the PDFs in a browser), `python -m src.ingest.parse` |
| Build the production index | `python -m src.index.hybrid build` |
| Recall@5 per retrieval mode | `python -m eval.retrieval_eval` |
| Chunking experiments | `python -m eval.chunking_eval` (add `--limit-docs 50` for a quick smoke run) |
| Retrieval versus answer quality | `python -m eval.rag_diagnostic` (after `eval.run_eval`) |
