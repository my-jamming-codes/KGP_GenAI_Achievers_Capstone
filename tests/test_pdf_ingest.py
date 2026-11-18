"""PDF ingestion: page furniture is removed, paragraphs are found, and a weak PDF never replaces a good web page."""
from src.ingest import parse as P
from src.ingest import pdf_text as T


def test_page_numbers_contents_hindi_and_watermark_are_removed():
    pages = ["भारतीय रिज़र्व बैंक\nRESERVE BANK OF INDIA\nChapter I - Preliminary ........................ 6\n58. No housing finance company shall\nWithdrawn",
             "  2  \n(1) up to ₹30 lakh with LTV ratio exceeding 90 percent,\nUp to ₹30 lakh ≤ 80 35\n35\nmore text\n7"]
    lines = T.clean_pages(pages)
    assert lines == ["RESERVE BANK OF INDIA", "58. No housing finance company shall",
                     "(1) up to ₹30 lakh with LTV ratio exceeding 90 percent,", "Up to ₹30 lakh ≤ 80 35", "35", "more text"]
    # the lone "35" in the middle of a page is a table cell and is kept; "2" and "7" at the page edges are page numbers


def test_caution_letterhead_is_removed():
    lines = T.clean_pages(["_______________\nCaution: RBI never sends mails, SMSs or makes calls asking for personal information\n"
                           "to anyone. Please do not respond in any manner to such offers.\nThe notice text."])
    assert lines == ["The notice text."]


def test_words_hyphenated_across_lines_are_joined():
    assert T.join_hyphenated(["shall not charge pre-", "payment levy", "Income-", "Tax"]) == ["shall not charge pre-payment levy", "Income-", "Tax"]


def test_paragraph_numbers_without_a_space_are_found():
    paras = P.split_paragraphs(["1. First rule text.", "2.In case of existing floating rate term loans", "(1)The bank shall provide"])
    assert [p for p, _ in paras] == ["1", "2"]
    assert P.split_paragraphs(["1.5 per cent of the amount"])[0][0] == "0"   # a decimal is not a paragraph number


def test_a_weak_pdf_keeps_the_web_page():
    html = [("0", "intro " * 50), *[(str(i), f"para {i} " * 30) for i in range(1, 11)]]
    good = [("0", "intro " * 50), *[(str(i), f"para {i} " * 30) for i in range(1, 11)]]
    assert P.pdf_check(html, good, {"5"}) == ""
    assert "short" in P.pdf_check(html, [("0", "tiny " * 60)], set())
    assert "numbered paragraphs" in P.pdf_check(html, [("0", "x " * 700), ("1", "y " * 10)], set())
    assert "cited by the rules file" in P.pdf_check(html, good, {"58"})
    assert "scanned image" in P.pdf_check([("0", "")], [("0", "a b")], set())


def test_pdf_file_names_match_case_and_browser_copies(tmp_path, monkeypatch):
    pdf = b"%PDF-1.4\n" + b"x" * 2000
    (tmp_path / "raw" / "pdf").mkdir(parents=True)
    (tmp_path / "raw" / "pdf" / "154MD (1).pdf").write_bytes(pdf)
    (tmp_path / "raw" / "pdf" / "fake.pdf").write_bytes(b"<html>bot check</html>" * 100)
    monkeypatch.setattr(T.config, "DATA", tmp_path)
    have = T.downloaded()
    assert have == {"154md.pdf": "data/raw/pdf/154MD (1).pdf"}            # the saved bot-check page is not a PDF
    url, saved = T.pdf_for("CB-CF", "", have)
    assert url.endswith("154MD.PDF") and saved == "data/raw/pdf/154MD (1).pdf"
