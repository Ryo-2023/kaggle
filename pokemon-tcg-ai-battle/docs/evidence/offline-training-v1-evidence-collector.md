# Offline Training v1 機械的証拠収集ツール (Evidence Collector) ドキュメント

本ドキュメントは、宣言的な受入計画（acceptance plan）を実行し、allowlistされた固定runnerで検証コマンドを走らせ、出力を機械的に検証基盤化するツールの仕様と利用方法について説明する。

## 目的と非目的

本ツールの目的は、Offline Training v1の統合可否を判断するための「機械的証拠」を、READY/PASS/APPROVEのような総合判定を含めずに構造化JSONへ収集することである。収集された事実に基づく最終的な統合可否の判断は、人間または別レイヤーの監査エージェントが行う。

- 本ツールは検証対象をPASSへ変えるtoolではない。required checkが失敗すればoverall verdictはFAILになる。テストや本番ロジックを書き換えてFAILを消すことはできないし、しない。
- 本ツールが実行するのは「検証基盤」である。`configs/offline_training_v1/final_acceptance_plan.json` の19項目全部を実際にこのtoolingブランチ上で実行し尽くすことは、本ツールの完了条件ではない。
- Offline Training v1、C4、Gemini Supportを統合した実際の最終acceptance runは、正典ブランチへの統合後（`feature/belief-guided-search` 上）で別途実施する。

## アーキテクチャ

1. **宣言的plan**: `configs/offline_training_v1/final_acceptance_plan.json` が `schema_version: offline-training-v1-acceptance-plan-v1` に従い、各checkの `id` / `runner` / `required` / `timeout_seconds` / `args` / `expected_outputs` / `artifact_inputs` / `artifact_outputs` / `privacy_class` / `failure_severity` を宣言する。
2. **plan validation**: `validate_plan_schema()` がJSON構造・重複ID・未知runner・timeoutの正値性・`args`の型・`privacy_class`の許可値を検証する。`validate_required_check_coverage()` が19個の必須check IDが揃っているかを検証する。両方とも `--validate-only` / `--dry-run` / 通常実行のすべてで共通して呼ばれる。
3. **allowlisted runner**: 任意のshellコマンドは実行しない。`ALLOWED_RUNNERS` に列挙された固定runner ID（`git_diff_check`, `import_closure`, `pytest`, `collection_smoke`, `training_smoke`, `resume_smoke`, `export_parity`, `package_build`, `package_verify`, `clean_room`, `privacy_scan`, `secret_scan`, `absolute_path_scan`, `artifact_hash`, `fallback_invariant`, `champion_invariant`, `gemini_manifest_validation`）だけを解決する。planが未知のrunnerを参照した場合は`--validate-only`の時点で拒否する。
4. **固定引数リスト実行**: 外部プロセスを起動するrunnerは `subprocess.run()` / `Popen()` にargvリストを渡す。`shell=True` は使用しない。
5. **PASS/FAIL/SKIP集約**: 各checkの結果は `PASS` / `FAIL` / `SKIP` のいずれかに正規化される。required checkがFAIL・未実行・timeoutのいずれであってもoverall verdictはFAILになる。optional checkは、宣言済み `artifact_inputs` が存在しない場合に限り理由付きでSKIPされる（required checkはこの経路を通らない）。

## 実行方法

```bash
PYTHONPATH=.:src python3 \
  scripts/collect_offline_training_v1_evidence.py \
  --repository-root . \
  --plan configs/offline_training_v1/final_acceptance_plan.json \
  --output /path/to/evidence.json \
  --run-id <run-id>
```

### 引数

| 引数 | 必須 | 説明 |
|---|---|---|
| `--repository-root` | 任意（既定 `.`） | リポジトリルートへのパス |
| `--plan` | 必須 | 受入計画JSONへのパス |
| `--output` | 必須（`--validate-only`時は無視） | 証拠JSONの出力先パス |
| `--run-id` | 任意 | 明示的なrun ID（省略時は自動生成） |
| `--validate-only` | 任意 | plan検証のみ実施して終了する（下記参照） |
| `--dry-run` | 任意 | 実行予定を解決するが実際のrunnerは起動しない（下記参照） |

### `--validate-only`

次だけを検査して終了する。実際のrunnerは一切実行しない。出力ファイルも書き込まない。

- JSON構文
- planスキーマ（必須フィールド、重複ID、未知runner、不正なtimeout、不正な`privacy_class`、`args`の型）
- 必須check集合（19項目すべてが揃っているか）

### `--dry-run`

plan validationとrunner解決・引数検証・artifact依存関係検証・実行予定一覧生成までを行うが、`pytest`・training・package buildなど実際のrunnerは起動しない。各checkの `bounded_excerpt` には `[DRY_RUN]` プレフィックスが記録される。証拠JSONは書き込まれる。

## 判定規則

- required checkがFAIL、未実行、またはtimeoutならoverall verdictはFAILになる。
- optional checkのSKIPには理由（`skip_reason`）が必須である。
- runnerからの結果が不整合・空・未知の形状であっても、collectorはクラッシュせずFAILとして扱う（`unknown resultはFAIL`）。
- collector自身が例外を送出した場合もFAILとして記録し、それまでに集めた事実は保存する。
- artifact hashが期待値と不一致ならFAILになる。
- Champion（`main.py` の既定agentが `make_rule_agent` = Rule Agent v0）でなければFAILになる。
- Promotion状態が `NO_DECISION` でなければFAILになる。ただし本リポジトリには機械可読なpromotion状態ファイルが存在しないため、この invariant は現状「反証がない限りNO_DECISION」という限定的なチェックである（要検証: 将来promotion状態ファイルが追加された場合はこのinvariantを強化する）。
- Gemini support integration manifestのHOLDモジュールが `src/mage_ptcg/offline_training/` からimportされていればFAILになる。

## Redaction（秘密情報・絶対path・個人情報の扱い）

証拠JSONは公開・共有され得るものとして扱う。次を出力へ残さない。

- `/home/...`・`/tmp/...`・`/Users/...` 形式の絶対path → `redact_text()` が `[REDACTED_PATH]` へ、リポジトリルート配下は `[REDACTED_REPO_ROOT]` へ置換する。この置換は各runnerのstdout/stderrだけでなく、`repository_root_resolved` / `collector.python_executable` / `collector.script_path` のようなメタデータフィールドにも適用する。
- secretらしい値（`api_key` / `token` / `password` / `credential` 等に後続する12文字以上の英数字トークン）→ `[REDACTED_SECRET]` へ置換する。
- stdout/stderrは全文を保存しない。各々SHA-256ダイジェストを記録し、`bounded_excerpt` は最大100行（先頭・末尾のみ）に切り詰める。

### 自己検証runner（`privacy_scan` / `secret_scan` / `absolute_path_scan`）

これら3つのrunnerは、外部スクリプトを呼び出すsubprocessではない。これまでに組み立てられた証拠ドキュメント（`results`）自体をJSON文字列化し、`RE_ABSOLUTE_PATH` / `RE_SECRET` / ローカルOSユーザー名の残存を走査する、collector自身のredactionパイプラインに対する回帰チェックである。マッチした生の値（実際の絶対pathや秘密情報の値そのもの）は結果へ含めず、件数のみを記録する。これはリポジトリ全体や生成物tarballを対象にした汎用セキュリティスキャナではないという限定されたスコープの自己検証であり、その旨を明示する。

## Trust boundary（信頼境界）

- cabtの合法手判定・実データパイプラインの成功可否は、本tool自身が判定するものではなく、対象runner（`pytest`、`scripts/run_offline_training_v1.py` 等）の実行結果を「事実」としてそのまま記録するに留まる。
- Champion / Promotionの状態を本toolが変更することはない。読み取り専用のinvariantチェックのみを行う。
- Gemini support integration manifest（`origin/feature/offline-training-v1-gemini-support` の `configs/offline_training_v1/gemini_support_integration_manifest.json`）は `git show <commit>:<path>` による read-only 参照のみで取得する。当該branchのcheckout・merge・変更は行わない。実際のtop-level schemaは `schema_version: gemini-support-integration-manifest-v1` で、featureの配列は `features` キー配下にある（`modules` キーではない。過去の実装齟齬を避けるため、`parse_gemini_manifest()` は `features` キーとschema_versionの両方を検証し、`modules` キーは受理しない）。

## Public / Private evidence

現状のCLIは単一の `--output` JSONファイルのみを生成する。このファイルは上記のredactionを経ているため、公開・レビュー共有を前提とした「public evidence」として扱ってよい。private dataset内容・checkpoint内容・model weight・raw private traceそのものを証拠JSONへ埋め込む処理はどこにも存在しない（そのようなrunnerを追加する場合は、このtrust boundaryを明示的に見直すこと）。

## 生成物の扱い

- `--output` で生成されるJSON、`--dry-run` の出力、`--validate-only` の（存在しない）出力はいずれもGit管理しない。`.gitignore` の対象外であっても、意図的にcommitしない。
- Champion/defaultやPromotion状態を本toolが書き換えることはない。

## Evidence JSONスキーマ（概要）

```json
{
  "schema_version": "offline-training-v1-evidence-v1",
  "run_id": "run_...",
  "started_at_utc": "...",
  "finished_at_utc": "...",
  "repository_root_resolved": "[REDACTED_REPO_ROOT]",
  "collector": {
    "script_path": "scripts/collect_offline_training_v1_evidence.py",
    "sha256": "...",
    "python_executable": "...(redacted if under /home,/tmp,/Users)...",
    "python_version": "...",
    "dependency_fingerprint": "...",
    "gpu_cuda_info": {"cuda_available": false, "device_count": 0, "devices": []}
  },
  "git": {"branch": "...", "commit_sha": "...", "tree_hash": "...", "dirty": false},
  "checks": [
    {
      "id": "champion_invariant",
      "runner": "champion_invariant",
      "required": true,
      "status": "PASS",
      "skip_reason": null,
      "started_at_utc": "...",
      "finished_at_utc": "...",
      "duration_seconds": 0.01,
      "exit_code": 0,
      "timed_out": false,
      "stdout_sha256": "...",
      "stderr_sha256": "...",
      "bounded_excerpt": "...",
      "produced_artifacts": []
    }
  ],
  "artifacts": [
    {
      "logical_name": "...",
      "portable_relative_path": "...",
      "size_bytes": 0,
      "sha256": "...",
      "artifact_type": "source_code",
      "privacy_class": "public",
      "package_inclusion": true,
      "producer_check_id": "...",
      "provenance_commit": "..."
    }
  ],
  "summary": {
    "required_checks_count": 19,
    "passed_checks_count": 0,
    "failed_checks_count": 0,
    "skipped_checks_count": 0,
    "blocking_failures": [],
    "warnings": [],
    "overall_verdict": "FAIL",
    "package_candidate_eligibility": false,
    "champion_agent": "unknown",
    "promotion_status": "unknown"
  },
  "gemini_support": {
    "source_commit": "7abde0bcbebe8bf5149303fa917f320ee7947129",
    "manifest_sha256": "...",
    "schema_validation_ok": true,
    "module_counts": {"P0": 0, "P1": 0, "HOLD": 0},
    "p0_acceptance_tests": [],
    "hold_modules_quarantined": true,
    "hold_quarantine_violations": []
  },
  "interrupted": false
}
```

## 実行制御・堅牢性

- **アトミック書き込み**: 出力先と同じディレクトリ内に一時ファイルを生成し、`os.replace()` でアトミックにリネームする。
- **SIGINT (Ctrl+C) と部分保存**: 実行中の割り込みは捕捉され、それまでに集めた事実を含む中途の報告書をアトミックに保存したうえで終了ステータス `130` で終了する。中断後に同じcollectorを再実行しても、永続化された壊れた状態は残らない。
- **timeout時のプロセス終了**: `run_command_safe()` は新しいprocess groupで子processを起動し、timeout時は `SIGTERM` を送り、猶予後も終了しなければ `SIGKILL` を送る。ゾンビプロセスを残さない。

## 既知の制約 (TODO: 要検証)

- Promotion invariantは、機械可読なpromotion状態ファイルが存在しないため「反証がなければNO_DECISION」という限定的なチェックに留まる。
- `privacy_scan` / `secret_scan` / `absolute_path_scan` は、そのrun内で組み立てられた証拠ドキュメントの自己検証であり、リポジトリ全体やパッケージtarballの内容物に対する汎用スキャンではない。
