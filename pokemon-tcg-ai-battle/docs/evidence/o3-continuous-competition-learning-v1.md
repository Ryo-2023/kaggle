# O3 Continuous Competition Learning v1 Evidence

## 結果

開始正典は `origin/feature/belief-guided-search` の `f4575557fcebb345f5ea826e55531685aab6c758` である。作業 branch は `feature/o3-continuous-competition-learning-v1`、worktree は指定の `pokemon-tcg-ai-battle-o3-continuous-learning` を使用した。

O3 は typed Kaggle CLI transport、Replay participant resolver、rules attestation、resumable acquisition manifest、O2 Deck/Opponent Pool refresh、seat-matched unseeded promotion metadata、および phase-manifest control plane を追加した。Champion/default は Rule Agent v0 のままで、Kaggle submission と Champion 自動昇格は実行していない。

## Kaggle capability と governance

ローカルの Kaggle CLI 2.2.3 help で `submissions`、`leaderboard`、`files`、`episodes <submission_id>`、`replay <episode_id>`、`logs <episode_id> <agent_index>`、`team-submissions <team_id>` を確認した。旧 `competitions view` は削除した。third-party `public_logs` は `NOT_SUPPORTED_BY_PUBLIC_API` として fail-closed にする。

Replay は Team ID/name の exact single match のときだけ `OWN_KAGGLE`、一致なしは `PUBLIC_OTHER`、identity/schema/複数一致の不明さは quarantine とする。rules attestation は既定で `UNVERIFIED_RULES_CONSTRAINT` であり、`PUBLIC_OTHER` は archive-only、analysis/training/pool/profile には流さない。

## 実装した gate

- acquisition は shell を使わず argv、timeout、bounded output、secret scan、atomic archive、exit status を用いる。
- O2 は raw archive を読まず、actual Intelligence Snapshot 以降だけを入力とする。
- Deck refresh は exact 60-card multiset hash で重複排除し、PUBLIC_OTHER を active pool へ追加しない。
- Opponent refresh は Rule Agent v0 と Random Legal Agent を維持し、Student artifact が存在するときだけ Student を有効化する。
- evaluation metadata は `engine_seed_supported=false`、`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false` を固定し、100 logical pair 未満を `INSUFFICIENT_EVIDENCE` にする。
- fixture acquisition は `BLOCKED_FIXTURE_CONTAMINATION`、actual Snapshot 不在は `BLOCKED_MISSING_ACTUAL_SNAPSHOT`。このため fixture-to-training 混入は起きない。

## 検証

`PYTHONPATH=. pytest -q tests/test_o3_pools_evaluation.py tests/competition_intelligence/test_o3_transport_governance.py` は 14 passed。`tests/competition_intelligence/test_external_sources.py` を加えた focused test は 26 passed。`PYTHONPATH=src python -m mage_ptcg.continuous_learning.run --help` と live acquisition CLI の help は pass した。

baseline の既知 failure `test_run_command_safe_timeout_and_child_cleanup` は Python interpreter 起動前に 0.5 seconds timeout となる race を再現した。PID を確実に生成して cleanup を検証できる 2.0 seconds deadline にして 3 consecutive pass を確認した。

公式 CLI 2.2.3 による read-only live smoke は `/tmp/o3-live-smoke-20260720` へ出力した。`PUBLIC_OTHER` collection は rules gate により無効のまま、own submissions と leaderboard の応答は untrusted live schema baseline を作らない fail-closed policy により quarantine、Replay は 0 件だった。これは認証や schema を推測で信頼済みにせず、raw archive から学習へ流さないことを確認する smoke である。

## 制約と再開条件

この worktree では authenticated own Kaggle Replay、TEAM_SHARED Bundle、または materialized actual Intelligence Snapshot を確認できなかった。そのため live smoke の remote collection、actual-only dataset、new `NEURAL_ACTUAL_TRAINED` artifact、100 logical pair evaluation、Promotion Report の実測は未実行である。fixture で代用していない。

実データ運用を再開するには、認証済み環境で rules attestation を別途根拠付きで更新し、own data の read-only acquisition を実行し、actual Snapshot を materialize してから既存 O2/C4 phase に渡す。PUBLIC_OTHER の rules unverified/archive-only 制約、engine metadata、Rule Agent v0 Champion はそのまま維持する。

## 復旧監査と最終検証（2026-07-20）

会話履歴を参照せず、Git・worktree・成果物・実行結果だけから状態を再構成した。指定 worktree は存在し、復旧前 HEAD は `e5732897b5404c4550e1c3c7e79f9ccd46461cfa`、復旧前 dirty 状態は clean、feature origin との divergence は `0 0` だった。開始 canonical は `f4575557fcebb345f5ea826e55531685aab6c758` で、local canonical worktree の未追跡 `.codex/` はO3 worktree外の既存変更として保持した。

O3 commit 列は `9c325a8`、`a767bc3`、`37328a4`、`77bd7e6`、`f3faac2`、`e573289`（feature origin と同一）である。実装範囲は typed `SubprocessKaggleTransport`／`ExternalRequest`、participant resolver、rules attestation、再開可能な acquisition、O2 Deck/Opponent Pool refresh、seat-matched unseeded evaluation metadata、fixture/actual phase gate である。`competitions view` は実行経路に存在せず、`public_logs` は `NOT_SUPPORTED_BY_PUBLIC_API` で fail-closed する。

最新 own-only live smoke は `/tmp/o3-live-smoke-recovered-b0ILzL` に保存した。outcome は `ARCHIVED=0`、`QUARANTINED=2`（own submission listing 1、leaderboard 1）、`UNAVAILABLE=0`、Replay `0` 件、`PUBLIC_OTHER collection=false`／`RULES_UNVERIFIED_ARCHIVE_ONLY` だった。schema baseline は未信頼のため quarantine され、学習へ流れる raw archive はない。従って SourceKind 別の保存件数は `OWN_KAGGLE=0`、`PUBLIC_OTHER=0`、`QUARANTINED=2`（quarantine は SourceKind envelope を作らない）、AllowedUse別の保存件数は全て0である。

基準 Deck Pool は1件（`repository-default-v1`、60枚 multiset hash `db12ba5cf48ca4b39be3e6031f205b3ad40ee263a6f32d0250f49c21c8463dd5`）、Opponent Pool は4定義中 enabled 2件（Rule Agent v0、Random Legal）、Bounded Search／Student はartifact不在のため無効である。actual Intelligence Snapshot hash、dataset/model/package hash、dataset records/split、complete DeckObservation、Team Bundle、100 logical-pair evaluation、Promotion Reportの実測値は未 materialize（`N/A`）であり、fixtureをactualとして扱っていない。leakage auditは本runでは対象データ0件、artifact classは未生成である。

検証結果は、O3 focused `30 passed`、timeout cleanup test `1 passed`×3、privacy/secret関連 focused `15 passed`、docs validation `Validated 12 canonical documents`、full regression `1485 passed / 0 failed`（5 warnings）である。`git diff --check` はpass、conflict marker scanは文書中のpytest罫線を除外した実マーカー0件である。clean-room／package検証はactual artifact不在のため未実行（該当なし）。

不変条件は `Champion/default = Rule Agent v0`、`Promotion = NO_DECISION`、`PUBLIC_OTHER = ARCHIVE only`、`rules = UNVERIFIED_RULES_CONSTRAINT`、`engine_seed_supported = false`、`pairing_mode = seat_matched_unseeded`、`exact_paired_inference = false`、`Kaggle submission = not performed` である。
