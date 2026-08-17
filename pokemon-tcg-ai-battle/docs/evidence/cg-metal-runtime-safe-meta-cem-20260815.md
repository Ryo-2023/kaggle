# cg Metal/Psychic runtime-safe behavior-family epoch j — 2026-08-15

## 結論

Metal/Psychic snapshotの通常 behavior-family（epoch i）は search runtime の timeout で使えなかったため、同じ source identityから search 実行量を構造的に無効化し、rule policyだけを残す4 variantを新しい generator recipeとして生成した。epoch jは両seat smokeを8/8 DONE・fault0で通過し、P1 control固定の risk-aware CEM と未使用 META_FINAL まで完走したが、BestKnown更新には至らなかった。

## source generation / contract

- base source commit: `3f5f71d4ff5923ffafe355a9f2e57fd0b88aa675`
- base source policy SHA: `f6ef16583d322c558d14a546ddf641799070277d1e30d43bafb9c42d89e3c252`
- source epoch: `internal-metal-runtime-safe-behavior-20260815-j`
- seed namespace: `internal-metal-runtime-safe-behavior-seed-20260815-j`
- generated root: `runs/cg-metal-runtime-safe-meta-20260815-j/`
- variants: `RULE_ONLY_PIPLUP_FIRST`、`RULE_ONLY_METAGROSS_FIRST`、`RULE_ONLY_RECEIVER_FIRST`、`RULE_ONLY_LUCARIO_PLAN_FIRST`
- pool SHA: `a4fcee67b39c6abd9f2fca881355544f5a757d82bff294bc2afac902dbfc0019`
- fresh meta SHA: `7947e1c4f95639e92a4cf678a482d6cc6ccf43883597d5517de39dfe4238058e`
- split SHA: `12e0285708d298bbe6a6e37b4721c32c1ad2f8c06f240b65786672f559e721bf`
- canonical deck SHA: `dfdfd61d32d84ee2c181890e79ecea29a280f5636de84d3d8a418e026b5171ef`

各 variant は Metal priority tableの固定変換に加え、`SEARCH_NUM_WORLDS = 0` と `SEARCH_LOCAL_FIXED_BUDGET` default `0.0` を exact replacement した。任意コード rewriteや外部入力はなく、`visible_state_only`、`local_eval_only`、authority falseを保持する。fresh batch／split source verificationはPASSした。

## smoke

P1 `cg-lethal-target-v1`＋root deckを候補に固定し、train splitの `RULE_ONLY_PIPLUP_FIRST` と `RULE_ONLY_METAGROSS_FIRST` を両seat・各2局で評価した。

- 8局: `8/8 DONE`
- fault: `0`
- illegal/timeout: `0`
- P1 outcome: `5W-0D-3L`
- total runtime: 約`5.66s`
- smoke summary SHA: `63c5c53eecaf719e23661d0576ddb9f685dab7dc1b90016581185f5ee1e8d6f4`

## CEM

P1をcontrolに固定し、population 8、elite 2、2世代、screen 144、独立再評価 96（2 block）、fresh DEV 32、合計272局を実行した。全局DONE・fault0である。

- CEM manifest SHA: `0c6ca0d6032dee118d4eb0c269f93e0ddf16a4b24a7b6ac4b297e517a78c4e11`
- gen0 results SHA: `e3662bf3964d9988b0f045430855bb162abd43aea309d6e027f8e12f499f9cb0`
  - screen top: candidate `cg-p1-cem-g00-c07-51238d38c1f0`、差`+12.50pt`
  - independent blocks: `−37.50pt`、`0pt`
  - positive gate不成立、`incumbent-center`保持
- gen1 results SHA: `62386ced86fdc30237f37845b6729c70001ad149ae7aa110227f3ff349594603`
  - screen top: candidate-02／05、各差`+37.50pt`
  - candidate-02 independent blocks: `0pt`、`+25.00pt`、mean `+12.50pt`だが worst `0pt`、seat gap `12.50%`
  - fresh DEV center: candidate `6W-0D-10L` 対 control `7W-0D-9L`、差`−6.25pt`
  - positive gate不成立、`incumbent-center`保持

## fresh META_FINAL

未使用 `META_FINAL` の `RULE_ONLY_LUCARIO_PLAN_FIRST` を使い、gen1 candidate-02を32局（各arm16）で確認した。

- candidate: `6W-0D-10L / 16`（37.50%）
- control: `11W-0D-5L / 16`（68.75%）
- delta: `−31.25pt`
- candidate seat gap: `0%`
- fault: `0`
- decision: `NOT_PROMOTABLE`
- summary SHA: `392d1c27fab0f79cb64adced813909ff640632bcddf258fc960f28fb62b0a76f`
- manifest SHA: `93c1a4a1878483c06e80e900c03b5da7fce222979e30d7c0dacfdb058c90f9b3`

## 判定

epoch jは「新しい source generation methodを runtime-safe にして、既存 `cg_bestknown_loop_v1.py` の前段 CEMへ接続できる」ことを示したが、性能改善は再現しなかった。P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変。candidate packageは診断 artifactとしてのみ保持し、P2/P3やdeck phaseへ渡していない。

次は同じ Metal/Psychic proxyのblind retryをせず、別deck／別sourceまたは、複数の runtime-safe source familyを同時に持つ生成方法へ進む。new sourceが fault0・independent positive・seat-safe を満たした時だけ BestKnown loopの研究 parentへ接続する。
