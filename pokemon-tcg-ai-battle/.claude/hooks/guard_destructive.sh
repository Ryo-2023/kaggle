#!/usr/bin/env bash
# Claude Code PreToolUse hook の薄い入口。stdin の JSON を Python 実装へ渡す。
exec python3 "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/guard_destructive.py"
