# ChatGPT投入用 Final Sprint context pack

## 0. この資料の位置づけ

このファイルは、2026-08-12に最優先方針をStrong Asset comparison → BestKnown freeze → value/AWR fine-tuneへ切り替えてからの最新状態を、ChatGPTへ渡すための補足packである。3:56以降の過去履歴・V4/V5・residual・teacher監査の詳細は次のpackが正本であり、本ファイルはそれ以降のランキング、AWR実装、実戦評価を追記する。

- 基礎pack: `docs/status/chatgpt_context_pack_2026-08-12.md`
- 3:56以降pack: `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md`
- Strong Asset census: `docs/evidence/strong-asset-census-20260812.md` / `.json`
- eligibility: `docs/evidence/strong-asset-eligibility-audit-20260812.md`
- native ranking: `docs/evidence/strong-asset-native-ranking-fast96-20260812.md`, `strong-asset-top3-pooled1536-20260812.md`
- runtime quarantine: `docs/evidence/strong-asset-slow5-failfast-diagnostic-20260812.md`, `strong-asset-r7-diagnostic-20260812.md`
- AWR: `docs/evidence/strong-asset-public-state-awr-20260812.md`

本資料は推測を確定扱いしない。`EvaluationBestKnown`、`TrainingEligibleBestKnown`、`SubmissionEligibleBestKnown`、`BestKnownArchaludon`、`GlobalBestKnown`を分離する。local_eval_onlyは性能比較には使えるが、training labelやsubmissionへ自動転用しない。

## 1. 最優先の最新方針

最終目的はRule v0やWave6の改善そのものではなく、現在利用可能な強いdeck + agent pairを同じcommon arenaで比較し、BestKnownを確定すること。その後BestKnownをbehavior policyとして大量on-policy dataを集め、hard-label BCではなくactor-visible/public-state value + AWR/filtered BC、必要ならpublic-only search/Q、deck optimizationを交互に行い、元のBestKnownを明確に超える提出候補を作る。

固定した禁止事項:

- Lucifer hard-label/outcome-weighted BCの同型sweepを追加しない。
- AWR今回armの同じtarget・temperature・max weight・epochの反復をしない。
- 24/48局だけで昇格・棄却を確定しない。96→384→768→1536を優先する。
- native teacher pairを測らずstudentだけを比較しない。
- commit、push、remote branch、Champion変更、Kaggle submissionを行わない。

## 2. Worktreeと実装境界

作業ディレクトリは `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle`。現在のworktreeには過去の研究用未追跡差分が大量にあるため、今回触った新規責務以外を整理・削除していない。

今回の新規実装は以下である。

- `scripts/run_asset_pair_ranking_v1.py`: native deck+policy pair inventory、self-play除外、common reference game作成、native factory接続。
- `scripts/parallel_cabt_evaluator_v1.py`: bounded spawn evaluator、max in-flight、worker recycle、atomic ledger、fault denominator、progress summary。
- `scripts/run_public_state_awr_v1.py`: sealed teacher outcomeからcross-fitted actor-visible advantage weightを作るV4 trainer runner。
- `scripts/measure_v4_checkpoint_broad_arena_v1.py`: 既存V4 evaluatorを24-opponent broad configへ接続するresearch-only wrapper。R7除外、config SHA、research-only flagsを出力する。
- `tests/meta_specialist/test_run_public_state_awr_v1.py`: 4 tests。
- `tests/test_measure_v4_checkpoint_broad_arena_v1.py`: 2 tests。

既存production `main.py`、V4 model/policy、通常V4 trainer、actor_pool、Champion、package、Kaggle提出経路は変更していない。

## 3. Strong Asset Census / eligibility

pool manifest SHAは `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`。102 assetsはpublic71/internal31、policy unique58、declared deck unique77、raw deck unique79、smoke true101/false1である。

現行effective分類:

| 区分 | 現状 |
|---|---|
| Evaluation登録 | 102件。ただしR7はsmoke false quarantine、slow5はruntime診断へ分離 |
| Training permission | 現行sourceと一致するsealed snapshot再利用はtomato/Luciferの2件。過去decision上のqualified setは6件と別管理 |
| Submission native pool | 0件。pool pairはlocal_eval_onlyでas-is package不可 |
| package fallback | pool外Rule v0 root deck archive。これは性能BestKnownではなく提出anchor |

R7 `public_archaludon_cinderace_r7`はfixed-sixで62/96という外部診断値があるが、current poolはsmoke false/local_eval_onlyである。従って性能上限の参考にはするが、TrainingEligible/SubmissionEligible/Promotionへ昇格させない。

## 4. Native common-arena ranking

### 4.1 fast96 screen

対象はsmoke trueの96 assets。各assetは24 reference opponents × 2 seats × 2 games = 96局。5 slow IDs（kinoshita_pimc_search、ozawa_metal_psychic_search、water_box_search、waterbox_search_v3、tientrum_alakazam_search）とR7は別扱い。

artifact root `runs/meta-specialist-asset-ranking-primary-fast96-20260812`:

- ranking SHA `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29`
- ledger SHA `dc68512a72d57b804589692b2603f9b7fc872a61fc336d7ab93623641e57704a`
- summary SHA `23f532e7b7ad5af08432ff6a37baeaf89fe1a0941fc336d7ab93623641e57704a2`
- manifest SHA `161f18d0367d456b5a7cf1680d1d1a1ec619e9bbb82f984c0d1e6940c1269147`
- requested9216、DONE9207、fault9。全faultは`medal_0019_df6f7443` STEP_LIMITで、asset-level quarantine。

point estimateはplamen76/96=79.17%、tomato73/96=76.04%、Lucifer70/96=72.92%。しかしこれはprimary screenであり、後続384で順位が逆転した。

### 4.2 top3 384 block群

共通条件はtop3 native pair、24 opponents、両seat、8 games/opponent/seat = 384局/asset。全block fault0。

| block | tomato | Lucifer | plamen | ranking SHA |
|---:|---:|---:|---:|---|
| 1 | 279/384=72.656% | 266=69.271% | 275=71.615% | `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b` |
| 2 | 273=71.094% | 282=73.438% | 272=70.833% | `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e` |
| 3 | 280=72.917% | 273=71.094% | 278=72.396% | `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7` |
| 4 | 275=71.615% | 282=73.438% | 277=72.135% | `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5` |

block1〜3はdefault block idが同じでgame_idが重複するため、artifact/base seed単位で集計する。block4は`asset-ranking-top3-block4`で別ID。

### 4.3 pooled1536 result

4 blocksをartifact単位で正しく集計した結果は次の通り。

| native pair | W/L/F | score | seat0 wins/768 | seat1 wins/768 |
|---|---:|---:|---:|---:|
| `tomatomato_archaludon` | 1107/429/0 | 72.0703% | 561 | 546 |
| `lucifer19_battlecore` | 1103/433/0 | 71.8099% | 554 | 549 |
| `plamen06_steel` | 1102/434/0 | 71.7448% | 567 | 535 |

tomatoのleadはLucifer+4勝、plamen+5勝のみ。block首位も変動するため、tomatoを`provisional EvaluationBestKnown`とし、top3をnear-tie control cohortとして保持する。pooled1536 evidence SHAは `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`。

### 4.4 未評価slow / R7診断

slow5はkinoshitaの検索予算・node capにより1局が長時間化したため、5 asset×48局を1局15秒上限でfail-fast診断した。240/240 fault、DONE=0で、性能順位は得られなかった。初回実行で親watchdogがtimeout row後の次game投入を落とす`KeyError`を再現し、回帰テストを追加して修正した。修正版関連suiteは18 passed。artifactは`runs/meta-specialist-asset-ranking-slow5-failfast1-20260812`、asset ranking SHA `eb14411fbc0ee71776498ec9a26341ac5692a16bf9732ac490e76cfd6864c201`、ledger SHA `25c9e00cb78ae9a26c15535ee59c6492cc75ad6a70eafc525c6d26fcd45e3913`、summary SHA `acf56e6466215bd3f3384c4a61c0bcc88ddeeb04f5579c82ea7aced3e28c83c1`、manifest SHA `ac784e94d630ced44c2adf39214f4723c6eaa3df36cb264a8ea7b739cca83669`である。R7はsmoke falseのため通常rankingから分離し、評価のみ96局を完走した（68W/28L、70.8333%、fault0）。R7 artifactは`runs/meta-specialist-asset-ranking-r7-diagnostic-20260812`、asset ranking SHA `7787f191ffdfd559d26a29b8365974c7e384a21950e5d8068aef2bd1137785ac`、ledger SHA `62b04763bd95c6f35b3b26799f3ad974414a98536b03eecb423f962c98c08b25`、summary SHA `e40b02ce05108fed7170f95cc4ed8b6d16452e21ede7a4a82c09b92b4cb08209`、manifest SHA `78f1883704741054176aa3de7f2c11b45b031ee7c93190f01ffff89cc5e896cf`である。R7はtomato/Lucifer/plamenの1536局と局数が揃わず、smoke false/local_eval_onlyのためBestKnown・training・promotion・submissionには入れない。このため102全件を含む`GlobalBestKnown`は未確定である。slow5はhard native killが未実装のため通常queueへ戻さず、比較不能quarantineとして別管理する。

## 5. BestKnown classification

- `EvaluationBestKnown`: 暫定 `tomatomato_archaludon` native 72.0703%/1536。Global確定ではない。
- `TrainingEligibleBestKnown`: 現行sealed snapshot・permission・common rankingの交点ではtomatoが第一候補。Luciferが第二、plamenはnative評価上位だがsealed training snapshot未確認のためtraining起点へ自動利用しない。
- `SubmissionEligibleBestKnown`: native poolは0件。現時点で提出可能なanchorはpool外Rule v0 packageのみ。これは性能比較対象と提出候補を混同しない。
- `BestKnownArchaludon`: tomato provisional。Lucifer/plamenをtie cohortとして保持。
- `GlobalBestKnown`: slow5未解決、R7は96局診断のみで局数非整合・smoke falseのため未確定。

「提出候補上位2 pair」は現時点ではsubmit-readyではない。条件付き候補はtomato nativeとLucifer nativeだが、両者ともlocal_eval_only/package closure blockerがある。実際にpackageできるfallbackはRule v0 root deck archiveのみで、native BestKnownと同じ意味ではない。

## 6. AWR runner / target semantics

### 6.1 実装上の修正点

初回実行ではV4 trainerのphysical record契約に反してprefixごとに異なるquality weightを渡し、`record decoder rows must share one quality_weight`でfail-fastした。これを修正し、同一`(episode_id, record_id)`内の全prefixに平均advantage由来の同一weightを付与した。修正後のfocused testは4 passed。

targetはbehavior policy probabilityやimportance ratioを捏造していない。screen/teacher trajectoryのepisode outcomeとactor-visible bucketから、fold外・episode外のbaselineを作り、`G - baseline`をbounded weightへ変換している。従ってこれは厳密なoff-policy AWR ratioではなく、cross-fitted outcome advantage weighted imitationである。

### 6.2 Tomato all-row AWR training

root `runs/meta-specialist-v4-public-state-awr-tomatomato-20260812`、report SHA `f3e0cca347e93163e765a4567b5f7f055aa5c774d17e22012d9a205fb08e6c8b`。

| seed | updates | validation NLL | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---|---|
| 0 | 63 | 0.593270→0.517162 | `3231598a6ed729545243cf356f7a27e63fe3fb8ab6cd10baf17335f1c646fa3f` | `cdd38fe29582be14655ab4ac534d532b4809c243294f10e41b4d5e3625db8c5d` |
| 1 | 63 | 0.585023→0.520517 | `5c8d6c1a50f18aff5aa4122cfdacbcbf4adc46e2f962a168c54516bacfab3863` | `461da57084e86b2a6743e6eeac679842a6b19568a8f32f7671d45e01e1b32103` |

target manifest SHA `1efb384d54a2a8ceb8ae40ef9e3530384116068d55dcc7d0186936b025879cef`、rows4968、records4317、target episodes81、test15除外。

### 6.3 Tomato filtered AWR

root `runs/meta-specialist-v4-public-state-filtered-tomatomato-20260812`、report SHA `73624cb2f765a61e18000c775f8edecc1ad4c715d8e4d59ea7b6695df5a4e7ed`。

| seed | updates | validation NLL | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---|---|
| 0 | 37 | 0.594713→0.546161 | `33b5394d60c4609f01ad2f4bdc5a4bd4541168e7ab5dc2c8673fdcb1a7ce4e1e` | `eb33d88bb285f5a6fd5d30ce435a9ec61e46aeb1d0a0fae9a3f6f8851f9481d2` |
| 1 | 37 | 0.584256→0.540745 | `70e7e44e99d1c2491dd3195e9a240607c9ffd452049a09e37da72a590b1fb6f4` | `886ff7f7d2eca2674cb441c8490286a21b0ca37991a577a7548f5ae070ebc5a4` |

同一温度・max weight・epochで、filteredはvalidation NLLも改善したが実戦24局がseed0 6/24、seed1 10/24へ悪化した。broadへ拡張しない。

## 7. AWR arena results

### 7.1 24局

同じtomato deck、held-out six、両seat、fault0。

| arm | seed0 | seed1 |
|---|---:|---:|
| Wave6 | 16/24 | 9/24 |
| all-row AWR | 14/24 | 15/24 |
| filtered AWR | 6/24 | 10/24 |

### 7.2 broad96

24 opponents × 2 seats × 2 games、base seed 9300000、fault0。

| arm | seed0 | seed1 | pooled |
|---|---:|---:|---:|
| Wave6 | 50/96 | 61/96 | 111/192 |
| all-row AWR | 54/96 | 54/96 | 108/192 |

artifact SHA:

- AWR s0 `3ce6b99bcf4a33c5e820d17fbbc3e99f6355144e8f445a6d7acc1067b9cb9827`
- AWR s1 `51d13b853fbb7229ee406135534599b2263ed42f40c0c3721035599f6939f165`
- Wave6 s0 `1cea260f0e74d874cc9dcd4618aaa86504da13bf286c9a0d97ec815e0564986d`
- Wave6 s1 `b7cc6b13d50ee44a0e69a3c33007aba344da09af531a64bb92b60eb9527659f`

### 7.3 broad384

24 opponents × 2 seats × 8 games、base seed 9400000、fault0。

| arm | seed0 | seed1 | pooled |
|---|---:|---:|---:|
| all-row AWR | 222/384 | 216/384 | 438/768 |
| Wave6 | 199/384 | 237/384 | 436/768 |

AWRはaggregateでは+2勝に見えるが、seed0 +23勝、seed1 -21勝で完全に反転している。native tomato 72.07%/1536に比べると、AWR s0 57.81%、s1 56.25%は大幅に下である。したがってAWRをBestKnown超え候補、提出候補、longrun候補にしない。

artifact SHA:

- AWR s0 `61c1c2391ef16e1024184d1485e9485603770301d8cdd55aaff31ddd66f7e259`
- AWR s1 `ba959739ce71ab4aee0b5fc9a1153e5db69fd309a128e94fbf47d0ec414cee9d`
- Wave6 s0: `runs/meta-specialist-strength/wave6-tomato-s0-broad384-20260812.json`, SHA `c25635b9a0fe55c3617f1734137af245bd814e837bbd7895c1c4c165d4e40382`
- Wave6 s1: `runs/meta-specialist-strength/wave6-tomato-s1-broad384-20260812.json`, SHA `60d000358abef9e517ebd3bd4a5acf946627443107865f14cf05bacd1c12c2a1`
- Wave6 broad384 control values: seed0 199/384=51.8229%、seed1 237/384=61.7188%、pooled436/768=56.7708%、fault0。

## 8. 重要な解釈

1. offline NLLはAWR全行・filteredの両方で下がったが、勝率は下がった。NLL改善を性能改善と解釈してはならない。
2. native tomato/Lucifer/plamenの上位差は1536局でも4〜5勝であり、BestKnownの点推定はnear-tieである。
3. native pairをstudentへ蒸留する場合、teacher pairそのものの強さ・deck identity・permission・package可否を別々に残す必要がある。
4. CABTはengine seed setterなし。seedを引数に渡してもgame-level paired評価ではなく、seed/blockをstratification metadataとして扱う。
5. slow policyをcommon queueへ混ぜると、性能ではなくscheduler/runtimeが支配する。slow5は別診断へ隔離する。

## 9. Longrun / submission decision

現時点のGO/NO-GO:

| 対象 | 判定 | 理由 |
|---|---|---|
| tomato native EvaluationBestKnown | provisional GO as control | 1536局点推定首位だがGlobal未確定 |
| Lucifer/plamen native control | retain | tomatoとnear-tie、次の比較control |
| all-row AWR | NO-GO | native BestKnown未達、seed反転 |
| filtered AWR | NO-GO | 24局で大幅悪化、broad不要 |
| Lucifer hard BC | NO-GO | 384局両seedでWave6未満 |
| residual/coarse residual | NO-GO | seed/seat反転・coverage/target問題 |
| longrun | NO-GO | BestKnown超え候補なし |
| Champion変更 | 禁止 | ユーザー承認なし、promotion gate未通過 |
| Kaggle submission | 未実施 | native pool local_eval_only、package anchorのみ |

## 10. 残課題と再開順

1. Wave6 broad384 controlは完了し、AWR evidenceへSHAを固定済みである。
2. slow5のfail-fast診断は完了済み（240/240 fault、DONE=0）。次に比較へ戻す場合はhard native killを実装し、同一protocolで個別再診断する。R7の96局診断は完了済みだが、smoke false/local_eval_onlyの扱いと局数非整合を解消するまではBestKnownへ昇格しない。
3. `EvaluationBestKnown`と`GlobalBestKnown`を混同しない新しいBestKnown manifestを作る。
4. top3 native cohortから、teacher/native pair自体を含む大量on-policy dataを再収集する。
5. 次の学習目的は、現在のglobal bucket outcome weightをそのまま反復せず、actor-visible `V_hat`またはaction-conditioned advantageをcross-fitして閉じる。
6. strict public-onlyを名乗るならown hand/card bagsを除外した別feature schemaとSHAを作る。
7. positive armだけ96→384→768→1536へ進め、各seed/seat/fault gateとnative BestKnown差を同時に記録する。

最後に、現在「完了」と呼べるのは、native top3比較1536、AWR実装、AWR bounded実戦評価、各artifactのSHA固定までである。全102件のGlobalBestKnown、submission-ready native pair、BestKnown超えfine-tune、deck optimization、longrun、Kaggle提出は未完了である。

## 11. 現時点の進捗率と停止条件

全体は約65%と評価する。これは「コードを書いた割合」ではなく、最終目的に対する到達度である。

| workstream | 進捗 | 根拠 |
|---|---:|---|
| Census / permission / pair identity | 100% | 102 asset inventory、training/submission/evaluation境界、native pair identityを固定 |
| 共通arena ranking | 85% | fast96、top3各1536、R7診断完了。slow5は240/240 faultで比較不能quarantine |
| BestKnown classification | 75% | EvaluationBestKnown tomato provisional、TrainingEligible intersection、SubmissionEligible native=0を確定。Globalは未確定 |
| value/AWR/fine-tune | 45% | runner、target、all-row/filtered、broad384を実行。native BestKnown超え armなし |
| deck optimization / public-only search-Q | 0% | 未着手。AWR候補がBestKnown未達のため開始条件未達 |
| package / submission candidate | 20% | Rule v0 archive anchorのみ。native poolはas-is package不可 |
| longrun / Champion / Kaggle submit | 0% | 明示的にNO-GO、実行なし |

現時点の最重要未完了は、(1) slow5をhard native kill付きで再評価するか比較不能として凍結する最終判断、(2) tomato/Lucifer/plamen near-tieから一つを起点にしたBestKnown超えtargetの設計、(3) submission可能なpair/package closureの確保である。AWR・Lucifer hard BC・residualの同型追加sweepは停止する。
