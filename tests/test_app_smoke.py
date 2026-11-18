"""The Streamlit workspace renders every page without an exception, and sign-off is the officer's click."""
import pytest
from streamlit.testing.v1 import AppTest

PAGES = ["Work queue", "Application", "Compliance checks", "Comments and sign-off", "FAQ", "Prompt lab", "Retrieval lab", "Activity log", "History and evaluation"]
MAIN = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "app" / "main.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("TRACE_FILE", str(tmp_path / "traces.jsonl"))
    import importlib

    from src import config
    importlib.reload(config)
    at = AppTest.from_file(MAIN, default_timeout=30)
    at.run()
    return at


def sign_in(at, user="officer.kiran"):
    at.selectbox[0].set_value(user)
    at.button[0].click().run()
    return at


def test_sign_in_and_every_page_renders(app):
    at = sign_in(app)
    assert not at.exception
    at.session_state["app_id"] = "HL-2048"
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)


def test_application_view_masks_identifiers(app):
    at = sign_in(app)
    at.session_state["app_id"] = "HL-2041"
    at.sidebar.radio[0].set_value("Application").run()
    text = " ".join(m.value for m in at.markdown)
    assert "ABCPV1234F" not in text and "2345 6789 0123" not in text and "XXXXXX234F" in text


def test_only_officer_buttons_are_enabled(app):
    at = sign_in(app, "approver.sana")
    at.session_state["app_id"] = "HL-2041"
    at.sidebar.radio[0].set_value("Comments and sign-off").run()
    cleared = next(b for b in at.button if b.label == "Compliance cleared")
    assert cleared.disabled


def test_faq_page_shows_rule_quotes_and_unverified_rule(app):
    at = sign_in(app)
    at.sidebar.radio[0].set_value("FAQ").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "HFC-MD/58" in text   # LTV cap paragraph, from the rules file
    warnings = " ".join(w.value for w in at.warning)
    assert "unverified in the rules file" in warnings   # bank guarantor disclosure


def test_activity_log_page_shows_questions_as_groups(app):
    from src.tracing import activity

    with activity.context(run="abc123", user="officer.kiran", title="Retrieval lab: stamp duty"):
        activity.emit("agent", "Searching the RBI corpus", ms=300, node="search", next="draft")
        activity.emit("vector", "Vector search in Qdrant (in memory)", ms=120, hits=40, query="stamp duty")
    at = sign_in(app)
    at.sidebar.radio[0].set_value("Activity log").run()
    assert not at.exception
    assert any("Retrieval lab: stamp duty" in e.label for e in at.expander)
    assert len(at.dataframe) >= 1
