# V4 Archaludon 長時間学習 runner（2026-08-10）

## 結論

`scripts/run_meta_specialist_v4_longrun.py` は、V4 Archaludon の拡大学習を開始するための
再現・監視用 wrapper である。fast research reader は train/validation を別々に指定でき、既定で
train 512 complete episode/component、validation 128 complete episode/component、2 seed × 3 epoch
（各 seed 最大 1,536 optimizer update を想定）を封印する。各 seed のbest checkpointを固定6 opponent・
両seat・各seat8局（96局）で評価する。

このrunnerは性能向上を主張しない。wave2 の新source checkpointが、fault 0かつ独立96局で
v2smoke baselineを上回るという判断が済んだ後にだけ起動する。

## 再開と監視の実際の契約

`train_recurrent_bc_v4` は各epoch終了時に validation を行い、best model checkpointとは別に、model state、
Adam optimizer state、completed epoch、deterministic sequence-order seed、run config、history、update/gradient
telemetryを `last-recurrent-bc-v4.pt` へ原子的に保存する。従って、停止後は**完了済みepoch境界からだけ**
再開できる。epoch内のcursorやRNG逐次状態は保存しないので、epoch途中をexact resumeしたとは主張しない。

resume state は、selection manifest SHA、materialize後の順序付き projected state/action/target/reach objective SHA、partition/component
coverage、burn-in、subset fraction、record cap、model dimension、optimizer/trainer設定、trainer source SHA、外側
longrun config SHA を含むcanonical configと照合する。いずれかが変わるとresumeを拒否する。training reportの再利用も、
この外側config、history連続性、update合計、有限loss/gradient/elapsed、checkpoint descriptorのhidden/embedding次元を
検証してからに限る。trainer source identityはBC本体に加えdataset projection、representation、modelの学習意味論closureを含む。
各seedのlast resume artifactはschema、seed、configured/next epoch、history、累積update、best tensor SHA、expected path、
model state、Adam stateをreportおよびbest checkpoint descriptorと照合し、実際のmodel/optimizerへloadできることまで確認する。
1 update以上を主張するartifactでは、Adamのstateが空でないこと、parameter IDが実modelのparameterへ対応すること、
`step`がupdate数の範囲内であること、`exp_avg` / `exp_avg_sq`のshapeと有限性も検証する。reportとlast artifactの
initial/best/final validation NLL、best epoch、component別best NLL、history、update数、累積train elapsedもhistoryから
再導出して厳密に照合する。

wrapperはこの制約を `restart_contract: epoch_boundary_optimizer_resume_only` として、開始前から
`run-manifest.json` と `progress_summary.json` へ原子的に記録する。

- 完了済みでhash/provenanceが一致する training/evaluation artifactだけを再利用する。
- training childの停止は `interrupted_epoch_boundary_resumable` と記録し、次回実行は最新の完成epoch
  snapshotを検証してから再開する。設定またはsource/data coverageが違えばfail-closedになる。
- wrapper自身の停止で `running` manifestだけが残った場合は、PID生存中なら二重起動を拒否し、PID不在なら
  stale leaseを上記interrupt状態へ原子的に遷移する。PIDはprocess start ticksとcmdline、起動command SHAを併用して
  PID再利用を区別し、SIGTERM/SIGHUPもchildへ停止を転送してinterrupt状態を保存する。
- 実行中は10秒既定で `progress_summary.json` にstage、PID、経過秒数、config hashを原子的に更新し、
  trainerもepoch完了ごとに `training-progress.json` へepoch、update count、loss、last checkpointを保存する。
- TTYではwrapperが所有する単一の更新式barへseed/epoch/update/train・validation NLL/gradientを集約し、非TTYでは
  同じ情報を約10秒間隔のsnapshotだけで出力する。child出力はterminalへ継承せず、atomic progress artifactだけを
  wrapperが読むため、evaluationを含めbar/snapshotは二重化しない。evaluationは局単位のcompleted/total、rate、ETA、
  fault、scoreをatomic progressへ保存し、wrapperが集約する。trainingはepoch境界のloss/update/gradientを保持し、
  materialize中とepoch内にもstage heartbeatを維持する。
- childのreturn codeを確認する前に`complete`/`done`へ遷移しない。非zero終了は`failed`（signal停止は
  `interrupted_epoch_boundary_resumable`）でbar/progressを閉じ、poll・例外・signalの全経路でchildをwaitし、必要時は
  terminate/killして回収する。stderrはpipe deadlockを避けて継続読取しつつ先頭64 KiBだけをbounded failure artifactへ残す。

held-out評価はbase seed、max steps、subject archetype、subject deckの絶対pathとcontent SHA、固定6 opponentの正確な
ID順序、各deck/policy fingerprint、evaluator implementation SHA、checkpoint provenanceをlongrun configとreport間で
完全一致させる。opponent fingerprintには実deck.csv bytes SHAを含み、evaluator closureにはneural policy、actor adapter、
runtime、runtime action変換、action schema、actor-visible feature抽出、deck resolver、card vocabulary、teacher worker内の
RNG seed実装、simulator glueを含める。条件が一つでも違う既存reportは再利用しない。

評価reportの再利用時は、overallおよびseat/opponent別のrequested/W/D/L/fault/played、score rate、Wilson CIをraw countから
再計算する。seat 0/1と固定opponent全件のmatrixが過不足なく、各行とoverallの合計が一致し、count・rate・intervalが
定義域内にある場合だけ受理する。負のdrawやrequested数と合わないW/D/Lのような形式上validでも算術上不可能なreportは拒否する。

initial manifestのconfig payload/hashはimmutableである。以後のstate/progress更新前にlive deck/pool/evaluator/configを初期値と
比較し、差異があればmanifestを書き換えずfail-closedになる。state更新によるsilent rebaselineは行わない。

## 起動コマンド

wave2 判定が正となった後、GPUが見えるrepository rootで次を実行する。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_meta_specialist_v4_longrun.py
```

成果物は `runs/meta-specialist-v4-archaludon-longrun/` に出力される。停止後も同じconfig・output rootで
同じコマンドを再実行すれば、完成epochまでを再利用する。target、model、optimizer設定、selection hashを
変える場合は新しいoutput rootを使う。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/meta_specialist/test_run_meta_specialist_v4_longrun.py -q
```

実行結果: `122 passed, 1 skipped`（V4モデル・dataset・policy・BC subset/resume・評価/longrun wrapperの回帰suite）。GPU/CABTはこのrunner
実装の検証では実行していない。
