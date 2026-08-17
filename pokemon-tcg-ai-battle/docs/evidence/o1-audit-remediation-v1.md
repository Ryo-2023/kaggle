---
project: MAGE-PTCG
evidence: o1-audit-remediation-v1
as_of: 2026-07-19
scope: fix/o1-audit-remediation-v1 (integration/o1-competition-intelligence-v1 worktree)
commits: e05e42a, 51040db, bb19ee4, 8db1ab2, 53d3e8a, 18fb9da
---

# O1 Competition Intelligence 独立監査Remediation Evidence

## 前提

開始時点で`fix/o1-audit-remediation-v1`は`origin/feature/belief-guided-search`（`739bede`）と同一HEADでcleanだった。独立監査で指摘された5件を、修正前に再現test/スクリプトで実際に再現したうえで分類・修正した。

## 指摘ごとの分類・再現・修正

### 1. composite splitによる同一opponentのsplit間分散 — accepted（コア部分）／一部reject

**再現**：同一opponent・異なるdeckの2episodeを`split_by_composite_group`へ渡すと、旧実装（全dimensionのANDによるcomposite key）は別groupへ分割し、別splitへ配置され得た（`/tmp/repro_finding_a.py`で実証、修正前は`AssertionError: BUG REPRODUCED`）。

**修正**：`group_split.py`をUnion-Find（Disjoint Set）による連結成分方式へ再設計。`leakage_audit.py`が既にhard gateしている`opponent_identity`（`EpisodeRecord.agent_b`）のみをconnectivity dimensionとし、同一opponentを共有するepisodeは推移的に同一componentへ強制する。component単位でtrain/validation/testへ決定的に割当て、分割不能な場合は`GroupSplitError`へ`component_count`／`component_sizes`／`largest_component_id`を構造化して保持し、row random splitへのfallbackは行わない。

**一部reject**：独立監査はsource lineageも「既存仕様上hard invariant」として守るよう求めたが、実装を確認した結果、`leakage_audit._HARD_INVARIANT_FIELDS`には含まれておらず（episode／opponent／temporal／future-claim／duplicateのみ）、`source_leakage_count`は既存設計でreport-onlyだった（deck-fingerprintと同じ理由：単一source per ingestion runが一般的な本アーキテクチャでは、source一致を無条件leakageとして扱うと通常利用のsplitが構造的に不可能になる）。この部分は不採用とし、`group_split.py`のdocstringに理由を明記した。deck fingerprint・agent/model versionも同様にconnectivityへ含めていない（既存設計を維持）。

### 2. Knowledge Claim import batch内duplicate — accepted

**再現**：同一claim_id・異なるcontentの2件を同一batchで`import_claims`へ渡すと、旧実装はエラーなく両方appendし、`latest_claims()`が最後の1件を黙って採用していた（`/tmp/repro_finding_b.py`で実証）。

**修正**：`import_claims`をbatch全体のpreflight検証へ変更。batch内・既存registryとの両方でcontent_hashを比較し、同一content（idempotent no-op）と異なるcontent（fail-closedでKnowledgeRegistryError、1件もappendしない）を区別する。書き込みは`_append_claims_atomically`（既存ログbytes＋新規行を単一`atomic_write_bytes`で置換）へ変更し、batch途中の部分appendを構造的に排除した。

### 3. Replay decisionの無条件教師化 — accepted

**再現**：`export_selected_rows`／`dataset_materialization.materialize_dataset`の学習用exportは、`high_info_selector.py`のHigh-Information Selectionを一切参照せず、permittedなselected episodeの全decisionをfallback以外無条件でexportしていた。

**修正**：`decision_eligibility.py`を新規追加し、`ANALYSIS_ALL_PERMITTED`（既存の分析用挙動）と3種のtraining policy（`TRAINING_HIGH_INFORMATION`／`TRAINING_VERIFIED`／`TRAINING_HIGH_INFORMATION_VERIFIED`、既定は最も厳格な`TRAINING_HIGH_INFORMATION_VERIFIED`）を区別する。verification basisにはRule v0自身の`teacher_ranking`最上位と実行行動の一致（`teacher_agreement`）という実データ由来のsignalを採用し、fabricationは行っていない。`offline_adapter.export_selected_decision_rows`で`(episode_id, decision_index)`単位のexportを実装し、`dataset_materialization.py`/`pipeline.run_materialize_dataset`/CLI `materialize-dataset --training-policy`で選択可能にした。

### 4. ingest時刻によるSourceEnvelope identityの非決定性 — accepted

**再現**：`local_ingest.ingest_local_file`・`team_bundle.import_team_bundle`は`acquired_at`／`created_at`が未指定の場合`time.gmtime()`へ黙ってfallbackしており、`SourceEnvelope.content_hash()`にこの値が含まれるため、同一bytesを別時刻に再ingestすると異なるidentityになっていた。

**修正**：`provenance.require_declared_time()`を追加し、未指定ならfail-closedで即座に例外とする。`ingest_local_file`の戻り値へ運用メタデータの`ingested_at`（content_hashに一切寄与しない）を追加。CLI `ingest-local --acquired-at`（必須）・`ingest-team --created-at`（必須）を追加した。

**既知の未対応範囲**：`external_acquisition.py`（Kaggle/public live ingestion経路）にも同型の`acquired_at=_timestamp()`無条件呼び出しが存在する。この経路はCLI引数も無く、本環境は`LOCAL_ONLY`でlive経路が未検証・未到達（`docs/evidence/competition-probe-v0.md`等で既に記録済みの制約と同じ）であるため、本remediationでは対象外とした。次にlive Kaggle capabilityが実証された場合は、同じ`require_declared_time`を`acquire_external_artifact`へ適用し、`run_ingest_kaggle`/`run_ingest_public`とCLIへ`--acquired-at`を追加することを次のtodoとして記録する。

### 5. `rule-bc-v1`の`source_id`名称不整合 — accepted（最小対応）

**再現・確認**：raw行自身の`"source_id"`キーはepisode identityであり、`contracts.SourceEnvelope.source_id`（`normalize_rule_bc_jsonl`の関数引数）とは無関係。実装（`replay_normalize.py`・`offline_adapter.py`）は元々この2つを混同していなかったが、名称の衝突自体は文書化されていなかった。

**修正（最小対応）**：新schema`rule-bc-v2`（`episode_id`/`source_envelope_id`/`source_lineage_id`/`replay_id`/`match_id`の明示分離）への移行は既存consumerへの影響が大きく本remediationの範囲を超えるため見送った。代わりに、内部変数を`row_episode_id`へ改名し、`replay_normalize.py`・`offline_reader.py`のdocstring／コメントへ`KNOWN SCHEMA NAMING COLLISION`を明記し、raw行の`source_id`集合が正規化後の`episode_id`集合と一致することを固定するtestを追加した。

## Phase 3: audit report拡張

- `snapshot_builder.build_snapshot`が常にhard-identity componentの診断情報（`component_count`／`component_sizes`／`largest_component_id`／`hard_connectivity_dimensions`／`splittable`／`unsplittable_reason`）を計算し、`pipeline.run_build_snapshot`が`component_diagnostics.json`として永続化する。
- `knowledge_registry.import_claims`の戻り値を`ImportClaimsResult`（`appended_claim_ids`／`duplicate_skipped_claim_ids`）へ変更し、`pipeline.run_import_knowledge`が実際の新規import件数とduplicate件数を分離して報告する。
- Finding C（decision_eligibility）実装により、dataset `audit_report.json`へ`decision_selection`（`training_policy`／`analysis_decision_count`／`training_eligible_decision_count`／`low_information_excluded_count`／`fallback_excluded_count`／`unverified_excluded_count`／`permission_excluded_decision_count`）を追加。`eligibility_manifest.json`にdecision別の`training_eligibility_reasons`／`verification_basis`／`verification_provenance`を保持。
- leakage_audit.jsonは既存どおりopponent／episode／temporal／duplicate／future-claimのhard判定と、deck-fingerprint／source／model-submissionのreport-only件数を両方含む（区分は`leakage_audit.py`のdocstringおよび本文書「1.」の記載を正とする）。

## テスト

新規focused test 58件（内訳：group_split関連7件、knowledge_registry関連7件〈batch atomicity 6件＋duplicate report 1件〉、decision_eligibility単体19件、dataset_materialization統合6件、source_time_determinism 10件、offline_reader/normalize 1件、snapshot component_diagnostics 2件、CLI e2e拡張・test_cli.py等の既存修正を除く純増分）。

```
PYTHONPATH=src /usr/bin/python3 -m pytest tests/competition_intelligence \
  tests/test_check_o1_protected_files.py \
  tests/test_competition_intelligence_runtime_isolation.py \
  tests/test_competition_intelligence_cli_end_to_end.py -q
```
結果：364 passed（開始時315 passedから+49、Finding A-Eの新規test合計）。

```
PYTHONPATH=src /usr/bin/python3 -m pytest -q
```
結果：1433 passed, 5 warnings（既知の環境依存warningのみ、failureなし）。

## セキュリティ・privacy・package監査

- `git diff --check 739bede..HEAD`：クリーン。
- conflict marker・secret pattern・絶対ローカルパスのgrepスキャン：本セッションの変更ファイルでヒットしたのは全てtest fixture内の意図的なダミー値（`sk-abcdefghijklmnop1234`等の既存secret-scan検証用文字列、`redact_value()`検証用の架空パス）のみで、実際の秘密情報・実パスの漏洩はなし。
- 保護対象ファイル17件（`main.py`、`deck.csv`、`agents/*`等）を`origin/feature/belief-guided-search`とSHA-256で比較し、全件一致。
- `scripts/build_submission.py --output-dir`で実際に`submission.tar.gz`を再ビルドし、tarball内メンバーが`main.py`／`deck.csv`／`agents/__init__.py`／`agents/rule_agent.py`の4件のみであることを確認。`source_revision.commit`は本セッション最終commitと一致、`dirty: false`。
- `tests/test_competition_intelligence_runtime_isolation.py`（4件）が引き続きPASSし、`main.py`が`competition_intelligence`をimportしないことを確認。

## Python 3.11 / cwd非依存 / 決定性の実行時検証

`~/.pyenv/versions/3.11.11/bin/python3.11`で以下を実施（cwdを`/tmp`配下の無関係なディレクトリへ変更したうえで実行）。

1. `compileall -q src/mage_ptcg/competition_intelligence` → 成功。
2. `scripts/run_competition_intelligence.py doctor` → `ok: true`。
3. 実fixtureで ingest → normalize → opponent多様化 → `build-snapshot`を同一パラメータで2回実行 → `snapshot_id`完全一致（`intelligence-snapshot-34883d412663afc21890`）。
4. 同一snapshotから`created_at`だけ変えて`materialize-dataset`を2回実行 → `dataset_hash`完全一致（`6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d`）。
5. `component_diagnostics.splittable == True`（opponent多様化後の実データで連結成分3件以上）を確認。

## Clean copyでのfull regression

`git archive`は`.git`を含まないため、`current_git_commit()`等のgit依存コードが「not a git repository」で失敗し19件が見かけ上failした（`test_check_o1_protected_files.py`・`test_submission_artifact.py`・`test_actual_agent_viability.py`等、いずれも本セッション無変更のファイル）。これはclean-copy手法自体の制約であり、本セッションの変更に起因する回帰ではないことを、`.git`を保持する`git clone --no-hardlinks --branch fix/o1-audit-remediation-v1`による代替cleanコピーで確認した：同コピー（`runs/`等のgit-ignoredデータを含まない）でfull regressionを実行した結果、**1433 passed, 0 failed**（worktree本体での実行結果と完全一致）。

## 未完了事項

- `external_acquisition.py`のingestion時刻非決定性は未修正（既知制約として上記「4.」に記録、live Kaggle capability実証後に対応）。
- Knowledge Claimのlifecycle transition（`transition_claim`でのtraining_eligible付与）はCLI未対応のまま（前回セッションからの既存制約、O1-3設計時点でも未実装）。
- `rule-bc-v2`への完全移行（`episode_id`/`source_envelope_id`/`source_lineage_id`/`replay_id`/`match_id`の分離）は見送り。

## canonical統合・push結果

- canonical (`feature/belief-guided-search`) へ`--no-ff` merge：merge commit `34b6db6`（競合なし）。
- canonical上でのfocused test再実行：363 passed／1 failed。canonical上のfull regression：1432 passed／1 failed。failした`test_check_o1_protected_files.py::TestCollectAgainstRealRepo::test_runs_data_root_is_absent_in_this_isolated_worktree`は本セッション無変更のテストファイルであり、canonical worktree固有の`runs/`（2.2GB、git-ignored、本セッション以前から存在）に起因する既知の環境依存差分（前回O1統合セッションと同一原因）。
- `origin/feature/belief-guided-search`へpush：`739bede..34b6db6`（fast-forward）。push後の`git rev-list --left-right --count HEAD...origin/feature/belief-guided-search`は`0	0`。
- push前後で`scripts/build_submission.py`によるsubmission.tar.gz再ビルドを実施し、tarballメンバーが`main.py`／`deck.csv`／`agents/__init__.py`／`agents/rule_agent.py`の4件のみであることを維持していることを確認。

## 現在の競技判断（不変）

- Champion／submission default：Rule Agent v0
- Neural Student v1：submission ref `54800005`、status `COMPLETE`、public score `600.0`でfreeze、性能Promotionは見送り
- 次のablation計画：`materialize-dataset --training-policy`を変えた比較（`ANALYSIS_ALL_PERMITTED` vs `TRAINING_HIGH_INFORMATION_VERIFIED`）による実データでのdataset品質差の定量評価は、O1 Snapshot dataset監査完了後（本remediation完了後）に着手する。
