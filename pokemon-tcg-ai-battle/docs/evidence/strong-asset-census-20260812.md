# Strong Asset Census (2026-08-12)

## 結論

`opponents/pool_manifest.json` の102個の deck+agent pairを、同一deckでもpolicyが異なれば別identityとして棚卸しした。既存証拠で測定済みの暫定最大は `public_archaludon_cinderace_r7` のfixed-six 96局 62W/0D/34L（64.58%、fault 0）。ただしsmoke失敗かつlocal_eval_onlyのため、学習・Champion昇格・提出には使わない。

Common-poolの最終GlobalBestKnownは、実行中Stage1 arenaが完了し全manifest/SHA/fault/seat/seedを検証するまで未確定。このCensusはarenaを起動・停止・編集せず、未完了出力をランキングへ混ぜていない。

## 1. 範囲・不変条件

- 読み取り元: `opponents/pool_manifest.json` (SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`)、`opponents/registry.yaml` (SHA `630c6d5679f063536286eb6eee003ba3dd29fb930f8e0d16ffa45df13b5c310e`)、各 `opponents/<id>/{main.py,deck.csv,SOURCE.md}`、既存 `runs/**/strength.json` と evidence。
- 新規作成は本Markdownと `docs/evidence/strong-asset-census-20260812.json` のみ。既存ファイル・他agent成果物・実行中arena outputは変更していない。
- 大規模CABT・再学習・提出は起動していない。
- 性能identityは `(agent/policy SHA, deck SHA)`。同一60枚deckでもagent/policy別に分離。
- `deck_id_declared_canonical_sha256`（manifestのmultiset digest）と`deck_sha256_raw_file`（deck.csv raw SHA）は別表現として併記。
- archetypeはID/SOURCE/deck名の明示ヒントから保守的に推定。`meta_*`/`medal_*`は元agent不在のdecklist-only generic asset。

## 2. 集計

| 指標 | 値 |
|---|---:|
| pair数 | **102** |
| public / internal | **71 / 31** |
| policy SHA unique | **58** |
| declared deck SHA unique | **77** |
| raw deck SHA unique | **79** |
| smoke true / false | **101 / 1** |
| training usable | **2** |
| evaluation-only | **100** |
| policy raw SHA mismatch | **0** |
| deck raw/canonical mismatch | **101（表現差）** |

policy raw SHAは102/102でmanifestと一致。deckはcanonical digestとraw SHAが別アルゴリズム／履歴表現で101件不一致。R7はlegacy宣言値がraw SHA (`42165967…`) と一致するため、両SHAを保存し混同しない。

## 3. training permission / runtime gate

学習可能としたのは、既存文書で明示されたbounded local teacher collectionに限定。`local_eval_only` だけから学習許可を推定しない。提出利用許可は全pairで付与していない。

| pair | training | 根拠 | runtime | 用途 |
|---|---|---|---|---|
| `lucifer19_battlecore` | **YES bounded local** | `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/manifest.json`（teacher collectionでstudent勝率ではない） | smoke_pass_fast, smoke=true | labels/trajectory収集のみ |
| `tomatomato_archaludon` | **YES bounded local** | `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/manifest.json`（teacher collectionでstudent勝率ではない） | smoke_pass_fast, smoke=true | labels/trajectory収集のみ |
| 上記以外100 pair | **NO/not evidenced** | manifestのlocal_eval_onlyのみ | smokeとは独立 | evaluation-only |

R7は最高測定値でもsmoke=falseのため学習源へ昇格させない。R7とtomatomatoは同一deckでも別policyで成績/smokeを移さない。

## 4. provisional GlobalBestKnown

- pair: `public_archaludon_cinderace_r7::deck=42165967b565dd42::policy=c08588467c3faa2c`
- archetype: Archaludon/Cinderace
- evidence: `runs/meta-specialist-strength/teacher-archaludon-r7-fixed6-seed9700000-96.json` (SHA `d7ae81a2c08d078575829f083f0828338eada0ec1729213ed5c02d0927a0c4e2`), fixed-six 96局 62W/0D/34L, fault 0, score 64.58%
- 判定: `PROVISIONAL / NOT PROMOTABLE`（smoke false, local_eval_only）。common-poolの最終GBKではない。

別軸: `waterbox_search_v3` Water Box/StarmieはSOURCE.mdにLB 789.4（Kaggle ref54772065）。pool copyは探索予算0.05秒で提出版1.0秒より弱く、common arena勝率ではないためR7値と合算しない。

| archetype | candidate | evidence | status |
|---|---|---|---|
| Archaludon/Cinderace | `public_archaludon_cinderace_r7::deck=42165967b565dd42::policy=c08588467c3faa2c` | runs/meta-specialist-strength/teacher-archaludon-r7-fixed6-seed9700000-96.json 62/96 (64.58%) | measured_local_fixed_six_best |
| Water Box/Starmie | `waterbox_search_v3::deck=fd1f98c19f581ac2::policy=a5f045c2019b00d5` | opponents/waterbox_search_v3/SOURCE.md LB 789.4 | external_lb_source_only |
| Rocket/Mewtwo | `ozawa_rocket_v2::deck=0c4a1f66c862ca1d::policy=a3b9cc59b82ebb34` | opponents/ozawa_rocket_v2/SOURCE.md pool 0.6138 | historical_reference_only |
| Water Box/Starmie (pure starmie) | `ozawa_starmie_v3::deck=82443d0c366fa0f5::policy=5b0895c38636fd4b` | opponents/ozawa_starmie_v3/SOURCE.md LB 653.2 | historical_reference_only |
| Crustle | `ozawa_crustle_v2::deck=5c45f8e7ca25a206::policy=19a8ceb5c59087bb` | opponents/ozawa_crustle_v2/SOURCE.md mirror 0.75 | historical_reference_only |
| Lucario | 未確定（itsuki9180_lucario_jp, kiyotah_lucario, kojimar_lucario, kokinnwakashuu_lucario_search, pilkwang_lucario_alakazam） | current common strengthなし | NOT DETERMINED |
| Alakazam | 未確定（nihei_alakazam, ruruko_alakazam_control, sue124_alakazam, tientrum_alakazam_search） | current common strengthなし | NOT DETERMINED |

## 5. 既存strength JSON

`runs/**/strength.json` は **128件**をinventory化（全件はJSON）。これらの多くはstudent research checkpointで現行opponentのagent+deck pairではないため、Strong Asset/Championへ自動昇格しない。

| path | subject | games | W/D/L | fault | score | pair扱い |
|---|---|---:|---|---:|---:|---|
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r7-alakazam/strength.json` | `checkpoint-f94fb93f5643` | 24 | 18/0/6 | 0 | 75.00% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r1-alakazam/strength.json` | `checkpoint-9010d5847aee` | 23 | 16/0/7 | 1 | 69.57% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-final-alakazam-strength.json` | `checkpoint-cf5c974fc70b` | 96 | 64/0/32 | 0 | 66.67% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t2-r5-alakazam/strength.json` | `checkpoint-73c7d9ef5c18` | 24 | 16/0/8 | 0 | 66.67% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r8-alakazam/strength.json` | `checkpoint-cf5c974fc70b` | 24 | 16/0/8 | 0 | 66.67% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t1-r8-alakazam/strength.json` | `checkpoint-cb29c4ec6190` | 23 | 15/0/8 | 1 | 65.22% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r5-alakazam/strength.json` | `checkpoint-edaf0083309b` | 23 | 15/0/8 | 1 | 65.22% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t2-r2-rocket/strength.json` | `checkpoint-db0c55ec6e7b` | 24 | 15/0/9 | 0 | 62.50% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t2-r4-alakazam/strength.json` | `checkpoint-c6071ee2de89` | 24 | 15/0/9 | 0 | 62.50% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r6-alakazam/strength.json` | `checkpoint-6645b89d1182` | 24 | 15/0/9 | 0 | 62.50% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r3-alakazam/strength.json` | `checkpoint-ee2497bc8481` | 23 | 14/0/9 | 1 | 60.87% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r4-alakazam/strength.json` | `checkpoint-660e90cd9930` | 24 | 14/0/10 | 0 | 58.33% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/dryrun-final-rocket-strength.json` | `checkpoint-2adf626d5767` | 12 | 7/0/5 | 0 | 58.33% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t1-r6-alakazam/strength.json` | `checkpoint-334d652d8b35` | 24 | 13/0/11 | 0 | 54.17% | research-only |
| `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t1-r8-rocket/strength.json` | `checkpoint-cd7bd8cd7853` | 24 | 13/0/11 | 0 | 54.17% | research-only |

## 6. 全102 pair inventory

`deck`=raw SHA先頭16桁、`declared`=manifest canonical先頭16桁、`policy`=raw main.py SHA先頭16桁。

| # | pair | archetype | deck raw / declared | policy | src | smoke | runtime | train |
|---:|---|---|---|---|---|---|---|---|
| 1 | `aman_crustleaware_fighting` | Crustle | `2a541d7bf3d9e6b3` / `c6ca39850d15dce9` | `67549fe5af8e8a84` | public | true | smoke_pass_fast | NO |
| 2 | `aristophanivan_multiply` | Other/Unknown | `2a541d7bf3d9e6b3` / `c6ca39850d15dce9` | `8f108c57c14714d6` | public | true | smoke_pass_fast | NO |
| 3 | `aristophanivan_probabilistic` | Other/Unknown | `2a541d7bf3d9e6b3` / `c6ca39850d15dce9` | `202ae85a283d80ba` | public | true | smoke_pass_fast | NO |
| 4 | `biohack44_crustlecounter2` | Crustle | `9c2647bd80d51bfd` / `9daa195aa54468c9` | `0eabab7ed642dd6e` | public | true | smoke_pass_fast | NO |
| 5 | `dashimaki360_crustlecounter` | Crustle | `9c2647bd80d51bfd` / `9daa195aa54468c9` | `881a239cc6b9f74b` | public | true | smoke_pass_fast | NO |
| 6 | `ferozahmedds_solution` | Other/Unknown | `e92d5717fd04865b` / `b702e251e3b56104` | `f868e12e7a9d33bb` | public | true | smoke_pass_fast | NO |
| 7 | `harukiharada_crustle` | Crustle | `0bf6b29c6d355563` / `bb6e08563acd6a2f` | `eb8c2384bf146a03` | public | true | smoke_pass_fast | NO |
| 8 | `itsuki9180_lucario_jp` | Lucario | `b4464eb525a25e65` / `b39573132435a9bd` | `f0eef9205eaadfb5` | public | true | smoke_pass_fast | NO |
| 9 | `kinoshita_pimc_search` | Other/Unknown | `55eb37154b6508e9` / `84a07f500b06e509` | `ad32ea721129468b` | internal | true | smoke_pass_but_very_slow | NO |
| 10 | `kiyotah_abomasnow` | Abomasnow | `90f308029355e715` / `2e7e2aebdd412ac8` | `441f42e47ea085ae` | public | true | smoke_pass_fast | NO |
| 11 | `kiyotah_dragapult` | Dragapult | `008816603a8ac836` / `60833b83948883fe` | `fc22f35118e5161c` | public | true | smoke_pass_fast | NO |
| 12 | `kiyotah_iono` | Other/Unknown | `bb264cca591df66a` / `7dc29ba6002f31ae` | `d892ad0787c3f557` | public | true | smoke_pass_fast | NO |
| 13 | `kiyotah_lucario` | Lucario | `b4464eb525a25e65` / `b39573132435a9bd` | `f868e12e7a9d33bb` | public | true | smoke_pass_fast | NO |
| 14 | `kojimar_lucario` | Lucario | `2a541d7bf3d9e6b3` / `c6ca39850d15dce9` | `44187ca3031261cd` | public | true | smoke_pass_fast | NO |
| 15 | `kokinnwakashuu_lucario_search` | Lucario | `b4464eb525a25e65` / `b39573132435a9bd` | `240574c80101dce1` | public | true | smoke_pass_fast | NO |
| 16 | `lucifer19_battlecore` | Metal/Psychic | `fbe6ab59992260b0` / `da1d56e33b96abcc` | `c4acf505565a0786` | public | true | smoke_pass_fast | YES |
| 17 | `makthanithin_baseline1084` | Other/Unknown | `2a541d7bf3d9e6b3` / `c6ca39850d15dce9` | `44187ca3031261cd` | public | true | smoke_pass_fast | NO |
| 18 | `masamikobayashi_garchomp` | Garchomp | `f6fe420cb34f07dd` / `bc2bc2474e05e046` | `6347a928757ddae2` | public | true | smoke_pass_fast | NO |
| 19 | `medal_0001_77a53ffc` | Medal decklist / unknown | `5ddb7ca2790518e3` / `77a53ffc32f89b22` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 20 | `medal_0004_01501d64` | Medal decklist / unknown | `7fc17fc61014dc3b` / `01501d644249c081` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 21 | `medal_0006_07bedfff` | Medal decklist / unknown | `730515d5b8e9d6f7` / `07bedfffbfad6ecb` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 22 | `medal_0007_dd63244c` | Medal decklist / unknown | `3b4ffbc0735d73a4` / `dd63244cb42c5002` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 23 | `medal_0009_25393c12` | Medal decklist / unknown | `3c9da09cd8d60862` / `25393c128f3bb0b6` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 24 | `medal_0010_4bf59ca5` | Medal decklist / unknown | `16ba8a623a75e2db` / `4bf59ca589c2d685` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 25 | `medal_0014_f50fa3a2` | Medal decklist / unknown | `6c115a5f06facc35` / `f50fa3a23cdf21be` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 26 | `medal_0015_5e60b8c7` | Medal decklist / unknown | `97f7424bf3758bc5` / `5e60b8c7eafc87e6` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 27 | `medal_0016_706fa912` | Medal decklist / unknown | `712f17011d979381` / `706fa9122e5b9ca9` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 28 | `medal_0018_053b4950` | Medal decklist / unknown | `ae8f420757b8f9e1` / `053b4950d40b4a85` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 29 | `medal_0019_df6f7443` | Medal decklist / unknown | `2d5862b0364bcead` / `df6f744371967571` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 30 | `medal_0020_d6c573dd` | Medal decklist / unknown | `e7fa70fcaef8fb7f` / `d6c573dd89bd1319` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 31 | `medal_0022_e40278fd` | Medal decklist / unknown | `78a57de595b8e7b7` / `e40278fd83d971c2` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 32 | `medal_0190_f06bd3d5` | Medal decklist / unknown | `262df0fed73650d1` / `f06bd3d5964e5f6d` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 33 | `medal_0236_f7e1adfe` | Medal decklist / unknown | `3fd35b596c6444e8` / `f7e1adfe46d551a8` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 34 | `medal_0282_78fc59fb` | Medal decklist / unknown | `31c587b5feb4991a` / `78fc59fb52f5218a` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 35 | `medal_0312_a3079bb2` | Medal decklist / unknown | `624d2c56d7fda71b` / `a3079bb2eb200f70` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 36 | `medal_0346_5b509bae` | Medal decklist / unknown | `4f06578ff3623d36` / `5b509baed30a8f00` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 37 | `medal_0362_dae58a68` | Medal decklist / unknown | `9f251e465a3c4d13` / `dae58a68ad346b5f` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 38 | `medal_0378_7bcec45f` | Medal decklist / unknown | `a775c003db981840` / `7bcec45fb9b7cd3c` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 39 | `medal_0427_3300b0c3` | Medal decklist / unknown | `cc72106fa38e074c` / `3300b0c327941f8c` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 40 | `medal_0460_3e769b3b` | Medal decklist / unknown | `ced514d0d0fa6690` / `3e769b3beea77f4e` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 41 | `medal_0509_203002de` | Medal decklist / unknown | `52800b5c06667e20` / `203002de844fcbf3` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 42 | `medal_0590_ff157aaa` | Medal decklist / unknown | `485538f8385f3ec6` / `ff157aaa919099f0` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 43 | `medal_2844_04dbbd93` | Medal decklist / unknown | `fa6a1be4866971af` / `04dbbd93391d1f19` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 44 | `medal_2845_67cf83ea` | Medal decklist / unknown | `cd7bfdfa205c4c37` / `67cf83ea7e092595` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 45 | `medal_2849_bd32b8f7` | Medal decklist / unknown | `43bf596df657c113` / `bd32b8f7825316d8` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 46 | `medal_2850_952f9507` | Medal decklist / unknown | `a23b4f079bdcc3dd` / `952f95079e538325` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 47 | `medal_2851_8543bee4` | Medal decklist / unknown | `51c92824abe3e1c3` / `8543bee4ad0091ad` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 48 | `medal_2852_b31a602e` | Medal decklist / unknown | `473295ed1af98513` / `b31a602e09766609` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 49 | `medal_2855_fba1f87c` | Medal decklist / unknown | `2fa0d035c97f0ba1` / `fba1f87c71154c33` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 50 | `medal_2856_458f87a5` | Medal decklist / unknown | `8515e101ad23f62a` / `458f87a5ad495833` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 51 | `medal_2857_0c1054dc` | Medal decklist / unknown | `1e51a892f46d2923` / `0c1054dcce6457d1` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 52 | `medal_2858_6644aa14` | Medal decklist / unknown | `e4e8dd2c4a31aebf` / `6644aa1457640c4c` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 53 | `medal_2859_02ea57ae` | Medal decklist / unknown | `74947440a3d99d7f` / `02ea57aeb3a6fbe0` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 54 | `medal_2862_65040fb4` | Medal decklist / unknown | `13d50fc6749361e6` / `65040fb471cbc542` | `6336b4d54e63c5da` | public | true | smoke_pass_no_latency_measurement | NO |
| 55 | `meta_1_cddce6bc` | Meta decklist / unknown | `33f38523c965d5dd` / `f1776ee333fe625b` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 56 | `meta_2_2a7de279` | Meta decklist / unknown | `f3332903a3b2827a` / `747769779b60ad8d` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 57 | `meta_3_fea3a860` | Meta decklist / unknown | `0f8fb632ade28336` / `09b937915fb787c6` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 58 | `meta_4_24aee684` | Meta decklist / unknown | `01380ff4f2c005bc` / `c04e65de725b8bb5` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 59 | `meta_5_6f809062` | Meta decklist / unknown | `2dc1632a1ac70184` / `e2e03fe8ef9592b1` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 60 | `meta_6_0981af32` | Meta decklist / unknown | `a34bfa575030329d` / `1bce920993a6864b` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 61 | `meta_rm_c8a386a0` | Meta decklist / unknown | `bba5c75b1ccc6c36` / `7851bf5595529f83` | `475cfa8b3106839e` | internal | true | smoke_pass_fast | NO |
| 62 | `naoto714_kangaskhan` | Kangaskhan | `ac0c6d8134b084fb` / `0befb6b09b21d0a3` | `5a6d88551aca8e81` | public | true | smoke_pass_fast | NO |
| 63 | `naoto714_slowking` | Slowking | `ad4c639f107f86f2` / `2ee4e3b41502a638` | `a784dd2ae91db60f` | public | true | smoke_pass_fast | NO |
| 64 | `naoto714_ursaluna` | Ursaluna | `5ffa2ab68390ddeb` / `5d65e1eff4153af8` | `ddb9c63819351f36` | public | true | smoke_pass_fast | NO |
| 65 | `nihei_alakazam` | Alakazam | `167d43335013f7b6` / `3f4515092dc59df3` | `a502b37132b5558f` | internal | true | smoke_pass_fast | NO |
| 66 | `nihei_comfey_library_out` | Comfey/Library Out | `1f098c6828032643` / `cb18f9444ac26e35` | `e72e83e84031fb19` | internal | true | smoke_pass_fast | NO |
| 67 | `nihei_cynthias_garchomp` | Garchomp | `e8179645bb6fa0ca` / `8015e2bae8b8f7f5` | `c8e280929e3956a4` | internal | true | smoke_pass_fast | NO |
| 68 | `nihei_double_dqn_houdin` | Other/Unknown | `167d43335013f7b6` / `3f4515092dc59df3` | `19b1d5b08232a613` | internal | true | smoke_pass_fast | NO |
| 69 | `nihei_festival_lead` | Festival | `6224ff04fae6eac4` / `6b295a20e0c10d5b` | `20ee80c99aea42b0` | internal | true | smoke_pass_fast | NO |
| 70 | `nihei_hydreigon_deckout` | Hydreigon | `448dc25da5e15d16` / `dec1127be8c82772` | `245a9b1f6ecde17d` | internal | true | smoke_pass_fast | NO |
| 71 | `nihei_megalopunny` | Mega Lopunny | `56e49277697078de` / `f03203e5bc6fc1cd` | `9354ef4897d49e88` | internal | true | smoke_pass_fast | NO |
| 72 | `official_random` | Calibration/Random | `42068a1803902756` / `b702e251e3b56104` | `003eae7805aa3849` | public | true | smoke_pass_fast | NO |
| 73 | `ozawa_crustle_rule` | Crustle | `5c45f8e7ca25a206` / `ef77c85d6e77e778` | `3a9d45cc08d22c27` | internal | true | smoke_pass_fast | NO |
| 74 | `ozawa_crustle_rule_rl` | Crustle | `5c45f8e7ca25a206` / `ef77c85d6e77e778` | `934b3f341e3aced1` | internal | true | smoke_pass_fast | NO |
| 75 | `ozawa_crustle_v2` | Crustle | `5c45f8e7ca25a206` / `ef77c85d6e77e778` | `19a8ceb5c59087bb` | internal | true | smoke_pass_fast | NO |
| 76 | `ozawa_grimmsnarl_rule_rl` | Grimmsnarl | `92b92bac9f9163ec` / `c20a8a46f5c63577` | `e453df4f1e9315db` | internal | true | smoke_pass_fast | NO |
| 77 | `ozawa_grimmsnarl_v2` | Grimmsnarl | `92b92bac9f9163ec` / `c20a8a46f5c63577` | `48621429950e717e` | internal | true | smoke_pass_fast | NO |
| 78 | `ozawa_metal_psychic_search` | Metal/Psychic | `2f1f530e5153ef23` / `596d10b4dbfed9cf` | `6e8a05bbafbdabc7` | internal | true | smoke_pass_but_very_slow | NO |
| 79 | `ozawa_rocket_rule` | Rocket/Mewtwo | `0c4a1f66c862ca1d` / `c191302f094ebda3` | `4fe28c139ae116df` | internal | true | smoke_pass_fast | NO |
| 80 | `ozawa_rocket_rule_rl` | Rocket/Mewtwo | `0c4a1f66c862ca1d` / `c191302f094ebda3` | `c688b96d1492f46d` | internal | true | smoke_pass_fast | NO |
| 81 | `ozawa_rocket_v2` | Rocket/Mewtwo | `0c4a1f66c862ca1d` / `c191302f094ebda3` | `a3b9cc59b82ebb34` | internal | true | smoke_pass_fast | NO |
| 82 | `ozawa_starmie` | Water Box/Starmie | `82443d0c366fa0f5` / `abde4f3593f4a718` | `12fa42bb99783b2f` | internal | true | smoke_pass_fast | NO |
| 83 | `ozawa_starmie_v3` | Water Box/Starmie | `82443d0c366fa0f5` / `abde4f3593f4a718` | `5b0895c38636fd4b` | internal | true | smoke_pass_fast | NO |
| 84 | `pilkwang_lucario_alakazam` | Lucario | `6415396d35c0f4b3` / `5822b67f77eed1df` | `60300d264de47d39` | public | true | smoke_pass_fast | NO |
| 85 | `plamen06_steel` | Metal/Psychic | `fbe6ab59992260b0` / `da1d56e33b96abcc` | `8a40be6825612ea9` | public | true | smoke_pass_fast | NO |
| 86 | `prvsiyan_grimmsnarl` | Grimmsnarl | `92b92bac9f9163ec` / `c20a8a46f5c63577` | `9b369c7d26dfce6b` | public | true | smoke_pass_slow | NO |
| 87 | `public_archaludon_cinderace_r7` | Archaludon/Cinderace | `42165967b565dd42` / `42165967b565dd42` | `c08588467c3faa2c` | public | false | smoke_failed_quarantine | NO |
| 88 | `rauffauzanrambe_advanced` | Other/Unknown | `008816603a8ac836` / `60833b83948883fe` | `b49039ff5558215e` | public | true | smoke_pass_fast | NO |
| 89 | `romanrozen_strongstart` | Calibration/StrongStart | `b4464eb525a25e65` / `b39573132435a9bd` | `6df4925011c9a48d` | public | true | smoke_pass_fast | NO |
| 90 | `ruruko_alakazam_control` | Alakazam | `1d92a15bb129accd` / `e6886f558d6ea4e6` | `bee9244d1568b43e` | internal | true | smoke_pass_fast | NO |
| 91 | `ruruko_experiment_a` | Other/Unknown | `1d92a15bb129accd` / `e6886f558d6ea4e6` | `31619f047b16ff14` | internal | true | smoke_pass_fast | NO |
| 92 | `ruruko_experiment_a_v2` | Other/Unknown | `167d43335013f7b6` / `3f4515092dc59df3` | `4c95096e95af8358` | internal | true | smoke_pass_fast | NO |
| 93 | `rv1922_agent` | Other/Unknown | `3f552e70584e650b` / `60833b83948883fe` | `4048acab92a254a7` | public | true | smoke_pass_fast | NO |
| 94 | `serariagomes_heuristic` | Heuristic/Unknown | `1156379af39e71bc` / `39d19fe3da32ec1a` | `9af9821674bcc7ae` | public | true | smoke_pass_fast | NO |
| 95 | `skarin_dragapult` | Dragapult | `4285ab2c575173fb` / `60833b83948883fe` | `fc22f35118e5161c` | public | true | smoke_pass_fast | NO |
| 96 | `sue124_alakazam` | Alakazam | `7b413177e5077777` / `16fc66abdcea7bd5` | `e81d3ecd7272da2e` | public | true | smoke_pass_fast | NO |
| 97 | `tientrum_alakazam_search` | Alakazam | `a8c9177354b92abe` / `87bed7987bdc4c06` | `42db1901f1f02c1d` | public | true | smoke_pass_but_slow_for_pool | NO |
| 98 | `tomatomato_archaludon` | Archaludon/Cinderace | `42165967b565dd42` / `0963c2daca1844b5` | `8908af5caad29682` | public | true | smoke_pass_fast | YES |
| 99 | `water_box_search` | Water Box/Starmie | `fd1f98c19f581ac2` / `b809c4807e5c7e6e` | `d949ee5e4c8d19ce` | internal | true | smoke_pass_but_very_slow | NO |
| 100 | `waterbox_search_v3` | Water Box/Starmie | `fd1f98c19f581ac2` / `b809c4807e5c7e6e` | `a5f045c2019b00d5` | internal | true | smoke_pass_but_slow_for_pool | NO |
| 101 | `yaminh_agent` | Other/Unknown | `dc2b68464064ae88` / `a9a7d38265bea5f0` | `d627fa239976dc98` | public | true | smoke_pass_fast | NO |
| 102 | `yaroslav_crustleaware_lucario` | Crustle | `b4464eb525a25e65` / `b39573132435a9bd` | `05cc0c506c15aa00` | public | true | smoke_pass_fast | NO |

### 主要pair provenance

- R7: upstream GitHub commit `39545440…`, raw deck `42165967…`, policy `c0858846…`, smoke false、fixed-six 62/96。
- tomatomato: Kaggle kernel `masamikobayashi/a-sample-archaludon-75-wr-vs-my-1300-starmie`、policy `8908af5c…`、teacher collection 96局 60W/36L、records5146。
- lucifer19: Kaggle kernel `lucifer19/battlecore-compact-agent`、policy `c4acf505…`、declared deck SHA `da1d56e3…`（`plamen06_steel`と共有）のためMetal/Psychic系として分類、teacher collection 48局 40W/8L、records2790。
- waterbox_search_v3: internal `feature/water-box-search` commit `0ed1995`、policy `a5f045c2…`、LB789.4は提出版で、bench budget0.05。
- ozawa_rocket_v2 / starmie_v3 / crustle_v2: frozen internal historical reference。
- `meta_*` 7件 / `medal_*` 36件: generic agent＋decklist-only、coverage/meta stress用途。

## 7. research candidates / arena境界

| candidate | status | census扱い |
|---|---|---|
| Rule v0 + root deck | packageable baseline / clean-room smoke 2/2 | legality/safety anchor |
| Wave6 seed0/seed1 | research-only checkpoints | pool submission pairではない |
| qualified teacher short candidates | shadow-B aggregate 50.5/96 vs Wave6 56/96 | BestKnownではない |
| current Stage1 arena | 実行中 | 完了後に独立reconciliation |

親agentから完了報告されたcurrent-source broad値としてroot 13/96、Wave6 s0 49/96、s1 55/96（fault0、evaluator `cb15090f…`）がある。これはpool manifestに対する実行中arenaの別系列 evidence であり、本Censusがarena出力を読んで再検証した値ではない。W/D/L、seat/seed/opponent内訳、protocol/SHA整合の最終確認後まで、本Censusのprovisional GBKやStrong Asset pair順位には採用しない。

## 8. 次のreconciliation

1. Stage1完了後、全arm manifestをread-only検証。
2. deck/policy/source SHAとpair_idを再計算。
3. game数・seat・seed・opponent lineage・fault・timeout・protocol/evaluator SHAを層化比較。
4. R7はsmoke remediationとpermission再確認までevaluation-only隔離。
5. tomatomato/luciferだけbounded local teacher sourceとして使用。
6. external LB / CEM / mirror / student checkpointをcommon arena勝率に換算しない。

## 9. 成果物

- machine-readable: `docs/evidence/strong-asset-census-20260812.json`
- human-readable: `docs/evidence/strong-asset-census-20260812.md`
- 既存pair inventory/auditは変更なし。
