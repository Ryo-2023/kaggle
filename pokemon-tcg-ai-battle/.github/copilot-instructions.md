# MAGE-PTCG Repository Instructions

リポジトリ共通ルールは`AGENTS.md`を正とする。本ファイルはCopilot向けの要約であり、矛盾時は`AGENTS.md`と各正典文書を優先する。

## Canonical Documentation

- `docs/plan/design/` and `docs/plan/implementation/` are the source of truth.
- Notion is a collaboration mirror.
- `docs/status/` contains current state.
- Page mappings are in `docs/notion/page_map.yaml`.

## Documentation Rules

After meaningful implementation, evaluation, integration, or architecture work:

1. Update the relevant canonical Markdown.
2. Update `docs/status/current_status.md`.
3. Update `docs/status/handoff.md`.
4. Add durable decisions to `docs/status/decisions.md`.
5. Preserve gates, dates, equations, Mermaid, schemas, tests, and failure modes.
6. Do not mark planned functionality as implemented.
7. Do not change progress without evidence.
8. Run `python scripts/docs/validate_docs.py`.

## Notion Sync

- Repository-to-Notion is the normal direction.
- Use exact page IDs.
- Never silently overwrite canonical Markdown from Notion.
- If both sides changed, produce a conflict report and stop.
- Strip YAML front matter before Notion writes.
- Do not delete child pages or databases.
- Verify every updated page.

## Engineering Priorities

- Maintain P0 Tier D／E continuously.
- Rule Agent v0 is Champion until Promotion passes.
- Competition data must not block C3／C4／C5.
- Student v0 GO／NO-GO: 2026-07-30.
- SMC、ES-MCCFR、PSRO、MAP-Elites、Tier A are Stretch.
- Maximum two active large slices.
