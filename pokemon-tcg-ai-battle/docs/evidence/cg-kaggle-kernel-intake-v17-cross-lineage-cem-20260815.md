# 公開Kaggle kernel intake v17 / Lucario cross-lineage CEM（2026-08-15）

## 結論

新しい公開kernel sourceの取得・生成経路は実CABTへ接続できた。Sushanth batch 7件を静的intakeした結果、合法性・entrypoint・source identityを同時に通過したのはLucario-Garchomp policy 1件だけだった。この未性能使用policyを、fault-free確認済みで異なる3つの合法deck parentと `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1` で組み合わせ、3候補の新しいpolicy×deck pairを生成した。3候補は12/12 smoke、fault 0で昇格し、P1固定CEMを2世代実行できた。

最終判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。世代1の新centerは未使用 `META_FINAL` でP1に対して `+12.50pt`（44/64 対 36/64）だったが、candidate seat rateが `0.78125 / 0.59375`、seat gap `18.75%` であり、seat-safe gateを満たさない。P1、root deck、BestKnown、Champion、production、submissionは不変である。

## intake

設定は `configs/meta_specialist/cg_kaggle_kernel_meta_v17.json`、発見rootは `runs/cg-kaggle-kernel-discovery-20260815-m/`。tar SHA、source policy SHA、deck hashは設定と各candidateのevidenceに固定した。intake report SHAは `7fe9648ddfc09b9ce0402985d0e731d7cc25dd85e3c8700f9f6ba121cfd81438`。

| candidate | intake result | 理由 |
|---|---|---|
| `kaggle_sushanth_lucario_garchomp_20260815` | accepted | exact 60、ACE SPEC 1、agent entrypoint、static findings 0 |
| Gardevoir / Hydreigon / Gouging Fire / Dragapult v3 | rejected | `invalid_ace_spec_count`（0枚または複数枚） |
| Venusaur | rejected | `missing_agent_entrypoint` |
| Palafin | rejected | `invalid_deck`（入力headerを含む） |

accepted intake rootは `runs/cg-kaggle-kernel-meta-intake-v17-20260815/`（pool SHA `01455ae117335dbc92059907dea5f912a93cf375016f32301b0b1b0f9a2612beab`、fresh SHA `765677a4a6b2f9ee46e1f18e57f2bbb1b259559b6bf640347435ea16a9608635`）。Lucarioのsource policy SHAは `00ba498d3fb6ced507fbd89ecf67966680a34b168020839931d085386ecbece1`、canonical deck SHAは `f2793dc38cb6e212ae5adcb595c51f2f90d5cc6ae0ba35afd73a7b9bb0ca8868` である。

P1両seat smokeは `runs/cg-kaggle-kernel-meta-smoke-v17-20260815/` で2/2 `DONE`・fault 0（1W-1L）。partial promotion後のrootは `runs/cg-kaggle-kernel-meta-promoted-v17-20260815/`、pool SHA `6f517a9e7f317965fb9f1616edb75e080bf6cc1dcd0ee84440954ea48166e5a0`、fresh SHA `1a15cfffa0e906582b79bd727daeb6d673a528a3ed60c66fadc72737298609f6` である。

## cross-lineage source generation

Lucario policyを次の3 deck parentへ組み合わせた。

- Koushikrudra rear-card: canonical deck `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb`
- Raunak advanced heuristic: canonical deck `e656740ab5d19a958fe1a2d05ca05d49bea09b273a5cb593de5e1d4d9cbb8340`
- Prvsiyan visible-grim v23: canonical deck `606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283`

generated rootは `runs/cg-cross-lineage-meta-v2-lucario-20260815/`。3候補をsealし、pool／fresh／meta／split SHAはそれぞれ `5255d1f62116bae9fbc32bf916da730e8d763829a261558d516f8a54a8155a73`／`e42d452de002fe49d77ef8f725f9fa10bc27ff1f0d90597e3e6d7a0ecf0effe9`／`be3c5d9782cb1a5e3cb153caadd86ef7c5e2bbb2d5888c6b626d6606957b7da6`／`2dcba4334bc3678bd610af56524f8b697f87cdd373360c8122627e739009bd26` とした。

smokeは `runs/cg-cross-lineage-meta-smoke-v2-lucario-20260815/` で12/12 `DONE`・fault 0（7W-5L）。promoted rootは `runs/cg-cross-lineage-meta-promoted-v2-lucario-20260815/`、pool／fresh／split SHAは `6b3d8b771f10f45e4f2ac457d325299f1e8ff0f00fc174e99699b9abf11e3edc`／`ca8a9e281491c62cfadd9c004c94f41c4442540c5c2b58863c0e8a9f60d92324`／`88a87babe0c7553023d1e806158fa505791e321c2f796312a18e9ec092508996`。`build_fresh_meta_batch_v1`相当のfresh bindingはrebind後に検証可能な状態である。

## CEM / holdout

`runs/cg-cross-lineage-cem-v2-lucario-20260815/` をP1固定、campaign seed `202608153`、population／elite `8／2`、2世代、`META_TRAIN+META_DEV` search、独立re-evaluation 1 block、positive-delta gateで実行した。全CABT rowはfault 0で、manifest SHAは `b15ba70da36b3c9955854aeb9252967e2cf1c08bd2f23628608c8368fdad488e`、generation results SHAは gen0 `5d64f9af644d0dfdad85d3519e04ca42204309905eb7f0c921641a6e1f1368ee`、gen1 `3f2431518aab9b2e6b824af7f262fa01c0270cf9b9591d7770cbc9bb7d446eca`。

- gen0: screen上位は見かけ上positiveでも独立で `3/8 対 7/8`、`6/8 対 7/8` へ反転。positive gateによりincumbent center保持。
- gen1: 新centerの独立TRAIN blockは `6/8 対 5/8`（+12.5pt、両seat `0.75/0.75`）。
- gen1の未使用 `META_FINAL` validation（Raunak deck）は `runs/cg-cross-lineage-cem-v2-lucario-20260815/generation-0001/dev/` に保存され、16局では `10/16 対 9/16`（+6.25pt）。
- 独立seedを変えた拡大holdoutは `runs/cg-cross-lineage-holdout-v2-lucario-final-20260815/`。32局/seat、合計64局で candidate `44/64`、control `36/64`、差 `+12.50pt`、fault 0。candidate seat rateは `0.78125/0.59375`、gap `0.1875` で `NOT_PROMOTABLE`。

holdout summary／manifest SHAは `e4f1b107dff2dde1cbbadbbfcc5a6c169096162d1e20ae509587293bedd41f5c`／`867fc086ec84a8a7ffee2bfc7e70cdb1bae5d87118ef962ba8eb27fe5d093c96`。

## 判断と次手

今回、ボトルネックだった「未使用かつruntime-safeなmeta sourceを増やす」経路は成立した。ただし、単一の新policy parentから3 deckへ組み替えたpoolは性能holdoutの相関を十分に下げられず、seat-safeなBestKnown更新には至らなかった。今回の同一Lucario policy×deck pairのblind retryはしない。

次は、(1) ACE SPEC／entrypointで落ちた公開kernelを、元policyを改変せず合法deckへ変換する明示的なdeck-repair adapter、または (2) 新しいpolicy lineageを含む別public source batch、を新epoch・新seedで作る。再開ゲートは `legality → static → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL → cg_bestknown_loop_v1`。全ゲート通過前にP2、BestKnown、Champion、production、submission、commit、pushは変更しない。

