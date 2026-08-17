# Cross-fitted MC residual targets — 2026-08-12

## 判定

Wave6 seed別のsealed screen transitionから、teacher hard labelを使わない研究専用のcross-fitted Monte-Carlo signed-behavior target manifestを生成した。これはtarget生成とprovenanceの証跡であり、residual学習、CABT評価、longrun、Champion変更、Kaggle提出は行っていない。

## 固定したobjective

`cross_fitted_mc_signed_behavior_residual` を使用する。各episodeのtransition returnは逆順に `G_t = reward_t + discount_t * G_(t+1)` として計算する。baselineはepisode SHA由来のdeterministic 2-foldにおけるfold外episode returnのglobal meanである。opponent ID、seat、behavior versionは入力screenのepisode連続性検証だけに使用し、manifest、target、runtime featureには出力しない。

各transition targetは、その実際に選ばれたsealed legal prefix tokenのindex列と、`signed_weight = clip((G_t - baseline) / 1.0, -1, 1)`を保存する。`target_kind`は常に`"signed_behavior_log_probability"`であり、teacher distribution、teacher hard selection、またはself-imitation labelではない。

## 成果物

- [target schema](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/src/mage_ptcg/meta_specialist/cross_fitted_outcome_residual_v1.py)
- [screen builder](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/build_cross_fitted_outcome_residual_manifest_v1.py)
- [schema tests](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_cross_fitted_outcome_residual_v1.py)
- [builder tests](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_build_cross_fitted_outcome_residual_manifest.py)
- seed0 artifact: `runs/meta-specialist-frozen-residual-outcome-targets-20260812/seed-0-cross-fitted-mc-targets-v1.json`
- seed1 artifact: `runs/meta-specialist-frozen-residual-outcome-targets-20260812/seed-1-cross-fitted-mc-targets-v1.json`

manifest loaderはopen schema、opponent/seatの注入、teacher hard targetへのreclassification、未終端episode、game reentry、noncontiguous transition orderをfail-closedで拒否する。全artifactは`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`である。

## 実データ集計

| Wave6 seed | train episodes | transitions | episode return（勝/負/0） | signed targets（正/負/0） | manifest SHA-256 |
|---:|---:|---:|---:|---:|---|
| 0 | 74 | 3,678 | 36 / 38 / 0 | 2,162 / 1,516 / 0 | `9d1a793a79f47206c36dc7e748f527fff339d7192e12b0e0cbc7201ea9c006d0` |
| 1 | 69 | 3,892 | 36 / 33 / 0 | 2,177 / 1,715 / 0 | `4725d7e6741c51b48a4cb828070753790dc9cd16c771ecf783b316f2091bc2f5` |

各source screenのtrain gameは一度だけ現れ、transition indexは0から連続し、74/74および69/69 episodeが最終transitionのみterminalであった。episode returnの範囲は両seedとも`[-1.0, +1.0]`である。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_cross_fitted_outcome_residual_v1.py \
  tests/meta_specialist/test_build_cross_fitted_outcome_residual_manifest.py
```

結果は`6 passed`。さらに実artifactを`load_cross_fitted_outcome_manifest_v1()`で再読込し、seed0=74 episode/3,678 transition、seed1=69 episode/3,892 transition、SHAを再検証した。`py_compile`と`git diff --check`も通過した。

## 残課題

- signed behavior objectiveをresidual-only trainerへ接続する前に、signed lossの正規化、negative advantageの最適化意味論、effective denominator、base tensor不変、variable legal-domainをTDDで閉じる必要がある。
- このtargetはon-policy outcome由来であり、teacher correctnessやcounterfactual action valueを示さない。cross-fitted value baseline/AWRとpublic-belief search/Qは未実装である。
- fixed-six CABT評価、shadow-C、longrun、promotionは未着手である。
