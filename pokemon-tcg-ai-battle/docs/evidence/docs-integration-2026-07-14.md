# ドキュメント統合記録（mage_ptcg_docs_migration → 既存正典、2026-07-14）

## Summary

第三者レビュー後の移行パッケージ`mage_ptcg_docs_migration/`（28ファイル）を、並行ドキュメント体系を作らずに既存正典へ差分統合した。既存の`docs/plan/design|implementation/00〜05`の詳細（数式、schema、テスト、Mermaid）は削除せず、移行側の新方針（P0／C1〜C5ロードマップ、日付付きGate、Rule v0 Champion固定、Competition Optional化、Student GO／NO-GO期限、safety上側信頼限界）を各文書へ節として追加した。commit、push、Notion書込みは未実施。

- 統合判定の反映：`ARCHITECTURE SOUND / EXECUTION SCOPE CORRECTED`
- 旧ロードマップ：移行指示の「旧B0〜B7直列」はこのリポジトリには存在せず、対応物は既存のG0〜G6 Gate直列だった。G0〜G6をDeprecated（歴史的参照）とし、P0／C1〜C5＋日付付きGateへ統一した。

## Existing-to-new mapping

| 既存ファイル | 現在の役割 | 正典性 | 移行パッケージの対応文書 | 統合方針 |
|---|---|---|---|---|
| `AGENTS.md` | Agent共通ガイド | 共通規則の正典 | `AGENTS.md`、`.github/copilot-instructions.md` | MERGE_INTO_EXISTING |
| `docs/plan/MAGE_PTCG_v5_README.md` | 計画文書の入口・正典順位 | 正典順位の正 | `docs/README.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/00_overall_plan.md` | 全体設計 | design正典 | `docs/canonical/00_overall_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/01_..._plan.md` | Knowledge／Deck設計 | design正典 | `01_knowledge_and_deck_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/02_..._plan.md` | Belief／Search設計 | design正典 | `02_belief_and_search_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/03_..._plan.md` | Teacher／Student設計 | design正典 | `03_teacher_student_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/04_..._plan.md` | Competition設計 | design正典 | `04_competition_intelligence_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/design/05_..._plan.md` | Evaluation／Submission設計 | design正典 | `05_evaluation_submission_design.md` | MERGE_INTO_EXISTING |
| `docs/plan/implementation/00〜05` | 実装仕様 | implementation正典 | `docs/canonical/*_implementation.md`（6件） | MERGE_INTO_EXISTING |
| `docs/plan/reference/plan_index.md` | 横断索引 | 従属 | （対応なし） | KEEP_EXISTING（リンク修正＋status／notion行の追加のみ） |
| `docs/plan/AGENTS.md` | plan配下の文章規約 | 従属 | （対応なし） | KEEP_EXISTING |
| `docs/evidence/*` | Evidence | Evidence正典 | （対応なし） | KEEP_EXISTING |
| （既存なし） | current status | 状態記録 | `docs/status/current_status.md` | ADD_NEW_FILE |
| （既存なし） | progress | 状態記録 | `docs/status/progress.md` | ADD_NEW_FILE |
| （既存なし） | handoff | 状態記録 | `docs/status/handoff.md` | ADD_NEW_FILE |
| （既存なし） | decisions | 判断記録 | `docs/status/decisions.md` | ADD_NEW_FILE |
| （既存なし） | Notion同期設定 | 運用設定 | `docs/notion/*`（6件） | ADD_NEW_FILE（`local_path`を既存実ファイルへ書き換え） |
| （既存なし） | Copilot instructions | 運用設定 | `.github/*`（3件） | ADD_NEW_FILE（パスを既存構成へ適合） |
| （既存なし） | docs検証 | 検証 | `scripts/docs/validate_docs.py` | ADD_NEW_FILE（`docs/canonical/`前提を`docs/plan/design|implementation/`へ適合、完了条件見出しの表記ゆれ許容、page_map整合検査を追加） |
| `docs/notion/local_hashes.json` | 同期用hash | 生成物 | 同名 | validate_docs.py実行で再生成 |

## Preserved existing content

- design／implementation全12文書の数式（SMC、MCCFR、safe resolving、meta posterior等）、Mermaid、schema、疑似コード、テスト一覧、完了条件は全量維持した。
- 旧G0〜G6 Gateは`design/00_overall_plan.md`§9.3と`design/05_..._plan.md`§9にDeprecated明示で保存した。
- Bootstrap Kernel先行方針（§9.1〜9.2、BK0〜BK2、ManualReviewSubstitution）は変更していない。
- `docs/plan/AGENTS.md`、`docs/evidence/`既存2件、`docs/agent/ai_orchestrator/`配下は無変更。

## Replaced / Deprecated content

- G0〜G6のGate直列 → Deprecated。正はP0／C1〜C5＋日付付きGate表（design/00 §9とdesign/05 §9で一致）。
- soak「100,000 gameをfinal minimum」 → 「10,000をfinal hard target、100k以上はoptional」（design/05 §11、implementation/05 §20・§22）。
- Runtime Tier表（design/00 §8）：旧A〜D → A〜E（D=Rule Agent v0 Champion、E=First Legal、AはStretch）。
- σ=(D,π,B,C) → σ=(D,π,B,K,C)（design/00 §3、Knowledge planeの追加）。
- ES-MCCFR：正典アルゴリズムとしての記述は維持しつつ、S3段階（既定経路ではない）と明示（design/02 §1.1・§9）。

## Conflicts（要ユーザー確認）

1. **CLI命名**：既存`mage-ptcg ...`（implementation/00 §7）と移行側`project ...`が並存する。暫定として`project ...`をSlice単位の最小集合と位置づけ、実装時に§7へ統一する注記を入れた（implementation/00 §13.4）。
2. **ActorInformationView**：implementation/00 §4.1の共通契約（`public_state`／`own_private_state`／`limited_knowledge`…）とimplementation/02 §3のfield粒度定義が異なる。§3を詳細版と位置づける注記を双方へ入れたが、C1統合時にどちらのfield構成を実装正とするかは実装確認が必要。
3. **CapabilityReport**：implementation/04に既存`KaggleCapabilityReport`（§3）と新`CompetitionCapabilityReport`（§1.1）が並存する。実測後の統一を注記した。
4. **`ai_orchestrator_review_disposition.md`**：READMEが参照するがリポジトリに存在しない（移行前からの欠落）。リンクを外しTODO表記にした。ファイルの所在確認が必要。
5. **テスト数の記載**：current_statusの「focused 99 pass／repository 345 pass／400試合」は第三者レビュー時の報告値であり、本統合作業では再実行していない（未検証のまま記載、implementation/00 §1.1に「報告値」と明記）。

## Validation

- `python scripts/docs/validate_docs.py` → `Validated 12 canonical documents.`（front matter必須キー、12ファイル、page ID重複なし、page_map整合、secret様パターンなし、`docs/notion/local_hashes.json`再生成）
- `git diff --check` → 指摘なし
- code fence／Mermaid fenceの偶数性・conflict marker → 問題なし（対象: AGENTS.md、docs/plan、docs/status、docs/notion、.github）
- 相対リンク検査 → broken 0件（既存の壊れた参照は5経路・Markdownリンク11箇所＋本文中のプレーンテキスト参照2箇所であり、`docs/agent/ai_orchestrator/`実パスへ修正済み。実体が存在しない`ai_orchestrator_review_disposition.md`のみリンクを外しTODO表記）
- Rule v1をChampionとする誤記、Replay必須依存の誤記 → なし
- Notion page ID：canonical 12件＋status 1件、重複なし
- 未実施：pytest（コード変更はdocs検証スクリプトの新規追加のみで既存コードに触れていない）、Notionページとの突合（Notion未接続）

## Next steps

1. 人間レビュー後、統合コミットを作成する（推奨メッセージは下記）。
2. commit後、`docs/notion/page_map.yaml`に従いGit→Notionの初回同期を行い、`last_sync.json`へcommit hashを記録する。
3. Conflicts 1〜4の判断（CLI統一、ActorInformationView field構成、CapabilityReport統一、review disposition所在）。
4. `mage_ptcg_docs_migration/`（移行元、Git未追跡）は独立レビュー後に削除済み。

推奨コミットメッセージ：

```text
docs(plan): 第三者レビュー後のP0/C1〜C5体系を既存正典へ差分統合

- design/implementation 00〜05へ改訂スコープ節とfront matterを追加し、G0〜G6をDeprecated化
- docs/status・docs/notion・.github・scripts/docs/validate_docs.pyを新設（既存パスへ適合）
- AGENTS.mdへcritical path・Notion同期規則を追記、壊れた計画書リンクを修正

Experiment: docs/evidence/docs-integration-2026-07-14.md
```
