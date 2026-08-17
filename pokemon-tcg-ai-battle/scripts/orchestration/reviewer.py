"""Strict parser and read-only review boundary for overnight patches."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class ReviewResult:
    verdict: str; risk: str; findings: tuple[str, ...]; auto_integration_allowed: bool

class ReviewProvider(Protocol):
    def review(self, worktree, context: dict[str, object], invocation_dir): ...

def parse_review(payload: str) -> ReviewResult:
    try: value = json.loads(payload)
    except json.JSONDecodeError as exc: raise ValueError("malformed reviewer JSON") from exc
    if not isinstance(value, dict) or set(value) != {"verdict", "risk", "findings", "auto_integration_allowed"}: raise ValueError("malformed reviewer schema")
    if value["verdict"] not in {"PASS", "PASS_WITH_NOTES", "REJECT"} or value["risk"] not in {"LOW", "MEDIUM", "HIGH"} or not isinstance(value["findings"], list) or any(not isinstance(x, str) for x in value["findings"]) or not isinstance(value["auto_integration_allowed"], bool): raise ValueError("malformed reviewer values")
    return ReviewResult(value["verdict"], value["risk"], tuple(value["findings"]), value["auto_integration_allowed"])

def review_allows_integration(result: ReviewResult) -> bool:
    return result.verdict == "PASS" and result.risk == "LOW" and result.auto_integration_allowed
