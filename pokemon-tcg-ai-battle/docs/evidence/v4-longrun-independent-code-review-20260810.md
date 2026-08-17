# V4 長時間学習 runner 独立コードレビュー（2026-08-10）

## 結論

**最新判定（Fix round 4 scoped）: PASS（Critical 0 / Important 0）**。Fix round 3の残存3件は解消した。
以下の初回 `CHANGES REQUIRED` とFix round 1〜3は、修正履歴と反例の記録として保存する。

**CHANGES REQUIRED**。`train_recurrent_bc_v4` の「完了した epoch の model state と Adam state を同じ
atomic rename で保存し、次の epoch から再開する」という局所契約は実装されており、focused test も
`32 passed, 1 skipped` である。一方、長時間 run 全体を「端末停止後に同じコマンドで安全に再開でき、
封印した設定の学習結果だけを評価する」とみなすには、次の Important 3件が残る。

1. wrapper 自身が停止すると manifest が `running` のまま残り、次回起動が必ず拒否される。
2. resume checkpoint と最終 training report が、選択 sequence、学習 objective、trainer 実装、主要 optimizer/model
   設定へ end-to-end で結び付いていない。異なる objective からの resume と、異なる設定の report 再利用を受理する。
3. 端末の進捗表示がなく、主 `progress_summary.json` も epoch/update/loss/gradient を取り込まない。resume 後の
   `elapsed_seconds` は累積値ではなく当該 process の経過時間へ戻る。

従って、現 runner は「正常に親 process が生存し続ける一回の run」には使えるが、**restart-safe / monitored long
run の受入条件をまだ満たさない**。下記の修正条件を満たしてから本格GPU学習を開始する。

## 対象

- `src/mage_ptcg/meta_specialist/recurrent_bc_v4.py`
- `scripts/run_meta_specialist_v4_bc.py`
- `scripts/run_meta_specialist_v4_longrun.py`
- `tests/meta_specialist/test_recurrent_bc_v4.py`
- `tests/meta_specialist/test_run_meta_specialist_v4_longrun.py`
- `docs/evidence/v4-archaludon-longrun-runner-20260810.md`

## Important 1: 親wrapper停止後に stale `running` から再開できない

### 根拠

`_run_child` は child 起動直後に manifest を `running` へ更新するが、`KeyboardInterrupt`、wrapper の SIGTERM、
端末消失、wrapper の SIGKILL を処理する `try/finally` / signal forwarding / stale lease recovery がない。
`require_startable_v4` が許可するのは `pending`、`interrupted_*`、`failed` だけであり、`running` は
`--restart-interrupted` の有無によらず拒否される。また `restart_interrupted` 引数は同関数内で参照されない。

最小再現では、存在しない PID を持つ stale `running` manifest に対しても次を得た。

```text
STALE_RUNNING_REPRO: LongrunError longrun is not startable from status 'running'
```

これは「停止後も同じ config・output root で同じコマンドを再実行すれば、完成 epoch までを再利用する」という
長時間runner文書の記述と矛盾する。直近に端末が落ちた運用条件でも再現し得るため、単なる表示上の問題ではない。

### 修正条件

- `_run_child` が SIGINT/SIGTERM/`KeyboardInterrupt` 時に child へ停止を転送し、終了を回収してから
  `interrupted_epoch_boundary_resumable`（training）または `interrupted_restartable`（evaluation）を atomic に記録する。
- SIGKILL/端末消失に備え、起動時に `running` の PID と process start identity / command identity を照合する。
  対象 process が生存していれば二重起動せず監視へ復帰するか明示的に拒否し、存在しなければ stale lease を
  `interrupted_*` へ遷移して既存 epoch checkpoint から再開する。
- test は少なくとも、親 `KeyboardInterrupt`、stale PID、PID が生存中の二重起動拒否、stale training から
  完了 epoch を再実行しないことを含める。

## Important 2: run identity と training artifact / resume state の結合が不完全

### 根拠A: objective を変更しても resume が通る

`last-recurrent-bc-v4.pt` の照合対象は、内部 `run_config`、`sequence_order_seed`、`epochs` である。しかし CLI が
渡す `run_config` には `burn_in`、`subset_fraction`、`max_records`、選択 sequence の順序付き identity digest、
trainer implementation digest がない。`recurrent_bc_v4.py` の objective / shuffle / update 実装も V4 model checkpoint
の implementation closure（`representation_v4.py` と `neural_model_v4.py`）に含まれない。

同一 record/component の sequence の `burn_in` だけを 1 から 0 へ変え、同じ `run_config` で epoch 1 を resume
した最小再現は拒否されず、次を返した。

```text
CHANGED_OBJECTIVE_RESUME_ACCEPTED 2 [0.0, 1.0] 4
```

すなわち history 上は一続きの2 epochだが、epoch 0 と epoch 1 で損失対象が異なる。この経路は CLI の
`--burn-in` 変更でも起こり、学習中に trainer sourceを修正した場合も検出できない。

### 根拠B: longrun validator が異なる設定の report を受理する

training report は `coverage_target`、device、lane、selection manifest SHAを持つ一方、封印された
`hidden_dim`、`embedding_dim`、`epochs`、`patience`、`learning_rate`、`tbptt_steps`、`max_records`、`burn_in`、
trainer identity / canonical run-config hashを公開しない。`validate_training_report_v4` も各 seed について
`history` が list、総 update が正の int、checkpoint path/hashが形式上存在することしか確認せず、
`sequence_order_seed == seed`、epoch連続性、history/update合計、有限loss/gradient/elapsed、model configを確認しない。

そのため、封印設定と無関係な最小 report を同じ output pathへ置くと validator は次のように受理した。

```text
MISMATCHED_REPORT_ACCEPTED: [0, 1]
```

後段evaluationのcheckpoint loaderは壊れたmodel fileを拒否するが、**別hidden dimension等で正常に作られたV4
checkpointは有効なmodelなので拒否理由がない**。したがって最終 manifest が主張する longrun config と、実際に
評価したmodelの学習設定が食い違い得る。

### 修正条件

- materialize後に、partition、episode/component、burn-in、順序付き `record_id` / `content_hash` を含む
  canonical selected-sequence digestを作る。
- `max_records`、`subset_fraction`、`burn_in`、coverage targetとactual coverage、learning rate、epochs、patience、
  TBPTT、gradient clip、model dimensions、device、seed pair、sequence-order seed、selection manifest SHA、trainer
  implementation identityを含む canonical training-config hashを一度だけ定義する。
- 同じ hash / sequence digestを、longrun manifest、training CLI report、各 seed の resume checkpointへ保存し、
  resume前とartifact再利用前に完全一致を要求する。実装変更後に継続を許す場合は、暗黙許可せず明示した
  migration/version契約を設ける。
- `validate_training_report_v4` は checkpoint descriptor のmodel configも読み、seed、履歴の連続 epoch、
  `epochs_completed == len(history)`、総updateとhistory合計、有限NLL/gradient/elapsedを検証する。
- regression test は、burn-in変更、trainer identity変更、hidden/embedding変更、seed取り違え、欠損/不連続history、
  update合計不一致をそれぞれ fail-closed で確認する。

## Important 3: 長時間runの主監視面が要件を満たさない

### 根拠

wrapper は10秒ごとに `progress_summary.json` を更新するが、内容は stage、PID、当該child processの
`elapsed_seconds` だけである。trainerの epoch/update/loss/gradient は別の `training-progress.json` へ epoch終了時だけ
書かれ、wrapper heartbeatはこれを主summaryへ統合しない。TTYでも `tqdm` の単一更新barはなく、child終了時まで
stage/update/ETAを端末で確認できない。これは repository の「長時間実験の端末表示」契約と一致しない。

さらに `RecurrentBCTrainingResultV4.elapsed_seconds` は resume時に `started = time.monotonic()` を新規設定した後の
当該 invocation時間であり、reportのseed-level `elapsed_seconds` は完了済みepochの時間を含まない。history内の
`train_elapsed_seconds` は保存されるがvalidation/materialization等を含む累積wall timeではなく、名称だけからは両者を
区別できない。

### 修正条件

- TTYではwrapperが単一 `tqdm` barを所有し、stage、seed、completed/requested epoch、update、直近train/validation
  NLL、gradient norm、fault、速度/ETAをpostfixへ集約する。非TTYでは約10秒ごとの集約snapshotだけを出す。
- `training-progress.json` の値を安全に読んで `progress_summary.json` へ統合する。詳細ログを `tee` しない。
- `invocation_elapsed_seconds`、`cumulative_train_elapsed_seconds`、可能なら累積wall timeを別fieldにし、resume後も
  historyから再構成できる値は累積として報告する。
- test は heartbeat時のepoch/update統合、resume後の累積時間、TTY/非TTYの出力頻度を確認する。

## 成立している点

- train 512 / validation 128 の非対称targetは commandまで伝播し、実選択器の許容上限512にも収まる。
- 現selection indexには componentが train 2,342、validation 572あり、component数だけを見れば512/128 targetは
  収容可能である。ただし本レビューでは実materialize完走を実行していないため、episode/record capを含む最終成立は
  GPU run前のCPU preflightで確認する。
- epoch callbackより先に `last-recurrent-bc-v4.pt` が保存されるため、callback位置での中断testでは完成epochを
  再実行しない。
- model stateとAdam stateは同じtemporary fileへ保存され、file `fsync` 後に `os.replace` される。best model
  checkpointも別途atomic renameされる。
- deterministic epoch shuffleは `sequence_order_seed` とepochをdomain separationしている。

## 実行した検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/meta_specialist/test_recurrent_bc_v4.py \
  tests/meta_specialist/test_run_meta_specialist_v4_longrun.py -q
```

結果: `32 passed, 1 skipped in 2.62s`。

追加のread-only反証:

- stale `running` manifest（不存在PID）へ `require_startable_v4(..., restart_interrupted=True)`。
- epoch 0保存後、同じrun configのままsequence burn-inを変更してresume。
- sealed configにない最小history/updateと異なるmodel設定を想定したtraining reportを
  `validate_training_report_v4` へ入力。
- 実selection indexのpartition別row/component数を集計（train 130,335 rows / 2,342 components、validation
  32,590 rows / 572 components）。

GPU、CABT、commit、pushは実行していない。

---

## Fix round 4 最終scoped再レビュー

### 判定

**PASS（Critical 0 / Important 0）**。Fix round 3の3件だけを同じ反例で再検証し、全て解消を
確認した。新しい安全性監査へは範囲を広げていない。

| Fix round 3 finding | Fix round 4の確認 | 判定 |
|---|---|---|
| empty Adam / summary矛盾 | resume exact key set、非空・有限Adam moments/step/shape、history/report/resume summaryを照合 | PASS |
| failure progress / child回収 | exit 3は `failed` close、有界stderr artifact、poll中例外はterminate/wait | PASS |
| impossible eval aggregate / closure | WDLF・denominator・score/CI・seat/opponent合計を再計算し、7依存をdigestへ追加 | PASS |

### 同一反例の結果

```text
R4_EMPTY_ADAM_INCONSISTENT_SUMMARY_REJECTED LongrunError last resume checkpoint key set is invalid
R4_IMPOSSIBLE_EVAL_AGGREGATE_REJECTED LongrunError held-out overall WDLF is invalid
R4_FAILED_CHILD_PROGRESS_EVENTS [('init', 2), ('close', 'failed')] manifest failed stderr diagnostic
R4_POLL_EXCEPTION_CHILD_REAPED True
R4_EVAL_CLOSURE_DEPENDENCIES_BOUND True 7
```

empty Adam単体は追加回帰testで `completed updates require nonempty Adam state` として拒否される。
resumeはexact payload key setに加え、initial/best NLL、best epoch、delta/improved、component metrics、
stale count、cumulative elapsedをhistory・training report・last resume間で照合する。

failure経路はreturn code判定後にreporterを `failed` でcloseし、64 KiB上限のstderr診断を残す。
poll loop内のprogress書き込み例外を強制した追加確認でも、実childはterminate/waitされ生存しなかった。

held-out validatorは不可能な負数WDLFに加え、games played/denominator、score、Wilson interval、
seat/opponent内訳とoverallの整合を検査する。前回欠落を指摘した次の7 sourceは、それぞれの
bytesを仮想変更した検査で全てevaluation digestを変化させた。

- `runtime_actions_v2.py`
- `actions.py`
- `actor_visible_features_v1.py`
- `actor_visible_v2.py`
- `decks.py`
- `card_vocabulary_registry_v1.py`
- `collect_teacher_records_v1.py`

### Fix round 4 検証

```text
54 passed, 1 skipped in 14.18s
```

対象は `test_recurrent_bc_v4.py`、`test_run_meta_specialist_v4_longrun.py`、
`test_measure_v4_checkpoint_strength.py`、`test_progress_v1.py` のfocused suiteである。GPU、CABT、commit、
pushは実行していない。

---

## Fix round 3 最終再レビュー

### 判定

**CHANGES REQUIRED（Critical 0 / Important 3）**。Fix round 2の元反例である「schema/model/Adamを持たない
last artifact」と「state更新時のsubject deck静的rebase」は拒否され、evaluation childの端末出力も
wrapperに一元化された。しかしclosed resumeは「load可能なdict」までしか検査せず、実Adam moments欠落と
resume/report数値矛盾を受理する。held-out validatorも対局条件は封印するが、不可能な勝敗集計を
受理する。progressはsingle-ownerになった一方、失敗childを「complete/done」と描画し、epoch内と対局中の
判断値・失敗診断も依然存在しない。

### 元findingの再検証

| finding | Fix round 3結果 | 判定 |
|---|---|---|
| I2: schema/model/Adam/historyが全くないlast artifact | schema、expected path、model/Adam loadを要求 | 元反例はPASS |
| I3: wrapper/evaluation childの端末二重所有 | child stdout/stderrを閉じwrapperのみがreporterを所有 | single-ownerはPASS |
| I4: state更新がlive subject deckへrebase | sealed payloadとlive payloadが違えば更新前にfail-closed | PASS |
| I4: opponent/evaluation runtime identity | subject/opponent実deck SHA、neural policy、actor pool、runtimeを追加 | PARTIAL |

### Important 2: last-resumeのclosed schemaと実Adam/report整合が依然不完全

`_validate_last_resume_lineage` はresume schema、seed、epoch、history、update合計、best tensor SHA、model/optimizer
dictのloadを確認するようになった。一方、次を検査しない。

- `next_epoch > 0` にもかかわらずAdam `state` が空でないこと。新規Adamの空state dictは
  `load_state_dict` に成功するため、model-only resumeが「model+Adam resume」として受理される。
- resume payloadのexact key setと、initial/best validation NLL、best epoch、stale epochs、component metrics、
  cumulative elapsedの存在・型・有限性。
- 上記resume stateとtraining reportの完全一致。reportの `best_epoch` / initial/best/delta/improved /
  component metricsはhistoryやlast resumeと照合されない。
- last resume自体のfile SHAとoptimizer-state SHA。

実在するclosed V4 best checkpoint/model state、正しいseed/path/digest/historyを使い、Adamだけを「1 update完了」
と矛盾する新規空stateへ置き換えた。同時にreportのbest NLL/epochを履歴と不整合な値へ変更しても、
全training reportが受理された。

```text
R3_EMPTY_ADAM_INCONSISTENT_SUMMARY_ACCEPTED [0, 1]
```

これは外部攻撃ではなく、中断後にAdam artifactだけが初期化・取り違えされた場合にoptimizer continuityを
失うrestart correctnessと、学習改善判断を誤るreport integrityの問題である。

修正条件:

- resume payloadのexact schema、各数値の型/有限性/範囲をtrainerとvalidator共通の1関数で検査する。
- 1 update以上のresumeはAdam stateを要求し、parameter mapping、moment tensor shape/finite、stepとupdate境界の
  整合を検査する。optimizer-state digestとlast file SHAをreportにも公開し相互照合する。
- initial/best NLL、best epoch、stale count、component metrics、cumulative elapsedをresume/report/history間で照合し、
  delta/improvedを検査時に再計算する。
- Adam empty/missing moment、resume key欠落、best epoch/NLL/component/cumulative矛盾のparameterized testを追加する。

### Important 3: single-ownerは成立したがprogress/fault契約は未達

`_run_child` はchild stdout/stderrを `DEVNULL` へ向け、wrapperだけが `ProgressReporterV1` を作る。この点で
二重bar/snapshotは解消した。しかし、実childがexit 3で失敗した場合もreturn code判定前に残りtotalを
強制advanceし、reporterを既定 `done` でcloseする。実際のイベントは次の通り。

```text
R3_FAILED_CHILD_PROGRESS_EVENTS
[('init', 2), ('update', 2, 'complete'), ('close', 'done')]
manifest failed
```

さらに次が残る。

- training child progressはmaterialize開始、seed開始、epoch終了時だけで、512 updateのepoch内はupdate/rate/ETAが
  停止する。
- evaluation childはatomic progress artifactを出さず、wrapperは全対局を1 itemとして扱う。対局数、速度、ETA、faultは
  完了まで不明である。
- stdout/stderrを全廃棄するため、OOMやdataset異常で失敗してもreturn code以外の診断artifactが残らない。
- sealed asset drift等によりpoll loop内の `_write_progress` が `LongrunError` を投げると、interrupt経路に入らず
  childをterminate/waitしない。

修正条件:

- child return codeを判定してからreporterの最終advance/closeを行い、失敗は `failed` でcloseする。
- trainingはsequence/update、evaluationはgame/faultの約10秒集約をatomic child artifactへ書き、端末出力は
  wrapperのみが描画する。
- child stderrはサイズ上限付きdiagnostic artifactへ保存する。child起動後の例外は全て
  `terminate -> bounded wait -> kill -> wait`で回収する。
- exit 0/3/signal、TTY/nonTTY、materialize/update、evaluation game/fault、progress write例外のsubprocess testを追加する。

### Important 4: asset封印は改善したがheld-out結果と評価closureは封じていない

manifestは初期config payloadを保持し、subject deck、各opponentの実deck file SHA/policy SHA、exact ID、
evaluation implementation SHAのいずれかがliveで変わればrebaseせずfail-closedになった。元の
`R2_SEALED_CONFIG_REBASED` は解消した。

しかし `validate_evaluation_report_v4` は対局条件/provenanceと `faults == 0` だけを確認し、勝/引分/負、
games played、denominator、score、seat/opponent内訳を検証しない。正確なcheckpoint/deck/opponent/seed/protocolのまま、
負数の引分と範囲外scoreを持つ不可能なreportが受理された。

```text
R3_IMPOSSIBLE_EVAL_AGGREGATE_ACCEPTED
```

また `evaluation_implementation_sha256_v1` は直接指定したneural policy、actor pool、runtimeを含むが、実行意味論を
持つ直接依存の次を含まない。

- `runtime_actions_v2.py` / `actions.py`（合法手decode・order semantics）
- `actor_visible_features_v1.py` / `actor_visible_v2.py`（評価入力）
- `decks.py` / `card_vocabulary_registry_v1.py`（subject binding）
- `collect_teacher_records_v1.py`（evaluation RNG seed）

これらの変更後もevaluation SHAは不変なため、過去reportを現runtimeのexact evaluationとして再利用できる。

修正条件:

- `games_played = wins + draws + losses`、`games_played + faults = requested_games`、非負int、denominator、score/
  Wilson interval、seat/opponent内訳のexact key/count/totalを再計算して照合する。
- 可能なら封印したper-game identity/resultからvalidatorが集計を再生成する。
- evaluation closureを上記の実行意味論依存へ拡張し、対象sourceごとの変更でdigestが変わるtestを追加する。

### Fix round 3 検証

新規read-only反証:

```text
R3_EMPTY_ADAM_INCONSISTENT_SUMMARY_ACCEPTED [0, 1]
R3_IMPOSSIBLE_EVAL_AGGREGATE_ACCEPTED
R3_FAILED_CHILD_PROGRESS_EVENTS [('init', 2), ('update', 2, 'complete'), ('close', 'done')] manifest failed
```

対象focused suite:

```text
51 passed, 1 skipped in 2.82s
```

関連V4 model / dataset / BC / runtime / actor pool / evaluator / progress / campaign / longrunへ広げたsuite:

```text
116 passed, 1 skipped in 7.72s
```

GPU、CABT、commit、pushは実行していない。

---

## Fix round 2 再レビュー

### 判定

**CHANGES REQUIRED（Critical 0 / Important 3）**。Fix round 1で残った元反例はすべて拒否され、signal/PID leaseも
通常経路では成立した。一方、reportとlast-resumeの相互照合はdigest 3値だけで、resume artifact本体のschema、seed、
epoch/history/update/best-stateを照合しない。さらに「sealed config」がstate更新ごとにlive assetから再計算されるため、
subject deck等を静かにrebaseできる。progressは単体componentとしては要件を満たすが、wrapperとevaluation childが同じ
terminalを二重所有し、training epoch内の進捗も存在しない。

### 元反例の再検証

| finding | Fix round 2結果 | 判定 |
|---|---|---|
| dead PIDのstale `running` | `R2_STALE interrupted_epoch_boundary_resumable` | PASS |
| 同じrun configでburn-in/objective変更resume | `R2_OBJECTIVE_REJECTED ValueError` | PASS |
| wrong trainer/sequence lineage report | `R2_WRONG_LINEAGE_REJECTED LongrunError` | PASS |
| wrong seed/max-steps/deck/archetype/opponent evaluation report | `R2_WRONG_EVAL_REJECTED LongrunError` | PASS |

trainer identityは `recurrent_bc_v4.py`、`recurrent_dataset_v4.py`、`representation_v4.py`、
`neural_model_v4.py` のclosureへ拡張された。selected objective SHAもprojected state、target masses、reach/quality weightを含む。
training-config SHAの再計算、live trainer SHA比較、reportとlast checkpoint内objective/trainer/external SHAの相互比較も追加された。

held-out validatorはbase seed、max steps、archetype、subject deck path + SHA、exact opponent list/order、fingerprints、
evaluation implementation SHAを確認する。SIGTERM/SIGHUPはchild停止・interrupt記録へ変換され、PID leaseはLinux process start
ticks + live cmdline hashでPID再利用を識別する。

### 元 Important 1: PASS（軽微な残リスクのみ）

通常のLinux/WSL経路ではdead PID recovery、live同一process拒否、SIGTERM/SIGHUP forwardingが成立した。前回の
Important blockerは解消した。

軽微な残リスクとして、child起動直後に `/proc/<pid>` を読めず `process_start_identity=None` になった場合もrunを開始する。
その状態でwrapperが落ちると、live childを同一processと証明できずstale扱いにして二重起動し得る。start identityを取得
できなければchildを停止してfail-closedにするtestが望ましいが、通常の同一user WSL `/proc` では発生確率が低いため、
本レビューではImportantへは数えない。

### Important 2: last-resume artifact本体との相互照合が不完全

`_validate_last_resume_lineage` はreportが指すtorch fileから `run_config` を読み、次の3系統だけを比較する。

- selected objective SHA
- trainer implementation SHA
- external longrun config SHA

一方、次を検査しない。

- resume schema
- `sequence_order_seed` と対象seed
- `epochs` / `next_epoch` とreportの `epochs_completed`
- resume内historyとreport history
- resume内 `optimizer_updates_completed` とreport/update合計
- resume内best tensor-state SHAとreport/best checkpoint descriptor
- model/Adam stateの存在・可読性
- seed別に期待されるlast-checkpoint path

そのため、`run_config` だけを持ちmodel/Adam/historyを一切持たない任意torch dictを作り、同じfileをseed 0/1の両方へ指定しても
training report全体が受理された。

```text
R2_FAKE_LAST_RESUME_ACCEPTED [0, 1]
```

これは外部改ざんを想定したsecurity指摘ではない。中断・report再生成・path取り違えで、評価するbest checkpoint、再開点、
historyが別run/seed由来でも「resume lineage確認済み」となるrestart correctnessの問題である。

修正条件:

- last artifactに正規resume schemaを要求する。
- seed、epochs、next epoch、history、update合計、best tensor SHAをreportと完全一致で検査する。
- model state / optimizer stateが存在し、対象model configへload可能であることを確認する。
- best checkpoint descriptorのtensor SHAをreport値およびlast resume値と照合する。
- seed 0/1のlast path交換、schema欠損、history差異、update差異、best SHA差異、model/Adam欠損をparameterized testで拒否する。

### Important 3: progress ownerと粒度が長時間run契約に未到達

`ProgressReporterV1` 自体はTTYで単一`tqdm` bar、非TTYで疎なsnapshotを実装し、component testも通る。wrapperは
seed/epoch/update/NLL/gradientをpostfixへ渡すようになった。

ただしintegrationでは次が残る。

1. held-out stageでwrapperが `ProgressReporterV1` を作る一方、`measure_v4_checkpoint_strength.py` childも同じ継承TTYへ
   別の `ProgressReporterV1` を作る。TTYでは2本のbar、非TTYでは2系統のsnapshotが同時に出て、端末表示の「runnerが
   直接所有する単一bar」に反する。
2. training childが `training-progress.json` を更新するのはepoch終了時だけである。512 episode/updateの1 epochが長い間、
   materialize中とepoch内はcompleted=0、seed/update/NLL/ETA不明のままで、長時間学習を判断できる粒度にならない。
3. wrapper + child integrationのTTY/nonTTY test、signal中断時bar close testがない。

修正条件:

- terminal reporterのownerをstageごとに一つへ固定する。推奨はchildがatomic progress artifactだけを書き、wrapperが全stageを
  描画する方式。少なくともevaluationではparent/childのどちらか一方の端末出力を無効化する。
- materializeおよびtrain sequence/updateの10秒集約progressをchild artifactへ書く。1 update 1行は出さない。
- wrapper + real/fake childを組み合わせ、TTYは単一bar、非TTYはinterval内1 snapshot系統だけであることをtestする。

### Important 4: 評価assetをmanifestへ封印した後もstate更新が再baselineする

evaluation reportのexact field検証は実装された。しかし `_config_payload(config)` が呼ばれるたびに、subject deck bytes、
opponent pool、evaluation sourceからhash/fingerprintをlive再計算する。`_update_manifest` は既存manifestのimmutable configを
保持せず、新しい `_manifest` で全体を書き直す。

初期化後にsubject deckを変更してstate更新した反例では、エラーにならずmanifestのconfig SHAとdeck SHAが新しい値へ変わった。

```text
R2_SEALED_CONFIG_REBASED True True
```

長時間学習中に別作業がdeck/pool/evaluatorを編集する運用では、開始時の「封印済みheld-out protocol」が別protocolへ静かに
置き換わる。再現性のためにhashを追加した目的を打ち消す。

またopponent fingerprintの `canonical_deck_hash` はpool manifestの宣言値で、`load_opponent_pool_v1` はpolicy file SHAを
実bytesと照合するが、opponent `deck.csv` は60枚合法性だけを確認し、宣言canonical hashとの一致を検証しない。別の合法60枚へ
変更されてもfingerprintが不変のまま実対戦deckだけが変わり得る。evaluation implementation closureもV4 runtime policy / actor
adapterを含まないため、checkpointから行動を作るruntime意味論の変更を識別できない。

修正条件:

- initializationで解決したconfig payload/hashをimmutableに保存し、全state/progress更新はその値を再利用する。更新前にlive
  assetsとsealed値を比較し、差異があればrebaseせずfail-closedにする。
- subject deck、各opponentの実 `deck.csv` file SHA、policy file SHA、exact IDsを封印する。canonical hashを使うなら実deckから
  再計算してmanifest値と照合する。
- evaluation implementation closureへneural policy/runtime adapter/actor-poolの評価意味論を含める。
- deck/pool/evaluatorをinitialize後に変更してからstate更新・評価・resumeする各testで、manifest不変かつfail-closedを確認する。

### Fix round 2 検証

元4反例と新規反例をread-onlyで実行した。新規反例は次の2件。

```text
R2_SEALED_CONFIG_REBASED True True
R2_FAKE_LAST_RESUME_ACCEPTED [0, 1]
```

対象focused suite:

```text
50 passed, 1 skipped in 2.89s
```

関連V4 model / dataset / BC / runtime / actor pool / evaluator / progress / campaign / longrunへ広げたsuite:

```text
115 passed, 1 skipped in 8.71s
```

GPU、CABT、commit、pushは実行していない。

---

## Fix round 1 再レビュー

### 判定

**CHANGES REQUIRED（Critical 0 / Important 4）**。前回指摘のうち、dead PID recovery、CLIからresumeまでの主要
hyperparameter伝播、history/update検査、非TTY snapshot、累積train時間は改善した。しかし、restartとartifact
lineageは一部しか閉じておらず、TTY監視も未実装である。加えて、held-out評価reportの再利用条件に性能判断を
変え得る大きな欠落を確認した。

### 前回 Important 1: PARTIAL

不存在PIDを持つ stale `running` manifest は自動的に
`interrupted_epoch_boundary_resumable` へ遷移するようになった。最小再現の結果は次の通り。

```text
STALE_RUNNING_RECOVERED interrupted_epoch_boundary_resumable
```

`KeyboardInterrupt` / `SystemExit` では child terminate、10秒後kill、interrupt状態記録も追加された。一方、次が
残るため完全解消ではない。

- SIGTERM / SIGHUP handlerがなく、これらでwrapperだけが終了するとinterrupt状態を記録もchildへ転送もしない。
- `running` の照合は `os.kill(pid, 0)` だけであり、process start identityとcommand identityを確認しない。PID再利用や
  unrelated processを「当該longrunが生存中」と誤認すると再開を拒否し続ける。
- 生存中の正しいorphan childへ監視を再接続する経路はなく、そのchildが終わるまで主heartbeatは停止する。
- これらsignal / PID identity境界のtestは追加されていない。

修正条件は、SIGTERM/SIGHUPを含むsignal forwardingとinterrupt記録、PID + process start + command identityの
lease照合、live orphanの監視再接続または明示的な安全回収、各境界のsubprocess testである。

### 前回 Important 2: PARTIAL

CLIが作るper-seed resume configには、record cap、subset fraction、burn-in、coverage、selected sequence SHA、trainer
source SHA、model/optimizer設定、外側longrun config SHAが入るようになった。longrun training report validatorもseed、
連続epoch、有限loss/gradient/elapsed、historyのupdate合計、hidden/embedding次元を検査する。通常のlongrun CLIで
burn-inだけを変更する経路は、外側configまたはresume config不一致として拒否される。

ただしend-to-end照合には次の穴が残る。

1. `validate_training_report_v4` は `selected_sequence_sha256` と `trainer_implementation_sha256` が64桁hexかだけを
   確認し、現在のtrainer/sourceまたはresume checkpoint内の値と比較しない。
2. CLIが生成する `training_config_sha256` をvalidatorは読みも再計算もしない。
3. trainer SHAは `recurrent_bc_v4.py` 1ファイルだけであり、state/action/target projectionを行う
   `recurrent_dataset_v4.py` 等を含まない。selected sequence SHAもrecord/content IDとburn-inだけで、projected state、
   `target_masses`、`reach_mass` を含まない。このためprojection実装が変わるとobjectiveが変わっても同じdigestになる。
4. 低水準APIの `train_recurrent_bc_v4` は引き続き `run_config=None` または不足configを許す。前回と同じ
   burn-in 1→0のAPI再現は `CHANGED_OBJECTIVE_API_RESUME_ACCEPTED 2` となった。longrun CLIの主経路は修正されたが、
   関数自身が「sealed run」を保証する契約にはなっていない。

有効なV4 checkpointと、現在config SHAだけをコピーし、trainer/sequence/training-config SHAを別値にしたreportは
現validatorに受理された。

```text
WRONG_LINEAGE_REPORT_ACCEPTED [0, 1]
```

修正条件は次の通り。

- trainer identityを、BC runner、trainer、dataset projection、representation/modelの学習意味論closureへ広げ、現在値と
  report値をvalidatorで比較する。
- `training_config_sha256` をcanonical payloadから再計算して一致を要求する。
- selected objective digestをprojected state/action/target/reachまで含めるか、再materializeした期待digest、report、各seedの
  last checkpointを相互照合する。
- sealed用途では `run_config` と必須identity fieldの欠損を拒否する。汎用の非sealed APIを残す場合はresume不可の別modeへ
  分離する。
- old trainer SHA、projection変更、偽training-config SHA、report/last-checkpoint間sequence SHA不一致の回帰testを追加する。

### 前回 Important 3: PARTIAL

非TTYではheartbeatごとに集約JSONを出し、`training-progress.json` のepoch/update/loss/gradientを主
`progress_summary.json` へ取り込むようになった。`invocation_elapsed_seconds` と
`cumulative_train_elapsed_seconds` も分離された。

一方、TTY分岐は出力を行わず、`tqdm` import / 単一更新barが存在しない。このため通常の対話terminalではepochが終わる
まで表示がなく、repositoryの長時間実験表示契約を満たさない。SIGTERM時の最終progressも上記Important 1と同様に残る。

修正条件は、wrapper所有の単一TTY `tqdm` bar、stage/seed/epoch/update/loss/gradient/速度/ETAのpostfix、非TTYの現行
約10秒snapshot維持、およびTTY/非TTY出力testである。

### 新規 Important 4: held-out評価の封印・再利用条件が不足

`validate_evaluation_report_v4` はcheckpoint identity、games/seat、総局数、opponent IDの**個数**、fault 0、valid status
だけを見る。次を確認しない。

- `base_seed`
- `max_steps`
- `subject_archetype_id`
- `subject_deck_csv` とdeck content SHA
- `fixed_held_out_opponent_ids` と `opponent_ids` の正確なID・順序
- 各opponentのdeck/policy fingerprint
- evaluator / opponent-pool implementation identity

実際に、base seed、max steps、deck path/archetype、6 opponent IDを全て誤った値にしたreportが受理された。

```text
WRONG_EVALUATION_PROTOCOL_ACCEPTED
```

これはsecurity問題ではなく、同じcheckpointの勝率を別の対戦条件で測ったartifactを独立held-out結果として再利用できる
性能評価上の問題である。特にsubject deckとopponent poolは現在の作業treeで変更され得るため、長時間学習の採否判断を
直接歪める。

修正条件は次の通り。

- longrun configへsubject deck content SHA、正確なheld-out opponent tuple、各opponent deck/policy fingerprint、評価実装
  identityを封印する。
- evaluation reportにも同じprovenanceを記録し、validatorがbase seed、max steps、archetype、deck path + SHA、exact
  opponent list/order、fingerprint、evaluator identityを完全一致で検査する。
- いずれか一つを変えたreportを再利用拒否するparameterized testを追加する。

### Fix round 1 検証

対象focused suite:

```text
34 passed, 1 skipped in 2.45s
```

関連するV4 model / dataset / BC / runtime policy / actor pool / held-out evaluator / campaign / longrunへ広げたsuite:

```text
102 passed, 1 skipped in 7.50s
```

追加のread-only反証:

- dead PIDのstale `running` recovery: PASS。
- 低水準APIのobjective変更resume: 依然ACCEPT。
- current config SHAを持つwrong trainer/sequence lineage report: ACCEPT。
- wrong seed/max-steps/deck/archetype/opponent-list evaluation report: ACCEPT。
- source/test検索でTTY `tqdm`、SIGTERM/SIGHUP handler、process start identityが存在しないことを確認。

GPU、CABT、commit、pushは実行していない。
