"""The project task board: a standalone page and its data, shown on the Task board page of the workspace.

The data is src/app/board/tasks.json (edit it to change a status or an assignee). The page is
src/app/board/task_board.html, with the data placed into it here."""
from __future__ import annotations

import json
from pathlib import Path

BOARD = Path(__file__).resolve().parent / "board"
STATUSES = ("done", "progress", "open")
PLACEHOLDER_START, PLACEHOLDER_END = "/*TASKS_JSON*/", "/*END*/"


def load_tasks() -> dict:
    data = json.loads((BOARD / "tasks.json").read_text(encoding="utf-8"))
    for t in data["tasks"]:
        if t["status"] not in STATUSES:
            raise ValueError(f"task {t['n']} has status {t['status']!r}; use one of {STATUSES}")
        if t["role"] not in data["leads"]:
            raise ValueError(f"task {t['n']} has a role with no lead: {t['role']!r}")
    return data


def render_html() -> str:
    page = (BOARD / "task_board.html").read_text(encoding="utf-8")
    start, end = page.index(PLACEHOLDER_START), page.index(PLACEHOLDER_END)
    payload = json.dumps(load_tasks(), ensure_ascii=False).replace("</", "<\\/")   # keeps the data from closing the script tag
    return page[:start] + payload + page[end + len(PLACEHOLDER_END):]


def counts(data: dict | None = None) -> dict[str, int]:
    tasks = (data or load_tasks())["tasks"]
    return {s: sum(1 for t in tasks if t["status"] == s) for s in STATUSES} | {"total": len(tasks)}
