#!/usr/bin/env python3
"""Claude Code の Bash 実行前に不可逆な破壊操作を拒否する。"""

from __future__ import annotations

import json
import re
import sys


DENY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+(-\w*[rR]\w*|-\w*f\w*[rR]|--recursive|--force)\b"), "再帰・強制削除は不可逆"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard は未コミット変更を破棄する"),
    (re.compile(r"\bgit\s+clean\s+-\w*f"), "git clean -f は未追跡ファイルを削除する"),
    (re.compile(r"\bgit\s+checkout\s+(--\s+\.|\.$|\.\s)"), "git checkout -- . は作業ツリーの変更を破棄する"),
    (re.compile(r"\bgit\s+restore\s+(--\s+)?\.(\s|$)"), "git restore . は作業ツリーの変更を破棄する"),
    (re.compile(r"\bgit\s+push\s+(--force|-f|--force-with-lease)\b"), "force push はリモート履歴を変更する"),
    (re.compile(r"\bdd\s+(if|of)="), "dd はデバイスやファイルを直接上書きする"),
    (re.compile(r"\bmkfs\b"), "mkfs はファイルシステムを初期化する"),
    (re.compile(r"\bfind\b.*\s-delete\b"), "find -delete は一括削除する"),
    (re.compile(r"\bfind\b.*-exec\s+rm\b"), "find -exec rm は一括削除する"),
    (re.compile(r"shutil\.rmtree"), "shutil.rmtree はディレクトリを再帰削除する"),
    (re.compile(r"\b(chmod|chown)\s+-\w*R"), "再帰的な権限変更は影響範囲が広い"),
    (re.compile(r"\btruncate\s+-s\s*0\b"), "truncate -s 0 は内容を破棄する"),
]


def match_deny(command: str) -> str | None:
    for pattern, reason in DENY_RULES:
        if pattern.search(command):
            return reason
    return None


def decide(payload: object) -> str | None:
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command", "")
    return match_deny(command) if isinstance(command, str) else None


def emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[guard_destructive] {reason}。"
                "必要ならユーザーの明示的な承認を得て手動で実行する"
            ),
        }
    }, ensure_ascii=False))


def selftest() -> int:
    denied = [
        "rm -rf data/raw", "rm -r build", "git reset --hard HEAD~1",
        "git clean -fd", "git checkout -- .", "git restore .",
        "git push --force origin main", "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sdb", "find . -name '*.csv' -delete",
        "python3 -c 'import shutil; shutil.rmtree(\"data\")'",
        "chmod -R 777 .", "truncate -s 0 deck.csv",
    ]
    allowed = [
        "git status", "git commit -m 'x'", "git push origin main",
        "git checkout -b feat/x", "git restore --staged main.py",
        "find . -name '*.py'", "rm -i scratch.txt",
    ]
    for command in denied:
        assert match_deny(command) is not None, f"deny 漏れ: {command}"
    for command in allowed:
        assert match_deny(command) is None, f"誤検出: {command}"
    assert decide({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}})
    assert decide({"tool_name": "Edit", "tool_input": {"command": "rm -rf x"}}) is None
    print(f"OK: deny {len(denied)} / allow {len(allowed)}")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    try:
        reason = decide(json.load(sys.stdin))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0
    if reason:
        emit_deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
