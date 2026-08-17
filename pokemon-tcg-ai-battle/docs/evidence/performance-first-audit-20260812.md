# Performance-First 監査・次arm方針（2026-08-12）

## 結論

現行 `UniformLegalPolicyFactory` strict-disagreement は、教師品質を上げる本命経路ではない。legal action の全 logits が 0 で、top-1 margin は 0、lower-index tie-break であるため、今回の pilot は supervision mask・regularization・seed/optimizer挙動を測る control として閉じる。勝率が上がっても expert correction の証拠とは解釈しない。

Performance-First の主目標は、Archaludon 候補を実勝率・meta-weighted expected score が伸びる学習系へ移行し、再現した改善 arm だけを longrun へ進められる状態にすることである。優先順は、現行 pilot の完走、強 teacher の実強度/label quality監査、Rule-neural residual、public-only search/Q、broad/meta-weighted arena である。

## 現行 strict pilot（実行中）

```text
PID: 5539
output: runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot-rerun-20260812
seeds: 0,1
init: 対応する Wave6 seed0 / seed1 checkpoint
selection: runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json
selection SHA: b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc
paired seed manifest SHA: 47d75eec59c8d058523a3c0b41319bf47edb04856022e9aadbaa5f52f786250b
dagger fraction: 1/3（実際の sequence mixture は seed0 0.120879）
strict action types: 9,13,14
mean behavior log probability: <= -0.2
hidden/embedding: 128/64; TBPTT: 8; epochs: 3; patience: 1
learning rate: 0.0003; device: cuda:0
```

GPU は Windows 実再起動後に復旧済みで、RTX PRO 5000 Blackwell / driver 595.95 / PyTorch 2.11.0+cu128 / capability `(12,0)`、2048 matmul、V4 `.to(cuda:0)`が成功している。pilot はモデル転送を通過し、seed0 epoch 0 を完了した。初回の OOM は性能失敗ではなく、同時 CUDA process 後の GPU lost と未完了 Windows host reboot に伴う環境障害だった。

### seed0 epoch 0 の実測

```text
screen: 96 games / 4,763 transitions
selected episodes: 88（train 68 / validation 20）
eligible/effective supervision mass: 851
selected total mass: 9,904
selected non-forced mass: 4,415（available non-forced 4,498）
base sequences: 640; dagger sequences: 88; actual mixture: 0.120879
optimizer updates: 580
initial validation complete-action NLL: 0.6020762301
epoch-0 train NLL: 0.5884395907
epoch-0 validation NLL: 0.5605048985
validation delta: -0.0415713316
mean preclip gradient norm: 1.6318524377
train elapsed: 1,297.073 s（約21.6分）
```

checkpoint は `...-checkpoints/seed-0/best-recurrent-bc-v4.pt`（3,451,265 bytes）と `last-recurrent-bc-v4.pt`（12,874,518 bytes）を生成済み。`last` の `next_epoch=1`、`best_epoch=0`、`optimizer_updates_completed=580` を確認した。seed0 epoch1以降とseed1、最終 `bc.json`、性能評価は未完了である。

## strict data の実質的な信号

CPU preflight の eligible mass は seed0 851、seed1 985。ただし全 eligible 行で `prefix_index=0` であり、後続 prefix の correction ではない。

| seed | teacher target type | student type の主内訳 | eligible mass |
|---:|---|---|---:|
| 0 | 13: 784, 14: 67, 9: 0 | 7: 427, 8: 147, 13: 198, 9: 64, 12: 15 | 851 |
| 1 | 13: 896, 14: 89, 9: 0 | 7: 601, 8: 132, 13: 161, 9: 69, 12: 21, 14: 1 | 985 |

したがって、`{9,13,14}` filter は実際には type 13/14 の lower-index uniform tie と first-prefix disagreement を主に抽出している。episode は広く採用されても loss-bearing mass は限定されるが、教師戦略の質は保証されない。

## V4 architecture audit

`SpecialistModelV4(card_vocab=1267, hidden_dim=128, embedding_dim=64)` は 857,474 parameters。entity は embedding/projection と host relation を通した後、global token は 41 scalar projection と entity mean pooling。entity self-attention/set interaction はない。candidate は source/target/host と relation、action type、prefixを混ぜた単一 scalar scorerで、candidate間 listwise attention はない。action type は共通 scorer（type embeddingのみ）で、STOP は別 linear head。value/Q/uncertainty/opponent-belief auxiliary head はない。

次の性能仮説は、モデル容量そのものより、candidate/entity interaction不足、action-type negative transfer、value/Q信号欠落、UniformLegal label noise である。V5 candidate-attention は強 teacher/search target と同じ短期 bakeoffで比較する。

## Teacher / hybrid / search の監査

### 強 teacher 候補

`opponents/public_archaludon_cinderace_r7` は固定六96局で 62/96、fault 0、score 0.6458333、両 seat 同率。一次 artifact は `runs/meta-specialist-strength/teacher-archaludon-r7-fixed6-seed9700000-96.json`（SHA `d7ae81a2c08d078575829f083f0828338eada0ec1729213ed5c02d0927a0c4e2`）。ただし現在の `opponents/pool_manifest.json` では `usage_boundary=local_eval_only`、`smoke_ok=false`であり、training-local teacher record収集へは使えない。R7の所有者による明示許諾とsmoke/manifest更新がない限り保留する。これは policy strength の証拠であって label quality の証明でもない。

Rule Agent v0 は `agents/rule_agent.py` SHA `fe855dffc9592f4957d6afdedf3b2b2fd0a3ad531e442f5ba616ff73f1bb16e6`、main default SHA `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7`。最新同一 lane 監査では fixed-six 12/96、fault0であり、legal prior/rollback/control として使う。強 teacher上限とは扱わない。

現行の `derivation_qualified` teacher は `tomatomato_archaludon`、`lucifer19_battlecore`、`plamen06_steel` の3件だけである。既存のtomatomato 16局probeは全敗かつ現行policy SHAと不一致、lucifer19/plamen06の現行sealed recordは存在しない。旧BC smokeもteacher IDとpolicy SHAが不一致であるため、いずれもmatched armへ再利用しない。pilot完了後、tomatomatoを現行SHAで新規24局収集→seal→seed0/1 BCを最短の正規teacher armとする。R7は`local_eval_only`かつ`smoke_ok=false`のため、所有者の新規許諾とmanifest更新なしにtrainingへ使わない。

### Search

`search_teacher_v1.py` SHA `e4d61fb99ad7ffbae9624cfc515aa43b5775f4b6c785407fe15a3dd22c326605` は soft target blender で、実 determinization/rollout/Q/visit 生成はない。Kinoshita native search は hidden state を要求し、6–16秒 block/SIGSEGV と binary identity問題があるため、直接 teacher/runtime に使わない。public-only bounded adapterを作り、まず100–500 rootの Q/SE/visit artifactを作る。

### 最短 hybrid arm

V4 semantic legal decoderを維持し、Rule v0 の legal candidate mask/priorityを固定 prior として `final_logits = neural_logits + alpha * rule_prior` の side adapterで比較する。まず alpha=0 と事前登録 alpha=1、同一 checkpoint/seed/deck/opponent/protocolを比較する。低confidence/OODは legalityを再確認した上でRuleへ fail-closedする。既存 generic `use_rule_proposal` は V4へ未接続なので、そのまま流用しない。

## 評価・meta の確定事項

CABT engineに seed setter/API はなく、evaluator は agent seedのみ渡す。game-level ledger/state/action hashもないため、現行結果は common-random-number paired ではなく、opponent×seat×training seed×repetitionで層化した独立評価とする。

opponent schedule v1 は96 IDs / 75 unique deck hashes。Grimmsnarl同一hashがweight 17.8%を占め、Archaludonは2.6%に留まる。class/hash正規化したmeta weightingと、16–32 qualified strong opponentのbroad poolを作る。fixed-sixはdevelopment、shadow-Aはdevelopment-external、shadow-Bは最終untouched diagnosticとする。

## 次の実験順序と停止条件

1. 現pilotを完走し、UniformLegal controlとして `bc.json`、seed別 checkpoint/SHA、effective mass、fault、NLLを保存する。
2. `public_archaludon_cinderace_r7` の permission/record coverageを確認し、Wave6 seed0→seed0 data、seed1→seed1 dataの matched strong-teacher BCを作る。
3. Rule v0 residual alpha=0/1 の短期 screenを同一 decoderで作る。
4. public-only search-Qが作れた場合のみ ExIt/soft policy armを追加する。
5. fixed-six→shadow-A→broad/meta-weighted→shadow-Bの順で successive halvingする。

Performance-first longrun は、broad/meta-weighted 384局以上でRule v0またはbest baselineをおおむね+5pt上回り、複数seedが正、shadow-B方向一致、high-weight catastrophic collapseなし、fault0/runtime内を目安とする。未達なら UniformLegal のthreshold/fraction細密探索やNLLだけのepoch延長へ戻らず、teacher/search/hybrid/objectiveを変更する。

commit、push、remote branch、Champion変更、Kaggle submissionは行っていない。

## 2026-08-12 追補 — strict-disagreement pilot 完走と fresh fixed-six

GPU復旧後の同一固定条件 pilot は `RESEARCH_ONLY_COMPLETE` として完走した。report は [pilot report](../../runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot-rerun-20260812)（SHA-256 `09bb90523093de626a2b1913fc693fc519b2d8feebf121756308e3ac8fa1c109`、約53MB）である。`device=cuda:0`、elapsed 約9,535秒、fault/OOMなし、promotion_authority=false である。

| seed | best epoch | init validation NLL | best validation NLL | updates | best checkpoint file SHA | tensor-state SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 1 | 0.602076 | 0.551747 | 1,740 | `bf8d7337b4ba5b4bce6bd186d2685e618ef0ae61212fbf54bf06e2de60afc7d3` | `42876048a4a4e8fe6cff8fcd8811c617b941aad928db542c72baad65da81a71d` |
| 1 | 2 | 0.689207 | 0.604715 | 1,731 | `561e9d84b20d3e0db4d32f93daa84bd780edad0710ca6847cbddfc87ff40faf8` | `6426e3bdc79b615d87f53882fd3b1666b0820e42331c0091a53aba73033baab8` |

strict selection は seed0/1 でそれぞれ 88/91 episodes、eligible/effective loss mass 851/985、selected non-forced mass 4,415/5,250、actual DAgger mixture 0.120879/0.124487 だった。全体 strict report では available 96 games、4,763/5,590 transitions、disagreement 2,983/3,592、eligible 851/985。両seedとも context-only transition と loss-bearing transition が分離されている。UniformLegal の target は依然として uniform tie（top1 margin 0）が中心なので、agreement/NLL改善を teacher quality の証拠とは解釈しない。

同じ subject deck、fixed-six、両seat、各 opponent×seat 8局、base seed `10100000`、max steps 2,000、evaluation protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、evaluator implementation SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835` で fresh checkpoint を評価した。

| arm | wins/losses | score | seat0 / seat1 | faults | artifact SHA |
|---|---:|---:|---:|---:|---|
| strict-disagreement seed0 | 48/48 | 50.00% | 24/24, 24/24 | 0 | `9459686a36058e449ba73e735724c5d7b9a9698f3d7589abcbc79b4edc622651` |
| strict-disagreement seed1 | 46/50 | 47.92% | 23/25, 23/25 | 0 | `d5a73acb2116bd1e79ae2ef399867fbeb858854a0352dda40729a20c441502a5` |

対応する Wave6 baseline は seed0 43/96、seed1 50/96（合計93/192）。fresh strict は合計94/192で差は **+1局 / +0.52ポイント** に留まった。seed別では seed0 +5局、seed1 -4局で、旧 strict-paired の合計 +4.17pt や shadow-A +5.21pt を再現しなかった。CABT は engine seed setter を持たず game-level pairing もないため、これは独立層化比較であり paired/McNemar の証拠ではない。

したがって今回の判定は、**GPU・loss mask・2-seed 学習経路は成功、strict-disagreement の性能改善は不合格**である。longrun、Champion変更、提出へは進まない。UniformLegal strictの threshold/fraction/epoch sweep は打ち切り、次は permission が明示された `tomatomato_archaludon` を現行 policy SHA で新規収集・sealし、qualified strong-teacher BCを2 seedで matched比較する。R7 (`local_eval_only`, `smoke_ok=false`) は引き続き trainingに使わない。

## 2026-08-12 追補 — qualified teacher の新規 collection / BC / fixed-six

strict-disagreement の再現性が不合格だった後、現行判断記録で `training-local` が明示的に許可されている `tomatomato_archaludon` を、古い probe や provenance 不一致 artifact を再利用せず新規に収集した。収集時に pool manifest SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`、teacher policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`、derivation decision SHA `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` を再確認した。

新規 collection は `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-24/` に保存され、24/24 games、fault 0、records 1,386、outcome 18 win / 6 loss、seat subject-first 16 / subject-second 8、unlabelled 0 だった。manifest SHA は `ffb18429302782bd57e58a689d151aad807dcb3139e37ecb2b99130afd3cd408`。seal 後の `snapshot_index.json` は SHA `23a5613a45d54a1e718abf9cdb9ac81134044bbcd181e66daec49f8402f5c72c`、train/development/test = 894/428/64、examples 1,386、dataset snapshot root SHA `b69c3dc06e4f2903d3f3637137e7644f79cceb1270081f97f08cd1e299e26dab` である。BC script が index shards を identity 化した snapshot ID は `022e295dec2d1893f76d3ffa9d347f283b2a1182c57c8107b391c9589a89205d`。

この collection は V4 recurrent checkpoint の直接学習ではなく、`scripts/run_bc_distillation.py` の `SpecialistPolicyModelV1` foundation BC である。seed0/seed1 は同じ snapshot・同じ foundation init ID `ed3038f2d21ced4a59d24580f588894818c10bc74737d550993a2d3c65c0c343` から別 seed で、各 2,000 steps、skip 0 で完走した。

| seed | elapsed | first→last loss | final checkpoint | fixed-six result | faults |
|---:|---:|---:|---|---:|---:|
| 0 | 1,016.5 s | 1.876205 → 0.080851 | `6db6d6ecb777ca0369d4c06d1533a4ed5fbdd92025388fd11fedff12ec43146e` | 29/96 (30.21%) | 0 |
| 1 | 1,025.7 s | 1.807806 → 0.099362 | `94c83f89023ddb787c5293bd78495096966e6ba4b2c89cff0e5b33ecdc264fd8` | 29/96 (30.21%) | 0 |

checkpoint filename hashは実ファイル SHA と一致した。run summary SHA は seed0 `5005e35a5fc6ab27d7713d613cfa7d2c848900ba193889321de7ebc6f4708c25`、seed1 `13adeb4c16c346085123dc4b2d0945ed485a879974172ed957d77025492a6baa`。fixed-six JSON SHA は seed0 `7bb7f0b309bffcbfdbd50bafa27985993298b314f9bae5fdabd08928a07f1abe`、seed1 `bce41adfc7deea3b67d3800317b4f5227f44f74aaa1b2445de385421fefba63a`。

評価は V1 actor-pool evaluator（V4 evaluator へ形式変換せず）で、同じ subject deck bytes（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）、固定6 opponent、両 seat、各 opponent×seat 8局、合計96局/seed、base seed `10100000`、max steps `2000`、evaluation protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault 0だった。両 seed が同じ29/96だが、これは既存 Wave6 V4 baseline 93/192（約48.44%）を大きく下回る。従って「lossが下がった」ことは性能改善ではなく、**qualified teacher の直接 V1-BC arm は不合格**と判定する。

この失敗は teacher collection の permission 不備や学習経路のクラッシュではない。BC checkpoint の model topology が V4 と異なり、V4 fixed-six の promotion candidateへ直接置換できないこと、V1 BC は θ0 foundation 用で後段 trajectory training を想定していることを分けて扱う。seed0/1の同一29/96は、少なくともこの固定budgetの直接BCを長時間化する根拠にならない。次は V4 topologyへ強teacher targetを正しく接続するか、事前登録した Rule-neural residual/hybrid を小規模比較する。longrun、Champion変更、提出は行わない。

## 2026-08-12 追補 — qualified teacher の V4 変換 prototype と shadow-B

### V4 recurrent 変換と短期学習

`tomatomato_archaludon` の新規 sealed snapshot（train/development/test = 894/428/64）を、保存済みの actor-visible record と `RecurrentRecordAuthorityRowV3` / `_project_record_steps_v4` で in-memory に V4 sequence へ変換した。変換は train 894 records → 1,037 V4 steps、development 428 records → 498 V4 steps、test 64 records は学習へ投入せず、15/8 episode、episode split混在0、変換エラー0だった。これは実装可能性を確認する throwaway prototypeであり、production converterや公開APIはまだ追加していない。

Wave6 V4 seed0/seed1 checkpointから、同一変換・同一初期値 binding、`epochs=2`、`lr=1e-4`、`tbptt=8`、`quality_weight=1`、`supervision_weight=1`、`burn_in=1`、GPU `cuda:0`、各30 optimizer updatesの短期学習を行った。

| seed | 初期 validation NLL | 最良 validation NLL | delta | best epoch | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 0.507981 | 0.477583 | -0.030398 | 1 | `aa2b99f646e96e0157a41e9a73747901c76dbb7823c657d4bb9bced2fdb3523e` | `32be3ebf24932ca1b2ba188b7e3143aaaa0a6e96b73c505112fb20b085d404e6` |
| 1 | 0.527590 | 0.505581 | -0.022009 | 1 | `b49c716b7833084547c42fdce0623d18b4ec9194ac3d100aeec0e0378057253b` | `57ecee61e9a3c14d44f665d09246866f1dc13c42e896ce1eb3395dd061c29c78` |

validation NLLは両seedで低下したが、以下のCABT結果と同一視しない。V4 screen/confirmは同じ subject deck bytes（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）、fixed-six 6 opponents、両 seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault 0で実行した。CABT engine seed setterがないため、game-level paired/McNemarではなく独立層化比較である。

### fixed-six screen / confirmation

24局 screen では Wave6 seed0/seed1 が各11/24、V4 qualified-teacher prototype が各12/24だった。小標本だが両seedで同方向だったため、事前に定めた確認条件に従い各96局へ拡大した。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline（既存同条件） | 43/96 (44.79%) | 50/96 (52.08%) | 93/192 (48.44%) | 0 |
| qualified-teacher V4 prototype | 49/96 (51.04%) | 57/96 (59.38%) | **106/192 (55.21%)** | 0 |
| 差 | +6.25pt | +7.29pt | **+13勝 / +6.77pt** | — |

fixed-sixでは両seed・両seatでWave6以上だった（候補のseat内訳はseed0 21/48, 28/48、seed1 27/48, 30/48）。ただしこれは開発pool内の結果であり、これだけで長時間化・Champion候補化はしない。

### shadow-B promotion-untouched 診断

shadow-B manifest `runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json`（SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`）を、runnerのv2 schema未対応バグを回帰テスト付きで修正してから使用した。v1/v2とも候補assetのdeck/policy/source SHA、pool identity、manifest SHAを再検証した。候補とWave6を各 opponent×seat 4局、合計48局/seedで同一設定評価した。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline | 29/48 (60.42%) | 27/48 (56.25%) | 56/96 (58.33%) | 0 |
| qualified-teacher V4 prototype | 24/48 (50.00%) | 27/48 (56.25%) | **51/96 (53.13%)** | 0 |
| 差 | -10.42pt | 0pt | **-5勝 / -5.21pt** | — |

shadow-Bではseed0候補が5勝差で悪化し、seed1は同率だった。特に `pilkwang_lucario_alakazam` へ候補seed0は0/8、Wave6は6/8だった。したがって、現在のV4 qualified-teacher prototypeは fixed-six の正方向シグナルを shadow-B で再現できず、**汎化ゲート不合格**と判定する。fault 0は維持できたが、longrun、Champion変更、Kaggle提出へは進まない。

### 次の分岐

このarmの固定budgetを延長せず、(1) teacher targetのV4接続で semantic ActionKey/episode/permission provenanceをproduction contract化する、または (2) V4 semantic decoderへ Rule v0 legal priorを固定alphaで加える residual/hybridを、同一checkpoint・2 seed・fixed-six→shadow-Bで比較する。`tomatomato` の24局/1,386 recordsは十分な戦略teacher品質を証明する規模ではなく、R7は引き続き `local_eval_only` / `smoke_ok=false` なので trainingへ使わない。UniformLegal threshold/fraction/epoch sweepとV1直接BCの延長は行わない。

## 2026-08-12 追補 — Rule v0 main-action residual alpha=1 prototype

shadow-B不合格後の次候補として、V4 `SpecialistNeuralDecisionSessionV4` のmain selection（`selection_type=0`）だけに、Rule v0のaction type優先度を正規化した固定priorを加えるin-memory prototypeを実施した。priorは EVOLVE/ATTACH/PLAY/ABILITY/ATTACK/END = `0.6/0.5/0.4/0.3/0.2/-1.0`、target selectionのdamage/hpはV4 stepへ完全に残っていないため変更しなかった。alpha=1のみを事前登録条件として評価し、alphaを勝率に合わせて後追い調整していない。V4 checkpoint、subject deck、shadow-B manifest、protocol、max steps、4局/cell、fault計上は直前のshadow-Bと一致し、Rule v0提出経路・checkpoint bytesは変更していない。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| prototype候補 alpha=0（直前候補） | 24/48 (50.00%) | 27/48 (56.25%) | 51/96 (53.13%) | 0 |
| prototype候補 alpha=1 | 25/48 (52.08%) | 18/48 (37.50%) | **43/96 (44.79%)** | 0 |
| alpha=1 − alpha=0 | +1勝 / +2.08pt | -9勝 / -18.75pt | **-8勝 / -8.33pt** | — |

alpha=1はseed0の `naoto714_ursaluna` では8/8まで上がった一方、seed1では `pilkwang_lucario_alakazam` 1/8、`prvsiyan_grimmsnarl` 1/8となり、両seatとも9/24へ崩れた。seed間の反転が明確なため、単純なaction-type priorのproduction化、および alpha の後追いsweepは打ち切る。Rule v0を使うなら、V4 semantic/physical mappingとconfidence/OOD gateを含む別契約として再設計し、同一seed・seat・opponentで再検証する。

prototype JSON SHAは seed0 `2258ddc1147c6dc1cb674761d4819a1df7ede7a6ad1ab683c1f9bb6990300ce0`、seed1 `ecf8934e2cd1614a3dbb88a94efb49f2bb18ecb9efea099b8a3ead0fc8a5b485`。これはin-memory monkey-patchによる研究診断であり、production code/agent identity/Championには反映していない。

## 2026-08-12 追補 — qualified teacher snapshot の被覆・alias・label監査

24局 snapshot の raw record 1,386件を `record_id` で sealed snapshot へ突合し、offline 集計した。episode は24件で、train/development/test は15/8/1 episodeに分離され、episode splitの混在は0だった。near-duplicateについては、snapshotのubiquitous groupが1つあり16 recordsが `example_quality_weight=0.5`、残り1,370 recordsは1.0だった。ubiquitous groupは三分割にまたがるため、重みで抑制されているが同一近似state自体は消えていない。

| partition | records | loss rows | quality-weighted rows | episodes | capped records |
|---|---:|---:|---:|---:|---:|
| train | 894 | 1,037 | 1,031.5 | 15 | 11 |
| development | 428 | 498 | 496.0 | 8 | 4 |
| test | 64 | 74 | 73.5 | 1 | 1 |

V4 recurrentへ実際に投入した学習対象は train 1,037 steps / development 498 stepsで、semantic prefix長は全体で prefix0=1,386、prefix1=162、prefix2=34、prefix3=9、prefix4=8、prefix5=5、prefix6=3、prefix7=2だった。大部分はphysical recordの最初のprefixであり、長いmulti-select continuationの教師信号は少数である。

raw selection typeは `0=715, 1=595, 9=70, 8=6`。sealed loss-rowのpositive target operationは `CARD=802, PLAY=374, ATTACK=150, ATTACH=107, EVOLVE=47, YES=44, NO=26, END=19, RETREAT=18, NUMBER=6` で、STOP targetも16 records相当（teacher selectionが空の16 records）存在した。raw recordの `teacher.status` は全1,386件でavailableだが、`behavior.status` は全件unavailable（外部teacherがpolicy distributionを公開しないため）である。この二つを同じteacher quality指標として読まない。

teacherのhard selectionは、1,370 recordsではlocal actionへ解決できたが、16 recordsはselectionが空で、V4 lossではSTOPへ写像された。これは「teacherがSTOPを選んだ」のか「selectionが記録できずSTOPへ正規化された」のかを区別できないため、次回強teacher collectionでは empty selection を明示的な `unavailable`／context-only とするか、STOP hard targetとして採用するかを事前固定する。

physical legal action domainは2〜28件に広がり、semantic aliasの重複を含むrecordは655/1,386件、selected action側で複数physical aliasが同一semantic actionへ写るrecordは90件だった。model_input_idは1,371 groups、異なるteacher targetを持つconflicting groupは0、同一inputの反復最大16件だった。したがってV4のcanonical semantic projectionは必要だが、今回の24局だけでshadow-B失敗をteacher quality単独へ帰属できない。

分離対象は (a) 16 empty selectionのSTOP写像、(b) RETREATを含むmain action typeの相対頻度、(c) alias canonicalization、(d) 24 episodeという小さい対戦被覆である。これを検証するため、同じ許可済みteacher・同じsubject deckで96局の新規collectionを別rootへ開始した（進行中: `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/`）。完了前の件数・勝率は未確定で、旧24局artifactへ追記・上書きしていない。

## 2026-08-12 追補 — root提出deckとArchaludon評価deckのidentity差

現在のroot `deck.csv` は raw SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、deck identity `deck-0fd28b79fc39ffc55f77` の60枚デッキ（Mega Lucario/Hariyama系、内部 variant `DV-000007`）である。一方、この期間のArchaludon V4学習・fixed-six・shadow-A/B評価は `opponents/public_archaludon_cinderace_r7/deck.csv` または `opponents/tomatomato_archaludon/deck.csv`（実験記録上のsubject deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）を使っている。従って、今回のV4勝率・shadow-B判定をroot `deck.csv` のKaggle提出性能へ直接移すことはできない。

root deckの変更は既存dirty差分に属し、今回の実験では変更していない。Archaludon policyを本番候補へ昇格する前に、提出対象deckと学習・評価subject deckのidentityを一致させるか、root deck向けに別laneで再収集・再評価する必要がある。

## 2026-08-12 追補 — qualified teacher 96局 snapshot / V4 short arm / shadow-B 再評価

24局 snapshot の被覆不足を切り分けるため、permission が明示された現行 `tomatomato_archaludon` を再収集した。収集 root は `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/`、96/96 games、fault 0、records 5,146、outcome 60 win / 36 loss、seat 48/48 だった。manifest SHA は `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff`、snapshot index SHA は `b5cc75c82ee321cb7841b99f80d49fd6759e56d060af435200239a45b36bc72f`、dataset snapshot root SHA は `38a361ec571e2d8ba9546db333fd48f33ffb72d7d8526ba304f4be80235c559a`、snapshot ID は `6eeb7b730fb8a064ed14801570c62f279927710f45b971fbc343da7f3b569ff` である。split は train/development/test = 3,351/966/829 records、episode = 63/18/15、episode split混在0、近似重複 cap は48 recordsだった。

この arm 用に `scripts/run_v4_qualified_teacher_snapshot_bc.py` を研究用に追加し、sealed snapshot の train/development/test 境界を保ったまま V4 sequenceへ投影した。test 829 records は学習・検証へ投入していない。train/development は3,860/1,108 V4 stepsで、短期 trainerは研究モードの uniform weight (`uniform_research_1.0; sealed_cap_reported_only`) を使用した。このため、sealed snapshotの品質cap統計は保持するが、今回のlossへcap重みを混在させていない。report SHA は `57d96ded9d07fa9a70b22a0f3c8319c1d0f9a34f9c067f1194625b0a3a34cc04`、objective SHA は `f9349d0deffdb077580f82996049287e56429a99645aa6e551daab924f4d6f53`、trainer SHA は `d543b9e1c60bc91c23aaed50c107c6eadcb1cc49e7b1f23e7a0c69d82c649845` である。

Wave6 V4 seed0/seed1から、epochs=1、patience=0、lr=1e-4、TBPTT=8、burn-in=1、cuda:0、各63 optimizer updatesで学習した。

| seed | 初期 validation NLL | 最良 validation NLL | delta | checkpoint file SHA | tensor SHA | 学習時間 |
|---:|---:|---:|---:|---|---|---:|
| 0 | 0.574510 | 0.491043 | -0.083467 | `6067a9fe8ed9ab9289c48b782b88520c64e94044921ae641d2bff6596569d789` | `f8a4818b609031504ad65af3f759872400182d3cd8fe8f4749e938bba1d56754` | 127.1s |
| 1 | 0.587545 | 0.521108 | -0.066437 | `f26cc2c20898176d5b318328b3d384176bdaee4eaa226c399585a8dd48dc4459` | `28a4937ede8b57269d4fb3277bf8c7b3a19bf33bf60d305963fdefd6ca0fc281` | 128.0s |

同一 subject deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、fixed-six 6 opponents、両seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fixed evaluator SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`、fault 0で24局 screenを行った。

| arm | seed0 | seed1 | 備考 |
|---|---:|---:|---|
| Wave6 baseline | 11/24 (45.83%) | 11/24 (45.83%) | 既存同条件 artifact |
| 96局 qualified-teacher V4 short | 17/24 (70.83%) | 17/24 (70.83%) | 両seedとも +6勝、screenのみ |

screenでは両seedが正方向だったが、promotion-untouched shadow-Bを省略しなかった。shadow-B manifest SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`、各 opponent×seat 4局、48局/seedで評価した。candidate JSON SHAは seed0 `b22ca91cbca3b90b743a9f044924e39597726ed4b26903374a2ab85b2ddff65e`、seed1 `01f69d01b09d6677e9d25d2686a06356b8b582cca770c7c2a31777442fe0d0da`、shadow evaluator SHA `088cadb0017738ffd41da722fe0456696ab02d82755b6280ec4fc67047896e35` である。

| arm | seed0 | seed1 | 合計（drawは0.5換算） | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline | 29/48 (60.42%) | 27/48 (56.25%) | 56/96 (58.33%) | 0 |
| 96局 qualified-teacher V4 short | 26/48 (54.17%) | 24W/23L/1D (51.04%) | 50.5/96 (52.60%) | 0 |
| 差 | -3勝 / -6.25pt | -2.5 score / -5.21pt | -5.5 score / -5.73pt | — |

seed0は `pilkwang_lucario_alakazam` 0/8、seed1も同相手2W/6Lで、`kiyotah_iono` と `prvsiyan_grimmsnarl` でも低下した。fixed-sixのNLL/screen改善は shadow-Bへ一般化しなかったため、この arm は **汎化ゲート不合格** と判定する。96局へ増やしたことで24局由来の被覆不足を一部緩和したが、同じ弱相手への崩れが残るため、V4 BCの長時間化、Champion変更、Kaggle提出へは進まない。なお shadow-BはCABT engine seed setterがなく、game-level paired/McNemarではなく独立層化比較である。

今回の結果から、単純にteacher collection局数を増やして同じV4 BCを延長する方針は採用しない。次の性能実験は、(a) teacher targetをlossへ入れる前の empty-selection/RETREAT/alias/episode continuityを明示するV4 contract、または (b) public-only action-value/search target・weak-matchup residualなど、shadow-Bで崩れた相手を直接診断できる別objectiveに限定する。root `deck.csv`とのidentity差も残っているため、Archaludon結果を提出デッキの性能と混同しない。

## 2026-08-12 追補 — empty selection を context-only にした診断 arm

96局 snapshotの teacher `mass_rows.selection=[]` をV4 projectionでSTOP hard targetへ写像していた点を切り分けるため、空selection recordをhidden contextとしては残しつつ、`supervision_weight=0` とする研究用flag `--exclude-empty-selection` を `scripts/run_v4_qualified_teacher_snapshot_bc.py` に追加した。新しいproduction runtime、teacher artifact、V4 model本体は変更していない。train/development内の除外対象は46/14 records（46/14 steps）、testは引き続き学習へ未投入である。

同じWave6 init、epochs=1、lr=1e-4、TBPTT=8、burn-in=1、cuda:0、各63 updates、subject deck/protocolを固定した。report SHAは `8a2dbd10af7d30b5a14be9ab345be26dd1cd811389249a7c375321d3c302950e`。seed0 checkpoint SHAは file `ae70404c8df7aadfa9c04aa0bf579f9136437e87e4b5b74827dffa28c89ea7e4` / tensor `f22afc60d6c8c17a2d74b9cf4e9af81025240332710d0552cc9c52b3c3e91f48`、seed1は file `61dd24350bd8be87cdaa811d6726191175b499a5886bfaa961e98e7cb146378f` / tensor `2cc00549857ffcef00ac86481659193f5e09b15c2b37da4e9baffd949ea0467d` である。NLLはseed0 0.506920→0.485650、seed1 0.528839→0.501906と低下した。

fixed-six 24局 screen（fault 0）の結果は次の通り。JSON SHAはseed0 `0f6e9e7597dfc938348e3959f4bfe1ed4c16a4adef9800313da7fba08298a81c`、seed1 `609fbde00a6e5fb07b9e159a8a0b77a0552ffbb8bf19f3428221533a349671a8`。

| arm | seed0 | seed1 | 解釈 |
|---|---:|---:|---|
| empty-selection context-only | 8/24 (33.33%) | 18/24 (75.00%) | seed0 seat1=0/12、seed1は正方向 |

Wave6同条件は各11/24である。片方のseedがseat横断で崩れ、もう片方が改善するため、この仮説は不合格であり、shadow-Bへ拡大しない。空selectionのSTOP写像だけを変更してもseed依存性は解消せず、むしろseed0のtrajectoryを悪化させた。これにより、同じteacher/V4 BC系列のthreshold・STOP扱い・長時間化を続ける根拠はなくなった。

## 2026-08-12 追補 — pre-registered action-balanced objective の不採用

同じ96局 snapshotとWave6 initに対し、既存コードで定義済みの `ACTION_BALANCED_WEIGHTS_V1` を一度だけ適用した。重みは `PLAY/ATTACH/ABILITY=1.0`、`EVOLVE=1.5`、`RETREAT=1.25`、`ATTACK=1.25`、`END=1.5`、`STOP=0.75`、その他 `0.75`（trainer内で平均1へ正規化）で、alphaや重みのsweepはしていない。epochs=1、lr=1e-4、TBPTT=8、各63 updates、test除外、subject deck/protocol/fault条件は直前armと同じである。

report SHAは `7d55180191933484f821cd89a879b7b0e73836d10abf7e1742f30810bce74728`。seed0 checkpoint file/tensor SHAは `fa540ec6f7ee685b9336ae35974106a0a8b8cd8ffee57c128d605c8395e1213f` / `bd6b007d8b03cc7637f629cbcd42f3dd3c34ee372a6f66b0b4b16ae615090da2`、seed1は `1b2e1b23a023574ec58a6c7f11f8dfc3e7a1c33d892036b343fb3c60a7ad54d9` / `b7b5401491190dc8feb433e61244849e3bee73b8cc3609cfad2cda51321a438c`。validation NLLはseed0 0.574510→0.495455、seed1 0.587545→0.524334へ低下した。

fixed-six 24局 screen（fault 0）はseed0 10/24、seed1 10/24（Wave6各11/24）。seed0/1ともbaseline未満であり、shadow-Bへ拡大しない。action-balancedのNLL低下も実戦改善に転化しなかったため、現行V4 qualified-teacher BCについて、STOP扱い・単純macro-action weighting・epoch延長の局所探索は打ち切る。

## 2026-08-12 追補 — qualified teacher `lucifer19_battlecore` の教師多様性 arm

`tomatomato_archaludon` とは別系統の、判断記録で `training-local` が許可された `lucifer19_battlecore` を一度だけ比較した。既存 artifact は再利用せず、現行 policy SHA・permission manifestを結び付けた新規 collection とした。収集 root は `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/`。

| artifact / 条件 | 値 |
|---|---|
| collection | 48/48 games, fault 0, records 2,790, outcome 40W/8L, seat 32/16 |
| teacher policy SHA | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` |
| collection manifest SHA | `1570bc1e2664fc6f60d126a6e0517cca1a2bca066976803ff954e6a6dfbe6424` |
| snapshot index SHA | `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3` |
| snapshot shard SHA | `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e` |
| snapshot ID / root SHA | `cf83f38937915205597818cad89efbf48ff1f6ef9e5477bb79621a438357ced9` / `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2` |
| split | train/development/test = 1,928/436/426 records |
| subject deck | `opponents/lucifer19_battlecore/deck.csv`, raw SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` |

teacher policyの48局強度は40/48 (83.33%)だったが、これは教師ラベルの正しさを直接証明しない。外部teacherのpolicy distributionは公開されず、collectionはactor-visible recordの抽出である。また、このdeckのraw bytesはtomatomato subject deck（`42165967...`）と異なるため、両teacherの勝率を単純に合算しない。

### V4 short BC と同一deck fixed-six screen

Wave6 V4 seed0/seed1 checkpointから、同じ research runner、epochs=1、lr `1e-4`、TBPTT=8、burn-in=1、cuda:0、各35 optimizer updatesで学習した。test 426 recordsは学習・検証へ投入していない。report SHAは `17f4e0c207875522530ff4e32b214cd5872a28eb878e53a2995d9d9b44fb33f7`、objective SHAは `9f159bfd5640a9e63fbc5ecc15e85b1042eec94e1ac515234704d1a59781c6ad`、trainer SHAは `d543b9e1c60bc91c23aaed50c107c6eadcb1cc49e7b1f23e7a0c69d82c649845` である。両seedとも研究用lossは低下した。

| arm | init val NLL | best val NLL | checkpoint file SHA | tensor SHA |
|---|---:|---:|---|---|
| seed0 candidate | 0.500953 | 0.457705 | `9058fd71fed68f9c0eaec2ed4a64fae16b0ece201279696900ee544a0dcaefa6` | `72628bc590241a9f0d87e4082930a5b47cbe778bc3d6761597c2f99c693988a5` |
| seed1 candidate | 0.519161 | 0.480082 | `b57e76cf29199d4a9f058273002dd4deafc8535abccccffbc5fef94bcbcb25a0` | `52b68b12c203e30d4376724151a41244e2d27d3149ba5ee9ffe34ff63f547308` |

同一lucifer subject deck、fixed-six 6 opponents、両seat、2 games/seat、base seed `10100000`、max steps `2000`、fault 0でscreenした。評価JSONのSHAは candidate seed0 `be784b5c349b5ef23f1be4bbbabc77939a39a09f5fa1c39c2d7323c49de02e69`、candidate seed1 `474c2f5d5dc5bdb74a9b57beecf1c952375713f3ae50dbe203d88fd2b3433f6d`、Wave6 baseline seed0 `060eb79a46630d8bb4da6748661701166a286bfeefac90570964e59d006db9ff`、baseline seed1 `a5339eaeb1b0f72423ff0c05ce656ce40b738d94e67da64e84bd977f1d3a5b92` である。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline | 15/24 (62.50%) | 10/24 (41.67%) | 25/48 (52.08%) | 0 |
| lucifer19 teacher V4 short | 14/24 (58.33%) | 13/24 (54.17%) | 27/48 (56.25%) | 0 |
| candidate − baseline | -1勝 / -4.17pt | +3勝 / +12.50pt | +2勝 / +4.17pt | — |

合計は+2勝だが、seed0ではbaselineを下回り、seed1の+3勝が合計を作っている。各cell 4局未満の診断ではなく24局/seedへ増やしたものの、seed間の符号反転は解消しなかった。したがって、lucifer armは **teacher系統を変えてもV4 short BCの再現可能な昇格根拠にならない** と判定する。shadow-Bへは進めず、longrun、Champion変更、Kaggle提出は行わない。

この結果は「lucifer teacherが弱い」と確定するものではない。teacherの40/48という対戦強度は別のpositive signalだが、label quality、subject deck差、V4 semantic projection、episode/seat coverage、outcomeとの乖離が残るためである。現時点では同じV4 BCのteacher差し替え・epoch・weightの追加sweepを続けず、public-only value/searchまたはweak-matchup residualのどちらか一つを事前登録したbounded比較へ移る方が情報量が高い。

## 2026-08-12 追補 — lucifer subject deck の Pilkwang trace 診断

lucifer19 subject deck上で、shadow-Bの `pilkwang_lucario_alakazam` に対して、candidate seed0/1とWave6 seed0/1を各4 games/seat（計8局/arm）実行した。これはCABT engine seed setterがなくgame-level pairedではないため、弱相手の公開情報trajectoryを観測する研究診断であり、promotion evidenceではない。manifest SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`、subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`、base seed `10100000`、max steps `2000`、fault 0を固定した。

| arm | seat0 | seat1 | 合計 | trace rows / redacted |
|---|---:|---:|---:|---:|
| lucifer candidate seed0 | 1W/3L | 1W/3L | 2W/6L | 409 / 123 |
| lucifer candidate seed1 | 0W/4L | 1W/3L | 1W/7L | 448 / 125 |
| Wave6 seed0 | 1W/3L | 3W/1L | 4W/4L | 361 / 94 |
| Wave6 seed1 | 2W/2L | 2W/2L | 4W/4L | 454 / 108 |

公開projectionで action type は全armで空（physical/private aliasを出さない設計）だったため、semantic selection type/context/count/log-probabilityだけを比較した。Pilkwang traceのseat別平均 complete-action log probabilityは次の通り。

| arm | seat0 rows / mean logp | seat1 rows / mean logp |
|---|---:|---:|
| candidate seed0 | 217 / -0.2973 | 192 / -0.3412 |
| candidate seed1 | 215 / -0.3135 | 233 / -0.3463 |
| Wave6 seed0 | 219 / -0.3971 | 142 / -0.3877 |
| Wave6 seed1 | 231 / -0.3374 | 223 / -0.3260 |

候補seed0/1はWave6と異なるselection trajectory（seed0 seat1のrows増、Wave6 seed0 seat1のrows減）を取ったが、redacted rowsが約26〜34%あり、これだけで「RETREATを誤った」「特定aliasを選んだ」とは言えない。現時点で支持される仮説は、teacher targetの単一action errorではなく、少数のsemantic decision差がGRUの後続trajectory・ゲーム長・seat calibrationへ増幅されること、またCABTの非paired乱数により同じbase seedでも局面が一致しないことである。

JSON SHAは candidate seed0 `50f57d5299ba45b284808e48175021e5261871393de29ca0ab9478a6e1c36767`、candidate seed1 `9d6b93701cc8fa2e77e33863cfea01db8b93b0c96656af1d84c66f43a76654a5`、Wave6 seed0 `2b1a8cd3e8e4491744a8b419831449eca81a7c647f42f9ef4792f4b0c484d79b`、Wave6 seed1 `ef64a75fb8ccb74e7660a75b4d4c1cea472abe5af0a1e350ecc0ca18670e22bc`。JSONL trace SHAはそれぞれ `863ead36ed8f6b6a7d9c8b033edadae4a26ac72c70d9f76d237e0f895343f0a9`、`622cf30f0c46b4be0e7c06f82198716c22ba4272dfa690488b2a15c5e561d791`、`6aec5b8a02b4d193c7d610c3d97acde90e539c91cf47f76d8e727e5571be6f68`、`589c2588903553d8d4380f1a8da4ccf67cd25e3627a2cd9ccc29542d9a2e3f43` である。production runtime・checkpoint・submission identityは変更していない。

## 2026-08-12 追補 — outcome-weighted V4 BC の固定六ゲート

teacher outcomeを研究用のepisode quality weightへ変換する最小実験を、事前登録した固定条件で一度だけ実行した。実装は `src/mage_ptcg/meta_specialist/outcome_weighted_v4.py` と `scripts/run_v4_qualified_teacher_snapshot_bc.py --outcome-weighted` に限定し、production runtime、semantic decoder、teacher artifact、deck、submission pathは変更していない。重みは max-normalized の固定値（win=1.0、draw=2/3、loss=1/3）で、sweepは行っていない。既存の `test` partition 426 recordsは学習・検証から除外し、同一episodeの全prefixへ同じ重みを付けた。

### 学習 artifact と provenance

対象は permission が結び付いた `lucifer19_battlecore` snapshotである。snapshot index SHAは `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`、shard SHAは `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e`、dataset snapshot root SHAは `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2`、subject deck SHAは `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` である。train/development/testは1,928/436/426 records、episodeは35/7（testを含めない学習側）で、trainのoutcomeは29 win / 0 draw / 6 loss、validationは5 win / 0 draw / 2 lossだった。report SHAは `1d2dda7caa93f37b453977494cc22acf7ef740fd87b4367e2662fd2c2771c2c8`、modeは `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4`、objective SHAは `1ca5807dc54410206cc82f19c613ba1387393cdf016cecdedf3e87f3a44f5d34`、trainer SHAは `d115bd58767ca6ba45016806d5135e713b5c6e0a4a2a4ce96590b1290f307b91`、promotion authorityはfalseである。

Wave6 seed0/seed1から、epochs=1、patience=0、lr=1e-4、TBPTT=8、burn-in=1、`cuda:0`、各35 optimizer updatesで学習した。validation complete-action NLLはseed0が0.559368→0.501640（-0.057728）、seed1が0.571340→0.517306（-0.054035）へ低下した。これはoffline objectiveの改善であり、CABT性能改善を意味しない。

| seed | candidate checkpoint file SHA | tensor SHA | fixed-six JSON SHA | 結果 | seat0 / seat1 | fault |
|---:|---|---|---|---:|---|---:|
| 0 | `24c3b82e40282e68050a7ab20832bf8a88cc0cbec4a60c63d57630b89b249a65` | `f544922e5143b0bd07df44e621e8b23e2dd09741c621503be6b54d0217d4fd3a` | `a68fcac3fa46c6e0cea48c85fcb68e6e1fe2532c9cb109a0fd31590605dda45d` | 12W/12L (50.00%) | 5/12, 7/12 | 0 |
| 1 | `d99e7d89573dfd0606e4b80c15136006e566958b597500f14057531d91a19e19` | `c462c4321c1c6fb0ebe6eaf01b102ec2fd489b502c10e3a789c96096bcde0c5f` | `bb7a35a075c41121c803f03930c32b1a862473240b81096ab1fa8cef73d89301` | 11W/13L (45.83%) | 7/12, 4/12 | 0 |

評価は同一 `lucifer19_battlecore` subject deck、fixed-six 6 opponents、両seat、2 games/opponent×seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、evaluator SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835` で行った。比較対象のWave6 JSON SHAはseed0 `060eb79a46630d8bb4da6748661701166a286bfeefac90570964e59d006db9ff`（15/24、seat0 9/12・seat1 6/12）とseed1 `a5339eaeb1b0f72423ff0c05ce656ce40b738d94e67da64e84bd977f1d3a5b92`（10/24、seat0/seat1各5/12）である。

### ゲート判定

候補合計は23/48、Wave6は25/48で、差は-2勝 / -4.17ポイントだった。seed0は12/24でbaseline 15/24を下回り、seed1は11/24でbaseline 10/24を上回ったが、seed1 seat1は4/12でbaseline 5/12を下回った。したがって、事前条件（両seedでmatching baseline以上、seat非悪化、fault 0）を満たさず **固定六ゲート不合格** とする。NLL低下だけを理由に候補を採用しない。

この不合格により、outcome-weighted armのshadow-B、長時間学習、Champion変更、Kaggle提出は実行しない。今回の結果は、Lucifer teacherの強度やoutcome重みが無価値だと確定するものではないが、現行V4 recurrent BCへ単純なepisode outcome weightingを加えても、seed横断の実戦再現性は得られないことを示す。次の性能実験は同じBCのweight/epoch sweepではなく、public-only value/search targetまたはweak-matchup residualのどちらか一つに限定し、fixed-sixで再び事前登録してからshadow-Bへ進む。

## 2026-08-12 追補 — outcome-weighted artifact の実装不備訂正と修正版

上記の旧armは trainer SHA `d115bd58767ca6ba45016806d5135e713b5c6e0a4a2a4ce96590b1290f307b91` に基づく。旧trainerはepisode quality `q` を各stepのlossへ掛け、同じ `q` の総和でepisode lossを割っていたため、episode内でqが一定なら勾配上はuniform BCと同値になる。追加した最小勾配テストでq相殺を再現し、旧armの勝率結果を実効outcome weightingの証拠として扱わないことにした。旧report SHA `1d2dda7caa93f37b453977494cc22acf7ef740fd87b4367e2662fd2c2771c2c8` と旧評価JSONは履歴artifactとして不変保存する。

分母からqualityを外す修正版のtrainer SHAは `bbe8c151a78d36daeb0a7da995d54d65fef7c94892dec513d0d4610334fa4308`。修正版reportは `runs/meta-specialist-v4-qualified-lucifer19-48-outcome-weighted-corrected-bc-20260812/report.json`、SHA `03021ad432b7de828da1f4a4297f1c4421c7c658f3cc4931b6df22e8590aa589`、mode `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4`、固定weights win=1.0/draw=2/3/loss=1/3、test 426 records除外、1 epoch・35 updates/seed・fault0。validation NLLはseed0 `0.5593681099→0.5019691924`、seed1 `0.5713402831→0.5183927419`。checkpoint SHAはseed0 file/tensor `c3ac8683e7fe4ef15f00b1560cfed701ba0202c216f1cefe2c95b630c0357eff` / `57372d0f0dcd3f1e3f494ddd7dec391884e14708c7ff71a37d3cc91c058d4d43`、seed1 `d2aa3f696746ab0330b080af4d9627db9dece38f6c64b432188be87a3f23cc75` / `5f68695b61e70721c8198a2946820e090b97a9228a0fff0285c7f7811b1d124a`。

同じ固定六条件（lucifer subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`、6 opponents、両seat、2 games/cell、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`）で、修正版candidateはseed0 `12/24`（seat0 8/12、seat1 4/12）、seed1 `14/24`（8/12、6/12）。Wave6 baselineは15/24（9/12、6/12）と10/24（5/12、5/12）。aggregateは26/48対25/48（+1勝、+2.08pt）だが、seed0とseed0 seat1がbaseline未満であり、事前ゲート不合格。評価JSON SHAはseed0 `3ff17a81bf3d95795216f3fa0c4bf1d5941889fc2d6958dfdcd198b948f9fde9`、seed1 `30656422dd405d78e6ade83d6a9cf1f78c2100fe788ae194d3e52045ac622833`。

この結果は、quality weightが実際に勾配へ作用することと、offline NLLが低下することを確認した一方、CABTでのseed横断改善を確認できなかった。shadow-B、longrun、Champion変更、Kaggle提出は行わない。以後、同じsnapshotの単純weight／loss-focused sweepは停止し、別objectiveのbounded比較へ移る。
