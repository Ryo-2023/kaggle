---
project: MAGE-PTCG
evidence: o1-dataset-materialization-and-audit
as_of: 2026-07-18
scope: integration/o1-competition-intelligence-v1 worktree
commits: 37c9eef (finalize snapshot pipeline), 07f8d49 (dataset materialization + audit)
---

# O1 Knowledge Claim拡張 / Dataset Materialization / Audit Evidence

## 前提（本セッション開始時点の状態）

canonical worktree (`feature/belief-guided-search`) のローカルHEAD (`3e198e8`) は、`git fetch`後のorigin (`c69aadc`) より **14 commit先行**しており、origin側に未pushの状態だった。この14 commitには、O1-0/O1-1（Slice 0-2）、O1-2〜O1-4（Slice 2-4）、O1-5、O1-6、および前回セッションの「カノニカル最終同期」が既に含まれていた。

事前に主張（1351 passed等）を鵜呑みにせず、`PYTHONPATH=src /usr/bin/python3 -m pytest -q` を実行して実測で確認した。

```
1351 passed, 5 warnings in 234.15s
```

`main.py`／`deck.csv`はoriginと`git diff origin/feature/belief-guided-search..HEAD -- main.py deck.csv`で無差分。この検証結果を根拠に、既存O1-2〜O1-4実装をそのまま採用し、本セッションでは残存gapの実装に集中した。

## gap分析で判明した未実装項目

読み取り専用調査（Explore agent、40項目チェックリスト）により、以下が未実装または不十分と判明した。

- `KnowledgeClaim`にpermission・observed/inferred・training/runtime eligibilityフィールドがない（`raw_source_id`は未検証の自由文字列、`permissions_summary`は`{}`固定）。
- Snapshot由来のdataset生成には、選択済みJSONLの出力のみで、manifest・audit report・statistics report・baseline mode・replay/knowledge/both切替のいずれも存在しない。

## 実装（commit 37c9eef: finalize competition intelligence snapshot pipeline）

- `contracts.py`: `KNOWLEDGE_CLAIM_SCHEMA_VERSION`をv1→v2へ。`EvidenceBasis`（OBSERVED/INFERRED、既定INFERRED）、`training_eligible`／`runtime_eligible`（既定False）、`supersedes`を追加。`training_eligible`/`runtime_eligible`は`status=SUPPORTED`のときのみTrueにできる（`__post_init__`で強制）。`with_transition`は`SUPPORTED`への遷移時のみeligibilityを明示付与でき、それ以外の遷移では自動的にFalseへ戻す。
- `contracts.py`: `IntelligenceSnapshot`に`permission_summary: Mapping[str, int]`を追加。
- `claim_bundle.py`: `evidence_basis`をbundleから読み取り（既定INFERRED）、`training_eligible`/`runtime_eligible`はimport時に常にFalse固定（statusと同じく「RAW以外で輸入不可」の一貫性）。
- `knowledge_registry.py`: `transition_claim`にeligibility付与時のpermission再検証（TRAINING/REPORTING）を追加。`_claim_from_payload`を新フィールドに対応。
- `knowledge_snapshot.py`: `KnowledgeSnapshot.evidence_basis_summary`を追加。
- `pipeline.py`: `run_import_knowledge`が`raw_source_id`の実archive・ANALYSIS権限を検証（未archiveならfail-closed）。`run_build_knowledge_snapshot`が`permissions_summary`/`evidence_basis_summary`を実データから算出。`run_archive_note`を追加（`raw_notes.archive_raw_note`をpipeline層から到達可能に）。
- `snapshot_builder.py`: `build_snapshot`が`permission_summary`を実データ（contributing sourcesのallowed_uses）から算出。
- `cli.py`: `archive-note`コマンドを追加。

### deck identity leakageに関する設計判断

`leakage_audit.py`は既に`deck_fingerprint_leakage_count`を計算・報告しているが、hard gate（`_HARD_INVARIANT_FIELDS`）には含めていない。これは同モジュールのdocstringに明記された意図的設計であり、`test_clean_split_passes_with_genuinely_diverse_opponents`は実際にdeck参照が全episode一定のfixtureデータで`passed=True`を期待するテストとして既に存在する。単一固定60枚デッキが繰り返し使われるドメインでdeck一致を無条件leakageとして扱うと、有効な分割が原理的に作れなくなるため、本セッションではこの設計を変更せず維持した。新規dataset audit（下記）では`leakage_check`としてこの値を含む監査結果全体を可視化する。

## 実装（commit 07f8d49: dataset materialization + audit）

`dataset_materialization.py`を新規追加し、`materialize_dataset()`が以下を生成する。

- `replay.jsonl` / `knowledge_claims.jsonl`（`sources={replay,knowledge,both}`で選択）
- `manifest.json`: `dataset_id`／`dataset_hash`／`snapshot_id`／`knowledge_snapshot_id`／`source_inventory`／`split`
- `audit_report.json`: `total_source_row_count`、`adopted_row_count`、`excluded_row_count`、`quarantined_row_count`、`excluded_reason_counts`、`observed_count`／`inferred_count`、`duplicate_count`、`leakage_check`、`permission_check`、`missing_provenance_count`、`determinism_verified`
- `statistics_report.json`: `by_source`／`by_deck`／`by_matchup`／`by_seat`／`by_action_type`（`DecisionRecord.phase`を代理指標として使用）／`by_split`／`knowledge_evidence_basis_counts`

`baseline=True`は、Snapshotを経由せず`offline_reader.iter_rule_bc_rows`で元のOffline Training collectionを無加工でそのまま読み込み、`sources`は`replay`に固定される（O1無しの既存動作の再現）。`sources=knowledge`は、`training_eligible=True`かつ現在も`raw_source_id`がTRAINING権限を持つclaimのみを採用する（権限は生成時に再検証、defense-in-depth）。決定性は、knowledge claim選択を関数内で2回独立計算し一致を要求する自己検証で担保する（一致しない場合は`DatasetMaterializationError`）。

CLI `materialize-dataset`を追加。`offline_training`／`student`のコードは一切import・変更しない（`test_offline_training_invariance.py`で検証）。

## テスト

新規focused test 33件（既存282件＋33件で315件）。

```
PYTHONPATH=src /usr/bin/python3 -m pytest tests/competition_intelligence \
  tests/test_check_o1_protected_files.py \
  tests/test_competition_intelligence_runtime_isolation.py \
  tests/test_competition_intelligence_cli_end_to_end.py -q
```
結果：315 passed。

```
PYTHONPATH=src /usr/bin/python3 -m pytest -q
```
結果：1384 passed, 5 warnings（既知の環境依存warningのみ、failureなし）。

主なテスト内容：
- `test_contracts.py`: training/runtime eligibilityがSUPPORTED以外で拒否、supersedesの自己参照拒否、`with_transition`によるeligibility付与・非SUPPORTED遷移時の自動リセット、`permission_summary`のhash寄与。
- `test_knowledge.py`: `evidence_basis`の既定・明示指定・不正値拒否、import時のeligibility強制ignore、`evidence_basis_summary`のhash寄与。
- `test_pipeline_end_to_end.py`: 未archiveの`raw_source_id`拒否、ANALYSIS権限欠如時の拒否、`permissions_summary`が実データを反映すること、`transition_claim`のTRAINING権限再検証（拒否・成功両方）。
- `test_dataset_materialization.py`（新規ファイル）: baseline/replay/knowledge/bothモード、dataset_hash決定性（同一入力で2回生成しhash一致、異なる`created_at`でも不変）、training_eligibleでないclaimの除外理由記録。
- `test_offline_training_invariance.py`（新規ファイル）: O1利用前後で`mage_ptcg.offline_training.dataset.build_dataset`の出力がbyte一致、`dataset_materialization`/`offline_adapter`が`mage_ptcg.offline_training.dataset`/`mage_ptcg.student`をimportしないことをclean subprocessで確認。

## Python 3.11 互換性・実行時検証

```
PYTHONPATH=src ~/.pyenv/versions/3.11.11/bin/python3.11 -m compileall -q src/mage_ptcg/competition_intelligence
```
結果：成功（exit 0）。

さらに、fixture収集→`run_materialize_dataset(..., sources="replay", baseline=True)`を実際にPython 3.11.11で実行し、`adopted_row_count > 0`のdatasetを正常に生成できることを確認した（構文互換だけでなく実行時互換を確認）。

## セキュリティ・保護対象ファイル監査

- `git diff --check 3e198e8..HEAD`：本セッションの2 commitではクリーン（既存の`artifacts/o1-competition-intelligence-v1/*.patch`内のtrailing whitespace警告は前回セッションの既存commitに由来し、本セッションの変更ではない）。
- conflict marker・secret pattern・絶対ローカルパスのgrepスキャン：本セッションの変更ファイルでヒットなし。
- 保護対象ファイル17件（`main.py`、`deck.csv`、`agents/__init__.py`、`agents/rule_agent.py`、`agents/rule_agent_v1.py`、evaluation/promotion.py等）を`origin/feature/belief-guided-search`とSHA-256で比較し、全件一致を確認。
- `scripts/build_submission.py --output-dir`で実際に`submission.tar.gz`をビルドし、tarball内メンバーが`main.py`／`deck.csv`／`agents/__init__.py`／`agents/rule_agent.py`の4件のみであることを確認（O1関連リソースの混入なし）。`source_revision.commit`は本セッションの最終commitと一致、`dirty: false`。

## canonical統合・push結果

- canonical (`feature/belief-guided-search`) へfast-forward merge：`3e198e8..d5a4e02`（競合なし）。
- canonical上でのfocused test再実行：314 passed／1 failed。failした`test_check_o1_protected_files.py::TestCollectAgainstRealRepo::test_runs_data_root_is_absent_in_this_isolated_worktree`は本セッションで無変更のテストファイルであり、`git diff 3e198e8..d5a4e02 -- tests/test_check_o1_protected_files.py`は空。原因はcanonical worktree固有の`runs/`（2.2GB、git-ignored、本セッション以前から存在）の有無であり、本セッション由来の回帰ではない。canonical上のfull regressionは1383 passed／1 failed（同一原因）。
- `origin/feature/belief-guided-search`へpush：`c69aadc..d5a4e02`（fast-forward）。push後の`git rev-list --left-right --count HEAD...origin/feature/belief-guided-search`は`0	0`。
- push前後で`scripts/build_submission.py`によるsubmission.tar.gz再ビルドを実施し、tarballメンバーが`main.py`／`deck.csv`／`agents/__init__.py`／`agents/rule_agent.py`の4件のみであることを維持していることを確認。

## 未実施・既知の制約

- live Kaggle環境での再検証は未実施（この環境は`LOCAL_ONLY`のまま、既存の分類を変更しない）。
- Knowledge Claimの`training_eligible`/`runtime_eligible`をSUPPORTEDへ遷移させるCLIコマンドは未追加（既存の`transition_claim`はPython関数として直接呼び出す想定のまま。CLIのlifecycle transitionコマンドはO1-3の元設計時点でも未実装だった）。
- `sources=knowledge`のdataset shardは、claimのcontent_payloadをそのまま並べたJSONLであり、決定レベルの教師ラベルへの変換は行っていない（Replay行動を無条件教師化しない、という非交渉条件を維持するための意図的な範囲限定）。
