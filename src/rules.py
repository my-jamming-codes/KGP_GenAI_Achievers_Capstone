"""Access to rules/rules.yaml. Tools may only use records whose status is 'verified'."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Optional

import yaml

from src import config


class UnverifiedRule(Exception):
    """Raised when code asks for a rule that is not verified. The caller must report 'unknown'."""


@lru_cache(maxsize=1)
def _doc() -> dict:
    return yaml.safe_load(config.RULES_FILE.read_text(encoding="utf-8"))


def rules_version() -> str:
    return _doc()["meta"]["rules_version"]


def all_rules(include_unverified: bool = False) -> list[dict]:
    return [r for r in _doc()["rules"] if include_unverified or r["status"] == "verified"]


def sources() -> dict:
    return _doc()["sources"]


def get_rule(rule_id: str) -> dict:
    for r in _doc()["rules"]:
        if r["rule_id"] == rule_id:
            if r["status"] != "verified":
                raise UnverifiedRule(f"{rule_id} is unverified: {r.get('unverified_reason', '').strip()}")
            return r
    raise KeyError(rule_id)


def is_verified(rule_id: str) -> bool:
    return any(r["rule_id"] == rule_id and r["status"] == "verified" for r in _doc()["rules"])


def _d(value) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def rules_for(theme: str, lender_type: str) -> list[dict]:
    """Verified records for a theme and lender type, oldest first: the amendment chain."""
    found = [r for r in all_rules() if r["theme"] == theme and r["lender_type"] == lender_type]
    return sorted(found, key=lambda r: _d(r.get("effective_date")) or date.min)


def source_lender_type(paragraph_id: str) -> Optional[str]:
    """Which lender type the cited document applies to, from the sources table."""
    doc_key = paragraph_id.split("/")[0]
    src = sources().get(doc_key)
    return src["applies_to"] if src else None
