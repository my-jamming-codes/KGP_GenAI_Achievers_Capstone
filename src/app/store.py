"""Applications, statuses, comments and officer actions. Status changes only through a human officer's action."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import config
from src.safety.pii import mask_obj
from src.tracing.tracer import log_officer_action

USERS = {
    "officer.kiran": {"name": "Kiran Rao (synthetic)", "role": "compliance_officer"},
    "processor.dev": {"name": "Dev Malhotra (synthetic)", "role": "credit_processor"},
    "approver.sana": {"name": "Sana Qureshi (synthetic)", "role": "approver"},
}
STATUSES = ["new", "in_review", "information_requested", "compliance_cleared", "returned"]


class Store:
    def __init__(self, applications_dir: Optional[Path] = None, store_dir: Optional[Path] = None, trace_path: Optional[Path] = None) -> None:
        self.app_dir = Path(applications_dir) if applications_dir else config.APPLICATIONS_DIR
        self.dir = Path(store_dir) if store_dir else config.STORE_DIR
        self.trace_path = trace_path
        self.file = self.dir / "state.json"

    # -- applications (the planted-issue answer key is never read here)
    def applications(self) -> list[dict]:
        apps = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.app_dir.glob("HL-*.json"))]
        state = self._state()
        for a in apps:
            a["status"] = state.get(a["application_id"], {}).get("status", a["status"])
        return apps

    def application(self, app_id: str) -> dict:
        return next(a for a in self.applications() if a["application_id"] == app_id)

    def masked_view(self, app: dict) -> dict:
        """What every screen shows: PAN, Aadhaar, phone, email and address masked."""
        return mask_obj(app)

    # -- state
    def _state(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}

    def _save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    def set_status(self, app_id: str, status: str, user_id: str, reason: str = "", trace_id: Optional[str] = None) -> None:
        """Only a signed-in compliance officer can change a status. The assistant has no user id and cannot."""
        user = USERS.get(user_id)
        if not user or user["role"] != "compliance_officer":
            raise PermissionError("only the compliance officer can change an application's status")
        if status not in STATUSES:
            raise ValueError(status)
        state = self._state()
        state.setdefault(app_id, {})["status"] = status
        self._save(state)
        action = {"compliance_cleared": "cleared", "returned": "returned", "information_requested": "information_requested"}.get(status, status)
        log_officer_action(user_id, app_id, action, reason, trace_id, self.trace_path)

    def add_comment(self, app_id: str, user_id: str, target: str, text: str) -> None:
        if user_id not in USERS:
            raise PermissionError("unknown user")
        state = self._state()
        state.setdefault(app_id, {}).setdefault("comments", []).append(
            {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user": user_id, "target": target, "text": text})
        self._save(state)
        log_officer_action(user_id, app_id, "commented", f"{target}: {text}", None, self.trace_path)

    def comments(self, app_id: str) -> list[dict]:
        return self._state().get(app_id, {}).get("comments", [])

    def record_check_action(self, app_id: str, user_id: str, check: str, action: str, trace_id: Optional[str] = None) -> None:
        """accepted or disputed, on one rule check result."""
        state = self._state()
        state.setdefault(app_id, {}).setdefault("check_actions", {})[check] = action
        self._save(state)
        log_officer_action(user_id, app_id, action, check, trace_id, self.trace_path)

    def check_actions(self, app_id: str) -> dict:
        return self._state().get(app_id, {}).get("check_actions", {})

    def save_results(self, app_id: str, output: dict) -> None:
        state = self._state()
        state.setdefault(app_id, {})["last_check"] = output
        self._save(state)

    def last_check(self, app_id: str) -> Optional[dict]:
        return self._state().get(app_id, {}).get("last_check")
