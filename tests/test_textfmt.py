"""Line breaks in plain text must survive being written as Markdown."""
from src.app.textfmt import softbreaks


def test_a_single_break_inside_a_paragraph_becomes_a_hard_break():
    assert softbreaks("first line\nsecond line") == "first line  \nsecond line"
    assert softbreaks("first line\nsecond line\nthird") == "first line  \nsecond line  \nthird"


def test_plain_text_after_a_list_gets_its_own_paragraph():
    note = "- ltv_cap: met. ok\n- kyc: met. ok\nRecommendation only; the officer decides."
    assert softbreaks(note) == "- ltv_cap: met. ok\n- kyc: met. ok\n\nRecommendation only; the officer decides."


def test_lists_blank_lines_and_continuations_are_left_alone():
    assert softbreaks("- a\n- b\n- c") == "- a\n- b\n- c"
    assert softbreaks("1. a\n2. b") == "1. a\n2. b"
    assert softbreaks("para one\n\npara two") == "para one\n\npara two"
    assert softbreaks("- a\n  still a\n- b") == "- a\n  still a\n- b"


def test_a_single_line_and_empty_text_are_unchanged():
    assert softbreaks("one line") == "one line" and softbreaks("") == ""
