# Continuous League Runbook

## 結論

`scripts/continuous_league.py` は、資格済み opponent の固定 catalog、CABT experience 収集、immutable replay、継続 R2D3 学習、checkpoint ごとの model-only 評価、population rollover、PSRO、source refresh、Public score 校正を分離した process と artifact で接続する。

Kaggle 提出はこの runbook の対象外であり、どの command からも実行しない。長時間運用を開始する前に、まず以下の短時間経路を同じ設定で完走させる。

## 1. Catalog と固定 benchmark

資格済み submitted assets と、公開 Replay／team remote から生成した Rule v0 deck pool を pinned catalog 化する。既存 submitted split は初期 role map として固定する。

```bash
.venv/bin/python scripts/analyze_leaderboard_decks.py \
  --competition pokemon-tcg-ai-battle \
  --cache-dir data/leaderboard_decks \
  --report data/leaderboard_decks/report.md \
  --json data/leaderboard_decks/report.json

.venv/bin/python scripts/policy_learning/build_deck_opponent_pool.py \
  --repo . \
  --leaderboard-report data/leaderboard_decks/report.json \
  --output data/opponent_deck_pool

.venv/bin/python scripts/continuous_league.py role-map-from-populations \
  --training-population /path/to/submitted_training_population.json \
  --validation-population /path/to/submitted_validation_population.json \
  --deck-holdout-population /path/to/submitted_deck_holdout_population.json \
  --final-holdout-population /path/to/submitted_final_holdout_population.json \
  --output artifacts/continuous-league/initial_role_map.json

.venv/bin/python scripts/continuous_league.py build-catalog \
  --repo . \
  --qualification-ledger /path/to/submitted_asset_registry.csv \
  --initial-role-map artifacts/continuous-league/initial_role_map.json \
  --deck-pool data/opponent_deck_pool/opponent_deck_pool.json \
  --output artifacts/continuous-league/catalog

.venv/bin/python scripts/continuous_league.py build-benchmark \
  --catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --spec configs/continuous_league/benchmark.example.yaml \
  --output artifacts/continuous-league/benchmark/manifest.json
```

`initial_role_map.json` は `{"agents/name": "BENCHMARK_VISIBLE", ...}` の形式にする。同じ policy、source lineage、exact deck の connected component を異なる role へ分ける指定は拒否される。以後の追加 asset は `role_ledger.json` を `--prior-role-ledger` に渡し、既存 role を変更しない。

Rule v0/v1 は固定 `BENCHMARK_VISIBLE` entry として catalog に追加される。deck pool は team remote の top-level `deck.csv`、`origin/dev:opponents/*/deck.csv`、Kaggle 公開 Replay の exact 60枚 deck を重複排除し、Rule v0 と組み合わせた `TRAINING_ACTIVE` entry にする。

新しい remote ref は、次の両 seat smoke が通るまで実行可能 catalog へ入らない。この command は ref を checkout せず、commit archive を隔離 worker で実行し、既存台帳を置換せずに更新版を別出力する。

```bash
.venv/bin/python scripts/continuous_league.py qualify-ref \
  --repo . \
  --ref origin/agents/<name> \
  --base-ledger /path/to/submitted_asset_registry.csv \
  --games 2 \
  --output artifacts/continuous-league/qualification/<name>

.venv/bin/python scripts/continuous_league.py build-catalog \
  --repo . \
  --qualification-ledger artifacts/continuous-league/qualification/<name>/submitted_asset_registry.csv \
  --prior-role-ledger artifacts/continuous-league/catalog/role_ledger.json \
  --deck-pool data/opponent_deck_pool/opponent_deck_pool.json \
  --output artifacts/continuous-league/catalog-next
```

既存 sealed component と deck／policy／source identity を共有する新 ref は、その sealed role を継承する。学習へ移すために role ledger を黙って書き換えず、holdout 世代交代を別途行う。

## 2. Population、experience、sealed replay

training role から frozen uniform mixture を作る。

```bash
.venv/bin/python scripts/continuous_league.py build-population \
  --catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --output artifacts/continuous-league/population
```

初期 RuntimePolicy がある場合、まず両 seat を含む smoke を行い、その後に本収集を行う。`collect` は局ごとの経験を `staging/` へ原子的に保存するため、同じ引数で再実行すれば完了局を読み直して未完了局から再開する。成功した CABT の詳細ログは経験を staging した直後に削除し、完了時には `staging/` も削除する。残るのは chunk と完了 manifest だけである。

```bash
.venv/bin/python scripts/continuous_league.py collect \
  --runtime artifacts/continuous-league/learner/stream/runtime_policies/<runtime_policy_id> \
  --catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --mixture artifacts/continuous-league/population/mixture.json \
  --deck deck.csv \
  --subject-deck-id current-deck \
  --population-epoch-id <population_epoch_id> \
  --episodes 2 \
  --output artifacts/continuous-league/collection

# 本収集の例。固定済み mixture に対し、両 seat が交互になる。
.venv/bin/python scripts/continuous_league.py collect \
  --runtime artifacts/continuous-league/learner/stream/runtime_policies/<runtime_policy_id> \
  --catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --mixture artifacts/continuous-league/population/mixture.json \
  --deck deck.csv \
  --subject-deck-id current-deck \
  --population-epoch-id <population_epoch_id> \
  --episodes 5000 \
  --seed 71000 \
  --output artifacts/continuous-league/collection

.venv/bin/python scripts/continuous_league.py seal \
  --chunk-manifest artifacts/continuous-league/collection/chunks/<chunk_id>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --output artifacts/continuous-league/replays
```

実戦 agent を Rule v0 の大きな deck pool に埋もれさせないため、本収集では相手別 quota を
使う。各値は偶数局であり、collector は各相手を先手・後手同数にして `collection_manifest.json`
へ実績を残す。

```bash
.venv/bin/python scripts/continuous_league.py collect \
  --runtime artifacts/continuous-league/learner/stream/runtime_policies/<runtime_policy_id> \
  --catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --mixture artifacts/continuous-league/population/mixture.json \
  --deck deck.csv --subject-deck-id current-deck \
  --population-epoch-id <population_epoch_id> \
  --opponent-episodes <hard_agent_instance_id>=256 \
  --opponent-episodes <second_actual_agent_instance_id>=128 \
  --opponent-episodes <rule_v0_instance_id>=128 \
  --output artifacts/continuous-league/collection
```

Learner は `status=SEALED` かつ checksum 一致の replay だけを読む。multi-select は single-action sequence を跨がない明示的な境界として除外数を記録する。

既存の長時間実験が、同じ `128` 次元 state と `64` 次元 action の R2D3 replay を不変に保存済みであれば、`import-replay` で再利用できる。import は source manifest と replay の checksum、全 sequence の形状、合法 action、有限値、行動由来を検証する。Kaggle 公開 Replay の action は拒否する。sequence を JSONL へ再展開せず replay 本体を一度だけコピーするため、大きな中間生成物を増やさない。

```bash
.venv/bin/python scripts/continuous_league.py import-replay \
  --source-replay /path/to/frozen/replay.json \
  --source-manifest /path/to/frozen/replay_manifest.json \
  --population-epoch-id <source_population_epoch_id> \
  --source-label <experiment_name> \
  --output artifacts/continuous-league/imported/replays
```

### 2.1 Rule補完と履歴モデル対戦を含む初期Replay

既存Replayの内訳を確認し、Rule v1または履歴モデル対戦が不足している場合は追加収集する。学習途中の代表checkpointを`publish`し、生成された複数のRuntime Policyを履歴モデルCatalogへまとめる。

```bash
.venv/bin/python scripts/continuous_league.py publish \
  --checkpoint /path/to/r2d3-step-010000.pt \
  --deck deck.csv \
  --config configs/continuous_league/gru256.yaml \
  --output artifacts/continuous-league/history-stream

.venv/bin/python scripts/continuous_league.py build-runtime-catalog \
  --runtime artifacts/continuous-league/history-stream/runtime_policies/<runtime_policy_id> \
  --base-catalog artifacts/continuous-league/catalog/catalog_snapshot.json \
  --output artifacts/continuous-league/catalog-with-history.json
```

`build-population`は`--policy-kind`で収集区分を限定できる。Rule v0のdeckを均等抽選するMixture、Rule v1専用Mixture、履歴モデル専用Mixtureを別々に作り、収集時は同じ新Population epoch IDへ束縛する。これにより、Rule v1と履歴モデルが大きなRule v0 poolへ埋没しない。

このリポジトリでV15互換Replayへ不足分を追加する標準ジョブは、次のコマンドで起動する。既定ではRule v0を1,000局、Rule v1を500局、履歴5世代を5,000局収集し、全chunkをV15親Replayへ一度だけ結合する。各局は原子的に保存されるため、停止後は起動コマンドを再実行すれば未完了局から続行する。

```bash
bash scripts/start_continuous_replay_collection.sh

systemctl --user status continuous-replay-bootstrap-v2.service
tail -20 runs/continuous-league-external-v1/bootstrap-v2/collection/runner.log
```

完了時は`collection_summary.json`と最終`replays/<id>/manifest.json`を残す。`--keep-intermediate`を指定しない標準実行では、最終Replayのchecksum照合と再読込が成功した後、再生成可能な局別中間データを削除する。

## 3. 継続学習と checkpoint 評価

学習は必ず有限の窓で実行する。短時間 smoke は `--max-updates 2`、通常の1周期は
`--max-replay-passes 30` を使う。無限に同じ Replay を反復する command はない。

```bash
.venv/bin/python scripts/continuous_league.py learn \
  --replay-manifest artifacts/continuous-league/replays/<replay_id>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --deck deck.csv \
  --config configs/continuous_league/default.yaml \
  --max-updates 2 \
  --output artifacts/continuous-league/learner
```

### 3.1 単一GPUでの高速学習

標準の高速設定は、Replayを一度だけprepackし、再利用するpinned CPU領域からGPUへ転送する。これはWSLでの長時間運用用であり、GPU常駐Replayは使わない。GPU常駐はVRAMだけでなくWindows側のhost commitを大きく消費し、hostのメモリ枯渇ではWSLのPythonごと停止するためである。

```bash
.venv/bin/python scripts/continuous_league.py learn \
  --replay-manifest artifacts/continuous-league/replays/<replay_id>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --deck deck.csv \
  --config configs/continuous_league/gru256_cuda_cycle.yaml \
  --max-replay-passes 30 \
  --output artifacts/continuous-league/learner-cuda-fast
```

TTYではrunnerを直接実行し、1本のprogress barを更新させる。`tee`やpipeへ通すと同じbarの断片が行として増えるため使用しない。非TTYでは約10秒ごとの集計行だけを出し、詳細は`progress_summary.json`で確認する。

終了した process が古い `RUNNING` JSON を残しても、observer は heartbeat を基準に判定する。

```bash
.venv/bin/python scripts/continuous_league.py learn-status \
  --progress artifacts/continuous-league/learner-cuda-fast/progress_summary.json \
  --stale-after-seconds 90
```

`status: STALE` は heartbeat が90秒を超えて更新されていないことを示す。再開点は
`last_checkpoint` の checkpoint であり、progress JSON の `RUNNING` 表示ではない。

停止は`Ctrl+C`または`SIGTERM`で要求する。現在のupdateが完了した後に最終checkpointを保存する。再開時は、最初の実行がoutput配下へ固定したReplayと、保存済みcheckpointを指定する。

```bash
.venv/bin/python scripts/continuous_league.py learn \
  --replay-manifest artifacts/continuous-league/learner-cuda-fast/replay_inputs/<replay_id>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --deck deck.csv \
  --config configs/continuous_league/gru256_cuda_cycle.yaml \
  --max-replay-passes 30 \
  --output artifacts/continuous-league/learner-cuda-fast \
  --resume artifacts/continuous-league/learner-cuda-fast/checkpoints/<checkpoint>.pt
```

`--max-updates` と `--max-replay-passes` は排他的で、いずれか一方が必須である。後者は
Replay sequence 数と batch size から update 上限を計算し、完了時には実際の nominal
Replay 周回数を記録する。既定CPU設定で作ったcheckpointは、model、batch size、precision
などの学習identityが異なるため高速設定へresumeできない。高速設定は新しいoutputで開始する。

ReplayがGPUへ収まるかは、長時間実行前に実データで測定する。

```bash
.venv/bin/python scripts/benchmark_continuous_learner.py \
  --replay-manifest artifacts/continuous-league/replays/<replay_id>/manifest.json \
  --device cuda:0 \
  --batch-sizes 512 \
  --hidden-size 256 \
  --prepack \
  --pin-memory \
  --bf16 \
  --fused-optimizer \
  --matmul-precision high \
  --output /tmp/continuous-learner-prepack.json
```

`status`が`PASS`で、有限のlossを保って複数updateが完了した最大候補だけを採用する。`REJECTED_OOM`または大きな停止時間を含む候補は、単発の中央値が高くても採用しない。

`--resident-replay`はこの標準運用に追加しない。WindowsのResource Exhaustion記録に`vmmemWSL`約59 GBのcommit増加とCUDA失敗が出た環境では、batch 512のGPU常駐Replayを有効にするとPythonがabortした。専用ホストでhost commitまで測定する実験にだけ使う。

### 3.2 WSLでCUDAがabortした後の復旧

`CUDA error: unknown error`、`GPU access blocked by the operating system`、または端末ごとのPython abortが起きた場合は、そのWSL session内で再実行しない。Windows PowerShellから次を実行してWSLのCUDA bridgeを作り直す。この操作は他のWSL processも停止する。

```powershell
wsl --shutdown
```

Ubuntuを開き直した後、CUDAを確認してから、最後に保存されたcheckpointでresumeする。

```bash
cd ~/kaggle/pokemon-tcg-ai-battle
nvidia-smi
.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

`nvidia-smi`がGPUを表示し、Pythonが`True`を出した場合だけ`learn --resume`を実行する。`progress_summary.json`が`RUNNING`のままでも、checkpointに記録されたstepだけが再開点である。

各 checkpoint は optimizer、target、scheduler、Torch/Python/NumPy RNG、PER priority を含む。評価側には optimizer や replay を含めず、semantic model state、feature/action contract、deck を hash した `RuntimePolicy` だけを公開する。

既定設定は 10,000 updates ごとに checkpoint と Runtime Policy を発行する。checkpointごとに固定Anchor benchmarkを回す場合は、別processのControllerを起動する。停止要求時にも最終 checkpoint が保存されるため、再開だけを目的とする細かい中間 checkpoint を常時残す必要はない。

`learn` は開始時にsealed replayの`manifest.json`と`replay.json`を`<output>/replay_inputs/<replay_dataset_version_id>/`へ不変コピーする。resume時はこの永続コピーを`--replay-manifest`へ渡す。`/tmp`はscratch専用であり、learner output、catalog、population、Replay、checkpointの保存先にしてはならない。

Exposure snapshot を作り、controller を別 process で起動する。

### 3.3 Checkpointごとの512局監視

この監視は、同じ4対戦相手、同じsubject deck、両席、同じseed規則をcheckpoint間で固定する。`benchmark_512.example.yaml`の4つのIDを、生成済みCatalogの固定した`BENCHMARK_VISIBLE` opponent instance IDへ置き換えてから使う。相手の追加時は既存ファイルを上書きせず、新しいbenchmark versionを作る。

| 用途 | 設定 | 局数 | 実行契機 |
|---|---|---:|---|
| 継続監視 | `benchmark_512.example.yaml` | 512 | 各checkpoint |
| 採用再確認 | `benchmark_1024.example.yaml` | 1,024 | 人が候補を指定 |

512局のmanifestとexposure snapshotを作る。`<run_root>`には学習runとは別の永続ディレクトリを指定する。実戦性を測る run は `benchmark_representative_512.example.yaml` をコピーし、少なくとも3種類の policy hash と2つの非 Rule policy を満たす catalog entry に置換する。旧4相手 anchor は継続時系列との比較専用に残す。

```bash
.venv/bin/python scripts/continuous_league.py build-benchmark \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --spec configs/continuous_league/benchmark_512.example.yaml \
  --output <run_root>/benchmark/anchor-512.json

.venv/bin/python scripts/continuous_league.py build-exposure \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --replay-manifest <replay_manifest> \
  --coverage-output <run_root>/exposure/replay_coverage.json \
  --output <run_root>/exposure/snapshot.json
```

task worker設定はコピーして、Catalog、benchmark、exposure、Runtime Policy root、evaluation output、history rootを同じrun用の永続パスへ設定する。`checkpoint_evaluation_history_root`がある場合だけ、workerは評価完了後に時系列を追記する。

```yaml
catalog: <run_root>/catalog/catalog_snapshot.json
benchmark: <run_root>/benchmark/anchor-512.json
exposure: <run_root>/exposure/snapshot.json
runtime_policy_root: runs/<learner_run>/stream/runtime_policies
evaluation_output_root: <run_root>/evaluations
checkpoint_evaluation_history_root: <run_root>/checkpoint_history
max_steps: 10000
```

Controllerは学習とは別端末で直接起動する。TTYでは評価中に `checkpoint benchmark` の1本の更新バーだけを表示し、task開始時と512局完了時に画面を再描画する。再描画する表は直近10 checkpoint の`step`、score rate、95%区間、最弱相手、fault、前回からの差分と、queue件数を示す。局ごとのログとworkerの完了JSONは表示しない。`nohup`、`tee`、pipe、標準出力／標準エラーのリダイレクトは使用しない。非TTYでは約10秒ごとの集約行だけを出す。`--max-pending-evaluations 0`は全checkpointを保持する既定である。正の値を指定して上限へ達した場合は、checkpointを捨てずにfail-closedとなる。

```bash
.venv/bin/python scripts/continuous_league.py controller \
  --root <run_root>/controller \
  --events runs/<learner_run>/stream/events \
  --benchmark-id <anchor_512_benchmark_id> \
  --exposure-snapshot-id <exposure_snapshot_id> \
  --handler-config <run_root>/handler_commands.yaml \
  --checkpoint-history <run_root>/checkpoint_history \
  --cpu-slots 1 \
  --max-pending-evaluations 0
```

controller が強制終了した後、`scheduler_state.json`に`RUNNING`が残ることがある。以前の controller と task worker が**すでに停止していることを確認した場合だけ**、次回起動へ`--recover-interrupted`を加える。この指定は残った task を `PENDING` へ戻して同じ evaluation job を ledger から再開する。稼働中の controller に対して使うと重複実行になるため、通常起動には付けない。

```bash
.venv/bin/python scripts/continuous_league.py controller \
  --root <run_root>/controller \
  --events runs/<learner_run>/stream/events \
  --benchmark-id <anchor_512_benchmark_id> \
  --exposure-snapshot-id <exposure_snapshot_id> \
  --handler-config <run_root>/handler_commands.yaml \
  --cpu-slots 1 \
  --max-pending-evaluations 0 \
  --recover-interrupted
```

評価ごとの成績は`<run_root>/checkpoint_history/evaluation_summary.json`で確認する。`latest_complete`、`best_complete`、各`history`行の`score_delta_from_previous_complete`を比較し、faultまたは未完了の結果を性能改善の根拠にしない。

1,024局の再確認は、512局用manifestを上書きせず、`benchmark_1024.example.yaml`から別manifestを作り、候補Runtime Policyを`evaluate`で明示的に実行する。これは自動採用・自動提出を行わない。

Controller は model を import せず、task worker を subprocess として起動する。submitted opponent の snapshot は manifest の配置場所から解決するため、局ごとの scratch directory へ作業ディレクトリを切り替えても参照先を失わない。queue は task identity でdedupeし、checkpoint監視では各eventを保持する。sealed evaluation、visible evaluation、checkpoint、rollover、collection、source refresh の順にresource budgetを配分する。

## 4. Bootstrap Champion から始める新規学習

既存 checkpoint の単純な resume ではなく、外部・チーム資産から新しい学習系列を始める場合は、まず実行可能な **deck-policy 組**を固定する。Kaggle 由来のデッキだけでは方策重みを作れないため、任意デッキ対応 policy と組み合わせた候補として扱う。remote branch は `refresh-sources --fetch-remote` の read-only fetch と snapshot だけを使い、他 branch へ書き込まない。

候補 registry を作る。`simulator-contract-hash` は CABT、action encoder、runtime 上限をまとめた固定 SHA-256 を指定する。値が変われば別の選抜として扱われる。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-build-candidates \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --deck-asset-registry <intake_root>/artifacts/deck_asset_registry.jsonl \
  --simulator-contract-hash <sha256> \
  --output <run_root>/bootstrap/candidates.json
```

次に 256 局の予備選抜用 schedule を作り、CABT を実行する。`bootstrap-run` は一つの進捗バーで進み、同じ `results.jsonl` を指定すれば完了済みの game key を再実行しない。fault、timeout、非合法手は結果へ保存され、最終選抜では候補失格になる。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-screen \
  --candidate-registry <run_root>/bootstrap/candidates.json \
  --opponent-instance <benchmark_opponent_id_1> \
  --opponent-instance <benchmark_opponent_id_2> \
  --games-per-candidate 256 \
  --output <run_root>/bootstrap/screen-schedule.json

.venv/bin/python scripts/continuous_league.py bootstrap-run \
  --candidate-registry <run_root>/bootstrap/candidates.json \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --schedule <run_root>/bootstrap/screen-schedule.json \
  --output <run_root>/bootstrap/screen-results.jsonl \
  --scratch-root <run_root>/bootstrap/scratch
```

予備選抜の上位4組だけを `bootstrap-rank` で別 artifact として凍結してから、同じ opponent 構成で 1,024 局の `bootstrap-validation-v1` schedule を作る。fault を含む候補は finalist にならない。全 game key を含む結果だけが Champion manifest を生成できる。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-rank \
  --candidate-registry <run_root>/bootstrap/candidates.json \
  --schedule <run_root>/bootstrap/screen-schedule.json \
  --results <run_root>/bootstrap/screen-results.jsonl \
  --finalists 4 \
  --output <run_root>/bootstrap/finalists.json
```

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-screen \
  --candidate-registry <run_root>/bootstrap/finalists.json \
  --opponent-instance <benchmark_opponent_id_1> \
  --opponent-instance <benchmark_opponent_id_2> \
  --games-per-candidate 1024 \
  --seed-namespace bootstrap-validation-v1 \
  --output <run_root>/bootstrap/validation-schedule.json

.venv/bin/python scripts/continuous_league.py bootstrap-run \
  --candidate-registry <run_root>/bootstrap/finalists.json \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --schedule <run_root>/bootstrap/validation-schedule.json \
  --output <run_root>/bootstrap/validation-results.jsonl \
  --scratch-root <run_root>/bootstrap/scratch

.venv/bin/python scripts/continuous_league.py bootstrap-validate \
  --candidate-registry <run_root>/bootstrap/finalists.json \
  --validation-schedule <run_root>/bootstrap/validation-schedule.json \
  --results <run_root>/bootstrap/validation-results.jsonl \
  --screen-benchmark-id <screen_schedule_id> \
  --output <run_root>/bootstrap/champion.json
```

`DIRECT_CHECKPOINT` Champion は互換 R2D3 checkpoint の online weight のみを step 0 bundle へコピーする。optimizer、scheduler、Replay priority、RNG、学習 step は引き継がない。ルールベース Champion は actor-visible な教師 decision を対局時に trace し、封印済み dataset から behavior cloning で初期 weight を作る。Kaggle Replay の行動を教師 label にしてはならない。

教師 trace は validation 対局を最初から実行する時だけ `--teacher-output` を渡す。trace は game key ごとの原子的なファイルであり、完了結果だけがあるのに対応する trace が無い状態は fail-closed となる。single-action でない decision、fault 局は dataset へ入れない。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-run \
  --candidate-registry <run_root>/bootstrap/finalists.json \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --schedule <run_root>/bootstrap/validation-schedule.json \
  --output <run_root>/bootstrap/validation-results.jsonl \
  --scratch-root <run_root>/bootstrap/scratch \
  --teacher-output <run_root>/bootstrap/teacher-trace

.venv/bin/python scripts/continuous_league.py bootstrap-collect-teacher \
  --examples <run_root>/bootstrap/teacher-trace \
  --deck-hash <champion_deck_sha256> \
  --teacher-candidate-id <champion_candidate_id> \
  --output <run_root>/bootstrap/teacher-dataset

.venv/bin/python scripts/continuous_league.py bootstrap-distill \
  --teacher-dataset <run_root>/bootstrap/teacher-dataset \
  --config <r2d3_config_with_distillation.yaml> \
  --device cuda:0 \
  --output <run_root>/bootstrap/distillation
```

教師蒸留で初期化する場合は、上の `distillation/distilled_weights.pt` と `teacher-dataset/manifest.json` の `teacher_dataset_id` を指定する。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-initialize \
  --champion <run_root>/bootstrap/champion.json \
  --distilled-weights <run_root>/bootstrap/distillation/distilled_weights.pt \
  --teacher-dataset-id <teacher_dataset_id> \
  --config <r2d3_config.yaml> \
  --output <run_root>/bootstrap/step0
```

`DIRECT_CHECKPOINT` Champion の場合だけ、`--distilled-weights` と `--teacher-dataset-id` の代わりに `--source-checkpoint <compatible_r2d3_checkpoint.pt>` を指定する。

新規学習では `--bootstrap-checkpoint` を使う。`--resume` は checkpoint 全状態を復元するため、同時指定は拒否される。`--deck` は Champion manifest の deck hash と一致しなければ開始前に失敗する。既存 hard Grimmsnarl chunk は deck、policy schema、population identity が一致すると確認できた場合だけ seal して使う。不明な場合は新規 collection を作る。

step 0 bundle は resume checkpoint ではないため、通常の `publish` には渡さない。`bootstrap-publish-runtime` が model-only RuntimePolicy として公開する。この RuntimePolicy で Champion deck の新規 replay を収集し、その sealed replay だけを最初の学習入力にする。これにより、選抜に使った対局結果や別 deck の既存 replay を誤って学習入力にしない。

```bash
.venv/bin/python scripts/continuous_league.py bootstrap-publish-runtime \
  --bootstrap-checkpoint <run_root>/bootstrap/step0 \
  --deck <champion_deck_snapshot.csv> \
  --config configs/continuous_league/bootstrap_champion.example.yaml \
  --output <run_root>/bootstrap/runtime

.venv/bin/python scripts/continuous_league.py build-population \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --output <run_root>/initial-population

.venv/bin/python scripts/continuous_league.py collect \
  --runtime <run_root>/bootstrap/runtime/runtime_policies/<runtime_policy_id> \
  --catalog <run_root>/catalog/catalog_snapshot.json \
  --mixture <run_root>/initial-population/mixture.json \
  --deck <champion_deck_snapshot.csv> \
  --subject-deck-id bootstrap-champion \
  --population-epoch-id <population_epoch_id> \
  --episodes 4096 --seed 71000 \
  --execution-block bootstrap-general-v1 \
  --output <run_root>/initial-collection

.venv/bin/python scripts/continuous_league.py seal \
  --chunk-manifest <run_root>/initial-collection/chunks/chunks/<experience_chunk_id>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --output <run_root>/initial-replays
```

`collect` は完了 game key を読み直すため、端末・WSL停止後も同じ引数で未完了局から再開する。TTYでは各長時間 command を直接起動する。`nohup`、`tee`、pipe、出力リダイレクトを挟むと単一 progress bar が崩れるため使わない。

同じ RuntimePolicy と population から複数の collection chunk を作る場合は、`--execution-block` を chunk ごとに変える。これは game key と sequence ID を分離し、seal 時の重複を防ぐ。停止後の再開では同じ block 名を使う。

```bash
.venv/bin/python scripts/continuous_league.py learn \
  --replay-manifest <sealed_replay>/manifest.json \
  --population-epoch-id <population_epoch_id> \
  --deck <champion_deck_snapshot.csv> \
  --bootstrap-checkpoint <run_root>/bootstrap/step0 \
  --config <r2d3_config.yaml> \
  --output <run_root>/learner \
  --max-replay-passes 1
```

## 5. Population rollover と PSRO

### 5.1 次周期の作成

新しい remote agent を qualification と catalog 更新まで完了した後、まず既存 Replay と
照合する。`plan-cycle` は Replay に存在しない **policy hash と deck hash の組**だけを検出し、
次の collection quota を保存する。まだ CABT を実行せず、他 branch も変更しない。

```bash
.venv/bin/python scripts/continuous_league.py plan-cycle \
  --catalog artifacts/continuous-league/catalog-next/catalog_snapshot.json \
  --replay-manifest artifacts/continuous-league/replays/<replay_id>/manifest.json \
  --bootstrap-episodes-per-new-opponent 32 \
  --output artifacts/continuous-league/cycle/plan.json
```

`plan.json` の `opponent_episode_quotas` を `collect --opponent-episodes` へ渡して収集し、
`seal`、`build-exposure`、有限 `learn`、固定 benchmark の順に実行する。新しい population
epoch へ移す場合だけ、その chunk を `rollover-manifest` の bootstrap として渡す。

新 opponent を採用する前に `collect --episodes 2` 以上で両 seat の bootstrap chunk を作る。新 replay は旧 replay を親として全旧 sequence を保持する。

新しい Replay で既存 checkpoint を継続する場合は、同じ catalog であっても parent を持つ
新しい population epoch を作る。これにより rollover checkpoint は旧 Replay identity を新しい
Replay identity へ明示的に切り替える。

```bash
.venv/bin/python scripts/continuous_league.py build-population \
  --catalog artifacts/continuous-league/catalog-next/catalog_snapshot.json \
  --parent-population artifacts/continuous-league/population/population_epoch.json \
  --output artifacts/continuous-league/population-next
```

```bash
.venv/bin/python scripts/continuous_league.py seal \
  --parent-replay-manifest artifacts/continuous-league/replays/<old_replay_id>/manifest.json \
  --chunk-manifest artifacts/continuous-league/collection/chunks/<bootstrap_chunk_id>/manifest.json \
  --population-epoch-id <new_population_epoch_id> \
  --output artifacts/continuous-league/replays
```

`rollover-manifest` は両 seat coverage がなければ失敗する。`rollover-apply` は model と target を継承し、指定時だけ optimizer を継承する。scheduler、process RNG、old/new PER priority は reset し、global step は継続、epoch step は 0 に戻す。生成 checkpoint を最初に resume するときは transition ID を `learn --resume-identity` に渡す。

PSRO の best-response 採用判断は `psro-decide` で行う。meta improvement、独立 validation improvement、fault、novelty、single-opponent overfit の全 gate を通るまで population は更新されない。

## 6. Source refresh と calibration

remote 更新が必要な run だけ `--fetch-remote origin` を明示する。

```bash
.venv/bin/python scripts/continuous_league.py refresh-sources \
  --repo . \
  --output artifacts/continuous-league/intake \
  --fetch-remote origin
```

取得後も discovered source は candidate/quarantine のままである。`qualify-ref`、pinned snapshot、role ledger 更新、両 seat bootstrap、rollover の順を飛ばして active population へ追加しない。`git fetch` と source scan は他 branch を変更せず、ref と blob を読み取るだけである。この一連は controller inbox の durable task event から自動実行できるが、qualification gate 自体は省略しない。

Public score は明示的に観測値を登録し、独立 RuntimePolicy が 30 件に達するまで `OBSERVATION_ONLY` とする。30 件以降は leave-one-RuntimePolicy-out 検証付き isotonic calibration を生成する。

## 7. 完了条件

- CABT game fault、illegal action、timeout が 0。
- 学習 loss、gradient、TD error が有限で、model parameter と step が更新される。
- strict resume 後の model、target、optimizer、scheduler、RNG、PER が復元される。
- 同じ game key を再実行せず、candidate と固定 baseline を同じ block で比較できる。
- sealed holdout marker は同じ holdout ID の 2 回目の使用を拒否する。
- Kaggle submit、Champion 変更、default deck 変更は別の明示的判断なしに行わない。
