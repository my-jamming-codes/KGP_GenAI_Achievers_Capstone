"""Run one question through the same code path as the app, with no browser, so it can be stepped through in a debugger.

Usage: python debug/run_one_question.py [--app HL-2043] [--tab "Loan request"] [--strategy C] "your question"

Saved state, the answer cache and traces go to a temporary folder (set DEBUG_KEEP=1 to use the real ones), so debugging
never changes the demo data. The model and embeddings endpoints come from the usual settings (.env or the shell).
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get("DEBUG_KEEP"):
    tmp = Path(tempfile.gettempdir()) / "rule_checker_debug"
    for name in ("STORE_DIR", "CACHE_DIR"):
        os.environ.setdefault(name, str(tmp / name.lower()))
    os.environ.setdefault("TRACE_FILE", str(tmp / "traces.jsonl"))

from src.agents import loop  # noqa: E402
from src.app.store import Store  # noqa: E402
from src.index.hybrid import HybridIndex  # noqa: E402
from src.llm import LLM  # noqa: E402
from src.schemas import case_from_application  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--app", default=None, help="application id, for example HL-2043")
    ap.add_argument("--tab", default=None, help="tab shared with the assistant, for example 'Loan request'")
    ap.add_argument("--strategy", default="C", choices=["A", "B", "C"])
    args = ap.parse_args()

    store = Store()
    case, screen = None, None
    if args.app:
        app = store.application(args.app)
        case = case_from_application(app)
        if args.tab:
            screen = {"application_id": args.app, "tab": args.tab, "fields": store.masked_view(app)["tabs"][args.tab]}

    out = loop.run(                       # <- the hand-off from the UI to the agents; step into this
        args.question, case=case, application_id=args.app, screen=screen, strategy=args.strategy,
        llm=LLM(), index=HybridIndex(), user="officer.kiran", cache=loop.AnswerCache(), force_full=bool(args.app and not args.tab),
    )

    print("\n--- result ---")
    print("refused:", out.refused, "| steps:", out.steps_used, "| tokens:", out.tokens, "| critic failures:", out.critic_failures)
    print(out.answer)
    for r in out.results:
        print(f"  {r.check:26s} {r.status:18s} {r.paragraph_id or '-'}")
    print("trace:", out.trace_id)


if __name__ == "__main__":
    main()
