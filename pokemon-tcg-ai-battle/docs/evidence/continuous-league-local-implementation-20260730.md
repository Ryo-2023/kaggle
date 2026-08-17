# Continuous League ローカル実装 Evidence（2026-07-30）

## 結論

継続 R2D3 学習、checkpoint ごとの model-only 評価、資格済み Team remote opponent の固定 roster、immutable replay、population rollover、SP-PSRO、source refresh、校正、report を接続するローカル実装を完了した。

本 Evidence は機能と短時間の合法動作を示す。環境勝率、性能優位、Public score 予測精度、24時間安定性は示さない。

## 実装

- Package: `src/mage_ptcg/continuous_league/`
- CLI: `scripts/continuous_league.py`
- Config: `configs/continuous_league/`
- Runbook: `docs/runbooks/continuous-league.md`
- R2D3 strict checkpoint: `src/mage_ptcg/policy_learning/r2d3/checkpoint.py`
- PER rollover API: `src/mage_ptcg/policy_learning/r2d3/replay.py`
- Ref qualification: `src/mage_ptcg/continuous_league/qualification.py`
- Public／remote deck pool: `scripts/policy_learning/build_deck_opponent_pool.py`

実装した境界は、Catalog／role ledger、ExposureSnapshot、BenchmarkManifest、Experience Chunk、ReplayDatasetVersion、TrainingCheckpoint、RuntimePolicy、EvaluationJob／Result、PopulationEpoch／Transition、Calibration、sealed markerである。

## 検証

### Focused regression

```text
.venv/bin/python -m pytest -q \
  tests/test_continuous_league_contracts.py \
  tests/test_continuous_league_collector.py \
  tests/test_continuous_league_learning.py \
  tests/test_continuous_league_cabt.py \
  tests/test_submitted_opponents_r2d3.py

65 passed, 1 skipped in 8.61s
```

skip は既存テストの任意依存条件であり、continuous league の失敗ではない。compileall、`scripts/docs/validate_docs.py`、`git diff --check` も PASS した。

### 学習と再開

- sealed replay から実 R2D3 learner を2 update実行した。
- loss、gradient norm、TD error は有限だった。
- model parameter とglobal stepが更新された。
- model、target、optimizer、scheduler、Torch／Python／NumPy RNG、PER priorityを含むschema v3 checkpointからstep 3へstrict resumeした。
- rolloverでは旧Replay sequenceを親versionから保持し、新相手の両seat bootstrapを検証した。
- rollover後はPER priorityを一様resetし、transition identityを使って学習を再開した。

### 公式 CABT

- Rule v0対Rule v1をseed 73000、seat交替、2局で実行し、2/2 `DONE`、fault 0だった。
- 学習checkpointから切り出したmodel-only RuntimePolicyをRule v0相手に1局実行し、`DONE`、合法action、trace生成を確認した。
- 資格済み台帳から16 assetをpinned snapshot化した。既存splitはtraining 12、visible 2、sealed 2で、Rule v0/v1をvisibleへ追加した。
- training 12＋visible 4の非sealed固定benchmark rosterは16 opponentになった。
- roster内の`agents/ozawa-crustle-rule`を相手にmodel-only RuntimePolicyを1局実行し、`DONE`、fault 0、Exposure cohort `EXACT_KNOWN`を確認した。

少数CABTの勝敗はsmoke用途だけであり、性能評価へ使用しない。

### 外部 source 取得と再開可能学習

2026-07-31 に読み取り専用で `origin` を fetch し、他 remote branch を checkout／commit／push せずに source intake を実行した。

- incremental source snapshot ID: `ce20523d79ae83298e4380bc86b143660de9484feefcd807f12b12eed19a9861`
- remote refs: 24、全 refs: 51
- source: 4,207、agent source: 1,353、exact 60枚 unique deck: 39
- Git mutation: false、automatic promotion: false

Kaggle Python API 2.2.3 の `competitions_list`、leaderboard、team submissions、公開 episode／Replay を読み取り専用で使用した。metadata probe は `PUBLIC_ARTIFACTS_ONLY` で成功した。leaderboard snapshot は `2026-07-30T16:21:00.737387+00:00`、team 数は5,997だった。

公開 gold 21件、silver 1件、bronze 1件と team remote deck を統合した。`origin/dev:opponents/*/deck.csv` も対象に含めた。

- candidate source: 86
- unique deck: 44
- deck pool hash: `90999b8bf06557737c321d8da9317df6f54d849964fd17174b1e7896eaea29af`
- training population: 56 opponent（資格済み submitted 12、公開 Replay deck 10、team remote deck 34）
- population epoch ID: `a317d4c4c96f791b210603a72c0177830a90ddc04a370b369e21b2a59ffd95ea`

この population から2局を両 seat で収集した。Kaggle 上位 Replay deck と `origin/dev` deck が各1局選ばれ、2/2 `DONE`、4 sequence、multi-select除外0だった。sealed replay IDは `dc5ba58f60051ce7fa62976b8d8a445f5b6d7afbcb8304a7195e2fe779ceddf2` である。

sealed replay から2 update学習し、step 2、loss 4.0731、gradient norm 0.5720、TD error mean 0.4674はいずれも有限だった。schema v3 checkpointとmodel-only RuntimePolicyを生成した。これは学習進行のsmokeであり、性能改善の根拠ではない。

### 並列 learner 実行（2026-07-31）

既存のR2D3 production runと並列に、CPU affinity 2 logical core・低優先度でこのsealed replayを200 update実行した。既存productionのGPU使用量は並列後も継続したが、このlearnerは既定`device: cpu`のためGPUを共有しない。

- result: `COMPLETED`、step 2→202、elapsed 2.82秒
- final loss: 2.2909、gradient norm: 0.1185、TD error mean: 0.0518（すべて有限）
- strict checkpoint: `runs/continuous-league-external-v1/learner/checkpoints/r2d3-step-000000000202.pt`、SHA-256 `e011df8b23fa421bf8d4ddf4ffd1477067805a9ec5b771a188e0fbed4c7cff63`
- RuntimePolicy ID: `c39db72d854b3d8e44fda62fdf8296291fb9af390a477af53097c0095d03cd50`

実行中のTTYは単一`tqdm` barで表示した。完了後の`progress_summary.json`が誤って`RUNNING`を保持する表示不整合を発見し、`COMPLETED`／`STOPPED`を明示保存する回帰テストで修正した。Replayは4 sequenceだけなので、この200 updateは並列実行とcheckpoint進捗の確認用途であり、性能改善の根拠には使わない。

### 再起動後の永続Replay recovery（2026-07-31）

WSL再起動で`/tmp/continuous-league-*`配下の旧Replay入力は消失した。一方で`runs/continuous-league-external-v1/learner/`にstep 202 checkpointとRuntimePolicyは残った。旧Replay identityを復元できないため、異なるReplayへのstrict resumeは行っていない。

- `ContinuousLearner`は学習開始時に、入力manifestとReplay payloadを`<output>/replay_inputs/<replay_dataset_version_id>/`へcontent-verifiedでコピーする。既存コピーの不一致はfail-closedとする。
- `qualify-ref`は`--output`を絶対化してからsnapshotをpinする。これは相対出力を隔離workerのscratch cwdから解決して`FileNotFoundError`になった実不具合の修正である。
- persistent inputから49 unique external deckと資格済みGrimmsnarl 1件を統合し、training 50 member、population epoch `e11c7209054d19d0aafea90f8bdc91b32353ccc283dd2a11d1ec71017cb7263a`を再作成した。
- 両seat各1局を収集してsealed replay `1b855fc7e84e8be03ffb1f0f570a283b21195b715f69b377faa82b96e5e98ba7`（2 sequence）へ保存した。fresh learnerは2 updateで`COMPLETED`、durable copyを入力にcheckpointからstrict resumeしてstep 3へ進んだ。step 3のloss 4.0627、gradient norm 0.6589、TD error mean 0.3944は有限だった。

このsmokeは入力の永続化・資格化・resumeの経路を確認するものである。2 sequenceでのloss、2局の勝敗、RuntimePolicyは環境勝率または学習改善の根拠に使わない。

### v15 frozen Replay の採用と再開可能な本収集（2026-07-31）

v15 performance experiment は学習を継続しているが、Replay 本体は既に 5,000 局から凍結されていた。source manifest の checksum、`128` 次元 public state、`64` 次元 legal action、選択 action、有限 reward/discount、行動由来を全 sequence で検証し、継続リーグの imported sealed replay として一度だけコピーした。

- source: `/home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-psro-production-v15/replay.json`
- source replay: 5,000 games、33,810 sequence、SHA-256 `ea07b3a5f4fa56a9312292b7b82c99c8e3561c4ca93091e9ea7274ed0b2a75ff`
- imported replay ID: `f01e7218bf598857f51351fb3c4f87f06e77155ed2b2fd4047494137308d1a70`
- import先: `runs/continuous-league-external-v1/imported-v15/replays/`

`collect` は局ごとの atomic staging を追加した。同一 request と mixture で起動し直すと、保存済み局を再実行せず未完了局から続行する。chunk と完了 manifest の確定後には staging と成功局の CABT 詳細ログを削除する。focused regression は、意図的な中断後に残り 1 局だけを実行すること、完了後の再実行が 0 局であることを確認する。

同じ再起動で既存R2D3 production processは消失した。durable progressが示す最終位置は`psro-best-response-seed2`の1,875/6,250、fault 0であり、完走ではない。自動再開はしていない。

### 新規 agent ref の資格化

`origin/agents/ozawa-grimmsnarl-rule+RL` の current commit `fe7e70e3b9da96a2e7f64571b506b3ae5711627c` を `qualify-ref` でpinした。

- policy hash: `2802e2f96f938020caa332ef49a3e6bb3bcda0825f637cad4ed22b7bf04e066e`
- deck hash: `92b92bac9f9163ecff933b3dc39294d2cc154c8684f3c8497877661419ebc59d`
- 両 seat 2局: 2/2 `DONE`、illegal 0、crash 0、timeout 0
- qualification: `TRAINING_ELIGIBLE`

同じ deck identity の旧 dev asset が `BENCHMARK_SEALED` であるため、append-stable role ledger は新 ref も sealed role に置いた。新 agent を勝手に training へ移して sealed deck を漏洩させてはいない。公開／remote deck 44件は training population へ追加済みである。

## 未実施の運用 Gate

- 24時間soak
- 長時間production学習
- 固定roster全体の十分な反復数による勝率推定
- 30 independent RuntimePolicyに対応する実Public score observation
- Kaggle提出、Champion変更、default deck変更

Kaggle read-only metadata／leaderboard／公開Replay取得は実証した。30件のidentity対応済みPublic score観測は未達であり、Public score校正は引き続き`OBSERVATION_ONLY`である。未資格の発見sourceを自動実行可能populationへ昇格しない。
