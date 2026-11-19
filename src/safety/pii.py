"""PII masking by pattern. Used on the stored application view, traces, logs and everything sent to the model."""
from __future__ import annotations

import re
from typing import Any

PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?(\d{4})(?!\d)")
PHONE_RE = re.compile(r"(?<![\dA-Za-z])(?:\+91[ -]?)?[6-9]\d{4}[ -]?\d{1}(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
ADDRESS_KEYS = ("address",)


def mask_text(text: str) -> str:
    """Mask PAN, Aadhaar, phone and email, keeping the last four characters where a rule may need them."""
    text = PAN_RE.sub(lambda m: "XXXXXX" + m.group(0)[-4:], text)
    text = AADHAAR_RE.sub(lambda m: "XXXX XXXX " + m.group(1), text)
    text = PHONE_RE.sub(lambda m: "XXXXXX" + m.group(1), text)
    return EMAIL_RE.sub(lambda m: m.group(1) + "***@" + m.group(2), text)


def mask_obj(obj: Any, key: str = "") -> Any:
    """Mask every string inside a nested structure. Address fields are hidden whole."""
    if isinstance(obj, dict):
        return {k: mask_obj(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v, key) for v in obj]
    if isinstance(obj, str):
        if any(a in key.lower() for a in ADDRESS_KEYS) and obj:
            return "[address hidden]"
        return mask_text(obj)
    return obj


def contains_full_identifier(text: str) -> bool:
    """True if a full PAN, Aadhaar, phone number or email is present. Used to check model output."""
    return bool(PAN_RE.search(text) or AADHAAR_RE.search(text) or PHONE_RE.search(text) or EMAIL_RE.search(text)) and mask_text(text) != text
