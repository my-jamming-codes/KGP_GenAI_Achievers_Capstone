"""The chunking schemes used in the experiments keep track of which RBI paragraphs each chunk covers."""
from eval import chunking_eval as C


def chunks():
    para = lambda pid, n, w: {"paragraph_id": pid, "doc_key": "D", "title": "T", "lender_type": "hfc", "text": " ".join([w] * n)}
    return [para("D/1", 150, "a"), para("D/2", 300, "b"), para("D/3", 50, "c")]


def test_paragraph_scheme_never_crosses_a_paragraph():
    s = C.paragraph_scheme(chunks(), 120)
    assert [c["covers"] for c in s] == [["D/1"], ["D/1"], ["D/2"], ["D/2"], ["D/2"], ["D/3"]]
    assert max(len(c["text"].split()) for c in s) <= 120


def test_fixed_windows_span_paragraphs_and_overlap():
    s = C.fixed_scheme(chunks(), 200, 0)
    assert [len(c["text"].split()) for c in s] == [200, 200, 100]
    assert s[0]["covers"] == ["D/1", "D/2"] and s[2]["covers"] == ["D/2", "D/3"]
    o = C.fixed_scheme(chunks(), 400, 80)
    assert len(o) == 2 and o[0]["text"].split()[320:] == o[1]["text"].split()[:80]


def test_every_word_is_kept():
    for name in C.SCHEMES:
        s = C.build_scheme(name, chunks())
        if name == "fixed_400_o80":
            continue   # overlap repeats words by design
        assert sum(len(c["text"].split()) for c in s) == 500, name
