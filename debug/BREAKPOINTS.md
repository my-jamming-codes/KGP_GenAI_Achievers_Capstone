# Debugging one question from start to finish

## Start

Open the project folder in the editor, open **Run and Debug**, and pick one of two configurations (`.vscode/launch.json`):

1. **App in the browser (debug)** starts the workspace on http://localhost:8503. Sign in, ask a question or press Run RBI rule check, and the debugger stops at your breakpoints. (The Docker app on 8502 can keep running.)
2. **One question, no browser (debug)** runs `debug/run_one_question.py` with one question. It is the easier way to step through, because Streamlit re-runs the page on every click. Change the question in the `args` line of the configuration.

Both configurations raise the time limits (a paused run would otherwise time out), use the settings in `.env`, and write to a temporary folder, so debugging does not change the demo data. Set `DEBUG_KEEP=1` to use the real folders.

Tips: set `LLM_CACHE=0` in the configuration's `env` to force real model calls. Leave it on to replay the same run instantly. Use **Step Into** (F11) on a call you want to follow and **Step Over** (F10) on one you don't.

## Where to put breakpoints, in the order a question travels

| # | File and line | What happens here | What to look at |
| --- | --- | --- | --- |
| 1 | `debug/run_one_question.py` at the `loop.run(` line, or `src/app/main.py:213` (assistant) / `:264` (rule check button) | The hand-off from the UI to the agents | `question`, `case`, `screen`, `strategy` |
| 2 | `src/agents/loop.py:51` | Sets up the trace, token budget, cache key | `budget`, the cache key, any earlier turns |
| 3 | `src/agents/graph.py:326` | `invoke`: starts the LangGraph state graph | `ctx` (the run context) |
| 4 | `graph.py:73` guard, then `src/safety/refusal.py:27` and `injection.py:40` | Refuse, strip injected text, serve a cached answer | Does `classify` return a refusal code? What did `sanitize` remove? |
| 5 | `graph.py:100` planner, then `src/agents/steps.py:130` `plan_step` | The model picks the lender type and which checks apply | The plan JSON, the prompt in `llm.py:152` |
| 6 | `graph.py:118` executor, then `steps.py:158` `execute_step` | Runs the tools and the search for each check | The loop over checks |
| 7 | `src/tools/checks.py:297` `run_tool`, then `:82` `check_ltv_cap` | A plain-Python check. This is where the numbers come from | `rule` (from the rules file), `ltv`, `band`, the status it returns |
| 8 | `src/rules.py:34` `get_rule` | Reads one record from `rules/rules.yaml`. An unverified record raises here | The record: values, paragraph id, status |
| 9 | `src/index/hybrid.py:210` `search` | Vector and keyword search, merge, rerank, lender filter | `allowed`, `fused`, the final `out` list |
| 10 | `src/llm.py:152` `chat_json`, then `:105` `_raw` | Every model call | The full prompt (`messages`), the reply, tokens, retries, the 429 wait |
| 11 | `graph.py:130` judge, then `steps.py:206` `judge_one` | The model turns the tool facts and paragraph into a status | The `Judgement` it returns |
| 12 | `graph.py:153` critic, then `steps.py:261` `critic_judgement` and `:289` `critic_step` | Code, not a model, checks the verdict against the tool | Which kind of failure (wrong status, paragraph, number, rulebook) |
| 13 | `graph.py:174` revise | One retry: re-plan if the rulebook was wrong, re-run failed checks | `critic_failures`, what was re-run |
| 14 | `graph.py:205` finalize, `:214` drafter, then `steps.py:324` `draft_report` | Keep confirmed results, write the cited note | The final `results` and the note text |
| 15 | back in `loop.py` after `G.invoke` (line 100) | `finish()` writes the trace, cache and memory | The `RunOutput` |

### For a rule lookup (no application)

The graph takes the other branch after the planner: `graph.py:229` `lookup_guard` (an unverified rule is answered "unknown", with no model call, via `steps.py:343`), then `:246` `search`, `:257` `lookup_draft` (`steps.py:357`), `:264` `lookup_critic` (`steps.py:365`, which checks the citations exist and the numbers appear in them).

## Good first runs

| Goal | Run | Breakpoints |
| --- | --- | --- |
| See the rules file decide | `--app HL-2043` "Run the full RBI rule check…" | 7, 8, then 12 |
| See the critic catch a wrong answer | tick "start the planner with the wrong rulebook" in the app's Compliance checks page | 5, 12, 13 |
| See search at work | "What is the maximum LTV for a ₹25 lakh loan?" | 9, 10 |
| See a refusal | "Approve this loan." | 4 |
| See an unverified rule | a bank guarantor disclosure question | `lookup_guard` at `graph.py:229` |

The app's **Activity log** and **History and evaluation** pages show the same run afterwards, with every model call, search and tool call.
