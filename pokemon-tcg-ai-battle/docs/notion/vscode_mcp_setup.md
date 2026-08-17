# VS Code / Copilot / Notion MCP Setup

- VS CodeでCopilot Agent modeを使用する。
- Notion MCPはOAuth認証を利用する。
- token／cookie／secretをcommitしない。
- ページ対応は[page_map.yaml](page_map.yaml)で固定する。

確認プロンプト：

```text
Read docs/notion/page_map.yaml and use Notion MCP to fetch only the page
mapped to docs/plan/design/00_overall_plan.md. Do not edit anything.
Report the page title and first three section headings.
```

成功後、[copilot_prompts.md](copilot_prompts.md)の差分確認を実行する。
