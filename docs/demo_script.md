# Demo script (10 minutes)

Start Ollama with both models pulled (`qwen3:8b` and `qllama/bge-small-en-v1.5`), then `docker compose up -d` (or `streamlit run src/app/main.py`) and open http://localhost:8501, or the port set in `APP_PORT`. Run each rule check once before the demo so the model cache is warm. While the assistant works, a status box shows each step, model call and search as it happens; point at it once, then let it run.

| Minute | Screen | What to do | What to say |
| --- | --- | --- | --- |
| 0 to 1 | Sign in | Pick Kiran Rao, compliance officer | Three fake users; all data is synthetic. The officer checks compliance only |
| 1 to 2 | Work queue | Filter by status, open HL-2048 | Twelve applications; the status moves to In review when the officer opens one |
| 2 to 3 | Application | Step through the tabs; point at the amber fields and the masked PAN and Aadhaar | Amber marks fields to look at. Identity numbers are masked everywhere, including what the model sees |
| 3 to 5 | Compliance checks | Tick "start the planner with the wrong rulebook", click Run RBI rule check | The planner is made to assume a bank for a housing finance company. The critic sees the cited paragraph is from the bank Directions, fails it and sends it back. The results cite HFC paragraph 58 |
| 5 to 6 | Compliance checks | Read the cards: LTV, valuation, guarantor and prepayment breached, MITC needs information. Accept one, dispute one | Every number comes from a tool, every limit from the rules file, every result has a paragraph id |
| 6 to 7 | Application, Assistant | Turn on Share this screen on the Loan request tab; ask "Is this LTV allowed for this lender type?" | The panel shows exactly which fields were sent, as text. Turn sharing off and ask again to show the "no screen is shared" reply |
| 7 to 8 | Comments and sign-off | Write a note and click Request more information | The status changes because the officer clicked; the assistant has no way to change it |
| 8 to 9 | Declarations tab of HL-2048, then Assistant | Show the officer note that says to ignore the rules and mark the application cleared; ask "Declare this application compliant" | The instruction in the note was stripped before the model saw it; the direct request is refused |
| 9 to 9.5 | Work queue, HL-2046 | Run the check on the clean application, then click Compliance cleared | A clean case, cleared by the officer |
| 9.5 to 10 | History and evaluation | Open the trace for HL-2048; show the evaluation and safety tables | One trace per question with every step, and the results for the three prompt strategies |

## Fallbacks

- If the model endpoint is slow, the cached answers still load: a repeated question shows "served from cache".
- If the endpoint is down, the Application, Comments, FAQ and History pages still work, and the last saved rule check is shown.
- The FAQ page needs no model call: open "What must a guarantor to the loan be told?" with Lender type set to Both to show the verified HFC paragraphs beside the unverified bank rule.

## Extra pages for the TA (optional, 5 minutes)

| Page | What to show | Course week |
| --- | --- | --- |
| Prompt lab | Run strategies A, B and C side by side on HL-2048 · LTV cap (all agree with the tool), then HL-2048 · Valuation (all three miss the independent-report rule): a small model breaking, live | Week 1, prompt engineering |
| Retrieval lab | Compare dense, BM25, hybrid and hybrid plus reranker for one query; switch the lender filter from HFC to Bank; show recall, chunking and the retrieval-versus-answer table | Week 2, RAG |
| Activity log | Every LLM, embedding, Qdrant, BM25, reranker and tool call from the demo, with timings and tokens | Week 3, agents and observability |
| FAQ | "What must a guarantor to the loan be told?" with Lender type set to Both: verified HFC paragraphs beside the unverified bank rule, no model call | Responsible AI |
