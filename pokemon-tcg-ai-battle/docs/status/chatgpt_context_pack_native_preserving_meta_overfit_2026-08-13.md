# Native-preserving meta-overfit 引き渡し Context Pack（2026-08-13）

作成日: 2026-08-13 JST。これは既存の `docs/status/chatgpt_context_pack_autonomous_meta_finetuning_2026-08-13.md` を上書きしない追加packである。目的は、最新の Strong Asset ranking、失敗した学習分岐、permission/package境界、native-preserving public-state advantage の次期設計、長時間実験の安全ゲートをChatGPTへ一括で渡すことにある。

## 0. 絶対方針と停止事項

最終目的は Rule v0 や Wave6 の改善値ではなく、利用可能な強い `deck + agent` population の共通arenaランキング、各種 BestKnown の確定、そこから native BestKnown を明確に超える提出可能な自前 pair を作ることである。比較の目的関数は常に「現行 `EvaluationBestKnown` を超えるか」であり、Rule v0 は性能比較の基準ではなく安全な package fallback である。

以下はこのpack読後に自動起動してはならない。

- Lucifer hard-label / outcome-weighted hard BC の追加sweep。
- Student v3 θ0/AWR、guarded score-bias、既存 mutation candidate の同型延長。
- CABT、学習、on-policy collection、deck sweep、longrun、package build、Champion変更、Kaggle submission。
- `96` 局の小差だけで `384/768/1536` を飛ばす昇格。

性能runを再開する場合は、ユーザーが明示した新規candidate、native control、common24、両seat、fault-inclusive denominator、seed-disjoint stageを含む新しいrun rootを別途作る。既存artifactは不変とする。現状の実験 authority は全て research-only / false である。

## 1. 現時点の BestKnown 分類

| 区分 | 現在の判定 | 数値 / 根拠 |
|---|---|---|
| `EvaluationBestKnown` | `tomatomato_archaludon` native pair（暫定） | 1107/1536 = 72.0703%、fault 0 |
| `BestKnownArchaludon` | Tomato native（暫定） | native top-3 の点推定首位。Luciferとの差4勝、Plamenとの差5勝で絶対確定ではない |
| `TrainingEligibleBestKnown` | Tomato primary、Lucifer/Plamen control（bounded） | permission-filtered sealed snapshot の範囲だけ。native codeの提出・無制限behavior権限ではない |
| `SubmissionEligibleBestKnown` | Strong Asset poolには無し | Rule v0 + root deck archiveが既存fallback。native source/deckはbundle不可 |
| `GlobalBestKnown` | unresolved | smoke-ready残存asset、R7、permission closure、共通protocol coverageが未完了 |

### 1.1 Native top-3 pooled 1536

一次evidence: `docs/evidence/strong-asset-top3-pooled1536-20260812.md` SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`。evaluator implementation SHAは `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`。

| pair | policy SHA | deck SHA | pooled W/D/L/F | score | seat0 / seat1 |
|---|---|---|---:|---:|---:|
| `tomatomato_archaludon` | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` | 1107/0/429/0 | 72.0703% | 561/768, 546/768 |
| `lucifer19_battlecore` | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | 1103/0/433/0 | 71.8099% | 554/768, 549/768 |
| `plamen06_steel` | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | 1102/0/434/0 | 71.7448% | 567/768, 535/768 |

全4 block × 3 asset × 384局は `DONE`、fault/draw 0。ただしblock首位は変動しており、Tomatoは暫定点推定首位に留める。

### 1.2 「提出候補 top-2」の扱い

性能上の上位2 pair は Tomato native、Lucifer native である。しかし両方とも `local_eval_only` の native `main.py` / deckであり、現在の `SubmissionEligibleBestKnown` ではない。提出候補として記録できるのは、今後これらの許可済み public-state recordsから導出する自前 student + bundle-allowed 自前deckであり、native code・native deckをそのままbundleへ入れてはならない。既存の安全な提出fallbackは Rule v0 + root deck archive（SHA `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`）だけで、Strong Assetより強いことは示さない。

## 2. 実験結果の確定判定

統合scoreboardの機械分類正本は `docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json` SHA `39f76c6474bbf6dbe89d8adf620da92a8cd240487c35c8d4c40637b4afd7023a`、説明資料は `.md` SHA `dc4047a90594d97b6b986c9b93c4a18a1cf756618694a6c647308c48a9e4fd95`。

| candidate / check | 実測 | 判定 |
|---|---:|---|
| Student θ0 | 7/96 vs native 66/96、−61.458pt、fault0 | `NOT_PROMOTABLE`。同型延長停止 |
| Student AWR | 3/96 vs native 66/96、−65.625pt、fault0 | `NOT_PROMOTABLE`。同型延長停止 |
| guarded Tomato score-bias | screen 75/96 vs 66/96（+9.375pt）→ confirm 262W/1D/121L vs 274W/0D/110L（−2.995pt） | `NO-GO`。768未実施 |
| mutation `3f6451` + Plamen policy | 1121/1536 vs Plamen parent1095/1536、+1.6927pt、fault0 | parent-only bounded positive、candidate-only |
| mutation `3f6451` vs Tomato native direct | 274/384 vs 275/384 + draw、score-rate −0.3906pt、fault0 | Tomato BestKnown未超過、停止 |
| mutation `3f6451` + Tomato native policy | 264/384 vs control260/384、+1.0417pt、fault0 | interaction signalのみ。事前+3pt gate未達、停止 |

根拠evidence SHA: guarded `5efd647a94684d94893d022dda0b37e3eaecb66f3168665d6c8c8e297e8e6e48`、mutation pooled `a2db3c4259b34366a9bceced2dbc0ed68abcfa479e24f8064c57bcd61dbe7a78`、Tomato direct `d34c5296a6f64bd6b23e3e80b255d5527b0fbb695923c6787e74d307089dd522`、Tomato-policy interaction `67c59b4300ac75a177da6b2b29c9f3b61805f62ffee55cd8453c8d65e20d1eb1`。一次 summary SHAはそれぞれ pooled manifest `6ac5a4ed93a4214c4cd0e41a37665b64df96b4ba922143fb5e17d39be7325144`、direct `f5a8f077f111881b606821fc312f2aa663fd57394f10724bb131b5e4f87429ba`、interaction `ba6486331ec8171fa9848cd22e792b55496726b2b7e4efd5d1ba7cf897b41e4`。

## 3. Full6 / dynamic curriculum / adapter

### 3.1 Full6

Full6 repairは `ready=false`、published rows 0、primary reproduction incomplete、ordered schema `5:34` 4件とglobal near-duplicate cross 1件が残る。manifest SHA `a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7`、semantic repair SHA `f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2`。evidence `docs/evidence/autonomous-full6-repair-and-dynamic-curriculum-v1-20260813.md` SHA `fd710933e2ec6114a678c416c3980ec1e988eac9682bd70cfb2a8f6a3eae3f5e`。primary raw再導出とordered target materializationが完了するまで、Full6を学習入力・性能根拠・teacher sourceにしない。

### 3.2 Dynamic META_TRAIN iteration-0

manifest `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json` SHA `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a`、semantic SHA `df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4`。META_TRAIN 20 opponents / 80 rowsのみ非zero exposure、META_DEV 0、META_FINAL exposure 0、teacher/behavior eligible 0、authority全false。`local_eval_only` は評価のみで、teacher behavior / labelへ自動転用しない。

### 3.3 Strict outcome adapter re-seal

current manifest `runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/adapter-manifest.json` SHA `0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89`、semantic adapter SHA `6ff323c8ec5cf377f8f2c9c75230416dcbafe9dfab01b801aada557ad6369454`、execution closure `b8fe183f78245bb91a34b440b0087c3bb5f17a65b1b9a1af5d97d629f2cb0de2`、outcome ledger `18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa`。runner source SHA更新を検出後、旧targetは `*-pre-reseal-20260813/` に保全し、同じsourceからatomic re-sealした。80 META_TRAIN rowsだけを出力するresearch/provenance adapterであり、training label / teacher behavior authorityではない。evidence SHA `09c93500045bd25c4266c7e781401057ff4d2173dcef5b9a7232f8623559efc2`。

## 4. 次期設計: native-preserving public-state advantage

既存Student v3を再学習するのではなく、immutable native policyを必ず呼ぶ小さな overrideを設計する。仕様正本は `docs/superpowers/specs/2026-08-13-native-preserving-meta-overfit-design.md` SHA `f55557fdf8d28d2ccbb3745a5c2951a00177d87dbae99e7ebec18c40c87f2967`、実装planは `docs/superpowers/plans/2026-08-13-native-preserving-meta-overfit.md` SHA `bf6ac942cc89d16d7163f99df6777d6ccae44f6f4386b681cf8f284a1e72b95b`。

### 4.1 Public advantage table contract

完了済みTask 1は `src/mage_ptcg/meta_specialist/native_public_advantage_v1.py` SHA `d7abb83b84d1f5d6170581dff8799d5ce8eaa4ab0ec57391ca225c45b6d93e28` として、strict JSONLからcanonical tableを作る。現在の契約は、`state_digest`、`action_key`、`opponent_id`、`seat`、`split`、`outcome`、`weight`だけを受理し、duplicate JSON key、非有限値、private/unknown field、heldout split、未許可opponent、低supportをfail-closedする。action tableはweighted outcome、state baseline、bounded delta、min support、domain-separated SHAを持つ。policy wrapperはnativeを先に呼び、single-choice `MAIN`のみで、tableにsupportがありdeltaが固定marginを超える場合だけnative option list内を候補にする。unknown/malformed/multi-select/ordered/non-legalはexact native fallback。

Task 1 evidence `docs/evidence/autonomous-native-public-advantage-v1-20260813.md` SHA `2cce1a000b62fbadb7a9cb851ffeb6c69785c0e3175c30139fcb95dfe2560f05`。focusedは15 passed、既存adapterは9 passed。これはsynthetic contract完成であり、実performanceを示さない。

### 4.2 未完了の実装plan

- Task 2 `native_meta_overfit_iteration_v1.py` / strict hard-negative iteration adapter: **未実装**。
- Task 3 `native_meta_overfit_alternating_v1.py`: **未実装**。既存 `alternating_meta_optimizer_v1.py` は存在するが新adapter bindingは未完了。
- Task 4 dry-run CLI: **未実装**。
- Task 5 common24 performance screen: **未実施**。
- Task 6 longrun gate integration: 既存longrun contractはあるが、この新規candidateのGO証拠は未成立。

既存の関連実装SHA: `src/mage_ptcg/meta_specialist/native_preserving_adapter_v1.py` `d8a6764e998d160e912d36e8b4e7d32e95b7f5847be0ddbd0fe2d2f2a9ed0464`、`src/mage_ptcg/meta_specialist/alternating_meta_optimizer_v1.py` `15687de5f271e3323297464b33add70a7c250308812eaccff1050b70384b1d47`、`src/mage_ptcg/meta_specialist/longrun_autonomous_v1.py` `cfb4a798b602546697c2bb7f846ee43c094e96a80c1ee853882e1b1680302c0e`、`scripts/run_autonomous_meta_finetune_longrun_v1.py` `72bb8c5c53ab5d4015f7d02b5223995c70c834247b4de6d3b15e1228446aae22`。

## 5. 実装順序と評価ゲート

1. Task 1 reviewを閉じ、table SHA再検証、permission/usage boundary、authority falseを確認する。
2. Task 2でstrict adapterからMETA_TRAIN-only hard-negative weightsを作る。faultは勝ちに数えずreliability penaltyのみ、DEV/FINALはzero exposure。
3. Task 3でpolicy-fixed/deck-fixed alternating state、native control、candidate identity、rollback SHAを束ねる。
4. dry-runでexecutor未起動、既存root不変、all authority falseを確認する。
5. native Tomato固定、common24、両seatで候補/native 96局を一回だけscreenする。
6. 96局で事前+3pt級かつfault0・seat gateを満たした場合だけseed-disjoint 384へ進む。384→768→1536も同じnative controlで行う。
7. candidateがnativeを複数blockで超え、clean META_DEV、package/runtime/legality/rollback closureが揃った場合のみ `LONGRUN_READY` を検討する。

policy updateはnative fallbackを捨てない。まずsingle-choice MAINに限定し、private information、将来乱数、相手手札、deck revealをfeatureへ入れない。CABT legalityはhard truthであり、候補生成で合法手を削除しない。

## 6. Longrun GO / NO-GO

現時点は **LONGRUN NO-GO**。理由は以下である。

- native BestKnown超越を示したcandidateがない（mutationのTomato directは負、interactionは+1.04pt bounded signal）。
- META_DEV clean independent blocksがない。iteration-0はMETA_DEV 0であり、評価gateの代替ではない。
- Full6 primary repair未完了、adapterはprovenance-only。
- Strong Asset native code/deckはlocal-eval-only、package permission / bundle closure未成立。
- candidate checkpoint lineage、portable runtime、qualified self deck、CABT legality/runtime closureが未成立。

既存 longrun implementationは `docs/evidence/autonomous-meta-finetuning-longrun-v1-20260813.md` SHA `a47d3aeeb524f1634180d726cd9b8ba55dcb7f85a08b8ba6d5a8a6515480d8fe` に記録された fail-closed start gateを持つ。`--execute`を直接呼ばず、`LONGRUN_READY` evidenceが明示的に生成されるまでdry-runだけにする。

## 7. Permission / package 境界

監査正本 `docs/evidence/autonomous-submission-permission-package-closure-20260813.md` SHA `ef4eda103ca5b05d30f09193f7bf3fc5c0ca2835776632da543e37c49f373679`。poolの102 assetは `local_eval_only`。native `main.py`、SOURCE.md、外部deckをsubmission bundleへコピーしない。許可済みtraining-local recordsから導出した自前weightは条件付きだが、source code混入なし、decision ref、teacher/data SHA、self-contained runtime、bundle-allowed self deck、CABT legality、latency、fallback/no-private auditが必要である。Plamenについては現行監査でteacher permission manifestのmaterializationが不足するため、新規behavior collectionを始めない。

## 8. Luna-only運用

このpackに基づく通常の調査・実装・artifact整形は GPT-5.6 Luna max を基本laneとする。Solは現状の設計判断には不要で、明示的に昇格されない限り起動しない。別agentを起動する場合も責務を分離し、同一ファイルを同時編集しない。モデル利用は権限付与を意味せず、commit/push/提出/Champion変更の権限は増えない。

## 9. 再現・検証コマンド

長時間runを起動せず、まず次のread-only検証だけを行う。

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
python -m json.tool docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json >/dev/null
TMPDIR=/tmp TEMP=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_public_advantage_v1.py \
  tests/meta_specialist/test_native_preserving_adapter_v1.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py
git diff --check
sha256sum docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json \
  docs/evidence/autonomous-integrated-scoreboard-v1-20260813.md
```

既存性能artifactを読むだけなら次を使う。

```bash
rg -n "EvaluationBestKnown|GlobalBestKnown|LONGRUN|candidate-only|NO-GO" \
  docs/evidence/autonomous-integrated-scoreboard-v1-20260813.{json,md}
```

`scripts/run_*cabt*`、`scripts/run_autonomous_meta_finetune_longrun_v1.py --execute`、Kaggle CLI/APIはこのpackの再現コマンドではない。明示承認・新規gate・別run rootなしに実行しない。

## 10. 未解決点

1. smoke-ready 5 assetsとR7のGlobalBestKnown common-protocol ranking。
2. native behavior/training permissionのassetごとの明示manifest、特にPlamen。
3. Full6 ordered4件とnear-duplicate componentの完全再現。
4. Task 2/3/4の実装とstrict table/iteration/rollback SHA binding。
5. public action keyのstable schemaと実runtime observationの完全対応。
6. bundle-allowed self deck、portable runtime、CABT legality、latency、private-state audit。
7. native BestKnownを複数独立META_DEV blockで+3pt級超えるcandidateの有無。

これらが解消されるまで、現在の最も安全な状態は「EvaluationBestKnownはTomato native provisional、mutationはcandidate-only、GlobalBestKnown unresolved、SubmissionEligible strong assetなし、longrun NO-GO」である。

## 11. Task 1 fix round 1 re-review（2026-08-13）

Task 1のnative public advantage contractは、初回reviewのpermission/hash指摘をfix round 1で反映した。現行module `src/mage_ptcg/meta_specialist/native_public_advantage_v1.py` SHA `dfdcf729debf3699e935412d8fc9f8ed149a90affd8dcbf8e8148a4165293e3d` は、`training_local` / `training_local_and_eval` のusage boundary、`training_allowed=true`、`behavior_allowed=true`、`submission_allowed=false`、manifestのresearch-only/authority falseを要求する。Tableはconstructor/from_dictでcanonical self-SHAを再計算し、entriesとnested coverageをimmutable化する。

新規re-review正本は `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-1-re-review.md` SHA `32395681d9784649b6b4d6757673ce985abd8dbd81616f1a646548599beefcba`。判定は `spec PASS / quality PASS / Critical0 / Important0 / Minor2`。Minorはfrom_dictの未知・省略authority fieldsを安全defaultで受ける点と、parsed Mapping入力でduplicate JSON key情報が失われる点であり、Task 2/4のstrict file loaderで解消可能。fix round 1 focusedは native public advantage + adapter + tuning surface `37 passed`、py_compile、docs validator、diff-checkがGREENである。

このre-reviewは契約層の判定で、性能改善・common24 screen・CABT・training・longrun・package・submissionのGOを与えない。Task 1の性能根拠は依然synthetic fixtureのみであり、native BestKnownを超えた候補は存在しない。

## 12. Task 2 strict hard-negative iteration adapter (2026-08-13)

Task 2は `COMPLETE / RESEARCH_ONLY`。既存のdynamic curriculum、strict common24 META_TRAIN outcome adapter、Task 1 public advantage table、native baseline identityをatomicなiteration manifestへ束ねる契約を追加した。META_TRAIN recordだけをadmissionし、META_DEV/META_FINALはrecord・exposure・quota・weightをゼロ固定する。loss hard-negative 0.40、seat imbalance 0.20、under-exposure 0.15、family diversity 0.15、reliability 0.10のbounded deterministic weightingを使い、opponent capとfamily floor/cap、fault/seat統計、protocol/execution-closure/source SHA/native policy・deck identityを保存する。

一次artifact/evidence: `docs/evidence/autonomous-native-meta-overfit-iteration-v1-20260813.md` SHA `89fda10b744b33b70e22fcdd616be4ba37de97b74b958b1d799258e645b18fa4`。

実装SHA:

- module `src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py`: `33f096b174b055c96b6db1c57f34c08664c1ee01e5f31bc71a34352f76561114`
- CLI `scripts/build_native_meta_overfit_iteration_v1.py`: `d295672400e130e18beea071f46206703b2585b7ab000d31ac0ad33ce65dd68d`
- tests `tests/meta_specialist/test_native_meta_overfit_iteration_v1.py`: `a8a4837461a23c2d0d31fd85c114cab8b8ff9a9eff6b71ac45892ac84d94c737`

検証はfocused 12 passed、dynamic curriculum/common24 adapterとのcombined 20 passed、py_compile、docs validator 13 canonical、git diff --check。CLIはDRY_RUNのみで、`--execute`はexit 2 fail-closed、CABT/training/submission/longrunは未起動。`ready_for_evaluation=false`は意図的であり、package/evaluator/performance gate未成立を示す。Task 2 report SHA `4d6ee4edc49cf624ebe031c4cf1353d0fbc8b9631f99af891ce34136179d718b`、review package SHA `38b727dbe8e2688ba4726d16e2763532f9723dfbf7156f3395e98d2cbd7bc246`。独立re-review待ち。

Task 2は性能改善の証拠ではない。次はTask 3 alternating state bridge、Task 4 dry-run candidate materializerを実装し、その後にのみnative Tomato common24 96局screenを検討する。現状のBestKnown分類（Tomato native provisional、mutation candidate-only、GlobalBestKnown unresolved、SubmissionEligible strong poolなし、longrun NO-GO）は変更しない。

## 13. 最新の完全統合判断（この節を最優先で読む）

このファイルは、ローカルファイルを直接読めないChatGPTへ渡すための単一資料である。前節に古い途中経過が残っている場合は、この節の最新レビュー判定と作業状態を優先する。

### 13.1 Task 2独立レビューで見つかった重要問題

Task 2のproducer検証は focused 12 passed、combined 20 passed、py_compile/docs/diff PASSだった。しかしLuna maxの独立re-reviewで、仕様適合は FAIL、qualityは CONDITIONAL PASS、Critical 0 / Important 2 / Minor 2 と判定された。

レビュー文書とSHA:

    .superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-2-re-review.md
    a5714613191ca10ca09f9050f7d73eefc0dfbf1922790b88446550ea98f134ff

Important I-1（permission再検証不足）:

- native_meta_overfit_iteration_v1 の weighting 経路が、META_TRAIN entryについて training_exposure_allowed、teacher_behavior_allowed、usage permission を再検証していない。
- META_TRAIN split名だけではtraining permissionを意味しない。permission flagsがfalse、またはlocal_eval_onlyに近い不適切なrowを、weight=1.0のsampling inputとして受理できる反例がある。
- training-local、training_allowed、behavior_allowed、research_only、authority falseを全て閉じる必要がある。
- 修正までTask 2 manifestを学習、behavior collection、longrunへ接続しない。

Important I-2（atomic writer競合）:

- 存在確認から os.replace までの競合窓で、別プロセスが同じ出力を作った場合に既存artifactをclobberし得る反例がある。
- 単独実行では新規writeとして動作するが、atomic new-write契約としては不十分。
- destinationが存在したら失敗する、またはexclusive create方式で競合時に既存bytesを絶対に置換しない実装へ修正する必要がある。

Minor:

- parsed Mapping入力ではduplicate JSON keyを後から検出できない。ただしstrict file loader側のduplicate rejectionは別にある。
- authority fieldの省略をsafe defaultで受ける経路がある。research-only用途では拒否へ寄せる方が強い。

この再レビューにより、Task 2はproducer tests greenからpermission fix待ちのREVIEW_BLOCKEDへ戻る。Task 3 bridge、性能screen、AWR、longrunを現実装へ接続してはならない。

### 13.2 現在の実装状態

| task | 状態 | 次の条件 |
|---|---|---|
| Task 1 public advantage | review PASS | 性能証拠は未取得 |
| Task 2 hard-negative adapter | producer COMPLETEだが独立review FAIL | I-1 permission再検証、I-2 non-clobber、再review PASS |
| Task 3 alternating bridge | Luna maxで実装中/未完了 | Task 2修正済みinputをbind |
| Task 4 dry-run materializer | 未完了 | Task 3 state/rollback完了 |
| Task 5 common24 96局 screen | 未実施 | Task 2/3/4のstrict gate後 |
| Task 6 longrun | 既存contractはあるが新candidate未接続 | NO-GO |

性能プロセスは現在起動していない。Task 2 review修正とTask 3 TDDだけを進めている。

### 13.3 Native基準と候補比較

現行native common24 pooled1536:

- Tomato: 1107W/0D/429L/0F = 72.0703%
- Lucifer: 1103W/0D/433L/0F = 71.8099%
- Plamen: 1102W/0D/434L/0F = 71.7448%
- protocol: 24 opponents、両seat、4 independent 384 blocks、fault-inclusive denominator
- native evidence SHA: e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863

Student v3:

- θ0 7/96 = 7.2917% vs native 66/96 = 68.75%、−61.458pt
- AWR 3/96 = 3.125%、−65.625pt
- fault0でも性能NO-GO。同じ型の学習を長時間化する根拠なし。

Guarded score bias:

- 96: candidate75/96、native66/96、+9.375pt
- 384: candidate262W/1D/121L、native274W/0D/110L、−2.9948pt
- 96局の見かけの正差は採用不可。

Plamen mutation 23-opponent parent-relative:

- candidate1101W/1D/370L/0F = 74.8302%
- parent1072W/0D/400L/0F = 72.8261%
- +2.0041pt、4 block全てcandidate-positive
- ただし23 non-self opponentであり、native common24 rankingとのglobal比較不可。

Plamen mutation common24:

- block1 277/384 vs parent268/384 = +2.3438pt
- block2 274/384 vs parent279/384 = −1.3021pt
- block3 288/384 vs parent260/384 = +7.2917pt
- block4 260/384 vs parent282/384 = −5.7292pt
- pooled candidate1099/1536 = 71.5495%
- pooled parent1089/1536 = 70.8984%
- pooled +0.6510pt、Tomato native72.0703%未満、candidate-only

Tomato direct:

- Plamen policy + mutation deck: 274/384 = 71.3542%
- Tomato native: 275/384 = 71.6146%（draw1、score-rate 71.7448%）
- −0.3906pt、second block停止

Tomato policy interaction:

- Tomato policy + mutation deck264/384 = 68.7500%
- Tomato native260/384 = 67.7083%
- +1.0417pt、事前+3pt gate未達、second block停止

### 13.4 Permissionと提出可否

- pool_manifest 102件は全て local_eval_only。
- native main.py、native deck、SOURCE.mdのas-is packageはNO-GO。
- TrainingEligibleはTomato primary/Lucifer controlのbounded training-local snapshotのみ。
- Full6の6 teacher catalogはintegrity PASSだが、結合datasetはordered4件とnear-duplicate crossでblocked。
- Rule v0 + root deckだけが既存package anchor。
- Rule v0 archive SHA: da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a
- Strong Assetから自前studentを提出するには、自前bundle_allowed deck、teacher provenance、portable runtime、dependency/allowlist、CABT legality、latency、fallback、privacy、clean-room、checkpoint closureが必要で、現時点では未閉鎖。

### 13.5 Full6とTomato clean lane

v2b catalog:

- file SHA 8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4
- semantic SHA da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e
- 6 teachers READY、96/96、fault0、seat48/48

Full6 blocked:

- 36,684 decisions
- unordered supported36,680
- Grim ordered schema5:34が4件
- non-ubiquitous near-duplicate cross 1 component、ID prefix 5a996ab25264...
- blocked descriptor SHA a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7
- semantic SHA f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2
- published rows0、ready=false、silent_drop=false

Tomato bridge/dataset:

- bridge file SHA 8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983
- bridge semantic SHA 3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0
- GPU manifest SHA 67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518
- dataset semantic SHA 351459083349917faf3b30384506849be0493de9996a4a4afa043c8f646626b5
- train/validation/test = 3623/486/1001、ordered0、unsupported0、leakage0

AWR sidecar:

- weights file SHA 63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229
- exact train rows3623 every/only
- raw mass3987.33142044282、ESS2804.4098482164172
- authority false、学習未起動

### 13.6 完成までの機械的手順

1. Task 2 I-1/I-2をLuna maxで修正し、reviewerが仕様適合PASSを再確認する。
2. Task 3 alternating state bridgeを、修正済みTask2 manifestとnative controlへstrict bindする。
3. Task 4 dry-runで新run root、候補identity、rollback、source SHA、process absenceを確認する。
4. Tomato native vs candidateをcommon24 96局で一回だけscreenする。
5. 96局の分岐:
   - nativeより10pt級弱い: 384へ進まず、そのpathを主線から外す。
   - native近辺/AWRのみ悪い: θ0/value/overrideへ戻る。
   - +3pt級でfault0/seat gate: seed-disjoint384へ進む。
   - 小差、反転、fault、protocol mismatch: candidate-only停止。
6. 384が再現的に+3pt以上なら768/1536へ進め、clean META_DEVで確認する。
7. package/permission/rollbackが揃うまでlongrunはNO-GO。

### 13.7 重要な非主張

- Task 1/Task 2のgreen testは性能改善ではない。
- synthetic GPU probeは競技性能ではない。
- 23-opponent mutationは24-opponent native rankingを置換しない。
- parent-relative +2.0041ptはTomato BestKnown超過ではない。
- local_eval_only nativeは提出可能pairではない。
- ready_for_evaluation=falseを人為的にtrueへ変えてはならない。
- fixed datasetを長く回すだけでは、ユーザーが求めるdynamic meta-overfit longrunではない。

### 13.8 最新の意思決定

EvaluationBestKnown = Tomato native provisional。  
BestKnownArchaludon = Tomato native provisional。  
TrainingEligibleBestKnown = Tomato primary / Lucifer control bounded。  
SubmissionEligibleBestKnown = Strong Asset poolなし、Rule v0 root-deck anchorのみ。  
GlobalBestKnown = unresolved。  
Task2 = 独立reviewでpermission/atomic writer blockerが見つかったため修正待ち。  
Task3 = Luna maxで実装中。  
Longrun = NO-GO。  
性能run/CABT/training/submission = 未起動。

この節と、それ以前の全節を合わせた本ファイルを単独でChatGPTへ渡せば、ローカルファイルを参照できなくても、現在の判断・反証・次の実行条件を再構成できる。

## 14. 最新方針補正後の実性能ループ（2026-08-13 追補）

この追補が従来節より新しい正典である。ユーザーの補正指示により、Task1〜4、B collector、Full6 descriptorの一般的なhardening/reviewを主目的へ戻さず、具体的な性能実験をblockingする再現可能bugだけを直す。目標は「強い既存deck/agent populationを初期資産として、上位metaへself-owned policyとdeckを交互最適化し、native BestKnownを超える提出可能pairと継続改善longrunを作る」ことのままである。

### 14.1 behavior permissionの一回限りの決着

repo全体を再走査した結果、現時点で実行可能な native behavior/self-rollout 候補は0件である。これはコード不足ではなく権限境界である。

| 資産 | 観測された権限 | 性能利用の扱い |
|---|---|---|
| Tomato/Lucifer/Plamenおよびpool 102件 | `usage_boundary=local_eval_only`。一部のteacher-derived weightはtraining-localのみ。`behavior_policy_allowed=false`、`teacher_behavior_labels_allowed=false` | opponent/evaluation control/meta情報としてのみ利用。behavior sourceへ昇格不可 |
| Tomato teacher snapshot | permission `allowed_usages=[training-local]`。behavior/self-rollout許可なし | public/action-conditioned teacher labelには使わない |
| Full6 unordered population | training-localは一部許可、ordered 4 quarantine、near-duplicate component未閉鎖、behavior ready=false、published rows=0 | critical pathにしない。behavior sourceとしてはNO-GO |
| Rule v0 + root deck archive | package/legalityのlocal anchor。pool rowは`LOCAL_EVALUATION`。archive/qualificationにpolicy ownership/self-rollout grantなし | self-owned package-only fallback。既存archiveのローカル検証は可能だが、native behavior permissionの代用ではない |
| O6 historical internal records | 一部team-internal `training_data_generation`等の記録はあるが、現行Tomato/Rule v0 source・permission・identityへの束縛なし | 現行behavior routeへ転用不可 |

したがって `owned_policy=true`、`explicit_self_rollout_allowed=true`、`behavior_allowed=true`を自己申告してはならない。再開条件は、project owner/issuerが policy SHA、deck SHA、clean source/archive、projection manifest、pool/scheduleへ束縛した明示的な self-owned/explicit-self-rollout attestationを発行すること。権限が出ない限り、nativeの行動ログを学習入力へ流す経路は閉じたままにする。

### 14.2 添付方針に従った最初のfresh性能測定

権限を拡張せず、現行のsubmission-compatible Rule v0を同一のbroad common24で直接評価した。これはbehavior learningではなく、self-owned policyの現状点と、次にparameterized direct optimizationへ進むかを決めるscreenである。

再現コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_performance_first_arena_v1.py \
  --opponent-ids aman_crustleaware_fighting,aristophanivan_multiply,aristophanivan_probabilistic,biohack44_crustlecounter2,dashimaki360_crustlecounter,ferozahmedds_solution,harukiharada_crustle,itsuki9180_lucario_jp,kiyotah_abomasnow,kiyotah_dragapult,kiyotah_iono,kojimar_lucario,kokinnwakashuu_lucario_search,lucifer19_battlecore,masamikobayashi_garchomp,medal_0001_77a53ffc,naoto714_kangaskhan,naoto714_slowking,naoto714_ursaluna,official_random,pilkwang_lucario_alakazam,plamen06_steel,prvsiyan_grimmsnarl,rauffauzanrambe_advanced \
  --output runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1 \
  --games-per-seat 2 --base-seed 14900000 --workers 16 \
  --worker-recycle-games 32 --timeout-seconds 600
```

一次artifact:

- run root: `runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1/`
- manifest SHA-256: `9f76ba6a15e5024b9cbc4ba89a1d69f6393d4f538097ab7f336614fe673a9d15`
- summary SHA-256: `916e2223803ea54b3b3ddd3403c398436723a04f7e38ddbcc81af6d5f388f11a`
- ledger SHA-256: `91190a18ebce76f0e7d6597f872ad07f47ba168226831c2fcd47ac1d9d6ca3cf`
- evaluator SHA-256: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- runner SHA-256: `bf1d325cae93874aa0878a6b4c3f1abadbcd4e4143ca077692e4e9fef42f08c6`
- root policy closure SHA-256: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- deck SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- broad config SHA-256: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- pool manifest SHA-256: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

結果は **11W/0D/85L、96/96 DONE、fault 0、score rate 11.4583%**。seat0は8/48、seat1は3/48で、seat差は大きい。過去の同系統screen（12局で8.33%/0%）と方向が一致するため、偶然の一回だけでなく「現行Rule v0はこのbroad poolに対して非常に弱い」という診断材料になる。ただしこの24-ID poolはnative Tomato 72%級のcommon native controlとは同一populationではなく、native BestKnownとの直接差をこの11.46%だけから断定してはいけない。先にこの同一poolに対する候補とcontrolを揃える必要がある。

### 14.3 現在の性能判断

現時点の結論は「行き詰まり」ではなく「permission blockerは確定し、次の性能実験の実装境界も具体化した」である。ただしRule v0のbaselineは弱く、同じRule v0を長時間学習する根拠はない。

既存コードの状態:

- `scripts/run_performance_first_arena_v1.py` はroot Rule v0をnative poolの24 IDへ接続でき、今回96局で実証済み。
- `src/mage_ptcg/optimization/outcome.py` の`PolicyParameters`/CEM/`ProposalMixtureController`はself-owned parameterized policyだが、対戦相手はsynthetic rule/familyで、native poolへ接続されていない。
- root `make_rule_agent`へ渡せる安全なparameter surfaceは、hash-bound KnowledgePackによるRule v0の同点tie-breakだけ。合法候補を増やさず、異なるRule scoreを逆転させない。
- `run_native_policy_candidate_pilot_v1.py`はpool内native asset向けで、root Rule v0 candidateをnative poolへ評価するbridgeではない。
- 既存のTomato/Plamen mutationはnative policyを固定したdeck raceであり、self-owned Rule v0のpolicy最適化結果ではない。

従って次の実装は、production `main.py`/`agents/rule_agent.py`を変更しない新規research-only bridgeとする。bridgeは次を必須にする:

1. immutable/hash-bound KnowledgePackまたは同等のself-owned tie-break config。
2. root policy closure、root deck、pool manifest、broad config、evaluator、seed scheduleをmanifestへ束縛。
3. baseline packなしと2〜3のtie-break候補を同一common24×両seat×2でscreen。
4. per-game status/fault/seat/opponent/seedを保存し、candidate-only研究証跡として扱う。
5. behavior permission、teacher labels、submission promotionをfalse固定。

96局で明確な改善がなければ384へ延長せず、このpolicy pathを停止する。改善が小さくてもfault0・seat崩壊なし・override/supportが十分なら384へ進む。384でnative controlに対しておおむね+3pt以上が再現すれば`LONGRUN_READY_CANDIDATE`とし、768/1536を必須待機条件にしない。現時点ではcandidate bridge未実装、384未起動、longrun NO-GOである。

### 14.4 deck raceとFull6の扱い

deck mutationは並列に継続できるが、既存mutation結果はcandidate-onlyであり、submission-ready pairではない。Plamen common24 pooled1536はcandidate 71.5495% vs parent 70.8984%で+0.6510pt、Tomato native 72.0703%未満。Tomato policy+mutationは384で68.75% vs native control67.7083%（+1.0417pt）だが事前+3pt gate未達で停止済み。既存score-biasは96で+9.375pt、384で−2.9948ptのため再利用しない。

Full6 unorderedはraw36684、unordered36680、ordered4 quarantine、near-duplicate component未閉鎖、published rows0、ready=falseのまま。critical pathの第一candidateをFull6完成待ちにしない。

### 14.5 ChatGPT向け判断要約

現在の選択肢は次の順序である。

| 分岐 | 状態 | 次の行動 |
|---|---|---|
| native behavior permission | 0件 | 自己申告で昇格せず、`NATIVE_BEHAVIOR_PERMISSION_BLOCKED`を維持 |
| Rule v0 common24 baseline | 11/96、fault0、seat差あり | 長時間化しない。tie-break bridgeを1回screen |
| tie-break candidate | bridge未実装 | 既存KnowledgePack契約を再利用し、新規research-only runnerを作る |
| real advantage/value | native behavior権限なし | self-owned rolloutのpublic state/action/outcomeのみ。synthetic tableは使わない |
| deck optimization | candidate-only既存結果あり | root/bundle-compatible deck routeを並列で継続可能 |
| package | Rule v0 root archiveのみlocal package GO | native as-is/derived strong asset/Kaggle external submitはNO-GO |
| longrun | 未開始 | tie-break/直接最適化が384でpositiveになるまでNO-GO |

### 14.6 次に最大の性能情報を得る最短手順

1. Luna maxでresearch-only tie-break bridgeをTDD実装する。
2. baseline packなし・candidate 2〜3個を同一96で実測する。
3. 11.46%から改善しない場合はこのpolicy routeを捨て、deck mutationまたは明示permission取得へ時間を移す。
4. 改善する場合だけ同じscheduleの384を一回回す。
5. 384で+3pt級、fault0、seat collapseなしなら初期longrun候補に昇格し、hard-negative/meta-weight更新を接続する。

この追補が、従来のStudent v3/AWRやTask1〜4の契約成果を否定せずに、ユーザーが要求した「契約整備から実性能ループへ直行」した現在の判断材料である。

## 15. fresh baseline evidence pointer

baselineの独立evidence文書は `docs/evidence/autonomous-self-owned-rule-v0-common24-baseline-20260813.md`（SHA `68ec40f7fee034b24799371c4de84b1860ecf6510c75a1d9bc0bba0d1209f8d4`）である。ここに再現コマンド、24 opponent ID、root/evaluator/pool/config SHA、96局summary、opponent別内訳、解釈と384 gateを固定した。ChatGPTへ渡す際は本ファイル単独で判断できるが、baseline節の一次証拠としてこのSHAも併記する。

## 16. 方針補正後のself-owned性能screen（最新）

### 16.1 Rule v0 broad baseline

behavior/self-rollout permissionの網羅監査は完了しており、native strong assetsは`local_eval_only`、teacher系はtraining-localのみ、Rule v0 rowは`LOCAL_EVALUATION`である。自己申告でbehavior権限へ昇格せず、`NATIVE_BEHAVIOR_PERMISSION_BLOCKED`を維持したまま、submission-compatible root Rule v0の直接local evaluationへpivotした。

fresh baselineは `runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1/`。common24 24 opponent × 両seat × repetition2 = 96局、base seed `14900000`、96/96 DONE、fault0/draw0、11W/85L、score `11.4583%`、seat0=8/48、seat1=3/48。manifest SHA `9f76ba6a15e5024b9cbc4ba89a1d69f6393d4f538097ab7f336614fe673a9d15`、summary SHA `916e2223803ea54b3b3ddd3403c398436723a04f7e38ddbcc81af6d5f388f11a`、ledger SHA `91190a18ebce76f0e7d6597f872ad07f47ba168226831c2fcd47ac1d9d6ca3cf`、evaluator SHA `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`。このpoolはnative Tomato 1536局の72.0703%と別populationなので、11/96からnativeとの差を直接主張しない。

### 16.2 self-owned policy bridge

新規research-only bridge `scripts/run_rule_v0_knowledge_pool_screen_v1.py` はproduction `main.py`/`agents/rule_agent.py`を変更せず、root Rule v0の同点KnowledgePackまたはpublic action-type bounded deltaをnative poolへ接続する。candidate manifestはroot policy closure、root deck、pool/broad config/evaluator、seed/common24、pack/action identity、`local_eval_only`、research-only、authority falseを束縛する。teacher labels、native behavior source、submission promotionは使用しない。bridge SHA `77cc7eb0f802a5d1dc3ceeb32ae96ed29dfdc93c68e75fcade1fb1a06e4c9970`、tests SHA `24eb14555b4b5dd577b1258bee1bb9289e1ab5a4f5d99b5bdf37c683bc0d8eda`、初期evidence SHA `bb3778d5b4c86f762e171c9d23a911f6aa4ab0ac8fd7cb0c2f624646b95a5d19`。

seed `14910000`の初回screenは以下だった（全arm 96局、fault0）。baseline-no-pack 3/96、play-minus 11/96、play-plus 11/96、attack-plus-200 11/96、play-minus-200 10/96。ただしbaselineを別fresh block `14900000`で再実行すると11/96で、96局point estimateの差だけでは候補昇格不可だった。

同一base `14900000`へmatched再配置したrunでは、baseline14/96、play-minus9/96、play-plus10/95/F1、attack-plus-20015/95/F1、play-minus-20010/96となった。play-plusの先頭aman seat0 seed14900000、attack-plus-200のaman seat1 seed14900003で`DeckValidationError: deck must contain exactly 60 cards, got 0`が発生したため、このmatched runは`SCREEN_INVALID`として保全する。

原因切り分けとして、同候補の先頭4セル（aman、両seat、repetition2、seed14900000..03）を、単一worker direct callおよびparallel evaluator workers=1/2で各5回、合計80ゲーム再実行した。全80/80 DONE、fault0で、候補factory/pack/action-delta単体ではfaultを再現しなかった。したがって元の2件は候補の性能ロジックからは再現不能なworker/importまたはartifact状態の一過性異常と分類する。ただし、再測定でfault0を確認するまで候補をpromotableとはしない。

### 16.3 root deck mutation

root Rule v0の1-card mutation（1252→6）を、同一common24/seed schedule `18000000`、root parent・mutant・Tomato native control各96局で実行した。run root `runs/final-sprint-autonomous/root-deck-mutation-v1/common24-96-20260813-retry-v1/`、288/288 DONE、fault0/draw0、game ID unique、seed schedule一致、authority false。parent10/96=10.4167%、mutant9/96=9.3750%、Tomato control68/96=70.8333%。mutantはparent比−1勝/−1.0417ptで、384へ延長せずcandidate-only。summary SHA `9a34b6fa80ceabd27b98201c5cb48bab0ae3e67afdd7b3683fb37a5aef605ad3`、manifest SHA `ef765f55ee9153ddedeca0e0ab4f1140a5771410da9adb7bb7a4af1fcf5f530c`、evidenceはrun root内 `evidence.md` SHA `243ad80d...`。root parent/mutantはbundle-compatible self-owned側、Tomato controlはlocal_eval_onlyであり、submission permissionやbehavior permissionを意味しない。

### 16.4 current decision and next gate

現時点で`LONGRUN_READY_CANDIDATE`は成立しない。Rule v0 baselineは弱いがfault0、deck mutationは負、policy screenはseed varianceと一過性faultを含み、384へ進む根拠がない。次は同一base `14900000`でpolicy full screenを一度だけfresh root・fault0条件で再測定する。そこで候補が明確にpositiveかつseat collapseなしなら384へ進める。候補が非positive、またはfault再発ならこのtie-break/action routeを停止する。

### 16.5 serial fault-free re-screen and 384 gate

同じbase `14900000`・同じcommon24・同じ96セルをworkers=1のfresh root `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-matched14900000-serial-v1/`で再実行した。全5 armが96/96 DONE、fault0だった。baseline-no-pack 12W-1D-83L（13.0208%）、play-minus 18W-0D-78L（18.7500%）、play-plus 13W-0D-83L（13.5417%）、attack-plus-200 8W-0D-88L（8.3333%）、play-minus-200 8W-0D-88L（8.3333%）。root summary SHA `28e31a1cc3f8c8f5b64b86817da0ee031e69db7dcf47611d6e6e17fadf55f096`。

同じ`(opponent, seat, repetition)`でbaselineとpaired比較すると、play-minusはloss→win14、win→loss8、draw→win1でnet +6 wins。play-plusはnet +1、attack-plus-200はnet −4、play-minus-200はnet −4だった。従って、次の384 screenへ進める候補はplay-minus（KnowledgePack PLAY tie score -2.0）のみとする。他deltaは停止する。384は同じcommon24・両seat・repetition8、baseline controlと同一seed universe、workers=1、fault-inclusive denominatorで行い、約+3pt以上・fault0・seat collapseなしなら`LONGRUN_READY_CANDIDATE`候補、未達ならこのtie-break routeを停止する。384はlongrun開始やChampion変更を自動的に意味しない。

### 16.6 play-minus 384 confirmation result

play-minusとbaselineを、同じcommon24、base seed `14900000`、両seat、repetition8で各384局、workers=1として実行した。fresh root `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-play-minus-384-14900000-serial-v1/`、全768局 DONE、fault0、draw0、summary SHA `d79aba2e9b8237813cdc5a4306da83e519fe42533f077a6ddaa1398e870ea05d`。baseline 43/384（11.1979%）、play-minus 41/384（10.6771%）、候補は−2勝/−0.5208pt。seat0は22/192→21/192、seat1は21/192→20/192。pairedはcandidate loss→win30、win→loss32、net−2勝だった。

96局serialのnet +6は384局で再現しなかった。従ってplay-minusは`NOT_PROMOTABLE`、`LONGRUN_READY_CANDIDATE`ではない。768/1536、longrun、Champion変更、submissionへ進めず、tie-break/action routeを停止する。古いscore-bias/residual/Student v3/AWR/BCへ戻らない。一次evidenceは `docs/evidence/autonomous-self-owned-rule-v0-play-minus-384-20260813.md`（SHA `d30d0f2f273fbba34a15c6fbbbbf3bfa97e9d4940b2dcfeed12bd368e73a4ee0`）。

古いStudent v3 θ0/AWR、Lucifer hard BC、UniformLegal、alpha=1、residual/sweep、score-bias、NLL-only full studentへ戻らない。Full6 unordered、B collector、submission closureは並列研究・package証跡として保全するが、第一性能candidateの前提にしない。性能/CABT/training/longrun/submission/Champion変更は未成立である。

### 16.7 最新deck role-surface screen（2026-08-13）

既存評価済み候補とはdeck multiset SHAが重ならない、Plamen parent向けの新規1-card swap 3件を、同一common24・両seat・repetition2の96局でscreenした。各候補はPlamen parentと同一policyのcandidate、同一policyのparent、Tomato native direct controlを比較し、全armで同一seed schedule・24 opponent・seat48/48を使用した。

| candidate | deck change | Plamen parent | Tomato native control | 判定 |
|---|---:|---:|---:|---|
| `role-4de54a1ed7be9e9b` | `1097→1118` | 78/96（parent比 +15勝） | 63/96 vs Tomato 70/96（−3勝） | candidate-only |
| `role-0cf624203dba440e` | `8→1159` | 68/96（parent比 −6勝） | 74/96 vs Tomato 69/96（−3勝） | candidate-only |
| `role-e3098bb5cbbda4e6` | `1244→1246` | 66/96（parent比 −4勝） | 70/96 vs Tomato 65/96（−3勝） | candidate-only |

全3候補・parent/controlを含む全1,152局でDONE、fault0、seat48/48、paired seed strata一致、authority false。Plamen parent比で一見+15勝の候補もTomato control比では負であり、native BestKnownを超えた根拠にはならない。3候補とも384、768、longrunへ進めない。新規artifactは `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v1/`、manifest SHA `110c8ac344125359ab9dc8611f7e514ad9fd6cd2a8ec64e98c4b404bb8624265`、統合JSON SHA `075988a4cd5556235a9c6bdb7d42456660d436826d9383a88148cc20d19aed84`、統合MD SHA `7e380f80cf774df3afb223a13ef8af6eb5410aadae9c70110ddb926937d4662f`。

### 16.8 最新実装方針：self-owned public rollout

native behavior permissionが無いことを理由に性能作業を止めず、self-owned Rule v0自身が実際に対戦したpublic rollout/outcomeだけを候補生成に使う。native opponentの行動をteacher labelとして保存・学習しない。1局の接続確認では、Rule v0 vs Tomato nativeが95 raw steps、DONE/DONEとなり、既存のpublic allowlist投影が94 events、84 action events、terminal event付きで通過した。

次に作るresearch-only artifactは、public state digest、合法action type/action digest、chosen action、terminal outcome、opponent/seat、policy/deck/evaluator identityだけを保存する。private hand/deck/prize、logs、hidden state、native behavior labelは保存しない。候補はRule v0を先に呼び、bounded public action overlayを適用し、未知・不正・multi-select・ordered selectionはexact baselineへ戻す。同じcommon24・同じseed scheduleでbaseline/candidateを96局screenし、fault0・seat非崩壊・paired positiveがある場合だけ384へ進む。synthetic Task1 tableは性能根拠に使わない。

実装計画は `docs/superpowers/plans/2026-08-13-self-owned-public-performance-loop.md` に固定した。実装・96局・384局・deck raceはすべてresearch-only、既存production/既存artifact/Champion/submissionは不変である。

### 16.9 real self-owned public rollout common24 96（最新）

方針補正のcritical pathに従い、root Rule v0自身をsubjectにしたreal public rolloutをcommon24で実行した。24 opponent × 両seat × repetition2、base seed `14900000`、requested/completed 96/96、全局`DONE`、fault0、draw0、結果11W/0D/85L。native opponentの行動はteacher labelとして保存していない。private hand/deck/prize、raw observation、logs、hidden stateは保存せず、公開trajectory projection、state/action digest、chosen action type、terminal outcome、opponent/seat、policy/deck/evaluator identityだけを保持した。engine seed capabilityは検証済み`ENGINE_SEED_UNSUPPORTED`である。

実rolloutの一次rootは `runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/`。主要SHAは次の通り。

| artifact | SHA-256 |
|---|---|
| `source-manifest.json` | `3e56a3911367cbcc53436c883371d6f1ff1ba169c8ecd1dc3162c6570b31e388` |
| `public-outcome-records.json` | `c78e5666acd697482dcdafa1bb59b814a9cecd99c80e24d76c83d22d56d221b2` |
| `rollout-summary.json` | `1b9b1e603d90b7f885e141cb666299a6c74042b4c83322e232cc8e4d33b6075c` |
| `action-outcome-table.json` bytes | `105b3f7924f86ad2fc48eaf270ff1de817bd0b0b60ebd0ceb5bbbe91db385ad2` |
| table embedded `table_sha256` | `822eda2d50a66119dd6cf07d7ef318cd430fce350643340ababce14e97f4e7f0` |

source manifestはcommon24 IDs、games per cell、records SHA、root policy/deck、pool manifest、evaluatorをstrict bindし、research-only/teacher_labels_used=false/private_state_used=false/authority falseを固定した。4局smoke tableは性能根拠に使わず、common24 tableだけを候補screen入力として許可する。

一次実行では`select.option[*].toolIndex`がprojection allowlistに無く3局がfailした。これは実性能sourceを阻害する再現可能なprojection bugだったため、`toolIndex`を認識するがpublic payloadへ転送しない最小修正を実施した。修正前rootは保全し、RED→GREEN regressionを追加。projection/evidence/privacy 35、self-owned+projection 25、py_compile/diff-check PASS。production main/rule_agent/evaluatorは変更していない。

最初のtableはPLAY/ATTACH/EVOLVE/ABILITY/ATTACK/END別のbounded action-type outcome diagnosticであり、full state-action advantageではない。全6 typeのdeltaが負で、値は約−95〜−104、`usable_signal=false`、`ready_for_screen=false`。よってcandidate screen、384、longrunを起動していない。これは「action typeごとの因果的失敗」を意味せず、loss episodeに含まれるaction typeの相関を見ただけである。現在の次段は、同じreal public sourceからturn/action count、公開board HP/energy/status、visible count、option type等のprivate-free state bucketを作り、bucket×action-conditioned outcomeのsupport/符号/不確実性を診断すること。2〜3候補以上の十分なsupportと異符号/安定性がなければcandidateを作らず、このrouteを停止する。

### 16.10 現在の総合判定

この時点では、実性能の候補を増やすより、既に得た実測結果を再現性と権限境界込みで採用判断できる状態にすることを優先する。native BestKnownはTomato 72.07%のまま、Student/AWR・tie-break・deck mutationはいずれもlongrun昇格条件を満たしていない。次の16.11診断がcandidate生成可能かを判定する最後の小さなpublic-only routeであり、ここが不成立ならpermission発行またはRule v0 fallbackが判断点になる。

### 16.11 public state/action-conditioned advantage diagnostic（最新）

action-type tableが全負でscreen不可だったため、同一real public sourceを再読込し、公開projectionからstate/action-conditioned diagnosticを作成した。抽出したfeatureはphase/action ordinal、public board flags、両seat active HP/energy、hand/deck/prize count、status maskのみ。card identity、private hand/deck/prize、teacher label、native behaviorは使用していない。

actual output `runs/final-sprint-autonomous/self-owned-public-state-action-advantage-common24-v1/` は2,865 examples、1,886 state buckets、eligible cells100/417 examples、competing state buckets3、mixed-sign buckets2。quality reasonsは`few_competing_state_buckets`と`insufficient_mixed_sign_state_buckets`で、`ready_for_candidate_screen=false`、`ready_for_longrun=false`。table semantic SHA `6078a40d838d57929fa9e20784b9da50fe06d1aa45149603eb29d8ec5b0a6358`、table bytes SHA `1e2348b8bccbff40e5b5b7298001de221d5bfbbdae34f87d2a1afb5b5e15189e`、bundle manifest SHA `1f77598ae20e91453c3bf27b1987f5d09581e71a07a4622e93af7b30ee4c0649`。

supportが疎なままcandidateへ進むと、loss episodeとの相関をcausal advantageと誤認するため、candidate 96/384/longrunは起動していない。combined focusedは45 passed（state/action5 + public outcome5 + projection/evidence/privacy35）、load/reloadのsemantic SHA一致、docs validator13、diff-check PASS。全authority false。

これでpermission blocker後のself-owned public route（粗いaction type、次のstate/action bucket）は一周完了した。次の優先順位は、(1)既存deck raceの未評価候補をnative control付きでscreen、(2)明示permissionが発行された場合のみbehavior sourceへ再開、(3)Rule v0 package fallbackを維持、である。旧Student v3/AWR/BC/score-biasやsynthetic tableへ戻らない。

実装SHA: module `3db3bb722376ab640b653254b9092048ad107494f0cc95675276e13378db46fc`、CLI `6f8e8847493782a94e5f5ac4b769c1f338648cc58d1bcc002922680442331070`、tests `8df55f3e130b42cdf5dd8df84b44b8063282d5ebfad8d5fa8b755f8ee1b29e10`、evidence `docs/evidence/autonomous-self-owned-public-state-action-advantage-common24-v1-20260813.md` SHA `693d2769f67b4fae606c5b23db27e15fac5fa076af7f0cb5f1a1f70c0e5ec845`。

「行き詰まり」ではない。permission blockerを一度で確定し、self-owned real sourceの96局収集、fault root causeの最小修正、public-only provenanceを完了した。一方、native BestKnown 72.07%を超えるcandidateも、`LONGRUN_READY_CANDIDATE`もまだ無い。旧Student v3/AWR/BC/score-biasへ戻らず、次のstate-conditioned diagnosticが最後の小さな性能ルートになる。そこでもsignalが無ければ、追加architectureではなくdeck race・明示permission・package fallbackへ資源を移す。longrun、学習、Champion変更、submissionは未成立である。

## 16.12 最新deck role-surface v2（最終、2026-08-13）

v1で評価済みのdeck multiset SHAと重ならない新規1-card swapを3件、Plamen parentと同一policyのcandidate、同一policyのparent、Tomato native controlの3 armでcommon24（24 opponent × 両seat × repetition2 = 96局/arm）へ投入した。全ledgerはDONE、fault0、seat/opp strata整合、authority falseである。新規candidate manifestは `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/candidate-manifest/candidates.json`、SHA `87d908094d3037722ea3734a067f82b9c3230fadbd0fccd07642a47e979d6a50`。統合evidence JSONは `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/role-surface-screen-evidence-v2.json`（SHA `d71e7622ca34077e3eede89afa4fe5d1a30c7c1674c3bb8c4917a59f853769e6`）、Markdownは同ディレクトリの `role-surface-screen-evidence-v2.md`（SHA `fce826690cb4cd8b0f8129410171878974ac2e323a96713b53f178637d99d17e`）である。

| candidate | deck change | Plamen parent (96) | Tomato native control (96) | follow-up |
|---|---:|---:|---:|---|
| `role-b5b3ebf1c0c8f065` | `1185→1213` | 68/96（parent 68、差0） | 73/96 vs 72/96（+1） | 384へ進めず |
| `role-8c8c69dc792c913f` | `1244→1245` | 70/96 vs 62/96（+8勝、1 drawを含む） | 70/96 vs 65/96（+5） | 384確認済み、停止 |
| `role-eb3c4bea51cda997` | `1159→1157` | 69/96 vs 73/96（−4） | 69/96 vs 67/96（+2） | 384へ進めず |

唯一384確認へ進めた `role-8c8c69dc792c913f` は、同一policy parentとの384局で candidate 276/384（71.875%）対 parent 290/384（75.5208%）、差−14勝、−3.646pt、fault0であった。したがって96局の一見した+8/+5は再現せず、全3候補をcandidate-only、`NOT_PROMOTABLE`とする。768/1536、longrun、Champion変更、submissionへは進めない。Tomato native BestKnown（pooled1536で1107/1536=72.0703%）は不変である。

重要な再現性注記として、既存common-protocol runnerの固定`block_id`により、v2のscreenと384 confirmationを跨いだraw `game_id`が192件重複する。各ledger内部ではgame ID一意、seed/seat/opponent strataは整合しているが、跨ぎ統合ではblock-qualified IDが必要である。この重複を隠さずevidenceへ明記し、v2統合を性能昇格の根拠には使わない。既存一次artifact、production main/rule_agent/evaluator、BestKnown、permission、packageは変更していない。性能run終了後に残留processはない。

## 16.13 最新の総合判断とChatGPTへの問い

現時点の実証結果は「契約整備はほぼ完了し、実性能ループの候補選別も一巡したが、longrunへ進める候補はまだ無い」である。実public rolloutは96/96 DONE・fault0・11W/85L、action-type signalは全負で不採用、state/action diagnosticは2,865 examplesに対しmixed-sign bucket 2でsupport不足、Rule v0 tie-breakは384で−0.5208pt、deck v2は96局で局所的に良く見える候補も384で反転した。したがって、native 72.07%を超えるperformance claim、`LONGRUN_READY_CANDIDATE`、training、CABT longrun、submissionはいずれも未成立である。

次の判断は追加architectureを作ることではない。外部のproject owner/issuerが明示的なbehavior/self-rollout permissionを発行するか、または現行のcandidate-only deck/public routeを終了してRule v0 package fallbackを維持するかである。permissionなしにnative policyをteacher label・behavior source・AWRへ流してはならない。synthetic Task1 table、旧Student v3/AWR/BC、score-bias、residual sweepを性能根拠へ戻してはならない。

このファイル自体を、ChatGPTへ渡す単一の判断資料とする。ローカルファイルを参照できない場合は、このMarkdownの全文を貼り付ける。主要入口は次の通りである。

- 最新の全体資料: `docs/status/chatgpt_context_pack_native_preserving_meta_overfit_2026-08-13.md`
- 状態の短い入口: `docs/status/current_status.md`
- 次担当向けhandoff: `docs/status/handoff.md`
- v2 deck evidence: `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2/role-surface-screen-evidence-v2.md`
- real public rollout evidence: `docs/evidence/autonomous-self-owned-public-outcome-common24-v1-20260813.md`
- state/action diagnostic evidence: `docs/evidence/autonomous-self-owned-public-state-action-advantage-common24-v1-20260813.md`
- integrated scoreboard: `docs/evidence/autonomous-integrated-scoreboard-v1-20260813.md` と `.json`

検証状態: docs validator `Validated 13 canonical documents.`、`git diff --check`、関連focused/nearby suitesは直近までPASS。commit、push、Kaggle提出、外部公開、追加longrunは実行していない。

## 16.14 V4 seed1 broad META_TRAIN public trace screen（最新）

permissionを拡張せず、既存の閉じたV4 Wave4 strict-paired checkpoint seed1をself-owned subject policyとして、broad META_TRAIN reference configの24 opponentへ実際に流した。native opponentの選択行動はteacher labelとして保存していない。subjectはV4 checkpoint自身、opponentはpoolのlocal-eval-only assetであり、これは評価用のreal rolloutであって、native behavior permissionやsubmission permissionを意味しない。

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_checkpoint_broad_arena_v1.py \
  --checkpoint runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon \
  --games-per-seat 2 --base-seed 14910000 --max-steps 2000 \
  --output runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-trace-v1/summary.json \
  --progress-path runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-trace-v1/progress_summary.json \
  --trace-output runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-trace-v1/public_trace.jsonl \
  --trace-max-rows 200000
```

一次artifactは `runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-trace-v1/` である。24 opponent × 両seat × repetition2 = 96局、全96局`DONE`、fault0、draw0、V4 seed1は **54W/0D/42L、score 56.25%**。seat0=25/48（52.0833%）、seat1=29/48（60.4167%）でseat collapseはないが、native Tomato pooled1536の72.0703%には明確に届かない。summary SHA `5c40916fb860e803f9c05de4c8955e165423d41ebc4927ffa19a68e1eacbe9d1`、manifest SHA `c7fcf10e25ad310b8a8717260ed255433f215fd6ad415917882190d4523667b5`、evaluator implementation SHA `faeb223cbfa64647c90343fc36f6a545f76a04feaa861f4f8ff4cb9a7be49130`、broad config SHA `832273ff656280d2556c9df09a9a3db9f2564a181eb78a3e658509d3b396209b`、pool SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`、checkpoint file SHA `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`、tensor SHA `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`、subject deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`。このtrace rootはaggregate summaryとpublic traceを保存し、per-game ledgerは別の同条件serial WDL run (`wave6-seed1-meta-train-common24-96-serial-v1`, ledger SHA `7c141aca5b51462962e3cca25057add8c62752042a755d8743e131bf76a94ff3`)にのみ存在するため、両rootを混同しない。

opponent別の重要なhard-negativeは、`lucifer19_battlecore` 0/4、`pilkwang_lucario_alakazam` 0/4、`plamen06_steel` 0/4、`kiyotah_iono` 0/4。逆に`dashimaki360_crustlecounter`、`ferozahmedds_solution`、`itsuki9180_lucario_jp`、`kiyotah_abomasnow`、`medal_0001_77a53ffc`、`naoto714_slowking`、`official_random`は各4/4で、単一の平均scoreよりmatchupごとのhard-negative再重み付けが次のmeta分布設計に有用である。

### traceのprivacyと学習可用性

`public_trace.jsonl` は4,484行、SHA `03b3c940208a2125bd2b2934cd08b505c76fa8542a4ed1eb7d8f0ea08d6a0c00`、overflow 0、redacted 1,167行である。private/hidden/hand/deck/prize/raw_observation/logs等のkey scanは4,484行中0 hit。したがってpublic-only保存境界は守られている。

しかし、現行V4 runtime traceの品質はcandidate学習には不足する。variantsは`duplicate-public-identity` 3,315、`public-v1-redacted` 1,167、`public-v1-representable` 2のみで、semantic action typeの集計は`SKILL` 4件だけであった。残りはselection_type/context、selected_count、order semantics、log probability等のbounded metadataに留まり、chosen action identity/action typeを一意に復元できない。これはnative行動ラベルを隠れて使う問題ではなく、V4自身のpublic action projectionが学習信号として疎であるという実装上の観測である。

したがって今回の96局は、V4をcandidateとして384へ昇格する結果ではない。また、このtraceをそのままaction-conditioned advantageやAWRへ流すこともしない。`public-only capture=安全`と`candidate-grade action supervision=未成立`を分離する。必要なら次の最小開発は、runtimeの既存C5 public projection契約を維持したまま、chosen actionのsemantic operationと合法候補集合を一意に表現できるpublic projection bridgeを、private情報を増やさずに追加することになる。ただし、これは新しい設計・TDDが必要であり、現時点で実装・学習・384延長は開始していない。

### V4 broad screen後の判定

| 判定 | 状態 |
|---|---|
| V4 seed1 broad evaluation | 96/96 DONE、54/42、fault0、seat崩壊なし |
| native BestKnown超え | 未達（56.25% vs Tomato pooled1536 72.07%） |
| real public trace保存 | privacy-safeで成立 |
| chosen-action semantic supervision | 2 representable rowsのみで未成立 |
| hard-negative分布 | Lucifer/Plamen/Pilkwang/Kiyotah Ionoなど0/4が確認できた |
| 384/768/longrun | 未起動 |
| native behavior permission | 0件のまま、自己申告昇格なし |

結論として、V4は「public rolloutを実際に動かし、hard-negativeの実測を得る初期policy」としては使えるが、「このtraceだけで安全にfine-tuneできるpolicy/value source」としてはまだ使えない。次の最大情報量の選択肢は、(A) public projection bridgeを最小追加してV4自身のchosen actionを一意化する、または(B) action supervisionを捨て、opponent別terminal outcomeだけを使うmeta-weighted deck/policy black-box screenへ進む、の二つである。native行動をteacher labelへ変換する経路、旧Student/AWR/BC、synthetic Task1 table、弱いV4をそのまま長時間化する経路には戻らない。

## 16.15 V4 fresh public trace + action table の最終判定（per-game ledger付き）

§16.14のaggregate trace rootとは別に、per-game `ledger.jsonl` を同じseed blockで保存するfresh rootを作り、trace・WDL・action tableの三者を同一source SHAで束ねた。対象はV4 Wave4 strict-paired seed1 checkpoint、24 opponent、両seat、各2局の96局である。全96局が`DONE`、fault0、draw0、54W/42L（56.25%）、seat0=21/48、seat1=33/48だった。Tomato native pooled1536の1107/1536（72.0703%）には届かず、384局、候補screen、policy update、training、longrunへは進めない。

### 一次artifactとSHA

Run root:

`runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/`

| artifact | SHA-256 | 意味 |
|---|---|---|
| `summary.json` | `df9148eb8550e0f5ecba8385335e6d02a15d4dcb86d186a6f80a1d55985a3137` | 96局のWDL、seat、opponent、authority、trace binding |
| `ledger.jsonl` | `6d39fe80c20bc8360360396fe180fef04b3d2b3864ce55e8b6f283ee49095630` | 96 game IDのper-game完了記録 |
| `public-trace.jsonl` | `40ca755cb0706033a7a5eaff2a695458e535346a37b5e462677476742bdc1afb` | 4,678行のprivate-free trace |
| `public-action-table.json` | `51213c7fde74953d46bbd95091bf50095641beeae82d20b02ffd4113670849d1` | traceから抽出したaction-conditioned診断表 |

action tableのsemantic SHAは `978fd1d3d8975ebb0ea17d03ff29f76aba5821d7c104a3c5bab0a199d73058f8`。tableはsource summary/ledger/trace SHAを内部に保持し、`native_action_labels_saved=false`、`teacher_labels_saved=false`、`private_fields_saved=false`、`training_authority=false`、`submission_authority=false`、`promotion_authority=false`である。実行器・checkpoint・deck・poolのbindは以下である。

- checkpoint file SHA: `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`
- checkpoint tensor SHA: `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181eb78a3e658509d3b396209b`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- engine seed support: `false`（seed recipeはbase `14910000`からの96セル）
- selected opponent assets: `source=public`、`usage_boundary=local_eval_only`、native behavior/training/submission permissionなし

### traceの安全性とaction signalの不足

traceは4,678行、redacted 1,248行、overflow 0で、private/hidden/hand/deck/prize/raw observation等の禁止key scanは0件だった。公開境界自体は守られている。一方、action tableは次の通りである。

```text
action_events = 8
action_types = 1
SKILL: support=8, wins=8, draws=0, losses=0, score_rate=1.0
trace_games_with_rows = 96
candidate_screen_started = false
ready_for_candidate_screen = false
usable_signal = false
```

gate理由は `insufficient_action_examples`、`insufficient_competing_action_types`、`insufficient_mixed_sign_action_types`。つまり8件のSKILLだけでは、別action typeとの比較も、mixed-signの優劣も、matchup/seatを跨ぐ因果的なadvantage推定もできない。8/8勝は強い信号ではなく、極小supportによる選択バイアスの可能性を排除できないため、候補化してはいけない。

### 旧aggregate rootとの区別

§16.14の `runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-trace-v1/` はaggregate summary/public traceを先に保存した別rootで、trace SHAは `03b3c940208a2125bd2b2934cd08b505c76fa8542a4ed1eb7d8f0ea08d6a0c00`、per-game ledgerを持たない。今回の判定・action table・per-game provenanceの正典は本節の `v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1` である。両rootを同じartifactとして扱わない。

### 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_v4_public_trace_meta_train_v1.py \
  --config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --checkpoint runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon \
  --output runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1 \
  --games-per-seat 2 --base-seed 14910000 --max-steps 2000

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_v4_public_trace_action_table_v1.py \
  --run-root runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1
```

runner SHAは `e984e5e7e58a50d3e5f461ea28b19b87abdaf64b5b9e255a1524c3ea90d51d5b`、table builder SHAは `4e979ec4edf28d848d6ae696a31279b6270576522c4c3868efb3fd1b96ff13d1`、evidence文書 `docs/evidence/autonomous-v4-public-trace-meta-train-common24-v1-20260813.md` SHAは `b23962ee55961a5f05368b8e782e705d1a9d9da53ba2ac3f697ab3e8f9fc1f73`。focused runner/table/heldout testsは9 passed、py_compile/docs validator/diff-checkもPASS。既存production、checkpoint、pool、旧WDL artifactは変更していない。

### 実性能ループに対する結論

今回で「permission-safeなreal public rolloutを取れるか」「traceからcandidate-grade action supervisionが得られるか」を分離して判定できた。前者は成立、後者は不成立である。したがって現在の最短選択肢は、(A) V4自身のpublic projection契約を維持しつつchosen actionのsemantic identityと合法候補集合をprivate-freeに一意化する小さなbridgeをTDDで設計する、または (B) action supervisionを捨て、今回得たopponent別terminal outcomeをhard-negative再重み付けの診断入力に限定して、deck mutation/black-box policy screenへ進む、の二択である。どちらを採る場合も、まず設計・受入条件を確定し、signalが十分になるまで384/longrunを起動しない。native behavior permissionの自己申告、native action labelのteacher化、旧Student v3/AWR/BC、synthetic tableの性能根拠化、弱いV4の固定epoch長時間化は引き続き禁止する。
## 16.16 outcome-only hard-negative schedule sidecar（実artifact）

V4 seed1のreal WDL ledgerから、action trace・teacher label・private fieldを一切読まずに、META_TRAINだけのopponent sampling scheduleをread-only派生した。broad 24のうちMETA_TRAIN 20 opponent・80局を含め、META_FINAL 4 opponent・16局は完全除外した。これはtraining datasetでもpolicy updateでもなく、次の評価対象quotaを決めるresearch-only sidecarである。

実artifact: `runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json`

- file SHA: `df9397e5e07f995ed41b000b8170a26b71f16ed429e9cfade57e36e949b4d3e9`
- semantic schedule SHA: `f8bec57883ce60e50bb33de0b01939f85d0bceda9a7f09d021d411f82d07570b`
- source: 96局、fault0、META_TRAIN 80局、META_FINAL 16局
- quota: 96、opponent weight cap 0.35、family cap 0.55、family floor 1
- formula: `reliability*(0.70*hard_negative+0.15*underexposure+0.15*diversity)`
- `action_trace_used=false`、`teacher_labels_used=false`、`private_fields_used=false`
- `training_authority=false`、`promotion_authority=false`、`submission_authority=false`、`longrun_authority=false`
- source ledger/summary/meta/pool/config/checkpoint/deckのSHAをmanifestへbindし、strict reload PASS

実装SHAは module `0677bbc9ed37449798f647397dfd461f828dd93ba59de15d98e75eb513783fb6`、CLI `d224bc91be1a64ab3f2c13eb7108c3281847a62ed913423ab4a3f55fe556ecd5`、tests `9099c404eb4d0e8cc4d9bdad2e2187a45dd00d8ec12c166e203e351f602ede34`、evidence `docs/evidence/autonomous-v4-outcome-only-hard-negative-schedule-v1-20260813.md` SHA `d0a4ce4056592127af80c9a485f442f4b52702e49e6ef420bdf382a375cd08a0`。未知ledger row/identity keyを拒否するclosed-schema hardening後も実scheduleのfile/semantic SHAとstrict reloadは不変。focused 6 tests、py_compile、docs validator、diff-check PASS。sidecarはpolicy更新・fine-tune・longrunを開始する権限を与えない。

## 16.17 Tomato policy × deck role-surface v2 のfresh 384確認

v2 screenでTomato native controlに一見+5勝だった `role-8c8c69dc792c913f`（deck `1244→1245`）について、同じTomato native policyをcandidate deckへ固定し、Tomato native deck+policy controlとfresh seed domainで384局ずつ再評価した。旧scheduler fault rootは不採用・上書きせず、workers=1/recycle16の新rootで再実行した。

Root: `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2-tomato-policy-interaction-384-retry-v1/`

| arm | result |
|---|---:|
| candidate deck + Tomato policy | 277/384 = 72.1354% |
| Tomato native deck + Tomato policy | 271/384 = 70.5729% |
| delta | +6 wins / +1.5625pt |

両armとも384/384 `DONE`、fault0、draw0、seat各192、24 opponent×seat×8、paired seed 384件一致、game ID 768件一意、旧partial root/v2 rootとのoverlapなし、authority falseである。候補はnative controlを超えたが、事前のpromotion gate `+3.0pt`を満たさないため `candidate-only`。768、1536、longrun、Champion変更、submissionは起動しない。

一次artifact SHA:

- interaction summary: `eab3b280821d69d2e7948011c8405d99e796133a86b94168bae9258f5133938f`
- ledger: `085f297748522677f030cfb4c5c4f1a087fb11e0c50873a817e3d91904d875f2`
- summary: `fc957dd35184ac91e0263cf669334595a63548fb114ea394f35da0b8f2ceba53`
- manifest: `5a6a8001cee64eef3e3ef289f1a21220a363e533b396a5114c2040e73a276992`
- integrity JSON: `5de1fc812123e3ab1e608629c105339c151c9f389052888fb7a7c4ab163f535e`
- integrity MD: `0a52ff66b522c7398ae9d3460c5ea7bdb7ab711dc6f124650a4525b5d9abf858`

Candidate deck SHA `c69076bc43426b5453e39e910c37ad62b2af42992abe1093157b893d44f3038d`、deck multiset SHA `a90a6e08321f2c7199495d6ea0a6e5df0deb32a7cc4a13f22e5bfa9f19f2f11d`、Tomato policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`、Tomato native deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`を固定している。詳細evidenceは `runs/final-sprint-autonomous/deck-mutation-plamen-role-surface-v2-tomato-policy-interaction-384-retry-v1/integrity-evidence.md` と `integrity-evidence.json` である。

この結果は「Tomato policyを固定したdeck mutationには+1.56ptのbounded positive signalがある」ことを示すが、native BestKnown超過の再現証明ではない。次は別deck mutationを無制限に増やすのではなく、同じpolicy-fixed short phaseでhard-negative scheduleの20 META_TRAIN opponentを使った候補選択へ接続する。candidateが+3ptを満たすまではDECK_FIXED_LONG、policy update、longrunへ進めない。

## 16.18 outcome-only policy-fixed bridge の実96局

16.16のreal outcome-only scheduleを、self-owned Rule v0のbounded action overlayへ接続するresearch-only bridgeを作成した。native/local-eval-only assetのactionをteacher labelとして読まず、候補はroot Rule v0を先に呼び、未知・不正・非対応selectionではexact baselineへ戻る。今回のcandidateは未評価の `ATTACK:+120`、controlは同じroot Rule v0である。META_FINALの4 opponentは完全除外し、META_TRAIN 20 opponentからschedule quota 96、両seat、同一seed/stratumでcandidate/controlをpaired生成した。

Bridge実装・CLI:

- module SHA: `8e43a71a3efb8a89bbe3eed7c21cb9e78ac35aedc85bad9c5b1c92c6cdff1997`
- bridge CLI SHA: `0e8cbed24c27d912fee76db1506b414546299f53b508375e1a53c086532accc7`
- bridge tests: `5e5f84a3270a13cd903513918d285ab1e01db379f9bef3cef0a9f917a4a09f29`
- bridge semantic SHA: `6d13acde40f003a409e0a2019beb397a8f1dcd2a3a1a899f2f4c596fd75facd9`
- bridge manifest SHA: `7f3af7a701211af7157e322c673cd0b0253105169547211a115436beab8caf97`
- game sidecar SHA: `cabc89d9e71013b8ffd90addbc6fc0adb912ab8a08df83e0a6f07dd31e8f3f5d`

実験rootは `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-96-retry-v1/`。初回stdin spawn失敗rootは性能結果として採用せず、実ファイルwrapperによるfresh retryだけを正典にした。retryのevaluation manifest SHAは `fa3413e01fbb39979b73b982bc95ee63d8819ebcf6aa2fa36c4d7ecdcc817eac`、summary SHAは `53bd5eafb0fa032f40008089507c318daff8483f5da6475781198e84c3e24b96`、ledger SHAは `0336501fc8f2ae02293db5c20fb69ccf0b7014a7104f1af8f917eafb27585b12`、run-result SHAは `d6c23e68769cc657950d7132c59ab08f2f4b29a084acd8a68701410e0ccefaf8`。

結果:

| arm | W-D-L | score rate（draw=0.5） |
|---|---:|---:|
| root Rule v0 control | 14-0-82 / 96 | 14.583% |
| `ATTACK:+120` candidate | 16-0-80 / 96 | 16.667% |
| paired | loss→win 13 / win→loss 11 | net +2 |

両arm `DONE=96`、fault0、seat各48、同一paired key 96件、heldout exposure0、opponent 19（quota 0のMETA_TRAIN entryを除外）、authority全falseである。+2.083ptはpositiveだが、96局だけでは昇格しない。方針補正に従い、candidate-onlyのままseed-disjoint 384確認へ進めた。

## 16.19 `ATTACK:+120` seed-disjoint 384確認

96局bridgeをstrict reloadした後、seed `14910096..14910479` の4 block（各96、合計384）を別rootへmaterializeした。parent 96局のseed `14910000..14910095`とは非重複で、candidate/controlは同一 `(opponent_id, seat, repetition, seed)` strataを共有する。

Root: `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-20260813/`

- confirmation manifest SHA: `a7500a75e6bb2848f43818d1766e9d6782bcff7dbd9521ecb938609d0a0eefb2`
- confirmation games SHA: `9e2cbe3a7eff8e59ab9f55e7854ea8b57caffa2a537d1de94503a637591e1d74`
- confirmation semantic SHA: `9ccb1180b21142a74a731c2d55ce79072a82843f5a45ef0ec85554f2d10d30de`
- evaluation summary SHA: `4401639360388c4edc45966aadc28476bcac92049ee0d9d4c7afbcc87d8bddbd`
- evaluation ledger SHA: `eda449d3fa071c4f0303c1421ec9fd369d51bb48e110e258a234ce839f5392df`
- evaluation manifest SHA: `c20d1a7b3a2b7663770edd4474bf86435d814f483e2b312108180e7cbc304401`

結果:

| arm | W-D-L | score rate（draw=0.5） |
|---|---:|---:|
| root Rule v0 control | 30-1-353 / 384 | 7.943% |
| `ATTACK:+120` candidate | 49-0-335 / 384 | 12.760% |
| paired | loss→win 42 / win→loss 23 | net +19 |

両arm `DONE=384`、fault0、candidate seat `(27,22)`、control seat `(13,17)`、paired key384件、seed unique384、heldout0で、candidate-control差は draw半分換算 `+4.818pt`。これはself-owned Rule v0 controlに対する再現可能なbounded positive signalであり、`LONGRUN_READY_CANDIDATE`候補として記録できる。ただし絶対勝率は12.76%で、native Tomato/Lucifer/Plamenの約72%級BestKnownには遠く、nativeを超えたとは言えない。したがってChampion変更、submission、長時間学習の自動開始は行わない。

次の性能判断は、(1)同じhard-negative META_TRAIN 20で別のbounded action surfaceを96 screenし、(2)このcandidateを親policyとしてdeck raceまたはpolicy fixed short phaseへ接続し、(3)必要ならnative controlを別armとして同一poolで再配置する、の順である。`ATTACK:+120`の384結果だけを根拠に768/1536やlongrunへ進めない。native behavior permissionは引き続き `NATIVE_BEHAVIOR_PERMISSION_BLOCKED` であり、native action labels、teacher behavior、synthetic table、旧Student v3/AWR/BCは不使用である。

## 16.20 Performance-First Research Mode の永続方針と、直近の実験更新

最新方針では、契約整備や追加レビューを主目的にせず、既存の強いassetを初期値としてMETA_TRAIN上位メタへの期待性能を上げる実性能ループを優先する。タスク選択は `期待性能向上 × 成功確率 ÷ 時間コスト`、資源目安は実CABT/deck-policy探索60%、candidate実装20%、分析10%、tests/docs10%とする。止める条件はprivacy/permission/illegal action/identity混同/fault隠蔽/leakage/artifact・evaluator破損/rollback不能など性能結果を壊すblockerに限定する。96局はscreen、384局は主要判断、768/1536は本命だけに使う。`NOT_PROMOTABLE` と `EXPLORATION_PRIORITY` は分離し、小差のcandidateを無目的に捨てない。

### iteration-1 bounded action screen

同一META_TRAIN schedule（20 opponent、META_FINAL 4除外、heldout0）でself-owned Rule v0の追加bounded actionをscreenした。`PLAY:-120` は candidate 6/96 vs control 10/96（−4.1667pt、paired net−4、fault0、seat3/3 vs5/5）、`EVOLVE:+120` は 13/96 vs16/96（−3.125pt、paired net−3、fault0、seat6/7 vs8/8）で、双方candidate-only/384未実施である。これは局所action surfaceを棄却する結果であり、deck探索やblack-box policy route全体を否定しない。

### weighted META_TRAIN deck halving

iteration-1上位12 META_TRAIN opponentへ重みを付け、parent `role-8c8c`と未評価deck mutation 4件を48局/armでscreenした。root manifest `99d78d5abff005d4f3387e8022fda960fcf22550dc3a97b03d3d7810ad81dc43`、ledger `6558917d36bb5b5d2cef429e96b286cd66582973f164d103fcb993989fc1c92b`、weighted summary `1e0fd95d1c0856348872ebf76ee084b7556863ad74fcbb321ad4ca53b907a540`。全240ゲームDONE/fault0、seat24/24、opponent各4、GID unique、paired seed一致。weighted scoreは parent=0.607676、a73 `1245→1152`=0.683902（+7.623pt）、95cc `1213→1185`=0.694563（+8.689pt）、551b `8,1121→1097,1147`=0.722281（+11.461pt）、432ff `8,1244→1097,1122`=0.723881（+11.620pt）。

common24 guardrailを5 arm×96局へ拡張した結果、全480局DONE/fault0、GID480 unique、seat48/48、seed paired mismatch0。parent 70/96、a73 72/96（+2.083pt）、95cc 70（0）、551b 68（−2.083pt）、432ff 70（0）。小差を自動棄却しない方針に従い、a73だけをseed-disjoint 384へ進めたが、candidate 273/384 (71.09375%) vs parent 280/384 (72.91667%)、差−1.8229pt、fault0、GID768 unique、paired384一致となり再現しなかった。a73は`NOT_PROMOTABLE`だが、Lucifer/Plamen/Aristo/Harukiharadaへの局所regressionとitsuki/naotoへの改善が観測されたため、hard-negative診断情報として保持する。768/1536は起動しない。

weighted halvingの結論は「weighted 48局で強く見えた候補をcommon24 guardrail→384で検証する価値はあるが、48局の大差をそのまま昇格根拠にしない」である。次候補は上位hard-negative（特にLucifer/Plamen/Aristo/Harukiharada）への局所改善を目的に、未評価deck surfaceを最大2〜3件だけ選ぶ。

### V4 semantic-action projection の実source最終判定

V4 seed1 checkpointをsynthetic fixtureではなく実sourceへ接続したfresh common24 96局で、96/96 DONE・fault0、1,289件のpublic decision rowをsemantic projectionへ保存した。12 semantic operationを観測したが、3,621件はduplicate public identityとしてfail-closed除外されたため、`usable_signal=false` / `ready_for_candidate_screen=false`となった。STOP-available rowは100件で、candidate-gradeの安定したaction-conditioned signalとしては不足する。private hand/deck/prize、future RNG、native action label、teacher label、logitは保存していない。

root `runs/final-sprint-autonomous/v4-seed1-semantic-action-projection-common24-96-serial-20260813-v2/`。summary SHA `db973af333e63209ca26a2fe94e289a34d3cd5e8715195a3158b434b800bcb3e`、ledger SHA `abdf4354e4594fb625673be2895456e25541f66b31f599f4ff4fe7f0c89ee801`、semantic rows SHA `4fb4654ada3fa4697b6a941e0e07ff9beea8f3abf7ec87c580272f6a150e4988`、projection summary SHA `9d954cc649f45d79335e2aa6ae4ec263aa8b5a4238ff1da626ce9cf29811a927`、evidence SHA `c11119a4ba9c7947b4f7ebb733af825e360b0494e5e053b65b4cd66c1333bde9`。module SHA `540156a020e8edde55e17490c2c7e99078532b9c26a691d14be2ef83cc49fd77`、runner SHA `9b773c5407c69b1ad8c9e5a68dc9274c9a314293659887a1fb29a30d2dfab8f9`、tests SHA `76cdf26d181172db9896486e1da4c3e03af177eae6abc466ad9436c9dd7e6c59`。focused3、py_compile/docs/diff/privacy PASS。bridgeはここで拡張せず、outcome-only hard-negativeとdeck/black-box policy探索へpivotする。

### 未評価bounded actionのweighted48 screen（ATTACH/END）

既評価のPLAY/EVOLVE/ATTACKを再実行せず、ATTACH:+120とEND:+120だけを同じiteration-1 weighted scheduleで各48局screenした。両方ともcandidate/control paired、META_TRAIN support19、seat24/24、authority false、fault0である。

| candidate | candidate W-L | control W-L | 差 | 判定 |
|---|---:|---:|---:|---|
| ATTACH:+120 | 7-41 | 6-42 | +1勝 / +2.083pt | common24 guardrailへ |
| END:+120 | 4-44 | 7-41 | −3勝 / −6.25pt | 局所NO-GO |

ATTACH rootは `runs/final-sprint-autonomous/v4-seed1-outcome-only-weighted-action-attach-plus-120-48-20260813-v1/`、manifest SHA `f314edb1803417ba227e97b3aa6a0d3c00df61f651cb418ea42f85c90165c465`、screen SHA `f798e9425e8a70d4517fc59499b90367e3a7ca2744e0e36b03d6c9fc7889b652`。+2.083ptはpromotion未成立だが、小差を理由に即棄却せず、全24 opponentのcommon24を評価専用guardrailとして次に実行する。heldout4件は重み更新・training・teacher化には投入しない。common24がpositive/fault0/seat安全ならMETA_TRAIN weighted96または384へ進め、negativeならこのaction surfaceだけ停止してdeck childへpivotする。

### ATTACH common24 guardrail の反転

ATTACH:+120を全24 opponentのevaluation-only common24 96局へ拡張したところ、candidate 6/96（6.25%）vs control 15/96（15.625%）、差−9.375pt、fault0、GID192 unique、paired net−9、seat candidate3/3 vs control7/8となった。META_TRAIN20のみでも candidate6/80 vs control13/80、heldout4は candidate0/16 vs control2/16で、heldout_training_exposure=0。ATTACHはこのaction surfaceだけをNO-GOとして停止し、384/longrunへ進めない。negative resultはdeck child/black-box全体の否定ではなく、次の局所候補選定へ使う。

## 16.22 Resource-Aware Parallelization の恒久方針

## 16.23 Resource-Aware weighted deck halving v2（新規候補2件）

既評価候補を全てmultiset除外し、未評価deck child 2件を同一Tomato native policy・同一META_TRAIN weighted subsetで比較した。warm-up/rampはworkers `1→2→4→8→12` を各4局で通過し、全段階DONE/fault0だった。候補は `510f5bb05224b5eb…`（1185→8）と `b92a3b55c5fa3485…`（1185→1159）。

| arm | W-D-L | weighted score | parent差 |
|---|---:|---:|---:|
| parent `role-8c8c` | 27-0-21 | 0.565485 | — |
| `510f…` | 29-1-18 | 0.611051 | +4.557pt |
| `b92a…` | 36-0-12 | 0.746219 | +18.073pt |

両候補ともweighted48ではpositiveだったためcommon24 guardrailへ進めた。parentは65/96（67.7083%）、`510f…`は60/96（62.5%、−5.208pt）、`b92a…`は67/96（69.7917%、+2.083pt）。`510f…`は停止し、`b92a…`のみbounded-positive candidateとしてseed-disjoint 384確認へ進める。+2.083ptはpromotion根拠ではなく、384で再現性を測るためのcandidate-only昇格である。

v2 artifact/evidence: root `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v2-20260813/`、candidate manifest SHA `4efca96cba35abadc7d123f50c56911fd5cc522695a8603d05433f9ed18996ab`、warmup telemetry SHA `cd2ea7d3ceef4cae6ce4d4440a7c8757d3aacfed1e920dd8ea0b947a72552253`、weighted JSON SHA `f334710692b75d4e4b49ff1d93045d12ff61e2e6c3e15fe1d0a86b8b89c60952`、common24 JSON SHA `5d3cdf48b4c38aebf344b345755db4cfb33a0e989456121d7676f3a679d0c76d`、corrected MD SHA `699de6672e5d1f7857efdf9367da0c64ded149d03348a7a90f69d3c606a24b42`、evidence SHA `d5b4aeea249d23660f802dc31c190af5604edd54e36ae10fd19d18e2571d4a93`。

ResourceGovernorを実性能runへ接続し、warm-up throughputはworkers `1/2/4/8/12` で `1.3401/1.5909/1.5973/1.7235/1.6068` games/s、weighted runは約`17.1781 games/s`、RSS/available memoryは約`34.7MB/46.34GiB`から`35.1MB/45.63GiB`、restart0だった。GPUは起動せず、authority全false、production runner/evaluator/既存一次artifactは不変である。

## 16.24 直近の判断と未完了384確認

最優先は `b92a3b55c5fa3485…`（1185→1159）のseed-disjoint 384確認である。candidate+same-policy parent、full common24、同一seed/strata/opponent/seat、fault0、GID一意、ResourceGovernor warm-up/ramp/telemetryを要求する。384がnegativeなら768/longrunへ進めずhard-negative情報だけ保持する。positiveでも+3pt未満ならcandidate-onlyであり、native BestKnown（Tomato 72.0703%、Lucifer 71.8099%、Plamen 71.7448% pooled1536）を超えたとは扱わない。

この384が未完了の時点では、非MAIN target score overlay（lethal bonus / overkill penalty）はprospective設計に留め、実装・性能runを起動しない。native behavior permissionは引き続きblocked、teacher/native action labelsは不使用、submission/Champion変更/longrunは未許可・未起動である。

## 16.25 b92a 384 confirmation 最終結果

v2 common24で唯一positiveだった `b92a3b55c5fa3485…`（1185→1159）を、同一Tomato native policy・full common24・seed-disjoint 384で確認した。parent/candidate各384、合計768 rows、DONE768、fault0、draw0、各arm seat192/192、各24 opponent×16、GID unique、candidate/parent seed schedule一致、paired strata一致。candidateは255-0-129（66.40625%）、parentは282-0-102（73.4375%）で、candidate−parentは **−7.03125pt**。weighted48の+18.073pt、common24の+2.083ptは再現せず、候補はcandidate-only/NO-GO、768/longrunには進めない。

confirmation root: `runs/final-sprint-autonomous/resource-aware-b92-confirmation-v1-20260813`

- confirmation summary SHA: `9729fdc7ec7b3034a4825f220c1639cc97335ff08144ff642ae3cb6ff41eb372`
- confirmation summary MD SHA: `6b3cc8471361bb5e81f89ee6ccb5b15bd7472bc72b7c01317543fca27f40dd50`
- evaluation manifest SHA: `702159926ae13dc648e9fb6ef2985d188c7fd3a80afd3953eacc243e58c98519`
- evaluation ledger SHA: `41c8807834dc33fbd1917c627ebc0131bf1f4eb2ba7456c3ae9fe8e1d1835ad0`
- warmup telemetry SHA: `e780b9ad4af464966c47c2d4fdb1eafc2f4a9d69586d03215f579c9df1e744a5`
- evidence SHA: `f8e1e3489052b1696801459f25024b33358160c16b9620b0e4b5da7a6a2336ae`

ResourceGovernorはnormal、safe workers12、GPU compute processなし、kill0、worker recycle16、evaluation throughput17.95 games/s、RSS約28.3→32.2MB、MemAvailable約46.01→45.73GB、restart0だった。authorityはresearch_onlyのみで、execution/promotion/training/longrun/submission全false。production runner/evaluator/既存一次artifactは変更していない。

この結果から、現在のdeck mutation候補もnative BestKnown級へは届かず、weighted/local positiveをcommon24/384で再現できないことが確認された。次は同じcandidateを無目的に再試行せず、hard-negativeを使った新しいpolicy/deck surfaceまたは非MAIN target overlayの最小TDD bridgeを、期待性能・成功確率・時間の比較後に1本だけ選ぶ。

Performance-Firstに加え、利用可能CPU/GPUをwall-clock短縮へ使うが、WSL/Windowsのmemory exhaustion、GPU OOM/lost、resource contentionを起こさない。GPUは`MAX_CONCURRENT_GPU_JOBS=1`、CPU/CABTは固定workersを避けて1→2→4→8→12のramp-upとし、workerごとのpeak/p95 RSS・throughput・fault・swap・MemAvailableを測って上限を決める。worker recycleは16〜32 gamesを基準にし、swap急増・major fault・throughput低下時は縮小する。

warning（free memoryがsafe threshold未満）では新規worker launch停止、criticalでは自分のowned workerだけgraceful terminate、emergencyではowned heavy childだけ止め`RESOURCE_PRESSURE_STOPPED`としてresume可能状態を保存する。OOM killer待ち、WSL shutdown/restart、無関係process killはしない。並列評価でunexpected faultが出た場合はaffected cellをserial再現し、serialで再現しなければresource/concurrency faultと性能failureを分離する。各heavy runはworker数、games/sec、elapsed、peak RSS、MemAvailable min、swap peak、CPU/GPU utilization、restarts、faultsを保存する。

初期監査値: logical CPU28、WSL MemTotal約47.0GiB、MemAvailable約43.6GiB、swap8GiB中約16.7MiB使用、GPU RTX PRO 5000 Blackwell 48,935MiB中約3,501MiB使用・compute processなし、`.wslconfig`は`memory=48GB / swap=8GB`。初期budget案はfree10GiB/fraction20%/emergency6GiB、initial workers2/max12/ramp[1,2,4,8,12]/recycle16/max GPU1。ユーザー設定変更、WSL再起動、既存run停止はしていない。ResourceGovernorは新規小moduleとして、CABT evaluator/deck search/candidate arena/dataset preprocessingへ段階適用し、GPU trainerはexclusive lockから始める。

ResourceGovernor v1の新規実装をTDDでGREEN化した。module SHA `2aaa4ed01625361ead9a13c10d2ba1577b11185fbc2fbbd53e624f6b47bf9508`、config SHA `e9e6f17d7b395d4973ca7bed8792d40c71367084dced6d3d740eaba62f743848`、tests SHA `6a2836d73bb5c684a97eef1eea633fd92b7bae5e991c2f8b1029d08b0f932086`、evidence SHA `6ba02375128d9e14074452b931bad1522559759efb50bc30e2a4608041b9f955`。focused 9 passed、nearby resource+progress+parallel evaluator 22 passed、py_compile/docs validator/diff-check PASS。telemetry root `runs/final-sprint-autonomous/resource-governor-v1-20260813/telemetry.json` SHA `34d71d2a4793d533d3389f17bc5c8b70344e77246320b57107b99eb6bc5d3a31`、payload SHA `5bcc48e4c82aeda10ad65f5550904c18fabb69d70eff3d13fdefdf1bb0279af6`、state normal/recommended workers12/GPU admission true/kills0。実装はrecommendation/admission専用でまだ既存runnerへ自動接続していないため、次回新規CABT/deck/candidate runでwarm-up→ramp telemetry→safe worker選択を接続する。

resource-aware weighted deck screenも完了した。warm-up `[1,2,4]` は各段階fault0、weighted48は parent `role-8c8c` 30-0-18 / weighted0.621496に対し、候補 `7e04086d`（8→1159）が28-0-20 / 0.583118（−3.83775pt）、`870229ee`（1182→1244）が29-0-19 / 0.587624（−3.38719pt）だった。全arm fault0、seat24+24、GID/seed unique、authority false。common24/384/longrunは起動せず、候補はcandidate-onlyで停止した。warm-up throughputは0.8925/1.6915/1.5575 games/s（workers1/2/4）、weighted runは17.1781 games/s、RSS/available memoryは34.7MB/46.34GiB→35.1MB/45.63GiB、restart0。weighted summary JSON SHA `106d921f4a705c25b9b870f7893d680a1b69ed300d5b2b854d561bfb0825ede3`、MD SHA `7f78e165ea2a011bca4f11fcde1141961b6e8c79c82ef37db32e9a4e41483fa8`、evidence SHA `272258ef5ce078ebf65193264e76e11944f09f2bb77c2be4988d5ef0ec60be79`。次の実性能ルートはself-owned policy/deck alternatingである。

## 16.26 非MAIN target lethal overlay weighted48 最終判定（2026-08-14）

Rule v0のMAIN action overlayとは重複しない、非MAINの合法target選択だけへ `lethal_bonus_delta=+120` を加える `nonmain-target-lethal-d120-v1` をfresh weighted48でscreenした。入力はpublic optionの `damage` / `hp` / `playerIndex` / `type` のみで、MAIN・非対応・不正・illegal・例外はRule v0へexact fallbackする設計である。今回のWDL runnerにはcoverage telemetryが接続されておらず、gateは未測定なので採用根拠にはしない。

有効な実測は v4 root のみ（v3はcandidate callableのbound methodへ`__name__`を設定しようとして48件AGENT_ERRORとなったため性能結果から除外）。`runs/final-sprint-autonomous/nonmain-target-lethal-d120-weighted48-20260814-v4/` はDONE96/fault0/draw0で、control 8/48（16.6667%）、candidate 6/48（12.5%）、差−4.1667pt。pairedはcontrol loss→candidate win 5、control win→candidate loss 7、WW1、LL35で、candidate seat勝数は5/1に偏った。candidate-only/NO-GOとしてcommon24・384・768・longrunへ進めない。native BestKnown/Champion/productionは不変である。

主要SHAは screen `2285b847…`、ledger `ce952220…`、summary `47f1a222…`、evaluation manifest `7491c2d8…`、run-result `6c1a8fae…`、candidate policy `edb1ce1c…`、evaluator `0cbac278…`、ResourceGovernor warmup payload `12aa49c9…` / file `44c40c4b…`。workers=1/recycle16、task_cap=1でsafe_workers=1（rampは1のみadmit）、DONE96/fault0。producer module `0dc53fec…`、build CLI `8d30ce22…`、run CLI `6e7753f5…`、tests `30ddec18…`、evidence `a1f1157e…`。focused6、py_compile、docs validator（13 canonical）、targeted diff-checkはPASS。production/evaluator/既存一次artifact/permission/authority/commit/push/submissionは不変。

次はこの面を再試行せず、b92aの384反転・ATTACH common24反転・target overlay負結果をhard-negativeとして使い、weighted deck childまたはdeck-policy alternatingを期待性能向上×成功確率÷時間で比較して1本だけ選ぶ。common24は目的ではなくguardrail、native behavior permissionがない間はnative actionをteacher label化しない。

## 16.27 Tomato native parent deck child screen / common24 最終判定（2026-08-14）

Tomato native BestKnown pairを親として固定した。parent deck SHAは`42165967…`、Tomato policy SHAは`8908af5c…`。既存`opponents/**`および過去final-sprint deck multisetを除外し、META_TRAIN選択12件のhard-negative重みで新規1-card mutationを2件だけscreenした。`1182→1086`（Buddy-Buddy Poffin）はweighted **−2.052pt**、`1185→1192`（Carmine、candidate `ae3075…`）はweighted **+7.106pt**だった。weightedはparent33/48、4d4 candidate32/48、ae candidate36/48、全144 DONE/fault0、seat24/24、paired seed/strata/GID gate PASS。

positiveだったaeだけを全24 opponent common24 guardrailへ進めた結果、parent **73/96（76.0417%）**、ae **62/96（64.5833%）**、差 **−11.458pt** に反転した。合計192 DONE/fault0、両arm seat48/48、各opponent4、paired seed/strata/GID gate PASS。したがってaeはcandidate-only/NO-GO、384・768・longrun・Champion変更・submissionへ進めない。weighted局所positiveをcommon24で再現できなかったため、このsurfaceはhard-negativeとして停止する。

weighted root `runs/final-sprint-autonomous/resource-aware-tomato-weighted-deck-v1-20260814/`。weighted manifest `656fbbcc…`、weighted ledger `5d2a5a51…`、final weighted summary `a2a55b21…`、weighted evidence `cb21528a…`、weighted MD `79807565…`。ae common24 root `runs/final-sprint-autonomous/resource-aware-tomato-ae-common24-v1-20260814/`、common24 summary `d99f1e39…`、MD `97109825…`、final `23b0d8d3…`、evaluation summary `5dace37b…`、manifest `4d02a700…`、ledger `c09861a8…`、evidence `8e5534b9…`。ResourceGovernor warmup/ramp、fault/seat/paired/identity gates、py_compile、focused tests、docs validator13、diff-checkはPASS。authority全false、production/evaluator/既存一次artifact不変、384/longrun未起動。

この結果を踏まえ、同じCarmine mutationの再試行はしない。次の実性能taskは、未評価deck surfaceまたはdeck-policy alternatingを、META_TRAIN重み・native control・時間コスト・hard-negative情報量で比較して1本だけ選ぶ。native behavior permissionがないため、native actionをteacher label化せず、native pairはcontrol/opponentとしてのみ扱う。

## 16.28 Tomato native parent surface weighted48（2026-08-14、停止）

Tomato native親（deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`）から、既存全deck multisetと重ならない `Full Metal Lab (1244)` の未評価置換を2件だけscreenした。候補は `1244→1123 Switch` と `1244→1252 Gravity Mountain` である。

| arm | W-D-L / 48 | weighted meta score | parent差 |
|---|---:|---:|---:|
| Tomato native parent | 33-0-15 | 0.686881594 | — |
| `1244→1123` | 28-0-20 | 0.587203759 | −9.9678pt |
| `1244→1252` | 27-0-21 | 0.566681594 | −12.0200pt |

同一META_TRAIN 12 opponent weighted subset、両seat、各repetition2、candidate/parent同一seed/paired strataで、全144局がDONE/fault0、各arm seat24/24、各opponent4局、arm内game-id/seed uniqueだった。2候補とも親を大きく下回ったため、common24/384/longrun/submissionへ進めず `candidate_only` で停止した。このFull Metal Lab surfaceはhard-negativeとして保持し、同じ置換を再試行しない。

ResourceGovernor warmupはworkers `[1,2,4,8,12]` を各4局、全段fault0で通過。weighted本体はworkers12、worker recycle16、144/144 DONE、wall 8.453秒、throughput 17.035 games/s、restart0、無関係process kill0。authorityはresearch_onlyのみ（execution/training/promotion/longrun/submission false）、production runner/evaluator/既存一次artifactは不変である。

正典rootは `runs/final-sprint-autonomous/resource-aware-tomato-surface-weighted-v1-20260814/`。manifest `a34365af14236b52b7375abdaea9a8e6448b849ed4372ada6f5cb12eb3a09803`、weighted summary JSON `6e854dc47186f5d00ddcb3a63e2b950b48448cd17163fc3ecd234fb289e58157`、MD `9be658d08f02bea5fc82c3593f9f86d797e99f08a255010a864cc0993b698020`、final `9cf88811b623f55cecd61ec8f079411708cd6ed332f54647f4a00b042dbdb868`、evidence `5d787d3105a6954c1c11ac8a3915879901c23b6f0477bc0c8f6ba668662664a2`。warmup telemetry `9ce24ed952b7e982cac53ba1f6b1627283e329aa54b93960f6c649e3eb760d8d`。

次の性能選択は、Carmine/b92a/ATTACHの局所positive反転と今回のFull Metal Lab負結果をhard-negativeとして使い、未評価deck-policy alternatingまたは別surfaceを期待性能×成功確率÷時間で1本だけ選ぶ。native behavior permissionがない間はnative action/label/logitをteacherへ流さず、局所weighted scoreをnative BestKnown超越やlongrun readinessと混同しない。

## 16.29 Tomato native parent overlay weighted48（2026-08-14、停止）

`Full Metal Lab`置換群の次の未評価面として、Tomato親の `1244→1102 Dusk Ball` と `1244→1141 Premium Power Pro` を、同一META_TRAIN weighted48でscreenした。parentは33-0-15、weighted `0.691191539`。Dusk Ballは32-0-16、`0.670108308`（−2.1083pt）、Premium Power Proは30-0-18、`0.633539837`（−5.7652pt）だった。

全144局DONE/fault0、各arm seat24/24、各opponent4、arm内GID/seed unique、parentとのpaired key/seed一致。ResourceGovernor warmup `[1,2,4,8,12]`各4局fault0、weighted workers12/recycle16、wall約8.213秒、throughput約17.533 games/s、restart0、kill0。authority全false、production/evaluator/既存一次artifact不変。両候補はcandidate-only/NO-GO、common24/384/longrun/submissionは未起動。Dusk/Premium面を同じ条件で再試行せず、hard-negativeとして次のdeck-policy alternatingまたは別surface選定へ渡す。

Root `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1141-1102-weighted-v1-20260814/`。manifest `ac4049685d3fbbff6aaf6c1ca2f6b87715ecb1474e18cd3fd41b369ae97d9b36`、weighted JSON `40d34f8ad7698685f41b44ca4c9841320f334366668eaa87bf21d7ed18d06e96`、MD `abaf43a99b15838b06dbf577058a68374ae7c0b7774b3ebccca7cb2c6dbdf24f`、final `1ad5468edb0a588e5e86a8157eac8cee65bc7b36daadde236c74a40651429c62`、warmup `5bb854372b825b773fa62f7ada654e193dd5e697b95529cd0de36cbb25c36ea6`、evidence `docs/evidence/autonomous-resource-aware-tomato-overlay-1141-1102-weighted-v1-20260814.md` SHA `533c4587feea838ab255f066e7c1a1f0c5aaf8b048765b8de96bac75ac9e914e`、wrapper `573ed429570eb3ac0b52bc07df209720a0fb1868a8598220e78133f3ba9a6058`、tests `585e6c7a7be04acd8506cd1e7166b8f2a0e1d78716406e16617c12a2a0b9f691`。

## 16.30 Tomato native policy threshold weighted48（2026-08-14、停止）

Tomato native parent policyの研究用コピーへ `_ICE_CREAM_HP_THRESHOLD` の全matchup値を一律 `-20` / `+20` する2候補を適用し、同一META_TRAIN weighted subset（12 opponent、各arm48局）で比較した。parentは35-0-13、weighted `0.718641367`。lower `ice-threshold-lower-v1` は28-0-20、`0.590939841`（parent差 **-12.7702pt**）、higher `ice-threshold-higher-v1` は30-0-18、`0.625769315`（**-9.2872pt**）。3 arm計144局は全てDONE/fault0/draw0、seat24/24、opponent各4、arm内GID/seed unique、parentとのpaired key/seed一致だった。

両候補とも明確なnegativeのためcandidate-only/NO-GOで停止し、common24/384/768/longrun/training/promotion/submissionへ進めない。同threshold面の再試行はせず、hard-negativeとして次のdeck-policy alternatingまたは別bounded surfaceへ渡す。ResourceGovernorはnormal、workers12、recycle16、wall約10.36秒、throughput約13.90 games/s、restart0、kill0。native action/teacher label/logit/private fieldは不使用、authority全false、production/evaluator/parent deck不変。

正典root `runs/final-sprint-autonomous/resource-aware-tomato-policy-threshold-weighted-v1-20260814/`。manifest `faedacf5578ad9d2e29365eb4a1b750075b072c34260f4dd9244deaaa12b341a`、warmup `42ddd8b29a194a015fcd4e55adcbd4af8e517ce029f8ae2b3b43747e51a6954b`、weighted JSON `9cad065f2fc15dbbb6705c19e0249e02bd549403ede68eff1809fcaf4c0077ba`、MD `3cac606931691562d6e6e9be20e24f3b7368377a74e8bbe8a4b8674f422f90b7`、final `6ea99d48d879d77d3933f5a43a9b1f6f4a891c301883509ef5778a16dccb8557`、evidence `7d866550c09ebc08f02c13159183e7ab93ccbd5502963ad831ddc7384d969108`。runner `f439712cc605e8189c7805c7ad66fbbaffb5a08eb6d33d55f32321af2d96e23a`、focused test `f1e1a8996091a4bbaf9ed118aa69d2e85d3770dcec6c501b89db3789d12d88b7`。focused/nearby5、py_compile、docs validator13、diff-check PASS。commit/push/submissionなし。

## 16.31 Tomato native setup-active priority weighted48 → common24（2026-08-14、停止）

Tomato native policyのsealed `_SETUP_ACTIVE_PRIORITY` mappingだけを研究用コピーで変更した。Duraludon-first（CINDERACE 20,000 / DURALUDON 100,000 / RELICANTH 5,000）とRelicanth-first（CINDERACE 20,000 / DURALUDON 5,000 / RELICANTH 100,000）を同一META_TRAIN weighted48でscreenした。parentは27-0-21、weighted `0.557124342`、Duraludon-firstは36-0-12、`0.757646623`（+20.0522pt）、Relicanth-firstは29-0-19、`0.602890066`（+4.5766pt）。全144局DONE/fault0/draw0、seat24/24、paired key/seed/GID gate PASS。

weighted positiveを小差でも捨てず、broad config全24 IDsのcommon24へguardrail拡張した。META_TRAIN weightingは使わず、heldout4（aristophanivan_multiply / dashimaki360_crustlecounter / lucifer19_battlecore / plamen06_steel）は評価のみ、`heldout_training_exposure=0`を固定した。parentは75/96（78.1250%）、Duraludon-firstは71/96（73.9583%、**-4.1667pt**）、Relicanth-firstは66/96（68.7500%、**-9.3750pt**）。全288局DONE/fault0/draw0、各arm seat48/48、opponent各4、paired seed/strata/GID gate PASS。局所weighted positiveはcommon24で反転したため、両候補をcandidate-only/NO-GO、384/768/longrun/training/promotion/submission未起動。同setup priority面はhard-negativeとして停止し、再試行しない。

初回weighted materializeは別surface moduleのprivate `replace_game`誤参照で実評価前に停止した。失敗をregression testで再現し、local metadata rebindingへ最小修正した。初回partial rootは保全し、fresh retry rootのみ正典とする。ResourceGovernorはweighted workers12/recycle16、common24 workers12/recycle16、warmup/ramp fault0、common24 throughput約15.75 games/s、restart0、kill0。authority全false、native action/teacher/private情報不使用、production/evaluator/parent deck不変。

正典rootはweighted `runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-weighted-v1-retry-20260814/`、common24 `runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-common24-v1-20260814/`。weighted manifest `7ea9be120b62698e8000289cc8f3a8f399a4880d781666ca2f46690a5718114b`、weighted summary `f13061be0612ede24cab97326bc0a5b645f7edf9d8e10766b2d0d3aac4f3448c`、common24 manifest `ecfb02332052026b9cbfa56914f73da2e372861add2abdac513a21f833d5d88c`、common24 summary `a6d44ae164d31c7d9fad423e750ec9b2c3f3af252300dbd80f7be524d6455e5a`、common24 evaluation ledger `0d31041b0dac222259388d187569e7a676ae9ccc48ff4742380320d9fdbd8e14`。runner `54cf5399c3fe0bb3a9a5f382b7521e43a617988198d8f4e85fd6583d001fdf51`、tests `d72347451773c311a797028fd258b7f86b361f20a2b90dcb1fd842e557868a93`、evidence `docs/evidence/autonomous-resource-aware-tomato-policy-setup-priority-20260814.md` SHA `b36554bdf3dece7013d994d5a48e79c562f470e09855fa2f3909b36e0bac258a`。focused5、nearby9、py_compile/docs validator13/diff-check PASS。commit/push/submissionなし。

## 16.32 FINAL-SPRINT policy×deck 2×2 / submission runtime（2026-08-14）

最新directiveの二層gateを適用する。`SUBMISSION_PROMOTION`の現BestKnownはRule v0×root deck **11/96=11.4583%**（fault0、summary `916e2223803…`）。`PERFORMANCE_TARGET`のnative benchmarkはV4 seed1×Archaludon **54/96=56.25%**（fault0、summary `db0f32c8…`）であり、72%級nativeをsubmission昇格閾値にしない。

未測セルを完成させ、Rule v0×Archaludonは **15/96=15.625%**（fault0、seat0=9/48、seat1=6/48）だった。fresh root `runs/final-sprint-autonomous/final-sprint-2x2-rule-v0-archaludon-deck-96-v2-20260814/`、summary `f9240ce41e556c77f9c5e7ee2f265e7a47286853eb78d303bf6e836d52a421d2`、manifest `90eaf881819016b9adadf31d2c07802c225e3eae1c7c5a46883269ac4c14b1cb`、ledger `16cc4bc252b1bd05be0f8f40103be5b9de88b3aea35331d6be956626ca027a78`。研究runnerのsubject deck固定bugをTDDで修正し、runner SHA `99cbc5f062e053aa07ea40fab1751f1a66e793defb4c9fb167bb5016d0e4d6cf`、test SHA `47a3e23dfbab405f77a178412869f511d9aac1e249e5ceda2b4c968cc7f7f7a2`。初回bug rootは除外し保全した。

V4 seed1×root deckはArchaludon strict qualificationがcore `[169,190]`を要求するため96/96 qualification fault。`V4_DIRECT_ROOT_DECK_CELL=CLOSED`であり、性能0%ではない。偽core・資格迂回・production編集はしていない。

提出監査ではRule archive SHA `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`（5,908 bytes）がarchive-only 2局PASS（fault0/illegal0）。V4 seed1 checkpointは3,451,469 bytes、file SHA `ec08ace5…`、tensor SHA `17682967…`、local torch `2.11.0+cu128`/numpy `2.5.1`/CUDA available。ただしV4はproduction entrypoint未接続、production card vocabulary gate未成立、runtime dependency closure未vendoringで`submission_ready=false`。Kaggle側torch、package size、latency/RSS/filesystem、Rules/Submit制限は`EXTERNAL_CONFIRMATION_REQUIRED`。V4 package/submitは未実施。

次の主線はP0=Rule v0、D0=root deckのsubmission-compatible alternating。新deck候補は既存hard-negativeを避けてweighted48→common24 96→明確positiveのみ384へ進める。native 72%はbenchmarkとして保持し、40–50%のsubmission-compatible candidateを11.4583%超として評価する。正典evidenceは `docs/evidence/autonomous-final-sprint-2x2-submission-compatible-20260814.md`（SHA `e781b8783179475dd0e312dc4e01aa0e315659de9acf6f1e5edf09fd798f8175`）。巨大context pack・一般hardening・V4 semantic拡張・無目的native deck mutation・submissionは行わない。

## 16.33 Rule v0 fixed-policy deck interaction（2026-08-14）

2×2後のsubmission-compatible主線として、Rule v0を固定し、既存Archaludon deck候補を同一runnerでscreenした。12 opponent、両seat、repetition2、base seed `23200000` の48 discoveryでは parent `3/48` に対し `ae3075c2…` が `10/48`（+14.583pt）。しかし同じrunner/evaluatorでbroad24、base seed `23210000` の96 guardrailへ拡張すると parent `20/96=20.8333%`、ae `8/96=8.3333%`（−12.5pt）へ反転。48局point estimateは採用根拠にならず、aeはcandidate-only/NO-GO、384/longrun未起動。

`b92a3b55…` は構造上60行でもRule v0実行時48/48 `AGENT_INVALID; cabt terminal result unavailable`。性能0%とはせず、runtime/deck incompatibilityとして閉じる。先行並列parent rootの1 fault（`DeckValidationError ... got 0`）は同一seed workers=1 probeでDONEだったため、faultを勝敗へ変換せずserial fresh rootを正典化した。

正典evidence: `docs/evidence/autonomous-final-sprint-rule-v0-deck-interaction-20260814.md`（SHA `c393a7608b85991216ee1cb802b8ca9b8e1fd7cbdeb422f4d821e0ba9bb80fe1`） / `.json`（SHA `5b8a5e73f45c6300c64dc3693a82bffb3849dbadbd343844224025a5e0beb121`）。採用rootは parent48 summary `b8146fee…`、ae48 `5146501a…`、parent96 `02ae029b…`、ae96 `dcf38e30…`。採用armは全てDONE/fault0/draw0、native action/teacher/private情報不使用、authority全false。P0=Rule v0、D0=root deckは維持し、次は既存hard-negativeと重複しない新surfaceをResourceGovernor workers=12既定で48→96へ一つだけ進める。

## 16.34 Rule v0 root-deck neighborhood screen（2026-08-14）

submission-compatible P0=Rule v0 / D0=root deck上で、既存multisetと重ならない1-card近傍を2件だけworkers12で48局screenした。parentは4/48（8.3333%）、`1182 Boss's Orders→1213 Judge` は3/48（6.25%、−2.083pt）、`1152 Poké Pad→1185 Explorer` は1/48（2.0833%、−6.25pt）。全144局DONE/fault0/draw0、同一12-opponent/seat/repetition/seed/evaluator。両候補はcandidate-only/NO-GO、common24/384/longrun未起動。

正典evidenceは `docs/evidence/autonomous-final-sprint-rule-v0-root-deck-neighborhood-20260814.md`（SHA `12e799e0266042aec289a5e3027fe9f0e04ff2234258b10eaf60130dc73e8e6d`）/ JSON（SHA `4ddcae2dae569c33246bdbec8797ae1319f339bbbd2ff5fbacc6fa482adf7c3c`）。このroot-deck面を再試行せず、hard-negativeを更新して別surfaceをworkers12で48→96へ選ぶ。

## 16.35 並列実行の既定値（ユーザー指示、2026-08-14）

新規で独立な性能評価・集計・検証は、ResourceGovernorが正常である限り `workers=12`、`worker_recycle_games=16` を標準起動値とする。複数candidate/armの評価、同一条件の比較、独立した静的検証は可能な範囲で並列に実行する。直列実行は同一seedのfault再現、環境切り分け、数局のsmoke、共有artifact競合を避ける必要がある場合に限る。既存artifact・既存結果は再計算せず、fresh rootへ出力する。

この方針は研究用 evaluator、performance-first arena、deck mutation、Rule v0 screen、outcome-only screen/confirmation系の関数・CLI既定値へ反映した。回帰32 passed、py_compile、docs validator（13 canonical）、git diff --check PASS。evaluator SHA `d9f2a6b0851753b751a075a333e913a0936713a848451e545763a597267b8def`、arena SHA `9d2c78f5fcc10adbe9a08ee5c87e84913fe5d9b721f7d5e041b21827611f13a2`、Rule v0 screen SHA `b41b3bc374c7bb8dd4091120f5c04740041d9d3dd5e8386dfa70f87935fadf3c`、weighted deck SHA `443c8069c8f958957fcb2fdfed89002c1c3785ee33cc4cbbf73ef6adf202d152`、outcome confirmation SHA `fef9e5ec24d15379b81bc35fc76d7e12a1449ac9a7340595437bbe6e839cad07`、test SHA `3fd7fd60d711e20ed449436160c2d895434f6556c58c0df104d2cad8147fe3c4`。明示的 `--workers 1` は fault 再現・smoke 用に引き続き有効。

## 16.36 FINAL-SPRINT 2×2最新追補（ChatGPT引き渡し用）

native 72%をsubmission promotion最低条件にせず、SubmissionEligibleBestKnown（Rule v0×root deck 11/96=11.4583%）とV4 benchmark（V4 seed1×Archaludon 54/96=56.25%）を分離した。未測2セルを同一protocolで実測した。

| policy | root deck | Archaludon deck |
|---|---:|---:|
| Rule v0 | 11/96=11.4583%（既測） | 7/96=7.2917%、fault0（今回） |
| V4 seed1 | 0/96、全資格fault（今回） | 54/96=56.25%（既測） |

V4×rootのfault reasonは全96件 `DeckQualificationError: deck is missing core card IDs: [169, 190]`。これは0%の性能主張ではなく、V4 checkpointがArchaludon deck bindingを要求するためdirect root-deck cellが閉じているという実行可否結果である。Rule v0×Archaludonはseat0=4/48、seat1=3/48、W/D/L/F=7/0/89/0。今回の2セルは24 opponent×2 seat×2 repetition、base seed `14920000`、workers12/recycle16、fault-inclusive denominator、authority全false、native action/private/teacher labelなし。

### 成果物

- Bridge: `scripts/run_submission_2x2_performance_v1.py` SHA `287321632f442c48671988575ad3e4a0f8fb877526c369eaa98343d30457272f`
- Tests: `tests/meta_specialist/test_submission_2x2_performance_v1.py` SHA `2ca3a8f6037fe515c002e0309cb00c44ed7bf3736461b8f6d56a25d9b3d51bd8`
- Run root: `runs/final-sprint-autonomous/submission-2x2-20260814-v1/`
- Immutable ledger SHA `2604f5c7cd43b7a93295ef151c7cf3ac3002321035ef9b6bbe247de891952ce3`
- Derived summary SHA `49404201f7ef438d37deb9618e31351961263a187f4af9d4b9165b36c982bb53`
- Completion manifest SHA `e819337f6b72a1c615a1c8b325330c004de763ebe441094113cbf1ccf8a9e0c1`
- Evidence: `docs/evidence/autonomous-submission-compatible-2x2-performance-20260814.md`

既存の別runnerで15/96と記録されたRule v0×Archaludon rootは履歴として保持し、今回の同一2×2 bridgeによる7/96 fresh ledgerを最新比較根拠とする。V4 package feasibilityは別read-only調査で、production entrypoint未接続・card vocabulary gate未成立・dependency closure未vendoringのためsubmission_ready=false。Kaggle側torch/package/latency/RulesはEXTERNAL_CONFIRMATION_REQUIRED。Kaggle submission、permission変更、commit/push、Champion変更なし。

## 16.37 outcome-only alternating runtime（実装契約）

2×2と同一seed/fault-inclusive評価を再利用するため、research-onlyの `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py` と `scripts/run_submission_2x2_performance_v1.py` を追加した。candidate/controlの同一 `(opponent, seat, repetition, seed)` strataを一つの evaluator blockへ投入し、stage sequenceは96→384→768→1536、phaseは`POLICY_FIXED_SHORT`/`DECK_FIXED_LONG`。既定workers=12、worker recycle=16、authorityはtraining/promotion/submission/longrun全false。端末の安全性より速度を優先し、ResourceGovernor正常時は独立armを可能な範囲で並列化するが、既存artifact上書き・fault除外・native action/teacher/private情報利用はしない。

TDD focused testsは alternating runtime 6件＋2×2 bridge 3件の合計9 passed、py_compile、`python scripts/docs/validate_docs.py`（13 canonical）、`git diff --check` PASS。2×2 bridge SHA `287321632f442c48671988575ad3e4a0f8fb877526c369eaa98343d30457272f`、tests SHA `2ca3a8f6037fe515c002e0309cb00c44ed7bf3736461b8f6d56a25d9b3d51bd8`、alternating module SHA `9a06ba77a9e2b16ecced051b32463afd9c233139d41e4e49c887f712ffb99bda`。CLI dry-runは実V4 checkpoint/root deck specの封印まで確認済み。これは性能改善やsubmission-ready判定ではなく、次のP0/D0 candidate screenを同じ評価契約で繰り返すための実行層である。

## 2026-08-14 — ChatGPT引き渡し: 並列実行既定の追加適用

最新の運用指示は「並列化できる計算は並列、workers=12を既定、過度な安全側の直列化を避けて速度優先」。ResourceGovernorが正常な独立評価は workers=12 / recycle=16、arm間・candidate間・独立検証は可能な範囲で同時実行する。直列はfault再現、環境切り分け、数局smoke、共有artifact競合回避に限定する。weighted action、non-MAIN target、self-owned Rule v0 public screen/rollout、Tomato interaction wrapperの後発入口をこの既定へ更新し、既存fault再現の `--workers 1` は残した。回帰テスト `tests/meta_specialist/test_parallel_execution_defaults_v1.py` は2 passed。新規性能run/提出/commit/pushはこの追補では実施していない。

## 16.38 alternating runtime の実候補接続（ChatGPT引き渡し用）

synthetic fixtureだけで未接続だった `outcome_only_alternating_runtime_v1` を、実在 Tomato native policy と a73 deck candidate へ接続した。入力は Tomato `main.py` SHA `8908af5c…`、candidate deck SHA `90299c7d…`、control Tomato deck SHA `42165967…`、broad24 opponent pool、stage `POLICY_FIXED_SHORT`、base seed `25000000..25000095`。candidate/controlを同一 `(opponent, seat, repetition, seed)` strataで各96局生成し、実 evaluatorへ一つのblockとして渡した。

結果は全192局 DONE/fault0/draw0。candidate `61/96=63.5417%`、control `65/96=67.7083%`、delta `−4.1667pt`。candidate seat0=34/48、seat1=27/48、control seat0=34/48、seat1=31/48。runtime manifestは `workers=12` / `worker_recycle_games=16`、authorityはexecute/training/promotion/submission/longrun全false。判定は `NOT_PROMOTABLE`、384/768/longrunへ進めていない。

正典root: `runs/final-sprint-autonomous/alternating-tomato-a73-96-20260814-v2/`。stage manifest SHA `2d255e15c1d54b135415080a4dfcf3decdff5a3f01922c3411a7ffd223b667d8`、stage summary SHA `26b160f5f5f4ee833a42568665cc1b4c3b259561d4d1ddd315902d593b9d3443`、evaluator ledger SHA `0739fd3b78f96058e51048c629bcdf127d8fc7030cc38c6e46c52e776d13aefb`。wrapper SHA `775c8e7b199a17d1b9da12f40668c287fcc7050e0e5e017e3679a1fa1cb11347`、runtime SHA `9a06ba77a9e2b16ecced051b32463afd9c233139d41e4e49c887f712ffb99bda`。詳細は `docs/evidence/autonomous-outcome-only-alternating-tomato-a73-96-20260814.md`。

初回のstdin直実行はPython multiprocessing spawnが`<stdin>`を親スクリプトとして解決できず `BrokenProcessPool` になった。これはrunnerの性能結果ではなく起動形の問題であり、実ファイルwrapperへ切り替えて別fresh rootで再実行した。既存production、既存artifact、permission、training、promotion、submission、commit、pushは変更していない。次候補は既存hard-negative/multiset重複を除き、同じ実接続runtimeをworkers12で96局から再開する。

## 16.39 — alternating sweep: 95cc deck × Tomato native policy（ChatGPT引き渡し追補）

実在Tomato native policy（policy SHA `8908af5c…`、usage boundaryは`local_eval_only`）と、既存候補とのmultiset重複を除いた95cc deck（`1213 -> 1185`）を、同一broad24 opponent pool・同一seed/seat/repetition strataで交互評価した。研究専用のため、candidate-onlyでありBestKnown、submission-ready、training-ready、longrun-readyを意味しない。全runはfresh root、workers=12、worker recycle=16、authority（execute/training/promotion/submission/longrun）はfalse、全行fault0である。

| stage | candidate W-D-L / win rate | control W-D-L / win rate | delta | 判定 |
|---|---:|---:|---:|---|
| 96 | 70-0-26 / 72.9167% | 69-0-27 / 71.8750% | +1.0417pt | seat差を含むため昇格せず |
| 384 | 282-0-102 / 73.4375% | 269-0-115 / 70.0521% | +3.3854pt | control seat gap >5ptで自動昇格不可 |
| 768 | 544-1-223 / 70.8984% | 524-0-244 / 68.2292% | +2.6693pt | `POSITIVE_CONTINUE` |
| 1536 | 1089-1-446 / 70.9310% | 1059-1-476 / 68.9779% | +1.9531pt | stage上限、追加局なし |

1536局のseat別はcandidateがseat0=70.9635%、seat1=70.8984%、controlがseat0=70.0521%、seat1=67.9036%。候補は両seatでcontrolを上回ったが、native policy自体がsubmission/behavior権限を持たないため、性能差を提出可能なpairへ昇格してはいけない。96局だけで一時的に改善したpolicy variantsも、384局で反転した（Relicanth-first +6.25pt→−2.3438pt、threshold-lower +3.125pt→−2.6042pt）。Duraludon-firstは−13.5417pt、threshold-higherは−1.0417pt。Rule v0 × 95cc deckはRule v0 × root controlに対して−3.125pt（12/96 vs 15/96）であり、submission-compatibleな改善ではない。

正典rootとSHAは以下の通り。96: `runs/final-sprint-autonomous/alternating-tomato-95cc-96-20260814-v1/`（manifest `e84549f8806e87c771a1a3bf94a14531d4077ac9e2ea7c960c3762c302395915`、summary `c2c0deb6c801d1fd972db520a9980907e8e60eb9d86c75138f4ff392bdc595ee`）。384: `runs/final-sprint-autonomous/alternating-tomato-95cc-384-20260814-v1/`（manifest `4bf12200903cada1a392b73d14cb2fb2f13bf89ec6def9e17d9dc8e9f3302f3a`、summary `81caf098668ca0bedd7f46e997b53b81ef3053217e0ea0fd02a561125ac097bb`）。768: `runs/final-sprint-autonomous/alternating-tomato-95cc-768-20260814-v1/`（manifest `5305c2a368cca3be657ff3ce1640e199276f77ff7d5063b02aaea477af1b1228`、summary `f54f920087531b3198100a62a42615b1f01bf63b0ff9f62eb79c7081a9211380`）。1536: `runs/final-sprint-autonomous/alternating-tomato-95cc-1536-20260814-v1/`（manifest `7559a535bad446ffec795fd7068c57c4a7633da55dd2de660a8a2781f37cc80a`、summary `574c67771b6363c1944d96ce7156577da406380853229c0edf388f27bcacea04`）。統合evidenceは `docs/evidence/autonomous-outcome-only-alternating-sweep-20260814.md`（SHA `729f96b2111b38cd625c6f2f8660c1b323ee55359a2a88ca6617887e8f8ccb08`）。

現時点の実務的結論は「95cc×Tomato nativeは研究上の継続候補として保存するが、提出候補・BestKnown・長時間学習へ自動昇格しない」。次に実行する場合は、既存hard-negativeとdeck multiset重複を除いた別surfaceを1件だけ選び、workers=12の96局から再開する。native action/teacher/private情報の学習流用、permission変更、production変更、commit/push、Kaggle提出は未実施。

## 16.40 — 自己所有 Rule v0 → outcome-weighted Student 実ループ（2026-08-14）

提出互換P0=Rule v0をsubjectに、broad24 pool × seat0/1 × repetition2を実CABTで収集した。`runs/final-sprint-autonomous/self-owned-rule-bc-v1-20260814/` は96/96 DONE、fault0、4,814 decision examples、seed `20260814..20260909`、workers=12。datasetはRuleBCExampleのpublic + own-private actor viewのみで、opponent policy/action/teacher/private stateは保存していない。collection manifest SHA `2d4fed9dd17ce0b7fa779707d1ff9eb76943cb720653eba753de61b9273a9ff4`、dataset SHA `e024e723ebf8b8502a9e25f573e3f99596d641fd718d1e6600a3f11ea0a85b59`、weights SHA `73fa4de488c4782a4bfc79690b2fd6a0afd8562322fb5f27727c030b0db6c501`。

### Student candidates

Episode terminal WDLから win/draw/loss weightを指定し、deterministic Student v0へ渡した。自己所有Rule v0 baselineは `12/0/84 = 12.5000%`。

| variant | W/D/L | 勝率 | paired baseline→candidate | model SHA |
|---|---:|---:|---|---|
| outcome-weighted 1.5/1/0.5 | 10/0/86 | 10.4167% | LL78, WL8, LW6, WW4 | `1f2b8efd25b0b9b34dbdec4cd81fed699316559775fcff0428b84dc192dfd7fa` |
| plain 1/1/1 | 10/0/86 | 10.4167% | LL77, WL9, LW7, WW3 | `a4b646f9c3c1cdce0c5beac75a4203d00c339858463ea54ea1c248e11557c540` |
| winner-heavy 3/1/0.1 | 6/0/90 | 6.2500% | LL82, WL8, LW2, WW4 | `880d669fc16c7436314db94de922270f04b87f4eac342ff20c4b7712c6e08b7e` |

全候補はfault0だがbaselineを下回ったため、384/768/longrun/promotionには進めない。このBC方向は hard-negative。plain modelのclean-room archiveは `9aabb1edd0479fca825e214cd210103983f325ddd6cdb183a7081005d8405182`、`student-v0-rule-v0-fallback` として構築・検証済みだがChampionとmain defaultは不変。

### 実装・検証SHA

- collector `40cad3071571e2992fec25f1ffcd2838d8db5acdfcb2df7f6fee5e82377b9f31`
- weighting module `a6995c93c5e52e7134a9ae7c77004386d379ab704c96dfd8a5f3f67011f371ea`
- weighted trainer `12c08ea6c782543b9fa9920180be87cfa9c5584a1e3098b4423825ccf909b244`
- evaluator `f5476614ac18736c710ecb48a7f6761a3fc5ceebdd4a6a722245a0aec3ddc64e`
- weighted support `2ee5bfa5a9cd8bbc78d216b7377b49e3f7946ba47b88f9dcf4811e00d7489d11`
- evidence `docs/evidence/autonomous-self-owned-rule-bc-outcome-loop-v1-20260814.md`

focused collection/weight/trainer/evaluator tests 15 passed、py_compile/docs validator13/diff-check PASS。production evaluator/main/agents、native permission、training authority、submission、commit/pushは不変。次は既存hard-negativeとdeck multiset重複を避けた別deck mutationまたはpublic-only search/target surfaceをworkers12の96局から一つだけ選ぶ。

### Rule v1差し替え比較（非採用）

同一 generic evaluator（SHA `edea82d78496891b6c39f5bd3f8c2d1b417cb1365cc556916c118f960cd48983`）で `make_rule_agent_v1` を96局×2回実行した。初回は14/95 DONE + 1 fault、retryは10/95 DONE + 1 fault。faultは別seedで `DeckValidationError: deck must contain exactly 60 cards, got 0`。欠落seedの直接single-game probeはDONEだったが、並列runのfault-free性が閉じていないため勝率へ変換しない。初回root manifest `98c7d35749e8576378063075997c3f8469bc65cf1ba5994196d6752263ba41c8`、retry manifest `f0849302a0ed58e7c8b951a380ecd4a5722b9ede038cdaf24d936fad5cdf7669`。Rule v1のblind retry/384/longrunは停止。

## 2026-08-14 追加: V4 broad384・Hybrid Student・R2D3速度既定

### V4 broad384 実測

既存V4 checkpoint 2種を同一broad24 arenaへ各384局、`workers=12` / `worker_recycle_games=16`、24 opponent×両seat×repetition8で並列評価した。Archaludon longrun Wave4 seed1は `224-0-160-0 = 58.3333%`（seat0=113/192、seat1=111/192）、Lucifer19 outcome-weighted BC seed0は `221-0-163-0 = 57.5521%`（seat0=115/192、seat1=106/192）。両run `DONE=384 / fault=0 / draw=0`。96局より低く、native Tomato約72% benchmarkに届かないためBestKnown・promotion・submission・longrun continuationへ進めない。

成果物は `docs/evidence/autonomous-v4-broad-384-checkpoint-evaluation-20260814.md` に固定した。longrun root `runs/final-sprint-autonomous/v4-archaludon-longrun-wave4-broad-384-20260814-seed1-v1/`（manifest `8049f21569ef4ae3b0db77c1b95297778413fe7334bad2907468e839d2e10ca6`、summary `63aee06d34835f2442341a414e3efef3264e97246f4198e8d88928b28440efe5`、ledger `ed1638f26f3642e8cfa5ce13569c73ac521302b3f1790c34eead0adee9821d4e`）、Lucifer root `runs/final-sprint-autonomous/v4-lucifer19-bc-broad-384-20260814-seed0-v1/`（summary `49dde46ecc927c6fb662c58bea6d5a188d340626ff3bf04f53d18a21576c95af`、ledger `54800c6e6b0424b73f10890c73155374153e0a2c0553219971d1a88a3af0867f`）。共通 evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`。

### Hybrid Student v0 実測

Rule v0 BCから作ったStudent v0をsingle MAIN overrideへ限定する `HybridStudentPolicy` を追加した。margin threshold `.05`、非MAIN/multi-select/不正出力/例外はRule v0へexact fallbackする。契約・evaluator連携テスト、py_compile、docs validator、diff-checkはPASS。workers12/recycle16でfresh96を実行し `DONE=96 / fault=0` だったが、candidateは `6/96 = 6.25%`（seat0=3、seat1=3）でRule v0 baseline `12/96 = 12.50%`を下回った。Hybrid/Student経路はcandidate-only/NO-GO、384/longrunへ進めない。

実装SHA: evaluator `6a0838c0c844a7a99ee2c488aa4fd21ac9e5898c17d45dba4173d204020f40e2`、hybrid `97ddb7062895c842328dde32bb5ffe7f21f5dbab73d016b96ab88d67478f4716`、runtime `fec0b7d2fafaaac2a5f83b33fc20f9945721763c6d2911239a0b94dd78d91e5a`、tests `db9a4b98b221c23c7ca4db17b0015dda4cca00b1c917f7986f4c08efd447ea6b`。

### R2D3/PSRO速度既定

研究用R2D3/PSRO controllerのproduction profile `cabt_workers`を16→12、低層`durable_psro_payoff_prefix`未指定workersを1→12へTDDで統一した。smoke4、明示workers、fault再現workers1は維持。R2D3 contract suiteは `67 passed, 1 skipped`。実R2D3学習・旧artifact resumeは未起動で、旧production artifact不在の監査結果は不変。

### 現在の意思決定

最良のself-contained V4でもbroad384は58.33%。native Tomato約72%はlocal-eval-only benchmark。自己所有Rule v0 outcome-weighted StudentとHybridはともにnegative。次は旧R2D3を推測resumeせず、現source/catalogから新規cold-start bridgeを作るか、submission-compatible Rule v0/D0の新surfaceを1件選ぶ。独立評価はworkers12/recycle16で実行し、candidate positiveのみ96→384へ進める。production main/agents、Champion、permission、submission、commit/pushは変更しない。

## 16.41 — Tomato deck Rule v0 → Student weighted loop（ChatGPT引き渡し追補）

Tomato native deck（deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）をsubjectに、提出互換のRule v0自己軌跡をbroad24・両seat・repetition2で実収集した。収集rootは `runs/final-sprint-autonomous/self-owned-tomato-rule-bc-v1-20260814/`、96/96 DONE、fault0、3377 examples、workers12。collection manifest SHA `e69364c922d2f6dad8add3d3a148068a4be0ca5dc620a3cad1f915f413399dad`、dataset SHA `81e5cb97815263092a6e1be10126fac039a9f7c07486db0c41ac8941b861f29a`。

現Student v0はordered Skill schema `(selection_type=5, context=34)`を表現しないため、最初の学習は想定どおりfail-closedした。学習器へunordered化・擬似順序付与はせず、`--exclude-unsupported`を明示した場合だけ7例を除外するTDD修正を追加した。学習対象3370例（train2898/validation472）。trainer SHA `0b1251e8594ef2e26e59f333b49a8266634bb3ccf49f6aecf632c419eabde4fc`、test SHA `267d6df6e7682ab30f13c3bdd1ee7fe60f20670a7cb3573b606eb5a89b9b0d04`。

### 同一seed 96局 screen

same broad24・same Tomato deck・base seed `20269000`、workers12/recycle16でRule v0、weighted/plain/heavy Student、weighted hybridを並列評価した。

| policy | W-D-L | score | seat0/seat1 | judgement |
|---|---:|---:|---:|---|
| Rule v0 | 13-0-83 | 13.5417% | 6/7 | control |
| weighted Student (1.5/1/0.5) | 15-0-81 | 15.6250% | 7/8 | +2.0833pt |
| plain Student (1/1/1) | 12-0-84 | 12.5000% | 4/8 | negative |
| heavy Student (3/1/0.1) | 13-0-83 | 13.5417% | 9/4 | seat imbalance |
| hybrid weighted | 12-0-84 | 12.5000% | 8/4 | negative |

weighted model SHA `8d9686df9900bb09b862b8ec5fda8a04d3c7e6d61de921588bde506715b8a282`、plain `d390b2058933f53e1163f3506968a4d933a3766bb6df055789d1f2a35a2aa402`、heavy `350b314fbef178079cdcc98aecfcae05d0e5e027341e6fb7b174cda8f621ef91`。

### 384局 confirmation

96局でpositiveだったweightedだけを未使用base seed `20270000`で384局確認し、Rule v0を同一scheduleで対照した。初回recycle16は各arm192局後の一斉recycleでspawn停滞したため、partial rootは不採用・保全し、fresh retryをrecycle64で完走させた。

| policy | W-D-L | score | seat0/seat1 |
|---|---:|---:|---:|
| Rule v0 | 50-0-334 | 13.0208% | 30/20 |
| weighted Student | 52-0-332 | 13.5417% | 29/23 |

retry summary SHAはweighted `0d8cc46a574de2e3cbb1885d238c7548011ffcac67dcff26cfd4b0a19497cc7d`、Rule `b636bd94ca8425e76ce684c7b023359c01d1ec2b6bcdada227c464e3b357b81e`。差は+0.5208ptに縮小し、768/longrun/promotion/submissionは起動しない。

正典evidenceは `docs/evidence/autonomous-self-owned-tomato-rule-student-loop-v1-20260814.md` SHA `09d9b7737c823e2e0348f80e6640fe1fda7bf57bbf5807605a3d8cdc6ff1456d`。Rule v0、Champion、production main/agents、既存artifact、permission、commit/push不変。

## 16.42 — Tomato Rule v0軌跡384局からの再学習（ChatGPT引き渡し追補）

96局データでのStudent差が小さかったため、同じTomato deck・Rule v0・broad24で追加384局を収集した。root `runs/final-sprint-autonomous/self-owned-tomato-rule-bc-384-v1-20260814/`、base seed `20271000`、workers12、全384 DONE/fault0、14542 examples。manifest SHA `e3d3d077082e1071a5866104ffdd9a95bf26963470d0455b0f698b65b779662a`、dataset SHA `5b3dada0d65ac47a1880dad47a096cd23582a28edee45b9eaef332e24c5fc745`。

ordered Skill `(5,34)` 7例は従来どおり学習から除外し、unordered化はしていない。3 variantを並列学習（各trainable14535例）し、未使用base seed `20272000`でRule v0と各384局をworkers12/recycle64で並列評価した。

| policy | W-D-L | score | seat0/seat1 |
|---|---:|---:|---:|
| Rule v0 | 50-0-334 | 13.0208% | 26/24 |
| weighted | 49-0-335 | 12.7604% | 27/22 |
| plain | 50-0-334 | 13.0208% | 26/24 |
| heavy | 54-0-330 | 14.0625% | 30/24 |

heavyの+4勝（+1.0417pt）が最大だが、weighted/ plainでは再現せず、強い昇格根拠ではない。全arm fault0/draw0。Student loopはデータ収集→重み付き学習→同一条件評価まで実接続済みだが、現時点はcandidate-only/NO-GO、768/longrun/promotion/submission未起動。

再学習model SHAは weighted `abf37436c82977eb38eb70549e42e9761b62ba56e693e9750d4384653927d155`、plain `4686f256d01fe2e4ed1c49a721d3750f96f8381a6ed91adf71ee41f008890561`、heavy `9d914648bab08ff2ce1c15746fe4676da79de81a7e78987f6b04337d1dd5ac73`。evaluation summaryは weighted `8c1940d7…`、plain/Rule `c51b51a5…`、heavy `a37a0779…`。詳細と全SHAは `docs/evidence/autonomous-self-owned-tomato-rule-student-loop-v1-20260814.md` に追記した。

## 16.43 — Night Stretcher deck mutation 384 confirmation（ChatGPT引き渡し追補）

Tomato native parent（deck SHA `42165967…`, policy SHA `8908af5c…`）から `1152→1097 Night Stretcher`（candidate deck SHA `b49944fa…`, multiset `46abcda0…`）を比較した。weighted48ではparent34/48、candidate35/48（+2.5259pt）、common24ではparent63/96、candidate68/96（+5.2083pt）だったため、同一24 opponent・両seat・repetition8の384 confirmationへ進めた。

384 confirmationはfresh root `runs/final-sprint-autonomous/resource-aware-tomato-night-confirmation384-v1-20260814/`、base seed `22730000`、workers12/recycle64。candidate262-0-122/384=68.2292%、parent284-0-100/384=73.9583%、delta −5.7292pt。全768行DONE/fault0、seat192/arm/seat、24 opponents×16/arm、paired seed/strata一致、GID unique、ResourceGovernor normal、restart/kill0。局所48/96 positiveは384で明確に反転したため、Nightはcandidate-only/NO-GO、768/longrun/submission未起動。

正典evidence `docs/evidence/autonomous-resource-aware-tomato-night-confirmation384-v1-20260814.md` SHA `5e2451c6d72ace5c00feb2905c4ec219c311e81f4b1297c2a4934931022c0b6f`。summary `6f8baa40…`、ledger `92fc3055…`、final `5f75ad7f…`、wrapper SHA `176f3c5f…`。この結果は、META_TRAIN weighted48/common24の小局positiveだけでCandidateを昇格させず、384 confirmationを必須にする根拠である。

## 16.44 — Tomato native deck overlay 1086/1192 weighted48（ChatGPT引き渡し追補）

Night384反転後、既存deck multiset119件とopponents全件を除外した新規surfaceをworkers12/recycle16でscreenした。Tomato parent（1244 Full Metal Lab維持）は36-0-12、weighted score 0.762530779。`1244→1086 Buddy-Buddy Poffin` は31-0-17、0.644721496（−11.7809pt）、`1244→1192 Carmine` は27-0-21、0.554529435（−20.8001pt）。全144局DONE/fault0/draw0、seat24/24、paired key/seed/GID gate PASS、ResourceGovernor warmup fault0、authority false。両候補は明確negativeでcommon24/384/longrunへ進めず、candidate-only/NO-GO。

Root `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1086-1192-weighted-v1-20260814/`。manifest `1a7e2695789d3fc5b235c6ce6c4b0065e7411a3ab00791d180d8d9a82847be6b`、summary `dbc54b333d2dee42ea450ef0d66c526dc1f1d88991782f3bddf6900a1d06b115`、MD `bf211d531d6278a0a966eac419f84112064b214572fb0ecdb8bc35e713514d63`、final `5bee64cbf480ac0b792e44c1e2c9362c5da7c07bf800ac5c932ddb721be6ef77`、ledger `ea215df059331992face061eb8795883ddb1e8ba6e222816a9e2b2addf0f9ca2`、wrapper `b90b681fbe31a8a19d60128ed0d5566b47707ec1db132d1838bdefa7b3043fbf`、evidence `b4b1d5ab9ce0657ee5c7c3a2d2ccc527fa4f8167f16f890d1b47cbea4f30d57f`。

## 16.45 — Tomato native Supporter置換 1194 Colress（ChatGPT引き渡し追補）

既存deck multiset119件とopponents全deckを除外したSupporter置換を評価した。weighted48では親25/48、`1182→1194 Colress's Tenacity` 34/48（+17.4900pt）、`1227→1194` 37/48（+23.2210pt）。common24では1182候補67/96対親64/96（+3.125pt）だったが、1227候補は67/96対親69/96（−2.083pt）で停止した。

1182候補の384確認は281/384=73.1771%対親256/384=66.6667%（+25勝、+6.5104pt、fault0）。しかし768確認では候補535/768=69.6615%対親533/768=69.4010%（+2勝、+0.2604pt）に縮小し、longrunへ進めない。全1536行DONE/fault0、両seat384/arm/seat、24 opponent×32、paired key/seed一致、GID unique、workers12/recycle64、warmup fault0、restart/kill0。勝ち差は特定opponentへ集中し、全体の継続改善とは判定しない。

384 root `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-confirmation384-1182-v1-20260814`、summary `a1eb3a52e11ad105cde2fa6db0cba310ff5788d42abfce8244f5946df1b418f0`、ledger `c531e5a57e903470e8d7391895885efaf21842f069db648b62892ce513c9be4d`。768 root `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-confirmation768-1182-v1-20260814`、summary `aaf8cd27c81789d8c89c4a21f86676be714be2e3fc5a57beeb82e3d8181d09a5`、ledger `615e0c95dbc372fe023fb8cc6bb26bb325905cea93c02f38f523c3130c44b2d6`、wrapper `67c2f2e784a80fdb22afac071a163630859a4496bfe3cb2384625b1876151b16`。統合evidence更新SHA `c04b313d5c0410284b3712c22fbf938ad84c3b77a756f6246b3d42efe91148ab`。

## 16.46 — Tomato native Supporter置換 1219 Petrel（ChatGPT引き渡し追補）

Colress系列の縮小後、未評価Supporter `1219 Team Rocket's Petrel` への置換2件をworkers12/recycle16でweighted48 screenした。親は35-0-13（0.715549142）。`1182→1219` は31-0-17（0.647951598、−6.7598pt）、`1227→1219` は34-0-14（0.701852174、−1.3697pt）。全144局DONE/fault0/draw0、seat/paired/seed/GID gate PASS、authority false。両候補はcommon24/384/longrunへ進めず、Petrel Supporter面はhard-negativeとして停止した。

Root `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1219-support-weighted-v1-20260814/`。manifest `cd16c14bf988a019945777567914f72de0f1419d300111687fcb70445d1624ab`、summary `d4982c677b822ad86a2e1a0abdfa571217440ae2d0de1f0d48ec695ff2066f28`、evidence `docs/evidence/autonomous-resource-aware-tomato-overlay-1219-support-weighted-v1-20260814.md` SHA `4a406b714c1900a0bace2d7bd8d45e6bf5854eff8d53535403933bd7caac743b`。

## 16.47 — 提出互換 Rule v0 root deck Colress確認（ChatGPT引き渡し追補）

P0 Rule v0＋root deckで `1192→1194 Colress's Tenacity` を研究用に評価した。weighted48は親5/48、候補7/48（+3.5181pt）、common24 retry-v3は親8/96、候補11/96（+2.6042pt）だった。384 confirmationでは親40/384=10.4167%、候補41/384=10.6771%（+1勝、+0.2604pt）まで差が縮小した。全768行DONE/fault0/draw0、workers12/recycle64、absolute opponent paths、seat/paired/seed/GID gate、heldout0、authority false。初回common24の相対path fault rootは不採用・保全し、修正retry-v3だけを採用。提出互換候補だが改善を再現できず、768/longrun/promotion/submissionへ進めない。

weighted manifest `6aa07690b4f605309418e1a574bd24050758412b185a3b2285974b77f052b417`、common24 retry manifest `98d484667946fc24e9cf94b335c75aa702c792d24e61c1a71a283f77931be56b`、384 confirmation manifest `0d53eba02b048b8f4cd53dd5ec6749d5f03b7aa43051a220a30629bd1ec1c07a`、summary `2d8c52b1b1fa91fa56c4afefe9c3cfe9290154bc071714eaa599864112abffeb`。

## 16.48 — 提出互換 Rule v0 root deck Item面（ChatGPT引き渡し追補）

新規 `1141→1122 Pokégear 3.0` と `1123→1121 Ultra Ball` をP0 Rule v0＋root deckで評価した。weighted48では親3/48、Pokegear5/48（+4.7211pt）、Ultra Ball7/48（+6.6071pt）だったが、common24では親17/96、Pokegear10/96（−6.7708pt）、Ultra Ball12/96（−5.2083pt）へ反転した。全288局DONE/fault0/draw0、workers12/recycle16、seat48/48、24 opponents、paired seed/GID gate、heldout0、authority false。両候補は384/longrunへ進めず、Item面をhard-negativeとして停止した。

Weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-items-v2-weighted48-20260814`、manifest `f2b536fa5baaf4d7c9d2f5da5dfee1700bc7747a23c5adcb276b48ba621ee01e`、common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-items-v2-common24-20260814`、manifest `6c061c6c4a60d21d2e7924f0ae3ae9f2b18b1884d77fdd3b29cac83217440a83`、summary `118f3c5f44f7f73deb2eb576f750a6aa5599192b6332a39f60d53ef03f86a57f`。

## 16.49 — 提出互換 Rule v0 root deck Tool/Stadium面（ChatGPT引き渡し追補）

`1159→1158 Maximum Belt` と `1252→1245 Festival Grounds` をP0 Rule v0＋root deckで評価した。weighted48では親weighted score 0.0253087、Maximum Belt 0.0881478（+6.2839pt）、Festival Grounds 0.1108048（+8.5496pt）だったが、common24で親9/96に対しMaximum Belt7/96（−7.2917pt）、Festival Grounds10/96（−4.1667pt）へ反転した。全288局DONE/fault0/draw0、workers12/recycle16、seat/paired/seed/GID gate、heldout0、authority false。384/longrunは起動せず、Tool/Stadium面をhard-negativeとして停止。

Weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-tool-stadium-v3-weighted48-20260814`、manifest `64d7c5e685cf2d743fb6d2f9d9e51665b2453c6f429643b981a4d166ab088f9`、common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-tool-stadium-v3-common24-20260814`、manifest `d970ef19a7c290e785c4a5dd1bad84d9cfaa41df26a5350d5f682bb9402f5781`、summary `7058abc988944be84f0f0ba9b6c2cfaf50be2c325a3650726d674a3792bd151b`。

## 16.51 — 提出互換 Rule v0 MAIN ABILITY+120 screen（ChatGPT引き渡し追補）

既評価のATTACK/PLAY/EVOLVE/ATTACH/ENDと重複しないMAIN ABILITY+120 research copyをweighted48でscreenした。candidate7/48（14.583%）対control3/48（6.25%）、相対+8.333pt、fault0、paired loss→win6 / win→loss2。ただしcandidate seat0=5/24/seat1=2/24、override/fallback coverage telemetry unknown、absolute BestKnown未達。coverage gate不成立のためcommon24/384/longrunへ進めずscreen-only/NO-GO。root `runs/final-sprint-autonomous/nonmain-ability-plus-120-weighted48-20260814-v1`、manifest `d8f62bd163c8bf70f1fb6e1aea2abae1f7fe38ca801c8c5071c3d21d504cb114`、games `8856b40bb24caf3e3a5c7ca40ac11bb53a28ea8626d2b3da75f5f116d79a6016`、evidence `e3c74d98e985688599062eb85ae2af4f1bf8fc2a4b1233c0f67880841dafe2d6`、module `8aee8bb93eb7555408e28c46029792a3201f5e4cf31f340d29a4acad577d850d`、runner `02a279588cd872a4e125180f64b996dd2449e23b1fffdd3334a3aa8d21d93224`、tests `421de2c39a799ac1527c9d9301b60e2b31ccddee15989e830a94b21ad07820f8`。focused4、py_compile/docs validator/diff-check PASS、production main/agents不変。

追加Telemetry smokeでcandidate/control各2局をfresh再測定し、全4 DONE/fault0。candidate telemetry `available=true`だがeligible=0/override_applied=0、unit合法ABILITY fixtureのみeligible=1/attempt=1/applied=1。旧48局は未instrumentedで後付け再計算せず、ABILITY面はscreen-only/NO-GOを維持した。Telemetry evidence `docs/evidence/autonomous-rule-v0-main-ability-telemetry-smoke-20260814.md` SHA `257c7ac1…`、focused5 pass。

## 16.50 — 提出互換 Rule v0 root deck Energy面（ChatGPT引き渡し追補）

`6→20 Rock Fighting Energy` と `6→11 Mist Energy` をP0 Rule v0＋root deckで評価した。weighted48では親に対し+2.6837pt/+2.0186ptだったが、common24で親11/96、Rock Fighting8/96（−3.125pt）、Mist7/96（−4.1667pt）へ反転した。全288局DONE/fault0/draw0、workers12/recycle16、seat/paired/seed/GID gate、heldout0、authority false。384/longrunは起動せずEnergy面をhard-negativeとして停止。Weighted manifest `34a7df4bce554bcee770e497edefe4f07d79fb098394e99f6a69e985bbec79e2`、common24 manifest `6a3e2dc6f0a56ee6bffac1b864c51143d33897846eb427e49da6ba3bd26e52f2`、summary `2566080c2309565c9226891c5940133fa2dbdafd6fbe9c33947d89d79e2df5ec`。

## 16.52 — Rule v0 MAIN ABILITY+120 telemetry再測定（ChatGPT引き渡し追補）

ABILITY scoreだけを+120するresearch-only copyをtelemetry付きfresh48で再測定した。candidate5-0-43/48（10.4167%）、control7-0-41/48（14.5833%）、delta−4.1667pt、全96局DONE/fault0、各seat24。candidate telemetryはobservations2126、main1375、eligible147、override attempts1375、applied44、fallback0。coverageは取得できたが性能が負のためSTOPし、common24/384/longrunへ進めていない。同surface再実行禁止。Root `runs/final-sprint-autonomous/rule-v0-main-ability-plus-120-telemetry-weighted48-20260814-v3`、screen SHA `c062845c…`、ledger `46eade78…`、evidence `3057ad6a…`。workers12/recycle16、authority false、production不変。

## 16.53 — Rule v0 MAIN priority ATTACK-first copy（ChatGPT引き渡し追補）

既存overlayと独立な研究用policy copyで、MAIN優先順を `ATTACK > PLAY > ATTACH > EVOLVE > ABILITY > END` に固定し、non-MAINはRule v0へexact fallbackした。初回96はcandidate13/96対control9/96（+4.1667pt）だったがcandidate seat gap6.25pt。別seed confirmation96はcandidate12/96対control13/96（−1.0417pt）へ反転した。両stageともworkers12/recycle16、DONE192/fault0/draw0。再現性がなく、384/768/longrun/promotion/submissionへ進めずcandidate-only hard-negativeで停止した。materializer SHA `b59617f4…`、test `b5bfd137…`、generated policy `8f2c6f5b…`、初回manifest `40cd17f7…`/summary `244874d7…`、confirmation manifest `989f28bb…`/summary `fb34d284…`、evidence doc `docs/evidence/autonomous-rule-v0-priority-attack-first-alternating-20260814.md`。production main/agents、deck、submission package、permissionは不変。
