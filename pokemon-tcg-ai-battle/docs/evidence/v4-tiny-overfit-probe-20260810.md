# V4 tiny-overfit probe（2026-08-10）

## 結論

`scripts/run_meta_specialist_v4_tiny_overfit_probe.py` は、V4 の表現・projection・optimizer が、
sealed な小さな teacher-forced subset を実際に覚えられるかを切り分けるための
**DIAGNOSTIC_ONLY** probe である。CABT、held-out strength、promotion の根拠には使わない。

この probe で train exact top-1 が 95% に達しなければ、現在の学習失敗は「データ量不足や
generalization」より手前、すなわち target/projection/model/optimizer の接続不良を優先して
調べる。95% に達しても、独立 validation や対戦強度の改善を意味しない。

## 固定するもの

- `materialize_fast_research_uniform_subset_v4` により、train と validation を各 4--8 complete
  episode/component ずつ materialize する。selection manifest の SHA-256 が一致しなければ停止する。
- train/validation の各 partition に positive STOP target が少なくとも 1 行なければ停止する。
- 各 epoch 後、`evaluate_recurrent_imitation_v4(..., recurrence="carry")` の完全 JSON を両 partition
  について保存する。したがって、forced complete-action domain size 1 は `forced_domain_size1_rows`
  としてのみ残り、NLL・exact top-1・action-type 内訳の分母には入らない。
- 最終 weight は V4 closed checkpoint として保存し、file SHA-256 と tensor-state SHA-256 を指定した
  strict reload が成功した場合だけ report を出す。

CLI は誤って短い smoke を診断と扱わないよう、20--50 epoch に限定する。出力 JSON は
`diagnostic_only: true` と `promotion_authority: false` を常に持つ。

## 実行例

GPU が空いているときに、repository root から次を実行する。これは小規模だが teacher-forced
decoder-prefix を全 epoch で測るため、wave3 GPU pilot と同時には実行しない。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_meta_specialist_v4_tiny_overfit_probe.py \
  --selection-manifest runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json \
  --selection-manifest-sha256 b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc \
  --episodes-per-partition 4 --components-per-partition 4 \
  --epochs 30 --seed 0 --hidden-dim 128 --embedding-dim 64 \
  --learning-rate 0.001 --tbptt-steps 8 --device cuda:0 \
  --output runs/meta-specialist-v4-diagnostics/archaludon-tiny-overfit-seed0.json \
  --progress-path runs/meta-specialist-v4-diagnostics/archaludon-tiny-overfit-seed0.progress.json
```

別端末では次の monitor を実行すると、1本のバーと epoch/NLL/top-1/ETA の集計だけを表示する。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/watch_v4_progress.py \
  runs/meta-specialist-v4-diagnostics/archaludon-tiny-overfit-seed0.progress.json \
  --interval 5
```

結果の判定には `overfit_assessment` を使う。

- `TINY_TRAIN_FIT_CONFIRMED`: non-forced train exact top-1 が 95% 以上に初めて達した epoch を
  `train_exact_top1_reaches_95_epoch` に記録する。学習パイプラインの tiny-set fitting 能力は確認できた。
- `TINY_TRAIN_FIT_NOT_REACHED`: 50 epoch 以内に達しなかった。長時間学習前に、target alignment、
  action-domain construction、reach weight、gradient/update を調査する。

## 検証

実 GPU run はこの実装時点では行っていない。fixture による契約試験は次で確認した。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/meta_specialist/test_run_meta_specialist_v4_tiny_overfit_probe.py \
  tests/meta_specialist/test_v4_imitation_metrics.py \
  tests/meta_specialist/test_recurrent_bc_v4.py
```

結果: `31 passed, 1 skipped`。
