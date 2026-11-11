"""Command line entry point: `rag ingest <path>` and `rag ask "<question>"`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ragkit.client import HttpInferenceClient, InferenceError
from ragkit.config import Settings
from ragkit.pipeline import RagPipeline
from ragkit.store import VectorStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag", description="RAG over your documents")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="chunk, embed and index a file or directory")
    p_ingest.add_argument("path", type=Path)
    p_ingest.add_argument("--append", action="store_true", help="add to the existing index")

    p_ask = sub.add_parser("ask", help="answer a question from the index")
    p_ask.add_argument("question")
    p_ask.add_argument("-k", type=int, default=None, help="number of passages to retrieve")
    p_ask.add_argument("--show-context", action="store_true", help="print retrieved passages")

    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        client = HttpInferenceClient(settings)

        if args.command == "ingest":
            if not args.path.exists():
                parser.error(f"path not found: {args.path}")
            store = VectorStore.load(settings.index_dir) if args.append else VectorStore()
            pipeline = RagPipeline(settings, client, store)
            count = pipeline.ingest(args.path)
            pipeline.store.save(settings.index_dir)
            print(f"Indexed {count} chunks ({len(pipeline.store)} total) in {settings.index_dir}/")
            return 0

        pipeline = RagPipeline(settings, client, VectorStore.load(settings.index_dir))
        answer = pipeline.ask(args.question, args.k)
        print(answer.text)
        print("\nSources:")
        for i, (chunk, score) in enumerate(answer.sources, 1):
            print(f"  [{i}] {chunk.source} (chunk {chunk.position}, score {score:.3f})")
            if args.show_context:
                print("      " + chunk.text.replace("\n", "\n      "))
        return 0
    except (RuntimeError, FileNotFoundError, InferenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
