# cg BestKnown current-state report — 2026-08-15（latest appendあり）

## 結論

現時点で、実CABTの運用基準は self-owned cg P1 `cg-lethal-target-v1` と root deck である。P2は fresh medal 24件と reserve 10件の両方で P1 を再現的に上回らず、BestKnown／Champion／productionへ昇格していない。初版記載後に実施したhistorical／behavior-family source CEMの結果は末尾の最新追記に示す。

## 実体と提出closure

| 対象 | identity | 判定 |
|---|---|---|
| cg P1 policy | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` | self-owned research parent |
| P1/root deck file SHA | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` | 60 cards、P1 packageと一致 |
| P1 archive | `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02` | local archive closure |
| current root `main.py` | `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7` | default Rule-v0 lane。P1 package policyとは別物 |
| current root `deck.csv` | raw SHA `2a541d…` / composition SHA `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` | 60 cards、P1 deckと同一bytes |

`runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1/manifest.json` は archive shape、CG runtime parity、deck/policy hash binding、2局smokeをPASS。`kaggle-cg-safety-gate-p1-8games-20260815.json` は8/8 DONE、fault 0、illegal 0をPASSした。ただし remote Submit verifier／契約がrepoに無く、`submission_ready_candidate=false`、外部提出は未実施である。

## P2 fresh holdout

- fresh medal 24件: P2 `188W-1D-190L-5F / 384`（49.0885%）、P1 `200W-0D-180L-4F / 384`（52.0833%）、差 `−2.9948pt`。fault 9件は`medal_0019_df6f7443`へ集中。
- reserve medal 10件: P2 `76W-1D-83L / 160`（47.8125%）、P1 `78W-0D-82L / 160`（48.7500%）、差 `−0.9375pt`、P2 seat gap 14.3750%。

両結果とも`NOT_PROMOTABLE`で、P2を次のCEM parent、P3、deck phaseへ渡していない。一次artifactはそれぞれ `runs/final-sprint-autonomous/cg-p2-fresh-medal-confirmation-20260815-v1/` と `runs/final-sprint-autonomous/cg-p2-fresh-medal-reserve-confirmation-20260815-v3/`。

## meta freshness

現行 `opponents/pool_manifest.json` はSHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`、102 rows（public 71、internal 31）、smoke-ready 101 rowsで、全て`local_eval_only`である。public smoke-ready 70 IDsは既存`runs/`／`configs/` artifactへ全件出現済みで、fresh・unused・smoke-ready public metaは0件。internalの`water_box_search`／`waterbox_search_v3`等はslow/quarantine扱いで未使用meta gateへ投入しない。

`public_archaludon_cinderace_r7` は smoke false、pool宣言値がraw-byte SHAでcomposition canonical SHAと不一致、かつ過去artifact使用済みのため再利用不可である。

## 研究loopと未完了点

`src/mage_ptcg/meta_specialist/cg_bestknown_loop_v1.py` を追加済み。fresh manifestのpool／policy／canonical deck／未使用証跡／seed namespaceを検証し、`DECK_FIXED_LONG`（policy）→`POLICY_FIXED_SHORT`（deck）→policyを最大8 cycleで進める。fault 0、正delta、seat gap≤5%の`POSITIVE_CONTINUE`だけを研究parentへ渡す。factorial追加後の関連focused suiteは60 passed、py_compile、docs validator、diff-checkはPASS。

次の再開条件は、(1)新しいmeta sourceとfreshness evidence／seed planを固定、(2)P1→policy CEM→独立seed→deck→policyを実行、(3)候補のlocal closureとremote Submit契約を確認、の順である。Champion、production、commit、push、Kaggle submitは変更していない。

## 最新追記 — historical source epoch e / behavior-family epochs f・g（2026-08-15）

同一Starmie履歴からe epochの3 policyをsealし、train-only smoke 4/4 DONE・fault0、risk-aware CEM 152局DONE・fault0、fresh DEV差0pt、candidate-05の未使用FINAL差−12.50ptを確認した。続いて、同じ履歴policyのvisible-state priority tableを4つへ固定変換するbehavior-family generatorを追加し、f epochをsealした。fのtrain-only smokeは8/8 DONE・fault0、CEMはscreen144＋独立再評価96＋fresh DEV32＝272局DONE・fault0。fresh DEVのP1 centerは差+6.25ptだったがseat gap 12.50%でgate外、gen1 candidate-04の未使用FINALは差−6.25pt・seat gap12.50%で`NOT_PROMOTABLE`となった。

両epochともP1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変である。`cg_bestknown_loop_v1.py`のpolicy→deck→policy、deck mutation、commit、push、Kaggle submitは起動していない。

その後、異なるComfey library-out deck／behavior familyへgeneratorを一般化したg epoch、さらにFestival deckのh epochも実施した。gは4 policy、CEM 272局、未使用FINAL各arm16局を全てDONE・fault0で完了したが差0pt・seat gap12.50%で未昇格。hも4 policy、train smoke 8/8、CEM 272局、未使用FINAL各arm16局を全てDONE・fault0で完了した。hのcandidate-05はFINALで`+6.25pt`だったがseat gap`12.50%`で`NOT_PROMOTABLE`。したがってBestKnown更新はなく、次は同じComfey／Festival proxyをblind retryせず、別deck／別sourceのbehavior-familyまたは新しいpermission済みsourceを固定する。詳細は `docs/evidence/cg-historical-meta-source-epoch-e-20260815.md`、`docs/evidence/cg-behavior-family-meta-cem-20260815.md`、`docs/evidence/cg-comfey-behavior-family-meta-cem-20260815.md`、`docs/evidence/cg-festival-behavior-family-meta-cem-20260815.md`。

## 最新追記 — Metal/Psychic behavior-family source epoch（runtime hard-negative）

許可済み `agents/ozawa-metal-psychic-search` の historical snapshot（commit `3f5f71d4ff5923ffafe355a9f2e57fd0b88aa675`）から、visible-state priority tableを4種類へ固定変換するgeneratorを実装し、`runs/cg-metal-behavior-family-meta-20260815-i/` に4件をsealした。全件新規 policy SHA、同一 canonical deck SHA `dfdfd61d32d84ee2c181890e79ecea29a280f5636de84d3d8a418e026b5171ef`、static findings 0、`local_eval_only`。pool／fresh／split SHAは `9cf7c7646ba8aeab4d1fb0165658d08041337df0f4a615bba66eaa656051b58d`／`686a7bb53815b45d93bc1a941e04d0dcbf1d4d22c35e5826ec2e8d26422ec27e`／`8a21f9a24f4cd6eee18df84d1a7e74b359638f58324b1028c0049ccde4a0b930` である。fresh batch／split verificationはPASSした。

一方、P1＋root deckの train smoke は既定環境8局で `1 DONE / 7 fault`、許可済み `SEARCH_LOCAL_FIXED_BUDGET=0.1` でも `6 DONE / 2 fault`、budget `0.0` の4局確認でも `0 DONE / 4 fault`。faultは全て `parent watchdog exceeded game timeout grace` で、元の未変換 Metal/Psychic snapshotでも再現している。source runtimeがbounded gateを満たさないため、CEM・fresh DEV/FINAL・BestKnown更新は未実施である。

Metal/Psychic epoch iは runtime-safety hard-negative として停止し、P1＋root deck、BestKnown、Champion、production、submissionは不変。次は同じsourceのblind retryではなく、search実行量を構造的に上限化した別generatorまたは新しいpermission済みsourceを新epochでsealし、短いfault0 smoke後にのみBestKnown loopへ接続する。一次evidenceは `docs/evidence/cg-metal-behavior-family-meta-20260815.md`。

## 最新追記 — historical source epoch k / cross-source confirmation

first-parent historical snapshotを複数remote refから読み出すsource-acquisition laneで、`runs/cg-source-audit-20260815-k4/` に22件をaccepted、158件をrejectedとしてsealした。pool SHAは`aa3dc3f3e6c3eab8a95aa9a6b0f67c958f245865cf9753cbe35b35a877441ce8`、fresh meta SHAは`2692d8301bb752f0c78190f04142d9519745f37b0e753c810754d5470acb7e55`、split SHAは`a644cedc468dabf75d17243953127beb281002f54e0cc7b6b9573f22ad748513`である。train smokeは8/8 DONE・fault0だった。

P1 control固定のk CEM（population8／elite2／2世代、独立再評価2回、risk-aware gate）は全blockをDONE・fault0で完了した。gen0 screen上位は独立評価で反転し、gen1 candidate-03はscreen`+18.75pt`、独立`+3.125pt`だったが、robust elite不足でcenter（P1）を保持した。META_DEVのcenter差は0ptである。一次artifactは`runs/cg-cynthia-historical-cem-20260815-k/`（manifest SHA `a1bc0d549cdad569625a4d01a3354f2a3955ed71cf8a816541269726f241b7c4`）。

汎用paired fresh confirmation runnerでk candidate-03を複数sourceへ確認した。Cynthia/Alakazam FINALではcandidate `14W-0D-18L`対P1 `13W-0D-19L`、差`+3.125pt`、seat gap`0%`で`PROMISING_CONFIRMATION`。一方、Hydreigon/Comfey derived FINALは`+9.375pt`でもseat gap`6.25%`、Psychic derived FINALは`+4.6875pt`でもseat gap`9.375%`で、いずれも`NOT_PROMOTABLE`である。よって単一FINALの正差を再現性あるBestKnown更新とは扱わない。詳細は`docs/evidence/cg-historical-source-epoch-k-and-crosssource-20260815.md`。

## 最新追記 — behavior-family epochs m–p / source audit l

Alakazam、Psychic、Hydreigon/Comfeyへvisible-state priority変換を一般化し、4 variantずつの新規policy SHAをsealした。mのfresh FINALは`−3.125pt`・seat gap`18.75%`、pの別base candidateはCEM独立で`+18.75pt`だったがfresh FINALで`−6.25pt`・seat gap`6.25%`となった。n/oのk candidate cross-sourceも正差はあったがseat gap gateを通らなかった。全heavy blockはDONE・fault0である。したがってbehavior-family generatorはsource生成・CEM接続方法としては有効だが、P1を更新するcandidateはまだない。詳細は`docs/evidence/cg-behavior-family-alakazam-psychic-hydreigon-20260815.md`。

さらに新しいremote headを対象にした `runs/cg-source-audit-20260815-l/` は`BLOCKED_NO_SAFE_CANDIDATES`（0 accepted／133 rejected）だった。artifact identity再利用とfilesystem-write quarantineを検出したため、追加CABTを起動せず停止した。P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変である。次は現candidateのblind retryではなく、別系統のpermission済みsourceまたは相関を管理した新しいsource-generation recipeをsealし、複数未使用FINALで正差・seat-safe・fault0を同時に確認する。

## 最新追記 — factorial behavior-family v2 epochs t / u（2026-08-15）

新しいmeta sourceの生成方法を優先し、可視状態の独立優先度軸を2つ合成するfactorial recipeを実装した。Alakazam epoch `t`（Cynthia/Garchomp）とComfey epoch `u`（Hydreigon/Deckout）を各4 policyでsealし、fresh pool／fresh meta／custom split、両seat smoke、P1 control固定のrisk-aware CEMへ接続した。実装と結果の一次evidenceは `docs/evidence/cg-factorial-behavior-family-20260815.md`。

Alakazam `t` は8/8 smoke、CEM272局を全てDONE・fault0で完了したが、両世代P1 centerを保持し、META_DEV center差は`−3.125pt`。Comfey `u` も8/8 smoke、CEM272局をDONE・fault0で完了した。gen1 candidate-05は独立2 block各`+25.00pt`だったがopponent seat gap 25–50%で`seat_safe=false`。未使用META_FINAL 64局ではcandidate `13W-0D-19L` 対P1 `17W-0D-15L`、差`−12.50pt`、fault0、`NOT_PROMOTABLE`だった。BestKnown／P1／Champion／production／submissionは不変である。

新remote source audit `r`（0/200 accepted）と`s`（0/48 accepted）も`BLOCKED_NO_SAFE_CANDIDATES`であり、artifact identity／source commit再利用などを検出した。現時点の新source獲得の最優先は、同じfactorial proxyのblind retryではなく、(1)既存identityと重複しない明示許可済みsnapshot、または(2)複数runtime-safe source familyを構造的に生成する方法である。通過条件はfault0、独立positive、seat gap≤5%、未使用fresh DEV/FINALであり、通過時のみ`cg_bestknown_loop_v1.py`へ接続する。

## 最新追記 — stratified behavior meta v2 / v2b（2026-08-15）

cross-snapshot v1のsplit偏りを修正する`stratified_behavior_meta_v2` generator／CLIを実装し、source commit・base candidate・derived policy SHA重複禁止、splitごとの2 family以上、static safety、authority falseを契約化した。v2は12件、CEM screen `288+288`、独立`192+192`、全fault0だったがrobust positive／seat-safe candidate 0件でP1を保持した。詳細は`docs/evidence/cg-stratified-behavior-meta-cem-20260815.md`。

別compositionのv2bも12件を`runs/cg-stratified-behavior-meta-20260815-v2b/`へsealし、全12 referenceの両seat 24局smokeはDONE 24/24・fault0、CEMはscreen `288+288`、独立`192+192`をDONE・fault0で完了した。gen1 screenの最大`+28.125pt`は独立`+12.50pt / −15.625pt`へ反転し、robust positive／opponent×seat safe候補は0件、gen1 DEVはcenter同士差0ptだった。なおsmokeを全pool指定したためMETA_FINAL 2件も投入済みとなり、fresh holdoutとしては無効化した（CEM検索にはFINALを使わず、FINAL identity hitは0件）。P1、BestKnown、Champion、production、submissionは不変である。一次evidenceは`docs/evidence/cg-stratified-behavior-meta-cem-20260815-v2b.md`。

次にsourceを増やす場合は同じproxyのblind retryをせず、fresh holdoutをsmokeから分離した新しいpermission済みsnapshotまたは別recipeをsealし、fault0→独立positive→opponent×seat safe（≤5%）→未使用DEV/FINALの順で判定する。
