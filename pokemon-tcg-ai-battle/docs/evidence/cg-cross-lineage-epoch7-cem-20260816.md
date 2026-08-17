# cross-lineage epoch7 source生成と c05 holdout（2026-08-16）

## 結論

同一 Lucario 系 self-owned source の追加ではなく、未使用の公開 kernel lineage から policy parent と deck parent を分離して交差再構成する `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1` を実行した。7 sourceを生成し、静的検査・合法性検査・P1 bounded smoke・promotion・split rebindingを完了した。P1固定の1世代 CEM は全 240 局（screen 180、独立再評価 60）が `DONE`・fault 0 だったが、risk-aware positive gateを満たさず center はP1のまま保持した。

独立未使用 `META_DEV`／`META_FINAL`（各1 source）でscreen上位 c05を両seat各8反復、候補／control計64局で確認したところ、c05は `3W-29L`（9.375%）、P1 controlは `0W-32L`（0%）で差 `+9.375pt`、fault 0だった。ただし holdout は2 sourceだけで、絶対勝率も低く、TRAIN独立再評価の差は `+20pt / 0pt`（worst 0pt）だったため、これは転移シグナルであって昇格証拠ではない。BestKnown、P1、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は変更していない。

## source生成

policy parentには `samrishb/unified-ptcg-framework-v2`、`sushanthtiruvaipati/pokemon-tcg-mega-emboar-strategy`、`yaminh/the-pokemon-company-ai-challenge-v3`、deck parentには同3系統と `sushanthtiruvaipati/pokemon-tcg-zacian-ex-heuristic-agent` を使った。各生成物は policy／deck pair identityを新規化したが、出典は公開 kernel lineageであり、self-owned sourceとは分類しない。`local_eval_only`、authority全false、既存pair identity重複拒否を維持した。

生成根は `runs/cg-cross-lineage-epoch7-20260816/`、promotion後の正本は `runs/cg-cross-lineage-epoch7-20260816-promoted/` である。受理7件、除外0件、P1 smokeは seed `202608984`・14局・8W-6L・fault 0だった。promoted poolには以下のSHAを固定した。

- pool manifest: `aa5a01b6a6bcfa12b2468c305c54810d02d5b5fc7e3fa359648455052569ff58`
- fresh meta: `ad5b80d9d5db4258f11958c167e2dda286ec30c1013fe45ce4e3252da4e582f5`
- meta manifest: `c32822ba9ac8b8384a0e58ac8d9353a482ab94197bdd1933c984a34c8cd2b70e`
- rebound split: `75c6262ce42d27a0e4e9ef4177b28a460a6f981e6f422b7692f0b95afaa46a88`
- promotion smoke summary: `d86221d84a2dde40644344b82d7ffa7ab60df5f8a9cea1b22a81b88f1f7f805b`

splitは `META_TRAIN=5`、`META_DEV=1`、`META_FINAL=1`。DEV／FINAL policy lineageは yaminh、TRAINは samrishb／Sushanth Emboar で、holdout policy lineageをTRAINから分離した。`load_weekend_split(..., verify_sources=True)` はPASSした。

## P1 CEM

実行根は `runs/cg-cross-lineage-epoch7-cem-20260816/`。seed `202608985`、population／elite `8／2`、1世代、`META_TRAIN_ALL`、独立再評価2回、positive delta gate、risk-aware updateである。

- screen: 180局、全row `DONE`・fault 0。deltaは `−15, −10, −10, −15, −5, 0, −5, −10pt`。
- independent c05: aggregated candidate `18/20` 対 control `16/20`、`+10pt`。反復は `+20pt / 0pt`、risk-aware minimum `0pt`。
- independent c06: aggregated candidate `15/20` 対 control `16/20`、`−5pt`。
- selection: `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`
- elites: `incumbent-center` × 2
- center: P1 configを保持
- campaign manifest SHA: `05512dd7be771d1bcccd02f8f0424d89e118045453e4198a4ce6b4a35f961757`
- generation manifest SHA: `86ef031d3007c6a0337ed01dc89515cf846a5606876b15cb9641e5d8d217cdb6`
- results SHA: `39ca059e30efad88f580875ae0f01d5a689f66daacf9f1b6d566b1f0044b95dd`

c05の policy SHAは `f4cd1930a96b652c0938d49488c6d268b938d4aa34e6385c870351eaf56a028d`、config SHAは `7d8ef6daefcf0e9adaea6e81f52c6469b769a2d4fb645c58c2609bbf7a8bab7c`。deckはP1 root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` に固定されている。

## 未使用DEV／FINAL holdout

未使用の2 referenceを、c05 packageとP1 packageへ同じ `base_seed=202608986`、両seat、各8反復で割り当てた。実行根は `runs/cg-cross-lineage-epoch7-c05-holdout-20260816-retry1/` で、64/64 `DONE`・fault 0だった。

- c05: `3W-0D-29L`、score `9.375%`
- P1 control: `0W-0D-32L`、score `0%`
- candidate delta: `+9.375pt`
- holdout summary SHA: `91332017fa989c85560b4d6c35e86b9a6b39da26aee6c77114cda27cb76b35a6`
- holdout complete manifest SHA: `bf6f513c17081526a8195db83b522175ec33e87a18dc7d6627c675e8eeb1934f`

この差は「新しい policy surface が holdout で全く無効ではない」ことを示すが、2 source・64局の単一 holdoutであり、P1 control自体の絶対値も0%だった。したがって `POSITIVE_CONTINUE`、P2昇格、deck phase、BestKnown更新の条件には使わない。holdoutを選抜・CEM updateへ戻すこともしていない。

## 再現コマンド

```bash
PYTHONPATH=.:src .venv/bin/python scripts/generate_cross_lineage_meta_v1.py \
  --policy-root runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/kaggle_yaminh_lucario_v3_staged_20260816 \
  --policy-root runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6g-20260816-retry1/kaggle_samrishb_unified_framework_20260816 \
  --policy-root runs/cg-kaggle-kernel-meta-promoted-public-fresh-epoch5d-p1-20260816/kaggle_sushanth_emboar_strategy_staged_20260816 \
  --deck-root runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/kaggle_yaminh_lucario_v3_staged_20260816 \
  --deck-root runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6g-20260816-retry1/kaggle_samrishb_unified_framework_20260816 \
  --deck-root runs/cg-kaggle-kernel-meta-promoted-zacian-staged-20260816/kaggle_sushanth_zacian_heuristic_staged_20260816 \
  --output runs/cg-cross-lineage-epoch7-20260816 \
  --source-epoch cross-lineage-epoch7-20260816 \
  --seed-namespace cross-lineage-epoch7-seed-20260816 \
  --p1-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1
```

以後の新sourceでは、public lineageの単純な交差だけを増やさず、source生成時点の holdout policy lineage分離、複数deck archetype、runtime-safe renderer、screen上位への独立再評価配分を一つの封印済みrecipeとして扱う。同じepoch7 pool・c05・seedのblind retryは行わない。

全artifactはresearch-onlyであり、commit、push、Champion変更、Kaggle提出は行っていない。
