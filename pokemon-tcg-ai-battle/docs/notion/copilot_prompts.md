# Copilot Agent Prompts

## 差分確認のみ

```text
Use the Notion MCP server and read docs/notion/page_map.yaml.
Fetch exact pages by ID. Compare them with local Markdown.
Do not edit anything.
Report sections only on each side, conflicting decisions, changed dates,
gates, percentages, implementation status, and conversion loss.
Treat Git Markdown as canonical.
Preserve equations, Mermaid, schemas, tests, completion conditions,
failure modes, and GO/NO-GO rules.
```

## 承認済み差分をGitへ

```text
Apply the approved reconciliation report to docs/plan/design/ and
docs/plan/implementation/.
Make the smallest edits. Preserve Japanese and technical detail.
Do not mark planned items as implemented.
Update docs/status separately.
Run python scripts/docs/validate_docs.py and show git diff.
Do not update Notion.
```

## Git→Notion

```text
Read docs/notion/page_map.yaml.
Use exact page IDs. Git Markdown is the source.
Strip YAML front matter. Preserve equations, Mermaid, tables, code,
dates, gates, and failure modes. Do not delete child pages or databases.
Record git commit and content hash. Fetch each page after updating.
Update last_sync.json only after verification.
```
