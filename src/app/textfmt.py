"""Text helpers for the workspace pages."""
from __future__ import annotations

import re

LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def softbreaks(text: str) -> str:
    """Make the line breaks in plain text show up when it is written as Markdown.

    Markdown joins lines separated by a single newline into one paragraph, and folds a plain line that follows a
    list item into that item. So a single break inside a paragraph becomes a hard break, and plain text after a
    list starts a paragraph of its own. Blank lines, list items and indented list continuations are left alone."""
    lines = str(text).split("\n")
    parts = [lines[0]]
    for prev, cur in zip(lines, lines[1:]):
        if not prev.strip() or not cur.strip() or LIST_ITEM.match(cur):
            sep = "\n"
        elif LIST_ITEM.match(prev):
            sep = "\n" if cur.startswith((" ", "\t")) else "\n\n"
        else:
            sep = "  \n"
        parts.append(sep + cur)
    return "".join(parts)
