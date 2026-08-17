# MAGE-PTCG Autonomous Meta-Fine-Tuning 完全統合 context pack

作成日: 2026-08-13
目的: このファイルだけを外部ChatGPTへ渡し、ChatGPTがローカルリポジトリを参照できなくても、現在の目的、実験結果、一次artifact、SHA、権限境界、失敗理由、設計、未解決点、次の実行条件を再構成できるようにする。

この文書は、以前の短いcontext packや会話の要約を補助資料としてではなく、単独で判断材料になるように書いている。後の章ほど新しい。古い途中経過と最新判定が矛盾する場合は、最後の「最新状態・意思決定」を優先する。

## 0. ユーザーの本来の目的

既存の強い deck + agent population を初期資産とし、大量の opponent deck/agent pool から現行上位メタ分布を構成する。その分布に対して意図的に fine-tune / overfit し、deck と policy を交互最適化する。native BestKnown を超える提出可能な pair と、性能が継続向上する長時間学習ループを完成させる。

この目的に必要な最終成果は、単なる学習器、単発の勝率、synthetic overfit、research-only manifestではない。最低限、次をすべて満たす必要がある。

1. immutable native control を保持した common protocol 評価。
2. public / actor-visible のみを用いた漏洩のない META_TRAIN 分布。
3. hard-negative を反映して次iterationへ更新できる curriculum。
4. policy-fixed / deck-fixed の交互最適化state、candidate identity、checkpoint、rollback。
5. native BestKnown を複数独立blockで再現的に上回る pair。
6. bundle-allowed self deck、portable runtime、CABT legality、latency、fallback、dependency、privacy、clean-roomの閉じた提出package。
7. META_DEVを学習へ混ぜず、性能が悪化したらrollbackし、良化時だけ次iterationへ進む longrun。
8. 全ての判断に一次artifact path、file SHA、semantic SHA、protocol、seed、fault分母を付けること。

現在は1〜4の安全なmaterializationを進めているが、5〜7は未達である。

## 1. 最重要の現状結論

現時点の機械的分類は次の通り。

| 軸 | 現在の結論 | 何を意味するか |
|---|---|---|
| EvaluationBestKnown | tomatomato_archaludon native、暫定 | common24 pooled1536で現行nativeの点推定首位 |
| BestKnownArchaludon | Tomato native、暫定 | Plamen mutationはcommon24でTomatoを超えていない |
| TrainingEligibleBestKnown | Tomato primary + Lucifer control、bounded | sealed snapshot / training-local範囲に限る |
| SubmissionEligibleBestKnown | Strong Asset poolには無し | poolはlocal_eval_only。現状のpackage anchorはRule v0 |
| GlobalBestKnown | unresolved | slow5 / R7 / permission / package / protocol coverageが残る |
| Student v3 θ0 | NO-GO | nativeより60pt超弱い96局実戦結果 |
| Student v3 AWR | NO-GO | nativeより65pt超弱い96局実戦結果 |
| guarded score-bias | NO-GO | 96局の正差が384局で消失 |
| Plamen mutation | candidate-only | parent-relative positiveはcommon24で小差・block反転 |
| Tomato policy x mutation deck | candidate-only | +1.04ptで事前+3pt gate未達 |
| Full6 teacher population | blocked | ordered 4件とcross-split near-duplicate 1 component |
| Dynamic curriculum | materialization only | META_TRAIN weighting準備。学習権限は付与しない |
| Longrun | NO-GO | candidate性能、META_DEV、package、rollbackが未閉鎖 |
| Kaggle提出 | 未実施 | 明示承認なしに提出しない |

一言で言うと、現在は「学習を長く回せば勝てる」段階ではなく、native controlを保った安全なmeta-overfit loopの入口を作り、次に実性能screenへ移る直前である。

## 2. 使用モデル・agent運用と権限

ユーザー指示により通常の実装・調査・資料作成はGPT-5.6 Luna maxを基本laneにする。Solは設計判断に不要として停止。別agentを使う場合もLuna max、責務分離、同一ファイル同時編集禁止とする。

モデルやeffortを上げても次の権限は付与されない。

- git commit / push
- Kaggle API / CLI submit
- production main.pyの変更
- Champion変更
- teacher native code / deckのas-is package
- permission boundaryの拡張
- private informationの利用

新規CLIはDRY_RUNを既定にし、executeは独立したexecutorと全authority gateがない限りfail-closedにする。現在、CABT、実学習、longrun、submissionの性能プロセスは起動していない。

## 3. Native BestKnownの基準

### 3.1 Common24 pooled1536 native ranking

protocolは同じ24 opponent、両seat、各384局のseed-disjoint blockを4本、fault-inclusive denominator、DONE onlyのW/D/Lである。

| rank | pair | W-D-L-F | score | seat0 wins | seat1 wins | policy SHA | deck SHA |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | tomatomato_archaludon | 1107-0-429-0 / 1536 | 72.0703% | 561/768 | 546/768 | 8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e | 42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e |
| 2 | lucifer19_battlecore | 1103-0-433-0 / 1536 | 71.8099% | 554/768 | 549/768 | c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c | fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6 |
| 3 | plamen06_steel | 1102-0-434-0 / 1536 | 71.7448% | 567/768 | 535/768 | 8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3 | fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6 |

一次evidence:
docs/evidence/strong-asset-top3-pooled1536-20260812.md
SHA: e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863

Tomato-Lucifer差は0.2604pt、Tomato-Plamen差は0.3255ptであり、1536局でもnear-tie。したがってTomatoは「globalで絶対最強」ではなく、現行common protocolにおける暫定native anchorである。

4つのnative ranking一次artifact:

- block1 asset_ranking SHA 58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b
- block2 asset_ranking SHA 776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e
- block3 asset_ranking SHA e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7
- block4 asset_ranking SHA 27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5

### 3.2 96局screenが危険である証拠

fast96 screenではPlamenが79.17%、Tomatoが76.04%、Luciferが72.92%だった。しかし384局confirmationではTomato 72.6563%、Plamen71.6146%、Lucifer69.2708%へ順位が反転した。2 block pooled768ではTomato71.875%、Lucifer71.354%、Plamen71.224%。4 block pooled1536では上表。

今後の昇格ルール:

- 96局: screenのみ。
- 384局: confirmation。事前+3pt、fault0、seat gate。
- 768局: independent block再現。
- 1536局: stable evaluation evidence。
- 96局単独でBestKnownを宣言しない。

### 3.3 未測定 asset

slow5:

- kinoshita_pimc_search
- ozawa_metal_psychic_search
- tientrum_alakazam_search
- water_box_search
- waterbox_search_v3

検索予算が大きく、diagnostic runは2/240程度で終了した。R7はsmoke=false。これらを黙ってrankingから除外できないため、GlobalBestKnownはunresolved。

## 4. Permission / package / submission

### 4.1 Pool境界

opponents/pool_manifest.jsonは102 assetすべてusage_boundary local_eval_only。pool assetはlocal arenaでの評価には使えるが、teacher behavior collection、training、submission、as-is bundleを自動的には許可しない。

主要asset:

| asset | boundary | smoke | 現在の扱い |
|---|---|---:|---|
| tomatomato_archaludon | local_eval_only | true | training-local sealed primary |
| lucifer19_battlecore | local_eval_only | true | training-local sealed control |
| plamen06_steel | local_eval_only | true | mutation evaluation、trainingは別permissionが必要 |
| public_archaludon_cinderace_r7 | local_eval_only | false | never package / unresolved ranking |

pool manifest SHA:
e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca

broad pool config SHA:
832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b

### 4.2 Training-local

Tomato sealed snapshot manifest SHA:
b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff

Lucifer sealed snapshot manifest SHA:
d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84

この許可はtraining-local recordの利用に限定される。native code、native logits、native source、external deckのsubmission利用や、behavior_allowedを自動で許可するものではない。

### 4.3 Submission anchor

Strong Asset native pairは提出不可。現行の技術的fallback package anchorはRule v0 + root deck。

- production policy SHA: 750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b
- root deck SHA: 2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19
- archive: runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz
- archive SHA: da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a
- clean-room smoke: 2 DONE / 0 fault / 0 illegal

これはfallback packageの有効性であって、native BestKnown超過の証拠ではない。自前studentを提出候補にするには、自前bundle_allowed deck、teacher provenance、portable runtime、dependency closure、CABT legality、latency、fallback、privacy、clean-roomを別個に閉じる必要がある。

## 5. これまでの性能実験

### 5.1 Student v3 θ0 / AWR common24 96

同じTomato native control:

| arm | W-D-L-F / 96 | score | native差 | 判定 |
|---|---:|---:|---:|---|
| native Tomato | 66-0-30-0 | 68.750% | control | baseline |
| Student θ0 | 7-0-89-0 | 7.2917% | −61.458pt | NO-GO |
| Student AWR | 3-0-93-0 | 3.1250% | −65.625pt | NO-GO |

全arm DONE、fault0、draw0、両seat、common24 strataは成立した。性能がnativeから大きく離れるため、同型Student v3/AWR/hard BCを384→1536へ延長しない。

reconciliation:

- θ0 output SHA 81bfda4621ec1fc6952dd781e04a569d41c5e5389e5dabe821f2ecce03fab0bf
- AWR reconciliation SHA 6482a3f613af330985bc0d5bcb829884f744a20433cc6e612d71aae189f38b93
- evaluator/provenance evidence SHA 88897f1a496318893e3d592f3e44c438f42c5f4c6fbb036f4b07d07b3c033f56

### 5.2 Guarded score-bias

Tomato nativeを先に呼び、bounded score overrideを行った研究candidate:

- 96局: candidate75/96、native66/96、+9.375pt、fault0。
- 384局: candidate262W-1D-121L、native274W-0D-110L、−2.9948pt、fault0。

evidence SHA:
5efd647a94684d94893d022dda0b37e3eaecb66f3168665d6c8c8e297e8e6e48

confirm summary SHA:
08adda12d4b87f4422caa0219d8f4ccbf8511e45bad4df3eb1c309231975d2c4

96局の正差を採用せず、384局でNO-GO。

### 5.3 Plamen mutation 23-opponent parent-relative

candidate ID:
aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9

candidate deck SHA:
9f413dd4423c2a90f40fa25753f01a610607fa1e0be8c54a9aee50b1285639e7

deck multiset SHA:
a9b45c1d90672bf46ad67bc61e4f8a7382a44e5745d27f1b823495655909f227

Plamen policy SHA:
8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3

23 non-self opponents、両seat、4x368、1472局:

- candidate 1101W-1D-370L-0F = 74.8302%
- parent native 1072W-0D-400L-0F = 72.8261%
- delta +2.0041pt
- 4 block全てcandidate-positive

しかしnative rankingは24 reference。23 opponentのparent-relative結果はglobal BestKnownの根拠にできない。

### 5.4 Plamen mutation common24

同じ24 opponent、両seat、各384、4 block:

| block | candidate | parent native | delta |
|---:|---:|---:|---:|
| 1 | 277/384 = 72.1354% | 268/384 = 69.7917% | +2.3438pt |
| 2 | 274/384 = 71.3542% | 279/384 = 72.6563% | −1.3021pt |
| 3 | 288/384 = 75.0000% | 260/384 = 67.7083% | +7.2917pt |
| 4 | 260/384 = 67.7083% | 282/384 = 73.4375% | −5.7292pt |
| pooled | 1099/1536 = 71.5495% | 1089/1536 = 70.8984% | +0.6510pt |

3072 rows DONE、fault0。block2/4で反転し、Tomato native72.0703%未満。candidate-only。

common protocol evidence:
docs/evidence/autonomous-deck-mutation-common-protocol-20260813.md
SHA a53ceea45f7dd3975dc7a7a5f35deaedbd25f77e0881001088c6b5f45badab6d

pooled mutation manifest:
runs/final-sprint-autonomous/deck-mutation-plamen-v1/common24-pooled-confirm-3f6451-1536/pooled_manifest.json
SHA 6ac5a4ed93a4214c4cd0e41a37665b64df96b4ba922143fb5e17d39be7325144

### 5.5 Plamen mutation vs Tomato direct

Plamen policy + mutation deck vs Tomato native, common24 384:

- candidate 274/384 = 71.3542%
- Tomato native 275/384 = 71.6146%（draw1、score-rate 71.7448%）
- delta −0.3906pt
- fault0、second block停止

summary SHA:
f5a8f077f111881b606821fc312f2aa663fd57394f10724bb131b5e4f87429ba

### 5.6 Tomato policy + mutation deck interaction

- candidate264/384 = 68.7500%
- Tomato native control260/384 = 67.7083%
- delta +1.0417pt、fault0、draw0
-事前+3pt gate未達、second block停止

summary SHA:
ba6486331ec8171fa9848cd22e792b55496726b2b7e4efd5d1ba7cf897b41e4a

interaction evidence:
docs/evidence/autonomous-deck-mutation-tomato-policy-interaction-20260813.md

### 5.7 過去のStrong Asset BC / residual系列

過去のLucifer V4 BCは96局の短期+5/+3勝が384局で消失。BC seed0 211/384 vs Wave6 228/384、BC seed1 229/384 vs Wave6 237/384、fault0だが非昇格。

Tomato public-state AWRはoffline NLLが下がってもbroad384でnative未達。残差armはblock/seed反転しNOT_PROMOTABLE。これらの追加sweep、longrun、Champion変更は停止済み。

## 6. Teacher catalog / Student v3 data path

### 6.1 v2b catalog

fresh v2b catalog integrity:

- file SHA 8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4
- semantic SHA da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e
- Tomato examples5110
- Lucifer5245
- Plamen5275
- Grimmsnarl8104
- Rocket5899
- Nihei7051、unlabelled2
- 各teacher96/96、fault0、seat48/48

catalog integrity PASSは結合split leakageや学習性能を保証しない。

### 6.2 Full6 blocker

36,684 decisionの分類:

- unordered set対応36,680
- Grim ordered schema 5:34が4件
- global non-ubiquitous near-duplicate cross 1 component（ID prefix 5a996ab25264...）
- blocked descriptor file SHA a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7
- semantic SHA f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2
- ready=false、published_rows=0、silent_drop=false、reproduction_skipped=true

orderedを黙ってdropしない。pointer-head exact対応か、明示quarantine付きunordered-only datasetという別purposeが必要。near-duplicateはconnected component単位でsplitを揃える。現時点でFull6を学習入力にしない。

### 6.3 Tomato clean lane

Tomato formal bridge:

- bridge file SHA 8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983
- semantic SHA 3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0
- source SHA 47ae3578b70fab181931fe6bdfa08eae36b5676e1dadc4b5df50f02c893eba9b
- 5110 rows、train3623 / validation486 / test1001
- ordered0、unsupported0、episode/non-ubiquitous leakage0

formal GPU dataset:

- manifest file SHA 67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518
- semantic dataset SHA 351459083349917faf3b30384506849be0493de9996a4a4afa043c8f646626b5
- train shard 911989b241b15b1e34e98099b78dc9c5f063ee0f5578023dbc8240ccc499acff
- validation shard 013f834864438dd86b1291f594dec096f30ca0996c198eca198ebbd0f945a038
- test shard f7b8ad1b760be01b0dd7806e09ec2fb46410b28402c3bbc052e2d7ccc4bebe0a

### 6.4 AWR adapter

Tomato train recordへ厳密joinしたsidecar:

- weights SHA 63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229
- every/only train rows3623
- raw weight mass 3987.33142044282
- ESS 2804.4098482164172
- dataset/catalog/sidecar SHAをbind
- effective_weightは出力せずtrainer qualityの二重乗算を避ける
- authority false

これはsidecar整合性であり、AWR学習がnativeを超えた証拠ではない。

### 6.5 Synthetic Student v3 probe

- GPU RTX PRO 5000 Blackwell
- torch2.11.0+cu128、CUDA12.8、BF16
- 80 steps
- loss1.336160 -> 8.9407e-7
- exact-set fidelity1.0、GPU/CPU decode1.0

synthetic tiny-overfitであり競技性能ではない。

## 7. Dynamic META_TRAIN curriculum

iteration0 manifest:

- file SHA b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a
- semantic SHA df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4
- META_TRAIN20 opponents、quota96
- META_DEV0
- META_FINAL4はlineageのみ、weight/quota/exposure0
- teacher_behavior_eligible0
- source meta manifest SHA e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae
- schedule SHA 9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a
- broad config SHA 832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b
- pool SHA e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca

strict outcome adapter:

- manifest SHA 0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89
- semantic SHA 6ff323c8ec5cf377f8f2c9c75230416dcbafe9dfab01b801aada557ad6369454
- ledger SHA 18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa
- 80 META_TRAIN rows、16 META_FINAL reject、META_DEV0、fault0

このcurriculumはsampling materializationであり、学習・behavior・promotion authorityを与えない。

## 8. Task 1 public-state advantage contract

実装SHA:

- module dfdcf729debf3699e935412d8fc9f8ed149a90affd8dcbf8e8148a4165293e3d
- tests 2912528aa99b0ebc9d6b98cd99f2f891e16b588ae5d523c2df5603aeaad9df20
- evidence 31d1f6c2d8ff4c60d65ac9b6d5153cc920b5e4f9926325b50e66b3f2a42fe896
- re-review 32395681d9784649b6b4d6757673ce985abd8dbd81616f1a646548599beefcba

strict row schema:

state_digest / action_key / opponent_id / seat / split / outcome / weight

契約:

- META_TRAIN only
- private key、duplicate JSON key、nonfinite、unknown source、low support、heldout splitをreject
- usage boundaryはtraining_local系
- training_allowed=true、behavior_allowed=true、submission_allowed=false
- native policyを先に呼び、single-choice MAINでtable supportとmarginを満たす場合のみbounded override
- unknown/malformed/multi-select/ordered/non-legalはexact native fallback
- table SHAをconstructor/from_dictで再計算し、entries/coverageをimmutable化

focused + adapter + tuning 37 passed。Task1 independent reviewはspec PASS、quality PASS、Critical0、Important0、Minor2。Task1はsynthetic table契約であり、性能改善の証拠ではない。

## 9. Task 2 strict hard-negative iteration adapter

### 9.1 Producer状態

新規4ファイル:

- module SHA 33f096b174b055c96b6db1c57f34c08664c1ee01e5f31bc71a34352f76561114
- CLI SHA d295672400e130e18beea071f46206703b2585b7ab000d31ac0ad33ce65dd68d
- tests SHA a8a4837461a23c2d0d31fd85c114cab8b8ff9a9eff6b71ac45892ac84d94c737
- evidence SHA 89fda10b744b33b70e22fcdd616be4ba37de97b74b958b1d799258e645b18fa4

Task2 report SHA:
4d6ee4edc49cf624ebe031c4cf1353d0fbc8b9631f99af891ce34136179d718b

review package SHA:
38b727dbe8e2688ba4726d16e2763532f9723dfbf7156f3395e98d2cbd7bc246

機能:

- existing dynamic curriculum verifierとcommon24 outcome adapter verifierを再利用
- recordsはMETA_TRAINのみ、heldout opponent、heldout record、duplicate game IDをreject
- META_DEV / META_FINALはzero exposure、zero weight、zero quota
- hard-negative formula: loss0.40、seat imbalance0.20、under-exposure0.15、family diversity0.15、reliability0.10
- family floor/cap、opponent cap、fault/seat statistics
- curriculum、adapter、table、native policy/deck/evaluator、optional legal candidateのSHA binding
- deterministic iteration seed、weights semantic SHA、iteration semantic SHA
- atomic new-only write、fsync、strict reload、完全再導出
- authority all false、research_only true
- package/evaluator/performance gate不足によりready_for_evaluation=false
- CLI default DRY_RUN、executeはexit2 fail-closed

producer検証:

- focused 12 passed
- combined Task2 + dynamic curriculum + common24 adapter 20 passed
- py_compile PASS
- docs validator: Validated 13 canonical documents.
- git diff --check PASS
- performance/CABT/training/submission/longrun未実施

### 9.2 Independent re-review最新判定

Luna max独立re-review:

- review SHA a5714613191ca10ca09f9050f7d73eefc0dfbf1922790b88446550ea98f134ff
- specification conformance: FAIL
- code quality: CONDITIONAL PASS
- Critical0、Important2、Minor2

I-1 permission:

weighting経路がMETA_TRAIN entryのtraining_exposure_allowed、teacher_behavior_allowed、usage permissionを再検証しない。META_TRAIN split名だけではtraining permissionではないため、permission false / local_eval_only相当のrowがweight=1.0でsamplingへ流れる実反証がある。training-local、training_allowed、behavior_allowed、research_only、authority falseを再検証してからweightingしなければならない。

I-2 atomic writer:

存在確認からos.replaceまでの競合窓で、別プロセスがdestinationを作った場合に既存artifactをclobberし得る反証がある。atomic new-write契約をexclusive claimに変え、競合時に既存bytesが絶対に変わらないことをtestする必要がある。

Minor:

- Mapping入力ではduplicate JSON key検出を後からできない。strict file loader側のduplicate rejectionはある。
- authority field省略をsafe defaultで受ける経路がある。research-onlyなら省略をrejectする方がよい。

これは第一回独立review時点の履歴である。その後I-1/I-2を二回目に修正し、opaque proof token、permission digest、claim-loss cleanup保護を追加した。第二回独立reviewはImportant 0でPASSとなったため、現在のTask2判定は `COMPLETE / RESEARCH_ONLY`。Task2 manifestをTask3/Task4のdry-runへ接続できるが、性能screen・training・longrun・submissionのGOは別ゲートである。

## 10. Task 3〜6の現状

### Task 3 alternating bridge

目的はexisting alternating_meta_optimizer_v1を薄くwrapし、Task2 iteration manifest、CandidateStateV1、deck mutation candidate、native control、evaluation summaryをbindすること。

必須:

- POLICY_FIXED_SHORT / DECK_FIXED_LONG phase invariants
- stage exact (96,384,768,1536)
- candidate/native pair同一protocol
- fault0、seat gap、native regression gate
- native regression 2回連続STOPPED
- checkpoint SHA mismatch reject
- run root内rollback descriptor
- authority false、execute/training/promotion/submission/longrun authorityなし

Task3のfocused 9 passed + existing joint 34 passedまで進んでいたが、Task2 I-1/I-2の独立review判明後、Task2 fixへ切り替えた。Task3最終artifact、review、性能runは未完了。

### Task 4 dry-run materializer

Task3 stateが完成した後、Tomato native baseline、Task2 iteration、public advantage table、optional legal deck、rollbackを新run rootへmaterializeする。既存rootは上書きしない。CABT/training/submission/process起動なし。

### Task 5 bounded common24 performance screen

Task2/3/4のstrict gate後にのみ実施。

1. Tomato native controlとcandidateを同じcommon24、両seat、fault-inclusive denominatorで96局。
2. 事前+3pt級、fault0、seat gateを全て満たす場合だけseed-disjoint384。
3. 384でnativeを約+3pt以上上回る再現性があり、seat collapse/faultがなければ768/1536。
4. 小差、反転、fault、protocol mismatchならcandidate-only停止。

### Task 6 longrun gate

package closure、qualified deck、portable runtime、clean META_DEV、checkpoint/rollback、performance evidenceを機械的に検証する。既存longrun contractがあっても、このcandidateのGO evidenceがなければNO-GO。

## 11. Longrunの正しい設計

固定datasetを40 epochから200 epochへ延ばすだけでは目的のlongrunではない。必要な閉ループ:

native strong population
  -> META_TRAIN weighted opponent rollout
  -> public value / action-conditioned advantage
  -> bounded policy update
  -> hard-negative opponent reweight
  -> deck mutation race
  -> clean META_DEV evaluation
  -> BestKnown update or rollback
  -> next iteration

Tomatoを主control、Lucifer/Plamenをdiversity/controlとして残す。ただしteacher behavior usageはpermission manifest範囲だけ。Full6を使うにはordered/quarantineとnear-duplicate splitを閉じる必要がある。

### Longrun GO条件

1. candidateがTomato native controlを同一protocol 384、可能なら768/1536で再現的に上回る。
2. candidate/native双方fault0、両seat、seed-disjoint、protocol/runner/evaluator identity一致。
3. clean META_DEVをtrainingに混ぜず、hard-negative source lineageが閉じる。
4. Full6または明示quarantine済みsubsetのordered/near-duplicate gate。
5. checkpoint、training summary、runtime closure、dataset/catalog/sidecar、deck qualificationがSHA-bound。
6. bundle-allowed self deck、portable entrypoint、dependency/allowlist/privacy/CABT legality/latency/fallback。
7. rollback checkpointとresume lineage。
8. 実行authorityの明示的なユーザー承認。

現在はこれらの一部が未達なのでLONGRUN NO-GO。

## 12. 主要未解決blockerと考え方

### 性能blocker

Student v3/AWRはnativeから大幅に弱い。score-biasは96局で良く見えて384で悪化。mutationはcommon24で+0.651ptだがblock反転、Tomato未達。したがって現段階でAWRやfixed Studentを長く回すのは、目的に対して非効率。

### Full6 blocker

ordered4件をsilent dropしてはいけない。pointer-head exact対応か、unordered-only subsetとして明示的に目的とcoverageを変える必要がある。near-duplicate componentはsplitを跨がせない。Tomato単独clean laneはあるが、population overfitの多様性はまだ限定的。

### Permission blocker

local_eval_onlyは評価権限であってtraining/behavior/submission権限ではない。META_TRAINという名前だけでも許可にはならない。Task2 review I-1はこの原則を実装へ戻すための必須修正。

### Atomicity blocker

artifact generatorが同時実行される可能性がある以上、存在確認後のreplaceは不十分。既存artifact bytesを絶対にclobberしないことを、競合testで証明する必要がある。

### Statistical blocker

1536局でもnative top3差は0.3pt前後。23 opponent mutation結果を24 opponent rankingへ直接比較しない。96局はscreenに留める。

### Package blocker

Strong Asset native source/deckはas-is submission不可。self studentを作る場合もself deck・portable runtime・CABT・latency・fallback・privacy・dependency・lineageを閉じる必要がある。

## 13. Decision matrix

### Student v3 / policy candidate

- nativeより10pt級弱い: 同設計の384延長をしない。
- θ0がnative近辺、AWRだけ悪い: θ0を基盤にvalue/overrideを再設計。
- 96局で明確に正: strict384へ進む。
- 384で+3pt以上、fault0、seat gate: 768検討。
- 小差、反転、fault: candidate-only停止。

### Deck mutation

parent-relative positiveは必ずcommon24でTomato controlへ再配置する。Tomato nativeを同protocolで超えない限りBestKnown昇格しない。+1pt interactionのみではlongrunへ進まない。

### Full6

ordered pointer-headまたは明示quarantine、near-duplicate component split、bridge/GPU cross-bindingが必要。完了まではFull6学習NO-GO。

## 14. 一次artifact index

### Scoreboard / classification

- integrated scoreboard JSON: docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json
  SHA 39f76c6474bbf6dbe89d8adf620da92a8cd240487c35c8d4c40637b4afd7023a
- integrated scoreboard Markdown:
  SHA dc4047a90594d97b6b986c9b93c4a18a1cf756618694a6c647308c48a9e4fd95
- native pooled1536 evidence SHA e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863
- BestKnown classification v3 JSON: docs/evidence/autonomous-bestknown-classification-v3-20260813.json
- classification v2 JSON: docs/evidence/autonomous-bestknown-classification-v2-20260813.json

### Design and SDD

- design spec SHA f55557fdf8d28d2ccbb3745a5c2951a00177d87dbae99e7ebec18c40c87f2967
- implementation plan SHA bf6ac942cc89d16d7163f99df6777d6ccae44f6f4386b681cf8f284a1e72b95b
- Task1 re-review SHA 32395681d9784649b6b4d6757673ce985abd8dbd81616f1a646548599beefcba
- Task2 report SHA 4d6ee4edc49cf624ebe031c4cf1353d0fbc8b9631f99af891ce34136179d718b
- Task2 producer review package SHA 38b727dbe8e2688ba4726d16e2763532f9723dfbf7156f3395e98d2cbd7bc246
- Task2 independent re-review SHA a5714613191ca10ca09f9050f7d73eefc0dfbf1922790b88446550ea98f134ff
- Task3 brief: .superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-3-brief.md

### Native and mutation

- native top3 evidence SHA e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863
- mutation common24 evidence path docs/evidence/autonomous-deck-mutation-common-protocol-20260813.md
- mutation pooled manifest SHA 6ac5a4ed93a4214c4cd0e41a37665b64df96b4ba922143fb5e17d39be7325144
- mutation direct summary SHA f5a8f077f111881b606821fc312f2aa663fd57394f10724bb131b5e4f87429ba
- interaction summary SHA ba6486331ec8171fa9848cd22e792b55496726b2b7e4efd5d1ba7cf897b41e4a

### Data / catalog / curriculum

- catalog v2b file SHA 8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4
- catalog semantic SHA da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e
- Full6 blocked descriptor SHA a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7
- Tomato bridge SHA 8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983
- Tomato dataset manifest SHA 67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518
- Tomato AWR sidecar SHA 63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229
- curriculum iteration0 SHA b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a
- curriculum semantic SHA df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4
- strict outcome adapter SHA 0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89
- outcome adapter semantic SHA 6ff323c8ec5cf377f8f2c9c75230416dcbafe9dfab01b801aada557ad6369454
- outcome ledger SHA 18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa

## 15. 再現コマンド

### 15.1 Document and JSON checks

    cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
    python -m json.tool docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json >/dev/null
    python scripts/docs/validate_docs.py
    git diff --check

## 19. 追補: Task2 fix round 1 の最終判定（2026-08-13）

Task2の独立再レビューで検出されたI-1/I-2はLuna maxで修正され、別Luna max reviewerの再レビューもPASSになった。旧reviewのFAILは履歴として残すが、現在のTask2判定は `SPEC PASS / QUALITY PASS` とする。

### 19.1 修正内容

- I-1 permission再検証: weighting前にbound meta manifestをSHA再検証し、sourceの `research_only=true`、authority全false、META_TRAIN entryの `training_exposure_allowed=true` / `teacher_behavior_allowed=true`、`training_allowed=true` / `behavior_allowed=true` / `submission_allowed=false`、usage boundaryがtraining-local系であることを検証する。`_derive_weighting` 内でもverified permission mapを再確認し、`local_eval_only` や権限falseのrowを排除する。
- I-2 atomic no-clobber: `os.replace`によるcheck-then-replaceを廃止し、temp bytesをfsyncした後にexclusive `os.link` claimを行う。destinationが先に存在する競合では失敗し、既存bytesを変更しない。directory fsyncとtemp cleanupも行う。

### 19.2 最終証拠

- fixed module SHA: `7a1c297b33e5734e8a6254f878c23ef994ca057e2e3895643c4bf7e03b7f98d2`
- fixed tests SHA: `000bf7adb6132a73d6f16be31d20ca40206ce8f979638db63d9425a5614b56f0`
- fixed evidence SHA: `54b276162d7f3abfa47e2fe23fdab26f3990284f8a9f581daedc298d7f8c1ada`
- fixed task report SHA: `5cc0939792e3b93db45f4e977aead92133b973ad76676a38485f91a132437f80`
- fix report SHA: `dcc9115c5cf4f7ad41f9658f02ab1a809d6fedd133aaf9d554efe11248c920`
- independent fix review SHA: `ca38b4a9694c32da1791918f4b760662428fec66277dd936ed24ee026d8445ac`
- combined focused tests: `26 passed`（約15秒）
- py_compile: PASS
- docs validator: `Validated 13 canonical documents.`
- git diff --check / whitespace: PASS

この結果により、Task2 artifactをTask3以降の設計・dry-runへ接続すること自体は許可できる。ただし、これは性能改善、CABT、学習、submission、longrunのGOを意味しない。Task3のstrict bridgeとTask4 materializerを先に完了し、Task5のcommon24 screenで初めて性能判断を行う。

### 19.3 現在の作業状態

- Luna-only運用へ切替済み。Sol reviewerは停止済み。
- Task3 alternating bridgeは、Task2 fix済み契約を入力に再検証中。途中作成済みのTask3 module/testsは保全されており、Task3 focused +既存joint suiteは直前時点で35 passedだが、最終evidence/review/artifactは未完了。
- Task4 materializer、Task5 common24 performance screen、Task6 longrun gateは未完了。
- CABT、実学習、longrun、Kaggle submission、commit、pushは引き続き未実施。
- Full6 repairは `ready=false` のまま、Tomato単独clean laneだけがformal bridge/GPU dataset/AWR sidecarまで準備済み。

したがって、現状は「Task2の入力契約は閉じた。性能実験へ進むためのTask3/4統合を進めている。Student v3がnativeを越えた証拠はまだない」という状態である。

## 20. 最新実行指示の反映と再監査（2026-08-13）

最新の Luna Max Autonomous Final Sprint 指示を読み込み、開始時点のFSを再確認した。実行中のCABT・training・longrunはなく、既存performance artifactは上書きしていない。既知の弱い経路（Student v3 θ0/AWR、同型hard BC、同型score-bias sweep）は追加評価しない。native Tomato/Lucifer/Plamenのcommon24結果をcontrolとして保持し、次の主線をreal META_TRAIN advantage、native-preserving override、deck-policy alternating、Full6 unordered population、submission closureに限定する。

### 20.1 Task2二回目修正

独立レビューで追加検出された二つの反例を修正した。

- I-1: `_derive_weighting`へ構造的に正しい偽permission mapを直接渡せる問題に対し、module-private opaque `_VerifiedCurriculum` proof tokenとpermission digestを導入。plain dict、proof tokenなしの偽map、digest mutationを拒否する。
- I-2: builder上位の広いexceptがclaim-loss時に先勝ちartifactを削除し得る問題に対し、`FileExistsError`を分離してcleanupしない。公開後の検証失敗時も、bytesが自分のrawと完全一致するときだけ削除する。

現時点のTask2 producer SHA:

- module: `a318868a11fa71b4f61a60aa6e68c3b3633226d0a970a2917c022a7b23e0b4ed`
- tests: `74db853a035fe51ba167c4c94cd5ece7d941b3c7761c427711f14f3d727a7078`
- script: `d295672400e130e18beea071f46206703b2585b7ab000d31ac0ad33ce65dd68d`
- evidence: `e4afda805e0515abf08db391b9a9f6937c0eed2da1fbc793a8b5d2d8af62cd19`
- task report: `6bb034e2ebaa36eb06ed4e63ac7be733058dd211d3acf94dc608b9e7d02564a8`
- fix review package: `be4bf6446a7b6a296694575e757bfb439e47e789f43077a7ba1d9ab912674a40`

検証は focused 20 passed、Task2+dynamic curriculum+common24 combined 28 passed、py_compile PASS、docs validator 13 canonical PASS、git diff --check PASS。ただし、Task2は独立Luna re-reviewの最終文書が出るまで「最終GREEN」とは確定しない。

### 20.2 Task3独立レビューで判明した反例

Task3の途中module/testsはTask2待ちで保全されている。独立レビューは spec FAIL / quality CONDITIONAL PASS（Critical0、Important3、Minor3）だった。

- I-1 regression counter: `advance_*` / `promote_*` が各回の previous regressions=0から再計算し、stateに累積countを保持しない。悪化2回でも停止しない。
- I-2 source root-of-trust: Task3 bridgeがTask2 strict verifier/source rehashを呼ばず、`sources=[]` fixtureを受理できる。table file差替え＋semantic SHA再計算も受理される。
- I-3 summary integrity: aggregate scoreをseat-level/per-game dataから再導出せず、policy/deck/evaluator SHA、common24 strata、game-id、seed universeをbindしない。seat結果とaggregateを矛盾させてもstage gateが通る。
- Minor: deck mutation candidate_id binding、rollback count/reason、baseline path/status containment。

したがってTask3はまだ性能candidateへ接続しない。修正後に、累積regression STOP、Task2 strict reverify、aggregate/seat/strata/seed identityの再導出をfocused testで閉じる。

### 20.3 Full6-unordered-v1の方針

Full6は36,684 decisions中36,680 unordered、ordered unsupported 4件、near-duplicate cross 1 componentである。最新指示に従い、pointer-head完全対応を直ちにcritical pathへ入れず、別purpose `FULL6_UNORDERED_POPULATION_V1` の明示quarantine artifactとして解放する方向で進める。ただしordered 4件をsilent dropせず `QUARANTINED_ORDERED_UNSUPPORTED` としてledgerへ残し、coverage `36680/36684`、near-duplicate component split closure、teacher/opponent/behavior/label permissionを別々に記録する。ready=false descriptorしかない現状態から、学習入力へ昇格したとはまだみなさない。

### 20.4 次の実行順序

1. Task2 independent re-reviewをPASSで固定。
2. Task3 I-1/I-2/I-3をTDD修正し、Task4 materializerを新run rootへ作る。
3. real META_TRAIN advantage sourceでnative-preserving candidateを作る。synthetic Task1 tableだけで性能を主張しない。
4. Full6 unordered populationをquarantine/permission付きで別rootへ構築する。
5. native Tomato controlとcandidateを同一common24で96局。candidateの実override coverageを保存する。
6. 96局で明確な改善があれば384局へ進める。384で約+3pt、fault0、seat collapseなし、clean META_DEV、rollback/package経路があればLONGRUN_READY_CANDIDATEを検討する。

LONGRUN_READYは性能とpackageの証拠が揃うまで出さない。現時点の結論は `LONGRUN_NOT_READY`、`SubmissionEligibleBestKnown`はStrong Asset poolなし、Rule v0 fallback anchorのみである。

### 20.6 Full6-unordered-v1 descriptor

速度優先の別purpose subsetを既存Full6 rootへ上書きせず生成した。

- path: `runs/final-sprint-autonomous/full6-unordered-population-v1/manifest.json`
- file SHA: `1ffae9d91451ba89350588f226cc74183a80ecab6ff4c6acd44301873bb605a2`
- semantic SHA: `bd0c5ec276b641f9ef74caadfdc40972bfe52d279cbe8045bdd84162dc6b7434`
- purpose/identity: `FULL6_UNORDERED_POPULATION_V1`
- source coverage: 36,684 decisions; 36,680 unordered; `coverage_closed=true`
- ordered quarantine: 4 rows, schema `5:34`, status `QUARANTINED_ORDERED_UNSUPPORTED`, `silent_drop=false`
- non-ubiquitous near-duplicate cross: one component, ID prefix `5a996ab25264020f...`; component assignment/closure未materialized
- published rows: 0; raw reproduction: false; behavior/training readiness: false; authority all false
- permission matrix: six teachers are `training_local_allowed=true` for opponent scheduling, but `behavior_policy_allowed=false`, `teacher_behavior_labels_allowed=false`, `derivative_action_labels_allowed=false`, teacher code/deck submission false, usage boundary local-eval-only
- evidence: `docs/evidence/autonomous-full6-unordered-population-v1-20260813.md`, SHA `84333a5124cf740f721b4c52994c3daa185d3012bcac35ee5a899042666a4b04`

このartifactは「orderedを黙って捨てず、unordered coverageを明示した」進捗であり、学習可能なFull6 datasetではない。near-duplicate component splitと一次raw reproductionが閉じるまで、Tomato clean laneだけを性能候補の入力にする。

### 20.5 Task2 second-fix independent reviewの確定

Task2 second-fix独立re-reviewが完了した。

- review doc: `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-2-second-fix-re-review.md`
- review SHA: `576e56c7bf45f332c87e42d9a924e351d103f3328897973e20665fbde54dee6b`
- verdict: I-1/I-2 acceptance PASS、code quality CONDITIONAL PASS、Critical 0 / Important 0 / Minor 2
- focused Task2: 20 passed
- combined Task2 + dynamic curriculum + common24: 28 passed
- py_compile: PASS
- docs validator: `Validated 13 canonical documents.`
- git diff --check: PASS

Minorは、同一プロセスでprivate tokenを意図的に覗けば人工proofを作れる点と、verify失敗後のbyte-equality cleanupがinode ownershipまで証明しない点。通常のbuilder競合で先勝ちartifactを削除する反例は再現せず、Task2をTask3/4の入力契約へ接続する許可条件は満たした。ただし性能GO、training authority、submission authorityを与えたわけではない。

### 15.2 Focused tests

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_public_advantage_v1.py tests/meta_specialist/test_native_preserving_adapter_v1.py tests/meta_specialist/test_native_meta_overfit_iteration_v1.py tests/meta_specialist/test_dynamic_meta_train_curriculum_v1.py tests/meta_specialist/test_common24_curriculum_outcome_adapter_v1.py

Task2 second-fix後のfocused/combined再検証はそれぞれ20 passed / 28 passed。Task3は別の独立review blockerがあるため、Task3修正後に再実行する。

### 15.3 Task2 dry-run shape

    PYTHONPATH=.:src .venv/bin/python scripts/build_native_meta_overfit_iteration_v1.py --repo-root . --curriculum-manifest runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json --outcome-adapter-manifest runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/adapter-manifest.json --public-advantage-table VERIFIED_TASK1_TABLE_JSON --native-baseline-identity VERIFIED_NATIVE_IDENTITY_JSON --output-manifest NEW_RUN_ROOT/iteration.json

Task2 CLI --executeはexit2。output pathは既存rootをoverwriteしない。I-1/I-2 fixと独立review PASS後、Task3/4のresearch-only dry-run入力として接続可能になった。実performance/trainingへはTask3/4/permission/package gate後のみ接続する。

### 15.4 禁止中の実行

以下はユーザーの明示承認、全gate、別run rootなしに実行しない。

    scripts/run_autonomous_meta_finetune_longrun_v1.py --execute
    Kaggle API / CLI submit
    既存performance run root overwrite
    local_eval_only native source/deckをbundleへコピー
    Full6 blocked descriptorを学習入力へ接続

## 16. ChatGPTが判断すべき質問

このpackを読んで助言する場合、次を分けて判断する。

1. Student v3をそのままlongrunへ進めるべきか。現証拠ではNO-GO。nativeから大幅に弱い。
2. native-preserving bounded overrideを試す価値があるか。Task1/2契約をpermission修正後にTask3/4へ接続し、96局screenを一回行う価値はある。ただし+3pt/384 gateが必要。
3. Plamen mutationをBestKnownと呼べるか。23-opponent parent-relativeはcommon24 global比較ではない。common24 pooledもTomato未達。
4. Full6で学習すべきか。ordered4件とnear-duplicate crossを閉じるまでNO-GO。Tomato単独clean laneは技術的に準備済み。
5. submissionを出せるか。Strong Asset native pairは不可。現状はRule v0 root-deck archive anchorのみ。
6. longrunを始めるべきか。native超過、clean META_DEV、package/runtime/deck/rollbackを閉じるまでNO-GO。
7. Task2 independent reviewを無視してTask3/5へ進めるべきか。不可。permissionとartifact atomicityの問題は、学習入力とrollback lineageを汚染するため先に直す。

## 17. 最新の意思決定

この完全pack作成時点の最新状態:

- EvaluationBestKnown = Tomato native provisional
- BestKnownArchaludon = Tomato native provisional
- TrainingEligibleBestKnown = Tomato primary / Lucifer control bounded
- SubmissionEligibleBestKnown = Strong Asset poolなし。Rule v0 root-deck anchorのみ
- GlobalBestKnown = unresolved
- Task1 = review PASS
- Task2 = I-1/I-2修正済み、第二回Luna独立review PASS（Important 0、Minor 2）。research-only入力としてTask3へ接続可
- Task3 = 独立reviewでI-1 regression counter、I-2 source rehash、I-3 summary strata/identityのImportant 3が残り、修正中。最終artifact未完了
- Task4 = 未完了
- Task5 = 未実施
- Task6 = NO-GO
- CABT = 未起動
- training = 未起動
- longrun = 未起動
- Kaggle submission = 未実施
- commit / push = 未実施
- existing performance artifacts = 保全。既存rootの上書きなし

このファイル単独でChatGPTへ渡せば、ローカルファイルを参照できなくても、目的、性能の実態、実装の成熟度、失敗反例、許可境界、次の実行条件を判断できる。

## 18. 完全pack自体の検証

このpackは情報集約用であり、性能artifactの代替ではない。作成後に以下を実行する。

    sha256sum docs/status/chatgpt_full_context_pack_native_meta_overfit_2026-08-13.md
    python scripts/docs/validate_docs.py
    git diff --check

---

## 2026-08-14 — cg policy screen v2 と package-bound alternating runtime

最新の policy-first 方針に従い、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` と cg P0 controlを固定し、未評価の public MAIN surfaceを2件だけ追加した。`cg-attach-threshold-v1`（Mega Lucarioがenergy 1のときFighting attachへ+12000）は17/96対control19/96、−2.0833pt。`cg-overkill-conservation-v1`（visible activeへの大overkill attackへbounded penalty）は12/96対control21/96、−9.3750pt。各192局は全DONE/fault0/draw0、両seat support、workers12/recycle16。両候補とも common24/384/768へは進めず、既評価 lethal/retreat/deck surfaceのblind retryも行わない。

両候補packageは7-file cg closure、60枚deck、sample cg runtime parity、clean-room 4局をPASSしたが、remote Submit verifier/archive contractはrepo内にない。従って `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED`、`submission_ready=false`、authority全falseを維持する。archive SHAはattach `21ad35e1f2715d338d75a24e9113f1c4b4fc3b367116a2876a36d3066559cc01`、overkill `c89a2f8a5a6dad285fad39fed79e3b429d9a57bf537de13379c3bbd897e07b2d`。evidenceは `docs/evidence/autonomous-cg-policy-screen-v2-and-alternating-runtime-20260814.md`。

native candidate factory専用だった alternating runtimeをcg packageへ接続する research-only adapterを追加した。module SHA `03d0fe298745755478b2f837b52cdebf07988d4f8c43232fda295ec26815276b`、CLI SHA `6d9b0ece162a0f5fa3eb8503842699d048985ee7482828a0f29bc07bbdb1213c`。`POLICY_FIXED_SHORT`はpolicy SHA固定・deck SHAのみ変更、`DECK_FIXED_LONG`はdeck SHA固定・policy SHAのみ変更。stageは96→384→768→1536、同一opponent×seat×rep×seed strata、positive/fault0/両seat support/seat gap≤5ptのときだけ次段を記録する。workers12、96はrecycle16、384以上はrecycle64、training/promotion/submission/unbounded-longrun authorityは全false。実Hilda deck＋attach policy packageで dry-run strict reloadを通過した（iteration SHA `75b9a932a4e709505702154f5b357e5f217da3225329f1dd8ad1cb08ef41d3ca`、stage manifest SHA `35fbe6df150bc2c7d7ce0320cb9e3d5a386abe8be2328425cf8ca779cae5d23a`）。performance executionは候補固定後にのみ行う。

Current classification: ResearchSubmissionCandidateBestKnownはcg lethal＋root deck（768でcandidate 161/768対control106/768、+7.1615pt、candidate-only）。VerifiedSubmissionEligibleBestKnown/ChampionはRule v0＋root deck 11/96、fault0のまま。active processなし、production/Champion/既存artifact不変、commit/push/Kaggle submissionなし。

# 最新 authoritative addendum（2026-08-13 Luna Max Final Sprint 指示反映）

この追補は、上記の履歴に対する現在の正典である。上の章に古い途中判定が残っている場合は、この追補の「現在の判定」「作業中」「次の実行順」を優先する。ChatGPTへ渡す際は、このファイル全体を渡し、回答では履歴ではなくこの追補の最新判定を起点にすること。

## A. 最新指示をどう解釈したか

最新指示の中心は、契約・資料・テストを増やすことではなく、strong native populationを保ったまま、real META_TRAIN → action-conditioned advantage → native-preserving candidate → common24 96 → 384 → longrunという性能ループへ最短で到達することである。

ただし、次の安全境界は維持する。

- Luna Maxのみを使う。Sol Maxは停止済みで、現在のagent一覧にも実行中Solはない。
- 既に完了した実験（Student v3 θ0/AWR、同型hard BC、同型score-bias sweep）を再実行しない。
- 既存performance artifactを上書きしない。新しいrun rootを使う。
- `local_eval_only` native deck/code/behaviorを提出packageへコピーしない。
- authority falseのresearch-only artifactを学習・promotion・submissionへ黙って昇格しない。
- full6のordered 4件、near-duplicate split、teacher permissionをsilent dropしない。
- CABT、GPU学習、longrun、Kaggle submissionは、開始条件が満たされた場合のみ実行する。Kaggle submission、commit、push、Champion/default変更はこの作業では行わない。

### 現在のプロセス状態

開始時のread-only確認では、関連するCABT、student-v3、meta-overfit、curriculum、deck-mutationの実行プロセスは存在しなかった。稼働していた有用runをkillしたわけではない。Sol reviewerはpending状態から停止し、Luna MaxのTask3 producerとFull6/document auditだけを継続している。

## B. 最新の一言結論

**技術的な基盤は、real META_TRAINの入口、native immutable control、dynamic curriculum、Task2 permission/atomic契約、Tomato clean data path、Full6の明示quarantine、deck mutation、rollback設計までかなり進んでいる。しかし、native 72%級を実際に上回る新policyはまだ存在せず、Task3の独立reviewを閉じるまで第一候補の性能screenへは進めない。長時間学習は「見通しはあるが、現時点ではまだ開始不可」であり、行き詰まりではないが、性能の決定的な実証がまだない状態である。**

具体的には、現在の主な不足はarchitectureの全面作り直しではない。

1. Task3 alternating bridgeの独立review blocker（累積regression、source/table再hash、per-game summary再導出）をGREENにする。
2. Task4 materializerで、native baseline、Task2 curriculum、real advantage、override、deck candidate、rollback、evaluator設定を一つの新規run rootへ束ねる。
3. synthetic Task1 tableではなく、real META_TRAIN由来のpublic/action-conditioned advantageを作る。
4. Tomato native controlと同一common24で、candidateの実overrideを含む96局を一度だけscreenする。
5. positiveなら384へ進み、+3pt級・fault0・seat collapseなし・clean META_DEV・rollback/package pathがあれば `LONGRUN_READY_CANDIDATE` を検討する。

## C. BestKnownと性能の最新機械分類

### C.1 native control

common24、両seat、fault-inclusive denominator、4つのseed-disjoint 384 blockをpooledしたnative基準は次の通り。

| pair | W-D-L-F / 1536 | score | seat0 | seat1 | 現分類 |
|---|---:|---:|---:|---:|---|
| `tomatomato_archaludon` | 1107-0-429-0 | 72.0703% | 561/768 | 546/768 | EvaluationBestKnown provisional / native control |
| `lucifer19_battlecore` | 1103-0-433-0 | 71.8099% | 554/768 | 549/768 | diversity/control |
| `plamen06_steel` | 1102-0-434-0 | 71.7448% | 567/768 | 535/768 | diversity/control |

TomatoとLuciferの差は0.2604pt、TomatoとPlamenの差は0.3255ptであり、1536局でもnear-tieである。したがってTomatoは「現在のcommon24で使う暫定anchor」であり、絶対的なGlobalBestKnownではない。

一次evidenceは `docs/evidence/strong-asset-top3-pooled1536-20260812.md`、SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`。block ranking artifactのSHAは、block1 `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`、block2 `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`、block3 `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`、block4 `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5`。

### C.2 追加candidateの採用可否

| 経路 | 実測 | 最新判定 | 重要な解釈 |
|---|---:|---|---|
| Student v3 θ0 | 7/96 = 7.2917% | NO-GO、再run禁止 | native 66/96 = 68.75%から−61.458pt |
| Student v3 AWR | 3/96 = 3.1250% | NO-GO、再run禁止 | nativeから−65.625pt |
| guarded score-bias | 96局は+9.375pt、384局は−2.9948pt | NO-GO | 96局の正差を信じないという重要な反証 |
| Plamen mutation、23 opponent | 1101/1472 = 74.8302% vs parent 1072/1472 = 72.8261% | candidate-only | 24 opponent native rankingとprotocolが違う |
| Plamen mutation、common24 pooled1536 | 1099/1536 = 71.5495% vs parent1089/1536 =70.8984% | candidate-only | parent-relative +0.651ptだがTomato72.0703%未達、block反転あり |
| Plamen mutation vs Tomato direct384 | 274/384 vs Tomato275/384、−0.3906pt相当 | 停止 | Tomato control超えなし |
| Tomato policy + mutation deck384 | 264/384 vs control260/384、+1.0417pt | candidate-only | 事前+3pt gate未達、second block停止 |

この表から、現在までに「native BestKnownを越えた提出可能pair」は存在しない。mutation frameworkを捨てる必要はないが、現candidateをBestKnown、training teacher、longrun seed、submission pairと呼んではならない。

## D. Permissionとpackageの現状

`opponents/pool_manifest.json` の全102 assetは `usage_boundary=local_eval_only`。評価poolとしての利用と、training opponent、behavior、teacher label、derivative action、submissionは別permissionである。META_TRAINというsplit名だけでは権限は発生しない。

現在の必要なpermission軸は少なくとも次である。

`evaluation_allowed`、`training_opponent_allowed`、`training_allowed`、`behavior_allowed`、`teacher_label_allowed`、`derivative_allowed`、`submission_allowed`、`research_only`、および authority flags。

Tomato/Luciferのsealed training-local pathは、許可されたrecord/derived weightをlocal trainingへ使うための境界であって、native code、native behavior logits、external deck、as-is submission、teacher label、behavior policyを自動的に許可しない。Plamen等の外部native pairはcommon arena controlやdeck evaluationへ使えても、teacher behaviorへ直接流してはいけない。

現行のSubmissionEligibleBestKnownはStrong Asset poolにはない。唯一の実用anchorは、Rule v0 + root deckの既存fallback archiveである。

- Rule v0 policy SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- archive SHA: `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`
- clean-room smoke: 2 DONE / 0 fault / 0 illegal

これはpackage fallbackの検証であり、native超過の性能証拠ではない。V3等の候補をsubmissionへ進めるには、self-owned/bundle-allowed deck、portable runtime、依存closure、CPU latency、CABT engine/plugin provenance、privacy、clean-room、fallback、candidate identityの全てが必要である。

## E. Data / Full6 / curriculum

### E.1 catalogとTomato clean lane

v2b catalog:

- file SHA `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4`
- semantic SHA `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e`
- examples: Tomato5110、Lucifer5245、Plamen5275、Grimmsnarl8104、Rocket5899、Nihei7051（unlabelled2）
- 各teacher96/96、fault0、seat48/48。catalog integrity PASSはsplit leakageや性能を保証しない。

Tomato clean bridge v2:

- bridge manifest file SHA `8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983`
- bridge semantic SHA `3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0`
- source SHA `47ae3578b70fab181931fe6bdfa08eae36b5676e1dadc4b5df50f02c893eba9b`
- 5110 rows、train3623 / validation486 / test1001、ordered0、unsupported0、episode/non-ubiquitous leakage0

Tomato GPU dataset v2:

- manifest SHA `67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518`
- semantic dataset SHA `351459083349917faf3b30384506849be0493de9996a4a4afa043c8f646626b5`
- train/validation/test records `3623/486/1001`
- train shard `911989b241b15b1e34e98099b78dc9c5f063ee0f5578023dbc8240ccc499acff`
- validation shard `013f834864438dd86b1291f594dec096f30ca0996c198eca198ebbd0f945a038`
- test shard `f7b8ad1b760be01b0dd7806e09ec2fb46410b28402c3bbc052e2d7ccc4bebe0a`

Tomato AWR sidecar adapter:

- weights file SHA `63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229`
- exactly every/only Tomato train3623 records
- raw weight mass `3987.33142044282`
- ESS `2804.4098482164172`
- dataset/catalog/sidecar binding PASS、authority false

ただし、このAWR sidecarの整合性は「AWR学習がnativeを超えた」ことを意味しない。旧AWR/Student v3性能が3/96だったため、同じepisode-level AWRを延長しない。

### E.2 Full6-unordered-v1の最新状態

速度優先の別purpose descriptorが、既存Full6 blocked rootを上書きせず作成された。

- path: `runs/final-sprint-autonomous/full6-unordered-population-v1/manifest.json`
- purpose/identity: `FULL6_UNORDERED_POPULATION_V1`
- file SHA: `1ffae9d91451ba89350588f226cc74183a80ecab6ff4c6acd44301873bb605a2`
- semantic SHA: `bd0c5ec276b641f9ef74caadfdc40972bfe52d279cbe8045bdd84162dc6b7434`
- source coverage: 36684 decisions、unordered対応36680、`coverage_closed=true`
- ordered: 4件、schema `5:34`、status `QUARANTINED_ORDERED_UNSUPPORTED`、silent_drop=false
- ordered identities/target sequences: 未materialize。pointer-head対応を偽装していない。
- non-ubiquitous near-duplicate cross: ID `5a996ab25264020f3a776c00489771e41b1bfbd2a0cff63eb0c907a8953e80ed` の1 component
- component assignment/closure: 未materialize、未検証
- published rows: 0、raw reproduction=false、ready_for_training=false、ready_for_behavior=false
- authority: 全false
- permission: six teacher全件で training-local/derived-weightのみ許可。behavior policy、teacher labels、derivative action labels、teacher code/deck submissionは不可。boundaryはlocal_eval_only。
- evidence: `docs/evidence/autonomous-full6-unordered-population-v1-20260813.md`
- evidence SHA: `01f633c6ed0d1bbeabbc2008fa19df6bf83aa82e23c5e6bf423353a663e31bb6`

このartifactの意味は「36,680件を対応済みと自己申告して学習へ流した」ことではない。ordered 4件とcross-split componentを明示的に残し、silent dropを防ぐための安全なsubset descriptorである。component closure、一次raw reproduction、permission gate、実dataset materializationが閉じるまで学習入力には使わない。

### E.3 META_TRAIN curriculum iteration 0

iteration0 dynamic curriculum:

- manifest file SHA `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a`
- semantic SHA `df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4`
- META_TRAIN20 opponents、quota96
- META_DEV0
- META_FINAL4はlineageのみ、weight/quota/exposure0
- behavior eligible0、authority false
- source meta manifest SHA `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- schedule SHA `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`
- broad config SHA `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- pool SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

strict outcome adapter:

- manifest SHA `a4c27800` で始まる現行artifact（旧pack記載の `0679bc79...` は初期再seal履歴）
- semantic SHA `f2a5547d` で始まる現行artifact
- ledger SHA `18f1bec6...`
- 80 META_TRAIN rows / 20 opponents、META_FINAL16 rejected、META_DEV0、fault0、unique game_id80

このcurriculumは実行スケジュールをmaterializeするためのものだ。fixed split名だけでtraining/behavior権限を発生させない。iteration outcome schemaは、次iterationへ流す前に、game_id、seed、protocol、subject identity、duplicate、fault、seatを持つstrict common24 reconciler経由に限定する必要がある。

## F. Task1 / Task2 / Task3 / Task4の状態

### F.1 Task1 public-state advantage

Task1はpermission-aware、native-first、bounded override、table self-SHAを閉じた契約である。Task1のsynthetic tableは性能結果ではない。

- latest module SHA `dfdcf729debf3699e935412d8fc9f8ed149a90affd8dcbf8e8148a4165293e3d`
- tests SHA `2912528aa99b0ebc9d6b98cd99f2f891e16b588ae5d523c2df5603aeaad9df20`
- evidence SHA `31d1f6c2d8ff4c60d65ac9b6d5153cc920b5e4f9926325b50e66b3f2a42fe896`
- independent review SHA `32395681d9784649b6b4d6757673ce985abd8dbd81616f1a646548599beefcba`
- review: spec PASS / quality PASS / Critical0 / Important0 / Minor2

overrideはnative policyを先に実行し、MAIN single-select、legal、support、margin、permissionを満たす場合だけ変更する。それ以外はnative exact fallback。unknown schema、ordered、multi-select、nonfinite、nonlegalはcandidateへ流さずfallbackまたはfail-closedとする。

### F.2 Task2 hard-negative iteration

Task2の二段階独立reviewで、I-1 permissionとI-2 atomic no-clobberが最終的に受入条件PASSになった。

- module SHA `a318868a11fa71b4f61a60aa6e68c3b3633226d0a970a2917c022a7b23e0b4ed`
- tests SHA `74db853a035fe51ba167c4c94cd5ece7d941b3c7761c427711f14f3d727a7078`
- script SHA `d295672400e130e18beea071f46206703b2585b7ab000d31ac0ad33ce65dd68d`
- evidence SHA `e4afda805e0515abf08db391b9a9f6937c0eed2da1fbc793a8b5d2d8af62cd19`
- review SHA `576e56c7bf45f332c87e42d9a924e351d103f3328897973e20665fbde54dee6b`
- focused Task220 passed、combined Task2+dynamic+common24 28 passed、docs validator PASS、diff-check PASS
- verdict: I-1/I-2 acceptance PASS、Critical0 / Important0 / Minor2、quality CONDITIONAL PASS

I-1は、source manifestをSHA再検証し、`research_only`、authority、META_TRAIN entry/source permission、training/behavior/submission flagsを確認し、module-private opaque proof tokenとpermission digestでplain/forged mapを拒否する。I-2は、temp bytesをfsyncしてexclusive `os.link` claimし、FileExistsErrorで先勝ちartifactを保持する。private tokenの意図的な同一プロセス覗き見とbyte-equality cleanupのinode ownership未証明がMinorとして残るが、通常のbuilder競合のclobber反例は閉じた。

Task2をTask3/4のresearch-only inputへ接続してよい。ただしTask2 PASSは性能、学習、longrun、submissionを意味しない。

### F.3 Task3 alternating bridge（現在作業中）

Task3 producer module/testsは次の設計を持つ。

- module: `src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py`
- current source SHA at pack update: `4729b05cb5dc50eff43464e8ea11317f25787f56a62b4e5b30fcb1d8d1b05a74`
- tests: `tests/meta_specialist/test_native_meta_overfit_alternating_v1.py`
- current test SHA: `69d2ad813de82486768b4508c7a59b48f48599ca1c396b52d69ef01f60d04d9d`
- evidence path: `docs/evidence/autonomous-native-meta-overfit-iteration-v1-20260813.md`
- evidence current SHA: `e4afda805e0515abf08db391b9a9f6937c0eed2da1fbc793a8b5d2d8af62cd19`
- focused Task3: 13 passed
- combined Task3 + alternating optimizer + deck mutation + joint suite: 39 passed

Task3で閉じた内容:

- phaseは `POLICY_FIXED_SHORT` / `DECK_FIXED_LONG` の固定側SHAを保つ。
- native baselineはimmutable controlとしてcandidateと同じprotocolで評価する。
- stage sequenceは96→384→768→1536に限定する。
- `NativeRegressionJournalV1` がstate SHA、summary SHA、decision SHA、consecutive regression countを保持する。
- nativeより悪化した連続2回で `stop_after_two` / rollback required とする。
- Task2 iteration manifest、schedule、public advantage table、source file SHAをstate bindingへ入れる。
- `EvaluationSummaryV1` はcandidate/nativeの全game recordを要求し、seat、outcome、fault、opponent、family、seed、game_id、common24 strataからaggregate scoreとseat scoreを再導出する。
- candidate/native policy/deck/evaluator SHA、game-id universe SHA、seed universe SHA、strata SHAをbindする。
- rollback descriptorはcandidate state、iteration manifest、checkpoint SHA、reason、regression count、authority falseを保持する。

ただし、Task3 producerの独立reviewはまだ必要である。以前のreviewで見つかった反例は、次の3点だった。

1. 各呼出しでregression countを0へ戻し、2回連続悪化で停止しない。
2. Task2 strict verifierやsource/table rehashを呼ばず、sources=[]やtampered tableを受理する。
3. aggregateをseat/per-gameから再導出せず、policy/deck/evaluator/common24 strata/game-id/seedをbindしない。

上記3点に対してproducer側のTDD修正は現在13 focused testsでGREENだが、独立Luna reviewerの最終判定、evidence/report/review packageのSHA固定が未完了である。したがってTask3は「実装テストGREEN・独立review待ち」であり、最終GREENではない。

### F.4 Task4 materializer

Task4は未完了。Task3の独立reviewを待たずに性能runへ進めるための抜け道ではなく、Task3 PASS後に新しいrun rootで次を一つのcandidate lineageへmaterializeする。

- native baseline identity / immutable control
- Task2 verified curriculum
- real META_TRAIN advantage table
- native-first override config
- optional legal/self deck candidate
- evaluator/common24 protocol
- checkpoint/resume/rollback
- candidate identity = deck SHA + policy/config SHA + curriculum SHA + table/bridge/catalog/evaluator/package closure

Task4はdry-runを先に作り、`ready_for_evaluation=false`、authority falseから開始する。既存run rootをoverwriteしない。

## G. 実性能へ戻るための次の順序

### G.1 直近の必須順序

1. Task3のfocused 13 / combined39をproducer evidenceへ固定する。
2. Luna独立reviewで、regression journal、Task2 source/table rehash、per-game aggregate、seat/strata/seed/game-id bindingを再確認する。
3. Task4 materializerを新規run rootへ作る。synthetic tableだけを性能入力にしない。
4. real META_TRAIN rolloutから、public state、own visible state、legal semantic action、chosen action、outcome、opponent family、seatを保存する。opponent ID/seatをruntime policy入力へ入れない。
5. V(s)、native action outcome、legal alternativesのbounded rolloutからaction-conditioned advantageを作る。episode winを全actionへ一括強化しない。
6. native-first overrideをcandidateへ入れる。unsupported/low support/low margin/ordered/multi-select/unknownではnative exact fallback。
7. Tomato native controlと同一common24・両seat・同一evaluatorで96局 screen。candidateの実override coverageとfallback理由を保存する。
8. 明確にpositiveなら384へ。目安はnative比+3pt、fault0、seat collapseなし。+3pt未満でもaction coverage/value confidenceが強ければ主agent判断で384まで進める。
9. 384で明確な再現性、clean META_DEV、rollback、package経路が揃ったときだけ `LONGRUN_READY_CANDIDATE` を検討する。768/1536はlongrunと並行confirmation可能。

### G.2 Longrunの定義

固定datasetを40 epochから200 epochへ伸ばすだけのものはlongrunと呼ばない。正しいloopは次である。

```text
strong native population
        ↓
weighted META_TRAIN rollout
        ↓
public/action-conditioned advantage
        ↓
native-preserving policy update
        ↓
hard-negative reweight
        ↓
deck mutation race
        ↓
clean META_DEV
        ↓
BestKnown update or rollback
        ↓
next iteration
```

各iterationで、current candidateをMETA_TRAINへrolloutし、opponent/family別regretを集計し、hard-negative weightを更新し、action-value/overrideとdeck searchを更新し、META_DEVで確認し、改善時だけ次lineageへ進める。META_DEV/FINALはtraining exposure0のまま保持する。

checkpointは25%、50%、75%、100%。2回連続native regressionでrollbackし、そのlineageをSTOPする。checkpoint/resumeではstate、curriculum、table、deck、policy、evaluator、package SHAを再検証する。

## H. Full6とdeckの並列作業

### H.1 Full6

Full6 unordered descriptorは作成済みだが、component assignmentとraw reproductionが未完了で、published_rows=0 / ready=falseである。次の作業は、near-duplicate connected component全体を同一partitionへ割り当て、ordered 4件をquarantine ledgerに保持したまま、全teacher permissionを厳密に再検証し、正式GPU datasetへmaterializeすること。Full6を早く使えるようにすることは重要だが、ready=falseをtrainingへ接続することは禁止する。

### H.2 Deck search

deck mutationはGPU policyを待たず、CPU arenaで並行継続できる。1-card swap、2-card swap、trainer/energy count、top-meta tech、hard-counter packageを合法60枚、core signature、known card IDs、CABT legality callback、authority falseで生成する。評価はparent-relativeだけでなく必ずTomato native controlへ再配置する。

交互最適化の順序:

```text
policy fixed → deck search → best legal deck
deck fixed → policy update / bounded override
→ META_DEV → promote or rollback
```

current Plamen mutationとTomato-policy interactionはpositive signalだが、Tomato nativeを超えず、+3pt gateにも届かないため、BestKnown/longrunへ昇格しない。

## I. Longrunとsubmissionの判定

現在の判定は以下。

| gate | 現在 |
|---|---|
| EvaluationBestKnown | Tomato native provisional |
| BestKnownArchaludon | Tomato native provisional |
| TrainingEligibleBestKnown | Tomato primary / Lucifer control bounded |
| SubmissionEligibleBestKnown | Strong Asset poolなし。Rule v0 fallback anchorのみ |
| GlobalBestKnown | unresolved |
| Task1 | review PASS |
| Task2 | I-1/I-2 PASS、Important0、Minor2 |
| Task3 | focused/combined GREEN、独立review・最終evidence待ち |
| Task4 | 未完了 |
| real META_TRAIN advantage | native-preserving candidate用の実performance artifactは未生成 |
| candidate common24 96/384 | 新しいTask3/4候補は未実施 |
| Full6 | unordered descriptorのみ、ready=false |
| package closure | Strong Asset as-is不可、Rule v0 anchorのみ |
| LONGRUN | `LONGRUN_NOT_READY` |
| CABT / training / longrun / Kaggle | 未起動 / 未実施 |
| git commit / push | 未実施 |

### LONGRUN_READYの必要条件

- real META_TRAIN data
- dynamic hard-negative curriculum
- native-preserving candidate
- common24 384でnative比明確な改善（目安+3pt）
- fault0、両seat致命的collapseなし
- clean META_DEV identity、学習混入なし
- rollback/checkpoint/resume
- package pathが説明可能
- deck-policy alternating stateが実際に動く

これらが揃うまで `LONGRUN_NOT_READY` を維持する。揃った場合だけ、exact run root、config/curriculum/policy/deck SHA、starting BestKnown、budget、checkpoint cadence、rollback、stop rule、start commandを別報告する。

## J. ChatGPTへ求める判断

このファイルだけを読んだChatGPTは、次を明確に分けて助言すること。

1. Student v3 θ0/AWRを長く回すか。答えはNO。nativeから大幅に弱く、同型延長は禁止。
2. native-preserving bounded overrideを一度試すか。Task3/4が独立review PASSし、real advantageができた後の96 screenには価値がある。ただし96の点差だけで昇格しない。
3. Plamen mutationをBestKnownと呼べるか。答えはNO。common24 pooledでもTomato未達、block反転あり。
4. Full6を今すぐ学習へ入れるか。答えはNO。descriptorは進捗だがready=false、near-duplicate component未閉鎖。
5. longrunへ進めるか。答えはNO-GO。まずTask3独立review、Task4、real advantage、96→384、package/rollback。
6. 何が行き詰まりか。architecture全体ではなく、実性能候補のnative超過がまだ未証明であることが最大の不確実性。Task2/データ境界はかなり前進しており、次は契約追加ではなく性能screenへ移るべき。
7. 何を並列化できるか。Full6 component closure、CPU deck race、submission/package closure、Task3 reviewは並列化できる。GPU performance runは主agentが調停し、重複実行を避ける。

## K. 再現・検証コマンド

リポジトリroot:

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_meta_overfit_alternating_v1.py \
  tests/meta_specialist/test_alternating_meta_optimizer_v1.py \
  tests/meta_specialist/test_deck_mutation_v1.py \
  tests/meta_specialist/test_joint_optimization_v1.py
python -m json.tool docs/evidence/autonomous-integrated-scoreboard-v1-20260813.json >/dev/null
python -m json.tool runs/final-sprint-autonomous/full6-unordered-population-v1/manifest.json >/dev/null
python scripts/docs/validate_docs.py
git diff --check
```

Task2 focused:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_meta_overfit_iteration_v1.py
```

Task3の現在のproducer検証は focused 13 passed、Task3 +既存joint suiteは39 passedという状態である。独立review完了後、review doc/evidence/reportの最新SHAをこのpackの最終追補へ更新する。

## L. goal_alignmentの最新解釈

`runs/final-sprint-autonomous/goal_alignment.jsonl` には、少なくとも次の意味をappendする。

- Task2 second fixは、permissionをsplit名から推測せず、strong native populationをtraining/behaviorへ流す前の境界を閉じた。ただし性能改善ではない。
- Full6-unordered descriptorは、coverage36,680/36,684、ordered quarantine、authority false、ready=falseを固定した。これはpopulation学習完了ではない。
- Task3 focused testsがGREENになったが、独立reviewとreal performanceは未完了。従って長時間学習可能とはまだ言えない。
- 次の直接目的は、Task3/4を閉じ、real META_TRAIN advantageとnative-preserving overrideをcommon24 96→384へ流すこと。

goal alignmentの判断は、常に「nativeの強さを保ったまま、上位meta opponent poolへ適応しているか」で行う。contract/testの追加自体を成果と数えず、実候補のoverride coverage、native差、seat/fault、META_DEV、rollback、packageを次の主要指標とする。

## M. このpackの受け渡し

ChatGPTへ渡すべき単一ファイルはこのファイルである。

`docs/status/chatgpt_full_context_pack_native_meta_overfit_2026-08-13.md`

このファイルはローカル参照を必要としないよう、目的、実績、一次SHA、permission、negative evidence、Full6、Task2、Task3、Task4、性能gate、package、longrun設計、再現コマンド、未解決点を含む。後日作業が進んだ場合は、既存履歴を削除せず、同じファイル末尾へ新しい `authoritative addendum` を追加する。新しい追補には必ず、作業時点、run root、artifact path、file SHA、semantic SHA、test結果、未実施事項、次のgate、goal alignmentを含める。

このpack自身の現時点SHA（この追補を加える前の値）は `c2ceef054c80cd8d60a8ab5a082c5c797f248bb1b68abeec9704981460db1766`。追補後は再計算する。検証コマンドは次の通り。

```bash
sha256sum docs/status/chatgpt_full_context_pack_native_meta_overfit_2026-08-13.md
python scripts/docs/validate_docs.py
git diff --check

```

## N. Task3 producer完了の最新追補（独立review待ち）

Task3 producer側のI-1/I-2/I-3修正が完了した。これは直前の「focused GREEN・独立review待ち」状態からの更新である。独立reviewが終わるまでは、Task3を最終PASSとは呼ばない。

### N.1 修正内容

- I-1: `NativeRegressionJournalV1` がcandidate state SHA、最後のevaluation summary SHA、decision SHA、`consecutive_native_regressions`をcontent-boundに保持する。前回の値を毎回0へ戻さず、同じcandidate lineageでnativeより悪い結果が2回連続したら `stop_after_two=true`、rollback required、lineage停止とする。
- I-2: iteration binding時にTask2 strict verifierを必須呼出しし、iteration manifest、sources、public advantage table、native baselineを再hashする。canonical reload後のsemantic/table SHAと、stateへ束縛されたSHAが一致しなければ拒否する。
- I-3: candidate/nativeの全game recordを必須化し、game_id、seed、opponent_id、family、seat、outcome、faultから、WDL aggregate、seat score、fault countを再導出する。candidate/nativeのcommon24 strata、game-id universe、seed universe、policy/deck/evaluator SHAを一致検証し、summaryだけの自己申告点数を受理しない。
- 公開APIにjournal型とfactoryを追加し、`__all__`からのimport漏れを回帰テストで閉じた。

### N.2 現行証跡

- module: `src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py`
- module SHA: `6f87250e888f3c74a1c6eb63b1fbf3f5fe86a356904d9c973f5f4e957200c2e3`
- tests: `tests/meta_specialist/test_native_meta_overfit_alternating_v1.py`
- tests SHA: `c3aa3b84ee6a4a62ce851f2284196a29953eef3c7ab571f923e8035b5f779178`
- evidence: `docs/evidence/autonomous-native-meta-overfit-alternating-v1-20260813.md`
- evidence SHA: `34dfc52502bfe80aea408c4366c0eea60ea8fc94c239e16560bb581fdb63ecb6`
- task report: `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-3-report.md`
- report SHA: `efcd3ca3d1448a8a7cadbf86fd74dc0ea71720c74eb0762f3b764a981956d2c2`
- producer review package: `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-3-review-package.md`
- review package SHA: `f0658ed15a41211a51d05e2c962792bac1f639072062e823230c2a4bced42562`
- focused Task3: `16 passed in 3.70s`
- combined Task3 + existing alternating optimizer/deck/joint suite: `42 passed in 4.55s`
- py_compile: PASS
- docs validator: `Validated 13 canonical documents.`
- git diff --check / trailing whitespace: PASS

### N.3 残っている判定

Task3の独立Luna reviewでは、次の反例を再現しないことを確認する。

1. journalを渡さず、または別candidate/state SHAのjournalを渡して、regression countをリセットできないこと。
2. Task2 verifierをmonkeypatch/fixtureの自己申告だけで迂回できず、sources/table/native baselineの実bytesを再hashすること。
3. per-game recordの outcome、fault、seat、strata、seed、game IDとsummaryのscoreが食い違ったとき、stage gateを通さないこと。
4. rollback descriptorがcandidate state、iteration manifest、checkpoint bytes、reason、regression count、authority falseを束縛すること。

独立reviewがPASSなら、Task3は「contractとしてGREEN」とし、次にTask4 materializerへ進む。ただし、Task3 PASSはまだ性能改善やlongrun readyではない。

## O. 最新の作業分岐（Task3後）

### review PASSの場合

Task4を新規run rootへmaterializeし、real META_TRAIN outcome adapterとpublic/action-conditioned advantageを入力にする。native Tomatoをimmutable controlとして同一common24へ接続し、candidateはnative-first fallbackから開始する。最初の実性能runは96局だけであり、candidateが実際にnative actionをoverrideした数、support、margin、fallback理由を保存する。

### review FAILの場合

失敗した反例だけをLuna producerへ戻し、同じ性能runを重複起動しない。既存native control、Full6 descriptor、Tomato clean dataset、Task2 artifactは保全する。

### 96局後

- θ0/AWR同型が再びnativeから大幅に弱ければ即停止。
- native-first candidateがoverride0なら、学習器としての性能証拠ではなくfallback smokeに留める。
- +3pt未満でも、action-conditioned support、value confidence、seat balance、fault0、同一protocolが明確なら384へ進める余地がある。
- 384でnativeより悪い、または2 blockで方向が反転するなら、同一loss/threshold/score-bias sweepは行わず、search-derived override、direct native tuning、deck optimization、別native lineageへpivotする。

## P. pack更新後の判定

現在の最終判定を、Task3 producer完了時点で更新すると次の通り。

- Task1: review PASS。
- Task2: I-1/I-2 acceptance PASS、Important0、Minor2。
- Task3: producer focused16/combined42 PASS、独立Luna review待ち。
- Task4: 未完了、Task3 review PASS後に着手。
- Full6-unordered: descriptor作成済みだがready=false、published_rows0、component closure未完了。
- real META_TRAIN advantage: Task1 synthetic tableは契約のみ。性能candidate用の実advantageは未生成。
- native-preserving 96/384 candidate: 未実施。既知のθ0/AWR/score-bias経路はNO-GOで再runしない。
- EvaluationBestKnown: Tomato native provisional 72.0703%。
- SubmissionEligibleBestKnown: Strong Asset poolなし、Rule v0 fallback anchorのみ。
- Longrun: `LONGRUN_NOT_READY`。
- performance/CABT/training/longrun/submission/commit/push: 未実施。

Task3独立review後にこのpackのさらに末尾へ最終review SHAと判定を追記し、その後Task4/real performanceへ移る。

## Q. Task3独立review中の追加反例（最新、修正待ち）

独立Luna reviewのread-only probeで、Task3 producerに重要な残存反例が見つかった。`advance_native_meta_overfit_state_v1` と `promote_native_meta_overfit_successive_halving_v1` が `regression_journal=None` を許しているため、呼出側がjournalを渡さない経路では、candidate scoreがnativeより低いsummaryを2回連続で与えても `previous_count=0` から毎回進み、96→384→768へ進められる。

これは「2回連続regressionでそのlineageをSTOP」というTask3仕様に対するImportant blockerである。性能runへは接続しない。最小修正は、evaluation summaryを処理する公開APIでjournalを必須引数にし、journalなしの評価をfail-closedにすること。代替としてstateにsealed cumulative countを持たせる方法もあるが、既存のcontent-bound journal設計を活かすならjournal必須化が小さい。修正後は、同一candidate/stateでnative未達を2回与え、1回目はcount=1、2回目はSTOP/rollback、journalなしは即拒否となる回帰テストを必ず追加する。

独立reviewの暫定所見は、I-2 strict verifier/source/table SHA rehashとI-3 per-game aggregate/seat/strata/seed/game-id identityは実装上閉じているが、Task3全体のspec判定はこのjournal経路を閉じるまでFAIL候補である。Task3 producerは現在この修正をTDDで行っており、完了後にmodule/tests/evidence/report/review SHAを再計算する。

その後の独立reviewで、journal反例に加えて、successive-halving候補間のnative control identityが十分に拘束されていない反例も確認された。異なるnative control score（例: 0.60と0.90）を、同一baselineとして受理できるprobeがあるため、canonical evaluator ledger/source SHA、common24 expected strata、control block、native policy/deck/evaluator identityをstate/summary/promotionへ必須化する必要がある。これはTask3のもう一つのImportant gateであり、journal必須化と併せてproducerがTDD修正中である。性能runへはまだ接続しない。

## R. Task3 I-4修正とI-5 control binding（最新作業中）

I-4のjournal必須化はproducer側でGREENになった。`advance_native_meta_overfit_state_v1` は全state transitionでbound journalを要求し、`promote_native_meta_overfit_successive_halving_v1` はcandidate集合と完全一致するjournal mappingを要求する。journalなしで2回連続regressionを通過する経路を拒否するRED→GREENテストが追加され、直近のproducer検証はfocused18 / combined44 passed、py_compile/docs validator/diff-check PASSである。

ただし、独立reviewが追加したI-5はまだ修正中である。successive-halvingの候補ごとに、異なるnative control scoreや異なるcontrol artifactを渡しても、単に最初のcandidateのnative scoreをbaselineとしてpromotionできる余地が残っている。次の最小修正では、`NativeBaselineArmV1` を `PROVEN` 状態、native policy/deck/evaluator SHA、canonical evaluator ledger/source SHA、common24 protocol/seed/strata/game-universe SHA、control block SHAと結合し、全候補のcontrol identityが完全一致しない限りpromotionを拒否する。またjournalのto_dict/from_dictまたは永続state再ロード時にcandidate_id/state SHAを再検証する。

I-6 producer修正が入ったが、独立reviewが終わるまではTask3の最終判定を `SPEC_FAIL / NOT_READY` とする。I-2 strict source/table rehash、I-3 per-game/seat/aggregate/identity再導出、authority falseは既に独立probeでPASS相当であり、I-6ではjournal/rollback/summaryの深い改変耐性を追加した。Task4、real advantage、CABT、training、longrunは引き続き未起動。

### R.1 I-5 producer実装完了（独立review待ち）

I-5のproducer側修正はRED→GREENまで進んだ。native control artifact/blockをsummaryとpromotionへbindし、全candidateが同一のnative control identityを使うことを検証する。`NativeBaselineArmV1` は `PROVEN` baselineを要求し、native policy/deck/evaluator、canonical ledger/source、common24 protocol/seed/strata/game-universeをcontrol block SHAへ結合する。forged block、unknown field、UNPROVEN baseline、異なるnative score/artifactを受理するテストを拒否する。journalにはfrom_dict/content-SHA roundtripを追加した。

現時点のproducer SHA（I-5修正後）:

- module `699a64b90932961e05f5b448fb05e51fd29e060dd2a03354e9db2e17866de0ad`
- tests `fe379761ed7a06e614199142a208fda80f86709d72c5c91facc1aeea65583951`
- evidence `97282fe9dc37023467b312eebe6eef1da3f15c765c8484af8067f4b4929be231`
- report `cd84535c77734e799f1e9d5111085362d1fe10459d000611434de063edf03437`
- producer review package `13219e8bbbdfda2cf9eca2803e9e3f1c1ab2cedcb5b1b4015bc84885206d556f`
- focused producer23 passed、combined local suite49 passed、py_compile/docs validator/diff-check PASS

この時点では、独立Luna reviewerが現行bytesを再読し、I-1 journal、I-5 control identity、I-2/I-3、rollback/authorityをPASS判定するまでTask3を最終GREENにしない。性能run、Task4、CABT、training、longrunは未起動である。

### R.2 独立mutation probeで追加された残存穴

現行I-5 bytesに対する追加probeでは、通常のacceptance testは通るが、mutable objectを後から改変すると安全契約を迂回できる反例が見つかった。

- `NativeRegressionJournalV1` の `authority` Mappingを `execute_allowed=true`へ変更しても、同じstate SHAでvalidationが通る。
- `consecutive_native_regressions=1` を `0`へ変更しても、journalのstate SHAが同じならvalidationが通る。これにより2回連続STOPの履歴を実行中にリセットできる。
- `RollbackDescriptorV1` は `count=0`、`reason='arbitrary'`をconstructorで受理し、生成後にauthorityを改変したobject verify経路も再検証しない。

これは、checkpoint/resumeや長時間学習の停止条件を実際のmutable stateへ接続する前に閉じるべき安全問題である。最小修正は、journal/rollbackをfrozenまたはmodule-private sealed stateにし、content-SHAを再計算してからverifyすること。加えてrollbackは`consecutive_native_regressions>=2`、reasonを許可enum、authority全false、candidate/state/control SHA一致に限定する。修正後にauthority mutation、count reset、reason/count gateのRED→GREEN回帰を追加する。

独立probeではさらに、`EvaluationSummaryV1` がfrozen dataclassでも内部のplain-dict game recordsを保持していたため、生成後にrecordのoutcomeを変更し、stored scoreとderived scoreを不一致にしてもstage evaluationが受理される反例があった。I-6 producerはjournal/rollback/summary recordsをdeep immutable/sealedにし、入口でcanonical rederiveする修正を実装した。独立reviewで現行bytesを確認するまで、Task3は `SPEC_FAIL / NOT_READY`、Task4/性能run/longrunは停止する。

### R.3 I-6 producer実装後の機械検証

- module SHA `05aa2ce2e6badf3aefc752bced1d52840a549c4ed0dc24d51c8aa99fb4e8ad22`
- tests SHA `8566095c4bbaa9d66e49b24b25f9c3d7224751cf49b88df5596da56e376f00f4`
- evidence SHA `722c5a854420cd6c6e65b919d484272a4b00bc0c8dcf7978bf01a0aaf2e74567`
- report SHA `dd318ee27515c01836fbb033eec979446205786ab7f5627c85526f89137438ca`
- producer review package SHA `968acc462f76baa0cb99d479bcb49429388f367d2be41ff8911d818b64142511`
- combined Task3 + optimizer/deck/joint suite: `53 passed in 9.13s`
- py_compile/docs validator/git diff-check: PASS（独立review待ち）

I-6の回帰対象は、journal authority/count reset、rollback authority mutation、rollback count=0/reason arbitrary、summary record outcome mutationである。producer実装ではjournal/rollbackをfrozen・authority immutable・serialized re-deriveし、summary recordsをdeep immutable化、`_coerce_summary`で既存instanceもcanonical再構築する。focused27 / combined53、py_compile/docs validator/diff-check PASS。これらを別Luna reviewerが現行bytesへ再適用し、Task3の仕様適合を確定する。

### R.4 I-6再レビューで残ったbind経路

I-6の通常mutation probeは、journal authority、summary record、rollback objectの改変を拒否するよう改善された。一方、公開`NativeRegressionJournalV1.bind`へ外部decisionを渡すと、`decision['consecutive_native_regressions']=0`をそのまま採用してcountをリセットし、次のbad regressionを384→768へ進められる反例が残った。`object.__setattr__`によるlow-level改変は別の敵対モデルとして記録するが、公開bind APIが外部decisionのcountを信頼することは通常経路のImportant blockerである。

最小修正は、bind内部で直前journal countとsummaryのcandidate/native scoreからexpected consecutive countを再導出し、外部decisionのcount/stop/rollbackは一致する場合だけ受理すること（またはcountを外部decisionから完全に無視すること）。`bind(... count=0)`によるリセット拒否をRED→GREEN回帰へ追加し、独立reviewで再probeするまでTask3をNOT_READYに保つ。

### R.5 I-7 bind count rederive完了（独立review待ち）

producerは`NativeRegressionJournalV1.bind`内で、直前journal countとsummaryのcandidate/native scoreから次のcountを内部再導出し、外部decisionのcount、stop_after_two、rollback_requiredが一致する場合だけ受理するよう修正した。直接bindへcount=0を注入しても拒否するRED→GREEN回帰を追加した。

- module SHA `5bb47b978f25377cf7351cf6ec6eae7e8f74b69929b39e154f605a2bc5e6fcd1`
- tests SHA `9565fdf07b1b058573af35b19eba8e80ed99fa88f1424d87dce4801ddffb7b2c`
- evidence SHA `f7ca4961350ca8f6a9592ba3c6b0f52cd7726215ea27884853d0d4b293777396`
- report SHA `ea6270722042f883ebd4455805ef8e44c7d03a67a1005aeb0271ba3a47bf03db`
- producer review package SHA `16589f1781cc8d153f25296f379c729661dbd6f05b0a64495d1785f2275325df`
- focused28 / combined54、py_compile/docs validator/git diff-check PASS

独立Luna reviewerが、I-1 journal omission/count reset、I-5 native control identity、I-6 deep mutation、I-2/I-3、rollback/authorityを現行bytesへ再適用するまで、Task3は最終PASSにしない。

### R.6 I-7再レビューで判明したlineage bind穴

I-7でcount値の直接偽造は拒否できるようになったが、公開`bind`の入力identity検証が不足していた。第一のbad summary後のstateへ、別stage（例: stateは384だがsummaryは96）または別candidateのgood summaryを渡すと、decision count=0を正規sealとして受理し、次のbad stageを連続regressionの外へ逃がせる反例がある。`rebind_state`も同candidateであれば未検証の後続stateを受け付ける。

これは、同一stage・同一candidate・同一native control・同一protocol/recordsに対するgood summaryがcountを正当に0へ戻すこととは異なる。最小修正は、bind入口でsummaryのcandidate_id、stage_games、native pair/policy/deck/evaluator/control identity、protocol、records/strata/seed/game-universeをstateと照合し、decisionをそのexact state+summaryから再導出すること。rebind_stateでは旧stateから許可されたphase/stage/identity遷移と新state SHAを検証する。これを閉じるまでTask3は `SPEC_FAIL / NOT_READY`、Task4/性能runは停止する。

### R.7 I-8 cross-lineage bind修正（作業中）

I-7独立review（SHA `794c99291a44e20c5bdcad7b7551957da266b8ce5e1c2a1fb96e3e5fc09cae55`）は、low-level journal/summary/rollback mutationは拒否できる一方、公開`bind`へ別candidateまたは別stageのgood summaryを渡すとcountを0へ戻せる反例を確認した。仕様適合はFAIL / NOT_READY、Critical0 / Important1 / Minor3である。

I-8では、bind入口でsummaryのcandidate_id、state stage、native control identity、protocol、record/strata/seed/game-universe SHAをstateと照合し、decisionをexact state+summaryから再導出する。`rebind_state`も同candidateというだけでは足りず、旧stateから許可されたphase/stage/identity遷移と新state SHAを検証する。正当な同一candidate・同一stage・同一controlの改善によるcount=0は許可し、cross-candidate/cross-stage/後付けstateはfail-closedにする。修正完了までTask4/性能run/CABT/longrunは未起動のままとする。

I-8 producerは実装と回帰テストを反映し、現行ローカルsuiteは57 passedになった。現行bytesのSHAは module `1c3e73c326e2b07c9d43af2488137c06083042f70034fb491872326c5c0dabe2`、tests `4cdfc761fd3d8f6ff8cb73773b31aaf84dee0794ef20fb3db42ebfd463b55560`、evidence `2b09a76baddf6e1a50fb9dcd144be01ff34c04088dfcfcea154009f1f60fc521`、report `e2b7260e7395b96db4827e208b296e96bdf7713071a367d6fa3407688c356d22`、review package `de2cf2dcb90da947b3f8be0ce5373ba57759097881e1bb5802ea6467ea781ee9`。独立Luna reviewerの現行bytes再probeが終わるまで、最終PASSにはしない。

最新の独立probeでは、公開`NativeRegressionJournalV1.bind`を直接呼ぶと、iteration protocolと異なる自己申告protocol SHA、self-derived native control/artifact/strata、matching decisionを受理する反例も確認された。通常の`advance/promote`経路は先にiteration/state closureを検証するため拒否できるが、公開bind単体のfail-closedが未達である。bindをprivate化するか、bind自身へiteration manifest/table/control/protocol/native baselineの再検証を要求する必要がある。Task3は引き続き`SPEC_FAIL / NOT_READY`である。

### R.8 I-8独立review確定

I-8独立review doc: `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-3-i8-re-review.md`、SHA `b8d35d6a1b6c58605e6a7f86245c279b28b27e5333f11bcb27bc5eb622809fda`。cross-candidate/stage/revision skip、legitimate same-stage good reset、good→next-stage、I-6/I-7 low-level mutationはすべて確認済みである。残存Importantは、exported `bind` direct pathがstrict Task2/common24 closureを迂回する一点だけである。これをprivate化またはverified iteration binding必須化するまでTask3は`SPEC_FAIL / NOT_READY`、Task4/CABT/training/longrun/submissionは未起動とする。

### R.9 I-8 public bind surface除去（独立review待ち）

I-8の残存Importantへ、producerが最小修正を適用した。`NativeRegressionJournalV1.bind` を公開authority surfaceから削除し、内部 `_bind` のみを strict `advance/promote` 経路から呼べる形へ限定した。自己整合した異種protocol（例 `9*64`）、偽decision、self-derived native controlを直接渡すprobeは、公開APIが存在しないため `AttributeError` でfail-closedとなる。

現行producer SHA:

- module `24515c230c48c894994ae5cc9de608d248079acd7dae76fc829e4d9de638daf1`
- tests `387167f9c63f697cbe1f4a83c12d2fc080f2245ea67294319f5067e8c31152b0`
- evidence `8382cec368fbe8ab12b3969cd40464753fa9ccb3c6783a0707599face833dd8f`
- report `17c37af612be1ab05c8009026962a9d75ea6fb8dacabef528d9ddcf094b91d1f`
- producer review package `7e5d23bfde6784e97859c2fa074e2d49ef22026f551103e93306e2d9bed6f1f`

producer検証はfocused32 / combined58 passed、py_compile/docs validator/diff-check/trailing-whitespace PASS。独立Luna reviewerによる現行bytes再probeが完了するまで、Task3は暫定 `REVIEW_PENDING` とし、Task4、real advantage、CABT、training、longrun、submissionは開始しない。

### R.10 Task3最終独立review PASS、Task4開始条件成立

Task3 I-8最終独立reviewが完了した。レビューdocは `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-3-final-re-review.md`、SHA `477373f4bca60fe6c27b04fb25cfd7f5fb23e65dd33532e1046dbd684bf0a1b5` である。判定は **SPEC PASS / QUALITY PASS**、Critical 0 / Important 0 / Minor 1。Minorはprivate `_bind`をreflectionで直接呼ぶ別脅威だけで、通常の公開transitionからは到達できない。

独立probeで、公開`bind`不存在・任意protocolの通常advance拒否、cross-candidate/stage/revision skip、journal/summary/rollback低層改変拒否、正当な同一stage resetとbad→good resetを確認した。4 suiteは `58 passed in 9.31s`、py_compile、docs validator 13、git diff-checkもPASSである。

これによりTask3のstrict regression/source/summary/native-control identity gateは実験開始可能な状態になった。次はTask4 materializerとして、verified META_TRAIN advantageからnative-preserving candidateを作り、candidate/deck/policy/bridge/catalog/sidecar/qualification/rollback lineageをatomicにbindする。ただし現時点ではTask4実装中であり、性能CABT、実学習、longrun、submission、Champion変更はまだ未実施である。

### R.11 Task4 RED開始

Task3 PASSを受け、Luna maxはTask4 materializerの契約確認とREDテスト追加へ移行した。正典plan上の契約は、verified META_TRAIN advantage、native policy/deck identity、candidate table、optional legal deck、source/permission/SHA、authority false、atomic run-root、rollback/resumeを全て閉じたうえで、native-preserving candidateを生成することである。現時点ではTask4の専用CLI/run-root/materializerが未達で、REDテストを追加中である。

Task4はまだGREENでも実artifact生成済みでもない。性能CABT、実学習、longrun、submission、Champion変更は未起動・未成立。Task4がGREENになった後、まず実META_TRAIN advantageのmaterializationとdry-run/reloadを検証し、その後にのみnative Tomato common24 96局を起動する。96局の事前分岐（弱ければ停止、native近辺なら384、明確な上振れなら768以降）は既存の評価計画に従う。

### R.13 Task4 copy atomicity fix（最終独立review待ち）

Task4独立reviewで、public advantage tableのstream copyが途中で失敗した際にpartial destinationが残るImportant反例が見つかった。producerは、sibling temporaryへstream copyし、flush/fsync後に`os.replace`でpublishする`_atomic_copy_new`へ修正した。copy失敗時はtemporaryと、この呼出しがpublishしたdestinationだけを削除し、run-root cleanup後にpartial tableを残さない。

その後の独立probeで、`os.replace`がrun-root claim後に別writerが作った既存destinationをclobberする反例が追加された。producerはさらに`os.link`によるexclusive publishへ変更した。`FileExistsError`時は競合winnerを保持し、temporaryだけをcleanupする。現行shared bytesは script `2fc6b3a5ec7f87b55e74475e43441891167233696937213145bd982beb9baa66`、tests `ba693baec6ba96f05205a7e5706e15acb4de9ffe00d7a2bce28661e336d15031`、focused+iteration 30 passedである。evidence/report/review SHAはproducerの最終seal後に更新する。

追加RED `test_partial_public_table_copy_is_atomic_and_not_left_in_blocked_root` をGREEN化した。現行script SHA `b96e8ca2da8fae387131748a746ad8edca7d72e57828018bb8788d23cc65e942`、tests SHA `f5c4c438398c321fb24b902387956b3b4549eca74d16d5c60df5aebcd8b9cc89`、evidence SHA `811c15ee9504c6be4830bf3609f27d126e44f164becabb3e29303a3e03c23dde`、report SHA `344a335bf8562e5e9a191f89984e84b92af490b375861a458603afedd7ebc218`、review package SHA `882af08bad6d93a4ca4e2680630271dc817a17bde6549ac858c12b6d0e893e92`。focused+iterationは29 passed、py_compile/docs validator/diff-check PASSである。

実入力permission blockerは不変で、current META_TRAIN behavior permission=false、real public advantage table未生成、blocked root `ready_for_evaluation=false`、authority/process falseである。独立Luna final reviewがPASSするまでTask4を最終GREEN扱いせず、性能/CABT/training/longrun/submissionは未起動とする。

### R.14 実advantage source route比較（設計のみ）

Task4のreal public/action-conditioned advantage入力を作る経路を、権限を増やさず比較した。比較正本は `docs/evidence/autonomous-native-advantage-source-route-comparison-v1.json`（SHA `f8fee41ebfc7f43413335c9c96a6e29aa0557bbb35ca0fb49372f66039131653`）と同名Markdown（SHA `412f2769da821a2d04854939ad95581413096eb572df1223817b8f945cc0c94e`）である。

- A: 明示的な behavior permission を得たMETA_TRAIN sourceを先行利用する。現時点は `BLOCKED_UNTIL_EXPLICIT_BEHAVIOR_PERMISSION`、新規局0、既存v2b snapshotの再利用だけを想定する。training-local derived-weight許可だけではbehavior sourceへ昇格できない。
- B: Tomato nativeの自前public-only self-rollout collectorを新規設計する。`state/action/outcome/seat/opponent-family/seed`のみを保存し、teacher labels/private stateを使わない。想定はcommon24 24 opponent×2 seat×2 repetition=96局/snapshotだが、まだ契約/fixture設計のみで実収集0局である。

現時点の推奨は、Aの明示permissionが閉じた場合はAを先行し、閉じない場合に限ってBのcollector契約をTDDで実装してから96局を評価すること。local_eval_only/behavior falseは変更せず、いずれの経路も実収集、training、CABT、submissionへ未接続である。

### R.17 Task4最終独立review PASS

Task4 copy/no-clobber修正の最終独立reviewが完了した。レビューdoc `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-4-final-re-review.md` のSHAは `a6b546dcdb6019b7384389be342a25b3b88c51242318590f803dbe8662b3a8b8`、判定は **SPEC PASS / QUALITY PASS**、Critical 0 / Important 0 / Minor 0 である。

独立probeで、partial copy OSError時のdestination/temp残留なし、blocked rootがprogress/run manifestだけであること、既存destination `WINNER` のbytes保持（`os.link`のFileExistsError）、既存blocked root再実行時のno-clobberを確認した。30 suiteは `30 passed in 13.32s`、py_compile/docs validator/diff-check PASS。現行script SHA `2fc6b3a5ec7f87b55e74475e43441891167233696937213145bd982beb9baa66`、tests `ba693baec6ba96f05205a7e5706e15acb4de9ffe00d7a2bce28661e336d15031`、evidence `dd738bc21c14400636693396df9aa946ea1c1419b113c24719e1e21dad9c48e0`、report `4b336bd3c35511c1f2b7a36a9a2f2429630c7911240e5dd2e74b6dd04050a138`、review package `16a464daf6849e735819545903629f646ee83144efc714d61d717ea4ed56fdc7`。Task4 materializerは最終GREENだが、実input permission blockerとreal advantage欠如は不変である。

### R.16 B routeの権限境界

Bのself-rolloutは、既存local_eval_only/native external sourceをteacher behaviorとして再利用する抜け道ではない。明示的にownedまたはpermission-authorizedなpolicy/agentがない限り、実収集を開始しない。契約設計で許可するのはpublic state、chosen legal action、terminal outcome、seat、opponent family、seed、policy/deck/evaluator SHAのみで、private state、teacher label、外部policyのbehavior label、submission permissionは含めない。

したがって、Bを実装する場合も初期artifactはdry-run/fixture、`ready=false`、authority全false、実収集0局である。common24 24 opponent×2 seat×2 repetition=96局は計画上のscreen規模であり、権限・source closure・portable packageが揃うまで性能結果とは扱わない。

### R.18 B collector初版と独立設計監査

### R.19 B collector blocker修正 GREEN / 実収集は権限待ち

B routeの4 Important + output containmentをTDDで閉じ、さらにdry-runへ未検証recordsを混入できないよう最小強化し、fault gameのfault_status一貫性も検証した。最終collector module SHAは `674a1783052893a2a2edfb08b6309af825bfb6ad5b853101295663a343ce221d`、tests SHA `596116309bd5e8870c948a8238da2eb4f3b70dd50154051be1ba82e9d34f55ba`、evidence SHA `9e26729928c12997da2d29c676fad43512c9fa33b614f680009a39ca20a0281b`、独立review SHA `72c2ab3b02410cc2aa6d8855e5316f50abc757f4815e952d0fc5b157ba383d65`。focused+nearby `32 passed`、py_compile、docs validator 13、git diff-check PASS。

最終契約は、callerの`owned_policy`/fake permissionをissuered source・permission・projection artifactの実bytes+SHAへ結合し、local_eval_only/behavior=falseをready化しない。pool manifest file/semantic SHAからselected 24 opponent/familyを再導出し、public projection schema/auditとcandidate/policy/deck/action identityをbindし、private/teacher/hidden/logprob fieldを拒否する。common24は96/96、24 opponent×2 seat×2 repetition、unique game/seed、連続step、terminal markerが各gameでちょうど1個かつ最終step、fault status、fault-inclusive denominatorを要求する。dry-run manifestはrecordsを含む経路を拒否し、complete snapshotは別APIでのみ検証する。outputは明示repo-root内の新規pathとexclusive no-clobberに限定する。

これは契約GREENであって、実収集GOではない。現Tomatoは`usage_boundary=local_eval_only`、`training_allowed=true`、`behavior_allowed=false`、`submission_allowed=false`で、実permission/family-bound pool/public projection source未供給。B実収集は0局、ready=false、CABT/training/longrun/submission未起動。verified synthetic fixtureは契約テストのみで、性能根拠へ昇格しない。明示self-rollout permissionまたは正式permission-authorized sourceが成立した場合だけ、fresh B root→96局→real advantage→native Tomato common24 screenへ進む。

### R.20 Submission Closure read-only audit

Rule v0 + root deck archiveのlocal package-only経路は、archive verifier、qualification verifier、portable `python -I` importを再確認した。archive SHAは `da4bbe9d...`、4 members（`main.py`、`deck.csv`、`agents/__init__.py`、`agents/rule_agent.py`）、60枚deck、qualification `bundle_allowed`、CABT legality `passed`、authority全false。archive source revisionはdirtyなのでruntime bytes変更時は再build/requalifyが必要。Strong Asset native as-isはlocal_eval_onlyでNO-GO、derived candidateはreal META_TRAIN advantage/portable package未成立でNO-GO、外部Kaggle提出はcontract UNKNOWNかつ明示承認待ちでNO-GO。evidence `docs/evidence/autonomous-submission-closure-readonly-audit-v1-20260813.md` SHA `e6ea4fa86c81efce6bfa3a4c446fc4644af959b20656e846592703960e7a867d`。

B public-only self-rollout collectorの初版は、common24 exact96/deterministic seed、public record allowlist、private/teacher field拒否、teacher behavior禁止、ready/evaluation false、authority false、`--execute`拒否、manifest/plan SHA、exclusive writeを実装し、focused12 passedとなった。

ただし独立Luna設計監査（`.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/b-route-design-review.md`、SHA `35a922d9fc9cd32527672250b47a052a5ad1a5e11faa3a2906e2a05c31f615df`）は DESIGN CONDITIONAL / NOT READY FOR COLLECTION、Critical0 / Important4 / Minor1 と判定した。残存反例は次の通り。

1. `local_eval_only` nativeでも callerが`owned_policy=true`、またはfake permission hash/decision_refを自己申告するとready_for_collection=trueになる。actual permission manifest/identityへのroot-of-trust bindがない。
2. selected 24 opponent IDs/familiesがpool manifest SHAへ照合されず、任意fixture IDsをcommon24として受理する。
3. state/action digestがopaque SHAだけで、public projection由来か、private stateが混入していないかを検証できない。
4. records validatorが96/96 complete、terminal topology、fault status/denominator、step continuityを要求せず、1 game/1 recordでもrecords SHAを生成する。

Minorとしてoutput path containmentも未固定である。現Tomato rowは `usage=local_eval_only, training=true, behavior=false, submission=false` であり、既存policy/deckをB behavior sourceへ昇格できない。producerは4 Important + path containmentをRED→GREEN修正中で、実収集/CABT/training/longrunは未起動である。

### R.12 Task4 materializer GREEN、実入力permission blockerを正式記録

Task4 run-root materializerはGREENになった。新規script `scripts/build_native_meta_overfit_iteration_v1.py` は、repo-contained新規run-root、no-clobber、failure cleanup、candidate table copy、iteration/progress/run manifest、`--execute` fail-closed、child-process不在を検証する。focused suiteは7、Task2との関連suiteは28、独立再実行した関連5-suiteは60 passedである。

実artifact probeは `runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813/` にBLOCKED rootとして固定した。progress SHA `bbc6510516d94c4396cc75eaa17b21616cbf48c94e2b931ae91f00749d5fdfa8`、run-manifest SHA `def3733f0cbde92da13d4668727eb2203012f1f882071603f06265327f3b4b63`。statusはBLOCKED、`ready_for_evaluation=false`、candidate artifacts false、authority全false、CABT/training/submission/process falseである。rootにはprogress/run manifestだけを置き、candidate iteration/table copyは生成していない。

Task4 evidenceは `docs/evidence/autonomous-native-meta-overfit-dryrun-v1-20260813.md`（SHA `08779c5bf381cf326fb6de91a0991b87bfd74ff4297cbb5e9753a15c16ef1569`）、reportは `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-4-report.md`（SHA `cf4313071dc4d40feb3e976e65c742797bf1e387a9be1aca8d9a6ae693ee29eb`）、review packageは `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/task-4-review-package.md`（SHA `de5bfa2bc13c8fc9e7ef4ab141b33fbaae256760332c7064231c9ec7c08480ad`）。script SHA `e5f5169e38566d55ade573872fe37ed96bc5d6171e88fdeed0a6c56a9f429e44`、test SHA `3a208f9450594a2c0082bfe50ffec8f06ef92dccb8b887358e1f48797cba34ac`。

block理由は、現行dynamic curriculumのMETA_TRAIN entryが `teacher_behavior_allowed=false` で、Task2/Task1のtraining-local + behavior permission gateを満たさないこと。また、repo内にreal META_TRAIN actor-visible public-state advantage tableが存在しないこと。Task1 synthetic table（SHA `d62bb3ec85115976c1e101282c60c0aa1d23e90b8b07382fd9268ad159b183b0`）は契約fixtureとしてinventoryしただけで、性能入力・BestKnown・candidate・longrunへ昇格していない。

再開条件は、(1) permission-authorized META_TRAIN source、または明示的に許可された自前native self-rollout、(2) state/action/outcome/seat/weightとsource SHAをboundしたreal public advantage table、(3) materializerのfresh run-root reload、(4)その後のnative common24 96 gateである。既存local_eval_only/behavior=falseを上書きせず、permissionが成立するまで性能runを起動しない。

## FINAL UPDATE 2026-08-14 — 最新実行・停止理由・再開条件（この章を最優先）

この章は、上記の全履歴を保ったまま追加した最新の統合判定である。旧章と数値・判定が矛盾する場合は、この章を優先する。`current_status.md` と `handoff.md` は短い運用サマリであり、外部ChatGPTへ渡す単一の完全資料は引き続き本ファイルである。

### F.1 現在の提出基準と最終目的

- 提出互換の基準anchorは **Rule Agent v0 + root `deck.csv`**。root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、policy closure SHA（`main.py` + `agents/__init__.py` + `agents/rule_agent.py`）`750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`、既存common24 96局 benchmarkは 11/96 = 11.4583%、fault0。
- native Tomato は 1107/1536 = 72.0703%（policy `8908af5c…`, deck `42165967…`）であるが、poolは `local_eval_only`。behavior/teacher/submission permissionを持たず、提出候補や学習教師へ昇格していない。
- 本来の目的（native BestKnownを再現的に超える submission-compatible pair と、deck↔policyの継続改善 longrun）は未達。Champion、提出package、longrun状態は変更しない。

### F.2 2026-08-14の性能screen履歴（最新から遡る）

全runはresearch-only、authority全false、既存production/既存artifact不変である。`workers=12`、`recycle16`（384/768確認は`recycle64`）を既定にし、ResourceGovernorのwarmup/rampを通過したものだけを実行した。

| lane / candidate | 結果 | 判定 |
|---|---:|---|
| Rule v0 root deck v8: `1142 Fighting Gong→1123 Switch` | weighted48で 3/48 vs parent 5/48、−4.765pt、fault0 | STOP |
| Rule v0 root deck v8: `1142 Fighting Gong→1141 Premium Power Pro` | 48/48 `AGENT_INVALID`。無効局を0勝としてscore化していない | INVALID / STOP |
| Tech v6: `6→16 Prism Energy` / `677→682 Stonjourner` | −3.661pt / −4.389pt | STOP |
| v7: `674 Hariyama→673 Makuhita` | weighted +17.4455ptだがcommon24 −1.042pt | hard-negative |
| v7: `1152 Poké Pad→1102 Dusk Ball` | 全48 `AGENT_INVALID` | INVALID / STOP |
| v6以前のItem/Tool/Stadium/Energy/line surfaces | weighted短期positiveがcommon24で反転する系列を複数確認 | hard-negative |
| Rule v0 `1192→1194 Colress` | weighted +3.518pt、common24 +2.604pt、384 +0.260pt | candidate-only |
| Tomato native `1182→1194 Colress` | 384では +6.510pt、768では +0.260ptへ縮小 | candidate-only / no longrun |
| Tomato native Night Stretcher `1152→1097` | common24 +5.208pt後、384で −5.729pt | hard-negative |
| Rule v0 `ABILITY+120` telemetry v3 | 5/48 vs control 7/48、−4.1667pt。coverageは取得済み | STOP |
| Rule v0 non-MAIN lethal +120 | 6/48 vs control 8/48、−4.1667pt | STOP |

weighted48のpositiveだけをcommon24 96へ、common24の明確なpositiveだけを384へ送る逐次gateを維持した。v8のinvalid arm、faultを含むscreen、旧runnerのcoverage不明なscreenは性能値へ変換しない。

### F.3 384/768確認と現在の結論

一時的な短期上振れは再現性gateで落ちている。Nightは parent 284/384 vs candidate 262/384（−5.7292pt）、Colress rootは parent 40/384 vs candidate 41/384（+0.2604pt）、Tomato Colressは384で大幅positiveでも768で +0.2604ptに縮小した。従って、現在の最良候補は「candidate-onlyの診断候補」であり、BestKnown、promotion、longrun、submissionへ昇格しない。

### F.4 V4 / 学習 / permission の境界

- V4 seed1 Archaludonは 54/96 = 56.25%、fault0だが、既存root deckが`[169,190]` coreを満たさず、V4 cellは **CLOSED**。0%性能として扱わない。
- portable closureは `production_entrypoint_not_connected`、`production_card_vocabulary_gate`、`runtime_dependency_closure_unvendored` の3 blockerが残る。V4 candidate JSONも`research_only=true`、training/promotion/submission/longrun authority全false、portable package/closure manifestなし。
- public action traceは sparse/duplicate identityが多く、semantic projectionは`usable_signal=false`。ABILITY/Student/BC/AWR/ProposalMixture/R2D3/PSROを、現local_eval_only poolから教師・behavior sourceとして起動してはならない。
- 現行native poolは`behavior_allowed=false`、`submission_allowed=false`。training-local flagだけでbehavior permissionへ昇格しない。実学習、teacher label、外部policyのbehavior collection、Kaggle提出は未実施。

### F.5 停止時間と原因（障害ではなく意図的停止）

最新v8 evidenceの更新・heavy runnerの最終完了は **2026-08-14 07:22 JST頃**。その後の確認時点（13:28 JST頃）でheavy Python/runner processは0件で、約6時間停止していた。これはcrash/hangではない。

停止理由は、(1) v8 Switchがnegative、(2) Premium Power Pro armが`AGENT_INVALID`でfail-closed、(3) 新しい候補を実行するまで既存候補のblind retryを禁止、(4) V4 portable/permission routeがSTATIC_BLOCKED、の4点である。性能を捏造せず、positive candidate identityが確定するまで次runを起動しない方針により、意図的にidleにした。

### F.6 次回再開条件（速度方針を反映）

新規candidate deck/policyのidentity、known-card/60枚/ACE/core legality、novel multiset、source/pool/evaluator SHAをfresh manifestへ固定した後にだけ再開する。再開時の既定は ResourceGovernor admission → runtime smoke → **weighted48（workers12, recycle16）**。両seat・same strata・fault0・invalid0・unique GID/seedを満たし、candidate-controlがpositiveならcommon24 96へ進む。common24でも再現した場合だけ384（workers12, recycle64）、その後768を個別gateで判断する。

既存Night、Colress、v8、ABILITY、PLAY/EVOLVE/ATTACH/END、旧Studentを同じseedで再実行しない。`AGENT_INVALID`を勝率へ置換しない。明確なpositiveが得られない場合はそのsurfaceをhard-negativeとして保存し、次のnovel surfaceまたは新しいpolicy/deck交互最適化へ進む。longrun/training/submissionは、384/768再現、portable closure、permission、rollback、META_DEV隔離が全て閉じるまで起動しない。

### F.7 最新一次artifact索引

- 最新v8: `docs/evidence/autonomous-rule-v0-root-deck-novel-v8-20260814.md`、SHA `a569c0f40114c681a1809b367d30590bc5f221f9b012ea729b52596f214f1225`。run manifest `e2afec222de1d66909171c862366639677777fdbfaba0208276b3f7a4695d51f`。
- V4 portable audit: `docs/evidence/autonomous-v4-portable-closure-audit-20260814.md`、SHA `3a3d98486d85e3ad80d213d9203526309f12f557ebecaee34ea4a3d0db5aa982`。
- Root Colress 384: `runs/final-sprint-autonomous/rule-v0-root-deck-weighted-support-item-v1-20260814-colress-confirmation384-v1/`、summary SHA `2d8c52b1b1fa91fa56c4afefe9c3cfe9290154bc071714eaa599864112abffeb`。
- Tomato Night 384: `runs/final-sprint-autonomous/resource-aware-tomato-night-confirmation384-v1-20260814/`、evidence SHA `5e2451c6d72ace5c00feb2905c4ec219c311e81f4b1297c2a4934931022c0b6f`。
- Alternating loop: module SHA `a2b65e08e5992e3b3745a4786747b71e1c3b937ec6c01c5d1e5044d384513ac9`、CLI SHA `ce60634a96fbe30fb038d19cb3fff787288eb013c5374c28c0d36a79608f5d33`。epoch1はnegativeでpolicy phase未起動。
- Resource-aware governor: module SHA `2aaa4ed01625361ead9a13c10d2ba1577b11185fbc2fbbd53e624f6b47bf9508`、推奨workers=12、GPU computeなし、kill=0。adapterはperformance runを自動起動しない。

### F.8 次の候補（静的選定のみ、実CABT未起動）

v8後の再開候補を既存157 unique multiset、過去manifest、opponents deck、60枚/known vocabulary/core/ACE SPEC制約で再走査した。候補はすべてruntime smoke合格後にのみweighted48へ送る。現時点で性能値、promotion、common24、384の根拠にはしていない。

1. 推奨1: `1102 Dusk Ball → 135 Bloodmoon Ursaluna`。candidate SHA `1a4bc1416b095be66eba0180b3315a03c44102508fe578e848b68d7bc045f651`、deck SHA `ce7e51d84ab02d85a2ddcafcdd4d1d17fec3692d53f0c78444cd048db929706d`、multiset SHA `9497277ed1708e74d3609d1548189440f500737c991c53c463998fa57ade21bf`。
2. 推奨2: `1102 Dusk Ball → 1225 Hilda`。candidate SHA `1f636fb20284ac62cb6262983f40db5ef14a345cf05c20862cfe234c4b16f854`、deck SHA `bcae6d8e12ec118da52ad84ac38ea58a3c747e436f3403d8b6425aeda1c2dbc4`、multiset SHA `bcd0a2135d460ff2127369a24144ba04b317a1f09407c4ab57553640301a07b4`。
3. 予備: `1102 → 1185 Explorer’s Guidance`（deck SHA `a3755cf993a59d242b14fd5bcac4d0b5dc62d409c545cb202cf3db3f013b945d`、multiset SHA `3ccf47bcbb419f96fb68c6a4ddae92d561f8c92615280b12ab3261752f21510e`）。
4. 予備: `1102 → 1197 Xerosic’s Machinations`（deck SHA `48157896683525133498ddfff2a14b73eb2901f6a2e2fa65305791d1cf79ba25`、multiset SHA `d91a6cc823bbccac5ec8bd56394910716b70074d04b0fe9ec20f039ef420ccbf`）。

この4件は候補生成・静的合法性・novelty確認までで、実行順は runtime smoke → 上位2件のみ workers12/recycle16 weighted48 → positive時だけcommon24 96 とする。smokeで`AGENT_INVALID`、選択範囲違反、copy/ACE/core違反が1件でも出た候補はscore化せず停止する。

この完全packの最新SHAは、更新後に`sha256sum`で再計算して`current_status.md`の単一pack参照へ反映する。Git commit/push、Kaggle提出、外部送信は行っていない。既存dirty worktreeと過去artifactは保全する。

## FINAL UPDATE 2026-08-14 — Dusk Ball候補のweighted→common24→384確認

上記F.8の静的候補を実行した。runtime smokeは Bloodmoon/Hilda とも各2局DONE・fault0で通過した。ResourceGovernorはWSLの`nvidia-smi`がOSによりブロックされる環境でも、CPU/memoryが健全ならCPU-only `normal` として workers=12を推奨するよう修正した。GPUを要求する経路は`gpu_count=0`で拒否され、GPU権限を偽装しない。これにより「GPU telemetry不可」と「計算資源異常」を分離した。

weighted48（親＋2候補、各48、workers12/recycle16）は全144局DONE/fault0。親3/48、Bloodmoon 3/48、Hilda 3/48で、両候補のweighted差は+0.1896pt。common24（親＋2候補、各96、24 opponent×両seat×2、workers12/recycle16）も全288局DONE/fault0で、親8/96に対しBloodmoon 12/96、Hilda 12/96（両方+4.1667pt）。同率のため宣言優先順位1位のBloodmoonだけをseed-disjoint confirmation384へ進めた。

confirmation384（親＋Bloodmoon、各384、24 opponent×両seat×8、base seed 23630000、workers12/recycle64）は全768局DONE/fault0、seat192/arm/seat、paired strata/seed/GID gate PASS。親は41W-1D-342L（score 10.8073%）、Bloodmoonは38W-0D-346L（9.8958%）で、差は **−0.9115pt**。短期common24の+4.1667ptは再現しなかったため、candidate-only/STOPとする。Hildaの384は起動せず、768、longrun、promotion、training、submissionにも進めない。同じDusk surfaceのblind retryも禁止する。

一次evidence: `docs/evidence/autonomous-rule-v0-root-deck-dusk-v10-20260814.md`。weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-weighted48-20260814-retry-v2/`（summary SHA `3c3dd00841bb13a3587758b92f7902c27c87f51168a2782489318a77c8ea7665`、manifest SHA `9b2216aad853596d695ea5ede9a3be6c38826741c3fea9d4b7c26a8cfcb40d2e`）、common24 root（manifest SHA `4122383f5a3d46e9d08ce8cd7ce803d67b78781b3be467a920c75079f5c3dec3`）、confirmation root（summary SHA `b5f186e5979b18f6ec79c293f36f6109b77b8ca569be4beade37da92d9da5d57`、manifest SHA `8fc4c5deecc35f0fbb9f0bd48e482c851cd6b407f18e0d2128d952274488b54d`）。

新規 runner/test/evidence とResourceGovernor回帰を `py_compile`、focused tests、`scripts/docs/validate_docs.py`（13 canonical documents）、`git diff --check` で検証した。production `main.py`/agents、Champion、既存artifactは変更していない。現在の最短再開条件は、別の新規deck/policy surfaceをruntime smoke後、workers12/recycle16のweighted48へ投入することであり、既評価Dusk/Bloodmoon/Hilda surfaceの再実行ではない。

## FINAL UPDATE 2026-08-14 — 95cc native親のMETA_TRAIN近傍探索

既存の研究資産であるTomato native policy（policy SHA `8908af5c…`、usage=`local_eval_only`）と、先行確認済みの95cc deck（deck SHA `fa66263d…`）を親に固定し、sealed META_TRAIN上位12 opponentの重み付きカード頻度からnovelな1-card候補を2件生成した。候補は`8→6`（candidate `7dd0a11d7113…`）と`8→3`（candidate `49e6c13cdbf9…`）で、known vocabulary・60枚・既存multiset非重複を確認した。

runtime smokeは親＋両候補の各2局（両seat）で全6局DONE/fault0。続くweighted48はworkers12/recycle16、12 META_TRAIN IDs×2 seat×2 repetitionを各arm48局、合計144局、全DONE/fault0。親のweighted scoreは0.6420337、`8→6`は0.6601390（+1.8105pt）、`8→3`は0.7059470（+6.3913pt）だった。ResourceGovernorはnormal、12 workers admitted、18.1044 games/s。

positive 2候補をcommon24へ同一24 opponent×2 seat×2 repetitionで送ったが、288局すべてDONE/fault0で、親・`8→6`・`8→3`の全armが67/96（69.7917%）となり、candidate deltaは両方0.0ptだった。384/768/longrun・training・promotion・submissionは起動していない。同じ95cc候補・seedのblind retryも禁止する。判定はcandidate-only / common24で再現せず、次のnovel surface待ち。

一次evidenceは`docs/evidence/autonomous-meta-weighted-95cc-neighborhood-v1-20260814.md`。weighted rootは`runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-v1-20260814`、common24 rootは`runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-common24-v1-20260814`。weighted manifest SHA `0ef180944aa0ec12c7cdff3021b5c97f976f6406e6554a99e3dfd5c750da13ee`、weighted summary SHA `56d76e0f7b2c67dcf53cdb5d77cfbdab2afdb9f68ccf77f8a4c106617b108777`、common24 summary SHA `4f896ba94ed070342ac3bf772158d0ab0ffc60df451ec9da3b3efac112ed409b`。新wrapper/testはpy_compile・focused5・docs validator13・diff-checkを通過し、production/Champion/既存artifactは不変。

v1の候補を再実行せず、同じ95cc親からgenerator seed `23673000`でv2のnovel候補2件を生成した。runtime smokeは親＋両候補の6局を全てDONE/fault0で通過。weighted48（base seed `23674000`、workers12/recycle16、144局、全DONE/fault0）は親35/48、`1097→6`候補37/48（weighted +3.2905pt）、もう1候補は−6.7798ptだった。陽性の`1097→6`だけをcommon24（base seed `23675000`）へ送り、親69/96対候補67/96（−2.0833pt、全192局DONE/fault0）へ反転した。95cc近傍v2も384/768/longrun・training・promotion・submissionへ進めず、同系列をcandidate-only/hard-negativeとして閉じた。v2 weighted manifest `361062be92a6aaabe900c6406d3457f3f482da294c2297f043ce4fd94d558a5e`、weighted summary `4461b753f249cdb39bc538e74538aa5148bb12402b9b75bd3f70eea131456b86`、common24 summary `70729ca6e932f87b78ab595a82fdc8ba4bcb4fef12fc1d4ffa02301232a18da3`。
## FINAL UPDATE 2026-08-14 — Rule v0 2-card coordinated package（768確認）

提出互換のP0（Rule v0＋root `deck.csv`）で、1-card近傍とは別に2-card coordinated packageを2件生成した。`8de3e32b1ed3f3c229c418412a722d99384b3986b28797a0a8d7d6eb15f5a057` は `[1123,1142]→[1086,3]`、`ad5b284c34d6167bb91ec79ee60ac9bd67fb3c8f12f3d3798e70c5f3234d32c6` は `[1102,1227]→[1086,1086]`。known vocabulary・60枚・root core・ACE・novel multisetを満たし、production/Championは不変。

runtime smokeは親＋2候補の6局を全DONE/fault0。weighted48（workers12/recycle16、144局）では親4/48、8de3は+0.9655pt、ad5bは−1.6572pt。8de3だけcommon24へ送り、親7/96対候補12/96（+5.2083pt、192局DONE/fault0）。seed-disjoint 384（base23683000、各384、workers12/recycle64）は親42/384対候補55/384（+3.3854pt、768局DONE/fault0）。さらにseed-disjoint 768（base23684000、各768、workers12/recycle64）は親71/768対候補82/768（+1.4323pt、1536局DONE/fault0）へ縮小した。

全段階で両seat・24 opponents・paired strata/seed/GID gate、ResourceGovernor normal、authority全false、heldout training exposure=0。768で差が縮小したためcandidate-onlyを維持し、1536、longrun、promotion、submission、policy training、native teacher collectionは起動しない。SubmissionEligibleBestKnownは引き続きRule v0＋root deck 11/96（11.4583%、fault0）。native Tomato 72.0703%はlocal_eval_only benchmarkで提出・教師には使わない。

正典evidence: `docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v1-20260814.md`。weighted manifest `86bc468305cc21175e755dfff60d7a3e9decee5e294ca12fb549503d10dc12d2`、common24 summary `fd3e02e49bb7ed4871c4fff8bf7637ef2d456d759dd983eb62512abd70b27b39`、384 summary `d0dbedfd426415a54c031ad26eb676e28575d70a795205b566641a94fbd5993c`、768 summary `0704284fdf75b26f7e830d462b28e14782784a93215ecab615e26d880172033c`。新規 package/confirmation wrapperとfocused testsは10 passed、py_compile PASS、processなし。次は既評価surfaceのblind retryではなく、新規novel package/policy surfaceをworkers12でsmoke→weighted48→common24へ送る。

v2では同一candidateを再実行せず generator seed `23685000`で2-card packageを2件生成した。weighted48（144局、workers12/recycle16）は両候補が親比+0.4089pt/+0.7816ptだったが、common24（各96、計288、全DONE/fault0）で−6.25pt/−5.2083ptへ反転し、384には進めなかった。v2 weighted manifest `9fe2e0507cc7fe7d53ee14126ed663b55300963e97d1ef255b3a5ce367ff35bb`、common24 summary `48a2e83f5c68073bbea0bae05effaf7127c9f9658c1f043fdb697073876e913d`。

v3では generator seed `23688000`で `[1102,1102]→[1219,3]` と `[1141,1252]→[3,1198]` を生成。weighted48は前者−7.1233pt、後者+0.8530pt。後者だけcommon24へ送り、親9/96対候補11/96（+2.0833pt、fault0）だったが、seed-disjoint 384（base23691000、workers12/recycle64）は親48/384対候補44/384（−1.0417pt）へ反転した。v3はcandidate-only/STOP、1536/longrun/promotion/submission/同候補blind retryはなし。途中の候補ID固定によるwrapper fail-closedは実験開始前に発生し、`--candidate-id`束縛を追加して正しい候補を一度だけ実行した。v3 weighted manifest `02a1ec99529048a83cf8644bd2d489f46020afd297b2b4ae14e28797daddfbe2`、common24 summary `1bc88a8f6be7e7991246a70878fb02ac1173224a51f1985c7e7a34d9f0482a15`、384 summary `bc99e66dd0439dcd9013aec00f6131a4ccb5726ac8b86e2ac128fcd713b0e921`。

## FINAL UPDATE 2026-08-14 — Rule v0 2-card coordinated package v4

v1〜v3と重複しない2-card packageをgenerator seed `23692000`で生成し、Rule v0＋root deck親を固定した。`06c7d58d…` は `[1142,1182]→[3,3]`、`651da340…` は `[1182,1192]→[3,5]`。runtime smokeは親＋候補2件の6局すべてDONE/fault0。weighted48（親＋2候補、144局、workers12/recycle16）は親4/48、候補4/48（−0.0444pt）、候補6/48（+4.6449pt）だった。陽性の`651da340…`のみcommon24へ送ったが、親10/96・候補10/96（0.0pt、192/192 DONE/fault0、両seat/24 opponents/paired seed/GID gate PASS）へ反転したためcandidate-only/STOPとした。384/768/1536、longrun、promotion、training、native teacher、submission、同候補blind retryは起動しない。

weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v4-20260814/`、manifest SHA `fed3db9cba03fe15ae7ebcc8f0d4c722ad758ee13062d3d63262917cd62acec6`、summary SHA `18693dd5ee2f7d0d5c5ab378ffb5054052b224dfb9c1f1c70d3953e3106c5292`、runtime smoke SHA `c3ae87b78a24df92749401b9ba1e1a3d20ff3cc3ff0d6329c250c0e810ea15a4`。common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v4-common24-20260814/`、manifest SHA `e3667ae9598bc8bb9ccc9cd14ca9ef1b3dea35fdb1ad10ba67709af4b5d2f1b3`、summary SHA `18701430945549fbfaeafa7728727d11075fa7323b6961a901faacf85b306283`。ResourceGovernor normal、workers12、weighted throughput18.535 games/s、common24 throughput16.645 games/s、restart/kill0。一次evidenceは `docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v4-20260814.md`。既存Champion/production/permissionは不変。

## FINAL UPDATE 2026-08-14 — Rule v0 2-card coordinated package v5

v4とmultisetが重複しない2件をgenerator seed `23695000`で生成した。`0b49700b…` は `[1152,1182]→[3,3]`、`fc0bfd8d…` は `[1141,1227]→[5,3]`。runtime smokeは親＋候補2件の6局すべてDONE/fault0。weighted48（144局、workers12/recycle16）は親2/48、候補5/48（+6.2545pt）、候補6/48（+8.9595pt）だったが、common24では親13/96、候補13/96（0.0pt）と候補11/96（−2.0833pt）へ反転した。したがってv5はcandidate-only/hard-negative、384/768/1536、longrun、promotion、training、native teacher、submission、同候補blind retryは起動しない。

weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v5-20260814/`、manifest SHA `ded751844988cb7500f4c4e13122994cf0158537783a375dc3bc542d1f744879`、summary SHA `7cd537df3bd822b5544ef2d5c78d2fe5a142c4f14eb7a2e65104c39a4e4b48f2`、runtime smoke SHA `bdf814e1eba25924a38fe7b04df2f3320d887515dfebfc2bca0175985a6e91fa`。common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v5-common24-20260814/`、manifest SHA `d7da287c87e8ab5b1e660898be3328d8d1811dc27d01cf4a9b6bb6e6458c5569`、summary SHA `6674c5df7f62c3a0d52a9d4b0e7970e886afc2ad1c46104e7e8b68e6758d3243`。全authority false、heldout exposure0、ResourceGovernor normal、processなし。一次evidenceは `docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v5-20260814.md`。

## FINAL UPDATE 2026-08-14 — Rule v0 coordinated package v6（384で縮小）

v5とnovel multisetを分離した2-card packageを、P0 Rule v0＋root deckで評価した。setup redundancy `[1102,1142]→[1225,1121]`（candidate `84fe042c…`）と recovery/reset `[1152,1182]→[1097,1213]`（candidate `9f1ea003…`）は runtime smoke、weighted48、common24を全てfault0で通過した。weighted48（各48、workers12/recycle16）は親3勝に対しsetup6勝、recovery6勝。common24（各96）は親8勝、setup9勝、recovery11勝で、recoveryのみ+3.125pt gateを通過した。

seed-disjoint confirmation384（各384、workers12/recycle64）は親44W-0D-340L、recovery45W-0D-339Lで、差は+1勝/+0.2604ptに縮小した。全768局DONE/fault0、両seat、24 opponents、paired strata/seed/GID gate PASS、authority false。したがってcandidate-only/STOPとし、768/1536/longrun/promotion/training/submissionは起動しない。setupの384は未実施。一次evidenceは `docs/evidence/autonomous-rule-v0-package-v6-phase-policy-20260814.md`、confirmation manifest SHA `7b9b9c2765b72f785ca86069157fce2b56dc6da60d64772b6525b6e60b0e0167`、summary SHA `0717b5539e5b720758cc27070f55033150896b36cee7e8860565ff2c45d9b37a`。

## FINAL UPDATE 2026-08-14 — 条件付きRule v0 policy screen（停止）

deck固定・policy-onlyの交互最適化入口として、公開条件 `energyAttached=true` かつ `turnActionCount>=2` の必須MAIN選択に限りATTACKへ+240する研究用overlayを作った。条件外、非MAIN、malformed、illegal、optional、例外はRule v0 exact fallbackで、private情報・teacher/native behaviorは未使用。

weighted48（candidate/control各48、12 META_TRAIN IDs×両seat×2、workers12/recycle16）は candidate 4W-1D-43L（9.375%）、control 6W-0D-42L（12.5%）、差 −3.125pt、全96局DONE/fault0だった。負差のためcommon24/384へ進めず、同surfaceのblind retryも禁止する。root `runs/final-sprint-autonomous/rule-v0-phase-conditioned-policy-screen-v1-20260814/`、manifest SHA `0d38bd78c439f3fa552befc3be0033afdfe54cf070156fc73efa2fbcbc6a30fe`、summary SHA `8398d423ff9cab8cbec115ff89c8b463863f2c598e5e7720af13afd80ea75ac8`。focused tests、py_compile、docs validator13、diff-check PASS。次は新規deck packageまたは新しいpublic-state policy仮説のsmoke→workers12 weighted48であり、既評価surfaceの再実行ではない。
## FINAL UPDATE 2026-08-14 — Rule v0 coordinated package v7（weightedで停止）

既評価multisetを自動除外する新seed `23703000`の2-card packageを、runtime smoke→workers12/recycle16 weighted48で評価した。`[1102,1142]→[3,5]` は親5/48対4/48（−2.6789pt）、`[1123,1227]→[3,3]` も4/48（−2.3188pt）で、全144局DONE/fault0、両seat/12 META_TRAIN opponent/unique GID・seed gate PASS。両候補negativeのためcommon24/384/768/longrunを起動しない。同候補blind retry、Champion更新、training、submissionはなし。

一次evidenceは `docs/evidence/autonomous-rule-v0-package-v7-20260814.md`。root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v7-20260814/`、manifest SHA `16d92ecb9637a38802ed812065b59364eef3f1dbbb98728e019a57f278ea0267`、weighted summary SHA `c96901229953aa107635e255b190a1fa8b1812586335bd82959f14e5c40b6275`、runtime smoke SHA `5116531ee619a0d894c2dde64aa3ff151c2156ec757dc3cc4bb34f2997b846ec`。次は既評価面のretryではなく、別のnovel deck/policy仮説を同じworkers12 gateで選ぶ。
## FINAL UPDATE 2026-08-14 — Rule v0 coordinated package v8（common24で停止）

v7までのmultisetを除外し、`[1102,1123]→[3,1121]` と `[1141,1142]→[1122,3]` を新seed `23705000`で評価した。weighted48では候補差+0.7375pt/+1.2051ptだったが、common24（全288局DONE/fault0、workers12/recycle16）で候補Aは親と同率11/96（0.0pt）、候補Bは6/96対親11/96（−5.2083pt）へ反転した。384/768/longrun/promotion/training/submissionは起動せず、candidate-only/STOP。一次evidenceは `docs/evidence/autonomous-rule-v0-package-v8-20260814.md`、weighted manifest `1d664051cce9d9c0fdcd013c835c0528f8a91801a45f34f42f084652e0b1e15e`、common24 summary `96feebd492b0ca823a6ad6923fccf0c1bbfe3f5b6d0091d087ad9b3b8041b6f9`。
## FINAL UPDATE 2026-08-14 — Rule v0 coordinated package v9（weightedで停止）

新seed `23708000`の2-card package `[1152,1152]→[3,1121]` と `[1182,1227]→[3,1122]` をruntime smoke後にweighted48へ投入した。候補差はそれぞれ−7.3836pt、−10.6352pt。全144局DONE/fault0、workers12/recycle16、両seat/12 META_TRAIN opponent/paired seed/GID gate PASS。common24/384/768/longrunは起動せず、v9はhard-negative/candidate-only。同候補blind retry、Champion更新、training、submissionはなし。Evidence `docs/evidence/autonomous-rule-v0-package-v9-20260814.md`、manifest `80e4d75f64f40789fdbf14160c55208792b2c8485dbbdb22299056a17dce7947`、summary `670a661cdd6423c31a131ebf693b79398fdb7105856aa59eb06918d470daf10f`。

## FINAL UPDATE 2026-08-14 — Dusk priority / coordinated package / reserve screens

最新FINAL SPRINT指示に従い、submission-compatibleなRule v0＋root deckを主線に、runtime smoke→workers12/recycle16 weighted48→common24の順で新規候補を評価した。Dusk Ball（1102）からBloodmoon Ursaluna（135）／Hilda（1225）への候補はsmoke全6局DONE/fault0、weighted48は両候補negative（Bloodmoon −1.3317pt、Hilda −2.8002pt）で停止した。2-card coordinated package v10も全144局DONE/fault0で、`[1102,1102]→[135,1225]` は−11.5972pt、`[1102,1142]→[135,1121]` は−8.7707pt。reserveのExplorer’s Guidance（1185）／Xerosic’s Machinations（1197）もweightedでそれぞれ−5.2774pt／−0.8492pt、全144局DONE/fault0で停止した。Dusk/Bloodmoon/Hilda/Explorer/Xerosicのblind retry、common24/384/longrunは起動しない。

## FINAL UPDATE 2026-08-14 — self-owned cg submission candidate（research-only）

Rule v0をそのまま提出候補とする以外の、self-ownedで提出互換性を検証できる候補として、root deck固定の`cg.api`ポリシーを隔離実装した。候補ポリシーは公開観測だけを使い、60枚root deckを固定し、未知・不正・非合法選択は合法fallbackへ閉じる。提出用archiveは次の7ファイルだけで構成される：`main.py`、`deck.csv`、`cg/__init__.py`、`cg/api.py`、`cg/sim.py`、`cg/utils.py`、`cg/libcg.so`。clean-room subprocess smokeは2/2 DONE、fault0、illegal_actions0、合計73 steps。runtime closureは未解決依存0、unknown third-party0、`cg/libcg.so`のSHA `ffd89bf923525a3e6feb5e6201e96a866c0f456895499ed5c4a566303caae67c`。

候補policy source SHAは`617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`、builder SHAは`6d3b9798662dca5b7ea6af981978169fb869ad84c7a95c83f09b3167aa8279b`、arena runner SHAは`a429e85a669e944e38e961969edcfdb218b761033c9e38210e70bc3646bddb1b`。archive SHAは`278438be73b73d1be385810530dadf6d3679711cd218b78b9847c48d15ca1bb5`、candidate manifest SHAは`282be186acd0466b083c7948e1465196a80ea2885e3339e845470b9b3f594fa0`。

同一root deck・broad24でRule v0 controlとpaired評価した結果は、common24 96でcandidate 11/96、control 7/96（+4.1667pt、192/192 DONE/fault0）、seed-disjoint confirmation384 retryでcandidate 60/384、control 34/384（+6.7708pt、768/768 DONE/fault0）、confirmation768でcandidate 123/768、control 92/768（+4.0365pt、1536/1536 DONE/fault0）だった。longrun1536ではcandidate 267W-1D-1268L、control 184W-1D-1350L、候補17.4154%対control12.0117%、差+5.4036pt。ただしcontrol側にpre-CABT deck identity faultが1件あり、requested 3072のうち3071 DONEとして扱う。候補faultは0であり、faultを勝利へ変換していない。

このarchiveはclean-room runtime probeには成功したが、公式Rule/student verifierとは`cg` runtime shapeが異なり、`submission_ready=false`。host runtime contractはPython3.12/kaggle-environments 1.32提供を前提にし、依存欠落時はfail-closed。現時点のSubmissionEligibleBestKnownは従来通りRule v0＋root deck 11/96（11.4583%、fault0）であり、Champion、production、Kaggle submission、native teacher collection、trainingは変更・実行していない。native Tomato/Lucifer/Plamenはlocal_eval_only benchmarkであり、as-is提出・teacher利用不可。

一次evidenceは`docs/evidence/autonomous-root-cg-submission-candidate-arena-20260814.md`、archive rootは`runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/`、arena rootsは`root-cg-candidate-arena-common24-96-20260814`、`root-cg-candidate-arena-confirmation384-retry-v1-20260814`、`root-cg-candidate-arena-confirmation768-20260814`、`root-cg-candidate-arena-longrun1536-20260814`。最初の384 runのcandidate側一過性faultは不採用とし、直接再構成がDONEだった後にseed-disjoint retryを一度だけ実施した。longrunのcontrol faultは勝利へ変換していない。

## FINAL UPDATE 2026-08-14 — 現在の判断と次の実行条件

小規模weighted positiveだけでは採用せず、common24→384→768の再現性を優先する。self-owned cg候補は性能上はRule v0 controlを上回ったが、公式提出verifier・portable package contract未接続のため、性能候補（research-only）に留める。次の主線は、同じ候補のblind retryではなく、self-owned policy＋root deckに対する新規・novelなdeck packageをruntime smoke後にworkers12/recycle16で評価し、positiveならcommon24へ進めること。policyとdeckを同時に変更せず、候補identity・deck SHA・policy SHA・opponent/seat/seed strataをmanifestへbindする。

ResourceGovernorはlogical CPU約28、memory cap約48GB、通常workers12/recycle16、384/768はrecycle64、GPU heavyは同時1を既定とする。現在active processはなく、既存production・Champion・過去artifactは不変。commit、push、Kaggle submission、external sendは未実施。既存dirty worktreeは保全する。

## FINAL UPDATE 2026-08-14 — self-owned cg policy固定のDusk deck screen

self-owned `cg.api` policyを固定し、Dusk Ball 1102→Bloodmoon Ursaluna 135 と 1102→Hilda 1225 の2 deck candidateを別package化した。各packageは7-file runtime closure、clean-room smoke 2/2 DONE、fault0、illegal_actions0で通過したが、公式Rule/student verifierとはruntime shapeが異なるため`submission_ready=false`を維持する。policy source SHA `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`、builder SHA（source-deck binding対応後）`d5cda2e312f61f97c27e6a08160417288b627e3edf91d96698b3ab4429c7b0a4`、arena runner SHA（candidate package deck binding対応後）`cb46b45fbedeb1b3891aa163b69e4c8853fe7b62cfdd465dc738412f28f4af16`。

同一24 opponent broad pool・両seat・paired seed strataで、weighted48相当はBloodmoon 5/48対control4/48（+2.0833pt）、Hilda 8/48対6/48（+4.1667pt）。common24ではBloodmoon 13/96対18/96（−5.2083pt）で停止、Hilda 12/96対10/96（+2.0833pt）で継続。Hildaのseed-disjoint 384は66/384対45/384（+5.4688pt、全768 DONE/fault0）、768は109/768対102/768（+0.9115pt、全1536 DONE/fault0）へ縮小した。両seat支持・fault0・authority falseだが、768で小差になったためlongrun/1536/promotion/submissionへ進めない。Bloodmoon/Hildaのblind retryもしない。

一次evidenceは`docs/evidence/autonomous-root-cg-dusk-deck-arena-v1-20260814.md` SHA `583e9cd05a23246cf5a247e28e039961db744e07677e0f0e60773285c7dc8859`。package manifest SHAはBloodmoon `03543ee03d71a577b3983213f42965d01ec7c8c794d3d1616ac99bf9462a9db3`、Hilda `db273287f2a57e7ca1c8c89ffab649c23fe9439bc394f97ec071164ed540f328`。現SubmissionEligibleBestKnownはRule v0＋root deck 11/96（11.4583%、fault0）のまま。次は同じDusk surfaceではなく、self-owned policy固定の新規novel deck/packageをsmoke→workers12 weighted48→common24へ投入する。

## FINAL UPDATE 2026-08-14 — self-owned cg reserve / coordinated package screens

self-owned `cg.api` policy固定でreserveと2-card packageを追加screenした。Explorer’s Guidance（1102→1185）はweighted48で5/48対control3/48（+4.1667pt）だったが、common24で13/96対13W-1D/96（−0.5208pt）。Xerosic’s Machinations（1102→1197）はweighted48で5/48対9/48（−8.3333pt）で停止した。

2-card packageのweighted48は、Dusk+Hilda/Bloodmoon 9/48対7/48（+4.1667pt）、Dusk+Bloodmoon/Ultra Ball 9/48対7/48（+4.1667pt）、Dusk+Petrel 9/48対3/48（+12.5pt）、PowerPro+Stretcher 6/48対4/48（+4.1667pt）。common24では前2件がそれぞれcontrol fault1を含む8/96対10/96（invalid）と16/96対16/96（0pt）で停止。後2件はともに+8.3333pt（Dusk+Petrel 18/96対10/96、PowerPro+Stretcher 16/96対8/96）だったため、最良候補としてDusk+Petrelのみ384へ送った。

Dusk+Petrelの384はcandidate 53/384対control55W-1D/384（−0.6510pt、全768局DONE/fault0）へ反転し、PowerPro+Stretcherとともにcandidate-onlyで停止した。全候補はworkers12、weighted/common24 recycle16、384 recycle64、同一24 broad pool・両seat・paired seed/GID gateで評価。768/longrun/promotion/submissionは起動しない。正典evidenceは`docs/evidence/autonomous-root-cg-deck-package-screen-v1-20260814.md`（更新後SHAをstatus/handoffへ記録）。

## FINAL UPDATE 2026-08-14 — 現時点の主判断

self-owned cg policy固定のroot deck variantではHilda単体が384で+5.4688pt、768で+0.9115ptへ縮小し、Dusk+Petrel packageも384で負へ反転した。したがって、現時点で公式提出可能なpairを昇格できる証拠はない。SubmissionEligibleBestKnownはRule v0＋root deck 11/96（11.4583%、fault0）を維持する。cg archiveはclean-room runtimeには成功しているが、公式verifier/runtime contract未接続で`submission_ready=false`。次は既評価candidateのblind retryではなく、新規novel package生成→smoke→workers12 weighted48→common24へ戻る。

## 2026-08-14 — 運用承認と並列実行の固定

ユーザーから今後の研究実行・候補生成・資料更新を包括承認された。以後、既存production、Champion、提出物、外部送信は変更せず、research-only / authority=false の範囲で候補生成と実性能評価を停止しない。通常の独立ゲーム評価はResourceGovernorを通し、workers=12・recycle=16を既定とする。384/768局はrecycle=64、GPU heavyは同時1を上限とする。候補はruntime smoke→weighted48→common24→seed-disjoint384→768の順で進め、weightedの小差だけでは昇格しない。AGENT_INVALID、illegal、deck validation、faultは勝率へ変換せずINVALID/STOPとし、META_FINAL/heldoutを学習重みへ投入しない。SubmissionEligibleBestKnownはRule v0＋root deck 11/96（11.4583%、fault0）のまま、公式提出・native teacher・trainingは別の明示許可が必要である。

## 2026-08-14 — Rule v0 coordinated package v11 (384で小差停止)

包括承認後、既存multisetを除外した新規2-card packageをRule v0＋root deck固定で評価した。runtime smokeは親＋候補2件の6局、weighted48は144局、common24は288局、全てDONE/fault0だった。候補3e338bf4…（[1123,1182]→[1121,3]）はweightedで親比+6.4336ptだったがcommon24で−3.1250ptとなり停止。候補8f75789b…（[1142,1182]→[1,3]）はweighted+4.6583pt、common24+5.2083ptだったためseed-disjoint 384へ進めたが、親46/384対候補50/384（+1.0417pt）に縮小した。全768局DONE/fault0、両seat/24 opponents/paired seed/GID gate PASS。768/longrun/promotion/submission/trainingは起動せずcandidate-only/STOPとした。

weighted rootは runs/final-sprint-autonomous/rule-v0-root-deck-package-v11-20260814/（manifest 444110489b77835c55bb2306888dab031a8137b5fbd729c7fc641be3f0c00a2c、summary 5cff2bca008ecd90e4357ed3a14e3af5abb5db3c0098a1a81185f0491c656e40）。common24 summaryは c156a52a2e1e3ea6d598a6c47185bca342e8676a3ec539664964667372ae417a、confirmation384 manifestは b209881fa5241b712830240ffb6740ada107e1b4e47938344391a670b912e2be、summaryは fe469826586265c151efc884e24a7dacbf6f9343d5bee18406f6675be32a6aa2。一次evidenceは docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v11-20260814.md。SubmissionEligibleBestKnownはRule v0＋root deck 11/96のまま。

## 2026-08-14 — Rule v0 coordinated package v12（384で反転）

新規2-card packageをruntime smoke→weighted48→common24→confirmation384で評価した。候補3a949927…（[1141,1227]→[1121,3]）はweighted +8.4805ptからcommon24 −2.0833ptへ反転。候補ea4a5c77…（[1142,1192]→[3,3]）はweighted +9.9898pt、common24 +3.1250ptだったが、384で親46勝対候補44勝（−0.5208pt）へ反転した。全実施局fault0、paired/seat/seed/GID gate PASS。v12はcandidate-only/hard-negativeとして停止し、768/longrun/promotion/submission/trainingは起動しない。一次evidenceは docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v12-20260814.md。SubmissionEligibleBestKnownはRule v0＋root deck 11/96のまま。

## 2026-08-14 — Rule v0 coordinated package v13（common24で停止）

新規2-card package v13をsmoke→workers12/recycle16 weighted48→common24で評価した。候補362fdf94…（[1141,1152]→[3,3]）はweighted +1.3570ptからcommon24 −5.2083ptへ反転。候補ee8f3a06…（[1102,1102]→[1121,3]）はweighted +6.4260ptからcommon24 −1.0417ptへ反転した。全432局DONE/fault0/draw0で、両候補candidate-only/hard-negative。384/768/longrun/promotion/submission/trainingは起動しない。一次evidenceは docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v13-20260814.md。SubmissionEligibleBestKnownはRule v0＋root deck 11/96のまま。

## FINAL PRIORITY CORRECTION 2026-08-14 — cg submission closure と policy-first screen

最新指示に従い、Rule v0 v14以降の機械的 deck mutation、Dusk/Hilda/Petrel の blind retry、V4 training/semantic/AWR/BC/R2D3/PSRO は停止し、self-owned cg policy＋root deck の提出互換 closure と policy-only surface を優先した。SubmissionEligibleBestKnown（remote contract 未確認を含まない verified lane）は Rule v0＋root deck 11/96（11.4583%、fault0）で不変。ResearchSubmissionCandidateBestKnown は cg P0 policy＋root deck だが、remote verifier が repo に無いため `submission_ready=false` のままである。

sample submission (`data/raw/sample_submission/sample_submission`) と engine README に対する local verifier `scripts/verify_root_cg_submission_candidate_v1.py` は archive shape、60枚 deck、`agent`、sample `cg` runtime 完全 parity、4局 clean-room smoke を確認した。report SHA `86b8371a97b7bd5c0d1a7fc46867f1852e2b8fe2d35a072ecbf8b2df0175e39a`。P0 archive SHA `278438be73b73d1be385810530dadf6d3679711cd218b78b9847c48d15ca1bb5`。新 variant は `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED` とし、提出・remote API・Champion変更は行わない。

fresh packaged cg P0 対 Rule control common24 は candidate 17/96 対 control 9/96（+8.3333pt、192/192 DONE/fault0、evaluator `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`）。

policy hypotheses（root deck immutable、public state only、private/teacher/native behavior 不使用）:

- `cg-lethal-target-v1`: visible active の `hp <= attack damage` の ATTACK に +12000。
- `cg-retreat-damage-v1`: active damage >=100 かつ powered bench がある RETREAT に +12000。

初期 variant package は Kaggle source loader が最後の callable（追加 `_score`）を entrypoint と誤選択して `AGENT_INVALID`/fault になった。原因を clean-room traceback で特定し、variant 末尾に explicit `agent()` wrapper、`obs.select`/untyped `Struct`/base score exception の fail-closed guard を追加した。旧 retry-safe roots は INVALID diagnostic として保持し、性能値へ算入しない。修正後 retry-safe4 は両候補とも 2局 smoke と verifier 4局 smoke を全て `DONE`, fault0, illegal0 で通過した。

修正後の同一 cg P0 control screen（broad24、同一 strata、workers12/recycle16）は次の通り。

| stage | candidate | control | delta | 判定 |
|---|---:|---:|---:|---|
| lethal common24, 192 games | 19W/0D/77L (19.7917%) | 15W/0D/81L (15.6250%) | +4.1667pt | 384 |
| retreat common24, 192 games | 16W/0D/80L (16.6667%) | 16W/0D/80L (16.6667%) | 0.0000pt | STOP |
| lethal confirmation384, 768 games | 64W/1D/319L (16.7969%) | 59W/0D/325L (15.3646%) | +1.4323pt | 768 |
| lethal confirmation768, 1536 games | 161W/0D/607L (20.9635%) | 106W/0D/662L (13.8021%) | +7.1615pt | candidate-only |

384/768 は seed-disjoint、workers12/recycle64、all DONE/fault0、両seat/24 opponent/paired seed/GID gate PASS。768 evaluator summary SHA `d613e70f04c2b476ed2a9582c3fbd91136f0993d7603dc55b8901e953363f537`、manifest-complete SHA `2ad0f3be495d325e2d3db35c63ed3757abf9253189d1acda0d56e69bad134974`。lethal source SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、archive SHA `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02`。evidence `docs/evidence/autonomous-cg-submission-closure-policy-screen-v1-20260814.md`（SHA `b1ec0406c80daf3898d6afd754318bd24597fbc386c1c192c326e6db8a63105e`）。

現在は active process なし。次は、remote contract の人手確認が無い限り提出を行わず、cg policy の新規 bounded public surface を必要最小限だけ screen する。retreat、初期 smoke fault variants、既評価 deck surface は再実行しない。ResourceGovernor は通常 workers12/recycle16、384/768 recycle64、GPU heavy 同時1を維持する。authority は training/teacher/promotion/submission/longrun 全 false、production `main.py`/Champion/root deck は不変、commit/push なし。

## CURRENT CANONICAL OVERRIDE — 2026-08-14 cg policy v2 / alternating runtime

この末尾を現在の正典として扱う。新規 `cg-attach-threshold-v1` は17/96対cg P0 control19/96（−2.0833pt）、`cg-overkill-conservation-v1` は12/96対21/96（−9.3750pt）。各192局DONE/fault0/draw0、workers12/recycle16で、common24以降へは進めない。両packageのlocal sample contract、runtime parity、clean-room 4局はPASSだが、remote Submit verifier/archive contractはUNKNOWN_NOT_BUNDLEDなので `submission_ready=false`。

cg packageを phase固定する bounded alternating runtimeを追加した。`POLICY_FIXED_SHORT` はpolicy SHA固定・deck SHAのみ変更、`DECK_FIXED_LONG` はdeck SHA固定・policy SHAのみ変更。96→384→768→1536、同一opponent/seat/repetition/seed strata、positive/fault0/両seat support/seat gap≤5ptのときのみ次stage。workers12、96 recycle16、384以上 recycle64、authority全false。実Hilda package＋attach policyのdry-run strict reloadはPASS。module `src/mage_ptcg/meta_specialist/cg_alternating_runtime_v1.py` SHA `03d0fe298745755478b2f837b52cdebf07988d4f8c43232fda295ec26815276b`、CLI `scripts/run_cg_alternating_runtime_v1.py` SHA `6d9b0ece162a0f5fa3eb8503842699d048985ee7482828a0f29bc07bbdb1213c`、evidence `docs/evidence/autonomous-cg-policy-screen-v2-and-alternating-runtime-20260814.md`。

ResearchSubmissionCandidateBestKnownはcg lethal＋root deck（768で+7.1615pt、candidate-only）。VerifiedSubmissionEligibleBestKnown/ChampionはRule v0＋root deck 11/96、fault0。active processなし、production/Champion/既存artifact不変、commit/push/Kaggle submissionなし。

## CURRENT CANONICAL OVERRIDE — 2026-08-14 cg alternating interaction execution

cg package closure は local contract PASS、remote Submit verifier は UNKNOWN_NOT_BUNDLED のまま。cg lethal policyを固定した実交互 runtime の deck phaseを、source policy/deck SHAを別々に束縛した新規 packageで実行した。

- `675 Lunatone → 676 Solrock`: candidate 14/96、control 22/96、delta −8.3333pt、全192 DONE/fault0、decision `NOT_PROMOTABLE`。
- `1192 Carmine → 1194 Colress's Tenacity`: candidate 14/96、control 14/96、delta 0.0000pt、全192 DONE/fault0、decision `NOT_PROMOTABLE`。

両方とも `POLICY_FIXED_SHORT` の同一 P0 policy・異なる deck SHA、workers12/recycle16、same opponent×seat×repetition×seed strataで実行した。positive gate未達のため `DECK_FIXED_LONG` は起動していない。Solrock execution summary SHA `07c68430a001e53a95d3093008359e98e5008592e36accdc3285fb95c141ad82`、Colress execution summary SHA `cfe90ca430fbdcb2347ba3c4acaa23d320e683a1ab26c7a7764c090e0d5952be`。一次evidence `docs/evidence/autonomous-cg-alternating-interaction-v1-20260814.md`。

Builderは研究用 `source_agent` 引数を追加し、policy/deckの混同を防いだ。builder SHA `e14dfd4da0d3181226d9942bb1812427c0fcebe08677d31c89d5a001842569bd`、test SHA `d1cca1e486630e4ef4537b90798f1a1395ca6c35592e4fd662a1b0026c396404`。production/Champion/root deck、remote submission、training、longrunは不変/未実行。ResearchSubmissionCandidateBestKnownはcg lethal＋root deck、VerifiedSubmissionEligibleBestKnown/ChampionはRule v0＋root deckのまま。
## FINAL CANONICAL OVERRIDE — 2026-08-14 cg closure / Festival Grounds alternating result

最新指示では cg submission closure を先に扱い、root deckは不変、policy-firstの bounded screen の後にのみ policy-fixed deck interaction を許可する。今回、`Gravity Mountain (1252) → Festival Grounds (1245)` を、cg P0 policy と lethal-target policyを別々に固定して評価した。候補 deck SHAは `d034887232321f6466b69c4b5c23580d05b4e169539582df60634be20f980f2e`、P0 policy SHAは `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`、lethal policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`。

local cg verifierはarchive shape、60枚、`agent`、sample `cg` parity、clean-room smokeをPASSしたが、repo標準 `scripts/verify_kaggle_submission.py` はcg 7-file packageに `kaggle-package-manifest.json` が無いため `BLOCKED`（`ValueError: kaggle package manifest must be a regular file`）。`probe_kaggle_contract.py --competition pokemon-tcg-ai-battle` は `AUTH_MISSING` で、remote archive/submission contractは未確認。したがって `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED`、`submission_ready=false`、Kaggle送信なし。

交互 runtimeは workers=12、96 recycle=16、384/768 recycle=64、同一broad24、両seat、paired opponent×seat×repetition×seed、全局DONE/fault0、authority=falseで実施した。

| stage | phase | candidate | control | delta | 判定 |
|---|---|---:|---:|---:|---|
| 96 | POLICY_FIXED_SHORT | 18/96 | 17/96 | +1.0417pt | NOT_PROMOTABLE（control seat gap 6.25%） |
| 384 | POLICY_FIXED_SHORT | 63/384 | 59/384 | +1.0417pt | POSITIVE_CONTINUE |
| 384 | DECK_FIXED_LONG | 82/384 | 64W-1D/384 | +4.5573pt | POSITIVE_CONTINUE |
| 768 | POLICY_FIXED_SHORT | 114/768 | 120/768 | −0.78125pt | NOT_PROMOTABLE / STOP |

384の一時的な正差は768で再現しなかったため、Festival deckをBestKnown、SubmissionEligible、Championへ昇格しない。1536/longrun/promotion/submissionは未実施。384のlethal policy interactionは研究信号に留め、外挿しない。blind retryはしない。

一次evidenceは `docs/evidence/autonomous-cg-alternating-festival-v1-20260814.md` SHA `098ec5148da7130752d1a24c66507b61d91e9fe0c8e69aaddc34be04acf48854`。iteration SHAは96 `cfc33c1aff05919a297de7b80e0daf64d2947130c39c4e2e2b020ee267907c09`、384 `7aa4672944a73e116625411346674ac2bd8437a43f188f35ba9b3e05049cffd1`、768 `4993dc8b1614da7f081cbe3c210ba3bc756b520283b9dcee17e073a0199ec0e0`。active processなし、production/Champion/root deck不変、training/teacher/commit/push/submissionなし。

## FINAL CANONICAL OVERRIDE — 2026-08-14 cg-lethal parent / effect decomposition / Crustle wall

最新 directive の正典を `cg-lethal-target-v1 + root deck` に固定した。P1 は common24 19/96 対 P0 15/96（+4.1667pt）、seed-disjoint 384 は 64W-1D 対 59W（+1.4323pt）、seed-disjoint 768 は 161W 対 106W（+7.1615pt）。全 block は DONE/fault0、両seat、24 opponent、paired seed/strata/GID gate PASS。P1 は research-only parent であり、Champion、SubmissionEligibleBestKnown、production default は不変。

P1 ledger の read-only decomposition では、768 の paired opponent delta 上位が medal_0001_77a53ffc +10/32、itsuki9180_lucario_jp +8/32、naoto714_kangaskhan +8/32、naoto714_slowking +7/32、naoto714_ursaluna +6/32。384 では medal +8/16、ferozahmedds_solution +4/16、kokinnwakashuu_lucario_search +4/16。seat は 768 で candidate seat0 +31/384、seat1 +24/384。既存 evaluator ledger は terminal WDL、seat/opponent/repetition/seed、cabt_turn/steps のみで、decisionごとの public state、lethal発火、実action変更、target HP/damage、energy/resource を保存しない。そのため causal lethal coverage と state decomposition は `UNMEASURED` と明示し、「lethal発火した勝ちゲームが勝因」とは解釈しない。詳細は `docs/evidence/autonomous-cg-lethal-effect-decomposition-and-crustle-v1-20260814.md`。

P1近傍の新規 bounded candidate `cg-crustle-wall-v1` は visible opponent active Crustle (345) に対し non-ex attack `{976..981}` を +24000、ex attack `{982,983}` を -24000、その他/不正状態は P0 exact fallback とした。package source SHA `90232bcbad524633bdde619d59beea8f9b0ad1897a5f0d417cade130073cd89f`、archive SHA `a4e2b10f19e13ac134f3d505c3c73b1ad6f607997bf5a24a8acf1394b40340b0`、clean-room smoke 2/2 DONE/fault0/illegal0。P0比較は 96 `22/96 vs 16/96`、384 `75/384 vs 60/384` (+3.9063pt)、768 `159/768 vs 133/768` (+3.3854pt) だったが、canonical P1比較384では候補 `68/384` 対 P1 `74W-1D/384`（−1.6927pt、DONE768/fault0）となった。したがって P1 は更新せず、Crustle candidate は candidate-only/STOP、1536/longrun/submission/trainingなし。同候補のP0比較だけを根拠に昇格しない。

submission closure は local cg verifier PASS のまま。fresh standard sidecar probe `runs/final-sprint-autonomous/cg-lethal-standard-closure-probe-v1-20260814/package/` で `kaggle-agent-package-v1` shape を追加したが、repo標準 verifier は inner cg schemaをRule v0 artifactとして認識せず `ArtifactValidationError: unsupported artifact schema version` で BLOCKED。sidecar SHA `50753e3c3dcf704eeb658a0c13af36eea0b5f4cf312c70cb06dace75fef19551`、inner manifest SHA `ca2d5d8c8d1bd6d30272514a47c94d1b8d0266d51bb862dc1001c3a2e925a875`。remote probeはAUTH_MISSING、`submission_ready=false`、Kaggle送信なし。cg runtimeをRule manifestに偽装しない。

次の優先は (1) P1 public decision/action telemetry を保存できる最小 read-only wrapper の確認、(2) telemetryでObserved failure/Hypothesis/Exact change/Risk/Kill conditionを固定した最大3件のP2生成、(3) P1をcontrolに common24→最良1件384→明確positive時768。Rule v0の新規機械的deck mutation、既評価attach/overkill/retreat、Dusk/Hilda/Petrel blind retry、Student/AWR/BC、native teacher、V4 semantic、R2D3/PSRO、candidateなしGPU longrunは行わない。workers12/recycle16、384/768 recycle64、GPU heavy同時1、authority全falseを維持する。

## FINAL CANONICAL OVERRIDE — 2026-08-14 cg package branch pushed

ユーザーの最新承認と `SUBMISSION LANE + RESEARCH LANE` directiveに従い、提出候補を新規 remote branchへ封印した。branchは `agents/ono-cg-lethal-v1`、baseは公式cg runtimeを追跡する共通baseline `235d2a874d023d2ab58eef16d36f74b4b8276beb`、commitは `1965b42b028f10960d08ccb4980be5b76946f98b`、`origin/agents/ono-cg-lethal-v1` へpush済みである。Kaggle actual submit、remote API、force-pushは行っていない。

Payload identityは self-owned `cg-lethal-target-v1` policy/main `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、official cg runtime `cg/libcg.so` `ffd89bf923525a3e6feb5e6201e96a866c0f456895499ed5c4a566303caae67c`、archive `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02`、inner manifest `0cc8fe72e8d1d65818651a77cde139be491e72224f963c4a206ffb268770a70d`、outer `kaggle-package-manifest.json` `3392240dde25f6c0fbb7494d1e0960c841be2fedec4b222adf0a76deac5d7cb5` で固定した。Rule v0をcg manifestへ偽装せず、authorityはtraining/teacher/promotion/submission/longrun全false、`submission_ready=false`/`CONTRACT_CONFIRMATION_REQUIRED`とする。

canonical標準verifierへcg inner-schema dispatchを追加し、fresh artifact directoryで outer `kaggle-agent-package-v1`、inner manifest、archive exact member order/metadata/hash、official runtime parity、Python isolation、CABT clean-room 4局を再検証した。結果は `PASS / READY_TO_SUBMIT`（clean-room 4/4 DONE、fault0、illegal0、JSON SHA `b3aa8c06a88a02e09e8ae79a729ed482e2b332181d3a25745d203ef3d5389711`）。cg-specific verifierも `PASS`（JSON SHA `e563f523575efb2e681267b4a8d070b051bae0be9f2f3fb7d5b80d420f4fd0`）。package testsは `63 passed, 10 skipped`、py_compile、branch `git diff --check`はPASS。提出branchのdiffには公式cg CRLFを含めず、runtime parityを保った。

research parentは引き続きP1 `cg-lethal-target-v1 + root deck`、P1 ledgerのcausal lethal coverageは`UNMEASURED`、Crustle wallはP1比較−1.6927ptでSTOP。Rule v0探索、blind retry、native teacher、training、V4/R2D3/PSRO、longrunは再開しない。新しいP2はtelemetryでObserved failure/Hypothesis/Exact change/Risk/Kill conditionを固定した後、P1 controlでsmoke→workers12/recycle16 weighted48→common24→384→768の順に扱う。通常評価workers=12/recycle=16、384/768 recycle=64、GPU heavy同時1を継続する。v8完了から確認までの停止は記録上約6時間（07:22頃→13:22頃）で、crash/hangではなく、negative/AGENT_INVALIDをfail-closed停止し新規候補未確定だったことが原因。現在はLane A closureが完了し、active processなし。

## FINAL CANONICAL OVERRIDE — 2026-08-14 cg P1 public telemetry / P2 bounded screen

Lane Aのcg提出候補branch closure後、Lane BをP1 `cg-lethal-target-v1 + root deck` に固定して再開した。P1の既存terminal ledgerにはdecision-level public state、lethal発火、実action変更、target HP/damage、resourceが無かったため、まず新規read-only telemetry wrapperを実装した。収集rootは `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1`。broad24、両seat、96/96 DONE、fault0、4,077 decision rowsと96 redacted deck-registration rows（deck_sizeのみ）、projection fault0、private/opaque key scan 0件である。これはP1の性能比較ではなく候補設計用の観測収集で、teacher/native behavior/training labelは保存していない。

telemetry source SHAはmodule `f00c7e88b33f87fc38739318ccd7affcc1295ca15da4f5291aac35a4f05c6bd6`、runner `16719260aeee164077756bfa02f639ddd3820c81935cb07ee4832bea46b891c2`、test `73aaeab378099d84a940b4ed20e16b213804f71462a4dd5b60d997aa590216c2`。manifest-complete SHA `5e389d495d480d5883213a09815ed24e6a92e174d7c4ea0800fca7f15a278e8c`、summary SHA `fabdd3fcc49432bf058f33bb2673904c7c194aebe480163558900a5171fc2f1f`。P1/evaluator identityは既存 `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` / `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` にbindした。

公開観測から最大3件のbounded P2をP1 controlでscreenした。各candidate/controlは96局、合計192局、workers=12/recycle=16、同一paired strata、両seat、fault0である。

| candidate | candidate | P1 control | delta | 判定 |
|---|---:|---:|---:|---|
| `cg-lethal-retreat-damage-v2` | 20W-0D-76L (20.8333%) | 19W-1D-76L (20.3125%) | +0.5208pt | 弱い正差、candidate-only/STOP |
| `cg-lethal-attach-threshold-v2` | 12W-0D-84L (12.5000%) | 18W-0D-78L (18.7500%) | −6.2500pt | STOP |
| `cg-lethal-overkill-conservation-v2` | 18W-0D-78L (18.7500%) | 21W-0D-75L (21.8750%) | −3.1250pt | STOP |

retreatの+0.5208ptは384昇格条件に届かず、attach/overkillは負差のためcommon24/384/768を起動しない。P1 parent、Champion、SubmissionEligibleBestKnown、production default、longrun、promotion、training、submissionは不変である。P2 module SHA `5841eb1cdb75ac64db652b20183cc7a67b85b706b92ae1c11b358d1127961051`、screen runner SHA `4f07ac2e8bc14142b0cb8a43ac4a1755a1d426b50d20234d9ca31dfd2075896e`、test SHA `090d077cbf5740a7f7e9cc6ee9b4743a9d48e18fb0d88bca8136cb275e347a1c`。

初回P2 runnerは評価中ではなくsummaryのseat集計再帰で停止した。修正後、既存DONE/fault0 ledgerを再利用してsummary/manifestだけを再封印した。局のblind retryや性能値の再計算は行っていない。修正経緯と各rootのsummary/manifest SHAは `docs/evidence/autonomous-cg-p1-telemetry-p2-screen-20260814.md`（SHA `246133cc0152d6ed51f31f497246c231cd3d977d818015741b7efcf31112024c`）に固定した。

現在active processなし。通常評価はworkers=12/recycle=16、384/768はrecycle=64、GPU heavy同時1を維持する。次の実行はP1 controlの新規bounded public hypothesisだけに限定し、positive/fault0/両seat/同一strataのweighted48のみcommon24へ進める。Rule v0機械的deck mutation、既評価surface blind retry、Student/AWR/BC、native teacher、V4/R2D3/PSRO、candidateなしlongrunは再開しない。Lane Aのremote contractは依然UNKNOWN/AUTH_MISSINGで、local verifier PASSでも `submission_ready=false`、Kaggle actual submitなし。
## FINAL CANONICAL OVERRIDE — 2026-08-14 cg P1 observed-failure neighborhood weighted48

P1 public telemetry の失敗だけから、追加の bounded policy candidate を最大3件生成した。観測は、legal lethal ATTACK が存在する192 stateのうち29 stateで非ATTACKを選択し、該当terminal gameの18件がloss、複数 lethal attack の42 stateのうち30 stateがloss gameに含まれた、というものに限定した。Rule v0、native teacher、private state、外部ラベル、blind retryは候補生成へ使っていない。

候補は `cg-lethal-lock-v1`（全 public lethal ATTACK に +30000）、`cg-lethal-setup-lock-v1`（ATTACH/EVOLVEとの同時提示時だけ lethal ATTACK に +30000）、`cg-lethal-resource-first-v1`（複数 lethal の最小 damage ATTACK に +16000）である。各候補は P1 `cg-lethal-target-v1` control と同じ12-opponent weighted subset、両seat、同一 paired strata/seed、48局ずつを実行した。runtime smokeは候補3件とも candidate/control DONE、weighted48も合計288局すべて DONE/fault0、workers=12/recycle=16で完了した。

| candidate | candidate W-D-L | P1 control W-D-L | delta | 判定 |
|---|---:|---:|---:|---|
| `cg-lethal-lock-v1` | 7-0-41 | 7-0-41 | +0.0000pt | STOP |
| `cg-lethal-setup-lock-v1` | 8-0-40 | 12-0-36 | −8.3333pt | STOP |
| `cg-lethal-resource-first-v1` | 11-0-37 | 11-0-37 | +0.0000pt | STOP |

全候補で candidate_delta_points は正でなく、common24/384/768、学習、teacher、promotion、submission、longrunは起動していない。P1 parent、Champion、SubmissionEligibleBestKnown、production defaultは不変である。一次evidenceは `docs/evidence/autonomous-cg-p1-lethal-neighborhood-weighted48-20260814.md`。module SHA `1da85ac835697e73bf8d45fc2b70097e7a1e8dcccae70e726eebeb53f1c287c6`、runner SHA `82119c74a0fd97244a1834100102c0782a2cbc115bb861a1aa9c193b092c6eb6`、test SHA `3aca226804aa8f83cf4b311a61dcfac974d73c6fb8473b41ea7cf12246a06d00`。

screen rootsは `runs/final-sprint-autonomous/cg-p1-lethal-neighborhood-{lock,setup,resource}-weighted48-20260814-v1/`。summary SHAは順に `68f7f5e564f680b73c4482c2ee045625d12142a1a42bfb587184c22924c6a174`、`507ccab819fbba3445ce8e3b5ccada27e6d20b0145f4cd9009b0308e036937e6`、`aaadbbd38214511c8414092345a77b4853532781eb3d7a78006f41dc2ef00692`。各manifest-complete SHAは `9c8f0c4fb23062bc490fde197f34cf72548d94b00dcf814566df79f85cbc368c`、`457bee3a6b48ef1ed686f097553813f528743b7bb9ad46f664c511e14352df2d`、`65af494c313d69ff8179e30aa7f4d4b2615f632c66b4c33dfb140d53f226bae6`。

この末尾が現在の最優先正典であり、過去のRule v0 deck/policy探索記録は履歴としてのみ保持する。次の実行は新しいP1公開観測失敗または新しいcg parent identityが確定した場合に限る。remote submission contractは未確認で、`submission_ready=false`、Kaggle actual submitなし、active processなし。

## FINAL UPDATE — 2026-08-14 META_TRAIN population-bound alternating runtime

既存102-row meta distributionから、`META_TRAIN`・evaluation-only・`local_eval_only`・`smoke_ok=true`の上位24 opponentだけをdeterministicに選ぶhash-bound scheduleと、既存cg alternating runtimeへ接続するresearch-only wrapperをTDDで追加した。`META_DEV`/`META_FINAL`、behavior collection、teacher labels、training、promotion、submissionはscheduleから除外した。meta manifest SHAは`e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`、pool SHAは`e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`、schedule file SHAは`d9b59a3ed3cb07f3845a5b32999ec86898d7fdec07b2e7bbb6a728948e25c7c3`。module SHAは`780e2cfaa7b5046b525ab23b8fc47161d7b2df9c8b78d6139d0948c23ce2b85f`、runner SHAは`212b05353242b640d03676edc049b101a8df7b791f1a9cc430163755673c6a14`、focused test SHAは`a78579c6308b10777f416414995e8aea6bcbb2502319e269f6000d815f4aa0ad`。focused 4 passed、py_compile、strict schedule/stage reload、diff-check PASS。

P1 `cg-lethal-target-v1`とP0 `root-cg-self-owned-v1`を同一root deckで`DECK_FIXED_LONG`比較した。fresh top24 populationで96局はP1 21-0-75 (21.875%)対P0 13-0-83 (13.5417%)、+8.3333pt、全192局DONE/fault0。ただしP0 control seat gapが6.25ptで既存5pt gate外。seed-disjoint 384ではP1 69-0-315 (17.9688%)対P0 66-0-318 (17.1875%)、+0.7813pt、全768局DONE/fault0、P1 seat gapは9.90ptへ拡大し`NOT_PROMOTABLE`。96/384 summary SHAはそれぞれ`8509aec24cbadc8cbd3ca9701562fe623b299438d4c2bb2539f03a8846af2d98`/`9511184b415242a7a45a49cf67b7bf5a0bb053ccd1a553a64becba6d189803f2`、population sidecar SHAは96 `be021eebb5e7be5b5ac6891ba6c10d3c3938b9e548777d3820546896d3188172`、384 `ceb5286db418083a55244b3ee4d5d2128f7fa549969a818c2de381733eaefe8a`。

分布接続・workers12/recycle16/64・paired strata・authority falseは成立したが、P1の96局優位はtop24 distributionの384局で再現しなかった。これはP1 policy自体の否定ではなく、population shiftとseat varianceの証拠としてcandidate-onlyに記録する。P1 package、Champion、root deck、production、submission branch、remote送信は不変。active processなし、training/teacher/longrun/commit/push/Kaggle submitなし。次の候補はP1 control・新しいObserved failure・runtime smokeを通してから同じworkers12→96→384 gateへ投入する。

## FINAL UPDATE — 2026-08-14 P1 observed-failure public-state neighborhood

P1 `cg-lethal-target-v1 + root deck`をcontrolに固定し、公開telemetryとP1 population-bound ledgerの失敗集中から新規candidateを作った。telemetryは96/96 DONE/fault0、4,077 decision rows、96 redacted deck-registration rows、private/opaque key scan 0件。候補入力は opponent active の公開 `id/hp/maxHp` と attack damageだけで、hand/prize/deck/private/teacher/native behaviorは参照していない。

| candidate | candidate | P1 control | delta | 判定 |
|---|---:|---:|---:|---|
| `cg-p1-heavy-active-attack-v1` | 14/96 | 19/96 | −5.2083pt | STOP |
| `cg-p1-very-heavy-active-attack-v1` | 18/96 | 25/96 | −7.2917pt | STOP |
| `cg-p1-heavy-active-conserve-v1` | 17/96 | 18/96 | −1.0417pt | STOP |
| `cg-p1-abomasnow-pressure-v1` | 15/96 | 22/96 | −7.2917pt | STOP |
| `cg-p1-ursaluna-pressure-v1` | 22/96 | 12/96 | **+10.4167pt** | 384へ確認 |

各96 screenはbroad24、両seat、同一paired strata、workers12/recycle16、全fault0。Ursaluna候補だけseed-disjoint 384へ進め、各arm384局（24 opponent×両seat×rep8、workers12/recycle64、全768 DONE/fault0）を実行した。candidateは72W-1D-311L=18.8802%、P1 controlは84W-0D-300L=21.8750%、delta **−2.9948pt**。GID768 unique、paired key/seed equal、各arm seat192/192、24 opponents。96局の正差は再現せず、candidate-only/STOP、768/longrun/training/teacher/promotion/submissionは起動しない。

一次evidenceは `docs/evidence/autonomous-cg-p1-observed-failure-screen-20260814.md`。実装SHAは module `e973d05c5f598e6467b0a5157fe35598fc5bf2ea620305c1a788cddcb1a78940`、96 runner `9ce437b326295e97d6e4b2e1b8632fa79b6518988a475e2e4a041f0b3588e7cf`、384 runner `4a9806031f415a6320df538ab115f16aebe88e5b0f9801d1282c94027be7ccd6`、module test `5318c1c054b08c756eaa29e8f922dbb6d3e053837a954218233f6b0f300fa25b`、confirmation test `c952371ba348a896160184ea056ad24025b04fe0c30af974bf15db4783a4b1bd`。384 summary `4aac32a8d3b4779869e34667c64aa47a3406ebcbdf2af2ce430914367a592c37`、manifest-complete `8cad2dbbba4ba6f03352b3ad4b3497390b933be76ae63c220902a2b6064454fa`。

P1 parent、Champion、SubmissionEligibleBestKnown、production/root deck、submission branchは不変。次のscreenは別の観測事実とbounded changeが揃った場合だけ再開し、今回5候補と同じseedのblind retryはしない。現在active processなし。

## FINAL CANONICAL OVERRIDE — 2026-08-14 cg P1 independent 768 / public failure candidate screen

P1 `cg-lethal-target-v1` と P0 `root-cg-self-owned-v1` を root deck固定、META_TRAIN top24、同一 opponent/seat/repetition/seed strataで独立 768 局ずつ評価した。P1 は `151/768`、P0 は `138/768`、差は `+1.6276pt`。全1,536局 `DONE`/fault0、P1 seat gap `2.8646pt`、authority は training/promotion/submission/longrun/teacher 全 false。これは P1 parent の再現性確認であり、Champion/SubmissionEligibleBestKnownの昇格ではない。独立 artifactは `runs/final-sprint-autonomous/cg-p1-independent-768-20260814-v1/`、summary SHA `cd0bcda15839bb89fa3df6a7f060a1cd30bca7c397fd49ab51cf587df947d9ed`、manifest-complete SHA `03d372f2affbad6f220c6f79b4547658caf398f2759ff35872a411728adc7569`。

既存 P1 public telemetry 4,077 decision rows と terminal WDLを strict public projectionで結合したところ、3,298 state buckets、support 6以上で competing state bucket 1、mixed-sign bucket 1だった。`ready_for_candidate_screen=false` として因果的 action labelを捏造しない。P0 telemetryも同一96 strata/base seed 40400000で追加し、P0 3,584 decision rows、P1 4,077 rows、両方 fault0。P0最初のstdin spawn partial rootは multiprocessing `<stdin>` 制約による無効診断として保持し、v2実ファイル wrapperの結果だけを採用した。

独立 768 の負け寄り public active-id clusterから、P1のlethal bonusを対象familyだけ抑制するP2を最大3件、P1 controlへ workers12/recycle16 weighted48で実行した。`cg-p1-public-suppress-dragapult-lethal-v1` は `15W-1D-80L` 対 `24W-0D-72L`（`-8.8542pt`）、`cg-p1-public-suppress-grimmsnarl-lethal-v1` は `17-0-79` 対 `17-0-79`（`0pt`）、`cg-p1-public-suppress-lucario-lethal-v1` は `17-0-79` 対 `17-0-79`（`0pt`）。計576局すべて DONE/fault0、両seat/paired strata gate PASS。positive候補が無いため common24/384/768、alternating promotion、training、teacher、longrunは起動しない。P1 parent、root deck、Champion、submission packageは不変。

新規 strict analyzer/module/runnerは `src/mage_ptcg/meta_specialist/cg_p1_public_hypothesis_v1.py`、`src/mage_ptcg/meta_specialist/cg_p1_public_failure_candidates_v1.py`、`scripts/run_cg_p0_public_telemetry_v1.py`、`scripts/run_cg_p1_public_failure_screen_v1.py`。focused tests `6 passed`、py_compile、evidence docs validator、diff-check PASS。一次evidenceは `docs/evidence/autonomous-cg-p1-independent768-public-failure-screen-20260814.md`。このpackを全履歴の正典として扱い、更新後の実体SHAは `docs/status/current_status.md` と `docs/status/handoff.md` の最新 integrity 行に記録する。

## FINAL CANONICAL OVERRIDE — 2026-08-14 cg P1/P0 paired public telemetry analyzer

P1 `cg-lethal-target-v1` と P0 `root-cg-self-owned-v1` の同一96 strata telemetryを、`(game_id, seat)`ごとの共通public-state prefixだけでstrictに比較した。P1 4,077 decision rows、P0 3,584 rowsから対応できたpublic-prefixは94行、operation differenceは0件、operation pairは0件だった。軌跡の最初のstate/action divergence後をcounterfactualとして扱わず、candidateは生成しない。`ready_for_candidate_screen=false`、candidate screen/common24/384/768/training/teacher/promotion/submission/longrunは未起動。

新規 `src/mage_ptcg/meta_specialist/cg_p1_paired_telemetry_v1.py` と `scripts/analyze_cg_p1_paired_telemetry_v1.py` は、公開allowlist外をfail-closedし、各armのterminal WDLだけをledgerから結合する。実解析artifactは `runs/final-sprint-autonomous/cg-p1-paired-public-telemetry-analysis-20260814-v1/analysis.json`、SHA `5dce92ebeed7011d06525bb8147302dd5f7c148037e2ea105c1a9b841047c8fd`。module/CLI/test SHAは `f012098a9899002c41a7d8456bcefc84a30fcc5ff12772f6b0780bdd78d6ae66` / `a5832313f4b4144209758204a4fe63671f8c494fa7cb0d522b9e2bda01a23de2` / `09c0745dc4e4fe98ad6e043faf56fa0e0f43f2b652b54ee16798f24facd0378e`。focused 3 passed、py_compile/docs validator/diff-check PASS。一次evidenceは `docs/evidence/autonomous-cg-p1-paired-public-telemetry-analysis-20260814.md`。

この結果は性能差ではなく情報不足の停止である。次に進む条件は、新しいpublic observation sourceから十分なoperation difference・support・mixed-signが得られ、P1 controlと同一strataのbounded candidateを定義できること。既存candidateと同一seedのblind retryは行わない。

## FINAL UPDATE — 2026-08-14 cg P1 deck/policy v2 screen

P1 `cg-lethal-target-v1` を固定した新規 deck interaction と、root deck固定の新規 policy variantを評価した。全評価は broad24、両seat、paired strata、workers=12、fault0、authority falseである。Dusk→Petrelは17/96対20/96（−3.125pt）、Dusk→Hildaは19/96対14/96（+5.208pt）、Dusk→Bloodmoonは14/96対16/96（−2.083pt）、Dusk→Explorerは18/96対23/96（−5.208pt）。Hildaのみ384へ進めたが、62/384対control 80/384（draw1を含む、−4.8177pt）へ反転したためSTOPした。

新規 policy variant `cg-p1-search-priority-v3` は、Mega Lucario未観測のsearch contextでDusk Ball/Premium Power/Poké Padに+12000、`cg-p1-gust-ko-v3` はvisible active HP 1–150時のBoss's Ordersを+12000とした。96局はsearch 17/96対12/96（+5.208pt）、gust 17/96対17/96（0pt）。searchのみ384へ進めたが、62/384対70/384（−2.0833pt）で反転した。module SHA `50a5bf036362358d515cfccce73be6bde3e2b99a1ea3058a003ca3bb6f5cf835`、test SHA `ccd9a998e3bab82e7362f6586b38accaddd778af430b6dbf44ed116c2e8931b0`。詳細と全summary SHAは `docs/evidence/autonomous-cg-p1-deck-and-policy-v2-screen-20260814.md` に固定した。

96局の単発positiveは昇格根拠にせず、同候補のblind retryはしない。P1 parent、Champion、SubmissionEligibleBestKnown、production default、longrun、promotion、training、submissionは不変。active processなし。次は新しいobserved failureまたは新しいpackage identityだけを smoke→96→positiveのみ384→再現時768へ投入する。

## FINAL CANONICAL OVERRIDE — 2026-08-16 公開kernel new4 / P1 CEM no-update

新しい未性能使用の公開source epochとして、Jazi Archaludon v28 staged、Kaiwalya payload B、Yaminh v3 staged、Jazi公開archive内のstandalone `main_rank1.py` snapshotを4 source poolへ封印した。Yaminh raw policyはisolated evaluatorで`__file__`が無いと`/kaggle_simulations/agent/deck.csv`を参照して2/2 faultになったためquarantineし、公開policy/deck bytesを変えずembedded exact deck fallbackだけを追加したstaged版を2/2 `DONE`・fault0で確認した。Jazi rank1 snapshotはP1対4局を全て`DONE`・fault0で確認した。公開scoreは性能証拠に使わず、全sourceを`local_eval_only`・research-onlyで扱う。

sealed rootは `runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/`、pool／fresh／meta／split SHAは `0c734ad4802b00605cda9a8d77215a5e6dfdbb94ed0f569254286be5cfc4574c`／`d7a28d33e7aa6e07dafd0cf4f76e2e894f441c3655b3093448a839f7ca954f07`／`2faf259f965011dea4fc17b047870f1c0d890cb162bcd4807ebe8d64dc426c73`／`d6783a320a631ccbf978bbba7cf04696248f8c03b3d69f7178aac5774cc1d81b`。splitはTRAIN=Jazi Archaludon＋Kaiwalya、DEV=Yaminh staged、FINAL=Jazi rank1 snapshot。全rowは`training_exposure=0`・`local_eval_only`で、CEM選定中FINALは未使用である。

P1固定CEMを2通り実行した。g02 `runs/cg-p1-cem-public-new4-20260816-g02/` はseed `202608194`、population／elite `12／3`、2世代、META_TRAIN_ALL、208局DONE/fault0だが、2 games/seatのscreen候補は全てseat-collapseでcenter保持。g03 `runs/cg-p1-cem-public-new4-20260816-g03/` はseed `202608195`、population／elite `8／2`、single TRAIN block（2 refs、6 games/opponent×seat）、独立re-evaluation 2回×2 games/opponent×seatでscreen432＋re-evaluation96＋DEV32の計560局DONE/fault0。g00 screen topは`5/24`対`4/24`（+4.1667pt）だったが、独立risk-aware／seat-safe gateを満たさず、g01もseat-collapseまたは独立負差／ゼロで、両世代とも`incumbent-center`保持となった。g01 P1 center DEVは`4/16`対`3/16`（+6.25pt）だが、CEM candidateの昇格根拠ではない。

g00 top candidate `cg-p1-cem-g00-c03-e4f3b46a61c5`の未使用DEV fresh validationは32局DONE/fault0、candidate/controlとも`1/16`、差0pt、同じseat-collapseだった。判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck file SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、`opponents/`、commit、pushは不変である。

`ono-`は公開kernel作者名ではなく、self-owned package branch `agents/ono-cg-lethal-v1`とlocal Git identity `bfe-lab-ono`／commit authorに由来するローカル識別子である。`cg-lethal` pair全体を公開sourceからのcopyとは扱わず、policy lineageとdeck byte/canonical一致を分離記録する。一次evidenceは `docs/evidence/cg-kaggle-public-new4-cem-20260816.md`。次は同じ4-source poolのblind retryをせず、runtime smoke候補とperformance holdoutを分離した新source epochを生成してから `cg_bestknown_loop_v1.py` へ接続する。
