# Self-owned action adapter meta source / P1 CEM (2026-08-15)

## 結論

`Feroz public policy` を直接再利用せず、同一 `option.type` の合法候補だけを決定的に置換する self-owned action adapter を生成した。生成・隔離・smoke・fresh-meta loader の契約は成立したが、今回の3-reference小規模 CEM は更新候補を確定できず、BestKnown は P1＋root deck のまま保持する。

この実験は source-generation の成立を示すものであり、Kaggle score、native leaderboard、提出性能の証拠ではない。全 artifact は `research_only=true`、`local_eval_only`、training/promotion/submission/longrun authority false である。

## 固定 parent と生成方式

- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck canonical SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- base source policy SHA: `ab8563b67b88b3666c2ff9c308505085a84fdac676c194c5b484d8544478c3b2`
- generated adapter policy SHA: `7f51c35ee3d12357a74e35397f80a7fb9a74b449734c62b4b1393c0b4c5d4405`
- generated adapter: `kaggle_ferozahmedds_same_type_adapter_v1_20260815`
- method: `same-option-type-deterministic-action-perturbation`

実装は `src/mage_ptcg/opponents/self_owned_action_adapter_v1.py` と `scripts/generate_self_owned_adapter_meta_v1.py`、pool sealing は `scripts/seal_self_owned_adapter_meta_v1.py` である。生成 package は base policy bytes を self-contained に埋め込み、候補 `type` と同じ `option.type` の未選択 index がある場合だけ hash-seeded replacement を行う。候補集合を越える index、重複、`minCount/maxCount` 違反、private情報・network・future RNG の注入は行わない。

## 契約検証と source sealing

`tests/test_self_owned_action_adapter_v1.py` は 6 passed。検証対象は deck 登録の不変性、単一選択の同一 type 置換、複数選択の件数・一意性・範囲、不正 base action の required-prefix fallback、生成 package の lineage、hash-bound pool の authority である。生成 policy の AST scan は findings `[]` で、imports は shared `cg` と標準ライブラリのみだった。

生成 source の pool は次の順で封印した。

1. `runs/cg-self-owned-adapter-pool-v2-20260815/` を作成。
2. P1 両seat smoke は 2/2 `DONE`、fault 0、draw 0（base seed `20260867`）。smoke summary SHA は `5c45a73d593043c3f5a6b5ee564c3514fde95c28f4bf9f3794e21c4a8bb59917`。
3. `runs/cg-self-owned-adapter-promoted-v2-20260815/` へ fault-free row だけを promotion。pool SHA は `7f76f36343a5e557e3fbfca9f441a9a882488f681d70ca7146be6891b0228a0f`、fresh SHA は `8bd24558399aee0a2078fd239f6ca67a6231256e6b011df7ddf1870dcb1900de`、promoted smoke SHA は `509e7595cc653bef0f5293f9f70346e9a02be50931fe7b4728cf31e89c0187c4`。
4. self-owned freshness evidence を追加し、`build_fresh_meta_batch_v1` が promoted source を受理することを確認した。

## 3-reference pool と split

Feroz source、Prvsiyan v23 source、generated adapter を `runs/cg-public-selfowned-merged-meta-v2-20260815/` に merge した。pool SHA は `90efe8f91164d08ad4720de9cf7f5ad27675dce6c9c4af2192a8700a5af7dc68`、fresh SHA は `c6c281ba16177fae41a0f9a8eef3f20658f02552adc25b6eb0ec8096ac86fd2c`、meta manifest SHA は `65b1a6548a8b521b34be61ddbd73559e512cae6d6d8fbb879725dda65e0903b0` である。`build_fresh_meta_batch_v1` は3 referenceを sorted order で受理した。

研究用 split は `runs/cg-public-selfowned-merged-meta-v2-20260815/cg_historical_split.json`（SHA `5521bd4684ef606fa36bb25d5535daa2e9d25842bdd7038f26d9938c7ef71442`）で、`META_TRAIN=Feroz`、`META_DEV=Prvsiyan v23`、`META_FINAL=generated adapter` とした。CEM検索へ FINAL は渡していない。generated adapter の smoke は runtime 合格であり、performance holdout の未使用性とは別管理である。

## P1 CEM pilot

実行 command は次の通りである。

```text
TMPDIR=/tmp PYTHONPATH=.:src python scripts/run_cg_p1_cem_v1.py \
  --output runs/cg-public-selfowned-cem-v1-20260815 \
  --split runs/cg-public-selfowned-merged-meta-v1-20260815/cg_historical_split.json \
  --source-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --control-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --pool-root runs/cg-public-selfowned-merged-meta-v1-20260815 \
  --generations 1 --all-train-refs --reeval-for-update \
  --reeval-games-per-opponent-seat 1 --positive-delta-gate \
  --campaign-seed 202608152 --population-size 4 --elite-count 1 --execute
```

これは `META_TRAIN` が1 referenceの pilot で、候補1件あたり4 games、screen合計20 games、independent re-evaluation合計4 gamesである。すべて `DONE`、fault 0 だった。

| stage | games | result |
|---|---:|---|
| screen candidate/control | 20 | 10W/10L, score 50.0%, fault 0 |
| independent re-evaluation | 4 | 1W/3L, fault 0 |
| update | — | `incumbent-center`、policy updateなし |

campaign manifest SHA は `2a8e79a834f9e046724e97984918bf8e6dad02cddcf1cd2bc1c9f58b20c44894`、generation manifest SHA は `6cf9b2edbf248a62efd971483dc82e402fe1a4855d8765fe68cdffddf304cb25`、results SHA は `08fd019f3d19e7e9d4a5c3af62c9ff142c5cd21a04f6993a238e8b002785dbfd` である。小標本かつ独立 block の seat collapse が残るため、candidateの fresh DEV/FINAL validation、deck phase、BestKnown loopの性能昇格は開始しなかった。

## 判定と次の条件

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。generated adapter は source-generation regression と loader contract の用途には使えるが、独立 policy lineage とは数えない。次の CEM では clone 数を増やして見かけの sample size を作らず、まず複数の未使用 deck/policy family を追加し、少なくとも従来の固定 CEM budgetに近い TRAIN reference 数を確保する。runtime smoke 候補と性能 holdout を分離し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過した候補だけを `cg_bestknown_loop_v1.py` の policy→deck→policy loopへ渡す。

P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変である。

