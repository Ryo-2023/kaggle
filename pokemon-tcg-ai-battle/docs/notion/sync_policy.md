# Notion Synchronization Policy

## Source of Truth

`docs/plan/design/`と`docs/plan/implementation/`のMarkdownが正典。Notionは共同作業用ミラー。ページ対応は[page_map.yaml](page_map.yaml)で固定する。

## Status Dashboard（特殊マッピング）

`page_map.yaml`の`status_pages`が対応するNotionページ（`39bfefac-d260-8143-b980-d17e9fb958a7`、「Pokémon TCG AI Battle Challenge｜プロジェクト管理」）は、通常のbody全置換ページではなく、意図的なstatus／dashboardミラーである。

- `docs/status/current_status.md`を主たる状態ソースとする。
- `docs/status/progress.md`、`docs/status/handoff.md`、`docs/status/decisions.md`は、このダッシュボードの対応sectionへ供給できる。
- 機械管理はstatus／progress／current-focus／handoff／decisions／sync-metadataの各sectionに限る。
- レイアウト、callout、column、quick link、child page、navigationは人間管理とし、機械同期で変更しない。
- child page「プロジェクトメニュー」を削除・上書きしない。
- このページへの全ページ置換（full-page replacement）を禁止する。

## Normal Flow

1. コードとMarkdownを同じbranchで更新
2. `python scripts/docs/validate_docs.py`
3. `git diff`
4. commit／merge
5. Notion MCPでmapped pageを更新
6. fetchして検証
7. `last_sync.json`更新

## Notion-originated Changes

1. exact page IDでfetch
2. localと比較
3. textual／semantic diff
4. 自動適用しない
5. 承認後にGitへ反映
6. commit後にNotionへ正式同期

## 禁止

- 双方向自動マージ
- Notionからlocal正典のsilent overwrite
- title検索だけで更新先決定
- 数式、Mermaid、Gate、期限、失敗経路の省略
- plannedをimplementedへ変更
- evidenceなしの進捗変更
- secretのcommit

## Conflict

両側変更時は停止し、only in Git、only in Notion、conflicting decisions、dates／Gate／progress、conversion lossを報告する。

## 変換

- YAML front matterはNotionへ送らない
- `# Title`はページtitleと重複するため省略可
- blockquoteはcalloutへ変換可
- Mermaid／LaTeX／codeを保持
- child page／databaseを削除しない
