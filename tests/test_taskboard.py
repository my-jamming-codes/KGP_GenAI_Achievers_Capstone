"""The project task board data and page."""
import json

from src.app import taskboard


def test_tasks_are_valid_and_counted():
    data = taskboard.load_tasks()
    tasks = data["tasks"]
    assert len({t["n"] for t in tasks}) == len(tasks)          # numbers are unique
    assert all(t["assignee"] and t["task"] and t["req"] in {"I", "II", "III", "IV", "V", "VI"} for t in tasks)
    c = taskboard.counts(data)
    assert c["done"] + c["progress"] + c["open"] == c["total"] == len(tasks)


def test_page_carries_the_data_and_no_placeholder_data():
    html = taskboard.render_html()
    assert "/*TASKS_JSON*/" not in html or html.count("/*TASKS_JSON*/") == 0
    payload = json.dumps(taskboard.load_tasks(), ensure_ascii=False)
    assert payload[:60] in html


def test_a_bad_status_is_refused(tmp_path, monkeypatch):
    bad = json.loads((taskboard.BOARD / "tasks.json").read_text())
    bad["tasks"][0]["status"] = "finished"
    (tmp_path / "tasks.json").write_text(json.dumps(bad))
    monkeypatch.setattr(taskboard, "BOARD", tmp_path)
    try:
        taskboard.load_tasks()
    except ValueError as e:
        assert "finished" in str(e)
    else:
        raise AssertionError("a bad status was accepted")
