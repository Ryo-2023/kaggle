---
title: Strong Asset public-state AWR bounded pilot (2026-08-12)
status: research-only
---

# 結論

`tomatomato_archaludon` のsealed teacher trajectoryから、hard BCを置き換える研究専用のcross-fitted actor-visible outcome advantage weightを作り、V4 warm-startへ1 epoch接続した。offline validation NLLは両seedで低下したが、broad common arenaではAWR全行armがBestKnown native pairを大きく下回り、Wave6との比較もseed反転した。従って、このbounded AWR armは`NOT_PROMOTABLE`であり、同じtarget・温度・weight・epochの追加sweep、longrun、submissionへ進めない。

この結果が否定するのは「tomato trajectoryをこの実装で1 epoch AWRする」方法であり、actor-visible value/AWRという設計空間全体ではない。ただし次回再開には、teacher/native BestKnownを超えることを目的に、target semantics・cross-fitting・pair cohort・評価budgetを新しい実験として再登録する必要がある。

## 1. 固定した入力と権限境界

- subject pair: `tomatomato_archaludon`
- subject deck raw SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- teacher snapshot: `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96`
- snapshot/collectionは現行training-local permissionに紐付くsealed artifactで、test partitionを学習targetへ混ぜない。
- Wave6 seed0 checkpoint file SHA: `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
- Wave6 seed0 tensor SHA: `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`
- Wave6 seed1 checkpoint file SHA: `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6`
- Wave6 seed1 tensor SHA: `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a`
- held-out broad reference config: `configs/meta_specialist/performance_first_broad_pool_v1.json`
- broad reference config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- broad wrapper SHA: `79b4f69b220402129c303a8c8c9bd8d4b00beab85ad33afa983c3b03363f953c`
- held-out protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`

全artifactは`research_only=true`、`promotion_authority=false`、`longrun_allowed=false`、`performance_evidence=false`として保存した。production V4 model/policy、actor_pool、`main.py`、Champion、Kaggle packageは変更していない。

## 2. Target construction

既存のlegacy AWR helperは重み計算だけでV4のmulti-select/STOP/recurrent record-groupへ接続されていなかったため、新規`run_public_state_awr_v1.py`を追加した。対象は厳密な公開情報ではなく、現行V4 actor-visible stateである。既存featureはstate scalarsとcard bagsを含むため、将来厳密public-onlyへ改名・再設計する場合は別schemaが必要である。

処理は以下である。

1. sealed teacher recordをtrain/validation/testへ再分割し、test episodeをtarget tableから除外する。
2. outcome returnを同一structural bucketのfold外episodeだけで平均し、対象episode自身と同じfoldをbaselineから除外する。
3. `advantage = return - cross_fitted_baseline`を温度0.25で指数化し、最大weight 4.0をbounded normalizeする。
4. V4 trainerの制約上、同一physical record内の全decoder prefixへ同一quality weightを与える。prefix単位で異なるweightを渡す実装はrejectされるため、`mean_prefix_advantage_per_episode_record`へ集約した。
5. `filtered` variantは非正advantageのrecordをsupervisionから除外する。context-only rowのGRU forward/hidden transitionは残し、loss denominatorからのみ除外する。

実装・テスト:

- `scripts/run_public_state_awr_v1.py`
- `tests/meta_specialist/test_run_public_state_awr_v1.py`
- focused test: 4 passed
- テスト内容: fold/episode leakageなし、weight bounded、filtered mask明示、record prefix同一weight、test partition除外

Tomato target manifestの主要値:

- rows: 4,968
- physical records: 4,317
- target episodes: 81（sealed test 15 episodeは除外）
- nonzero supervision rows: 4,968（全行arm）
- filtered variantの実行時はpositive 2,897 / zero 2,071、除外sequence 31
- all-row target manifest SHA: `1efb384d54a2a8ceb8ae40ef9e3530384116068d55dcc7d0186936b025879cef`
- filtered target manifest SHA: `0d6e31c9311683b09b77e8c8cf55bc1d2ce8c413ac24a9d32195568d018ea0dd`

## 3. Training artifacts

固定条件は1 epoch、learning rate `1e-4`、TBPTT8、burn-in1、同一Wave6 seed checkpointである。

### 3.1 全行AWR

artifact root: `runs/meta-specialist-v4-public-state-awr-tomatomato-20260812`

| seed | updates | validation NLL before → after | delta | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---:|---|---|
| 0 | 63 | 0.593270 → 0.517162 | -0.076108 | `3231598a6ed729545243cf356f7a27e63fe3fb8ab6cd10baf17335f1c646fa3f` | `cdd38fe29582be14655ab4ac534d532b4809c243294f10e41b4d5e3625db8c5d` |
| 1 | 63 | 0.585023 → 0.520517 | -0.064506 | `5c8d6c1a50f18aff5aa4122cfdacbcbf4adc46e2f962a168c54516bacfab3863` | `461da57084e86b2a6743e6eeac679842a6b19568a8f32f7671d45e01e1b32103` |

report SHA: `f3e0cca347e93163e765a4567b5f7f055aa5c774d17e22012d9a205fb08e6c8b`。

### 3.2 filtered AWR

artifact root: `runs/meta-specialist-v4-public-state-filtered-tomatomato-20260812`

| seed | updates | validation NLL before → after | delta | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---:|---|---|
| 0 | 37 | 0.594713 → 0.546161 | -0.048552 | `33b5394d60c4609f01ad2f4bdc5a4bd4541168e7ab5dc2c8673fdcb1a7ce4e1e` | `eb33d88bb285f5a6fd5d30ce435a9ec61e46aeb1d0a0fae9a3f6f8851f9481d2` |
| 1 | 37 | 0.584256 → 0.540745 | -0.043512 | `70e7e44e99d1c2491dd3195e9a240607c9ffd452049a09e37da72a590b1fb6f4` | `886ff7f7d2eca2674cb441c8490286a21b0ca37991a577a7548f5ae070ebc5a4` |

report SHA: `73624cb2f765a61e18000c775f8edecc1ad4c715d8e4d59ea7b6695df5a4e7ed`。

NLL低下は両variantで観測されたが、これはteacher-like offline objectiveへの適合であり、実戦勝率の改善を意味しない。

## 4. 実戦評価

### 4.1 24局 sanity

同じheld-out six、両seat、2 games/opponent/seat、fault0である。

| arm | seed0 | seed1 |
|---|---:|---:|
| Wave6 same tomato deck | 16/24 (66.67%) | 9/24 (37.50%) |
| all-row AWR | 14/24 (58.33%) | 15/24 (62.50%) |
| filtered AWR | 6/24 (25.00%) | 10/24 (41.67%) |

この24局は大きなCIを持つため、filteredを不採用候補とする早期警告には使うが、BestKnown順位の確定には使わない。

### 4.2 broad 96局

broad poolは24 opponents × 2 seats × 2 games = 96局/seed。all-row AWRとWave6を同じsubject deck、同じbase seed range `9300000`で実行した。CABT engineにseed setterがないため、同一seed番号を使ってもgame-level pairedではなく独立層化比較である。

| arm | seed0 | seed1 | aggregate |
|---|---:|---:|---:|
| Wave6 | 50/96 (52.08%) | 61/96 (63.54%) | 111/192 (57.81%) |
| all-row AWR | 54/96 (56.25%) | 54/96 (56.25%) | 108/192 (56.25%) |
| filtered AWR | 24局のみ | 24局のみ | broad未実施（停止） |

all-row AWRはseed0だけ+4勝、seed1で-7勝となり、aggregateではWave6より-3勝。seed/seat安定性のpromotion gateを満たさない。

一次artifact:

- AWR s0: `runs/meta-specialist-strength/awr-tomato-s0-broad96-20260812.json`, SHA `3ce6b99bcf4a33c5e820d17fbbc3e99f6355144e8f445a6d7acc1067b9cb9827`
- AWR s1: `runs/meta-specialist-strength/awr-tomato-s1-broad96-20260812.json`, SHA `51d13b853fbb7229ee406135534599b2263ed42f40c0c3721035599f6939f165`
- Wave6 s0: `runs/meta-specialist-strength/wave6-tomato-s0-broad96-20260812.json`, SHA `1cea260f0e74d874cc9dcd4618aaa86504da13bf286c9a0d97ec815e0564986d`
- Wave6 s1: `runs/meta-specialist-strength/wave6-tomato-s1-broad96-20260812.json`, SHA `b7cc6b13d50ee44a0e69a3c33007aba344da09af531a64bb92b60eb9527659f`

### 4.3 broad 384局 confirmation

broad pool、両seat、8 games/opponent/seat = 384局/seed。all rows AWRを事前固定した2 seedだけ拡張し、filteredの追加評価は行っていない。

| arm | seed0 | seed1 | aggregate |
|---|---:|---:|---:|
| all-row AWR | 222/384 (57.8125%) | 216/384 (56.25%) | 438/768 (57.0313%) |
| Wave6 control | 199/384 (51.8229%) | 237/384 (61.7188%) | 436/768 (56.7708%) |

全8評価ともfault0。AWRはaggregate +2勝だが、seed0 +23勝、seed1 -21勝の反転であり、両seed・両seat非悪化ではない。native tomatoの1536局72.0703%には大幅に届かないため、AWRをBestKnown超え候補とは扱わない。

一次artifact:

- AWR s0: `runs/meta-specialist-strength/awr-tomato-s0-broad384-20260812.json`, SHA `61c1c2391ef16e1024184d1485e9485603770301d8cdd55aaff31ddd66f7e259`
- AWR s1: `runs/meta-specialist-strength/awr-tomato-s1-broad384-20260812.json`, SHA `ba959739ce71ab4aee0b5fc9a1153e5db69fd309a128e94fbf47d0ec414cee9d`
- Wave6 s0: `runs/meta-specialist-strength/wave6-tomato-s0-broad384-20260812.json`, SHA `c25635b9a0fe55c3617f1734137af245bd814e837bbd7895c1c4c165d4e40382`
- Wave6 s1: `runs/meta-specialist-strength/wave6-tomato-s1-broad384-20260812.json`, SHA `60d000358abef9e517ebd3bd4a5acf946627443107865f14cf05bacd1c12c2a1`

Wave6 broad384 control values are seed0 `199/384=51.8229%`, seed1 `237/384=61.7188%`, pooled `436/768=56.7708%`. All rows are fault0.

## 5. 判定と次の方針

- `filtered AWR`: `NOT_PROMOTABLE`; broad arena未実施のまま停止。
- `all-row AWR`: `NOT_PROMOTABLE`; NLLは改善したが、native BestKnownに届かず、Wave6比較もseed反転。
- Lucifer hard/outcome BC: 384局で両seedWave6未満の既存判定を維持。
- 既存public residual/coarse residual: seed/seat反転のため追加sweep禁止。

次の性能作業を再開する条件は、tomato/Lucifer/plamen native cohortをcontrolとして明示し、AWR targetを「teacher outcome global bucket baseline」から、より直接的なcross-fitted actor-visible valueまたはaction-conditioned advantageへ再設計すること。ただし、現行AWRを同条件で反復しても情報量は低い。

longrunは`NO-GO`。理由は実戦候補がBestKnownを超えておらず、seed/seat gateも未通過であるため。Champion、`main.py`、package、Kaggle submissionは変更していない。
