# V4 sealed offline imitation metrics（2026-08-10）

## 結論

`scripts/measure_v4_imitation_metrics.py` は、V4 checkpoint を file SHA-256 と tensor-state SHA-256 の両方で strict load し、fast research materializer が再構成した同一の sealed sequence を teacher-forced で測る診断器である。CABT 対戦や checkpoint 選抜は行わず、promotion authority は持たない。

## 固定する対象

- 選択 manifest は引数の file SHA-256 で固定する。
- materializer は `materialize_fast_research_uniform_subset_v4` をそのまま使い、complete episode / split / component / positive STOP の条件を再検証する。
- JSON の root `selected_sequence_sha256` と partition ごとの SHA-256 は、投影後の state、target mass、reach mass、record identity を含む。carry/reset は同一の partition digest を共有しなければならない。
- checkpoint は `load_specialist_checkpoint_v4` を通す。model config、artifact file digest、tensor-state digest、source/callable closure のいずれかが不一致なら JSON を出さず停止する。

## 測定値

各 train / validation partition について、carry（episode 内で hidden を保持）と reset（各 physical record で reset）を別 pass で測る。

- `complete_action`: forced domain size 1 を除いた exact top-1、top-3、soft-target complete-action NLL、model top-1 が受ける teacher mass。
- `root` / `later`: semantic prefix 長 0 / 1 以上の同じ指標。
- `action_type`: canonical teacher target が candidate の場合は action type、STOP の場合は `STOP` 別の count / top-1 / NLL。
- `teacher_prefix_survival`: 同じ物理 record 内で、その prefix までの非 forced teacher MAP action を連続して top-1 再現できた割合。これは teacher forcing 下の局所誤差が prefix 内でどう累積するかを見る診断であり、free-running game strength ではない。

forced domain size 1 は方策選択の情報を持たないため、`forced_domain_size1_rows` として監査用にのみ数え、NLL・top-k・survival の分母から除外する。NLL の重みは訓練時と同じ `reach_mass` である。

## wave3 で使う checkpoint provenance の取得

学習 report の `seed_results.<seed>` が唯一の checkpoint provenance 入力である。checkpoint 本体から digest を再計算して report と食い違う場合は、評価を開始せず report または artifact の生成経路を調査する。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c '
import json
report = json.load(open("runs/meta-specialist-v4-performance-wave3/archaludon-diversity512-epoch1.json", encoding="utf-8"))
seed = "0"
row = report["seed_results"][seed]
print("checkpoint=", row["best_checkpoint_path"])
print("file_sha256=", row["best_checkpoint_file_sha256"])
print("tensor_state_sha256=", row["best_checkpoint_tensor_state_sha256"])
print("card_vocabulary_size=", report["card_vocabulary_size"])
'
```

`hidden_dim` と `embedding_dim` は当該 run の学習引数と厳密に一致させる。wave3 diversity pilot では `128` / `64` を予定している。checkpoint file SHA-256 を独立に監査する場合は、次を使う（値は上記 report と一致しなければならない）。

```bash
sha256sum runs/meta-specialist-v4-performance-wave3/archaludon-diversity512-epoch1-checkpoints/seed-0/best-recurrent-bc-v4.pt
```

## wave3 seed 0 の完全コマンド template

GPU pilot の checkpoint が生成され、GPU が学習に使われていないことを確認してから実行する。以下の `<...>` は直前の provenance 出力で置換する。output は checkpoint と同じ wave3 root に固定し、他 seed と上書きしない。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_imitation_metrics.py \
  --selection-manifest runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json \
  --selection-manifest-sha256 b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc \
  --checkpoint '<best_checkpoint_path>' \
  --checkpoint-file-sha256 '<best_checkpoint_file_sha256>' \
  --checkpoint-tensor-state-sha256 '<best_checkpoint_tensor_state_sha256>' \
  --card-vocabulary-size '<card_vocabulary_size>' --hidden-dim 128 --embedding-dim 64 \
  --max-records 65536 --episodes-per-partition 512 --components-per-partition 512 \
  --train-episodes-per-partition 512 --validation-episodes-per-partition 128 \
  --train-components-per-partition 512 --validation-components-per-partition 128 \
  --require-positive-stop --device cuda:0 \
  --output runs/meta-specialist-v4-performance-wave3/archaludon-diversity512-epoch1-seed0-imitation-metrics.json
```

seed 1 は checkpoint path / two digest / output basename の `seed0` を `seed1` に替えるだけで、selection と materializer targets は同一にする。`--device cuda:0` が使えない環境では `--device cpu` も契約上は可能だが、多数の decoder prefix を4 pass（train/validation × carry/reset）で通すため、GPU pilot と競合しない GPU 時間を優先する。

## wave3 batch mode（推奨）

`--training-report` と `--seeds 0,1` を使うと、sealed subset を**一度だけ**materializeし、report が持つ各 best checkpoint を連続 strict load して同じsubsetで測る。output は top-level の共通 subset identity と、seed別の `seed_results` を持つ。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_imitation_metrics.py \
  --selection-manifest runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json \
  --selection-manifest-sha256 b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc \
  --training-report runs/meta-specialist-v4-performance-wave3/archaludon-diversity512-epoch1.json \
  --seeds 0,1 \
  --max-records 65536 --episodes-per-partition 512 --components-per-partition 512 \
  --train-episodes-per-partition 512 --validation-episodes-per-partition 128 \
  --train-components-per-partition 512 --validation-components-per-partition 128 \
  --require-positive-stop --device cuda:0 \
  --output runs/meta-specialist-v4-performance-wave3/archaludon-diversity512-epoch1-imitation-metrics.json
```

batch mode は次を fail-closed で照合する。

- training report schema、research-only mode、promotion authority、config SHA、live trainer/source closure
- report と指定した selection manifest の path / SHA-256
- `max_records`、`subset_fraction`、`burn_in`、episode/component/positive STOP coverage target
- materialize 後の `selected_sequence_sha256`
- 指定 seed が report に記録され、各 `best_checkpoint_path` / file SHA / tensor-state SHA / model dimensions が存在すること

そのため seedごとにCLIを二回実行して別のmaterializationを偶然比較するより、直接的に再現可能である。single-checkpoint mode は従来の全 checkpoint / digest / model dimension 引数を与える形のまま維持する。

## wave2 seed 1 の再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_imitation_metrics.py \
  --selection-manifest runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json \
  --selection-manifest-sha256 b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc \
  --checkpoint runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --checkpoint-file-sha256 15ef5f3412a74eed0a4fece1f15195d8bef2f5b1f01944dbec203535ff6648bd \
  --checkpoint-tensor-state-sha256 2d761919b0351e66d7b6ece12f83717e7aba7f7ef8c97600faa3dfd7b44a5d9e \
  --card-vocabulary-size 1267 --hidden-dim 128 --embedding-dim 64 \
  --max-records 16384 --episodes-per-partition 64 --components-per-partition 64 \
  --require-positive-stop \
  --output runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-seed1-imitation-metrics.json
```

この checkpoint は wave2 の source closure に合う artifact に限定する。歴史的 checkpoint は strict loader により再利用不能となり得るため、値だけを比較根拠に転用しない。

## 検証

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_v4_imitation_metrics.py`: 2 passed。
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_v4_imitation_metrics.py tests/meta_specialist/test_recurrent_bc_v4.py tests/meta_specialist/test_neural_model_v4.py`: 68 passed, 1 skipped。
- full 64/64 CPU pass は4 pass×多数prefixとなり、wave3 GPU pilot のhost資源と競合するため実行を中止した。strict loader と materializer を含む CLI 経路は fixture checkpoint / sealed-subset contract test で実行済みであり、wave3では上記CUDA templateを使う。

## wave2 seed 1 実測

TODO: wave3 seed 0/1 の strict-load JSON から、validation carry/reset の action-type と survival を追記する。これは CABT 強度の代替ではなく、held-out strength evaluation と合わせて解釈する。
