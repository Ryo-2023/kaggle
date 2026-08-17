---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-07-18
---

# Competition Intelligence Sidecar（O1）Slice 0–2 実装 Evidence

## 結果

O1 Competition Intelligence Framework（`docs/plan/design/04_kaggle_competition_intelligence_and_joint_optimization_plan.md`§20、`docs/plan/implementation/`同§23）のうち、O1-0（Guardrails）とO1-1（Foundation）の一部（Slice 0–2）を実装した。O1-2（Replay正規化）以降のO1-6までは本セッションでは未実装であり、継続計画としてのみ記録する。Champion／submission default（Rule Agent v0）、Promotion（`NO_DECISION`）、canonical branch、Promotion Gate関連artifactは変更していない。

## Baseと作業場所

| 項目 | 値 |
|---|---|
| canonical repository | `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle`（branch `feature/belief-guided-search`、HEAD `3ef1ba39794180cdc36162a0b0347d3ffbcc6239`。本セッションでは未変更） |
| 作業worktree | `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o1-intelligence`（既存の検証済みdetached worktree、本セッション開始前から存在） |
| worktree HEAD | `6782e687a6bb667c3ca5343df9974352ddd7cd2c`（detached、本Slice作業前後で不変。commit後にNEW_HEADへ更新） |
| 参照した長期実行Evidence | `docs/evidence/offline-training-v1-long-run-20260718.md`／`.json`（model_hash `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`、package SHA `d4e2cdcb4557b4bbb9968266a0990525a7e172b9a9e664b477a21f957892e67d`と一致確認済み。両hashを含むデータ実体`runs/`はこのworktreeには存在せず、canonical workspace側のgit管理外データ） |
| Promotion Gate同時実行の確認 | 作業開始時に`ps aux`でtraining/promotion関連processを確認したが実行中プロセスは検出されなかった。GPU、cabt大量対戦、96,530 decisions全件解析、pytest-xdist、複数プロセス並列は本セッションで使用していない |

## 実装範囲

新規パッケージ`src/mage_ptcg/competition_intelligence/`（`canonical.py`、`atomic_io.py`、`contracts.py`、`permissions.py`、`provenance.py`、`archive.py`、`runstate.py`、`catalog.py`、`config.py`、`local_ingest.py`、`cli.py`、`__init__.py`）と、`scripts/run_competition_intelligence.py`、`scripts/check_o1_protected_files.py`を追加した。既存責務の再利用方針、モジュール対応表、未実装範囲の詳細は[実装計画書§23](../plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md#23-competition-intelligence-sidecar-実装o1-2026-07-18)を正とする（本Evidenceでは複製しない）。

主な設計上の決定：

- `IntelligenceSnapshot`は内容ハッシュ自己検証（`build_intelligence_snapshot()`だけが構築経路）とし、同一入力から常に同一`snapshot_sha256`／`snapshot_id`を得る（決定性は`tests/competition_intelligence/test_contracts.py::TestIntelligenceSnapshot`で検証）
- `KnowledgeClaim`のlifecycleは`validate_claim_transition()`が非合法遷移（`RAW→SUPPORTED`直行、terminal状態の再オープン等）を拒否し、`SUPPORTED`遷移には`E3_CONTROLLED_LOCAL_EVIDENCE`以上を要求する
- SourceEnvelopeのpermissionはdefault deny（`PUBLIC_OTHER`は`ARCHIVE`のみ、`TEAM_SHARED`は空集合）とし、`intersect_allowed_uses()`で複数sourceを混ぜた場合に最も狭い許可へ収束させる
- raw archiveはcontent-addressed（`raw/sha256/<prefix>/<hash>`）＋fsync-durable atomic writeとし、既存`mage_ptcg.competition.redaction.secret_scan`で事前scanしてunsafeな内容はquarantineへ隔離する
- SQLite catalogは`source_manifests/*.json`から常に再構築可能な非正本キャッシュとした
- Config（`CompetitionIntelligenceConfig`）は`auto_promote`／`auto_submit`／`public_other_training_enabled`を`__post_init__`で強制拒否する
- CLI（`scripts/run_competition_intelligence.py`）は実装済みの`doctor`／`ingest-local`／`rebuild-catalog`のみを登録し、未実装コマンドをstubとして追加していない

## テスト

| 検証 | コマンド | 結果 |
|---|---|---|
| 新規focused tests | `env -u PYTHONPATH python3 -m pytest tests/competition_intelligence/ tests/test_competition_intelligence_runtime_isolation.py tests/test_check_o1_protected_files.py -q` | 123 passed（内訳：`tests/competition_intelligence/` 111、runtime isolation 4、protected files checker 8） |
| repository全体tests | `nice -n 10 env -u PYTHONPATH python3 -m pytest tests/ -q` | 1145 passed／**1 failed**（下記参照）、130.63秒、single process |
| byte-compile | `env -u PYTHONPATH python3 -m compileall -q src/ scripts/ main.py agents/` | pass |
| `git diff --check` | — | pass（差分なし報告） |
| protected files before/after | `scripts/check_o1_protected_files.py baseline` → `verify` | 下記参照 |

### 既知の1件failについて

`tests/test_actual_agent_viability.py::test_viability_runner_redacts_raw_runner_values_and_resumes`が失敗する。原因は、fixtureが埋め込むcard ID `700`という数字列が、同テスト実行で生成される`artifact_hash`（run固有のsha256 hex文字列）に偶然部分文字列として出現し、「`700`が出力に含まれない」という素朴なsubstringアサーションが誤って失敗すること（`git diff`で本セッションが変更したファイルにこのテスト・関連実装は含まれない）。`docs/status/current_status.md`が既に記録する「既存の環境依存flaky test、無変更worktreeでも同一再現を確認済みで本pilotとは無関係」という既知issueと一致するパターンであり、本Sliceの変更による regressionではない。

### `env -u PYTHONPATH`について

このリポジトリの`.venv`はcanonical workspaceへのsymlinkで、ROSインストールに由来する`PYTHONPATH`環境変数がpytest実行を汚染することが`docs/evidence/cabt-capability-recovery.md`で既に文書化されている。本セッションでも同じ理由で`env -u PYTHONPATH`を使用した（新規の回避策ではなく、既存文書化済みの実行手順を踏襲した）。

## Runtime isolation

`tests/test_competition_intelligence_runtime_isolation.py`は、`PYTHONPATH`を除いたclean subprocessで次を検証する。

- 素の`import main`後、`sys.modules`に`mage_ptcg.competition_intelligence`、`mage_ptcg.dataops`、`sqlite3`、`pandas`、`sklearn`のいずれも現れない
- `main.py`のソースコードに`competition_intelligence`という文字列参照が存在しない
- `main.make_rule_agent(deck=...)`（既定Rule Agent v0経路）と`main.make_student_agent(deck=...)`（Student未接続時のRule v0 fallback経路）を実際に構築・1回実行した後も同様に到達禁止moduleが現れない

## 自己レビュー

実装コードに対して観点別の自己レビューを行い、2件の実質的な問題を修正した（見つけただけで放置していない）。

| # | 観点 | 検出内容 | 修正 |
|---|---|---|---|
| 1 | Privacy／正本のabsolute path | `local_ingest.ingest_local_file`が`origin_reference`へ`str(source)`（ingestするマシンの絶対path、homeディレクトリ名を含み得る）をそのまま格納し、正本SourceEnvelope manifestへ保存していた | 既存`mage_ptcg.competition.redaction.redact_value`（C2b probeが既に使うhome path redaction）を再利用し、格納前にredactするよう修正。`tests/competition_intelligence/test_privacy.py`で配線を回帰確認 |
| 2 | Permission／default deny | `PUBLIC_OTHER`向けSourceEnvelopeでも、呼び出し側が`allowed_uses`へ`TRAINING`／`REDISTRIBUTION`を明示指定すれば通ってしまい、設計書§16.4の「`PUBLIC_OTHER`は`TRAINING`／`REDISTRIBUTION`を常にfalse」という無条件denyの意図を満たしていなかった | `SourceEnvelope.__post_init__`に`PUBLIC_OTHER`＋`TRAINING`／`REDISTRIBUTION`の組を無条件拒否する検証を追加（`ARCHIVE`／`ANALYSIS`／`REPORTING`は引き続きmanifest次第で許可）。4件のテストを追加 |

その他の観点（architecture／schema／crash・determinism／runtime・regression）は、既存責務の再利用（重複package化なし）、`runtime→sidecar`到達不可の実測、atomic write／content-addressed dedup／stale lock recovery／catalog再構築の実測、repository regression実測で確認済みであり、追加の修正事項は見つからなかった。data leakage観点（episode/opponent/temporal split）は、Snapshot builder自体が未実装（O1-4は継続計画）のため、本Sliceでは対象コードが存在せず評価対象外である。

## Protected files before/after

```bash
python scripts/check_o1_protected_files.py baseline --output artifacts/o1-competition-intelligence-v1/protected-files-before.json
# ... 実装作業 ...
python scripts/check_o1_protected_files.py verify --baseline artifacts/o1-competition-intelligence-v1/protected-files-before.json
```

対象は`main.py`、`deck.csv`、`agents/`（Rule Agent v0/v1）、`src/mage_ptcg/evaluation/promotion.py`、`src/mage_ptcg/offline_training_v1_support/{promotion,statistics}.py`、Kaggle packaging/verification scripts一式、`configs/offline_training_v1/{production,final_acceptance_plan}.json`（git blob SHAで比較）と、`runs/`／`submissions/`／`data/`（git-ignoredデータルート、存在確認のみ）。git blob SHAはこのworktreeに存在するファイルの内容そのものを表すため、モデルartifactやrun-scoped packageのように別途SHA-256が公表されている対象との対応は、該当ファイルが本worktreeに存在する場合にのみ検証可能である（`runs/`はこのworktreeには存在しないため、`absent_in_this_worktree`として記録し、ハッシュを捏造していない）。

`verify`結果：`protected_files_unchanged: true`（diff_count 0）。※コマンド実行ログは本ファイル執筆時点のものであり、最終commit直前に再実行した結果を完了報告へ転記する。

## 残る制約

- O1-2（Replay正規化・解析）〜O1-6（Meta/Surrogate）は未実装。継続順序は[実装計画書§23.3](../plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md#233-未実装範囲継続計画)
- live Kaggle capability probeの拡張（O1-5）は未確認のまま。既存C2b Evidence（[competition-probe-v0.md](competition-probe-v0.md)）の`LOCAL_ONLY`結論を変更しない
- 96,530 decisions全件の重い再解析、Opponent Surrogateの大規模学習、Student再学習は本Sliceの完了条件に含めていない（意図的）
