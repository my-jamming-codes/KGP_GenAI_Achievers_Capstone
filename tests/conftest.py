"""Shared test doubles: a fake model and a small in-memory index, so tests never call an endpoint."""
import re
from datetime import date

import pytest

from src.schemas import Case, RetrievedParagraph
from src.tools import checks as tools

L = 100000


class FakeIndex:
    corpus_version = "test-corpus-1"

    def __init__(self):
        self.paras = {
            "HFC-MD/58": ("hfc", "58. No housing finance company shall grant housing loans to individuals: (1) up to ₹30 lakh with LTV ratio exceeding 90 percent, (2) above ₹30 lakh and up to ₹75 lakh with LTV ratio exceeding 80 percent, and, (3) above ₹75 lakh with LTV ratio exceeding 75 per cent"),
            "CB-CF/111": ("bank", "111. The bank shall abide by the following Loan to Value (LTV) and Risk Weights. Up to ₹30 lakh ≤ 80 35; > 80 and ≤ 90 50; Above ₹30 lakh & up to ₹75 lakh ≤ 80 35; Above ₹75 lakh ≤ 75 50"),
            "HFC-MD/158": ("hfc", "158. When a person is considering being a guarantor to a loan, they should be informed about: (1) their liability as guarantor;"),
            "HFC-MD/163": ("hfc", "163. The HFC shall not charge pre-payment levy or penalty on pre-closure of housing loans"),
            "HFC-MD/22": ("hfc", "22. risk weights ... LTV Ratio ≤ 80% | 35"),
        }

    def _hits(self, ids):
        return [RetrievedParagraph(paragraph_id=i, doc_key=i.split("/")[0], lender_type=self.paras[i][0], score=1.0, text=self.paras[i][1]) for i in ids]

    def search(self, query, lender_type=None, k=5, mode="hybrid_rerank"):
        words = set(re.findall(r"[a-z]+", query.lower()))
        ids = [i for i, (lt, text) in self.paras.items() if lender_type in (None, lt)]
        ids.sort(key=lambda i: -len(words & set(re.findall(r"[a-z]+", self.paras[i][1].lower()))))
        return self._hits(ids[:k])

    def fetch_paragraph(self, paragraph_id):
        return self._hits([i for i in self.paras if i == paragraph_id])


def make_case(**kw):
    base = dict(lender_type="hfc", hfc_layer="middle", loan_amount=64 * L, property_value=80 * L, case_date=date(2026, 11, 12))
    base.update(kw)
    return Case(**base)


def honest_responder(case, checks, lender=None, wrong_first=None):
    """A fake model that plans the given checks and judges exactly what the tools find.
    wrong_first: {check: status} makes the first judgement of that check wrong."""
    used = {}

    def respond(schema, system, user):
        if schema == "PlannerReply":
            return {"intent": "rule_check", "scope": "specific", "lender_type": lender or case.lender_type, "checks": checks, "search_query": ""}
        if schema == "DraftReply":
            return {"answer": "Results listed. Recommendation only; the officer decides.", "citations": []}
        check = re.search(r"Rule check: (\w+)", user).group(1)
        lt = "hfc" if "Lender type: Housing" in user else "bank"
        tool = tools.run_tool(check, case.model_copy(update={"lender_type": lt}))
        status = tool.status
        if wrong_first and check in wrong_first and not used.get(check):
            used[check] = True
            status = wrong_first[check]
        return {"status": status, "paragraph_id": tool.paragraph_id or "", "limit_read": None, "reason": "as computed"}

    return respond


@pytest.fixture(autouse=True)
def _activity_log_in_tmp(tmp_path, monkeypatch):
    """Activity events from tests go to a temporary file, never the project's log."""
    from src.tracing import activity

    monkeypatch.setattr(activity, "log_path", lambda: tmp_path / "activity.jsonl")


@pytest.fixture
def index():
    return FakeIndex()


