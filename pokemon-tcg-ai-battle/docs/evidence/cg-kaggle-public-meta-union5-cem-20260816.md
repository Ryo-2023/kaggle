# cg 公開kernel meta union5 / P1 CEM（2026-08-16）

## 結論

新しい未性能使用の公開 Kaggle kernel policy source を5件取得し、source identity・static safety・合法 deck・bounded CABT smokeを封印した。5件を `META_TRAIN / META_DEV / META_FINAL` に分離した union5 poolへ統合し、P1固定 CEM を2世代実行した。全680局は `DONE`・fault 0 だったが、screen上の改善は独立再評価へ安定転移せず、DEV候補も seat collapse で無効となった。

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、pushは変更していない。

## 1. source provenance と権限境界

以下は公開ページから取得した policy snapshotであり、CABTの native性能や公開スコアを性能証拠として扱っていない。全件 `local_eval_only`、`research_only` で、submission／promotion／training authorityは false のまま封印した。

| source | 公開ページ | identity / 注意点 |
|---|---|---|
| Rmy | [prvsiyan/ptcg-rmy-surface-souta-1208-loader-v1](https://www.kaggle.com/code/prvsiyan/ptcg-rmy-surface-souta-1208-loader-v1) | `mega_lopunny_cleanroom_entrypoint` aliasを明示した loader。source tar SHA `4995cdc212e8de35e4e8e79fd66cc6da21125958104b70064aa53b2cdfdfc153` |
| Aristophanivan | [aristophanivan/probablity-v2](https://www.kaggle.com/code/aristophanivan/probablity-v2) | `%%writefile main.py` cellをstagedし、import-timeのdeck書込み1行だけを隔離ローカル評価用に除去。source tar SHA `96d62e7402f8bb6a5f9640970bc1c645bee7b3f768067ba64f312d63d114e239` |
| Kityugin | [kityugin/version-8-mega-lucario-ex](https://www.kaggle.com/code/kityugin/version-8-mega-lucario-ex) | notebook自身が公式 sample / submitted baselineを参照すると記載。独立作者系譜とは扱わず、distinct policy snapshotとしてのみ使用。source tar SHA `55a08199b75aa9d10a9259ae2c7b857c0423453c1044e6e94754a1e883bbad6b` |
| Aman | [aman5153684/a-crustle-aware-fighting-agent](https://www.kaggle.com/code/aman5153684/a-crustle-aware-fighting-agent) | root deckと同一のdeckを持つ Crustle-aware policy。source tar SHA `5bde58e23efb85c7786051f52157f972890f181bbf89be0f847d70e5dd721b6c` |
| Penguin | [penguin069/public-scores-915](https://www.kaggle.com/code/penguin069/public-scores-915) | optional forward-search policy。公開scoreは性能証拠に使用せず、policy snapshotとしてのみ使用。source tar SHA `e25b8c2199e8083803aa8498c161399a268a79513396ee705ffa589f054ce8d8` |

全5件の bounded smoke は各2局、合計10局を `DONE`・fault 0 で完了した。各 source の raw policy、deck、tar、static gate、smoke、promotion結果は対応する `runs/cg-kaggle-kernel-meta-intake-*`、`runs/cg-kaggle-kernel-meta-promoted-*`、`SOURCE.md` に保存した。なお、`ono-` はこの公開source群の作者名ではなく、self-owned package branch `agents/ono-cg-lethal-v1` のローカル識別子である。

## 2. union5 と historical split

sealed rootは `runs/cg-kaggle-kernel-meta-promoted-union5-20260816/`。

- pool manifest SHA: `b0e4ffb937c1468180cd378d4b1e4d115bb6a2cf3396e99d03d46394908aa4b3`
- fresh meta SHA: `81730c60e8b882f64dd09e5f2741fc2114eb75bb88275c2a890ba9150714b4c2`
- historical split SHA: `0a2d42dce9c8c1bad3035d1f0102e25e682de1a0fb47bd870f38611849e01a4a`
- meta manifest SHA: `b9c087fc4fe82cf2dafc0b99a623f1e4f68f2266b6500d7f6d20d5be70ec47cd`

splitは次のとおりで、各 row の `training_exposure=0` と `usage_boundary=local_eval_only` を固定した。

- `META_TRAIN`: Aman、Penguin、Rmy（3 refs）
- `META_DEV`: Aristophanivan（1 ref）
- `META_FINAL`: Kityugin（1 ref）

`cg_historical_split.json` の evaluator SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、P1 policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` である。

## 3. P1固定 CEM

実験 rootは `runs/cg-p1-cem-union5-20260816-g01/`。campaign seed `202608167`、population／elite `16／4`、2世代、initial scale fraction `0.20`、`META_TRAIN_ALL`、独立 re-evaluation 2回、各 opponent×seat 2局、positive-delta gate、risk-aware updateを使用した。

| 世代 | screen | independent re-evaluation | DEV | 判定 |
|---|---:|---:|---:|---|
| g00 | 204局 | 120局 | — | screen候補の改善がrepeatで安定せず、center保持 |
| g01 | 204局 | 120局 | 32局 | candidateはDEVで1/16対control 0/16だが seat collapse、無効 |

全680局は `DONE`・fault 0（g00: 204+120、g01: 204+120+32）。g00/g01とも `elites` は4件すべて `incumbent-center` となり、CEM center、P1 policy SHA、root deck SHAは変化しなかった。g00 screen上位は最大 `+0.25` だったが、独立 repeat の delta は `+0.25 / -0.1667` などへ反転した。g01 screen上位も独立 repeatで負方向またはゼロとなり、positive・risk-aware・seat-safeを同時に満たす候補はなかった。

g01の `META_DEV`（Aristophanivan）確認は candidate `1W-0D-15L`（6.25%）、control `0W-0D-16L`（0%）。しかし candidate seat rateは `0.125 / 0.0`、`seat_collapse=true`、`valid=false` であり、promotion条件を満たさない。`META_FINAL` は未使用のまま保持した。

## 4. 再現コマンドと一次artifact

```bash
PYTHONPATH=src:. python scripts/run_cg_p1_cem_v1.py \
  --output runs/cg-p1-cem-union5-20260816-g01 \
  --split runs/cg-kaggle-kernel-meta-promoted-union5-20260816/cg_historical_split.json \
  --source-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --control-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --pool-root runs/cg-kaggle-kernel-meta-promoted-union5-20260816 \
  --generations 2 --resume --all-train-refs --reeval-for-update \
  --reeval-repeats 2 --reeval-games-per-opponent-seat 2 \
  --positive-delta-gate --risk-aware-update \
  --campaign-seed 202608167 --population-size 16 --elite-count 4 \
  --initial-scale-fraction 0.20 --execute
```

主要な結果は `manifest.json`、`generation-0000/results.json`、`generation-0001/results.json`、各 stageの `summary.json` に保存した。source intake wrapperの focused testsは `13 passed`、対象 moduleの `py_compile` は pass である。

## 5. 次の再開条件

今回のunion5 refsはこのCEMで性能使用済みであるため、同じpoolを未使用 batchとして再利用しない。次は smoke候補と性能holdoutを分離した新しい未性能使用 source epochを作り、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を順に通過した候補だけ `cg_bestknown_loop_v1.py` へ接続する。P1のblind retry、deck search、BestKnown／Champion変更、commit、push、Kaggle提出は行わない。
