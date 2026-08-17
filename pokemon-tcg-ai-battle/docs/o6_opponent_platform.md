# O6 Opponent Intelligence Platform v1

## 結論

`mage_ptcg.opponents` は、既存 O5 の canonical JSON・deck identity・atomic I/O を再利用し、remote team branch を checkout も import もせず commit SHA 固定で収集する facade である。未署名の source は `BLOCKED_PERMISSION` に留め、実行・学習・公開・提出へ流さない。

## Quick Start

```bash
PYTHONPATH=src python3 -m mage_ptcg.opponents sync-team-branches \
  --repo . --output-dir /tmp/o6-opponents --json
PYTHONPATH=src python3 -m mage_ptcg.opponents build-registry \
  --output-dir /tmp/o6-opponents --population team-meta-YYYYMMDD-v1 --json
PYTHONPATH=src python3 -m mage_ptcg.opponents validate \
  --output-dir /tmp/o6-opponents --json
```

人間の source review が終わった後だけ、reviewer は build manifest を `APPROVED` に更新し、次を実行する。

```bash
PYTHONPATH=src python3 -m mage_ptcg.opponents publish \
  --output-dir /tmp/o6-opponents --artifact-store /tmp/o6-store \
  --population team-meta-YYYYMMDD-v1 --approve --json
PYTHONPATH=src python3 -m mage_ptcg.opponents list-populations --artifact-store /tmp/o6-store --json
PYTHONPATH=src python3 -m mage_ptcg.opponents fetch team-meta-YYYYMMDD-v1 --artifact-store /tmp/o6-store --json
PYTHONPATH=src python3 -m mage_ptcg.opponents list --population team-meta-YYYYMMDD-v1 --artifact-store /tmp/o6-store --json
```

`smoke` と `evaluate` は approved runtime spec 以外を fail-closed で拒否する。candidate は必ず `--candidate-entrypoint module:function` を明示する。

## Source contribution and permissions

推奨する branch root の `opponent-source.yaml` は `agent.entrypoint`、`deck.path`、ruleset、permission scope を明記する。namespace policy の既定は `evaluation`、`strategy_analysis`、`team_redistribution` を review 後に許可し、`training_data_generation` は追加 review、`public_redistribution` と `submission_bundle` は禁止する。

Collector は `origin/agents/*` を `git for-each-ref` で列挙し、`git ls-tree`／`git show` だけで inventory を作る。source path traversal、symlink、submodule、巨大 file、binary は警告または quarantine とし、remote code を自動 import しない。危険 capability scan は review 補助であり安全性の証明ではない。

## Snapshot and runtime limitations

Population hash は member order と表示時刻に依存しない。`LocalArtifactStore` は staging に payload を書き、bundle hash を作成し、manifest を最後に atomic publish する。同一 ID の異内容は拒否し、同一内容は idempotent である。古い snapshot は変更しない。

Native adapter は明示 approval 後だけ、一回ごとの subprocess・snapshot root cwd・最小 env・timeout・stdio 上限で entrypoint を呼ぶ。OS network namespace は作成しないため、結果へ必ず `NETWORK_ISOLATION_UNAVAILABLE` を記録する。dependency 解決、cabt deck submission、legal-action smoke、state leakage、determinism、実 league は approved source と official cabt runtime が揃うまで未実施である。

Runtime bundle は `mage_ptcg.opponents.runtime_closure.build_runtime_closure()` による allow-list closure（entrypoint の静的 import graph + 実行時 file-open/`ctypes.dlopen` trace + 宣言済み deck artifact、host platform に一致する native binary のみ）で構成する。docs/tests/report/experiments/data、cache、他 OS 向け binary は closure builder が拒否し、`runtime/<agent_id>/closure_report.json` に required/optional/excluded/blocked/unresolved を記録する。実測結果と `population_identity_hash` が runtime bundle 自体のバイトへ依存する設計（`runtime_bundle_registry_hash`）は [o6-opponent-intelligence-v1.md](evidence/o6-opponent-intelligence-v1.md) の Phase A 節を参照。

## Public evidence

`PublicGitRepositoryCollector`、`PublicDeckEvidenceCollector`、`PublicStrategyDocumentCollector`、`LeaderboardSnapshotCollector` は transport 注入前の offline contract である。`LocalPublicEvidenceInboxCollector` は JSON inbox を `EXACT`、`DECK_FAITHFUL`、`BEHAVIORAL_SURROGATE`、`OBSERVED_ONLY` として明示分類し、partial deck を exact に昇格しない。live network sync は規約・credential・transport の明示確認後にだけ追加する。

## Public Opponent Source Corpus 統合（Phase B）

`mage_ptcg.opponents.public_source`（`public-source` サブコマンド群）は、外部 Public Source Corpus（Repository Snapshot 経路）の分類 JSON のみを import する、metadata-only な独立モジュールである。Public Agent の raw／extracted コードは読み込まず、実行・CABT smoke・Team pipeline（`build_population()`／`OpponentRegistry`）への接続も行わない。Candidate state は `NATIVE_OPPONENT_CANDIDATE`／`DECK_STANDARD_PILOT_CANDIDATE`／`SURROGATE_CANDIDATE`／`REVIEW_REQUIRED`／`BLOCKED` の5値にキャップされ、`source_id` を参照しない rule-based 判定で導出する。詳細は [runbooks/o6-public-source-intake.md](runbooks/o6-public-source-intake.md) と [evidence/o6-opponent-intelligence-v1.md](evidence/o6-opponent-intelligence-v1.md) の Phase B 節を参照。
